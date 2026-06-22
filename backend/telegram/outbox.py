"""Admin-authored Telegram broadcast outbox."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import func

from ..db import SessionLocal, sqlite_database_path
from ..models import TelegramBroadcast, TelegramBroadcastRecipient, TelegramUser
from .i18n import t
from .sender import send_message_sync, send_photo_sync

logger = logging.getLogger("wgmik.telegram.outbox")

BROADCAST_STATUS_ACTIVE = {"queued", "sending"}
RECIPIENT_STATUS_SENT = {"sent", "acknowledged"}


def broadcast_media_root() -> Path:
    db_path = sqlite_database_path()
    if db_path:
        return Path(db_path).parent / "telegram_outbox"
    return Path("./data/telegram_outbox")


def telegram_user_label(user: TelegramUser) -> str:
    if user.telegram_username:
        return f"@{user.telegram_username}"
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return name or f"User #{user.id}"


def recalculate_broadcast_counts(db, broadcast: TelegramBroadcast) -> None:
    db.flush()
    rows = (
        db.query(
            TelegramBroadcastRecipient.status,
            func.count(TelegramBroadcastRecipient.id),
        )
        .filter(TelegramBroadcastRecipient.broadcast_id == broadcast.id)
        .group_by(TelegramBroadcastRecipient.status)
        .all()
    )
    counts = {status: int(count) for status, count in rows}
    broadcast.total_count = sum(counts.values())
    broadcast.sent_count = sum(counts.get(status, 0) for status in RECIPIENT_STATUS_SENT)
    broadcast.failed_count = counts.get("failed", 0)
    broadcast.acknowledged_count = counts.get("acknowledged", 0)


def _final_status(broadcast: TelegramBroadcast) -> str:
    pending = max(
        0,
        broadcast.total_count - broadcast.sent_count - broadcast.failed_count,
    )
    if pending > 0:
        return "sending"
    if broadcast.failed_count and broadcast.sent_count:
        return "partial_failed"
    if broadcast.failed_count:
        return "failed"
    return "sent"


def _ack_keyboard(recipient_id: int, lang: str):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("broadcast_ack_button", lang),
                    callback_data=f"bcast:ack:{recipient_id}",
                )
            ]
        ]
    )


def _ack_done_keyboard(recipient_id: int, lang: str):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=t("broadcast_ack_done_button", lang),
                    callback_data=f"bcast:noop:{recipient_id}",
                )
            ]
        ]
    )


def acknowledged_keyboard(recipient_id: int, lang: str):
    return _ack_done_keyboard(recipient_id, lang)


def dispatch_broadcast(broadcast_id: int) -> None:
    db = SessionLocal()
    try:
        broadcast = db.get(TelegramBroadcast, broadcast_id)
        if not broadcast:
            return
        if broadcast.status not in BROADCAST_STATUS_ACTIVE and broadcast.status != "partial_failed":
            return

        broadcast.status = "sending"
        if not broadcast.started_at:
            broadcast.started_at = datetime.utcnow()
        db.commit()

        recipient_ids = [
            rid
            for (rid,) in (
                db.query(TelegramBroadcastRecipient.id)
                .filter(
                    TelegramBroadcastRecipient.broadcast_id == broadcast_id,
                    TelegramBroadcastRecipient.status == "pending",
                )
                .order_by(TelegramBroadcastRecipient.id.asc())
                .all()
            )
        ]

        photo_bytes: bytes | None = None
        if broadcast.photo_path:
            try:
                photo_bytes = Path(broadcast.photo_path).read_bytes()
            except OSError as exc:
                logger.warning("Broadcast photo unreadable for %s: %s", broadcast.id, exc)
                photo_bytes = None

        for recipient_id in recipient_ids:
            recipient = db.get(TelegramBroadcastRecipient, recipient_id)
            if not recipient or recipient.status != "pending":
                continue
            user = db.get(TelegramUser, recipient.telegram_user_id) if recipient.telegram_user_id else None
            lang = (user.language if user else "") or "en"
            try:
                keyboard = _ack_keyboard(recipient.id, lang)
                if photo_bytes:
                    sent = send_photo_sync(
                        recipient.chat_id,
                        photo_bytes,
                        broadcast.body,
                        filename=broadcast.photo_filename or "broadcast.jpg",
                        reply_markup=keyboard,
                    )
                else:
                    sent = send_message_sync(
                        recipient.chat_id,
                        broadcast.body,
                        reply_markup=keyboard,
                    )
                recipient.status = "sent"
                recipient.telegram_message_id = getattr(sent, "message_id", None)
                recipient.sent_at = datetime.utcnow()
                recipient.error_code = ""
                recipient.error_message = ""
            except Exception as exc:
                logger.warning(
                    "Telegram broadcast %s failed for recipient %s: %s",
                    broadcast.id,
                    recipient.id,
                    exc,
                )
                recipient.status = "failed"
                recipient.error_code = exc.__class__.__name__[:64]
                recipient.error_message = str(exc)[:1000]
            recipient.updated_at = datetime.utcnow()
            recalculate_broadcast_counts(db, broadcast)
            db.commit()
            time.sleep(0.075)

        recalculate_broadcast_counts(db, broadcast)
        broadcast.status = _final_status(broadcast)
        broadcast.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def dispatch_broadcast_async(broadcast_id: int) -> None:
    thread = threading.Thread(
        target=dispatch_broadcast,
        args=(broadcast_id,),
        daemon=True,
        name=f"tg-broadcast-{broadcast_id}",
    )
    thread.start()


def resume_pending_broadcasts(limit: int = 10) -> None:
    db = SessionLocal()
    try:
        ids = [
            bid
            for (bid,) in (
                db.query(TelegramBroadcast.id)
                .filter(TelegramBroadcast.status.in_(list(BROADCAST_STATUS_ACTIVE)))
                .order_by(TelegramBroadcast.created_at.asc())
                .limit(limit)
                .all()
            )
        ]
    finally:
        db.close()
    for broadcast_id in ids:
        dispatch_broadcast_async(broadcast_id)


def acknowledge_recipient(recipient_id: int, telegram_chat_id: int) -> bool:
    db = SessionLocal()
    try:
        recipient = db.get(TelegramBroadcastRecipient, recipient_id)
        if not recipient:
            return False
        user = db.get(TelegramUser, recipient.telegram_user_id) if recipient.telegram_user_id else None
        if not user or int(user.telegram_user_id) != int(telegram_chat_id):
            return False
        if recipient.status not in RECIPIENT_STATUS_SENT:
            return False
        if recipient.status != "acknowledged":
            recipient.status = "acknowledged"
            recipient.acknowledged_at = datetime.utcnow()
            recipient.updated_at = datetime.utcnow()
            broadcast = db.get(TelegramBroadcast, recipient.broadcast_id)
            if broadcast:
                recalculate_broadcast_counts(db, broadcast)
            db.commit()
        return True
    finally:
        db.close()
