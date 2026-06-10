"""Admin paneli resurslar API: orders / products / couriers / customers."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from Data.unit_of_work import UnitOfWork
from Domain.enums import OrderStatus
from Service.courier_service import CourierService
from Service.exceptions import (
    DomainError,
    EntityNotFoundError,
    InvalidOperationError,
    ValidationError,
)
from Service.food_service import FoodService
from Service.ledger_service import LedgerService
from Service.order_display import order_display_number
from Service.order_service import OrderService
from webapp.admin.auth import admin_required, operator_required, role_of
from webapp.auth import TelegramUser
from webapp.deps import (
    _container,
    get_courier_service,
    get_food_service,
    get_ledger_service,
    get_order_service,
)
from webapp.pagination import Page

log = logging.getLogger(__name__)

# Mahsulot rasmlari uchun media katalogi (`Bots/common.py:save_food_photo`
# bilan bir xil joyga yozamiz — Mini App va admin botning rasm yuklash
# pattern'lari yagona disk topologiyasiga ega bo'lsin).
_MEDIA_ROOT = Path(__file__).resolve().parent.parent.parent / "media"
_FOODS_DIR = _MEDIA_ROOT / "foods"
_FOOD_ALLOWED_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_FOOD_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — broadcast'dagi limit bilan bir xil


async def _save_food_image(file: UploadFile) -> str:
    """UploadFile'ni media/foods/<uuid>.<ext> ga saqlab, nisbiy yo'lni qaytaradi.

    Validatsiya: kengaytma whitelist, hajm chegarasi (5MB) streaming oqimda
    tekshiriladi (xotirada to'liq saqlash shart emas — katta fayllarda DoS oldi).
    """
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _FOOD_ALLOWED_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"Faqat {', '.join(sorted(_FOOD_ALLOWED_EXTS))} formatdagi rasmlar qabul qilinadi.",
        )
    _FOODS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    abs_path = _FOODS_DIR / name
    size = 0
    try:
        with open(abs_path, "wb") as f:
            while True:
                chunk = await file.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _FOOD_MAX_BYTES:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Rasm juda katta ({_FOOD_MAX_BYTES // 1024 // 1024} MB dan oshmasin).",
                    )
                f.write(chunk)
    except HTTPException:
        # Yarim yozilgan faylni tozalaymiz — orfan qoldirmaymiz.
        try:
            abs_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    # Loyihaga nisbatan path (`media/foods/xxx.jpg`) — Food.image_file_id da
    # shu format saqlanadi (Bots/common.py:save_food_photo bilan moslik).
    return f"media/foods/{name}"


def _delete_food_image_file(rel_path: Optional[str]) -> None:
    """Disk'dan rasm faylini o'chiradi (eski rasm yangisi bilan almashtirilganda).

    Best-effort: xato silent. Faqat `media/foods/...` formatidagi yo'llar
    qabul qilinadi (path traversal himoyasi).
    """
    if not rel_path or not rel_path.startswith("media/foods/"):
        return
    try:
        abs_path = _MEDIA_ROOT.parent / rel_path
        # Yana bir himoya — abs_path haqiqatan _FOODS_DIR ichida ekanini tekshiramiz.
        abs_path.resolve().relative_to(_FOODS_DIR.resolve())
    except (ValueError, OSError):
        return
    try:
        abs_path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("Mahsulot rasmini o'chirib bo'lmadi (%s): %s", rel_path, e)


# ============================ Orders ============================

orders_router = APIRouter(prefix="/api/admin/orders", tags=["admin:orders"])


class AdminOrderItemOut(BaseModel):
    food_id: Optional[int]
    food_name: str
    unit_price: Decimal
    quantity: int


class AdminCourierBrief(BaseModel):
    id: int
    full_name: str
    username: Optional[str] = None
    telegram_id: int
    phone_number: Optional[str] = None


class AdminCustomerBrief(BaseModel):
    id: int
    full_name: str
    phone_number: str
    telegram_id: int


class AdminOrderOut(BaseModel):
    id: int
    daily_number: Optional[int] = None
    display_number: str
    status: str
    status_label: str
    total_amount: Decimal
    contact_phone: str
    note: str = ""
    latitude: float
    longitude: float
    map_url: str
    created_at: Optional[str] = None
    accepted_at: Optional[str] = None
    delivering_at: Optional[str] = None
    arrived_at: Optional[str] = None
    delivered_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    customer: AdminCustomerBrief
    courier: Optional[AdminCourierBrief] = None
    items: List[AdminOrderItemOut] = []


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _to_admin_order(o) -> AdminOrderOut:
    return AdminOrderOut(
        id=o.id,
        daily_number=getattr(o, "daily_number", None),
        display_number=order_display_number(o),
        status=o.status.name,
        status_label=o.status.label_uz,
        total_amount=o.total_amount,
        contact_phone=o.contact_phone,
        note=o.note or "",
        latitude=float(o.delivery_latitude),
        longitude=float(o.delivery_longitude),
        map_url=f"https://maps.google.com/?q={o.delivery_latitude},{o.delivery_longitude}",
        created_at=_iso(o.created_at),
        accepted_at=_iso(o.accepted_at),
        delivering_at=_iso(getattr(o, "delivering_at", None)),
        arrived_at=_iso(getattr(o, "arrived_at", None)),
        delivered_at=_iso(o.delivered_at),
        cancelled_at=_iso(o.cancelled_at),
        customer=AdminCustomerBrief(
            id=o.customer.id,
            full_name=o.customer.full_name,
            phone_number=o.customer.phone_number,
            telegram_id=o.customer.telegram_id,
        ),
        courier=(
            AdminCourierBrief(
                id=o.courier.id,
                full_name=o.courier.full_name,
                username=o.courier.username,
                telegram_id=o.courier.telegram_id,
                phone_number=o.courier.phone_number,
            ) if o.courier else None
        ),
        items=[
            AdminOrderItemOut(
                food_id=it.food_id,
                food_name=it.food_name,
                unit_price=it.unit_price,
                quantity=it.quantity,
            )
            for it in (o.items or [])
        ],
    )


@orders_router.get("", response_model=Page[AdminOrderOut])
async def list_orders(
    user: TelegramUser = Depends(operator_required),
    c=Depends(_container),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    customer_id: Optional[int] = Query(default=None),
    courier_id: Optional[int] = Query(default=None),
    since_iso: Optional[str] = Query(default=None, alias="since"),
    until_iso: Optional[str] = Query(default=None, alias="until"),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AdminOrderOut]:
    """Admin barcha buyurtmalarni ko'radi; operator faqat o'zi yaratganlarni."""
    sf = c.order_service._sf  # type: ignore[attr-defined]
    st = None
    if status_filter:
        try:
            st = OrderStatus[status_filter.upper()]
        except KeyError:
            raise HTTPException(status_code=400, detail="Noma'lum status")
    since = datetime.fromisoformat(since_iso) if since_iso else None
    until = datetime.fromisoformat(until_iso) if until_iso else None
    # Operator role — server tomondan operator ID bilan cheklaymiz.
    operator_filter: Optional[int] = None
    if role_of(user.id, c) == "operator":
        operator_filter = int(user.id)
    async with UnitOfWork(sf) as uow:
        total = await uow.orders.count_filtered(
            status_filter=st, since=since, until=until,
            customer_id=customer_id, courier_id=courier_id,
            created_by_operator_id=operator_filter,
        )
        rows = await uow.orders.list_filtered(
            status_filter=st, since=since, until=until,
            customer_id=customer_id, courier_id=courier_id,
            created_by_operator_id=operator_filter,
            limit=limit, offset=offset,
        )
        return Page[AdminOrderOut](
            items=[_to_admin_order(o) for o in rows],
            total=total, limit=limit, offset=offset,
        )


@orders_router.get("/{order_id}", response_model=AdminOrderOut)
async def get_order(
    order_id: int,
    user: TelegramUser = Depends(operator_required),
    c=Depends(_container),
    orders: OrderService = Depends(get_order_service),
) -> AdminOrderOut:
    try:
        order = await orders.get(order_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    # Operator faqat o'zi yaratgan buyurtmani ko'rishi mumkin
    if role_of(user.id, c) == "operator":
        if int(order.created_by_operator_id or 0) != int(user.id):
            raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    return _to_admin_order(order)


@orders_router.post("/{order_id}/cancel", response_model=AdminOrderOut)
async def cancel_order(
    order_id: int,
    _=Depends(admin_required),
    orders: OrderService = Depends(get_order_service),
) -> AdminOrderOut:
    try:
        order = await orders.cancel(order_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Buyurtma topilmadi")
    except DomainError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return _to_admin_order(order)


# ============================ Products ============================

products_router = APIRouter(prefix="/api/admin/products", tags=["admin:products"])


class AdminProductOut(BaseModel):
    id: int
    name: str
    description: str = ""
    price: Decimal
    # Minimal buyurtma soni (1 = cheklov yo'q)
    min_quantity: int = 1
    is_available: bool
    image_path: Optional[str] = None
    deleted_at: Optional[str] = None   # ISO sana — arxivlangan bo'lsa

    model_config = ConfigDict(from_attributes=True)


class ProductCreateIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=2000)
    price: Decimal = Field(gt=0)
    # Minimal buyurtma soni — 1..999 (service'da ham validatsiya)
    min_quantity: int = Field(default=1, ge=1, le=999)


class ProductUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    price: Optional[Decimal] = Field(default=None, gt=0)
    is_available: Optional[bool] = None
    min_quantity: Optional[int] = Field(default=None, ge=1, le=999)


def _to_admin_product(f) -> AdminProductOut:
    return AdminProductOut(
        id=f.id, name=f.name, description=f.description or "",
        price=f.price,
        min_quantity=int(getattr(f, "min_quantity", 1) or 1),
        is_available=f.is_available,
        image_path=f.image_file_id,
        deleted_at=f.deleted_at.isoformat() if getattr(f, "deleted_at", None) else None,
    )


@products_router.get("", response_model=Page[AdminProductOut])
async def list_products(
    user: TelegramUser = Depends(operator_required),
    c=Depends(_container),
    foods: FoodService = Depends(get_food_service),
    archived: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AdminProductOut]:
    """Mahsulotlar ro'yxati paginatsiya bilan.

    * Admin: aktiv + ?archived=true bo'lsa arxivlanganlar
    * Operator: faqat aktiv (Arxiv tab admin-only)
    """
    if archived and role_of(user.id, c) != "admin":
        raise HTTPException(status_code=403, detail="Arxiv faqat adminlar uchun.")
    items, total = await foods.list_paginated(
        archived=archived, limit=limit, offset=offset,
    )
    return Page[AdminProductOut](
        items=[_to_admin_product(f) for f in items],
        total=total, limit=limit, offset=offset,
    )


@products_router.post("/{food_id}/restore", response_model=AdminProductOut)
async def restore_product(
    food_id: int,
    _=Depends(admin_required),
    foods: FoodService = Depends(get_food_service),
) -> AdminProductOut:
    """Arxivlangan mahsulotni qaytarish."""
    try:
        food = await foods.restore(food_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    return _to_admin_product(food)


@products_router.post("", response_model=AdminProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(
    payload: ProductCreateIn,
    _=Depends(admin_required),
    foods: FoodService = Depends(get_food_service),
) -> AdminProductOut:
    try:
        food = await foods.create(
            name=payload.name,
            description=payload.description,
            price=payload.price,
            image_file_id=None,
            min_quantity=payload.min_quantity,
        )
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_admin_product(food)


@products_router.patch("/{food_id}", response_model=AdminProductOut)
async def update_product(
    food_id: int,
    payload: ProductUpdateIn,
    _=Depends(admin_required),
    foods: FoodService = Depends(get_food_service),
) -> AdminProductOut:
    try:
        food = await foods.update(
            food_id,
            name=payload.name,
            description=payload.description,
            price=payload.price,
            is_available=payload.is_available,
            min_quantity=payload.min_quantity,
        )
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_admin_product(food)


@products_router.delete("/{food_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=None)
async def delete_product(
    food_id: int,
    _=Depends(admin_required),
    foods: FoodService = Depends(get_food_service),
):
    from fastapi.responses import Response
    try:
        await foods.delete(food_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@products_router.post("/{food_id}/image", response_model=AdminProductOut)
async def upload_product_image(
    food_id: int,
    photo: UploadFile = File(..., description="JPG/PNG/WEBP, max 5 MB"),
    _=Depends(admin_required),
    foods: FoodService = Depends(get_food_service),
) -> AdminProductOut:
    """Mahsulot rasmini yuklash yoki almashtirish (multipart/form-data).

    Eski rasm (mavjud bo'lsa) disk'dan o'chiriladi. Yangi rasm
    `media/foods/<uuid>.<ext>` ga yoziladi va `Food.image_file_id` ustuni
    shu nisbiy yo'l bilan yangilanadi.
    """
    # Avval mahsulot bor-yo'qligini tekshiramiz (404 birinchi, rasm yozishdan oldin).
    try:
        old_food = await foods.get(food_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    old_image = old_food.image_file_id

    # Yangi rasmni diskka yozamiz.
    rel_path = await _save_food_image(photo)

    # DB'da yangilaymiz.
    try:
        food = await foods.update(food_id, image_file_id=rel_path)
    except EntityNotFoundError:
        # Race: yozish jarayonida o'chirilgan — yangi rasmni tozalaymiz.
        _delete_food_image_file(rel_path)
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    except ValidationError as e:
        _delete_food_image_file(rel_path)
        raise HTTPException(status_code=400, detail=str(e))

    # Eski rasm endi yetim — diskdan o'chiramiz (best-effort).
    if old_image and old_image != rel_path:
        _delete_food_image_file(old_image)
    return _to_admin_product(food)


@products_router.delete("/{food_id}/image", response_model=AdminProductOut)
async def delete_product_image(
    food_id: int,
    _=Depends(admin_required),
    foods: FoodService = Depends(get_food_service),
) -> AdminProductOut:
    """Mahsulot rasmini olib tashlaydi (mahsulotning o'zi qoladi)."""
    try:
        old_food = await foods.get(food_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    old_image = old_food.image_file_id
    if not old_image:
        return _to_admin_product(old_food)

    try:
        food = await foods.update(food_id, image_file_id="")
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mahsulot topilmadi")
    # FoodService.update'da image_file_id="" bo'lsa, ustunni "" qiladi.
    # Aslida NULL kerak — service'da bo'sh string'ni NULL'ga aylantirish kerak.
    # Hozircha update orqali "" yoziladi, diskdan rasm o'chiriladi.
    _delete_food_image_file(old_image)
    return _to_admin_product(food)


# ============================ Couriers ============================

couriers_router = APIRouter(prefix="/api/admin/couriers", tags=["admin:couriers"])


class AdminCourierOut(BaseModel):
    id: int
    telegram_id: int
    full_name: str
    username: Optional[str] = None
    phone_number: Optional[str] = None
    is_active: bool
    has_started_bot: bool
    delivered_today: int = 0
    delivered_month: int = 0
    delivered_total: int = 0
    # Kuryer qo'lidagi naqd pul (DELIVERED'larda yig'ilgan, hali topshirilmagan)
    cash_balance: Decimal = Decimal("0.00")


class CourierUpdateIn(BaseModel):
    """Admin PATCH — kuryer maydonlarini yangilash.

    Faqat berilgan maydonlar yangilanadi (PATCH semantikasi). Eski client'lar
    `is_active` bilan kelishi mumkin — moslik saqlangan.
    """
    is_active: Optional[bool] = None
    phone_number: Optional[str] = Field(default=None, max_length=24)


@couriers_router.get("", response_model=Page[AdminCourierOut])
async def list_couriers(
    _=Depends(admin_required),
    c=Depends(_container),
    archived: bool = Query(default=False),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AdminCourierOut]:
    """Kuryerlar ro'yxati paginatsiya + bitta aggregate query (N+1 yo'q).

    `stats_per_courier` har bir kuryer uchun (today, month, total) DELIVERED
    sonini bitta GROUP BY so'rovda hisoblaydi. Eski variant har kuryer uchun
    3 ta count chaqirardi — yuzdan ortiq kuryerda sezilarli sekinlashardi.
    """
    from datetime import datetime, timezone
    sf = c.courier_service._sf  # type: ignore[attr-defined]
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today_start.replace(day=1)
    async with UnitOfWork(sf) as uow:
        total = await uow.couriers.count(archived=archived)
        items = await uow.couriers.list_paginated(
            archived=archived, limit=limit, offset=offset,
        )
        stats = await uow.orders.stats_per_courier(
            [k.id for k in items],
            today_start=today_start, month_start=month_start,
        )
        out: List[AdminCourierOut] = []
        for k in items:
            today, month, total_d = stats.get(int(k.id), (0, 0, 0))
            out.append(AdminCourierOut(
                id=k.id, telegram_id=k.telegram_id, full_name=k.full_name,
                username=k.username, phone_number=k.phone_number,
                is_active=k.is_active,
                has_started_bot=k.has_started_bot,
                delivered_today=today,
                delivered_month=month,
                delivered_total=total_d,
                cash_balance=Decimal(k.cash_balance or 0),
            ))
        return Page[AdminCourierOut](
            items=out, total=total, limit=limit, offset=offset,
        )


class CourierCashSummaryOut(BaseModel):
    """Barcha kuryerlar qo'lidagi jami naqd (admin nazorati)."""
    total_cash: Decimal
    couriers_with_cash: int


@couriers_router.get("/cash-summary", response_model=CourierCashSummaryOut)
async def couriers_cash_summary(
    _=Depends(admin_required),
    couriers: CourierService = Depends(get_courier_service),
) -> CourierCashSummaryOut:
    """Kuryerlarda jami qancha naqd 'yo'lda' ekanini ko'rsatadi."""
    total_cash, with_cash = await couriers.total_cash_outstanding()
    return CourierCashSummaryOut(
        total_cash=Decimal(str(total_cash)),
        couriers_with_cash=with_cash,
    )


class CourierSettleCashIn(BaseModel):
    """Kuryer naqd topshirdi. `amount` berilmasa — to'liq balans (hammasi)."""
    amount: Optional[Decimal] = Field(default=None, gt=0)


@couriers_router.post("/{courier_id}/settle-cash", response_model=AdminCourierOut)
async def settle_courier_cash(
    courier_id: int,
    payload: CourierSettleCashIn,
    _=Depends(admin_required),
    couriers: CourierService = Depends(get_courier_service),
    orders: OrderService = Depends(get_order_service),
) -> AdminCourierOut:
    """Admin kuryerdan naqd pulni qabul qildi — balansidan ayiradi.

    `amount=None` → hammasini topshirdi (balans 0 bo'ladi).
    """
    try:
        c = await couriers.settle_cash(courier_id, amount=payload.amount)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Kuryer topilmadi")
    except InvalidOperationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    stats = await orders.delivered_stats_for_courier(c.id)
    return AdminCourierOut(
        id=c.id, telegram_id=c.telegram_id, full_name=c.full_name,
        username=c.username, phone_number=c.phone_number,
        is_active=c.is_active,
        has_started_bot=c.has_started_bot,
        delivered_today=stats.today,
        delivered_month=stats.month,
        delivered_total=stats.total,
        cash_balance=Decimal(c.cash_balance or 0),
    )


@couriers_router.patch("/{courier_id}", response_model=AdminCourierOut)
async def update_courier(
    courier_id: int,
    payload: CourierUpdateIn,
    _=Depends(admin_required),
    couriers: CourierService = Depends(get_courier_service),
    orders: OrderService = Depends(get_order_service),
) -> AdminCourierOut:
    """Kuryer maydonlarini yangilaydi: is_active va/yoki phone_number.

    PATCH semantikasi — faqat yuborilgan maydonlar yangilanadi. Hech qaysi
    maydon yuborilmagan bo'lsa, joriy holatni qaytaradi (no-op).
    """
    try:
        c = None
        if payload.phone_number is not None:
            # `phone_number=""` — telefonni tozalash; aks holda set_phone validate qiladi.
            phone = payload.phone_number.strip() or None
            c = await couriers.set_phone(courier_id, phone)
        if payload.is_active is not None:
            c = await couriers.set_active(courier_id, payload.is_active)
        if c is None:
            # Hech narsa berilmagan — joriy holatni qaytaramiz.
            c = await couriers.get(courier_id)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Kuryer topilmadi")
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    stats = await orders.delivered_stats_for_courier(c.id)
    return AdminCourierOut(
        id=c.id, telegram_id=c.telegram_id, full_name=c.full_name,
        username=c.username, phone_number=c.phone_number,
        is_active=c.is_active,
        has_started_bot=c.has_started_bot,
        delivered_today=stats.today,
        delivered_month=stats.month,
        delivered_total=stats.total,
        cash_balance=Decimal(c.cash_balance or 0),
    )


# ============================ Customers ============================

customers_router = APIRouter(prefix="/api/admin/customers", tags=["admin:customers"])


class AdminCustomerOut(BaseModel):
    id: int
    telegram_id: int
    full_name: str
    phone_number: str
    created_at: Optional[str] = None
    orders_count: int = 0
    total_spent: float = 0.0
    cashback_balance: Decimal = Decimal("0.00")
    bottles_balance: int = 0


@customers_router.get("", response_model=Page[AdminCustomerOut])
async def list_customers(
    _=Depends(admin_required),
    c=Depends(_container),
    q: str = Query(default=""),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AdminCustomerOut]:
    """Mijozlar ro'yxati paginatsiya bilan + bitta aggregate query (N+1 yo'q).

    `stats_per_customer` GROUP BY orqali bir nechta mijoz uchun count/sum bitta
    so'rovda hisoblaydi. Eski variant har user uchun alohida `list_by_customer`
    chaqirardi — minglab mijozlar bo'lganda javob soniyalab cho'zilardi.
    """
    sf = c.user_service._sf  # type: ignore[attr-defined]
    async with UnitOfWork(sf) as uow:
        total = await uow.users.count_search(q)
        users = await uow.users.search(q, limit=limit, offset=offset)
        stats = await uow.orders.stats_per_customer([u.id for u in users])
        out: List[AdminCustomerOut] = []
        for u in users:
            cnt, spent = stats.get(int(u.id), (0, 0.0))
            out.append(AdminCustomerOut(
                id=u.id, telegram_id=u.telegram_id,
                full_name=u.full_name, phone_number=u.phone_number,
                created_at=u.created_at.isoformat() if u.created_at else None,
                orders_count=cnt,
                total_spent=spent,
                cashback_balance=Decimal(u.cashback_balance or 0),
                bottles_balance=int(u.bottles_balance or 0),
            ))
        return Page[AdminCustomerOut](items=out, total=total, limit=limit, offset=offset)


@customers_router.get("/{customer_id}/orders", response_model=Page[AdminOrderOut])
async def customer_orders(
    customer_id: int,
    _=Depends(admin_required),
    c=Depends(_container),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AdminOrderOut]:
    sf = c.user_service._sf  # type: ignore[attr-defined]
    async with UnitOfWork(sf) as uow:
        total = await uow.orders.count_filtered(customer_id=customer_id)
        rows = await uow.orders.list_filtered(
            customer_id=customer_id, limit=limit, offset=offset,
        )
        return Page[AdminOrderOut](
            items=[_to_admin_order(o) for o in rows],
            total=total, limit=limit, offset=offset,
        )


# ---------------------- Balance adjustments (admin) ----------------------

class CashbackAdjustIn(BaseModel):
    """Mijoz keshbek balansiga qo'shish/ayirish. delta — manfiy ham bo'lishi mumkin."""
    delta: Decimal = Field(description="So'mda. Manfiy = ayirish.")
    reason: str = Field(default="", max_length=200)


class BottlesAdjustIn(BaseModel):
    delta: int = Field(description="Manfiy = qaytarib oldi, musbat = qo'shimcha berdi.")
    reason: str = Field(default="", max_length=200)


class CustomerBalanceOut(BaseModel):
    customer_id: int
    cashback_balance: Decimal
    bottles_balance: int


@customers_router.post("/{customer_id}/cashback", response_model=CustomerBalanceOut)
async def adjust_cashback(
    customer_id: int,
    payload: CashbackAdjustIn,
    _=Depends(admin_required),
    ledger: LedgerService = Depends(get_ledger_service),
) -> CustomerBalanceOut:
    try:
        u = await ledger.adjust_cashback(customer_id, Decimal(str(payload.delta)), reason=payload.reason)
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    except InvalidOperationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return CustomerBalanceOut(
        customer_id=u.id,
        cashback_balance=Decimal(u.cashback_balance or 0),
        bottles_balance=int(u.bottles_balance or 0),
    )


@customers_router.post("/{customer_id}/bottles", response_model=CustomerBalanceOut)
async def adjust_bottles(
    customer_id: int,
    payload: BottlesAdjustIn,
    _=Depends(admin_required),
    ledger: LedgerService = Depends(get_ledger_service),
) -> CustomerBalanceOut:
    try:
        u = await ledger.adjust_bottles(customer_id, int(payload.delta))
    except EntityNotFoundError:
        raise HTTPException(status_code=404, detail="Mijoz topilmadi")
    except InvalidOperationError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return CustomerBalanceOut(
        customer_id=u.id,
        cashback_balance=Decimal(u.cashback_balance or 0),
        bottles_balance=int(u.bottles_balance or 0),
    )
