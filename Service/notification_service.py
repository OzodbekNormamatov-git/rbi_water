"""DEPRECATED: turli komponentlar `Service.notifications` package'idan keladi.

Eski importlar ishlashda davom etishi uchun shim:
    from Service.notification_service import NotificationService
        ↓
    from Service.notifications import NotificationService

Yangi kodda to'g'ridan-to'g'ri `Service.notifications` package'ini ishlatish tavsiya etiladi.
"""
from Service.notifications import NotificationService
from Service.notifications.formatters import (
    format_customer_timeline,
    format_dm_for_courier,
    format_group_claimed,
    format_group_new,
    make_courier_dm_kb as courier_dm_kb,
    make_group_new_kb as group_new_kb,
)

__all__ = [
    "NotificationService",
    "format_customer_timeline",
    "format_dm_for_courier",
    "format_group_claimed",
    "format_group_new",
    "courier_dm_kb",
    "group_new_kb",
]
