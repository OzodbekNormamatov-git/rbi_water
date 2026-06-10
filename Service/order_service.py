from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Sequence
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from Data.unit_of_work import UnitOfWork
from Domain.constants import (
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    MAX_ADDRESS_DETAILS_LENGTH,
    MAX_ADDRESS_LABEL_LENGTH,
    MAX_BOTTLES_PER_TRANSACTION,
    MAX_QUANTITY_PER_ITEM,
)
from Domain.enums import OrderStatus
from Domain.models.order import Order, OrderItem
from Service.exceptions import (
    EntityNotFoundError,
    InvalidOperationError,
    ValidationError,
)
from Service.ledger_service import (
    cap_cashback_usage,
    compute_cashback_for,
    quantize_cashback,
    quantize_cashback_use,
)
from Service.order_display import order_display_number


def _local_tz():
    """Toshkent (yoki config'dagi) timezone — kunlik raqam sanasi uchun.
    Topilmasa UTC+5 fallback (analytics_service bilan bir xil pattern)."""
    try:
        from config import get_settings
        return ZoneInfo(get_settings().timezone)
    except Exception:
        return timezone(timedelta(hours=5))


@dataclass(slots=True)
class CartItem:
    food_id: int
    quantity: int


@dataclass(slots=True)
class DeliveredStats:
    today: int
    month: int
    year: int
    total: int


@dataclass(slots=True)
class NewOrderInput:
    customer_telegram_id: int
    items: List[CartItem]
    delivery_latitude: float
    delivery_longitude: float
    contact_phone: str
    note: str = ""
    # Mijoz tarafdan qisqa noyob kalit (UUID/timestamp). Bir xil kalit bilan
    # qayta yuborilsa — yangi buyurtma yaratilmaydi, mavjudi qaytariladi.
    idempotency_key: Optional[str] = None
    # Manzil yorlig'i (masalan, "Uy") va batafsil izoh — frontend Address Book'dan
    # snapshot sifatida yuboradi yoki bo'sh qoldiradi (free-form pin).
    address_label: str = ""
    address_details: str = ""
    # Mijoz ishlatmoqchi bo'lgan keshbek miqdori (so'm).
    cashback_to_use: Decimal = field(default_factory=lambda: Decimal("0.00"))
    # Mijoz bu yetkazib berishda qaytaradigan bo'sh idishlar soni.
    bottles_returned: int = 0
    # Bu buyurtma orqali mijozga yetkaziladigan idishlar soni (default: jami item miqdori).
    # None bo'lsa — itemlar qiymatidan avtomatik hisoblanadi.
    bottles_issued: Optional[int] = None
    # Operator yaratgan bo'lsa — operator'ning Telegram ID si (audit trail).
    # Mijoz o'zi yaratgan bo'lsa NULL qoladi. Mijoz tomondan API'da kelmaydi,
    # faqat operator API'da o'rnatiladi.
    created_by_operator_id: Optional[int] = None


