"""
Operational diagnosis: Telegram usage charts vs web panel (same peer_id → same series).

Run: ``python bin/diagnose-tg-usage-charts.py`` from the repo root (uses DATABASE_URL / settings).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from .calendar_utils import app_date_calendar, selected_month_bounds_utc, utc_range_to_local_day_bounds
from .fair_usage_usage import app_zoneinfo
from .models import Peer, Router, TelegramPeerBinding, TelegramUser, UsageDaily, UsageMinute, UsageSample
from .telegram.usage_chart_image import _calendar_day_start_utc, usage_points_for_tg_menu


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _router_matches(name: str, substr: str) -> bool:
    if not substr.strip():
        return True
    return substr.strip().lower() in _norm(name)


def _peer_name_matches(peer_name: str, filters: Sequence[str]) -> bool:
    if not filters:
        return True
    n = _norm(peer_name)
    return any(f.strip() and f.strip().lower() in n for f in filters)


def build_diagnosis_report(
    db: Session,
    *,
    router_name_substr: str = "",
    peer_name_filters: Sequence[str] | None = None,
    telegram_telegram_user_id: int | None = None,
) -> str:
    """
    Return a multi-line human-readable report.

    ``peer_name_filters``: if non-empty, only peers whose name contains any filter (case-insensitive).
    ``telegram_telegram_user_id``: filter to one Telegram account (platform user id), or all users.
    """
    filters = [x for x in (peer_name_filters or []) if (x or "").strip()]
    lines: list[str] = []
    lines.append("=== TG usage chart vs web panel diagnosis ===")
    lines.append("")
    lines.append(
        "Charts use the same data as GET /peers/{id}/usage for that peer_id. "
        "The bot only charts peers linked in telegram_peer_bindings (visible)."
    )
    lines.append("")
    lines.append(
        "UI note: live WireGuard RX/TX on the peer page come from RouterOS (cumulative counters), "
        "not from the usage chart API. Compare the chart card to Telegram, not only live counters."
    )
    lines.append("")

    routers = db.query(Router).order_by(Router.id.asc()).all()
    rids = [r.id for r in routers if _router_matches(r.name or "", router_name_substr)]
    if router_name_substr.strip() and not rids:
        lines.append(f"No routers matching name substring {router_name_substr!r}.")
        return "\n".join(lines)

    peers = (
        db.query(Peer)
        .filter(Peer.router_id.in_(rids if rids else [r.id for r in routers]))
        .order_by(Peer.id.asc())
        .all()
    )
    if filters:
        peers = [p for p in peers if _peer_name_matches(p.name or "", filters)]

    by_router_name: dict[int, str] = {r.id: r.name or "" for r in routers}
    dup_names: dict[tuple[int, str], list[int]] = defaultdict(list)
    for p in db.query(Peer).all():
        key = (p.router_id, _norm(p.name or ""))
        if key[1]:
            dup_names[key].append(int(p.id))

    lines.append("--- Duplicate peer names on same router (same router_id + name) ---")
    shown_dup = False
    for (rid, pname), ids in sorted(dup_names.items(), key=lambda x: (x[0][0], x[0][1])):
        if len(ids) <= 1:
            continue
        if rids and rid not in rids:
            continue
        if filters and not _peer_name_matches(pname, filters):
            continue
        shown_dup = True
        rname = by_router_name.get(rid, "?")
        lines.append(f"  router {rid} ({rname!r}) name={pname!r} -> peer ids {ids}")
    if not shown_dup:
        lines.append("  (none in scope)")
    lines.append("")

    tg_q = db.query(TelegramUser).order_by(TelegramUser.id.asc())
    if telegram_telegram_user_id is not None:
        tg_q = tg_q.filter(TelegramUser.telegram_user_id == int(telegram_telegram_user_id))
    tg_users = tg_q.all()
    if not tg_users:
        lines.append("No telegram_users rows" + (f" for telegram_user_id={telegram_telegram_user_id}" if telegram_telegram_user_id else "") + ".")
        return "\n".join(lines)

    now = datetime.now(timezone.utc)
    month_start, month_end = selected_month_bounds_utc(now, app_zoneinfo(), app_date_calendar())
    day_start = _calendar_day_start_utc(now)
    # Match usage_points_for_tg_menu / compute_peer_usage_points: cap end at now.
    month_end_capped = min(month_end, now)
    month_start_day, month_end_day = utc_range_to_local_day_bounds(
        month_start,
        month_end_capped,
        app_zoneinfo(),
    )

    lines.append("--- Telegram bindings (visible) ---")
    for tu in tg_users:
        lines.append(
            f"TelegramUser db_id={tu.id} telegram_user_id={tu.telegram_user_id} "
            f"@{tu.telegram_username or ''} blocked={tu.is_blocked}"
        )
        bindings = (
            db.query(TelegramPeerBinding)
            .filter_by(telegram_user_id=tu.id, visible=True)
            .order_by(TelegramPeerBinding.id.asc())
            .all()
        )
        if not bindings:
            lines.append("  (no visible bindings)")
            lines.append("")
            continue
        for b in bindings:
            p = db.get(Peer, b.peer_id)
            if not p:
                lines.append(f"  binding id={b.id} peer_id={b.peer_id} ORPHAN (no peer row)")
                continue
            rname = by_router_name.get(p.router_id, "?")
            flags: list[str] = []
            if not p.selected:
                flags.append("selected=False (scheduler skips → often empty charts)")
            if dup_names.get((p.router_id, _norm(p.name or ""))) and len(dup_names[(p.router_id, _norm(p.name or ""))]) > 1:
                others = [x for x in dup_names[(p.router_id, _norm(p.name or ""))] if x != p.id]
                flags.append(f"DUPLICATE_NAME peer_ids={others}")
            # usage rollups
            d_count = (
                db.query(func.count(UsageDaily.id))
                .filter(
                    UsageDaily.peer_id == p.id,
                    UsageDaily.day >= month_start_day,
                    UsageDaily.day <= month_end_day,
                )
                .scalar()
                or 0
            )
            d_sum = (
                db.query(func.coalesce(func.sum(UsageDaily.rx), 0), func.coalesce(func.sum(UsageDaily.tx), 0))
                .filter(
                    UsageDaily.peer_id == p.id,
                    UsageDaily.day >= month_start_day,
                    UsageDaily.day <= month_end_day,
                )
                .one()
            )
            m_cnt = (
                db.query(func.count(UsageMinute.id))
                .filter(
                    UsageMinute.peer_id == p.id,
                    UsageMinute.minute_ts >= day_start.replace(tzinfo=None),
                    UsageMinute.minute_ts <= now.replace(tzinfo=None),
                )
                .scalar()
                or 0
            )
            s_cnt = (
                db.query(func.count(UsageSample.id))
                .filter(
                    UsageSample.peer_id == p.id,
                    UsageSample.ts >= day_start.replace(tzinfo=None),
                    UsageSample.ts <= now.replace(tzinfo=None),
                )
                .scalar()
                or 0
            )
            pts_today, _ = usage_points_for_tg_menu(db, p.id, "today", now)
            pts_month, _ = usage_points_for_tg_menu(db, p.id, "month", now)
            today_tot = sum(x.get("rx", 0) + x.get("tx", 0) for x in pts_today)
            month_tot = sum(x.get("rx", 0) + x.get("tx", 0) for x in pts_month)

            lines.append(
                f"  binding id={b.id} peer_id={p.id} name={p.name!r} router={p.router_id}({rname!r}) "
                f"iface={p.interface!r} pubkey={p.public_key[:16]}…"
            )
            if flags:
                lines.append("    ** " + " | ".join(flags))
            lines.append(
                f"    usage_daily rows this panel-month [{month_start_day}..{month_end_day}]: {int(d_count)} "
                f"sum_rx+tx={int(d_sum[0] + d_sum[1])}"
            )
            lines.append(
                f"    usage_minute rows local-today window: {int(m_cnt)} | usage_sample rows: {int(s_cnt)}"
            )
            lines.append(
                f"    TG-equivalent series totals: today_pts={len(pts_today)} bytes~{today_tot} | "
                f"month_pts={len(pts_month)} bytes~{month_tot}"
            )

            # Suggest alternate peer with same name+router that has more month usage (stale binding hint)
            if filters or router_name_substr.strip():
                skey = (p.router_id, _norm(p.name or ""))
                candidates = dup_names.get(skey, [])
                best: tuple[int, int] | None = None
                for oid in candidates:
                    if oid == p.id:
                        continue
                    o = db.get(Peer, oid)
                    if not o:
                        continue
                    sm = (
                        db.query(func.coalesce(func.sum(UsageDaily.rx), 0), func.coalesce(func.sum(UsageDaily.tx), 0))
                        .filter(
                            UsageDaily.peer_id == oid,
                            UsageDaily.day >= month_start_day,
                            UsageDaily.day <= month_end_day,
                        )
                        .one()
                    )
                    tot = int(sm[0] + sm[1])
                    if best is None or tot > best[1]:
                        best = (oid, tot)
                cur_tot = int(d_sum[0] + d_sum[1])
                if best and best[1] > cur_tot and best[1] > 0:
                    lines.append(
                        f"    !! SUSPECT_STALE_BINDING: peer_id={best[0]} has larger month sum ({best[1]} B) "
                        f"than bound peer ({cur_tot} B) for same router+name — re-link Telegram user to peer_id={best[0]}."
                    )
        lines.append("")

    if peers and (filters or router_name_substr.strip()):
        lines.append("--- Peers in name/router filter (for comparison to bindings above) ---")
        for p in peers:
            rname = by_router_name.get(p.router_id, "?")
            lines.append(f"  peer_id={p.id} name={p.name!r} router={p.router_id}({rname!r}) selected={p.selected}")
        lines.append("")

    lines.append("=== end ===")
    return "\n".join(lines)


def iter_lines_for_tests(report: str) -> Iterable[str]:
    return (ln for ln in report.splitlines())
