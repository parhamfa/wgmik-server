"""Outbound Telegram notification engine.

Called from the scheduler after fair-usage enforcement to send quota warnings,
throttle/unthrottle alerts, and periodic summaries.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..fair_usage_sync import is_rule_over_quota
from ..fair_usage_tiers import active_tier_for_combined_usage, ordered_tiers_for_rule
from ..models import (
    FairUsageRule,
    FairUsageState,
    FairUsageTier,
    Peer,
    SettingsKV,
    TelegramNotificationConfig,
    TelegramNotificationLog,
    TelegramPeerBinding,
    TelegramUser,
    TelegramUserNotificationPreference,
)
from .formatters import _fmt_bytes
from .i18n import t

logger = logging.getLogger("wgmik.telegram.notifications")


def _fmt_speed_pair(dl_kbps: int, ul_kbps: int) -> str:
    def one(k: int) -> str:
        if k >= 1000:
            v = k / 1000.0
            s = f"{v:.1f}".rstrip("0").rstrip(".")
            return f"{s} Mbps"
        return f"{k} kbps"

    return f"⬇️ {one(dl_kbps)} · ⬆️ {one(ul_kbps)}"


def _throttle_detail_for_notification(
    db: Session,
    rule: FairUsageRule,
    used_rx: int,
    used_tx: int,
    state: Optional[FairUsageState],
) -> tuple[Optional[str], int, int]:
    """Human tier label (tiered rules only) and effective throttle speeds for this rule."""
    if rule.tiered:
        tiers = ordered_tiers_for_rule(db, rule.id)
        combined = used_rx + used_tx
        tier: Optional[FairUsageTier] = None
        if state and state.tier_id:
            tier = db.get(FairUsageTier, state.tier_id)
        if tier is None:
            tier = active_tier_for_combined_usage(tiers, combined)
        if tier:
            idx = next((i for i, x in enumerate(tiers) if x.id == tier.id), 0)
            label = (tier.name or "").strip() or f"Tier {idx + 1}"
            return label, tier.throttle_download_kbps, tier.throttle_upload_kbps
        return None, rule.throttle_download_kbps, rule.throttle_upload_kbps
    return None, rule.throttle_download_kbps, rule.throttle_upload_kbps


USER_NOTIFICATION_EVENT_TYPES: tuple[str, ...] = (
    "quota_warning_80",
    "quota_warning_90",
    "quota_hit",
    "quota_lifted",
    "daily_summary",
    "weekly_summary",
)

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


def _get_event_config(db: Session, event_type: str) -> TelegramNotificationConfig | None:
    return db.query(TelegramNotificationConfig).filter_by(event_type=event_type).first()


def _default_client_notification_enabled(db: Session, event_type: str) -> bool:
    cfg = _get_event_config(db, event_type)
    return bool(cfg.notify_clients) if cfg else True


def user_notification_enabled(db: Session, tg_user_pk: int, event_type: str) -> bool:
    pref = (
        db.query(TelegramUserNotificationPreference)
        .filter_by(telegram_user_id=tg_user_pk, event_type=event_type)
        .first()
    )
    return _default_client_notification_enabled(db, event_type) if pref is None else bool(pref.enabled)


def effective_user_notification_enabled(db: Session, tg_user_pk: int, event_type: str) -> bool:
    cfg = _get_event_config(db, event_type)
    if not cfg or not cfg.enabled:
        return False
    return user_notification_enabled(db, tg_user_pk, event_type)


def get_user_notification_preferences(db: Session, tg_user_pk: int) -> dict[str, bool]:
    prefs = (
        db.query(TelegramUserNotificationPreference)
        .filter_by(telegram_user_id=tg_user_pk)
        .all()
    )
    out = {
        event_type: _default_client_notification_enabled(db, event_type)
        for event_type in USER_NOTIFICATION_EVENT_TYPES
    }
    for pref in prefs:
        out[pref.event_type] = bool(pref.enabled)
    return out


def set_user_notification_preference(db: Session, tg_user_pk: int, event_type: str, enabled: bool) -> bool:
    if event_type not in USER_NOTIFICATION_EVENT_TYPES:
        return False
    pref = (
        db.query(TelegramUserNotificationPreference)
        .filter_by(telegram_user_id=tg_user_pk, event_type=event_type)
        .first()
    )
    if pref is None:
        pref = TelegramUserNotificationPreference(
            telegram_user_id=tg_user_pk,
            event_type=event_type,
            enabled=enabled,
        )
        db.add(pref)
    else:
        pref.enabled = enabled
    db.commit()
    return True


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


def _run_bot_coro(make_coro) -> bool:
    """Run a one-shot bot coroutine on the polling bot's loop, or a throwaway one."""
    from .sender import run_bot_coro
    try:
        run_bot_coro(make_coro)
        return True
    except Exception:
        logger.exception("Failed to send TG message")
        return False


