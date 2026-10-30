"""Usage self-check.

Recomputes each selected peer's usage from the raw counter samples with its own
minimal logic (deliberately not usage_deltas, so a bug there cannot hide
itself) and compares it with the minute rollups the dashboards read.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import SessionLocal
from .destructive_ops import exclusive_operation_gate
from .fair_usage_usage import app_zoneinfo
from .models import Action, Peer, SettingsKV, UsageMinute, UsageSample
from .usage_holds import (
    DIRECTION_WORDS,
    format_bytes,
    format_local_window,
    held_minute_bytes,
    peer_label,
    send_admin_telegram,
)


SELFCHECK_KEY = "usage_selfcheck_last"
MISMATCH_RATIO = 0.01
MISMATCH_MIN_BYTES = 100 * 1024 * 1024


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _iso(dt: datetime) -> str:
    return _naive_utc(dt).replace(tzinfo=timezone.utc).isoformat()


def today_window(now_utc: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Local midnight today until now, both as aware UTC datetimes."""
    now_utc = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    tz = app_zoneinfo()
    local_midnight = datetime.combine(now_utc.astimezone(tz).date(), time(0, 0), tzinfo=tz)
    return local_midnight.astimezone(timezone.utc), now_utc


def _recompute_from_raw(db: Session, peer_id: int, start: datetime, end: datetime) -> tuple[int, int]:
    """Baseline = last sample before the window; each later reading adds cur - prev, or cur after a drop."""
    baseline = (
        db.query(UsageSample.rx, UsageSample.tx)
        .filter(UsageSample.peer_id == peer_id, UsageSample.ts < start)
        .order_by(UsageSample.ts.desc(), UsageSample.id.desc())
        .first()
    )
    rows = (
        db.query(UsageSample.rx, UsageSample.tx)
        .filter(UsageSample.peer_id == peer_id, UsageSample.ts >= start, UsageSample.ts < end)
        .order_by(UsageSample.ts.asc(), UsageSample.id.asc())
        .all()
    )
    totals = [0, 0]
    prev = (int(baseline[0] or 0), int(baseline[1] or 0)) if baseline else None
    for rx, tx in rows:
        cur = (int(rx or 0), int(tx or 0))
        if prev is not None:
            for i in (0, 1):
                totals[i] += cur[i] - prev[i] if cur[i] >= prev[i] else cur[i]
        prev = cur
    return totals[0], totals[1]


