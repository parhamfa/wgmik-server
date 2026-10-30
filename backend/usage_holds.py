"""Usage holds: bytes kept out of usage until an admin counts or discards them.

Holds come from the poller (one reading faster than the per-peer rate cap) and
from the one-sided traffic guard (usage_guard.py). Pending and discarded holds
are excluded wherever usage is rebuilt from raw samples; approving a hold adds
its bytes back to the minute, daily and monthly rollups.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from .db import SessionLocal
from .destructive_ops import exclusive_operation_gate
from .fair_usage_usage import app_zoneinfo
from .models import Action, Peer, SettingsKV, UsageDaily, UsageHold, UsageMinute, UsageMonthly
from .usage_deltas import counter_day_key
from .usage_storage import bulk_upsert_usage_minute, floor_to_minute_utc

logger = logging.getLogger("wgmik.usage_holds")

HOLD_STATUSES = ("pending", "approved", "discarded")
# Bytes of these holds are not part of usage; approved holds were added back.
EXCLUDED_HOLD_STATUSES = ("pending", "discarded")
# tx is what the router sends to the client (download); rx is the client's upload.
DIRECTION_WORDS = {"tx": "download", "rx": "upload"}
REASON_LABELS = {"one_sided": "one-sided traffic", "implausible_jump": "implausible jump"}

MAX_PEER_RATE_SETTING_KEY = "usage_max_peer_rate_mbps"
DEFAULT_MAX_PEER_RATE_MBPS = 1000
MINUTE_KEY_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class HoldDecision:
    status: str  # approved | discarded | pending | missing | busy
    changed: bool
    message: str


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def minute_key(dt: datetime) -> str:
    return floor_to_minute_utc(_naive_utc(dt)).strftime(MINUTE_KEY_FORMAT)


def parse_minutes_json(value: Optional[str]) -> dict[datetime, int]:
    try:
        raw = json.loads(value or "{}")
    except ValueError:
        return {}
    out: dict[datetime, int] = {}
    for key, amount in raw.items():
        try:
            minute = datetime.strptime(str(key)[:19], MINUTE_KEY_FORMAT)
        except ValueError:
            continue
        out[minute] = out.get(minute, 0) + int(amount or 0)
    return out


def format_bytes(value: int) -> str:
    value = int(value or 0)
    if value >= 1024 ** 3:
        return f"{value / 1024 ** 3:.2f} GB"
    if value >= 1024 ** 2:
        return f"{value / 1024 ** 2:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def peer_label(peer: Optional[Peer], peer_id: int) -> str:
    if peer is None:
        return f"peer #{peer_id}"
    return peer.name or (peer.public_key or "")[:12] or f"peer #{peer_id}"


def max_peer_rate_bytes_per_second(db: Session) -> int:
    kv = db.get(SettingsKV, MAX_PEER_RATE_SETTING_KEY)
    mbps = DEFAULT_MAX_PEER_RATE_MBPS
    if kv and str(kv.value or "").strip():
        try:
            mbps = max(1, int(float(str(kv.value).strip())))
        except ValueError:
            mbps = DEFAULT_MAX_PEER_RATE_MBPS
    return mbps * 1_000_000 // 8


def held_minute_totals(
    rows: Iterable[tuple[str, Optional[str]]],
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    include_end: bool = False,
) -> dict[datetime, list[int]]:
    """Sum held bytes per UTC minute as {minute: [rx, tx]} from (direction, minutes_json) rows."""
    start_minute = floor_to_minute_utc(_naive_utc(start)) if start is not None else None
    end_naive = _naive_utc(end) if end is not None else None
    end_minute = floor_to_minute_utc(end_naive) if end_naive is not None else None
    out: dict[datetime, list[int]] = {}
    for direction, minutes_json in rows:
        idx = 0 if direction == "rx" else 1
        for minute, amount in parse_minutes_json(minutes_json).items():
            if start_minute is not None and minute < start_minute:
                continue
            if end_naive is not None:
                if include_end and minute > end_minute:
                    continue
                if not include_end and minute >= end_naive:
                    continue
            out.setdefault(minute, [0, 0])[idx] += amount
    return out


def held_minute_bytes(
    db: Session,
    peer_id: int,
    *,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    include_end: bool = False,
) -> dict[datetime, list[int]]:
    """Per-minute bytes of the peer's pending/discarded holds, i.e. bytes that are not usage."""
    query = db.query(UsageHold.direction, UsageHold.minutes_json).filter(
        UsageHold.peer_id == peer_id,
        UsageHold.status.in_(EXCLUDED_HOLD_STATUSES),
    )
    if start is not None:
        # A hold's minutes never come after its end_ts.
        query = query.filter(UsageHold.end_ts >= floor_to_minute_utc(_naive_utc(start)))
    return held_minute_totals(query.all(), start=start, end=end, include_end=include_end)