def _send_message_sync(chat_id: int, text: str) -> bool:
    """Send a message via a one-shot Bot instance. Works whether the polling bot is running or not."""
    return _run_bot_coro(lambda bot: bot.send_message(chat_id, text))


def _send_photo_sync(chat_id: int, png: bytes, caption: str) -> bool:
    """Send a photo (PNG bytes) with caption; same loop strategy as _send_message_sync."""
    from aiogram.types import BufferedInputFile

    return _run_bot_coro(
        lambda bot: bot.send_photo(
            chat_id,
            BufferedInputFile(png, filename="card.png"),
            caption=caption[:1024],
        )
    )


def _fair_usage_card_png(db: Session, peer: Peer) -> Optional[bytes]:
    """Fair-usage status card PNG for notification messages (sync; None on failure)."""
    try:
        from ..fair_usage_peer_status_dto import build_fair_usage_peer_status_dto
        from .fair_usage_image import build_fair_usage_card_svg
        from .svg_render import render_svg_to_png

        dto = build_fair_usage_peer_status_dto(db, peer)
        if not dto.rules:
            return None
        svg = build_fair_usage_card_svg(dto, peer.name or peer.public_key[:12])
        return render_svg_to_png(svg)
    except Exception:
        logger.exception("Fair-usage card render failed for peer %s", peer.id)
        return None


def _summary_points(db: Session, peer_id: int, period: str, now: datetime) -> tuple[list[dict], str]:
    from .usage_chart_image import usage_points_for_tg_menu, usage_points_for_week

    if period == "weekly":
        return usage_points_for_week(db, peer_id, now)
    return usage_points_for_tg_menu(db, peer_id, "today", now)


def _render_chart_png(name: str, scope_label: str, mode: str, points: list[dict]) -> Optional[bytes]:
    """Usage chart PNG (sync; None when no data or on failure)."""
    if not points:
        return None
    try:
        from ..calendar_utils import app_date_calendar
        from ..settings import settings as app_settings
        from .svg_render import render_svg_to_png
        from .usage_chart_image import build_usage_chart_svg

        payload = {
            "peerName": name,
            "scopeLabel": scope_label,
            "mode": mode,
            "timezone": app_settings.timezone,
            "dateCalendar": app_date_calendar(),
            "points": points,
        }
        return render_svg_to_png(build_usage_chart_svg(payload))
    except Exception:
        logger.exception("Summary chart render failed for %s", name)
        return None


def _summary_chart_png(db: Session, peer: Peer, period: str, scope_label: str, now: datetime) -> Optional[bytes]:
    points, mode = _summary_points(db, peer.id, period, now)
    return _render_chart_png(peer.name or peer.public_key[:12], scope_label, mode, points)


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


def _quota_hit_dedup_key(rule: FairUsageRule, state: Optional[FairUsageState]) -> str:
    """Dedup one throttle alert per effective rule/tier within a throttle episode."""
    episode = _throttle_episode_key(state)
    tier_key = str(state.tier_id) if rule.tiered and state and state.tier_id else "-"
    return f"{rule.id}:{tier_key}:{episode}"


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

    rule_key = str(rule.id)
    rule_label = rule.name or f"#{rule.id}"
    # Quota warning 80%
    if 80 <= pct < 90:
        _notify_event(
            db, bindings, admin_chat_id, peer, "quota_warning_80",
            peer_name=peer_name, pct=pct, used=used, total=total,
            rule_name=rule_label,
            dedup_extra=rule_key,
        )
    # Quota warning 90%
    elif 90 <= pct < 100 and not is_throttled:
        _notify_event(
            db, bindings, admin_chat_id, peer, "quota_warning_90",
            peer_name=peer_name, pct=pct, used=used, total=total,
            rule_name=rule_label,
            dedup_extra=rule_key,
        )
    # Quota reached / throttled — only for *this* rule if it is actually over quota.
    # (Peer may be throttled because another applicable rule fired; do not blame every rule.)
    if is_throttled and state and state.rule_id == rule.id and is_rule_over_quota(used_rx, used_tx, rule, db):
        tier_label, tdl, tul = _throttle_detail_for_notification(db, rule, used_rx, used_tx, state)
        _notify_event(
            db, bindings, admin_chat_id, peer, "quota_hit",
            peer_name=peer_name, pct=pct, used=used, total=total,
            rule_name=rule_label,
            dedup_extra=_quota_hit_dedup_key(rule, state),
            tier_label=tier_label,
            throttle_dl_kbps=tdl,
            throttle_ul_kbps=tul,
        )


