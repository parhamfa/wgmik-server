"""One-sided traffic guard.

Real traffic always moves both ways: over any 15-minute window with at least
1 GiB of download, upload never fell below 0.83% of it. A counter that climbs
by gigabytes in one direction while the other barely moves (the 2026-06-16
incident) is a counter fault, not usage. Such bytes are moved out of the
rollups into a pending hold for an admin to count or discard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from .models import Peer, UsageHold, UsageMinute
from .usage_holds import DIRECTION_WORDS, EXCLUDED_HOLD_STATUSES, create_hold, parse_minutes_json, remove_from_rollups
from .usage_storage import floor_to_minute_utc


GUARD_WINDOW_MINUTES = 15
GUARD_MIN_BYTES = 1024 ** 3
GUARD_MAX_REVERSE_RATIO = 0.003

_OTHER = {"rx": "tx", "tx": "rx"}


def run_one_sided_guard(db: Session, now_utc: Optional[datetime] = None) -> list[tuple[int, str]]:
    """Hold one-sided bursts in the last 15 complete minutes. Returns (hold_id, note) per new hold.

    The caller commits and then sends the Telegram alerts.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is not None:
        now_utc = now_utc.astimezone(timezone.utc)
    # Only complete minutes: the poller may still be adding to the current one.
    window_end = floor_to_minute_utc(now_utc)
    window_start = window_end - timedelta(minutes=GUARD_WINDOW_MINUTES)

    counted: dict[int, dict[str, dict[datetime, int]]] = {}
    rows = (
        db.query(UsageMinute.peer_id, UsageMinute.minute_ts, UsageMinute.rx, UsageMinute.tx)
        .join(Peer, Peer.id == UsageMinute.peer_id)
        .filter(
            Peer.selected == True,  # noqa: E712
            UsageMinute.minute_ts >= window_start,
            UsageMinute.minute_ts < window_end,
        )
        .all()
    )
    for peer_id, minute_ts, rx, tx in rows:
        per_dir = counted.setdefault(int(peer_id), {"rx": {}, "tx": {}})
        per_dir["rx"][minute_ts] = per_dir["rx"].get(minute_ts, 0) + int(rx or 0)
        per_dir["tx"][minute_ts] = per_dir["tx"].get(minute_ts, 0) + int(tx or 0)

    notices: list[tuple[int, str]] = []
    for peer_id, per_dir in sorted(counted.items()):
        # Minutes already held (any status) are never held again; bytes of
        # pending/discarded holds still count toward the true volume, so a hold
        # in one direction never makes the other direction look one-sided.
        held_minutes = {"rx": set(), "tx": set()}
        held_bytes = {"rx": 0, "tx": 0}
        holds = (
            db.query(UsageHold)
            .filter(UsageHold.peer_id == peer_id, UsageHold.end_ts >= window_start)
            .all()
        )
        for hold in holds:
            if hold.direction not in held_minutes:
                continue
            for minute, amount in parse_minutes_json(hold.minutes_json).items():
                if minute < window_start or minute >= window_end:
                    continue
                held_minutes[hold.direction].add(minute)
                if hold.status in EXCLUDED_HOLD_STATUSES:
                    held_bytes[hold.direction] += amount

        true_volume = {d: sum(per_dir[d].values()) + held_bytes[d] for d in ("rx", "tx")}
        for direction in ("tx", "rx"):
            candidates = {
                minute: amount
                for minute, amount in per_dir[direction].items()
                if amount > 0 and minute not in held_minutes[direction]
            }
            candidate_total = sum(candidates.values())
            if candidate_total < GUARD_MIN_BYTES:
                continue
            reverse = true_volume[_OTHER[direction]]
            if reverse >= GUARD_MAX_REVERSE_RATIO * true_volume[direction]:
                continue
            peer = db.get(Peer, peer_id)
            if peer is None:
                continue
            ratio_pct = (reverse / true_volume[direction] * 100) if true_volume[direction] else 0.0
            hold, note = create_hold(
                db,
                peer=peer,
                direction=direction,
                reason="one_sided",
                minutes=candidates,
                start_ts=min(candidates),
                end_ts=max(candidates) + timedelta(minutes=1),
                reverse_bytes=reverse,
                reason_detail=(
                    f"{DIRECTION_WORDS[_OTHER[direction]]} was {ratio_pct:.3f}% of {DIRECTION_WORDS[direction]} "
                    f"over {GUARD_WINDOW_MINUTES} minutes; anything under {GUARD_MAX_REVERSE_RATIO * 100:.1f}% is held"
                ),
                now_utc=now_utc,
            )
            remove_from_rollups(db, peer_id, direction, candidates)
            held_minutes[direction].update(candidates)
            notices.append((hold.id, note))
    return notices
