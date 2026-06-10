"""Notification matn quruvchilari — sof funksiyalar, Telegram ga bog'liq emas.

Test'lash oson: input → output. Hech qanday I/O yo'q.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as _tz
from typing import Optional

from Domain.enums import OrderStatus
from Domain.models.order import Order
from Service.order_display import order_daily_label, order_display_number


def _courier_head(order: Order) -> str:
    """Kuryer xabarlari uchun ENG TEPADAGI kunlik tartib raqami qatori.

    Kuryerlar kun davomida qisqa "№ 02" raqami bilan ishlaydi — to'liq
    "20260611-02" id pastda qoladi. Eski orderlarda (daily_number NULL) bo'sh.
    """
    label = order_daily_label(order)
    return f"<b>№ {label}</b>\n" if label else ""


# Toshkent vaqti — config'dan o'qish kerak edi, lekin formatter sof funksiya bo'lib
# qolishi uchun lazy importga aylantiramiz.
def _tz_local():
    try:
        from config import get_settings
        from zoneinfo import ZoneInfo
        return ZoneInfo(get_settings().timezone)
    except Exception:
        # Fallback: UTC+5 (Toshkent)
        return _tz(timedelta(hours=5))


def fmt_time(dt: Optional[datetime]) -> str:
    if dt is None:
        return ""
    try:
        local = dt.astimezone(_tz_local())
    except Exception:
        local = dt
    return local.strftime("%H:%M")


def maps_link(lat: float, lon: float) -> str:
    return f"https://maps.google.com/?q={lat},{lon}"


def _items_short(order: Order) -> str:
    lines = []
    for it in (order.items or []):
        lines.append(f"  • {it.food_name} × {it.quantity}")
    return "\n".join(lines)


def _items_with_total(order: Order) -> str:
    return "\n".join(
        f"• {it.food_name} × {it.quantity} = {it.line_total} so'm"
        for it in (order.items or [])
    )


# ---------------------- Couriers group ----------------------

def format_group_new(order: Order) -> str:
    """Kuryerlar guruhi: yangi (NEW) buyurtma. Operator yaratgan bo'lsa belgilanadi."""
    operator_badge = ""
    if getattr(order, "created_by_operator_id", None):
        operator_badge = f"  <i>(📞 operator orqali)</i>"
    return (
        f"{_courier_head(order)}"
        f"<b>🆕 Yangi buyurtma {order_display_number(order)}</b>{operator_badge}\n"
        f"Mijoz: {order.customer.full_name}\n"
        f"📍 Manzil: <a href='{maps_link(order.delivery_latitude, order.delivery_longitude)}'>"
        f"xaritada ko'rish</a> (lokatsiya pastda)\n"
        f"\n<b>Tarkibi:</b>\n{_items_with_total(order)}\n"
        f"\n<b>Jami:</b> {order.total_amount} so'm (naqd)\n"
        f"{('📝 ' + order.note) if order.note else ''}"
    ).rstrip()


def format_group_claimed(order: Order) -> str:
    """Kuryerlar guruhi: claim'dan keyin — faqat status."""
    courier = order.courier.full_name if order.courier else "Kuryer"
    return (
        f"{_courier_head(order)}"
        f"<b>✅ Buyurtma {order_display_number(order)}</b>\n"
        f"<b>{courier}</b> tomonidan olindi.\n"
        f"Davomi shaxsiy chatda."
    )


# ---------------------- Courier DM ----------------------

def format_dm_for_courier(order: Order) -> str:
    """Kuryerga DM da yuboriladigan to'liq ma'lumot — har transitsiyada qayta tahrirlanadi."""
    head = _courier_head(order).rstrip("\n")
    lines = ([head] if head else []) + [
        f"<b>Buyurtma {order_display_number(order)}</b>",
        f"Holati: <b>{order.status.label_uz}</b>",
        "",
        f"👤 Mijoz: {order.customer.full_name}",
        f"📞 Tel: <code>{order.contact_phone}</code>",
        f"📍 Manzil: <a href='{maps_link(order.delivery_latitude, order.delivery_longitude)}'>"
        f"xaritada ko'rish</a>",
        "",
        "<b>Tarkibi:</b>",
        _items_with_total(order),
        "",
        f"<b>Jami:</b> {order.total_amount} so'm (naqd kutiladi)",
    ]
    if order.note:
        lines.append(f"📝 {order.note}")
    return "\n".join(lines)


