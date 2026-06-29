from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Sequence

from sqlalchemy import and_, case as sa_case_when, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import selectinload

from Data.repositories.base import BaseRepository
from Domain.enums import OrderStatus
from Domain.models.daily_counter import DailyOrderCounter
from Domain.models.order import Order, OrderItem


class OrderRepository(BaseRepository[Order]):
    model = Order

    def _full_query(self):
        """Order + selectinload'lar. Soft-delete filter qo'shilmaydi — caller hal qiladi."""
        return select(Order).options(
            selectinload(Order.items),
            selectinload(Order.customer),
            selectinload(Order.courier),
        )

    async def next_daily_number(self, day: date) -> int:
        """Berilgan kun uchun navbatdagi kunlik buyurtma raqamini ATOMIK qaytaradi.

        PostgreSQL `INSERT ... ON CONFLICT DO UPDATE ... RETURNING` — bitta
        statement'da race-safe:
          * Kun uchun qator yo'q bo'lsa → yaratadi, 1 qaytaradi
          * Bor bo'lsa → atomik +1 qiladi, yangisini qaytaradi

        UoW tranzaksiyasi ichida chaqiriladi — order yaratish bilan birga
        commit/rollback bo'ladi (to'liq xatoda raqam isrof bo'lmaydi).
        Bir vaqtda kelgan ikki order hech qachon bir xil raqam olmaydi
        (ON CONFLICT row lock).
        """
        stmt = (
            pg_insert(DailyOrderCounter)
            .values(day=day, last_number=1)
            .on_conflict_do_update(
                index_elements=[DailyOrderCounter.day],
                set_={"last_number": DailyOrderCounter.last_number + 1},
            )
            .returning(DailyOrderCounter.last_number)
        )
        res = await self._session.execute(stmt)
        return int(res.scalar_one())

    async def get_full(self, order_id: int) -> Optional[Order]:
        """Soft-deleted bo'lsa ham qaytaradi — admin tarix va restore uchun zarur."""
        res = await self._session.execute(self._full_query().where(Order.id == order_id))
        return res.scalar_one_or_none()

    async def get_for_update(self, order_id: int) -> Optional[Order]:
        """Pessimistic row-level lock — claim/transition oqimida race oldini oladi."""
        res = await self._session.execute(
            self._active_only(self._full_query()).where(Order.id == order_id).with_for_update()
        )
        return res.scalar_one_or_none()

    async def get_by_idempotency_key(
        self, customer_id: int, idempotency_key: str,
    ) -> Optional[Order]:
        res = await self._session.execute(
            self._active_only(self._full_query()).where(
                Order.customer_id == customer_id,
                Order.idempotency_key == idempotency_key,
            )
        )
        return res.scalar_one_or_none()

    async def list_active_by_courier(self, courier_id: int) -> Sequence[Order]:
        """Tugallanmagan (yetkazilmoqda yoki qabul qilingan) buyurtmalar."""
        res = await self._session.execute(
            self._active_only(self._full_query())
            .where(
                Order.courier_id == courier_id,
                Order.status.in_([OrderStatus.ACCEPTED, OrderStatus.DELIVERING]),
            )
            .order_by(Order.created_at.asc())
        )
        return res.scalars().all()

    async def list_by_status(self, status: OrderStatus, limit: int = 50) -> Sequence[Order]:
        res = await self._session.execute(
            self._active_only(self._full_query())
            .where(Order.status == status)
            .order_by(Order.created_at.desc())
            .limit(limit)
        )
        return res.scalars().all()

    async def list_recent(self, limit: int = 20) -> Sequence[Order]:
        res = await self._session.execute(
            self._active_only(self._full_query()).order_by(Order.created_at.desc()).limit(limit)
        )
        return res.scalars().all()

    # ---------------------- Admin analytics ----------------------

    async def count_by_status(self) -> dict[str, int]:
        """Hozir har bir holatda nechta buyurtma bor (arxivlanganlarsiz)."""
        res = await self._session.execute(
            self._active_only(
                select(Order.status, func.count(Order.id)).group_by(Order.status)
            )
        )
        return {row[0].name: int(row[1]) for row in res.all()}

    async def finance_in_window(
        self,
        since: datetime,
        until: datetime,
        *,
        exclude_cancelled: bool = True,
    ) -> dict:
        """Bir oraliqning professional moliyaviy breakdown'i:
            cash_revenue    — naqdda yig'ilgan (= total_amount)
            cashback_used   — keshbek bilan to'langan qism
            cashback_earned — yangi yaratilgan liability
            gross_sale      — items_total (cashback'gacha bo'lgan to'liq summa)
            orders_count    — buyurtmalar soni
        """
        stmt = self._active_only(select(
            func.coalesce(func.sum(Order.total_amount), 0).label("cash"),
            func.coalesce(func.sum(Order.cashback_used), 0).label("cb_used"),
            func.coalesce(func.sum(Order.cashback_earned), 0).label("cb_earned"),
            func.coalesce(func.sum(Order.items_total), 0).label("gross"),
            func.count(Order.id).label("c"),
        ).where(Order.created_at >= since, Order.created_at < until))
        if exclude_cancelled:
            stmt = stmt.where(Order.status != OrderStatus.CANCELLED)
        res = await self._session.execute(stmt)
        row = res.first()
        return {
            "cash_revenue":   float(row[0] or 0),
            "cashback_used":  float(row[1] or 0),
            "cashback_earned": float(row[2] or 0),
            "gross_sale":     float(row[3] or 0),
            "orders_count":   int(row[4] or 0),
        }

    async def finance_by_day_since(
        self, since: datetime, *, exclude_cancelled: bool = True,
    ) -> list[dict]:
        """Kunlik moliyaviy breakdown — finance UI uchun."""
        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "postgresql":
            day_expr = func.date_trunc("day", Order.created_at)
        else:
            day_expr = func.strftime("%Y-%m-%d", Order.created_at)
        stmt = self._active_only(select(
            day_expr.label("day"),
            func.coalesce(func.sum(Order.total_amount), 0).label("cash"),
            func.coalesce(func.sum(Order.cashback_used), 0).label("cb_used"),
            func.coalesce(func.sum(Order.cashback_earned), 0).label("cb_earned"),
            func.coalesce(func.sum(Order.items_total), 0).label("gross"),
            func.count(Order.id).label("c"),
        ).where(Order.created_at >= since))
        if exclude_cancelled:
            stmt = stmt.where(Order.status != OrderStatus.CANCELLED)
        stmt = stmt.group_by("day").order_by("day")
        res = await self._session.execute(stmt)
        return [
            {
                "date": str(r.day)[:10],
                "cash_revenue": float(r.cash or 0),
                "cashback_used": float(r.cb_used or 0),
                "cashback_earned": float(r.cb_earned or 0),
                "gross_sale": float(r.gross or 0),
                "count": int(r.c or 0),
            }
            for r in res.all()
        ]

    async def finance_by_month_since(
        self, since: datetime, *, exclude_cancelled: bool = True,
    ) -> list[dict]:
        """Oylik moliyaviy breakdown."""
        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "postgresql":
            month_expr = func.to_char(Order.created_at, "YYYY-MM")
        else:
            month_expr = func.strftime("%Y-%m", Order.created_at)
        stmt = self._active_only(select(
            month_expr.label("month"),
            func.coalesce(func.sum(Order.total_amount), 0).label("cash"),
            func.coalesce(func.sum(Order.cashback_used), 0).label("cb_used"),
            func.coalesce(func.sum(Order.cashback_earned), 0).label("cb_earned"),
            func.coalesce(func.sum(Order.items_total), 0).label("gross"),
            func.count(Order.id).label("c"),
        ).where(Order.created_at >= since))
        if exclude_cancelled:
            stmt = stmt.where(Order.status != OrderStatus.CANCELLED)
        stmt = stmt.group_by("month").order_by("month")
        res = await self._session.execute(stmt)
        return [
            {
                "month": str(r.month),
                "cash_revenue": float(r.cash or 0),
                "cashback_used": float(r.cb_used or 0),
                "cashback_earned": float(r.cb_earned or 0),
                "gross_sale": float(r.gross or 0),
                "count": int(r.c or 0),
            }
            for r in res.all()
        ]

    async def all_time_cashback_totals(self) -> dict:
        """Hech qachon sotilgan barcha cashback ko'lami (audit/admin xulosa).
        Arxivlangan va CANCELLED'lar hisobga kirmaydi."""
        stmt = self._active_only(select(
            func.coalesce(func.sum(Order.cashback_used), 0).label("used"),
            func.coalesce(func.sum(Order.cashback_earned), 0).label("earned"),
        ).where(Order.status != OrderStatus.CANCELLED))
        res = await self._session.execute(stmt)
        row = res.first()
        return {
            "cashback_used_total": float(row[0] or 0),
            "cashback_earned_total": float(row[1] or 0),
        }

    async def hourly_counts_for_day(self, day_start: datetime) -> list[tuple[int, int]]:
        """Bir kun ichida soatlik buyurtma soni."""
        from datetime import timedelta
        day_end = day_start + timedelta(days=1)
        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "postgresql":
            hour_expr = func.extract("hour", Order.created_at)
        else:
            hour_expr = func.strftime("%H", Order.created_at)
        stmt = self._active_only(select(
            hour_expr.label("hour"),
            func.count(Order.id).label("count"),
        ).where(
            and_(Order.created_at >= day_start, Order.created_at < day_end)
        )).group_by("hour").order_by("hour")
        res = await self._session.execute(stmt)
        return [(int(r.hour), int(r.count)) for r in res.all()]

    async def hourly_counts_in_window(
        self, since: datetime, until: datetime,
    ) -> list[tuple[int, int]]:
        """Bir oraliqdagi soatlik buyurtmalar yig'indisi — pik vaqtlarni topish uchun."""
        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "postgresql":
            hour_expr = func.extract("hour", Order.created_at)
        else:
            hour_expr = func.cast(func.strftime("%H", Order.created_at), type_=None)
        stmt = self._active_only(select(
            hour_expr.label("hour"),
            func.count(Order.id).label("count"),
        ).where(
            and_(Order.created_at >= since, Order.created_at < until),
            Order.status != OrderStatus.CANCELLED,
        )).group_by("hour").order_by("hour")
        res = await self._session.execute(stmt)
        return [(int(r.hour), int(r.count)) for r in res.all()]

    async def weekday_counts_in_window(
        self, since: datetime, until: datetime,
    ) -> list[tuple[int, int]]:
        """Hafta kunlari bo'yicha buyurtma soni (0=Yakshanba ... 6=Shanba)."""
        dialect = self._session.bind.dialect.name if self._session.bind else "postgresql"
        if dialect == "postgresql":
            dow_expr = func.extract("dow", Order.created_at)
        else:
            dow_expr = func.strftime("%w", Order.created_at)
        stmt = self._active_only(select(
            dow_expr.label("dow"),
            func.count(Order.id).label("count"),
        ).where(
            and_(Order.created_at >= since, Order.created_at < until),
            Order.status != OrderStatus.CANCELLED,
        )).group_by("dow").order_by("dow")
        res = await self._session.execute(stmt)
        return [(int(float(r.dow)), int(r.count)) for r in res.all()]

    async def top_products_since(
        self, since: datetime, limit: int = 5,
    ) -> list[tuple[int | None, str, int, float]]:
        """Ko'p sotilgan mahsulotlar — order_items darajasida JOIN bilan filter."""
        stmt = select(
            OrderItem.food_id,
            OrderItem.food_name,
            func.sum(OrderItem.quantity).label("qty"),
            func.sum(OrderItem.unit_price * OrderItem.quantity).label("revenue"),
        ).join(Order, Order.id == OrderItem.order_id).where(
            Order.created_at >= since,
            Order.status != OrderStatus.CANCELLED,
            Order.deleted_at.is_(None),  # arxivlangan buyurtmalar hisobga olinmaydi
        ).group_by(OrderItem.food_id, OrderItem.food_name).order_by(
            func.sum(OrderItem.quantity).desc()
        ).limit(limit)
        res = await self._session.execute(stmt)
        return [(r[0], r[1], int(r[2] or 0), float(r[3] or 0)) for r in res.all()]

    def _apply_order_filters(
        self,
        stmt,
        *,
        status_filter: Optional[OrderStatus] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        customer_id: Optional[int] = None,
        courier_id: Optional[int] = None,
        created_by_operator_id: Optional[int] = None,
        include_archived: bool = False,
    ):
        if not include_archived:
            stmt = self._active_only(stmt)
        if status_filter is not None:
            stmt = stmt.where(Order.status == status_filter)
        if since is not None:
            stmt = stmt.where(Order.created_at >= since)
        if until is not None:
            stmt = stmt.where(Order.created_at <= until)
        if customer_id is not None:
            stmt = stmt.where(Order.customer_id == customer_id)
        if courier_id is not None:
            stmt = stmt.where(Order.courier_id == courier_id)
        if created_by_operator_id is not None:
            stmt = stmt.where(Order.created_by_operator_id == created_by_operator_id)
        return stmt

    async def list_filtered(
        self,
        *,
        status_filter: Optional[OrderStatus] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        customer_id: Optional[int] = None,
        courier_id: Optional[int] = None,
        created_by_operator_id: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Order]:
        stmt = self._apply_order_filters(
            self._full_query().order_by(Order.created_at.desc()),
            status_filter=status_filter, since=since, until=until,
            customer_id=customer_id, courier_id=courier_id,
            created_by_operator_id=created_by_operator_id,
        )
        stmt = stmt.offset(offset).limit(limit)
        res = await self._session.execute(stmt)
        return res.scalars().all()

    async def count_filtered(
        self,
        *,
        status_filter: Optional[OrderStatus] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        customer_id: Optional[int] = None,
        courier_id: Optional[int] = None,
        created_by_operator_id: Optional[int] = None,
    ) -> int:
        stmt = self._apply_order_filters(
            select(func.count(Order.id)),
            status_filter=status_filter, since=since, until=until,
            customer_id=customer_id, courier_id=courier_id,
            created_by_operator_id=created_by_operator_id,
        )
        res = await self._session.execute(stmt)
        return int(res.scalar_one() or 0)

    async def stats_per_customer(
        self, customer_ids: Sequence[int],
    ) -> dict[int, tuple[int, float]]:
        """Bitta query'da N ta mijozning (orders_count, total_spent) ni qaytaradi.

        N+1 muammosini bartaraf qiladi: `customer_ids` ro'yxati uchun bitta
        GROUP BY query ishga tushadi. CANCELLED buyurtmalar total_spent'dan
        chiqarib tashlanadi, lekin orders_count ga kiradi (UI'da "buyurtmalar
        soni" deganda hammasi nazarda tutiladi).
        """
        if not customer_ids:
            return {}
        # orders_count — barchasi; total_spent — CANCELLED'siz. Arxivlanganlar chetda.
        stmt = self._active_only(select(
            Order.customer_id,
            func.count(Order.id).label("c"),
            func.coalesce(
                func.sum(
                    sa_case_when(
                        (Order.status != OrderStatus.CANCELLED, Order.total_amount),
                        else_=0,
                    )
                ),
                0,
            ).label("s"),
        ).where(Order.customer_id.in_(list(customer_ids)))).group_by(Order.customer_id)
        res = await self._session.execute(stmt)
        return {
            int(row[0]): (int(row[1] or 0), float(row[2] or 0))
            for row in res.all()
        }

    async def list_by_customer_paginated(
        self, customer_id: int, *, limit: int = 20, offset: int = 0,
    ) -> Sequence[Order]:
        """Mijozning buyurtmalari — arxivlanganlar chetga (mijoz "Buyurtmalarim" uchun)."""
        res = await self._session.execute(
            self._active_only(self._full_query())
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc())
            .offset(offset).limit(limit)
        )
        return res.scalars().all()

    async def count_by_customer(self, customer_id: int) -> int:
        res = await self._session.execute(
            self._active_only(select(func.count(Order.id)))
            .where(Order.customer_id == customer_id)
        )
        return int(res.scalar_one() or 0)

    async def count_delivered_by_courier(
        self,
        courier_id: int,
        since: Optional[datetime] = None,
    ) -> int:
        """Kuryer yetkazib bergan (DELIVERED) zakazlar soni — arxivlanganlar chiqariladi."""
        stmt = self._active_only(select(func.count(Order.id)).where(
            Order.courier_id == courier_id,
            Order.status == OrderStatus.DELIVERED,
        ))
        if since is not None:
            stmt = stmt.where(Order.delivered_at >= since)
        res = await self._session.execute(stmt)
        return int(res.scalar_one() or 0)

    async def stats_per_courier(
        self,
        courier_ids: Sequence[int],
        *,
        today_start: datetime,
        month_start: datetime,
    ) -> dict[int, tuple[int, int, int]]:
        """Bitta query'da N ta kuryer uchun (today, month, total) DELIVERED sonini qaytaradi.

        N+1 muammosini bartaraf qiladi: har kuryerga 3 ta count emas, bitta
        GROUP BY + CASE WHEN orqali barchasi bir so'rovda. Faqat aktiv (arxiv emas)
        DELIVERED buyurtmalar.
        """
        if not courier_ids:
            return {}
        stmt = self._active_only(select(
            Order.courier_id,
            func.coalesce(func.sum(
                sa_case_when((Order.delivered_at >= today_start, 1), else_=0)
            ), 0).label("today"),
            func.coalesce(func.sum(
                sa_case_when((Order.delivered_at >= month_start, 1), else_=0)
            ), 0).label("month"),
            func.count(Order.id).label("total"),
        ).where(
            Order.courier_id.in_(list(courier_ids)),
            Order.status == OrderStatus.DELIVERED,
        )).group_by(Order.courier_id)
        res = await self._session.execute(stmt)
        return {
            int(row[0]): (int(row[1] or 0), int(row[2] or 0), int(row[3] or 0))
            for row in res.all()
        }

    # ---------------------- Soft delete admin helpers ----------------------

    async def list_archived(self, limit: int = 50, offset: int = 0) -> Sequence[Order]:
        """Admin "Arxiv" — soft-deleted buyurtmalar (faqat admin uchun)."""
        res = await self._session.execute(
            self._deleted_only(self._full_query())
            .order_by(Order.deleted_at.desc())
            .offset(offset).limit(limit)
        )
        return res.scalars().all()
