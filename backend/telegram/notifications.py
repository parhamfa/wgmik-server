"""Outbound Telegram notification engine.

Called from the scheduler after fair-usage enforcement to send quota warnings,
throttle/unthrottle alerts, and periodic summaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    FairUsageRule,
    FairUsageState,
    Peer,
    SettingsKV,
    TelegramNotificationConfig,
    TelegramNotificationLog,
    TelegramPeerBinding,
    TelegramUser,
)
from .formatters import _fmt_bytes
from .i18n import t

logger = logging.getLogger("wgmik.telegram.notifications")

# Warnings: at most once per peer/user per window while usage stays in a band.
_DEDUP_WARNING_HOURS = 24
# quota_hit / quota_lifted: keyed by throttle episode in message_hash; this is a safety net.
_DEDUP_EPISODE_HOURS = 168


def _dedup_hours(event_type: str) -> int:
    if event_type in ("quota_warning_80", "quota_warning_90"):
        return _DEDUP_WARNING_HOURS
    if event_type in ("quota_hit", "quota_lifted"):
        return _DEDUP_EPISODE_HOURS
    return _DEDUP_WARNING_HOURS


def _is_event_enabled(db: Session, event_type: str) -> tuple[bool, bool]:
    """Return (notify_clients, notify_admin) for event_type."""
    cfg = db.query(TelegramNotificationConfig).filter_by(event_type=event_type).first()
    if not cfg or not cfg.enabled:
        return False, False
    return cfg.notify_clients, cfg.notify_admin


def _already_sent(
    db: Session,
    tg_user_id: int,
    peer_id: int,
    event_type: str,
    msg_hash: str,
    *,
    within_hours: float,
) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    return (
        db.query(TelegramNotificationLog)
        .filter(
            TelegramNotificationLog.telegram_user_id == tg_user_id,
            TelegramNotificationLog.peer_id == peer_id,
            TelegramNotificationLog.event_type == event_type,
            TelegramNotificationLog.message_hash == msg_hash,
            TelegramNotificationLog.sent_at >= cutoff.replace(tzinfo=None),
        )
        .first()
        is not None
    )


def _record_sent(db: Session, tg_user_id: int, peer_id: int, event_type: str, msg_hash: str) -> None:
    db.add(TelegramNotificationLog(
        telegram_user_id=tg_user_id,
        peer_id=peer_id,
        event_type=event_type,
        message_hash=msg_hash,
    ))
    db.flush()


def _make_hash(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _send_message_sync(chat_id: int, text: str) -> bool:
    """Send a message via a one-shot Bot instance. Works whether the polling bot is running or not."""
    from .bot import _get_tg_settings, _decrypt_token

    cfg = _get_tg_settings()
    token = _decrypt_token(cfg.get("tg_bot_token", ""))
    if not token or len(token) < 20:
        logger.warning("Cannot send TG message: no valid bot token")
        return False

    async def _do_send():
        from aiogram import Bot
        bot = Bot(token=token)
        try:
            await bot.send_message(chat_id, text)
            return True
        finally:
            await bot.session.close()

    try:
        # If there's already a running bot loop, use it; otherwise spin up a throwaway one
        from .bot import _bot_loop, _bot_running
        if _bot_running and _bot_loop and not _bot_loop.is_closed():
            future = asyncio.run_coroutine_threadsafe(_do_send(), _bot_loop)
            return future.result(timeout=15)
        else:
            return asyncio.run(_do_send())
    except Exception:
        logger.exception("Failed to send TG message to %s", chat_id)
        return False


def _get_admin_chat_id(db: Session) -> Optional[int]:
    kv = db.get(SettingsKV, "tg_admin_chat_id")
    if kv and kv.value:
        try:
            return int(kv.value)
        except ValueError:
            pass
    return None


def _get_bindings_for_peer(db: Session, peer_id: int) -> list[TelegramPeerBinding]:
    return (
        db.query(TelegramPeerBinding)
        .join(TelegramUser, TelegramPeerBinding.telegram_user_id == TelegramUser.id)
        .filter(
            TelegramPeerBinding.peer_id == peer_id,
            TelegramPeerBinding.visible == True,
            TelegramUser.is_blocked == False,
        )
        .all()
    )


def _throttle_episode_key(state: Optional[FairUsageState]) -> str:
    """Stable string for one throttle episode (one notification per episode)."""
    if not state or not state.throttled_at:
        return "0"
    ts = state.throttled_at
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return str(int(ts.timestamp()))


def check_and_send_notifications(
    db: Session,
    peer: Peer,
    rule: Optional[FairUsageRule],
    used_rx: int,
    used_tx: int,
    now_utc: datetime,
) -> None:
    """Called per peer after fair-usage enforcement. Checks thresholds and sends alerts."""
    if not rule:
        return

    total = rule.download_quota_bytes or 0
    if total <= 0:
        return

    used = (used_rx + used_tx) if rule.quota_mode == "combined" else used_rx
    pct = round(used / total * 100)

    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer.id).first()
    is_throttled = state.throttled if state else False
    peer_name = peer.name or peer.public_key[:12]

    bindings = _get_bindings_for_peer(db, peer.id)
    admin_chat_id = _get_admin_chat_id(db)
    if not bindings and not admin_chat_id:
        return

    # Quota warning 80%
    if 80 <= pct < 90:
        _notify_event(
            db, bindings, admin_chat_id, peer, "quota_warning_80",
            peer_name=peer_name, pct=pct, used=used, total=total,
            dedup_extra="",
        )
    # Quota warning 90%
    elif 90 <= pct < 100 and not is_throttled:
        _notify_event(
            db, bindings, admin_chat_id, peer, "quota_warning_90",
            peer_name=peer_name, pct=pct, used=used, total=total,
            dedup_extra="",
        )
    # Quota reached / throttled — once per throttle episode (hash includes throttled_at)
    if is_throttled:
        _notify_event(
            db, bindings, admin_chat_id, peer, "quota_hit",
            peer_name=peer_name, pct=pct, used=used, total=total,
            dedup_extra=_throttle_episode_key(state),
        )


def notify_quota_lifted(db: Session, peer: Peer) -> None:
    """Called when a peer's throttle is removed."""
    notify_clients, notify_admin = _is_event_enabled(db, "quota_lifted")
    if not notify_clients and not notify_admin:
        return

    peer_name = peer.name or peer.public_key[:12]
    bindings = _get_bindings_for_peer(db, peer.id)
    admin_chat_id = _get_admin_chat_id(db)
    hours = _dedup_hours("quota_lifted")
    from .placeholder import get_internal_admin_log_user_id

    for b in bindings:
        if not notify_clients:
            break
        tg_user = db.get(TelegramUser, b.telegram_user_id)
        if not tg_user:
            continue
        msg_hash = _make_hash("quota_lifted", str(peer.id), str(tg_user.id), "lift")
        if _already_sent(db, tg_user.id, peer.id, "quota_lifted", msg_hash, within_hours=hours):
            continue
        lang = tg_user.language or "en"
        text = t("notif_quota_lifted", lang, name=peer_name)
        if _send_message_sync(tg_user.telegram_user_id, text):
            _record_sent(db, tg_user.id, peer.id, "quota_lifted", msg_hash)

    if notify_admin and admin_chat_id:
        aid = get_internal_admin_log_user_id(db)
        msg_hash = _make_hash("quota_lifted", str(peer.id), "admin", "lift")
        if not _already_sent(db, aid, peer.id, "quota_lifted", msg_hash, within_hours=hours):
            text = t("notif_quota_lifted", "en", name=peer_name)
            if _send_message_sync(admin_chat_id, f"[Admin] {text}"):
                _record_sent(db, aid, peer.id, "quota_lifted", msg_hash)


