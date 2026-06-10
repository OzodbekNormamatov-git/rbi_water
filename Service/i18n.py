"""Xato kodlari va localizatsiya lug'ati.

Service qatlami xato matnini emas, **kodini** qaytaradi. Bot/WebApp matnga
aylantiradi. Buning ustunligi:
  1. Yangi til qo'shilsa, faqat lug'atni yangilash kerak.
  2. Service'lar sof biznes mantiqda qoladi (UI text yo'q).
  3. Front-end ham kod orqali xatoga reaktsiya qila oladi.

Foydalanish:
    raise ValidationError(code="cart_empty")
    raise ValidationError(code="phone_invalid", phone=phone_value)

Bot/WebApp tomonda:
    from Service.i18n import translate
    msg = translate(err.code, locale="uz", **err.context)
"""
from __future__ import annotations

from typing import Dict


# Til ↦ kod ↦ shablon
_MESSAGES: Dict[str, Dict[str, str]] = {
    "uz": {
        # Cart / order
        "cart_empty":            "Savatcha bo'sh.",
        "cart_item_qty_invalid": "Mahsulot soni 0 dan katta bo'lishi kerak.",
        "cart_item_qty_too_big": "Bir mahsulotda {max} dan oshmasin.",
        "item_below_minimum":    "\"{name}\" uchun minimal buyurtma {min} dona. Iltimos, miqdorni oshiring.",
        "food_unavailable":      "Mahsulot #{food_id} hozir mavjud emas.",
        "food_not_found":        "Mahsulot topilmadi.",
        "order_not_found":       "Buyurtma topilmadi.",
        "order_already_closed":  "Buyurtma allaqachon yopilgan.",
        "order_not_yours":       "Bu buyurtma sizniki emas.",
        "order_state_invalid":   "Holatni o'zgartirib bo'lmaydi: {status}.",
        "order_already_claimed": "Buyurtmani allaqachon boshqa kuryer oldi yoki holati: {status}.",
        "note_empty":            "Buyurtmaga izoh kiritish majburiy.",
        "note_too_long":         "Izoh juda uzun ({max} belgidan ko'p emas).",
        # Location
        "location_required":     "Yetkazib berish manzili (lokatsiya) kerak.",
        "location_invalid":      "Lokatsiya koordinatalari noto'g'ri.",
        # Phone
        "phone_required":        "Aloqa telefoni kerak.",
        "phone_invalid":         "Telefon raqam noto'g'ri formatda. Masalan: +998901234567",
        "phone_taken":           "Bu telefon raqam boshqa hisobga biriktirilgan.",
        # User
        "user_not_registered":   "Avval ro'yxatdan o'ting (/start).",
        "name_too_short":        "Ism juda qisqa.",
        # Food (admin)
        "name_short":            "Nom juda qisqa.",
        "price_invalid":         "Narx noto'g'ri formatda.",
        "price_positive":        "Narx 0 dan katta bo'lishi kerak.",
        "food_min_qty_invalid":  "Minimal buyurtma soni {min} va {max} oralig'ida bo'lishi shart.",
        # Courier
        "courier_not_registered":         "Kuryer ro'yxatda yo'q.",
        "cash_amount_invalid":            "Naqd summa noto'g'ri.",
        "cash_settle_exceeds":            "Kuryerda atigi {available} so'm naqd bor — bundan ko'pini qabul qilib bo'lmaydi.",
        "courier_not_started_bot":        "Avval kuryer botiga shaxsiy yozib /start yuboring.",
        "courier_not_active":             "Hisobingiz hali aktivlashtirilmagan. Admin bilan bog'lanib, sizni aktiv qilib qo'yishini so'rang.",
        "courier_has_active_order":       "Sizda tugallanmagan buyurtma bor ({ids}). Avval uni yopib, keyin yangisini oling.",
        # Address book
        "address_label_required":         "Manzilga nom bering (masalan, \"Uy\").",
        "address_label_too_long":         "Manzil nomi juda uzun ({max} belgidan ko'p emas).",
        "address_details_too_long":       "Manzil tafsilotlari juda uzun ({max} belgidan ko'p emas).",
        "address_label_taken":            "Bu nomdagi manzil allaqachon mavjud. Boshqa nom tanlang.",
        "address_not_found":              "Manzil topilmadi.",
        "address_limit_reached":          "Manzillar soni cheklovga yetdi ({max} ta). Avvalgilaridan birini o'chiring.",
        # Cashback
        "cashback_not_enough":            "Hisobingizda yetarli keshbek yo'q (mavjud: {available}).",
        "cashback_over_limit":            "Bitta buyurtmada keshbek bilan eng ko'pi bilan {ratio_percent}% ulushni qoplash mumkin.",
        "cashback_negative":              "Keshbek miqdori manfiy bo'la olmaydi.",
        "cashback_disabled":              "Keshbek dasturi hozir o'chirilgan. Iltimos, keshbeksiz buyurtma bering.",
        # Settings (admin)
        "settings_percent_out_of_range":  "Keshbek foizi {min}% va {max}% oralig'ida bo'lishi shart.",
        "settings_ratio_out_of_range":    "Keshbek bilan qoplash chegarasi {min} va {max} oralig'ida bo'lishi shart.",
        # Bottles
        "bottles_out_of_range":           "Idishlar soni 0..{max} oralig'ida bo'lishi shart.",
        "bottles_return_exceeds_balance": "Mijozda atigi {available} ta idish mavjud, {requested} ta qaytarib bo'lmaydi.",
        # Broadcast
        "broadcast_body_required":        "Xabar matni bo'sh bo'la olmaydi.",
        "broadcast_body_too_long":        "Xabar matni juda uzun ({max} belgidan ko'p emas).",
        "broadcast_caption_too_long":     "Rasm bilan yuborilayotgan matn {max} belgidan ko'p bo'lmasligi kerak.",
        "broadcast_title_too_long":       "Sarlavha juda uzun ({max} belgidan ko'p emas).",
        "broadcast_not_found":            "Rassilka topilmadi.",
        "broadcast_already_running":      "Bu rassilka allaqachon yuborilmoqda.",
        # Misc
        "internal_error":        "Server xatosi yuz berdi.",
    }
}

DEFAULT_LOCALE = "uz"


def translate(code: str, locale: str = DEFAULT_LOCALE, **context: object) -> str:
    """Xato kodini foydalanuvchi tilidagi matnga aylantiradi.

    Noma'lum kod bo'lsa kodning o'zi qaytariladi (degraded, lekin debug uchun
    foydali — nima yetishmayotganini ko'rasiz).
    """
    table = _MESSAGES.get(locale) or _MESSAGES[DEFAULT_LOCALE]
    template = table.get(code) or _MESSAGES[DEFAULT_LOCALE].get(code) or code
    try:
        return template.format(**context)
    except (KeyError, IndexError):
        return template