# ---------------------- Customer DM (yagona timeline) ----------------------

def format_customer_timeline(order: Order, brand_name: str) -> str:
    """Mijoz DM da yagona xabar — har transitsiyada o'sib boruvchi timeline."""
    head = f"<b>📦 Buyurtma {order_display_number(order)}</b>"
    status_line = f"Holat: <b>{order.status.label_uz}</b>"

    timeline: list[str] = []

    if order.created_at:
        timeline.append(f"{OrderStatus.NEW.emoji} <b>{fmt_time(order.created_at)}</b> — Buyurtma qabul qilindi")

    if order.accepted_at and order.courier is not None:
        courier_name = order.courier.full_name
        username = f" (@{order.courier.username})" if order.courier.username else ""
        # Telefon — alohida satr, `tel:` link Telegram'da bosiladi (mobile'da
        # darhol qo'ng'iroq dialogini ochadi). Telefon yo'q bo'lsa, qator ham yo'q.
        phone_line = ""
        if order.courier.phone_number:
            phone_line = (
                f'\n    📞 <a href="tel:{order.courier.phone_number}">'
                f'{order.courier.phone_number}</a>'
            )
        timeline.append(
            f"{OrderStatus.ACCEPTED.emoji} <b>{fmt_time(order.accepted_at)}</b> — "
            f"Kuryer biriktirildi: <b>{courier_name}</b>{username}{phone_line}"
        )

    if getattr(order, "delivering_at", None):
        timeline.append(
            f"{OrderStatus.DELIVERING.emoji} <b>{fmt_time(order.delivering_at)}</b> — Kuryer yo'lga chiqdi"
        )

    if getattr(order, "arrived_at", None):
        timeline.append(
            f"{OrderStatus.ARRIVED.emoji} <b>{fmt_time(order.arrived_at)}</b> — Kuryer yetib keldi"
        )

    if order.delivered_at:
        # Yetkazib berildi qatoriga — kuryer olib ketgan bo'sh idishlar haqida ma'lumot
        # (mijoz checkout'da kiritmaydi — kuryer yetkazganda hisoblaydi va saqlaydi)
        bottles_taken = int(order.bottles_returned or 0)
        bottles_info = (
            f"\n    ♻️ Bo'sh idishlar olindi: <b>{bottles_taken}</b> ta"
            if bottles_taken > 0
            else "\n    ♻️ Bo'sh idish olinmadi"
        )
        timeline.append(
            f"{OrderStatus.DELIVERED.emoji} <b>{fmt_time(order.delivered_at)}</b> — "
            f"Yetkazib berildi{bottles_info}"
        )

    if order.cancelled_at:
        timeline.append(
            f"{OrderStatus.CANCELLED.emoji} <b>{fmt_time(order.cancelled_at)}</b> — Bekor qilindi"
        )

    items = _items_short(order)
    total = f"💵 Jami: <b>{order.total_amount} so'm</b> (naqd kuryerga)"

    parts = [head, status_line, ""]
    if timeline:
        parts.extend(timeline)
        parts.append("")
    parts.append("<b>Tarkibi:</b>")
    if items:
        parts.append(items)
    parts.append("")
    parts.append(total)

    if order.status == OrderStatus.DELIVERED:
        parts.append("")
        parts.append(f"<b>{brand_name}</b> ni tanlaganingiz uchun minnatdormiz! 💧")

    return "\n".join(parts)


# ---------------------- Inline keyboards (composition root uchun) ----------------------

def make_group_new_kb(order_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text="✅ Men olaman", callback_data=f"order:claim:{order_id}")
        ]]
    )


