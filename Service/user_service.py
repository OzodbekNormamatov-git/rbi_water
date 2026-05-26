from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from Data.unit_of_work import UnitOfWork
from Domain.models.user import User
from Service.exceptions import EntityNotFoundError, ValidationError

_PHONE_RE = re.compile(r"^\+?\d{9,15}$")


def _normalize_phone(raw: str) -> str:
    cleaned = re.sub(r"[\s\-()]", "", raw or "")
    if not _PHONE_RE.match(cleaned):
        raise ValidationError("phone_invalid")
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


@dataclass(slots=True)
class RegistrationInput:
    telegram_id: int
    full_name: str
    phone_number: str


class UserService:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_by_telegram_id(self, telegram_id: int) -> Optional[User]:
        async with UnitOfWork(self._sf) as uow:
            return await uow.users.get_by_telegram_id(telegram_id)

    async def get_by_phone(self, phone: str) -> Optional[User]:
        """Telefon orqali mijozni topadi (operator lookup uchun).

        Telefon avval normalize qilinmaydi — caller mas'ul (chunki noto'g'ri
        format `ValidationError` chiqarmasin, faqat None qaytarsin). Repository
        darajasida `phone_number` ustun bo'yicha aniq mos kelish izlanadi.
        """
        async with UnitOfWork(self._sf) as uow:
            return await uow.users.get_by_phone(phone)

    async def is_registered(self, telegram_id: int) -> bool:
        return (await self.get_by_telegram_id(telegram_id)) is not None

    async def mark_started_bot(self, telegram_id: int) -> None:
        """Mijoz botga /start bosganini belgilash — DM xabar yuborilishi uchun ruxsat."""
        async with UnitOfWork(self._sf) as uow:
            user = await uow.users.get_by_telegram_id(telegram_id)
            if user is None or user.has_started_bot:
                return
            user.has_started_bot = True
            await uow.users.add(user)

    async def find_or_create_for_operator(
        self, *, full_name: str, phone_number: str,
    ) -> User:
        """Operator orderi uchun mijoz topish/yaratish.

        Mantiq:
          1. Telefon orqali izlash — mavjud bo'lsa, qaytarish (eski mijoz)
          2. Yo'q bo'lsa — sintetik manfiy `telegram_id` bilan yangi mijoz yaratish
             (`has_started_bot=False`, DM xabar yuborilmaydi)

        Mijoz keyinroq botga /start yuborsa, alohida real `telegram_id` bilan
        ro'yxatdan o'tadi — bu yerda merge qilmaymiz (operator yaratgan mijoz
        "guest" sifatida qoladi, real registratsiya alohida hisob bo'ladi).
        """
        full_name = (full_name or "").strip()
        if len(full_name) < 2:
            raise ValidationError("name_too_short")
        phone = _normalize_phone(phone_number)

        async with UnitOfWork(self._sf) as uow:
            existing = await uow.users.get_by_phone(phone)
            if existing is not None:
                # Eski mijoz — arxivlangan bo'lsa, qayta tiklaymiz
                if existing.is_deleted:
                    await uow.users.restore(existing)
                # Ism o'zgargan bo'lsa, yangilaymiz (operator yangi ismni eshitgan bo'lishi mumkin)
                if existing.full_name != full_name:
                    existing.full_name = full_name
                    await uow.users.add(existing)
                return existing

            # Yangi mijoz — sintetik manfiy telegram_id
            # Vaqt asosida (millisekundlar) — unique va monotonik
            synthetic_tg = -int(time.time_ns() // 1000)
            user = User(
                telegram_id=synthetic_tg,
                full_name=full_name,
                phone_number=phone,
                has_started_bot=False,  # bot bilan ishlamaydi, DM yuborilmaydi
            )
            return await uow.users.add(user)

    async def register(self, data: RegistrationInput) -> User:
        full_name = (data.full_name or "").strip()
        if len(full_name) < 2:
            raise ValidationError("name_too_short")
        phone = _normalize_phone(data.phone_number)

        async with UnitOfWork(self._sf) as uow:
            existing = await uow.users.get_by_telegram_id(data.telegram_id)
            if existing:
                if existing.is_deleted:
                    await uow.users.restore(existing)
                existing.full_name = full_name
                existing.phone_number = phone
                existing.has_started_bot = True
                await uow.users.add(existing)
                return existing

            phone_owner = await uow.users.get_by_phone(phone)
            if phone_owner:
                raise ValidationError("phone_taken")

            user = User(
                telegram_id=data.telegram_id,
                full_name=full_name,
                phone_number=phone,
                has_started_bot=True,
            )
            return await uow.users.add(user)

    async def archive(self, user_id: int) -> None:
        """SOFT DELETE — mijozni arxivlash. Eski buyurtmalar admin uchun saqlanadi.
        Mijoz qaytadan /start yuborsa — avtomatik tiklanadi (register orqali).
        Idempotent.
        """
        async with UnitOfWork(self._sf) as uow:
            user = await uow.users.get(user_id)
            if user is None or user.is_deleted:
                return
            await uow.users.soft_delete(user)

    async def restore(self, user_id: int) -> User:
        """Arxivdan qaytaradi (admin manual)."""
        async with UnitOfWork(self._sf) as uow:
            user = await uow.users.get(user_id)
            if user is None:
                raise EntityNotFoundError("user_not_registered")
            if not user.is_deleted:
                return user
            return await uow.users.restore(user)
