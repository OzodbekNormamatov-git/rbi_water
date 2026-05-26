from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import func, select

from Data.repositories.base import BaseRepository
from Domain.models.courier import Courier


class CourierRepository(BaseRepository[Courier]):
    model = Courier

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[Courier]:
        """Soft-deleted bo'lsa ham qaytaradi — admin restore va eski buyurtmalar uchun."""
        res = await self._session.execute(
            select(Courier).where(Courier.telegram_id == telegram_id)
        )
        return res.scalar_one_or_none()

    async def get_active_by_telegram_id(self, telegram_id: int) -> Optional[Courier]:
        """Faqat aktiv (deleted_at IS NULL) kuryerni qaytaradi — bot oqimlari uchun."""
        res = await self._session.execute(
            self._active_only(select(Courier).where(Courier.telegram_id == telegram_id))
        )
        return res.scalar_one_or_none()

    async def list_active(self) -> Sequence[Courier]:
        """Aktiv (arxivlanmagan) + is_active=True — kuryer guruh notification uchun."""
        stmt = self._active_only(
            select(Courier).where(Courier.is_active.is_(True))
        )
        res = await self._session.execute(stmt)
        return res.scalars().all()

    async def list_all_ordered(self) -> Sequence[Courier]:
        """Admin uchun: avval aktivlar, keyin ism bo'yicha. Faqat soft-active."""
        stmt = self._active_only(
            select(Courier).order_by(Courier.is_active.desc(), Courier.full_name.asc())
        )
        res = await self._session.execute(stmt)
        return res.scalars().all()

    async def list_archived(self) -> Sequence[Courier]:
        """Arxivlangan kuryerlar — admin "Arxiv" tab uchun."""
        stmt = self._deleted_only(select(Courier).order_by(Courier.deleted_at.desc()))
        res = await self._session.execute(stmt)
        return res.scalars().all()

    # ---------------------- Paginated (admin large lists) ----------------------

    async def list_paginated(
        self, *, archived: bool = False, limit: int = 50, offset: int = 0,
    ) -> Sequence[Courier]:
        """Paginatsiyalangan ro'yxat — `archived=True` Arxiv tab uchun."""
        base = select(Courier)
        if archived:
            stmt = self._deleted_only(base).order_by(Courier.deleted_at.desc())
        else:
            stmt = self._active_only(base).order_by(
                Courier.is_active.desc(), Courier.full_name.asc(),
            )
        stmt = stmt.offset(offset).limit(limit)
        res = await self._session.execute(stmt)
        return res.scalars().all()

    async def count(self, *, archived: bool = False) -> int:
        base = select(func.count(Courier.id))
        stmt = self._deleted_only(base) if archived else self._active_only(base)
        res = await self._session.execute(stmt)
        return int(res.scalar_one() or 0)