def make_courier_dm_kb(order: Order):
    """Kuryer DM tugmalari — har bir holatga mos keladigan bitta tugma.

    Oqim: ACCEPTED → DELIVERING → ARRIVED → tasdiqlash sahifasi → DELIVERED
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    if order.status == OrderStatus.ACCEPTED:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="🚗 Yo'lga chiqdim",
                callback_data=f"order:delivering:{order.id}",
            )
        ]])
    if order.status == OrderStatus.DELIVERING:
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📍 Yetib keldim",
                callback_data=f"order:arrived:{order.id}",
            )
        ]])
    if order.status == OrderStatus.ARRIVED:
        # Tasdiqlash sahifasini ko'rsatish tugmasi (DELIVERED ga emas, summa ko'rinishiga)
        return InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text="📋 Buyurtmani yopish",
                callback_data=f"order:confirm:{order.id}",
            )
        ]])
    return None


# ---------------------- Customer ARRIVED notification ----------------------

def format_customer_arrived(order: Order) -> str:
    """Mijozga qisqa, e'tibor jalb qiluvchi "yetib keldi!" xabar.

    Asosiy timeline xabariga TEGMAYDI — bu alohida push-notification kabi
    yangi xabar. DELIVERED bo'lganda o'chiriladi (chiqindi qoldirmaymiz).
    """
    courier = order.courier
    courier_part = ""
    if courier:
        courier_part = f"\nKuryer: <b>{courier.full_name}</b>"
        if courier.username:
            courier_part += f" (@{courier.username})"
        if courier.phone_number:
            courier_part += (
                f'\n📞 <a href="tel:{courier.phone_number}">{courier.phone_number}</a>'
            )
    return (
        f"🔔 <b>Buyurtmangiz {order_display_number(order)} yetib keldi!</b>\n"
        f"Iltimos, kuryerni qarshi oling."
        f"{courier_part}"
    )


# ---------------------- Courier confirmation page (ARRIVED → DELIVERED) ----------------------

def format_courier_confirmation(order: Order, currency: str = "so'm") -> str:
    """Kuryer "Qabul qildim" oldidan ko'radigan sahifa.

    Kuryer bu yerda mijozdan olingan bo'sh idishlar sonini +/− tugmalari
    bilan kiritadi (mijoz checkout'da emas, bu yerda kiritiladi). So'ngra
    "Yetkazib berildi" bossa — buyurtma yopiladi va balans yangilanadi.
    """
    items_lines = "\n".join(
        f"  • {it.food_name} × {it.quantity}"
        for it in (order.items or [])
    ) or "  —"
    cash_amount = order.total_amount
    bottles_returned = int(order.bottles_returned or 0)

    cash_block = f"💵 <b>Naqd qabul qilaman:</b> {cash_amount} {currency}"
    if order.cashback_used and order.cashback_used > 0:
        # Mijoz keshbek bilan qoplagan qism — kuryer naqdda OLMAYDI
        cash_block += (
            f"\n   <i>(keshbek bilan {order.cashback_used} {currency} qoplangan, naqd kerak emas)</i>"
        )

    bottles_block = (
        f"♻️ <b>Mijozdan olingan bo'sh idishlar:</b> <b>{bottles_returned}</b> ta\n"
        f"   <i>Quyidagi +/− tugmalari bilan aniq sonni kiriting.</i>"
    )

    return (
        f"{_courier_head(order)}"
        f"<b>📋 Buyurtma {order_display_number(order)} — yakuniy tasdiq</b>\n\n"
        f"📦 <b>Mahsulotni topshiraman:</b>\n{items_lines}\n\n"
        f"{cash_block}\n\n"
        f"{bottles_block}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ <b>Diqqat:</b> tasdiqlashdan oldin yuqoridagi ma'lumotlarni tekshiring.\n"
        f"Bo'sh idishlar sonini to'g'ri kiritganingizga ishonch hosil qiling.\n\n"
        f"✅ <b>Yetkazib berildi</b> tugmasini bossangiz — pul, idishlar va mahsulotlar "
        f"uchun javobgarlikni o'z bo'yningizga olasiz va buyurtma yopiladi."
    )


def make_courier_confirmation_kb(order_id: int, bottles_returned: int = 0):
    """Tasdiqlash sahifasi tugmalari — bo'sh idishlar stepper + Yetkazib berildi / Orqaga.

    Layout:
        [➖ idish]   [N ta]   [➕ idish]
        [✅ Yetkazib berildi — buyurtmani yopish]
        [⬅️ Orqaga]
    """
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    bottles_returned = max(0, int(bottles_returned or 0))
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="➖",
                callback_data=f"order:btl:dec:{order_id}",
            ),
            InlineKeyboardButton(
                text=f"♻️ {bottles_returned} ta",
                callback_data="noop",  # markaziy ko'rsatkich — bosish ishlamaydi
            ),
            InlineKeyboardButton(
                text="➕",
                callback_data=f"order:btl:inc:{order_id}",
            ),
        ],
        [InlineKeyboardButton(
            text="✅ Yetkazib berildi — buyurtmani yopish",
            callback_data=f"order:delivered:{order_id}",
        )],
        [InlineKeyboardButton(
            text="⬅️ Orqaga",
            callback_data=f"order:back_to_dm:{order_id}",
        )],
    ])
