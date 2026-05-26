"""Domain darajadagi konstantalar — biznes qoidalar.

Bu fayl framework-free, faqat Python. Ham bot, ham webapp ishlatadi.
Magic raqamlar va string'lar shu yerga ko'chiriladi.
"""
from __future__ import annotations

from typing import Final

# ---------------------- Buyurtma cheklovlari ----------------------
MAX_QUANTITY_PER_ITEM: Final[int] = 999
MIN_QUANTITY_PER_ITEM: Final[int] = 1
MAX_ITEMS_PER_ORDER: Final[int] = 50
MAX_NOTE_LENGTH: Final[int] = 500

# Telefon raqam regexi (xalqaro format)
PHONE_REGEX: Final[str] = r"^\+?\d{9,15}$"

# Latitude/longitude chegaralari
LAT_MIN: Final[float] = -90.0
LAT_MAX: Final[float] = 90.0
LON_MIN: Final[float] = -180.0
LON_MAX: Final[float] = 180.0

# ---------------------- Manzillar xotirasi (Address Book) ----------------------
MAX_ADDRESSES_PER_USER: Final[int] = 10
MAX_ADDRESS_LABEL_LENGTH: Final[int] = 40
MAX_ADDRESS_DETAILS_LENGTH: Final[int] = 200

# ---------------------- Keshbek (Cashback) ----------------------
# Default qiymatlar — `AppSettings` jadvalida birinchi qator yaratilganda
# ishlatiladi. Live qiymatlar admin tomonidan o'zgartiriladi va DB'dan o'qiladi.
DEFAULT_CASHBACK_PERCENT: Final[float] = 1.5
DEFAULT_MAX_CASHBACK_USAGE_RATIO: Final[float] = 1.00  # to'liq qoplash mumkin
# Eski kod uchun shim — ish vaqtida ishlatilmaydi (`AppSettings` ustun chaqiriladi).
MAX_CASHBACK_USAGE_RATIO: Final[float] = DEFAULT_MAX_CASHBACK_USAGE_RATIO
# Keshbekni hisoblashning birligi (mijoz QO'LGA OLAYOTGAN keshbek qadami).
# Misol: 1.5% of 47 230 = 708.45 → 700 (har 100 so'mga floor — mijozga foydali).
CASHBACK_ROUND_UNIT: Final[int] = 100

# Keshbekni ISHLATISH birligi — mijoz buyurtmada eng kam shuncha sumdan
# ko'paytirib qoplaydi. 1000 — slider step va minimal qoplash chegarasi.
# Misol: 1000, 2000, 5000 ✓; 1400, 5700 ✗ (floor to 1000).
CASHBACK_USE_UNIT: Final[int] = 1000

# ---------------------- Idishlar (bottle) hisobi ----------------------
# Bir buyurtmada qaytarilishi mumkin bo'lgan idishlar maksimumi
# (mijoz tasodifan katta son kiritmasin uchun himoya).
MAX_BOTTLES_PER_TRANSACTION: Final[int] = 50

# ---------------------- Broadcast / Rassilka ----------------------
MAX_BROADCAST_TITLE_LENGTH: Final[int] = 80
MAX_BROADCAST_BODY_LENGTH: Final[int] = 3500
# Yuborish oralig'i — Telegram per-bot rate limit (~30 msg/sec) ga moslab.
BROADCAST_SEND_DELAY_SECONDS: Final[float] = 0.05