def _adjust_rollups(
    db: Session,
    peer_id: int,
    direction: str,
    minutes: dict[datetime, int],
    sign: int,
    *,
    minute_floor: Optional[datetime] = None,
) -> None:
    """Add (sign=1) or remove (sign=-1) held bytes from the minute, daily and monthly rollups.

    Day keys are app-local and month keys UTC, exactly as the poller writes them.
    Updates are single SQL statements so they never overwrite a concurrent poll.
    """
    tz = app_zoneinfo()
    is_rx = direction == "rx"
    minute_col = UsageMinute.rx if is_rx else UsageMinute.tx
    daily_col = UsageDaily.rx if is_rx else UsageDaily.tx
    monthly_col = UsageMonthly.rx if is_rx else UsageMonthly.tx
    per_day: dict[str, int] = {}
    per_month: dict[str, int] = {}
    minute_rows: list[dict[str, object]] = []
    for minute, amount in sorted(minutes.items()):
        if amount <= 0:
            continue
        day_key = counter_day_key(minute, tz)
        month_key = minute.strftime("%Y-%m")
        per_day[day_key] = per_day.get(day_key, 0) + amount
        per_month[month_key] = per_month.get(month_key, 0) + amount
        if sign > 0:
            if minute_floor is None or minute >= minute_floor:
                minute_rows.append(
                    {"peer_id": peer_id, "minute_ts": minute, "rx": amount if is_rx else 0, "tx": 0 if is_rx else amount}
                )
        else:
            db.execute(
                update(UsageMinute)
                .where(UsageMinute.peer_id == peer_id, UsageMinute.minute_ts == minute)
                .values({minute_col: func.max(0, minute_col - amount)})
                .execution_options(synchronize_session=False)
            )
    if minute_rows:
        bulk_upsert_usage_minute(db, minute_rows)

    for model, key_col, col, buckets in (
        (UsageDaily, UsageDaily.day, daily_col, per_day),
        (UsageMonthly, UsageMonthly.month_key, monthly_col, per_month),
    ):
        for key, amount in buckets.items():
            new_value = col + amount if sign > 0 else func.max(0, col - amount)
            result = db.execute(
                update(model)
                .where(model.peer_id == peer_id, key_col == key)
                .values({col: new_value})
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0 and sign > 0:
                row = model(peer_id=peer_id, rx=amount if is_rx else 0, tx=0 if is_rx else amount)
                setattr(row, key_col.key, key)
                db.add(row)
    db.flush()


def remove_from_rollups(db: Session, peer_id: int, direction: str, minutes: dict[datetime, int]) -> None:
    _adjust_rollups(db, peer_id, direction, minutes, -1)


def format_local_window(start: datetime, end: datetime) -> str:
    tz = app_zoneinfo()
    start_local = _naive_utc(start).replace(tzinfo=timezone.utc).astimezone(tz)
    end_local = _naive_utc(end).replace(tzinfo=timezone.utc).astimezone(tz)
    if start_local.date() == end_local.date():
        return f"{start_local:%Y-%m-%d %H:%M}-{end_local:%H:%M} ({tz.key})"
    return f"{start_local:%Y-%m-%d %H:%M} to {end_local:%Y-%m-%d %H:%M} ({tz.key})"


def create_hold(
    db: Session,
    *,
    peer: Peer,
    direction: str,
    reason: str,
    minutes: dict[datetime, int],
    start_ts: datetime,
    end_ts: datetime,
    reverse_bytes: int,
    reason_detail: str,
    now_utc: datetime,
) -> tuple[UsageHold, str]:
    """Record a pending hold plus its usage_held Action. Callers move the bytes themselves.

    Returns the hold and the plain-English note (also used for the Telegram alert).
    """
    minutes = {floor_to_minute_utc(_naive_utc(m)): int(b) for m, b in minutes.items() if int(b) > 0}
    total = sum(minutes.values())
    hold = UsageHold(
        peer_id=peer.id,
        direction=direction,
        reason=reason,
        start_ts=_naive_utc(start_ts),
        end_ts=_naive_utc(end_ts),
        bytes=total,
        minutes_json=json.dumps({m.strftime(MINUTE_KEY_FORMAT): b for m, b in sorted(minutes.items())}),
        status="pending",
        created_at=_naive_utc(now_utc),
    )
    db.add(hold)
    db.flush()

    word = DIRECTION_WORDS.get(direction, direction)
    other = DIRECTION_WORDS["rx" if direction == "tx" else "tx"]
    span = "window" if reason == "one_sided" else "reading"
    note = (
        f"Held {format_bytes(total)} of {word} for {peer_label(peer, peer.id)}, "
        f"{format_local_window(start_ts, end_ts)}. {other.capitalize()} in the same {span}: "
        f"{format_bytes(reverse_bytes)}. Reason: {REASON_LABELS.get(reason, reason)} ({reason_detail}). "
        f"Not counted until an admin chooses Count it or Discard (hold #{hold.id})."
    )
    db.add(Action(peer_id=peer.id, ts=_naive_utc(now_utc), action="usage_held", note=note))
    return hold, note


def _minute_retention_floor(db: Session, now_utc: datetime) -> datetime:
    from .usage_maintenance import load_retention_settings

    days = load_retention_settings(db)["minute_rollup_retention_days"]
    return floor_to_minute_utc(_naive_utc(now_utc) - timedelta(days=days))


def decide_hold(
    db: Session,
    hold_id: int,
    *,
    approve: bool,
    source: str,
    now_utc: Optional[datetime] = None,
) -> tuple[Optional[UsageHold], bool]:
    """Count (approve) or discard a pending hold. Already-decided holds are left untouched.

    Returns (hold, changed). The caller commits.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    new_status = "approved" if approve else "discarded"
    # Claim the hold atomically so a double click can never count it twice.
    claimed = db.execute(
        update(UsageHold)
        .where(UsageHold.id == hold_id, UsageHold.status == "pending")
        .values(status=new_status, decided_at=_naive_utc(now_utc))
        .execution_options(synchronize_session=False)
    )
    hold = db.get(UsageHold, hold_id, populate_existing=True)
    if hold is None or claimed.rowcount == 0:
        return hold, False

    peer = db.get(Peer, hold.peer_id)
    name = peer_label(peer, hold.peer_id)
    word = DIRECTION_WORDS.get(hold.direction, hold.direction)
    amount = format_bytes(hold.bytes)
    reason = REASON_LABELS.get(hold.reason, hold.reason)
    if approve:
        _adjust_rollups(
            db,
            hold.peer_id,
            hold.direction,
            parse_minutes_json(hold.minutes_json),
            1,
            minute_floor=_minute_retention_floor(db, now_utc),
        )
        action = "usage_hold_approved"
        note = f"Counted {amount} of held {word} for {name} (hold #{hold.id}, {reason}); added back to usage by {source}."
    else:
        action = "usage_hold_discarded"
        note = f"Discarded {amount} of held {word} for {name} (hold #{hold.id}, {reason}); it stays out of usage. Decided by {source}."
    db.add(Action(peer_id=hold.peer_id, ts=_naive_utc(now_utc), action=action, note=note))
    db.flush()
    return hold, True


def decide_hold_by_id(hold_id: int, *, approve: bool, source: str) -> HoldDecision:
    """Session-owning wrapper for the Telegram buttons; serialised with polling and the guard."""
    if exclusive_operation_gate.is_active():
        return HoldDecision("busy", False, "Usage maintenance is running. Try again when it finishes.")
    with exclusive_operation_gate.coordinated_activity():
        db = SessionLocal()
        try:
            hold, changed = decide_hold(db, hold_id, approve=approve, source=source)
            if hold is None:
                db.rollback()
                return HoldDecision("missing", False, "This hold no longer exists.")
            db.commit()
            if not changed:
                return HoldDecision(hold.status, False, f"Already {hold.status}; nothing changed.")
            name = peer_label(db.get(Peer, hold.peer_id), hold.peer_id)
            word = DIRECTION_WORDS.get(hold.direction, hold.direction)
            amount = format_bytes(hold.bytes)
            if approve:
                return HoldDecision("approved", True, f"Counted: {amount} of {word} added back to {name}'s usage.")
            return HoldDecision("discarded", True, f"Discarded: {amount} of {word} stays out of {name}'s usage.")
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def _admin_chat_id() -> Optional[int]:
    db = SessionLocal()
    try:
        kv = db.get(SettingsKV, "tg_admin_chat_id")
        if not kv or not str(kv.value or "").strip():
            return None
        return int(str(kv.value).strip())
    except (TypeError, ValueError):
        return None
    finally:
        db.close()


def send_admin_telegram(text: str, *, reply_markup=None) -> bool:
    """Best-effort message to the admin chat. Never raises."""
    try:
        chat_id = _admin_chat_id()
        if chat_id is None:
            return False
        from .telegram.sender import send_message_sync

        send_message_sync(chat_id, text, reply_markup=reply_markup)
        return True
    except Exception as exc:
        logger.warning("Admin Telegram message failed: %s", exc)
        return False


def hold_decision_keyboard(hold_id: int):
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Count it", callback_data=f"hold:approve:{hold_id}"),
                InlineKeyboardButton(text="Discard", callback_data=f"hold:discard:{hold_id}"),
            ]
        ]
    )


def notify_holds_created(notices: Iterable[tuple[int, str]]) -> None:
    """Send one admin alert with Count it / Discard buttons per new hold. Never raises."""
    for hold_id, note in notices:
        try:
            markup = hold_decision_keyboard(hold_id)
        except Exception:
            markup = None
        send_admin_telegram(f"Usage held for review\n\n{note}", reply_markup=markup)
