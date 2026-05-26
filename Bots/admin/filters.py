from __future__ import annotations

from typing import Iterable

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


class IsAdminFilter(BaseFilter):
    """Faqat admin role'iga ruxsat. Operator'lar bunday handler'larga kira olmaydi."""

    def __init__(self, admin_ids: Iterable[int]) -> None:
        self._admin_ids = set(int(x) for x in admin_ids)

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user and user.id in self._admin_ids)


class IsAdminOrOperatorFilter(BaseFilter):
    """Admin yoki operator role'iga ruxsat. Faqat /start kabi umumiy handler'lar uchun."""

    def __init__(self, admin_ids: Iterable[int], operator_ids: Iterable[int]) -> None:
        self._ids = set(int(x) for x in admin_ids) | set(int(x) for x in operator_ids)

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return bool(user and user.id in self._ids)