def run_usage_selfcheck(
    db: Session,
    *,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
    now_utc: Optional[datetime] = None,
) -> dict[str, Any]:
    """Compare counted (minute rollup) usage with raw-sample usage minus held bytes.

    Saves the summary in SettingsKV and logs one Action per mismatching peer.
    The caller commits and decides whether to alert Telegram.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    default_start, default_end = today_window(now_utc)
    start = _naive_utc(window_start or default_start)
    end = _naive_utc(window_end or default_end)

    peers = db.query(Peer).filter(Peer.selected == True).order_by(Peer.id.asc()).all()  # noqa: E712
    counted_rows = (
        db.query(UsageMinute.peer_id, func.coalesce(func.sum(UsageMinute.rx), 0), func.coalesce(func.sum(UsageMinute.tx), 0))
        .filter(UsageMinute.minute_ts >= start, UsageMinute.minute_ts < end)
        .group_by(UsageMinute.peer_id)
        .all()
    )
    counted_by_peer = {int(pid): (int(rx or 0), int(tx or 0)) for pid, rx, tx in counted_rows}

    mismatches: list[dict[str, Any]] = []
    largest_gap_pct = 0.0
    for peer in peers:
        raw_rx, raw_tx = _recompute_from_raw(db, peer.id, start, end)
        held = held_minute_bytes(db, peer.id, start=start, end=end)
        held_rx = sum(v[0] for v in held.values())
        held_tx = sum(v[1] for v in held.values())
        expected = {"rx": raw_rx - held_rx, "tx": raw_tx - held_tx}
        counted_rx, counted_tx = counted_by_peer.get(int(peer.id), (0, 0))
        counted = {"rx": counted_rx, "tx": counted_tx}
        peer_mismatches = []
        for direction in ("rx", "tx"):
            diff = abs(counted[direction] - expected[direction])
            scale = max(abs(expected[direction]), counted[direction], 1)
            largest_gap_pct = max(largest_gap_pct, diff / scale * 100)
            if diff > max(MISMATCH_RATIO * abs(expected[direction]), MISMATCH_MIN_BYTES):
                peer_mismatches.append(
                    {
                        "peer_id": int(peer.id),
                        "name": peer_label(peer, peer.id),
                        "direction": direction,
                        "counted": counted[direction],
                        "recomputed": expected[direction],
                    }
                )
        if peer_mismatches:
            mismatches.extend(peer_mismatches)
            details = "; ".join(
                f"{DIRECTION_WORDS[m['direction']]} counted {format_bytes(m['counted'])} "
                f"but raw counters give {format_bytes(m['recomputed'])}"
                for m in peer_mismatches
            )
            db.add(
                Action(
                    peer_id=peer.id,
                    ts=_naive_utc(now_utc),
                    action="usage_selfcheck_mismatch",
                    note=f"Usage self-check for {format_local_window(start, end)}: {details}.",
                )
            )

    summary = {
        "ran_at": _iso(now_utc),
        "window_start": _iso(start),
        "window_end": _iso(end),
        "peers_checked": len(peers),
        "largest_gap_pct": round(largest_gap_pct, 3),
        "mismatches": mismatches,
    }
    kv = db.get(SettingsKV, SELFCHECK_KEY)
    if kv is None:
        db.add(SettingsKV(key=SELFCHECK_KEY, value=json.dumps(summary)))
    else:
        kv.value = json.dumps(summary)
    db.flush()
    return summary


def format_selfcheck_result(summary: dict[str, Any], label: str = "today so far") -> str:
    count = int(summary.get("peers_checked") or 0)
    peers = f"{count} peer" if count == 1 else f"{count} peers"
    mismatches = summary.get("mismatches") or []
    if not mismatches:
        return (
            f"Self-check for {label}: {peers}, all match "
            f"(largest gap {float(summary.get('largest_gap_pct') or 0):.1f}%)."
        )
    lines = [
        f"- {m['name']}: {DIRECTION_WORDS.get(m['direction'], m['direction'])} counted "
        f"{format_bytes(m['counted'])}, raw counters say {format_bytes(m['recomputed'])}"
        for m in mismatches
    ]
    noun = "mismatch" if len(mismatches) == 1 else "mismatches"
    return f"Self-check for {label}: {peers}, {len(mismatches)} {noun}:\n" + "\n".join(lines)


def run_usage_selfcheck_locked(*, notify: bool, label: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Run today's self-check serialised with polling. Returns None while maintenance runs.

    With notify=True, one admin Telegram message lists any mismatches.
    """
    if exclusive_operation_gate.is_active():
        return None
    with exclusive_operation_gate.coordinated_activity():
        if exclusive_operation_gate.is_active():
            return None
        db = SessionLocal()
        try:
            summary = run_usage_selfcheck(db)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    if notify and summary["mismatches"]:
        if label is None:
            end_local = datetime.fromisoformat(summary["window_end"]).astimezone(app_zoneinfo())
            label = f"{end_local:%Y-%m-%d} until {end_local:%H:%M}"
        send_admin_telegram(format_selfcheck_result(summary, label))
    return summary


def selfcheck_reply_text() -> str:
    """Plain-English answer for the admin /selfcheck command."""
    summary = run_usage_selfcheck_locked(notify=False)
    if summary is None:
        return "Usage maintenance is running. Try /selfcheck again when it finishes."
    return format_selfcheck_result(summary)
