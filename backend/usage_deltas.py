from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


NEAR_32BIT_COUNTER_BYTES = int(3.5 * 1024 * 1024 * 1024)
LOW_COUNTER_RESET_BYTES = 768 * 1024 * 1024


@dataclass(frozen=True)
class CounterDelta:
    delta: int
    dropped: bool
    near_32bit_drop: bool


@dataclass
class CounterQuarantineState:
    unstable_days: dict[str, set[str]] = field(default_factory=lambda: {"rx": set(), "tx": set()})

    def apply(self, direction: str, result: CounterDelta, day_key: str) -> int:
        if result.near_32bit_drop:
            self.unstable_days.setdefault(direction, set()).add(day_key)
        if day_key in self.unstable_days.setdefault(direction, set()):
            return 0
        return result.delta


def counter_delta(previous: int, current: int) -> CounterDelta:
    previous = int(previous or 0)
    current = int(current or 0)
    dropped = current < previous
    near_32bit_drop = (
        dropped
        and previous >= NEAR_32BIT_COUNTER_BYTES
        and current <= LOW_COUNTER_RESET_BYTES
    )
    return CounterDelta(
        delta=0 if dropped else current - previous,
        dropped=dropped,
        near_32bit_drop=near_32bit_drop,
    )


def counter_day_key(ts_utc: datetime, tz: ZoneInfo) -> str:
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)
    else:
        ts_utc = ts_utc.astimezone(timezone.utc)
    return ts_utc.astimezone(tz).date().strftime("%Y-%m-%d")


def near_32bit_drop_sql(current_col: str, previous_col: str) -> str:
    return (
        f"CASE WHEN {previous_col} IS NOT NULL "
        f"AND {current_col} < {previous_col} "
        f"AND {previous_col} >= {NEAR_32BIT_COUNTER_BYTES} "
        f"AND {current_col} <= {LOW_COUNTER_RESET_BYTES} "
        "THEN 1 ELSE 0 END"
    )


def guarded_delta_sql(current_col: str, previous_col: str, unstable_col: str) -> str:
    return (
        f"CASE WHEN {previous_col} IS NULL THEN 0 "
        f"WHEN {unstable_col} > 0 THEN 0 "
        f"WHEN {current_col} < {previous_col} THEN 0 "
        f"ELSE {current_col} - {previous_col} END"
    )
