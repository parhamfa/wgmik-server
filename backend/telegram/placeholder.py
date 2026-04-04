"""Internal TelegramUser row for FK on TelegramNotificationLog (admin dedup rows)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import TelegramUser

# Telegram user IDs are positive; -1 is reserved for internal log rows only.
INTERNAL_ADMIN_TELEGRAM_ID = -1


def get_internal_admin_log_user_id(db: Session) -> int:
    """PK of the placeholder user used for admin-channel notification logs."""
    u = db.query(TelegramUser).filter_by(telegram_user_id=INTERNAL_ADMIN_TELEGRAM_ID).first()
    if u:
        return u.id
    u = TelegramUser(
        telegram_user_id=INTERNAL_ADMIN_TELEGRAM_ID,
        telegram_username="__wgmik_internal_admin__",
        first_name="",
        last_name="",
        language="en",
        is_blocked=True,
    )
    db.add(u)
    db.flush()
    return u.id
