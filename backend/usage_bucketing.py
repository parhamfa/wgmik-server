"""Chart bucket alignment in the app timezone (settings.timezone via caller-provided ZoneInfo).

Buckets are [local midnight + k * interval, ...) wall-clock windows, not UTC epoch multiples.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def local_bucket_start_utc_naive(ts_utc_naive: datetime, interval: int, tz: ZoneInfo) -> datetime:
    """Start of the local wall-clock window of length `interval` seconds containing `ts_utc_naive`."""
    if interval <= 0:
        raise ValueError("interval must be positive")
    t = ts_utc_naive.replace(tzinfo=timezone.utc).astimezone(tz)
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    sec = int((t - day_start).total_seconds())
    bucket_sec = (sec // interval) * interval
    start_local = day_start + timedelta(seconds=bucket_sec)
    return start_local.astimezone(timezone.utc).replace(tzinfo=None)


def aggregate_rows_to_local_buckets(
    rows: list[tuple[datetime, int, int]],
    interval: int,
    tz: ZoneInfo,
) -> list[tuple[datetime, int, int]]:
    """Sum (ts, rx, tx) rows into local-aligned buckets; sorted by bucket start (naive UTC)."""
    buckets: dict[datetime, list[int]] = {}
    for ts_naive, rx, tx in rows:
        b = local_bucket_start_utc_naive(ts_naive, interval, tz)
        acc = buckets.setdefault(b, [0, 0])
        acc[0] += int(rx or 0)
        acc[1] += int(tx or 0)
    return sorted(((b, acc[0], acc[1]) for b, acc in buckets.items()), key=lambda x: x[0])


def aggregate_router_rows_to_local_buckets(
    rows: list[tuple[int, datetime, int, int]],
    interval: int,
    tz: ZoneInfo,
) -> list[tuple[int, datetime, int, int]]:
    """Per-router rows summed into local buckets; sorted by router_id, bucket."""
    buckets: dict[tuple[int, datetime], list[int]] = defaultdict(lambda: [0, 0])
    for router_id, ts_naive, rx, tx in rows:
        b = local_bucket_start_utc_naive(ts_naive, interval, tz)
        key = (router_id, b)
        acc = buckets[key]
        acc[0] += int(rx or 0)
        acc[1] += int(tx or 0)
    return sorted(
        ((rid, b, acc[0], acc[1]) for (rid, b), acc in buckets.items()),
        key=lambda x: (x[0], x[1]),
    )
