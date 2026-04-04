"""Fair usage period usage totals in app timezone (settings.timezone).

Period = scope_period_count × scope_period_unit (hour | day | week | month).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from calendar import monthrange
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import func

from .models import UsageMinute, SettingsKV
from .settings import settings
from .usage_bucketing import local_bucket_start_utc_naive
from .usage_storage import floor_to_minute_utc

if TYPE_CHECKING:
    from .models import FairUsageRule


def app_zoneinfo() -> ZoneInfo:
    try:
        return ZoneInfo((settings.timezone or "UTC").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


SCOPE_UNIT_MAX: dict[str, int] = {"hour": 168, "day": 90, "week": 52, "month": 24}


def normalize_scope_period(rule: "FairUsageRule") -> tuple[int, str]:
    cnt = max(1, int(getattr(rule, "scope_period_count", None) or 1))
    unit = (getattr(rule, "scope_period_unit", None) or "month").lower().strip()
    if unit not in ("hour", "day", "week", "month"):
        unit = "month"
    cap = SCOPE_UNIT_MAX.get(unit, 24)
    cnt = min(cnt, cap)
    return cnt, unit


def format_scope_label(count: int, unit: str) -> str:
    u = unit.lower()
    if count == 1:
        return {"hour": "Hourly", "day": "Daily", "week": "Weekly", "month": "Monthly"}.get(u, "Monthly")
    plural = {"hour": "hours", "day": "days", "week": "weeks", "month": "months"}.get(u, u + "s")
    return f"{count} {plural}"


def sync_legacy_time_scope_field(rule: "FairUsageRule") -> None:
    """Keep legacy time_scope column in sync for DB readers / short labels."""
    cnt, unit = normalize_scope_period(rule)
    rule.scope_period_count = cnt
    rule.scope_period_unit = unit
    if cnt == 1:
        rule.time_scope = {"hour": "hourly", "day": "daily", "week": "weekly", "month": "monthly"}.get(unit, "monthly")
    else:
        suf = {"hour": "h", "day": "d", "week": "w", "month": "mo"}.get(unit, "mo")
        rule.time_scope = f"{cnt}{suf}"[:16]


def get_week_start_day(db: Session) -> int:
    kv = db.get(SettingsKV, "week_start_day")
    if kv:
        try:
            return int(kv.value)
        except ValueError:
            pass
    return 0


def add_calendar_months(d: date, delta: int) -> date:
    if delta == 0:
        return d
    y, m = d.year, d.month
    m += delta
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    last = monthrange(y, m)[1]
    day = min(d.day, last)
    return date(y, m, day)


def _sum_usage_minute_range(
    peer_id: int, db: Session, start_naive: datetime, end_naive: datetime
) -> tuple[int, int]:
    row = (
        db.query(
            func.coalesce(func.sum(UsageMinute.rx), 0),
            func.coalesce(func.sum(UsageMinute.tx), 0),
        )
        .filter(
            UsageMinute.peer_id == peer_id,
            UsageMinute.minute_ts >= start_naive,
            UsageMinute.minute_ts <= end_naive,
        )
        .one()
    )
    return int(row[0]), int(row[1])


def peer_scope_usage_for_rule(
    peer_id: int,
    rule: "FairUsageRule",
    db: Session,
    now_utc: datetime | None = None,
) -> tuple[int, int]:
    """Used RX/TX for the fair-usage period (minute rollups, local calendar alignment)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    cnt, unit = normalize_scope_period(rule)
    tz = app_zoneinfo()
    nu = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    end_naive = floor_to_minute_utc(nu)

    if unit == "hour":
        interval = cnt * 3600
        start_naive = local_bucket_start_utc_naive(nu, interval, tz)
        return _sum_usage_minute_range(peer_id, db, start_naive, end_naive)

    if unit == "day":
        now_local = now_utc.astimezone(tz)
        today = now_local.date()
        start_date = today - timedelta(days=cnt - 1)
        start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
        start_naive = start_local.astimezone(timezone.utc).replace(tzinfo=None)
        return _sum_usage_minute_range(peer_id, db, start_naive, end_naive)

    if unit == "week":
        now_local = now_utc.astimezone(tz)
        today = now_local.date()
        wsd = get_week_start_day(db)
        dow = today.weekday()
        days_since = (dow - wsd) % 7
        week_start = today - timedelta(days=days_since)
        start_date = week_start - timedelta(days=7 * (cnt - 1))
        start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
        start_naive = start_local.astimezone(timezone.utc).replace(tzinfo=None)
        return _sum_usage_minute_range(peer_id, db, start_naive, end_naive)

    now_local = now_utc.astimezone(tz)
    today = now_local.date()
    first_this = today.replace(day=1)
    start_date = add_calendar_months(first_this, -(cnt - 1))
    start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
    start_naive = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    return _sum_usage_minute_range(peer_id, db, start_naive, end_naive)


def compute_next_reset_utc_for_rule(
    rule: "FairUsageRule",
    db: Session,
    now_utc: datetime | None = None,
) -> datetime:
    """When the current scope period ends (timezone-aware UTC)."""
    now_utc = now_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    cnt, unit = normalize_scope_period(rule)
    tz = app_zoneinfo()
    nu = now_utc.astimezone(timezone.utc).replace(tzinfo=None)

    if unit == "hour":
        interval = cnt * 3600
        b_naive = local_bucket_start_utc_naive(nu, interval, tz)
        b_loc = b_naive.replace(tzinfo=timezone.utc).astimezone(tz)
        nxt = b_loc + timedelta(hours=cnt)
        return nxt.astimezone(timezone.utc)

    if unit == "day":
        now_local = now_utc.astimezone(tz)
        today = now_local.date()
        start_date = today - timedelta(days=cnt - 1)
        start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
        nxt = start_local + timedelta(days=cnt)
        return nxt.astimezone(timezone.utc)

    if unit == "week":
        now_local = now_utc.astimezone(tz)
        today = now_local.date()
        wsd = get_week_start_day(db)
        dow = today.weekday()
        days_since = (dow - wsd) % 7
        week_start = today - timedelta(days=days_since)
        start_date = week_start - timedelta(days=7 * (cnt - 1))
        start_local = datetime.combine(start_date, datetime.min.time()).replace(tzinfo=tz)
        nxt = start_local + timedelta(weeks=cnt)
        return nxt.astimezone(timezone.utc)

    now_local = now_utc.astimezone(tz)
    today = now_local.date()
    first_this = today.replace(day=1)
    start_date = add_calendar_months(first_this, -(cnt - 1))
    next_start_date = add_calendar_months(start_date, cnt)
    nxt = datetime.combine(next_start_date, datetime.min.time()).replace(tzinfo=tz)
    return nxt.astimezone(timezone.utc)


def peer_scope_usage_bytes(
    peer_id: int,
    time_scope: str,
    db: Session,
    now_utc: datetime | None = None,
) -> tuple[int, int]:
    """Backward-compatible bridge: legacy time_scope string → synthetic rule."""
    from .models import FairUsageRule

    r = FairUsageRule()
    r.time_scope = time_scope
    m = {"hourly": ("hour", 1), "daily": ("day", 1), "weekly": ("week", 1), "monthly": ("month", 1)}
    unit, cnt = m.get(time_scope, ("month", 1))
    r.scope_period_unit = unit
    r.scope_period_count = cnt
    return peer_scope_usage_for_rule(peer_id, r, db, now_utc)
