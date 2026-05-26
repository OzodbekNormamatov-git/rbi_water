from __future__ import annotations

import enum


class OrderStatus(str, enum.Enum):
    """Buyurtma hayot tsikli:

        NEW ─claim─► ACCEPTED ─yo'lga chiqdim─► DELIVERING ─yetib keldim─►
            ARRIVED ─qabul qildim─► DELIVERED
            │
            └─ cancel (admin) ─► CANCELLED  (har qanday holatdan)

    Etaplar:
      * NEW         — yaratildi, hech qaysi kuryer olmagan
      * ACCEPTED    — kuryer guruhdan claim qildi, DM oldi
      * DELIVERING  — kuryer "Yo'lga chiqdim" bosdi, yo'lda
      * ARRIVED     — kuryer yetib keldi, mijozni kutmoqda
                      (mijozga "buyurtmangiz yetib keldi!" alohida bildirishnoma yuboriladi)
      * DELIVERED   — kuryer "Qabul qildim" tasdiqladi: pul + idishlar + yetkaziildi
                      (bildirishnoma o'chiriladi, kuryer yangi buyurtma olishi mumkin)
      * CANCELLED   — admin bekor qildi
    """

    NEW = "new"
    ACCEPTED = "accepted"
    DELIVERING = "delivering"
    ARRIVED = "arrived"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)

    @property
    def is_active(self) -> bool:
        """Tugallanmagan — kuryer yangi buyurtma olishi mumkinligi tekshiruvi uchun."""
        return self in (
            OrderStatus.NEW,
            OrderStatus.ACCEPTED,
            OrderStatus.DELIVERING,
            OrderStatus.ARRIVED,
        )

    @property
    def label_uz(self) -> str:
        return {
            OrderStatus.NEW: "Yangi",
            OrderStatus.ACCEPTED: "Qabul qilindi",
            OrderStatus.DELIVERING: "Yetkazilmoqda",
            OrderStatus.ARRIVED: "Yetib keldi",
            OrderStatus.DELIVERED: "Yetkazib berildi",
            OrderStatus.CANCELLED: "Bekor qilindi",
        }[self]

    @property
    def emoji(self) -> str:
        """Status uchun standart emoji — bot va webapp ishlatadi."""
        return {
            OrderStatus.NEW: "🆕",
            OrderStatus.ACCEPTED: "👤",
            OrderStatus.DELIVERING: "🚗",
            OrderStatus.ARRIVED: "📍",
            OrderStatus.DELIVERED: "✅",
            OrderStatus.CANCELLED: "❌",
        }[self]

    @property
    def color_token(self) -> str:
        """CSS rang token — frontend `--status-X` orqali stillash uchun."""
        return self.value
