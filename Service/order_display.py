"""Buyurtma raqamini ko'rsatish (display) — yagona manba.

Barcha joy (bot xabarlari, notification formatter'lar, REST API serialization)
shu funksiyani ishlatadi — format hamma joyda BIR XIL bo'lsin.

Format:
  * `daily_number` mavjud bo'lsa → "YYYYMMDD-NN"  (masalan "20260607-03")
    - YYYYMMDD = buyurtma yaratilgan Toshkent mahalliy sanasi
    - NN = kunlik raqam, kamida 2 xona (03, 12, 105)
    - Sana prefiks tufayli global UNIQUE (kunlik raqam o'zi unique emas)
  * `daily_number` NULL bo'lsa (eski, migration'gacha buyurtmalar) → "#1043"
    - Eski `id` ga fallback (xulq-atvor buzilmaydi)
"""
from __future__ import annotations

from datetime import timedelta, timezone


def _local_tz():
    """Toshkent (yoki config'dagi) timezone. Topilmasa UTC+5 fallback."""
    try:
        from config import get_settings
        from zoneinfo import ZoneInfo
        return ZoneInfo(get_settings().timezone)
    except Exception:
        return timezone(timedelta(hours=5))


def order_display_number(order) -> str:
    """Buyurtma uchun odamlar ko'radigan raqam (string).

    `order` — `daily_number`, `created_at`, `id` atributlariga ega obyekt
    (SQLAlchemy Order modeli yoki shunga o'xshash).
    """
    daily = getattr(order, "daily_number", None)
    if daily is None:
        # Eski buyurtma — global id ga fallback
        return f"#{order.id}"
    created = getattr(order, "created_at", None)
    if created is None:
        # created_at yo'q bo'lsa (kutilmaydi) — faqat raqamni ko'rsatamiz
        return f"#{int(daily):02d}"
    try:
        local = created.astimezone(_local_tz())
    except Exception:
        local = created
    return f"{local.strftime('%Y%m%d')}-{int(daily):02d}"
