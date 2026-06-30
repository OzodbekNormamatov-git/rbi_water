"""Kuryer Mini App API — /api/courier/*.

Buyurtmalar HAMMA aktiv kuryerga ko'rinadi (polling); birinchi "Olaman" bosgan
oladi. Race-safety mavjud `OrderService.claim_by_courier` (SELECT FOR UPDATE) —
bir vaqtda bir nechta bosilsa ham aniq bitta g'olib. Transitsiyalar ham o'sha
service metodlaridan o'tadi; bot va web bir xil mantiqqa tayanadi.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from Data.unit_of_work import UnitOfWork
from Domain.constants import MAX_BOTTLES_PER_TRANSACTION
from Domain.enums import OrderStatus
from Service.exceptions import DomainError, EntityNotFoundError, InvalidOperationError
from Service.notification_service import NotificationService
from Service.order_display import order_display_number
from Service.order_service import OrderService
from webapp.deps import (
    current_courier,
    get_notification_service,
    get_order_service,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/courier", tags=["courier"])


# ---------------------- Schemas ----------------------

class CourierMeOut(BaseModel):
    id: int
    full_name: str
    phone_number: Optional[str] = None
    is_active: bool
    cash_balance: Decimal = Decimal("0.00")
    active_order_id: Optional[int] = None


class CourierItemOut(BaseModel):
    food_name: str
    quantity: int
    unit_price: Decimal


class CourierOrderOut(BaseModel):
    id: int
    display_number: str
    status: str
    status_label: str
    total_amount: Decimal
    items: List[CourierItemOut] = []
    latitude: float
    longitude: float
    map_url: str
    address_label: str = ""
    address_details: str = ""
    note: str = ""
    bottles_issued: int = 0
    bottles_returned: int = 0
    created_at: Optional[str] = None
    # Faqat biriktirilgan kuryerga (active/claim) — mijoz telefoni:
    contact_phone: Optional[str] = None


class BottlesIn(BaseModel):
    value: int = Field(ge=0, le=MAX_BOTTLES_PER_TRANSACTION)


def _to_courier_order(o, *, include_phone: bool) -> CourierOrderOut:
    return CourierOrderOut(
        id=o.id,
        display_number=order_display_number(o),
        status=o.status.name,
        status_label=o.status.label_uz,
        total_amount=o.total_amount,
        items=[
            CourierItemOut(food_name=it.food_name, quantity=it.quantity, unit_price=it.unit_price)
            for it in (o.items or [])
        ],
        latitude=float(o.delivery_latitude),
        longitude=float(o.delivery_longitude),
        map_url=f"https://maps.google.com/?q={o.delivery_latitude},{o.delivery_longitude}",
        address_label=o.address_label or "",
        address_details=o.address_details or "",
        note=o.note or "",
        bottles_issued=int(o.bottles_issued or 0),
        bottles_returned=int(o.bottles_returned or 0),
        created_at=o.created_at.isoformat() if o.created_at else None,
        contact_phone=(o.contact_phone if include_phone else None),
    )


# ---------------------- Read ----------------------

@router.get("/me", response_model=CourierMeOut)
async def courier_me(
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
) -> CourierMeOut:
    sf = orders._sf  # type: ignore[attr-defined]
    async with UnitOfWork(sf) as uow:
        active = await uow.orders.list_active_by_courier(courier.id)
    active_id = active[0].id if active else None
    return CourierMeOut(
        id=courier.id,
        full_name=courier.full_name,
        phone_number=courier.phone_number,
        is_active=courier.is_active,
        cash_balance=Decimal(courier.cash_balance or 0),
        active_order_id=active_id,
    )


class CourierStatsOut(BaseModel):
    today: int = 0
    month: int = 0
    year: int = 0
    total: int = 0
    cash_balance: Decimal = Decimal("0.00")


@router.get("/stats", response_model=CourierStatsOut)
async def courier_stats(
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
) -> CourierStatsOut:
    s = await orders.delivered_stats_for_courier(courier.id)
    return CourierStatsOut(
        today=s.today, month=s.month, year=s.year, total=s.total,
        cash_balance=Decimal(courier.cash_balance or 0),
    )


@router.get("/available", response_model=List[CourierOrderOut])
async def available_orders(
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
    limit: int = Query(default=50, le=100),
) -> List[CourierOrderOut]:
    """Hali olinmagan (NEW) buyurtmalar — barcha aktiv kuryerga ko'rinadi."""
    sf = orders._sf  # type: ignore[attr-defined]
    async with UnitOfWork(sf) as uow:
        rows = await uow.orders.list_by_status(OrderStatus.NEW, limit=limit)
    # NEW buyurtmada mijoz telefoni ko'rsatilmaydi (hali biriktirilmagan).
    return [_to_courier_order(o, include_phone=False) for o in rows]


