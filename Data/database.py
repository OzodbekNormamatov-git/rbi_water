from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from Domain.models.base import Base

log = logging.getLogger(__name__)

# Idempotent migratsiyalar — mavjud bazaga yangi ustun va jadval qo'shadi.
# Alembic'gacha vaqtinchalik yechim; har bir o'zgarishni shu yerga yozamiz.
# Har biri ALOHIDA tranzaksiyada ishlaydi (Pg'da xato butun blokni bloklamasin).
_MIGRATIONS: tuple[str, ...] = (
    # ------ Eski (asl) idempotent o'zgarishlar ------
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_dm_message_id BIGINT",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivering_at TIMESTAMPTZ",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS ix_orders_idempotency_key ON orders (idempotency_key)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_orders_customer_id_idempotency_key "
        "ON orders (customer_id, idempotency_key) WHERE idempotency_key IS NOT NULL",
    "ALTER TABLE order_items DROP CONSTRAINT IF EXISTS fk_order_items_food_id_foods",
    "ALTER TABLE order_items ALTER COLUMN food_id DROP NOT NULL",
    """ALTER TABLE order_items
       ADD CONSTRAINT fk_order_items_food_id_foods
       FOREIGN KEY (food_id) REFERENCES foods(id) ON DELETE SET NULL""",

    # ------ v2: Mijoz keshbek + idishlar balansi ------
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS cashback_balance NUMERIC(12,2) NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS bottles_balance INTEGER NOT NULL DEFAULT 0",
    # CHECK constraint'lar — balanslar manfiy bo'lmasligini DB tomonida ham kafolatlaydi.
    """DO $$ BEGIN
       IF NOT EXISTS (
         SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_cashback_nonneg'
       ) THEN
         ALTER TABLE users ADD CONSTRAINT ck_users_cashback_nonneg
           CHECK (cashback_balance >= 0);
       END IF;
       END $$""",
    """DO $$ BEGIN
       IF NOT EXISTS (
         SELECT 1 FROM pg_constraint WHERE conname = 'ck_users_bottles_nonneg'
       ) THEN
         ALTER TABLE users ADD CONSTRAINT ck_users_bottles_nonneg
           CHECK (bottles_balance >= 0);
       END IF;
       END $$""",

    # ------ v2: Buyurtmaga keshbek/bottle/manzil snapshot ustunlari ------
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS items_total NUMERIC(12,2) NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS cashback_used NUMERIC(12,2) NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS cashback_earned NUMERIC(12,2) NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bottles_issued INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS bottles_returned INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS address_label VARCHAR(40) NOT NULL DEFAULT ''",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS address_details VARCHAR(200) NOT NULL DEFAULT ''",
    # Eski yozuvlarda items_total = total_amount (cashback bo'lmagan), backfill.
    "UPDATE orders SET items_total = total_amount WHERE items_total = 0 AND total_amount > 0",

    # ------ v3: performance indekslari (katta bazada admin paneli tezligi uchun) ------
    # pg_trgm — full_name/phone_number ustida `ilike '%q%'` sequential-scansiz.
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_users_full_name_trgm "
        "ON users USING gin (full_name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_users_phone_trgm "
        "ON users USING gin (phone_number gin_trgm_ops)",
    # Sortlash uchun (ro'yxatlar standart tartibi).
    "CREATE INDEX IF NOT EXISTS ix_users_created_at_desc ON users (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_orders_created_at_desc ON orders (created_at DESC)",
    # Status filter + sort kombinatsiyasi uchun composite indeks.
    "CREATE INDEX IF NOT EXISTS ix_orders_status_created_at ON orders (status, created_at DESC)",
    # Kuryer DELIVERED statistikasi uchun partial indeks.
    "CREATE INDEX IF NOT EXISTS ix_orders_delivered_at "
        "ON orders (delivered_at DESC) WHERE delivered_at IS NOT NULL",
    # Default manzilni tezda topish uchun partial indeks.
    "CREATE INDEX IF NOT EXISTS ix_customer_addresses_default "
        "ON customer_addresses (customer_id) WHERE is_default = true",

    # ------ v4: app_settings singleton + default qator ------
    """CREATE TABLE IF NOT EXISTS app_settings (
        id INTEGER PRIMARY KEY,
        cashback_enabled BOOLEAN NOT NULL DEFAULT true,
        cashback_percent NUMERIC(5,2) NOT NULL DEFAULT 1.5,
        max_cashback_usage_ratio NUMERIC(5,2) NOT NULL DEFAULT 1.00,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )""",
    # Singleton qatorni yaratamiz (id=1) — har sozlama o'qiganda chaqiriladi.
    "INSERT INTO app_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING",

    # ------ v5: broadcast'larda ixtiyoriy rasm ------
    "ALTER TABLE broadcasts ADD COLUMN IF NOT EXISTS photo_path VARCHAR(255)",

    # ------ v6: Soft delete — 4 ta jadval ------
    "ALTER TABLE foods    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ALTER TABLE users    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ALTER TABLE couriers ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    "ALTER TABLE orders   ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ",
    # Partial indekslar — faqat aktiv qatorlar uchun.
    # Bu eng tez-tez ishlatiladigan filter (WHERE deleted_at IS NULL),
    # kichik indeks katta jadvalda ham millisekundlar javob beradi.
    "CREATE INDEX IF NOT EXISTS ix_foods_active "
        "ON foods (id) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_users_active "
        "ON users (id) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_couriers_active "
        "ON couriers (id) WHERE deleted_at IS NULL",
    "CREATE INDEX IF NOT EXISTS ix_orders_active "
        "ON orders (id) WHERE deleted_at IS NULL",

    # ------ v7: ARRIVED status + arrival notification message + arrived_at timestamp ------
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS customer_arrived_message_id BIGINT",
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS arrived_at TIMESTAMPTZ",

    # ------ v8: Call operator order creation ------
    "ALTER TABLE orders ADD COLUMN IF NOT EXISTS created_by_operator_id BIGINT",
    "CREATE INDEX IF NOT EXISTS ix_orders_created_by_operator_id "
        "ON orders (created_by_operator_id) WHERE created_by_operator_id IS NOT NULL",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS has_started_bot BOOLEAN NOT NULL DEFAULT false",
    # Eski (existing) mijozlar ro'yxatdan o'tgan bo'lsa, ular bot bilan
    # ishlagan — bizning yangi flagimiz default false, lekin ular allaqachon
    # /start bosgan. Backfill: barcha mavjud mijozlarni True deb belgilaymiz.
    # Yangi (operator yaratgan) mijozlar default false bo'lib qoladi.
    "UPDATE users SET has_started_bot = true WHERE has_started_bot = false AND telegram_id > 0",

    # ------ v9: Kuryer telefon raqami ------
    # NULL: hali kiritilmagan (eski kuryerlar). Mijoz va admin ko'radi.
    "ALTER TABLE couriers ADD COLUMN IF NOT EXISTS phone_number VARCHAR(20)",
)


class Database:
    """Async SQLAlchemy engine + session factory'ni inkapsulyatsiya qiluvchi sinf."""

    def __init__(self, url: str, echo: bool = False) -> None:
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=echo,
            pool_pre_ping=True,
            future=True,
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def create_all(self) -> None:
        """MVP uchun jadvallarni yaratadi. Prodda Alembic migratsiyalardan foydalanish kerak.

        `create_all` mavjud jadvallarga yangi ustun qo'shmaydi — shu sababli
        idempotent ALTER TABLE / DROP CONSTRAINT qatorlarini yuritamiz.

        Har bir migratsiya **alohida tranzaksiyada** ishlaydi — bittasi
        muvaffaqiyatsiz bo'lsa, qolganlari to'sib qolmaydi (PostgreSQL
        bitta tranzaksiyada xato bo'lsa, keyingi statementlar bloklanadi).
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        for stmt in _MIGRATIONS:
            try:
                async with self._engine.begin() as conn:
                    await conn.execute(text(stmt))
            except Exception as e:
                log.warning("Migration skipped (%s): %s", stmt.splitlines()[0][:80], e)

    async def dispose(self) -> None:
        await self._engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as s:
            yield s