def _notify_event(
    db: Session,
    bindings: list[TelegramPeerBinding],
    admin_chat_id: Optional[int],
    peer: Peer,
    event_type: str,
    *,
    peer_name: str,
    pct: int,
    used: int,
    total: int,
    dedup_extra: str,
) -> None:
    notify_clients, notify_admin = _is_event_enabled(db, event_type)
    if not notify_clients and not notify_admin:
        return

    hours = _dedup_hours(event_type)
    from .placeholder import get_internal_admin_log_user_id

    i18n_key = {
        "quota_warning_80": "notif_quota_warning",
        "quota_warning_90": "notif_quota_warning",
        "quota_hit": "notif_quota_hit",
    }.get(event_type, "notif_quota_warning")

    for b in bindings:
        if not notify_clients:
            break
        tg_user = db.get(TelegramUser, b.telegram_user_id)
        if not tg_user:
            continue
        msg_hash = _make_hash(event_type, str(peer.id), str(tg_user.id), dedup_extra)
        if _already_sent(db, tg_user.id, peer.id, event_type, msg_hash, within_hours=hours):
            continue
        lang = tg_user.language or "en"
        text = t(i18n_key, lang, name=peer_name, pct=str(pct),
                 used=_fmt_bytes(used), total=_fmt_bytes(total))
        if _send_message_sync(tg_user.telegram_user_id, text):
            _record_sent(db, tg_user.id, peer.id, event_type, msg_hash)

    if notify_admin and admin_chat_id:
        aid = get_internal_admin_log_user_id(db)
        msg_hash = _make_hash(event_type, str(peer.id), "admin", dedup_extra)
        if _already_sent(db, aid, peer.id, event_type, msg_hash, within_hours=hours):
            return
        text = t(i18n_key, "en", name=peer_name, pct=str(pct),
                 used=_fmt_bytes(used), total=_fmt_bytes(total))
        if _send_message_sync(admin_chat_id, f"[Admin] {text}"):
            _record_sent(db, aid, peer.id, event_type, msg_hash)