@router.get("/active", response_model=List[CourierOrderOut])
async def active_orders(
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
) -> List[CourierOrderOut]:
    """Kuryerning hozirgi (tugallanmagan) buyurtmalari — to'liq, telefon bilan."""
    sf = orders._sf  # type: ignore[attr-defined]
    async with UnitOfWork(sf) as uow:
        rows = await uow.orders.list_active_by_courier(courier.id)
    return [_to_courier_order(o, include_phone=True) for o in rows]


# ---------------------- Claim + transitions ----------------------

async def _sync_after_claim(order, orders: OrderService, notifier: NotificationService) -> None:
    """Claim'dan keyin: guruh xabarini yopish + mijoz timeline (best-effort)."""
    try:
        await notifier.mark_group_message_claimed(order)
    except Exception as e:
        log.warning("Guruh xabarini yopib bo'lmadi #%s: %s", order.id, e)
    # Kuryerga DM — web claim'da ixtiyoriy (kuryer ilovada). Best-effort, unclaim YO'Q.
    try:
        dm_id = await notifier.send_order_to_courier_dm(order)
        if dm_id is not None:
            await orders.attach_courier_dm_message(order.id, dm_id)
    except Exception as e:
        log.info("Kuryer DM yuborilmadi (web claim) #%s: %s", order.id, e)
    await _sync_customer_timeline(order, orders, notifier)


async def _sync_customer_timeline(order, orders: OrderService, notifier: NotificationService) -> None:
    try:
        msg_id = await notifier.upsert_customer_status_message(order)
        if msg_id is not None:
            await orders.attach_customer_dm_message(order.id, msg_id)
    except Exception as e:
        log.warning("Mijoz timeline yangilanmadi #%s: %s", order.id, e)


@router.post("/orders/{order_id}/claim", response_model=CourierOrderOut)
async def claim_order(
    order_id: int,
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
    notifier: NotificationService = Depends(get_notification_service),
) -> CourierOrderOut:
    """Buyurtmani olish — race-safe. Boshqa kuryer ulgursa 409."""
    try:
        order = await orders.claim_by_courier(order_id, courier.telegram_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    except InvalidOperationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await _sync_after_claim(order, orders, notifier)
    return _to_courier_order(order, include_phone=True)


async def _transition(method, order_id, courier_telegram_id):
    try:
        return await method(order_id, courier_telegram_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    except InvalidOperationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except DomainError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/orders/{order_id}/delivering", response_model=CourierOrderOut)
async def mark_delivering(
    order_id: int,
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
    notifier: NotificationService = Depends(get_notification_service),
) -> CourierOrderOut:
    order = await _transition(orders.mark_delivering, order_id, courier.telegram_id)
    try:
        await notifier.update_courier_dm_message(order)
    except Exception:
        pass
    await _sync_customer_timeline(order, orders, notifier)
    return _to_courier_order(order, include_phone=True)


@router.post("/orders/{order_id}/arrived", response_model=CourierOrderOut)
async def mark_arrived(
    order_id: int,
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
    notifier: NotificationService = Depends(get_notification_service),
) -> CourierOrderOut:
    order = await _transition(orders.mark_arrived, order_id, courier.telegram_id)
    try:
        await notifier.update_courier_dm_message(order)
    except Exception:
        pass
    await _sync_customer_timeline(order, orders, notifier)
    try:
        arrived_id = await notifier.send_customer_arrived_alert(order)
        if arrived_id is not None:
            await orders.attach_customer_arrived_message(order.id, arrived_id)
    except Exception as e:
        log.info("ARRIVED alert yuborilmadi #%s: %s", order.id, e)
    return _to_courier_order(order, include_phone=True)


@router.post("/orders/{order_id}/bottles", response_model=CourierOrderOut)
async def set_bottles(
    order_id: int,
    payload: BottlesIn,
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
) -> CourierOrderOut:
    """Yetkazishdan oldin mijozdan olingan bo'sh idishlar sonini kiritish."""
    order = await _transition(
        lambda oid, tg: orders.set_bottles_returned(oid, tg, payload.value),
        order_id, courier.telegram_id,
    )
    return _to_courier_order(order, include_phone=True)


@router.post("/orders/{order_id}/delivered", response_model=CourierOrderOut)
async def mark_delivered(
    order_id: int,
    courier=Depends(current_courier),
    orders: OrderService = Depends(get_order_service),
    notifier: NotificationService = Depends(get_notification_service),
) -> CourierOrderOut:
    """Yetkazib berildi — balans/jurnal yangilanadi (race-safe, order LOCK)."""
    order = await _transition(orders.mark_delivered, order_id, courier.telegram_id)
    # Mijoz ARRIVED alohida xabarini o'chirish (bo'lsa)
    if order.customer_arrived_message_id:
        try:
            await notifier.delete_customer_arrived_alert(order)
        except Exception:
            pass
        await orders.clear_customer_arrived_message(order.id)
    await _sync_customer_timeline(order, orders, notifier)
    try:
        await notifier.update_courier_dm_message(order)
    except Exception:
        pass
    return _to_courier_order(order, include_phone=True)
