"""Kuryer bot — soddalashtirilgan.

Kuryerning butun ish oqimi (buyurtma olish, transitsiyalar, idish kiritish) endi
WEB ILOVADA (`/courier/`). Bot faqat:
  * /start — ro'yxatga olish + aktivlik holati + ilovaga yo'naltirish
  * 📞 telefon raqamni saqlash (contact share)
  * 📊 statistika (qisqacha; batafsil ilovada)
  * Yangi buyurtma DM bildirishnomasi — `NotificationService.notify_couriers_new_order`
    (bu funksiya buyurtma yaratilganda chaqiriladi, bot handler emas)

Kuryerlar guruhi endi faqat ADMIN uchun LOG (tugmasiz) — claim guruhda emas.
"""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from Bots.common import fmt_money
from Service.courier_service import CourierService
from Service.exceptions import DomainError
from Service.order_service import OrderService

log = logging.getLogger(__name__)


def _pretty_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (user.username or f"tg{user.id}")


BTN_STATS = "📊 Statistikam"
BTN_SHARE_PHONE = "📞 Telefonimni yuborish"


def _courier_main_kb(*, needs_phone: bool = False) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=BTN_STATS)]]
    if needs_phone:
        rows.append([KeyboardButton(text=BTN_SHARE_PHONE, request_contact=True)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_courier_dispatcher(
    *,
    courier_service: CourierService,
    order_service: OrderService,
    courier_group_chat_id: int,
) -> Dispatcher:
    dp = Dispatcher()

    # ------------------------------------------------------------------
    # /start — ro'yxatga olish + ilovaga yo'naltirish
    # ------------------------------------------------------------------
    @dp.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
    async def cmd_start_private(message: Message) -> None:
        courier = await courier_service.get_or_register(
            telegram_id=message.from_user.id,
            full_name=_pretty_name(message.from_user),
            username=message.from_user.username,
            mark_started=True,
        )
        needs_phone = not courier.phone_number
        kb = _courier_main_kb(needs_phone=needs_phone)
        if courier.is_active:
            text = (
                "👋 Salom! Siz kuryer sifatida ro'yxatdasiz va <b>aktiv</b> holatdasiz.\n\n"
                "📋 Yangi buyurtmalar shu yerga <b>xabar</b> bo'lib keladi va "
                "<b>«Buyurtmalar» ilovasida</b> ko'rinadi. Kim birinchi <b>«Men olaman»</b> "
                "bossa — o'shaniki. Olish, yo'lga chiqish va yopish — hammasi ilovada.\n\n"
                "Pastdagi yoki menyu tugmasi orqali ilovani oching."
            )
        else:
            text = (
                "👋 Salom! Siz kuryer sifatida ro'yxatga olindingiz, ammo hisobingiz hali "
                "<b>noaktiv</b>. Iltimos, admin bilan bog'lanib, sizni aktiv qilib qo'yishini "
                "so'rang. Aktiv qilingach, yangi buyurtmalar shu yerga keladi va ilovada ko'rinadi."
            )
        if needs_phone:
            text += (
                "\n\n📞 <b>Telefon raqamingizni yuboring</b> — mijozlar sizga to'g'ridan-to'g'ri "
                "qo'ng'iroq qila olishi uchun. Pastdagi <b>«Telefonimni yuborish»</b> tugmasini bosing."
            )
        await message.answer(text, reply_markup=kb)

    @dp.message(CommandStart())
    async def cmd_start_other(message: Message) -> None:
        if message.chat.id == courier_group_chat_id:
            await message.answer(
                "Bu guruh endi faqat admin uchun kuzatuv (log). Buyurtmalar kuryerlarga "
                "shaxsiy xabar va ilova orqali boradi."
            )

    # ------------------------------------------------------------------
    # Telefon raqamni saqlash (contact share)
    # ------------------------------------------------------------------
    @dp.message(F.chat.type == ChatType.PRIVATE, F.contact)
    async def on_contact(message: Message) -> None:
        contact = message.contact
        if contact.user_id and contact.user_id != message.from_user.id:
            await message.answer(
                "❗️ Iltimos, faqat <b>o'z</b> telefon raqamingizni yuboring "
                "(boshqa kontaktni emas)."
            )
            return
        try:
            courier = await courier_service.set_phone_by_telegram_id(
                message.from_user.id, contact.phone_number,
            )
        except DomainError as e:
            await message.answer(f"❗️ {e}")
            return
        if courier is None:
            await message.answer("Avval /start bosing.")
            return
        await message.answer(
            f"✅ Telefon raqamingiz saqlandi: <code>{courier.phone_number}</code>\n\n"
            f"Endi mijozlar buyurtma detalida sizga qo'ng'iroq qila olishadi.",
            reply_markup=_courier_main_kb(needs_phone=False),
        )

    # ------------------------------------------------------------------
    # 📊 Statistikam (qisqacha; batafsil ilovada)
    # ------------------------------------------------------------------
    @dp.message(F.chat.type == ChatType.PRIVATE, F.text == BTN_STATS)
    @dp.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
    async def my_stats(message: Message) -> None:
        courier = await courier_service.get_by_telegram_id(message.from_user.id)
        if courier is None:
            await message.answer("Avval /start bosing.")
            return
        stats = await order_service.delivered_stats_for_courier(courier.id)
        cash = courier.cash_balance or 0
        cash_line = (
            f"\n\n💵 <b>Qo'lingizdagi naqd:</b> {fmt_money(cash)}\n"
            f"<i>Bu summani kompaniyaga topshirishingiz kerak.</i>"
            if cash and float(cash) > 0 else ""
        )
        await message.answer(
            "📊 <b>Sizning statistikangiz</b> (yetkazib berilgan zakazlar):\n\n"
            f"• Bugun: <b>{stats.today}</b>\n"
            f"• Shu oyda: <b>{stats.month}</b>\n"
            f"• Shu yilda: <b>{stats.year}</b>\n"
            f"• Hammasi: <b>{stats.total}</b>"
            f"{cash_line}",
            reply_markup=_courier_main_kb(),
        )

    return dp


def make_courier_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
