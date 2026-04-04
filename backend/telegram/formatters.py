"""Format usage / fair-usage data for Telegram messages."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..models import (
    FairUsageState,
    Peer,
    Router,
    UsageDaily,
    UsageMonthly,
)
from ..fair_usage_sync import get_effective_fair_usage_rule
from ..fair_usage_usage import (
    app_zoneinfo,
    compute_next_reset_utc_for_rule,
    normalize_scope_period,
    peer_scope_usage_for_rule,
)
from .i18n import t


def _fmt_bytes(b: int) -> str:
    if b <= 0:
        return "0"
    gb = b / (1024 ** 3)
    if gb >= 1:
        return f"{gb:.1f} GB" if gb != int(gb) else f"{int(gb)} GB"
    mb = b / (1024 ** 2)
    return f"{mb:.1f} MB" if mb != int(mb) else f"{int(mb)} MB"


def _format_next_reset_wall_clock(next_reset_utc: datetime) -> str:
    """Next reset instant formatted in the app timezone (Settings / global timezone)."""
    if next_reset_utc.tzinfo is None:
        next_reset_utc = next_reset_utc.replace(tzinfo=timezone.utc)
    local = next_reset_utc.astimezone(app_zoneinfo())
    return local.strftime("%Y-%m-%d %H:%M")


def _progress_bar(pct: float, length: int = 10) -> str:
    filled = int(round(pct / 100 * length))
    filled = max(0, min(length, filled))
    return "\u2588" * filled + "\u2591" * (length - filled)


def format_peer_line(peer: Peer, router: Optional[Router], lang: str = "en") -> str:
    status = t("peer_disabled", lang) if peer.disabled else ""
    rname = router.name if router else "?"
    label = peer.name or peer.public_key[:12]
    parts = [f"**{label}**", f"@ {rname}/{peer.interface}"]
    if status:
        parts.append(f"[{status}]")
    return " ".join(parts)


def format_usage_scope(
    peer: Peer, scope: str, db: Session, lang: str = "en", now_utc: datetime | None = None
) -> str:
    from ..fair_usage_usage import peer_scope_usage_bytes

    now_utc = now_utc or datetime.now(timezone.utc)
    scope_map = {"today": "daily", "week": "weekly", "month": "monthly"}
    ts = scope_map.get(scope, "monthly")
    rx, tx = peer_scope_usage_bytes(peer.id, ts, db, now_utc)
    label = peer.name or peer.public_key[:12]
    scope_label = {"today": t("btn_today", lang), "week": t("btn_this_week", lang), "month": t("btn_this_month", lang)}.get(scope, scope)
    header = t("usage_header", lang, scope=scope_label)
    return f"**{label}**\n{header}\n  \u2b07 {_fmt_bytes(rx)}  \u2b06 {_fmt_bytes(tx)}  \u2211 {_fmt_bytes(rx + tx)}"


def format_fair_usage_status(
    peer: Peer, db: Session, lang: str = "en", now_utc: datetime | None = None
) -> str:
    now_utc = now_utc or datetime.now(timezone.utc)
    rule = get_effective_fair_usage_rule(peer, db)
    if not rule:
        return f"**{peer.name or peer.public_key[:12]}**: —"

    used_rx, used_tx = peer_scope_usage_for_rule(peer.id, rule, db, now_utc)
    total = rule.download_quota_bytes or 1
    used = used_rx + used_tx if rule.quota_mode == "combined" else used_rx
    pct = min(100, round(used / total * 100))
    bar = _progress_bar(pct)

    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer.id).first()
    throttled = state.throttled if state else False
    status = t("fu_throttled", lang) if throttled else t("fu_ok", lang)

    next_reset = compute_next_reset_utc_for_rule(rule, db, now_utc)
    reset_str = _format_next_reset_wall_clock(next_reset)

    label = peer.name or peer.public_key[:12]
    quota_line = t("fu_quota_line", lang, used=_fmt_bytes(used), total=_fmt_bytes(total))
    reset_line = t("fu_next_reset", lang, time=reset_str)
    cnt, unit = normalize_scope_period(rule)
    scope = f"{cnt}{unit[0]}" if cnt > 1 else unit

    lines = [
        f"**{label}** — {rule.name} ({scope})",
        f"  {bar} {pct}%  [{status}]",
        f"  {quota_line}",
        f"  {reset_line}",
    ]
    return "\n".join(lines)
