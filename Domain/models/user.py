from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, List

from sqlalchemy import BigInteger, Boolean, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from Domain.models.base import Base, SoftDeleteMixin, TimestampMixin

if TYPE_CHECKING:
    from Domain.models.address import CustomerAddress
    from Domain.models.order import Order


class User(Base, TimestampMixin, SoftDeleteMixin):
    """Mijoz (mahsulot buyurtma qiluvchi).

    `cashback_balance` va `bottles_balance` — Service qatlami orqali atomik
    yangilanadi (buyurtma yaratish / status o'zgarishi paytida). DB tomonida
    CHECK constraint'lar manfiy qiymatlardan saqlaydi.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)

    # Keshbek hisobi — so'mda (integer ball emas, lekin tiyinlarsiz Decimal).
    # Default 0; negativ qiymat — DB CHECK orqali bloklanadi.
    cashback_balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0.00"),
    )
    # Mijoz qo'lidagi bo'sh idishlar (baklashka) soni.
    # Buyurtma yaratilganda yangi idishlar berilsa +, mijoz qaytarsa −.
    bottles_balance: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )

    # Mijoz botga /start bosgan-bosmaganligi.
    #   * True  — mijoz bot bilan o'zaro aloqada, DM xabarlar yuboriladi
    #   * False — operator yaratgan "guest" mijoz (yoki bot bilan hech qachon
    #             muloqot qilmagan). Telegram DM xabar yuborilmaydi (silent skip).
    # Operator yaratgan mijozlarda sintetik manfiy `telegram_id` ishlatiladi.
    has_started_bot: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    # Avto-eslatma ("suv kerakmi?") — mijoz xohlamasa o'chirib qo'yiladi (opt-out).
    # Default True; kelajakda profil sahifasida toggle qilinishi mumkin.
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True,
    )

    orders: Mapped[List["Order"]] = relationship(back_populates="customer", lazy="selectin")
    addresses: Mapped[List["CustomerAddress"]] = relationship(
        back_populates="customer",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="CustomerAddress.id.asc()",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User id={self.id} tg={self.telegram_id} name={self.full_name!r}>"
