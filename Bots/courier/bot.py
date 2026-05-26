from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from Domain.enums import OrderStatus as _OrderStatus
from Service.courier_service import CourierService
from Service.exceptions import DomainError
from Service.notification_service import NotificationService
from Service.notifications.formatters import (
    format_courier_confirmation,
    make_courier_confirmation_kb,
)
from Service.order_service import OrderService

log = logging.getLogger(__name__)


def _pretty_name(user) -> str:
    parts = [user.first_name or "", user.last_name or ""]
    name = " ".join(p for p in parts if p).strip()
    return name or (user.username or f"tg{user.id}")


# Reply tugmalari (markerlar — F.text == ... bilan tutiladi).
BTN_STATS = "📊 Statistikam"
BTN_SHARE_PHONE = "📞 Telefonimni yuborish"


def _courier_main_kb(*, needs_phone: bool = False) -> ReplyKeyboardMarkup:
    """Kuryer asosiy menyusi.

    `needs_phone=True` bo'lsa, "📞 Telefonimni yuborish" tugmasi ham qo'shiladi —
    `request_contact=True` orqali Telegram contact share dialog ochadi.
    Telefon kiritilgandan keyin bu tugma yashiriladi (faqat statistika qoladi).
    """
    rows = [[KeyboardButton(text=BTN_STATS)]]
    if needs_phone:
        rows.append([KeyboardButton(text=BTN_SHARE_PHONE, request_contact=True)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def build_courier_dispatcher(
    *,
    courier_service: CourierService,
    order_service: OrderService,
    notification_service: NotificationService,
    courier_group_chat_id: int,
) -> Dispatcher:
    dp = Dispatcher()

    # ------------------------------------------------------------------
    # /start  — DM da: kuryerni ro'yxatga olamiz va has_started_bot=True
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
                "Endi kuryerlar guruhida yangi buyurtmalarda <b>«Men olaman»</b> tugmasini "
                "bosishingiz mumkin. Buyurtma sizga biriktirilgach, barcha ma'lumotlar "
                "(manzil, lokatsiya, mijoz tel.) shu chatga keladi."
            )
        else:
            text = (
                "👋 Salom! Siz kuryer sifatida ro'yxatga olindingiz, ammo hisobingiz hali "
                "<b>noaktiv</b>. Iltimos, admin bilan bog'lanib, sizni aktiv qilib qo'yishini "
                "so'rang. Aktiv qilingach, kuryerlar guruhidan «Men olaman» tugmasini bosib "
                "buyurtma olishingiz mumkin bo'ladi."
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
                "Bu guruh kuryerlar uchun. Yangi buyurtmalar shu yerga keladi."
            )

    # ------------------------------------------------------------------
    # DM: telefon raqamni saqlash (contact share)
    # ------------------------------------------------------------------
    #
    # Kuryer "📞 Telefonimni yuborish" tugmasini bosganda (yoki istalgan vaqtda
    # boshqa contact yuborganda) — telefon DB ga yoziladi. Faqat o'z raqamini
    # qabul qilamiz (boshqa odamning contact'ini emas — chunki kuryer adashib
    # yuborishi mumkin).
    # ------------------------------------------------------------------

    @dp.message(F.chat.type == ChatType.PRIVATE, F.contact)
    async def on_contact(message: Message) -> None:
        contact = message.contact
        # Telegram: contact.user_id — agar shu raqam egasi Telegram'da bo'lsa.
        # Bizning kontekstda kuryerning o'z raqami bo'lishi shart, shuning uchun
        # user_id berilgan bo'lsa va u kuryerniki bo'lmasa — rad etamiz.
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
    # DM: Statistikam
    # ------------------------------------------------------------------

    @dp.message(F.chat.type == ChatType.PRIVATE, F.text == "📊 Statistikam")
    @dp.message(Command("stats"), F.chat.type == ChatType.PRIVATE)
    async def my_stats(message: Message) -> None:
        courier = await courier_service.get_by_telegram_id(message.from_user.id)
        if courier is None:
            await message.answer("Avval /start bosing.")
            return
        stats = await order_service.delivered_stats_for_courier(courier.id)
        await message.answer(
            "📊 <b>Sizning statistikangiz</b> (yetkazib berilgan zakazlar):\n\n"
            f"• Bugun: <b>{stats.today}</b>\n"
            f"• Shu oyda: <b>{stats.month}</b>\n"
            f"• Shu yilda: <b>{stats.year}</b>\n"
            f"• Hammasi: <b>{stats.total}</b>",
            reply_markup=_courier_main_kb(),
        )

    # ------------------------------------------------------------------
    # Group: «Men olaman»
    # ------------------------------------------------------------------

    @dp.callback_query(F.data.startswith("order:claim:"))
    async def on_claim(cb: CallbackQuery) -> None:
        if not cb.message or cb.message.chat.id != courier_group_chat_id:
            await cb.answer("Bu tugma kuryerlar guruhida bosiladi.", show_alert=True)
            return

        # Username/ism sinxron qilamiz (mark_started ga tegmaymiz — uni faqat DM /start belgilaydi).
        await courier_service.get_or_register(
            telegram_id=cb.from_user.id,
            full_name=_pretty_name(cb.from_user),
            username=cb.from_user.username,
        )

        order_id = int(cb.data.split(":")[2])
        try:
            order = await order_service.claim_by_courier(order_id, cb.from_user.id)
        except DomainError as e:
            await cb.answer(str(e), show_alert=True)
            return

        # Guruh xabarini "olindi" holatga keltirish (tugmalar olib tashlanadi)
        await notification_service.mark_group_message_claimed(order)

        # Kuryerga DM yuborish — agar bot bloklangan bo'lsa, claim'ni bekor qilamiz.
        try:
            dm_msg_id = await notification_service.send_order_to_courier_dm(order)
        except TelegramForbiddenError:
            log.warning(
                "Kuryer tg=%s botni bloklagan/start bosmagan — claim bekor qilinadi (order=%s)",
                cb.from_user.id, order.id,
            )
            await courier_service.mark_bot_unreachable(cb.from_user.id)
            order = await order_service.unclaim(order.id)
            await notification_service.reopen_group_message(order)
            await cb.answer(
                "Botga shaxsiy yozolmadik. Avval kuryer botiga DM dan /start yuboring, "
                "keyin guruhda «Men olaman» ni qayta bosing.",
                show_alert=True,
            )
            return

        if dm_msg_id is None:
            log.error("Claim muvaffaqiyatli, lekin DM yuborilmadi: order=%s", order.id)
            await cb.answer(
                "Buyurtma sizga biriktirildi, ammo DM yuborilmadi. "
                "Iltimos, botga /start yuboring va admin bilan bog'laning.",
                show_alert=True,
            )
        else:
            await order_service.attach_courier_dm_message(order.id, dm_msg_id)
            await cb.answer("✅ Siz oldingiz! DM ga qarang.")

        # Mijozga xabar — yagona "holat lentasi"ni edit qilamiz (yangi yuborilsa msg_id saqlaymiz).
        try:
            new_msg_id = await notification_service.upsert_customer_status_message(order)
            if new_msg_id is not None:
                await order_service.attach_customer_dm_message(order.id, new_msg_id)
        except (TelegramAPIError, OSError) as e:
            log.warning("Mijoz status xabarini yangilab bo'lmadi #%s: %s", order.id, e)

    # ------------------------------------------------------------------
    # DM: tranzitsiyalar (bu callbacklar faqat DM da bo'lishi kerak)
    # ------------------------------------------------------------------

    async def _sync_customer_timeline(order) -> None:
        """Mijozning yagona "holat lentasi" xabarini yangilash (network xato — silent)."""
        try:
            new_msg_id = await notification_service.upsert_customer_status_message(order)
            if new_msg_id is not None:
                await order_service.attach_customer_dm_message(order.id, new_msg_id)
        except (TelegramAPIError, OSError) as e:
            log.warning("Mijoz timeline xabarini yangilab bo'lmadi #%s: %s", order.id, e)

    @dp.callback_query(F.data.startswith("order:delivering:"))
    async def on_delivering(cb: CallbackQuery) -> None:
        if not cb.message or cb.message.chat.type != ChatType.PRIVATE:
            await cb.answer("Bu tugmani shaxsiy chatda bosing.", show_alert=True)
            return
        order_id = int(cb.data.split(":")[2])
        try:
            order = await order_service.mark_delivering(order_id, cb.from_user.id)
        except DomainError as e:
            await cb.answer(str(e), show_alert=True)
            return
        await notification_service.update_courier_dm_message(order)
        await _sync_customer_timeline(order)
        await cb.answer("🚗 Yo'lga chiqildi.")

    @dp.callback_query(F.data.startswith("order:arrived:"))
    async def on_arrived(cb: CallbackQuery) -> None:
        """Kuryer manzilga yetib keldi — DELIVERING → ARRIVED.

        Bu yerda mijozga ALOHIDA bildirishnoma yuboriladi ("yetib keldi!"),
        asosiy timeline xabari ham yangilanadi. Kuryer DM da tasdiqlash tugmasi paydo bo'ladi.
        """
        if not cb.message or cb.message.chat.type != ChatType.PRIVATE:
            await cb.answer("Bu tugmani shaxsiy chatda bosing.", show_alert=True)
            return
        order_id = int(cb.data.split(":")[2])
        try:
            order = await order_service.mark_arrived(order_id, cb.from_user.id)
        except DomainError as e:
            await cb.answer(str(e), show_alert=True)
            return
        # Kuryer DM tugmasini yangilash (endi "Buyurtmani yopish" tugmasi)
        await notification_service.update_courier_dm_message(order)
        # Asosiy timeline xabariga ARRIVED qatorini qo'shamiz
        await _sync_customer_timeline(order)
        # ALOHIDA push-notification mijozga (e'tibor jalb qilish)
        try:
            arrived_msg_id = await notification_service.send_customer_arrived_alert(order)
            if arrived_msg_id is not None:
                await order_service.attach_customer_arrived_message(order.id, arrived_msg_id)
        except (TelegramAPIError, OSError) as e:
            log.warning("Mijozga 'yetib keldi' xabarini yuborib bo'lmadi #%s: %s", order.id, e)
        await cb.answer("📍 Yetib keldingiz. Mijozga xabar yuborildi.")

    @dp.callback_query(F.data == "noop")
    async def on_noop(cb: CallbackQuery) -> None:
        """Stepper o'rta ko'rsatkichi — bosish ma'noli emas, faqat silent ack."""
        await cb.answer()

    @dp.callback_query(F.data.startswith("order:confirm:"))
    async def on_confirm_open(cb: CallbackQuery) -> None:
        """ARRIVED holatdagi buyurtma uchun tasdiqlash sahifasini ko'rsatadi.

        Kuryer DM dagi joriy xabarni edit qiladi — yangi xabar yubormaymiz,
        chat tozaroq turishi uchun. Bo'sh idishlar stepper (+/−) ham shu yerda.
        Boshlang'ich qiymat — order.bottles_returned (yangi orderda 0).
        """
        if not cb.message or cb.message.chat.type != ChatType.PRIVATE:
            await cb.answer("Bu tugmani shaxsiy chatda bosing.", show_alert=True)
            return
        order_id = int(cb.data.split(":")[2])
        order = await order_service.get(order_id)
        # Faqat haqiqiy biriktirilgan kuryer va ARRIVED holatda
        if order.courier is None or order.courier.telegram_id != cb.from_user.id:
            await cb.answer("Bu buyurtma sizga biriktirilmagan.", show_alert=True)
            return
        if order.status != _OrderStatus.ARRIVED:
            await cb.answer("Buyurtma hozir tasdiqlash holatida emas.", show_alert=True)
            return
        bottles = int(order.bottles_returned or 0)
        try:
            await cb.message.edit_text(
                text=format_courier_confirmation(order),
                reply_markup=make_courier_confirmation_kb(order.id, bottles_returned=bottles),
                disable_web_page_preview=True,
            )
        except TelegramAPIError as e:
            # Edit imkonsiz — yangi xabar yuboramiz fallback
            log.info("Tasdiqlash sahifasini edit qilib bo'lmadi #%s: %s — yangi yuboriladi", order.id, e)
            await cb.message.answer(
                text=format_courier_confirmation(order),
                reply_markup=make_courier_confirmation_kb(order.id, bottles_returned=bottles),
                disable_web_page_preview=True,
            )
        await cb.answer()

    @dp.callback_query(F.data.startswith("order:btl:"))
    async def on_bottles_step(cb: CallbackQuery) -> None:
        """Bo'sh idishlar stepper (+/−) — ARRIVED holatdagi confirmation sahifasida.

        Callback formati: `order:btl:inc:<id>` yoki `order:btl:dec:<id>`.
        Har bosishda DB'da `order.bottles_returned` yangilanadi va xabar matni
        (raqam) edit qilinadi. Validatsiya `OrderService.set_bottles_returned`
        ichida (state, courier, max chegara).
        """
        if not cb.message or cb.message.chat.type != ChatType.PRIVATE:
            await cb.answer("Bu tugmani shaxsiy chatda bosing.", show_alert=True)
            return
        try:
            _, _, action, order_id_s = cb.data.split(":")
            order_id = int(order_id_s)
        except (ValueError, IndexError):
            await cb.answer()
            return

        # Joriy qiymatni o'qib, +1/-1 qilamiz
        order = await order_service.get(order_id)
        current = int(order.bottles_returned or 0)
        new_value = current + 1 if action == "inc" else max(0, current - 1)
        if new_value == current:
            # Pastki chegara (0) — silent
            await cb.answer()
            return
        try:
            order = await order_service.set_bottles_returned(
                order_id, cb.from_user.id, new_value,
            )
        except DomainError as e:
            await cb.answer(str(e), show_alert=True)
            return

        # Matn ham yangilanadi (qator: "Mijozdan olingan bo'sh idishlar: N ta")
        try:
            await cb.message.edit_text(
                text=format_courier_confirmation(order),
                reply_markup=make_courier_confirmation_kb(
                    order.id, bottles_returned=int(order.bottles_returned or 0),
                ),
                disable_web_page_preview=True,
            )
        except TelegramAPIError as e:
            log.info("Stepper edit qilib bo'lmadi #%s: %s", order.id, e)
        await cb.answer()

    @dp.callback_query(F.data.startswith("order:back_to_dm:"))
    async def on_confirm_back(cb: CallbackQuery) -> None:
        """Kuryer tasdiqlash sahifasidan orqaga qaytsa — DM matnini qaytaramiz."""
        if not cb.message or cb.message.chat.type != ChatType.PRIVATE:
            await cb.answer("Bu tugmani shaxsiy chatda bosing.", show_alert=True)
            return
        order_id = int(cb.data.split(":")[2])
        order = await order_service.get(order_id)
        # `update_courier_dm_message` shu xabarni qaytadan edit qiladi (DM matn + tugma)
        await notification_service.update_courier_dm_message(order)
        await cb.answer()

    @dp.callback_query(F.data.startswith("order:delivered:"))
    async def on_delivered(cb: CallbackQuery) -> None:
        """Yakuniy tasdiq: ARRIVED (yoki avval — DELIVERING) → DELIVERED.

        Bu yerda:
          1) Order DELIVERED qilinadi (balans yangilanadi — cashback + bottles)
          2) Mijozning "yetib keldi" alohida xabari o'chiriladi
          3) Asosiy timeline xabariga DELIVERED qatori qo'shiladi
          4) Kuryer DM xabari yopilgan ko'rinishga keladi (tugmasiz)
        """
        if not cb.message or cb.message.chat.type != ChatType.PRIVATE:
            await cb.answer("Bu tugmani shaxsiy chatda bosing.", show_alert=True)
            return
        order_id = int(cb.data.split(":")[2])
        try:
            order = await order_service.mark_delivered(order_id, cb.from_user.id)
        except DomainError as e:
            await cb.answer(str(e), show_alert=True)
            return

        # 1) Mijozning ARRIVED alohida xabarini o'chirib (bo'lsa)
        if order.customer_arrived_message_id:
            try:
                await notification_service.delete_customer_arrived_alert(order)
            except (TelegramAPIError, OSError) as e:
                log.info("ARRIVED xabarini o'chirishda muammo #%s: %s", order.id, e)
            await order_service.clear_customer_arrived_message(order.id)

        # 2) Asosiy timeline'ni yangilash (DELIVERED qatori qo'shiladi)
        await _sync_customer_timeline(order)

        # 3) Kuryer DM xabari (matn + tugmasiz — order yopilgan ko'rinishi)
        await notification_service.update_courier_dm_message(order)

        await cb.message.answer(
            "✅ Buyurtma yopildi. Endi yangi buyurtma olishingiz mumkin.",
            reply_markup=_courier_main_kb(),
        )
        await cb.answer("📦 Yetkazib berildi. Rahmat!")

    return dp


def make_courier_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
