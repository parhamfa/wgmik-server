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
from ..fair_usage_sync import evaluate_fair_usage_chain, get_applicable_fair_usage_rules, is_rule_over_quota
from ..fair_usage_tiers import active_tier_for_combined_usage, ordered_tiers_for_rule
from ..fair_usage_usage import (
    app_zoneinfo,
    compute_next_reset_utc_for_rule,
    format_scope_label,
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


def format_next_reset_from_iso(iso_str: str | None, lang: str = "en") -> str:
    """Format API `next_reset` ISO string for Telegram text and fair-usage card images."""
    if not iso_str:
        return "—"
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    reset_str = _format_next_reset_wall_clock(dt)
    return t("fu_next_reset", lang, time=reset_str)


def _escape_telegram_md(text: str) -> str:
    """Escape characters that break Telegram classic Markdown (user-supplied rule/peer names)."""
    if not text:
        return text
    out = []
    for ch in text:
        if ch in "\\_*[]()~`>#+-=|{}.!":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _progress_bar(pct: float, length: int = 10) -> str:
    filled = int(round(pct / 100 * length))
    filled = max(0, min(length, filled))
    return "\u2588" * filled + "\u2591" * (length - filled)


def format_peer_line(
    peer: Peer,
    router: Optional[Router],
    lang: str = "en",
    *,
    show_router: bool = False,
) -> str:
    """Short peer line for TG. Router is only shown when show_router=True (technical view)."""
    status = t("peer_disabled", lang) if peer.disabled else ""
    label = _escape_telegram_md(peer.name or peer.public_key[:12])
    parts = [f"**{label}**"]
    if show_router and router:
        rname = _escape_telegram_md(router.name or "?")
        iface = _escape_telegram_md(peer.interface or "")
        parts.append(f"@ {rname}/{iface}")
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


def format_fair_usage_condensed(peer: Peer, db: Session, lang: str = "en", now_utc: datetime | None = None) -> str:
    """Short summary for daily/weekly TG summaries."""
    now_utc = now_utc or datetime.now(timezone.utc)
    rules = get_applicable_fair_usage_rules(peer, db)
    if not rules:
        return ""
    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer.id).first()
    parts: list[str] = []
    for rule in rules:
        urx, utx = peer_scope_usage_for_rule(peer.id, rule, db, now_utc)
        if rule.tiered:
            tiers = ordered_tiers_for_rule(db, rule.id)
            if not tiers:
                continue
            used = urx + utx
            max_th = max(t.threshold_bytes for t in tiers)
            total = max_th or 1
            pct = min(100, round(used / total * 100)) if total > 0 else 0
        else:
            total = rule.download_quota_bytes or 1
            used = urx + utx if rule.quota_mode == "combined" else urx
            pct = min(100, round(used / total * 100)) if total > 0 else 0
        cnt, unit = normalize_scope_period(rule)
        slab = format_scope_label(cnt, unit)
        parts.append(f"{slab} {pct}%")
    winner = evaluate_fair_usage_chain(db, peer, now_utc)
    if state and state.throttled and winner:
        winner_rule, winner_tier = winner
        effective_label = winner_rule.name
        if winner_tier:
            tier_name = (winner_tier.name or "").strip() or f"≥{_fmt_bytes(winner_tier.threshold_bytes)}"
            effective_label = f"{effective_label} / {tier_name}"
        parts.append(f"{t('status_effective_rule', lang)}: {effective_label}")
    return " · ".join(parts)


