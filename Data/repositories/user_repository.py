from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, or_, select

from Data.repositories.base import BaseRepository
from Domain.models.user import User


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        """Soft-deleted bo'lsa ham qaytaradi — restore va admin lookup uchun zarur."""
        res = await self._session.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalar_one_or_none()

    async def get_for_update(self, user_id: int) -> Optional[User]:
        """Pessimistic row-level lock — balans yangilash atomarligi uchun.
        Soft-deleted mijozning ham balansi yangilanishi mumkin (refund kabi)."""
        res = await self._session.execute(
            select(User).where(User.id == user_id).with_for_update()
        )
        return res.scalar_one_or_none()

    async def get_by_phone(self, phone: str) -> Optional[User]:
        res = await self._session.execute(select(User).where(User.phone_number == phone))
        return res.scalar_one_or_none()

    @staticmethod
    def _apply_search_filter(stmt, query: str):
        q = (query or "").strip()
        if not q:
            return stmt
        like = f"%{q}%"
        return stmt.where(or_(User.full_name.ilike(like), User.phone_number.ilike(like)))

    async def search(
        self, query: str = "", *, limit: int = 50, offset: int = 0,
    ) -> Sequence[User]:
        """Admin "Mijozlar" ro'yxati — faqat aktivlar."""
        stmt = self._active_only(
            self._apply_search_filter(select(User), query).order_by(User.created_at.desc())
        )
        stmt = stmt.offset(offset).limit(limit)
        res = await self._session.execute(stmt)
        return res.scalars().all()

    async def count_search(self, query: str = "") -> int:
        stmt = self._active_only(
            self._apply_search_filter(select(func.count(User.id)), query)
        )
        res = await self._session.execute(stmt)
        return int(res.scalar_one() or 0)

    async def count_since(self, since: datetime) -> int:
        """Yangi (aktiv) mijozlar — arxivlangan dashboard'da hisoblanmaydi."""
        stmt = self._active_only(
            select(func.count(User.id)).where(User.created_at >= since)
        )
        res = await self._session.execute(stmt)
        return int(res.scalar_one() or 0)

    async def count_all(self) -> int:
        stmt = self._active_only(select(func.count(User.id)))
        res = await self._session.execute(stmt)
        return int(res.scalar_one() or 0)

    async def signups_by_day_since(self, since: datetime) -> list[tuple[str, int]]:
        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "postgresql":
            day_expr = func.date_trunc("day", User.created_at)
        else:
            day_expr = func.strftime("%Y-%m-%d", User.created_at)
        stmt = self._active_only(
            select(day_expr.label("day"), func.count(User.id).label("count"))
            .where(User.created_at >= since)
            .group_by("day")
            .order_by("day")
        )
        res = await self._session.execute(stmt)
        return [(str(r.day)[:10], int(r.count or 0)) for r in res.all()]

    async def list_reminder_candidates(self) -> Sequence[User]:
        """Avto-eslatma nomzodlari: aktiv + botda faollashgan + opt-in + real ID.
        (Ochiq buyurtma / sikl / cooldown tekshiruvi service'da Python'da.)"""
        stmt = self._active_only(
            select(User).where(
                User.has_started_bot.is_(True),
                User.reminders_enabled.is_(True),
                User.telegram_id > 0,
            )
        )
        res = await self._session.execute(stmt)
        return res.scalars().all()

    async def list_all_telegram_ids(self) -> list[int]:
        """Broadcast uchun — faqat real, botda faollashgan aktiv mijozlar.

        Filtrlar:
          * `_active_only` — arxivlanganlar chiqarib tashlanadi
          * `telegram_id > 0` — operator yaratgan "guest" mijozlar sintetik
            manfiy ID ga ega; ularga DM yuborib bo'lmaydi (kafolatli failed)
          * `has_started_bot` — botga /start qilmaganlarga DM yuborib bo'lmaydi
        Shu tariqa broadcast.total/failed aniq bo'ladi va behuda API chaqiruv +
        rate-limit kechikishi sarflanmaydi."""
        res = await self._session.execute(
            self._active_only(
                select(User.telegram_id)
                .where(User.telegram_id > 0, User.has_started_bot.is_(True))
                .order_by(User.id.asc())
            )
        )
        return [int(r) for r in res.scalars().all()]

    async def cashback_liability_total(self) -> tuple[float, int]:
        """Faqat aktiv mijozlarning keshbek qarzlari — arxivlanganlar liability'dan chiqdi."""
        stmt = self._active_only(
            select(
                func.coalesce(func.sum(User.cashback_balance), 0),
                func.count(User.id),
            ).where(User.cashback_balance > 0)
        )
        res = await self._session.execute(stmt)
        row = res.first()
        return float(row[0] or 0), int(row[1] or 0)

    async def bottles_outstanding_total(self) -> tuple[int, int]:
        stmt = self._active_only(
            select(
                func.coalesce(func.sum(User.bottles_balance), 0),
                func.count(User.id),
            ).where(User.bottles_balance > 0)
        )
        res = await self._session.execute(stmt)
        row = res.first()
        return int(row[0] or 0), int(row[1] or 0)