def send_daily_summary(db: Session) -> None:
    """Send daily usage summary to all clients with bindings."""
    notify_clients, notify_admin = _is_event_enabled(db, "daily_summary")
    if not notify_clients and not notify_admin:
        return

    from ..fair_usage_usage import peer_scope_usage_bytes
    now = datetime.now(timezone.utc)

    tg_users = db.query(TelegramUser).filter_by(is_blocked=False).all()
    admin_chat_id = _get_admin_chat_id(db)
    admin_lines = []

    for tg_user in tg_users:
        bindings = (
            db.query(TelegramPeerBinding)
            .filter_by(telegram_user_id=tg_user.id, visible=True)
            .all()
        )
        if not bindings:
            continue

        lang = tg_user.language or "en"
        lines = [t("usage_header", lang, scope=t("btn_today", lang))]
        for b in bindings:
            peer = db.get(Peer, b.peer_id)
            if not peer:
                continue
            rx, tx = peer_scope_usage_bytes(peer.id, "daily", db, now)
            name = peer.name or peer.public_key[:12]
            lines.append(f"  {name}: \u2b07{_fmt_bytes(rx)} \u2b06{_fmt_bytes(tx)}")
            admin_lines.append(f"  {name} (@{tg_user.telegram_username}): \u2b07{_fmt_bytes(rx)} \u2b06{_fmt_bytes(tx)}")

        if notify_clients and len(lines) > 1:
            _send_message_sync(tg_user.telegram_user_id, "\n".join(lines))

    if notify_admin and admin_chat_id and admin_lines:
        header = "[Admin] Daily Summary"
        _send_message_sync(admin_chat_id, f"{header}\n" + "\n".join(admin_lines))


def send_weekly_summary(db: Session) -> None:
    """Send weekly usage summary to all clients with bindings."""
    notify_clients, notify_admin = _is_event_enabled(db, "weekly_summary")
    if not notify_clients and not notify_admin:
        return

    from ..fair_usage_usage import peer_scope_usage_bytes
    now = datetime.now(timezone.utc)

    tg_users = db.query(TelegramUser).filter_by(is_blocked=False).all()
    admin_chat_id = _get_admin_chat_id(db)
    admin_lines = []

    for tg_user in tg_users:
        bindings = (
            db.query(TelegramPeerBinding)
            .filter_by(telegram_user_id=tg_user.id, visible=True)
            .all()
        )
        if not bindings:
            continue

        lang = tg_user.language or "en"
        lines = [t("usage_header", lang, scope=t("btn_this_week", lang))]
        for b in bindings:
            peer = db.get(Peer, b.peer_id)
            if not peer:
                continue
            rx, tx = peer_scope_usage_bytes(peer.id, "weekly", db, now)
            name = peer.name or peer.public_key[:12]
            lines.append(f"  {name}: \u2b07{_fmt_bytes(rx)} \u2b06{_fmt_bytes(tx)}")
            admin_lines.append(f"  {name} (@{tg_user.telegram_username}): \u2b07{_fmt_bytes(rx)} \u2b06{_fmt_bytes(tx)}")

        if notify_clients and len(lines) > 1:
            _send_message_sync(tg_user.telegram_user_id, "\n".join(lines))

    if notify_admin and admin_chat_id and admin_lines:
        header = "[Admin] Weekly Summary"
        _send_message_sync(admin_chat_id, f"{header}\n" + "\n".join(admin_lines))