def notify_quota_lifted(db: Session, peer: Peer) -> None:
    """Called when a peer's throttle is removed."""
    cfg = _get_event_config(db, "quota_lifted")
    if not cfg or not cfg.enabled:
        return
    notify_admin = bool(cfg.notify_admin)

    peer_name = peer.name or peer.public_key[:12]
    bindings = _get_bindings_for_peer(db, peer.id)
    admin_chat_id = _get_admin_chat_id(db)
    hours = _dedup_hours("quota_lifted")
    from .placeholder import get_internal_admin_log_user_id

    card_png: Optional[bytes] = None

    def _deliver(chat_id: int, text: str) -> bool:
        nonlocal card_png
        if card_png is None:
            card_png = _fair_usage_card_png(db, peer) or b""
        if card_png:
            return _send_photo_sync(chat_id, card_png, text)
        return _send_message_sync(chat_id, text)

    for b in bindings:
        tg_user = db.get(TelegramUser, b.telegram_user_id)
        if not tg_user:
            continue
        if not effective_user_notification_enabled(db, tg_user.id, "quota_lifted"):
            continue
        msg_hash = _make_hash("quota_lifted", str(peer.id), str(tg_user.id), "lift")
        if _already_sent(db, tg_user.id, peer.id, "quota_lifted", msg_hash, within_hours=hours):
            continue
        lang = tg_user.language or "en"
        text = t("notif_quota_lifted", lang, name=peer_name)
        if _deliver(tg_user.telegram_user_id, text):
            _record_sent(db, tg_user.id, peer.id, "quota_lifted", msg_hash)

    if notify_admin and admin_chat_id:
        aid = get_internal_admin_log_user_id(db)
        msg_hash = _make_hash("quota_lifted", str(peer.id), "admin", "lift")
        if not _already_sent(db, aid, peer.id, "quota_lifted", msg_hash, within_hours=hours):
            text = t("notif_quota_lifted", "en", name=peer_name)
            if _deliver(admin_chat_id, f"[Admin] {text}"):
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
    rule_name: str = "",
    dedup_extra: str,
    tier_label: Optional[str] = None,
    throttle_dl_kbps: Optional[int] = None,
    throttle_ul_kbps: Optional[int] = None,
) -> None:
    cfg = _get_event_config(db, event_type)
    if not cfg or not cfg.enabled:
        return
    notify_admin = bool(cfg.notify_admin)

    hours = _dedup_hours(event_type)
    from .placeholder import get_internal_admin_log_user_id

    def _message_for_user(lang: str) -> str:
        if event_type == "quota_hit":
            if throttle_dl_kbps is not None and throttle_ul_kbps is not None:
                speed = _fmt_speed_pair(throttle_dl_kbps, throttle_ul_kbps)
                common = dict(
                    name=peer_name,
                    rule=rule_name or "—",
                    speed=speed,
                    pct=str(pct),
                    used=_fmt_bytes(used),
                    total=_fmt_bytes(total),
                )
                if tier_label:
                    return t("notif_quota_hit_tiered", lang, tier=tier_label, **common)
                return t("notif_quota_hit_flat", lang, **common)
            return t("notif_quota_hit", lang, name=peer_name)
        return t(
            "notif_quota_warning",
            lang,
            name=peer_name,
            pct=str(pct),
            used=_fmt_bytes(used),
            total=_fmt_bytes(total),
            rule=rule_name or "—",
        )

    card_png: Optional[bytes] = None

    def _deliver(chat_id: int, text: str) -> bool:
        nonlocal card_png
        if card_png is None:
            card_png = _fair_usage_card_png(db, peer) or b""
        if card_png:
            return _send_photo_sync(chat_id, card_png, text)
        return _send_message_sync(chat_id, text)

    for b in bindings:
        tg_user = db.get(TelegramUser, b.telegram_user_id)
        if not tg_user:
            continue
        if not effective_user_notification_enabled(db, tg_user.id, event_type):
            continue
        msg_hash = _make_hash(event_type, str(peer.id), str(tg_user.id), dedup_extra)
        if _already_sent(db, tg_user.id, peer.id, event_type, msg_hash, within_hours=hours):
            continue
        lang = tg_user.language or "en"
        text = _message_for_user(lang)
        if _deliver(tg_user.telegram_user_id, text):
            _record_sent(db, tg_user.id, peer.id, event_type, msg_hash)

    if notify_admin and admin_chat_id:
        aid = get_internal_admin_log_user_id(db)
        msg_hash = _make_hash(event_type, str(peer.id), "admin", dedup_extra)
        if _already_sent(db, aid, peer.id, event_type, msg_hash, within_hours=hours):
            return
        text = _message_for_user("en")
        if _deliver(admin_chat_id, f"[Admin] {text}"):
            _record_sent(db, aid, peer.id, event_type, msg_hash)