class OrderService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create_order(self, data: NewOrderInput) -> Order:
        if not data.items:
            raise ValidationError("cart_empty")
        if data.delivery_latitude is None or data.delivery_longitude is None:
            raise ValidationError("location_required")
        if not (LAT_MIN <= float(data.delivery_latitude) <= LAT_MAX) or not (
            LON_MIN <= float(data.delivery_longitude) <= LON_MAX
        ):
            raise ValidationError("location_invalid")
        if not (data.contact_phone or "").strip():
            raise ValidationError("phone_required")
        if not (data.note or "").strip():
            raise ValidationError("note_empty")

        # Manzil meta-data sanitizatsiyasi
        label = (data.address_label or "").strip()[:MAX_ADDRESS_LABEL_LENGTH]
        details = (data.address_details or "").strip()[:MAX_ADDRESS_DETAILS_LENGTH]

        # Idishlar miqdori validatsiyasi
        bottles_returned = max(0, int(data.bottles_returned or 0))
        if bottles_returned > MAX_BOTTLES_PER_TRANSACTION:
            raise ValidationError(
                "bottles_out_of_range", context={"max": MAX_BOTTLES_PER_TRANSACTION},
            )

        cashback_to_use_in = Decimal(str(data.cashback_to_use or 0))
        if cashback_to_use_in < 0:
            raise ValidationError("cashback_negative")

        async with UnitOfWork(self._sf) as uow:
            # Mijozni LOCK qilib olamiz — keshbek/bottle balansi atomik yangilanishi shart.
            user_row = await uow.users.get_by_telegram_id(data.customer_telegram_id)
            if user_row is None:
                raise InvalidOperationError("user_not_registered")
            user = await uow.users.get_for_update(user_row.id) or user_row

            # Idempotency: agar shu mijoz shu key bilan oldin yaratgan bo'lsa, o'sha order'ni qaytar.
            if data.idempotency_key:
                existing = await uow.orders.get_by_idempotency_key(
                    user.id, data.idempotency_key,
                )
                if existing is not None:
                    return existing

            # Cashback konfiguratsiyasi (live, admin tomonidan boshqariladi)
            cfg = await uow.settings.get_or_create()
            cashback_enabled = bool(cfg.cashback_enabled)
            cashback_percent = Decimal(cfg.cashback_percent or 0)
            max_usage_ratio = Decimal(cfg.max_cashback_usage_ratio or 0)

            # Itemlarni yig'amiz va items_total ni hisoblaymiz.
            order_items: List[OrderItem] = []
            items_total = Decimal("0.00")
            total_qty = 0
            for item in data.items:
                if item.quantity <= 0:
                    raise ValidationError("cart_item_qty_invalid")
                if item.quantity > MAX_QUANTITY_PER_ITEM:
                    raise ValidationError(
                        "cart_item_qty_too_big",
                        context={"max": MAX_QUANTITY_PER_ITEM},
                    )
                food = await uow.foods.get(item.food_id)
                if food is None or not food.is_available:
                    raise InvalidOperationError(
                        "food_unavailable",
                        context={"food_id": item.food_id},
                    )
                line = OrderItem(
                    food_id=food.id,
                    food_name=food.name,
                    unit_price=food.price,
                    quantity=item.quantity,
                )
                items_total += food.price * item.quantity
                total_qty += int(item.quantity)
                order_items.append(line)

            # Minimal buyurtma soni — admin AppSettings'da belgilaydi (default 1 = cheklov yo'q).
            # Mahsulotlar umumiy soni shu chegaradan kam bo'lsa — rad etamiz
            # (kichik buyurtmalar yetkazib berish narxini qoplamaydi). Server tomondan
            # majburiy — frontend ham bloklaydi, lekin bu yagona ishonchli manba.
            min_order_qty = int(getattr(cfg, "min_order_quantity", 1) or 1)
            if total_qty < min_order_qty:
                raise ValidationError(
                    "order_below_minimum", context={"min": min_order_qty},
                )

            # Bottles issued (default: items'ning umumiy soni — har bir mahsulot 1 idish)
            bottles_issued = (
                int(data.bottles_issued) if data.bottles_issued is not None else int(total_qty)
            )
            if bottles_issued < 0:
                bottles_issued = 0
            if bottles_issued > MAX_BOTTLES_PER_TRANSACTION * 10:
                # Sanity cap — biror joyda buzuq qiymat kelmasin
                bottles_issued = MAX_BOTTLES_PER_TRANSACTION * 10

            # Bottles returned — mijozning balansidan oshmasligi shart
            if bottles_returned > int(user.bottles_balance or 0):
                raise InvalidOperationError(
                    "bottles_return_exceeds_balance",
                    context={
                        "available": int(user.bottles_balance or 0),
                        "requested": bottles_returned,
                    },
                )

            # Keshbek ishlatish: dasturi yoqilgan bo'lishi shart, balansdan oshmasin,
            # va admin tomonidan belgilangan ulush cheklovini buzmasin.
            # Mijoz tomondan kelgan miqdorni 1000 so'mga FLOOR qilamiz —
            # masalan 1400 → 1000 (silent). Frontend slider step bilan
            # bu hech qachon sodir bo'lmaydi, lekin server tomondan kafolat beradi.
            requested_cashback = quantize_cashback_use(cashback_to_use_in)
            if requested_cashback > 0 and not cashback_enabled:
                raise ValidationError("cashback_disabled")

            available_cashback = Decimal(user.cashback_balance or 0)
            if requested_cashback > available_cashback:
                raise InvalidOperationError(
                    "cashback_not_enough",
                    context={"available": float(available_cashback)},
                )
            capped = cap_cashback_usage(items_total, requested_cashback, max_usage_ratio)
            if capped < requested_cashback:
                raise ValidationError(
                    "cashback_over_limit",
                    context={"ratio_percent": int(max_usage_ratio * 100)},
                )
            cashback_used = capped

            total_amount = (items_total - cashback_used).quantize(Decimal("0.01"))
            if total_amount < 0:
                total_amount = Decimal("0.00")

            # DELIVERED bo'lganda mijoz oladigan keshbek — items_total dan.
            # Cashback dasturi o'chirilgan bo'lsa, yangi liability yaratmaymiz.
            cashback_earned = (
                compute_cashback_for(items_total, cashback_percent)
                if cashback_enabled else Decimal("0.00")
            )

            # Kunlik raqam — Toshkent mahalliy sanasi bo'yicha atomik counter.
            # Idempotency tekshiruvidan KEYIN (takroriy POST raqam isrof qilmasin)
            # va validatsiyalardan keyin (xato bo'lsa UoW rollback — raqam ham qaytadi).
            today_local = datetime.now(_local_tz()).date()
            daily_number = await uow.orders.next_daily_number(today_local)

            order = Order(
                customer_id=user.id,
                status=OrderStatus.NEW,
                daily_number=daily_number,
                items_total=items_total,
                cashback_used=cashback_used,
                cashback_earned=cashback_earned,
                total_amount=total_amount,
                bottles_issued=bottles_issued,
                bottles_returned=bottles_returned,
                delivery_latitude=float(data.delivery_latitude),
                delivery_longitude=float(data.delivery_longitude),
                address_label=label,
                address_details=details,
                contact_phone=data.contact_phone.strip(),
                note=(data.note or "").strip(),
                idempotency_key=data.idempotency_key,
                created_by_operator_id=data.created_by_operator_id,
                items=order_items,
            )
            await uow.orders.add(order)

            # Keshbek darhol ushlab qo'yiladi (escrow): bekor qilinsa qaytariladi.
            # Idishlar balansi YETKAZIB BERILDI bo'lganda yangilanadi.
            if cashback_used > 0:
                user.cashback_balance = (
                    Decimal(user.cashback_balance or 0) - cashback_used
                ).quantize(Decimal("0.01"))
                await uow.users.add(user)

            return await uow.orders.get_full(order.id)  # type: ignore[return-value]

    async def get(self, order_id: int) -> Order:
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get_full(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            return order

    async def list_for_customer(
        self, telegram_id: int, *, limit: int = 20, offset: int = 0,
    ) -> Sequence[Order]:
        async with UnitOfWork(self._sf) as uow:
            user = await uow.users.get_by_telegram_id(telegram_id)
            if user is None:
                return []
            return await uow.orders.list_by_customer_paginated(
                user.id, limit=limit, offset=offset,
            )

    async def count_for_customer(self, telegram_id: int) -> int:
        async with UnitOfWork(self._sf) as uow:
            user = await uow.users.get_by_telegram_id(telegram_id)
            if user is None:
                return 0
            return await uow.orders.count_by_customer(user.id)

    async def list_recent(self, limit: int = 20) -> Sequence[Order]:
        async with UnitOfWork(self._sf) as uow:
            return await uow.orders.list_recent(limit=limit)

    async def delivered_stats_for_courier(self, courier_id: int) -> "DeliveredStats":
        """Kuryer yetkazib bergan zakazlar soni: bugun, oy, yil, hamma vaqt (UTC)."""
        now = datetime.now(timezone.utc)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        year_start = day_start.replace(month=1, day=1)
        async with UnitOfWork(self._sf) as uow:
            today = await uow.orders.count_delivered_by_courier(courier_id, since=day_start)
            month = await uow.orders.count_delivered_by_courier(courier_id, since=month_start)
            year = await uow.orders.count_delivered_by_courier(courier_id, since=year_start)
            total = await uow.orders.count_delivered_by_courier(courier_id, since=None)
        return DeliveredStats(today=today, month=month, year=year, total=total)

    async def list_by_status(self, status: OrderStatus, limit: int = 50) -> Sequence[Order]:
        async with UnitOfWork(self._sf) as uow:
            return await uow.orders.list_by_status(status, limit=limit)

    async def attach_group_message(self, order_id: int, message_id: int) -> None:
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            order.group_message_id = message_id
            await uow.orders.add(order)

    async def attach_courier_dm_message(self, order_id: int, message_id: int) -> None:
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            order.courier_dm_message_id = message_id
            await uow.orders.add(order)

    async def attach_customer_dm_message(self, order_id: int, message_id: int) -> None:
        """Mijoz DM dagi yagona holat-lentasi xabarining id sini saqlaymiz —
        keyingi statuslarda shu xabar edit qilinadi (yangi xabar yuborilmaydi)."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            order.customer_dm_message_id = message_id
            await uow.orders.add(order)

    async def attach_customer_arrived_message(self, order_id: int, message_id: int) -> None:
        """ARRIVED holatda yuborilgan "yetib keldi!" bildirishnoma id'si.
        DELIVERED bo'lganda bot uni o'chiradi (e'tibor tortildi, chiqindi qoldirmaymiz)."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            order.customer_arrived_message_id = message_id
            await uow.orders.add(order)

    async def clear_customer_arrived_message(self, order_id: int) -> None:
        """O'chirilgan xabar ID'sini DB'dan ham tozalaymiz (idempotent qayta yuborish uchun)."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get(order_id)
            if order is None:
                return
            order.customer_arrived_message_id = None
            await uow.orders.add(order)

    async def claim_by_courier(
        self,
        order_id: int,
        courier_telegram_id: int,
    ) -> Order:
        """
        Atomic: faqat NEW holatdagi orderni bitta kuryer olishi mumkin.
        Qoidalar (UoW ichida tekshiriladi):
          1) Kuryer botga avval /start yuborgan bo'lishi shart (has_started_bot).
          2) Kuryer admin tomonidan AKTIV qilingan bo'lishi shart (is_active).
          3) Kuryerning aktiv (ACCEPTED/DELIVERING) buyurtmasi bo'lmasligi shart.
        """
        async with UnitOfWork(self._sf) as uow:
            courier = await uow.couriers.get_by_telegram_id(courier_telegram_id)
            if courier is None or not courier.has_started_bot:
                raise InvalidOperationError("courier_not_started_bot")
            if not courier.is_active:
                raise InvalidOperationError("courier_not_active")

            active = await uow.orders.list_active_by_courier(courier.id)
            if active:
                ids = ", ".join(order_display_number(o) for o in active)
                raise InvalidOperationError(
                    "courier_has_active_order", context={"ids": ids},
                )

            # RACE-SAFE: row-level lock orqali boshqa kuryer parallel claim qila olmaydi.
            order = await uow.orders.get_for_update(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            if order.status != OrderStatus.NEW or order.courier_id is not None:
                raise InvalidOperationError(
                    "order_already_claimed",
                    context={"status": order.status.label_uz},
                )

            order.courier = courier
            order.status = OrderStatus.ACCEPTED
            order.accepted_at = datetime.now(timezone.utc)
            await uow.orders.add(order)
            await uow.session.refresh(order, attribute_names=["courier", "customer", "items"])
            return order

    async def mark_delivering(self, order_id: int, courier_telegram_id: int) -> Order:
        return await self._transition(
            order_id,
            courier_telegram_id,
            from_=(OrderStatus.ACCEPTED,),
            to=OrderStatus.DELIVERING,
        )

    async def mark_arrived(self, order_id: int, courier_telegram_id: int) -> Order:
        """Kuryer manzilga yetib keldi — mijozdan kutmoqda. Balans hali yangilanmaydi
        (faqat DELIVERED'da). Mijozga "yetib keldi" bildirishnoma yuboriladi (caller)."""
        return await self._transition(
            order_id,
            courier_telegram_id,
            from_=(OrderStatus.DELIVERING,),
            to=OrderStatus.ARRIVED,
        )

    async def set_bottles_returned(
        self,
        order_id: int,
        courier_telegram_id: int,
        bottles: int,
    ) -> Order:
        """Kuryer ARRIVED holatda — mijozdan olingan bo'sh idishlar sonini qayd qiladi.

        Bu metod confirmation oynasidagi +/− stepper handlerlari tomonidan
        chaqiriladi. Faqat ARRIVED holatda, faqat biriktirilgan kuryer chaqira oladi.
        Yakuniy DELIVERED tasdiqlanganda — `order.bottles_returned` shu qiymat saqlanadi.

        Validatsiya:
          * bottles >= 0
          * bottles <= MAX_BOTTLES_PER_TRANSACTION (sanity cap)
          * Order ARRIVED holatda bo'lishi shart
          * Courier biriktirilgan bo'lishi shart
        """
        bottles = max(0, int(bottles))
        if bottles > MAX_BOTTLES_PER_TRANSACTION:
            raise ValidationError(
                "bottles_out_of_range", context={"max": MAX_BOTTLES_PER_TRANSACTION},
            )
        async with UnitOfWork(self._sf) as uow:
            courier = await uow.couriers.get_by_telegram_id(courier_telegram_id)
            if courier is None:
                raise InvalidOperationError("courier_not_registered")
            order = await uow.orders.get_full(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            if order.courier_id != courier.id:
                raise InvalidOperationError("order_not_yours")
            # Faqat ARRIVED holatda kiritish mumkin (DELIVERED'gacha)
            if order.status != OrderStatus.ARRIVED:
                raise InvalidOperationError(
                    "order_state_invalid",
                    context={"status": order.status.label_uz},
                )
            order.bottles_returned = bottles
            await uow.orders.add(order)
            await uow.session.refresh(order, attribute_names=["courier", "customer", "items"])
            return order

    async def mark_delivered(self, order_id: int, courier_telegram_id: int) -> Order:
        """Kuryer "Qabul qildim" tasdiqladi: pul, idishlar, yetkaziildi —
        kuryer javobgar bo'ladi. Balans yangilanadi (cashback + bottles).

        ACCEPTED/DELIVERING'dan ham qabul qiladi (back-compat, eski tugmalar uchun).
        """
        return await self._transition(
            order_id,
            courier_telegram_id,
            from_=(OrderStatus.ACCEPTED, OrderStatus.DELIVERING, OrderStatus.ARRIVED),
            to=OrderStatus.DELIVERED,
            stamp_delivered=True,
        )

    async def unclaim(self, order_id: int) -> Order:
        """Claim'ni qaytarish — DM yuborilmaganda buyurtmani yana NEW holatiga qaytaramiz."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get_full(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            if order.status != OrderStatus.ACCEPTED:
                # faqat ACCEPTED'ni qaytaramiz; aks holda — no-op
                return order
            order.courier = None
            order.status = OrderStatus.NEW
            order.accepted_at = None
            order.courier_dm_message_id = None
            await uow.orders.add(order)
            await uow.session.refresh(order, attribute_names=["customer", "items"])
            return order

    async def archive(self, order_id: int) -> Order:
        """SOFT DELETE — buyurtmani arxivga (admin "tozalash"). CANCELLED'dan
        farqi: CANCELLED biznes oqimi (bekor qilindi), DELETED — umuman ko'rinmaydi
        (stat/finance/mijoz "Buyurtmalarim"da yo'q). Idempotent."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get_full(order_id)
            if order is None or order.is_deleted:
                if order is None:
                    raise EntityNotFoundError("order_not_found")
                return order
            await uow.orders.soft_delete(order)
            return order

    async def restore(self, order_id: int) -> Order:
        """Arxivlangan buyurtmani qaytaradi (admin)."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get_full(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            if not order.is_deleted:
                return order
            return await uow.orders.restore(order)

    async def cancel(self, order_id: int) -> Order:
        """Buyurtmani bekor qiladi va ushlab qo'yilgan keshbekni qaytaradi."""
        async with UnitOfWork(self._sf) as uow:
            order = await uow.orders.get_full(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            if order.status.is_terminal:
                raise InvalidOperationError("order_already_closed")

            # Eskirgan keshbek (cashback_used) ni mijozga qaytaramiz.
            if order.cashback_used and order.cashback_used > 0:
                user = await uow.users.get_for_update(order.customer_id)
                if user is not None:
                    user.cashback_balance = (
                        Decimal(user.cashback_balance or 0) + Decimal(order.cashback_used or 0)
                    ).quantize(Decimal("0.01"))
                    await uow.users.add(user)

            order.status = OrderStatus.CANCELLED
            order.cancelled_at = datetime.now(timezone.utc)
            await uow.orders.add(order)
            await uow.session.refresh(order, attribute_names=["courier", "customer", "items"])
            return order

    async def _transition(
        self,
        order_id: int,
        courier_telegram_id: int,
        *,
        from_: tuple[OrderStatus, ...],
        to: OrderStatus,
        stamp_delivered: bool = False,
    ) -> Order:
        async with UnitOfWork(self._sf) as uow:
            courier = await uow.couriers.get_by_telegram_id(courier_telegram_id)
            if courier is None:
                raise InvalidOperationError("courier_not_registered")
            order = await uow.orders.get_full(order_id)
            if order is None:
                raise EntityNotFoundError("order_not_found")
            if order.courier_id != courier.id:
                raise InvalidOperationError("order_not_yours")
            if order.status not in from_:
                raise InvalidOperationError(
                    "order_state_invalid",
                    context={"status": order.status.label_uz},
                )
            order.status = to
            now = datetime.now(timezone.utc)
            if to == OrderStatus.DELIVERING and order.delivering_at is None:
                order.delivering_at = now
            if to == OrderStatus.ARRIVED and order.arrived_at is None:
                order.arrived_at = now
            if stamp_delivered:
                order.delivered_at = now
                # DELIVERED bo'lganda:
                #   1) Keshbekni qo'shamiz (cashback_earned)
                #   2) Idishlar balansi: +issued −returned
                #   3) Kuryer NAQD balansi: += total_amount (mijozdan olingan naqd)
                user = await uow.users.get_for_update(order.customer_id)
                if user is not None:
                    if order.cashback_earned and order.cashback_earned > 0:
                        user.cashback_balance = (
                            Decimal(user.cashback_balance or 0) + Decimal(order.cashback_earned or 0)
                        ).quantize(Decimal("0.01"))
                    new_bottles = (
                        int(user.bottles_balance or 0)
                        + int(order.bottles_issued or 0)
                        - int(order.bottles_returned or 0)
                    )
                    if new_bottles < 0:
                        new_bottles = 0
                    user.bottles_balance = new_bottles
                    await uow.users.add(user)
                # Kuryer qo'lidagi naqd — mijoz to'lagan summa (total_amount).
                # Keshbek bilan qoplangan qism naqd EMAS, total_amount allaqachon
                # net (items_total − cashback_used), shuning uchun aynan shu qo'shiladi.
                # ATOMIK: kuryer qatorini LOCK qilamiz — admin aynan shu payt
                # settle qilsa, lost-update bo'lmasin (user balansi kabi).
                cash = Decimal(order.total_amount or 0)
                if cash > 0:
                    locked_courier = await uow.couriers.get_for_update(courier.id) or courier
                    locked_courier.cash_balance = (
                        Decimal(locked_courier.cash_balance or 0) + cash
                    ).quantize(Decimal("0.01"))
                    await uow.couriers.add(locked_courier)
            await uow.orders.add(order)
            await uow.session.refresh(order, attribute_names=["courier", "customer", "items"])
            return order
