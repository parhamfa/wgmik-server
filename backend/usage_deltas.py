from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# Marks databases whose usage is counted with the fresh-start rule below. The
# quarantine repair tool refuses to run unless it sees this exact value.
USAGE_COUNTING_VERSION_KEY = "usage_counting_version"
USAGE_COUNTING_VERSION = "2"


@dataclass(frozen=True)
class CounterDelta:
    delta: int
    dropped: bool


def counter_delta(previous: int, current: int) -> CounterDelta:
    """Bytes between two cumulative counter readings.

    A drop means the counter started over (router reboot, peer disable/enable,
    32-bit wrap). Treat it as a fresh start: the new value is the traffic since
    the restart.
    """
    previous = int(previous or 0)
    current = int(current or 0)
    dropped = current < previous
    return CounterDelta(
        delta=current if dropped else current - previous,
        dropped=dropped,
    )


def counter_day_key(ts_utc: datetime, tz: ZoneInfo) -> str:
    if ts_utc.tzinfo is None:
        ts_utc = ts_utc.replace(tzinfo=timezone.utc)
    else:
        ts_utc = ts_utc.astimezone(timezone.utc)
    return ts_utc.astimezone(tz).date().strftime("%Y-%m-%d")


def fresh_start_delta_sql(current_col: str, previous_col: str) -> str:
    """SQL twin of :func:`counter_delta` for LAG()-based raw sample queries."""
    return (
        f"CASE WHEN {previous_col} IS NULL THEN 0 "
        f"WHEN {current_col} < {previous_col} THEN {current_col} "
        f"ELSE {current_col} - {previous_col} END"
    )