def _send_periodic_summary(db: Session, *, event_type: str, period: str, scope_btn_key: str, admin_header: str) -> None:
    """Shared daily/weekly summary: per-user text summary plus per-peer usage chart photos."""
    cfg = _get_event_config(db, event_type)
    if not cfg or not cfg.enabled:
        return
    notify_admin = bool(cfg.notify_admin)

    from ..fair_usage_usage import peer_scope_usage_bytes
    from .formatters import format_fair_usage_condensed

    now = datetime.now(timezone.utc)

    tg_users = db.query(TelegramUser).filter_by(is_blocked=False).all()
    admin_chat_id = _get_admin_chat_id(db)
    admin_lines = []
    # Render each peer's chart once per scope label (label is baked into the image).
    chart_cache: dict[tuple[int, str], bytes | None] = {}

    def _chart_for(peer: Peer, scope_label: str) -> bytes | None:
        key = (peer.id, scope_label)
        if key not in chart_cache:
            chart_cache[key] = _summary_chart_png(db, peer, period, scope_label, now)
        return chart_cache[key]

    admin_peers: list[Peer] = []
    for tg_user in tg_users:
        if not effective_user_notification_enabled(db, tg_user.id, event_type):
            continue
        bindings = (
            db.query(TelegramPeerBinding)
            .filter_by(telegram_user_id=tg_user.id, visible=True)
            .all()
        )
        if not bindings:
            continue

        lang = tg_user.language or "en"
        scope_label = t(scope_btn_key, lang)
        lines = [t("usage_header", lang, scope=scope_label)]
        user_peers: list[Peer] = []
        for b in bindings:
            peer = db.get(Peer, b.peer_id)
            if not peer:
                continue
            rx, tx = peer_scope_usage_bytes(peer.id, period, db, now)
            name = peer.name or peer.public_key[:12]
            lines.append(f"  {name}: \u2b07{_fmt_bytes(rx)} \u2b06{_fmt_bytes(tx)}")
            fu_line = format_fair_usage_condensed(peer, db, lang, now)
            if fu_line:
                lines.append(f"    {t('btn_limits', lang)}: {fu_line}")
            admin_lines.append(f"  {name} (@{tg_user.telegram_username}): \u2b07{_fmt_bytes(rx)} \u2b06{_fmt_bytes(tx)}")
            user_peers.append(peer)
            admin_peers.append(peer)

        if len(lines) > 1:
            _send_message_sync(tg_user.telegram_user_id, "\n".join(lines))
            for peer in user_peers:
                png = _chart_for(peer, scope_label)
                if png:
                    name = peer.name or peer.public_key[:12]
                    caption = t("usage_chart_caption", lang, name=name, scope=scope_label)
                    _send_photo_sync(tg_user.telegram_user_id, png, caption)

    if notify_admin and admin_chat_id and admin_lines:
        _send_message_sync(admin_chat_id, f"{admin_header}\n" + "\n".join(admin_lines))
        # Admin gets one aggregate chart across all reported peers, not per-peer spam.
        scope_label = t(scope_btn_key, "en")
        from .usage_chart_image import merge_usage_points

        series: list[list[dict]] = []
        mode = "days"
        seen: set[int] = set()
        for peer in admin_peers:
            if peer.id in seen:
                continue
            seen.add(peer.id)
            points, mode = _summary_points(db, peer.id, period, now)
            series.append(points)
        agg_points = merge_usage_points(series)
        name = t("adm_dashboard_chart_name", "en")
        png = _render_chart_png(name, scope_label, mode, agg_points)
        if png:
            caption = t("usage_chart_caption", "en", name=name, scope=scope_label)
            _send_photo_sync(admin_chat_id, png, caption)


def send_daily_summary(db: Session) -> None:
    """Send daily usage summary (text + per-peer charts) to all clients with bindings."""
    _send_periodic_summary(
        db,
        event_type="daily_summary",
        period="daily",
        scope_btn_key="btn_today",
        admin_header="[Admin] Daily usage summary",
    )


def send_weekly_summary(db: Session) -> None:
    """Send weekly usage summary (text + per-peer charts) to all clients with bindings."""
    _send_periodic_summary(
        db,
        event_type="weekly_summary",
        period="weekly",
        scope_btn_key="btn_this_week",
        admin_header="[Admin] Weekly usage summary",
    )