def format_fair_usage_status(
    peer: Peer,
    db: Session,
    lang: str = "en",
    now_utc: datetime | None = None,
    *,
    detailed: bool = False,
) -> str:
    """
    Fair-usage block for TG. Default is simple (no rule names, no router, no scope_type).
    Pass detailed=True for admin-style labels (rule name, scope type).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    rules = get_applicable_fair_usage_rules(peer, db)
    if not rules:
        lab = _escape_telegram_md(peer.name or peer.public_key[:12])
        return f"**{lab}**: —"

    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer.id).first()
    throttled = state.throttled if state else False
    status = t("fu_throttled", lang) if throttled else t("fu_ok", lang)
    label = _escape_telegram_md(peer.name or peer.public_key[:12])
    winner = evaluate_fair_usage_chain(db, peer, now_utc)
    winning_rule_id = winner[0].id if winner else None

    blocks: list[str] = []
    if detailed and len(rules) > 1:
        blocks.append(f"**{label}**  [{status}]  _({len(rules)} rules)_")
    else:
        blocks.append(f"**{label}**  [{status}]")

    for rule in rules:
        used_rx, used_tx = peer_scope_usage_for_rule(peer.id, rule, db, now_utc)
        rn = _escape_telegram_md(rule.name)
        cnt, unit = normalize_scope_period(rule)
        slab = format_scope_label(cnt, unit)
        stype = rule.scope_type or "?"
        oq = is_rule_over_quota(used_rx, used_tx, rule, db)
        effective = throttled and winning_rule_id == rule.id

        if detailed:
            rule_hdr = f"  • **{rn}** · {slab} · `{stype}`{' ⚠' if oq else ''}{' [effective]' if effective else ''}"
        else:
            rule_hdr = f"  • {slab}{' ⚠' if oq else ''}{' [effective]' if effective else ''}"

        if rule.tiered:
            tiers = ordered_tiers_for_rule(db, rule.id)
            combined = used_rx + used_tx
            active = active_tier_for_combined_usage(tiers, combined)
            max_th = max((t.threshold_bytes for t in tiers), default=0)
            total = max_th or 1
            pct = min(100, round(combined / total * 100)) if total > 0 else 0
            bar = _progress_bar(pct)
            next_reset = compute_next_reset_utc_for_rule(rule, db, now_utc)
            reset_str = _format_next_reset_wall_clock(next_reset)
            quota_line = t("fu_quota_line", lang, used=_fmt_bytes(combined), total=_fmt_bytes(total))
            reset_line = t("fu_next_reset", lang, time=reset_str)
            blocks.append(rule_hdr)
            blocks.append(f"    {bar} {pct}%")
            blocks.append(f"    {quota_line}")
            if active:
                an = _escape_telegram_md((active.name or "").strip() or f"≥{_fmt_bytes(active.threshold_bytes)}")
                blocks.append(f"    _Current speed tier: {an} · ↓{active.throttle_download_kbps/1000:.1f} ↑{active.throttle_upload_kbps/1000:.1f} Mbps_")
            blocks.append(f"    {reset_line}")
        elif rule.quota_mode == "combined":
            total = rule.download_quota_bytes or 1
            used = used_rx + used_tx
            pct = min(100, round(used / total * 100)) if total > 0 else 0
            bar = _progress_bar(pct)
            next_reset = compute_next_reset_utc_for_rule(rule, db, now_utc)
            reset_str = _format_next_reset_wall_clock(next_reset)
            quota_line = t("fu_quota_line", lang, used=_fmt_bytes(used), total=_fmt_bytes(total))
            reset_line = t("fu_next_reset", lang, time=reset_str)
            blocks.append(rule_hdr)
            blocks.append(f"    {bar} {pct}%")
            blocks.append(f"    {quota_line}")
            blocks.append(f"    {reset_line}")
        else:
            dl_total = rule.download_quota_bytes or 1
            ul_total = rule.upload_quota_bytes or 1
            pct_dl = min(100, round(used_rx / dl_total * 100)) if dl_total > 0 else 0
            pct_ul = min(100, round(used_tx / ul_total * 100)) if ul_total > 0 else 0
            bar_dl = _progress_bar(pct_dl)
            bar_ul = _progress_bar(pct_ul)
            next_reset = compute_next_reset_utc_for_rule(rule, db, now_utc)
            reset_str = _format_next_reset_wall_clock(next_reset)
            reset_line = t("fu_next_reset", lang, time=reset_str)
            blocks.append(rule_hdr)
            blocks.append(f"    DL {bar_dl} {pct_dl}%  {_fmt_bytes(used_rx)} / {_fmt_bytes(dl_total)}")
            if ul_total > 0:
                blocks.append(f"    UL {bar_ul} {pct_ul}%  {_fmt_bytes(used_tx)} / {_fmt_bytes(ul_total)}")
            blocks.append(f"    {reset_line}")

    return "\n".join(blocks)
