from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from Domain.models.base import Base, SoftDeleteMixin, TimestampMixin


class Food(Base, TimestampMixin, SoftDeleteMixin):
    """Sotuvdagi mahsulot (ovqat, suv va h.k.). Jadval nomi tarixiy sabablarga ko'ra `foods`.

    Soft delete:
      * `deleted_at IS NULL` — aktiv, mijoz ko'radi va admin Mahsulotlar ro'yxatida.
      * `deleted_at != NULL` — arxivlangan. Mijoz ko'rmaydi, lekin eski buyurtmalarda
        snapshot ko'rinadi. Admin "Arxiv" tab'idan qaytarishi mumkin.
      * `is_available=False` — vaqtinchalik (mahsulot bor lekin sotuvda emas).
        Soft delete'dan farqi: bu doim qaytarish mumkin, admin asosiy ro'yxatda ko'radi.
    """

    __tablename__ = "foods"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Minimal buyurtma soni — mijoz shu mahsulotdan kamida shuncha dona olishi shart.
    # Default 1 = cheklov yo'q. Cap: MAX_QUANTITY_PER_ITEM (999). Admin Mini App'da
    # belgilanadi; buyurtma yaratishda server item.quantity >= min_quantity tekshiradi.
    min_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    image_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
