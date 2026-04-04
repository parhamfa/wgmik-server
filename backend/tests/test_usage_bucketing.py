"""Tests for local wall-clock chart bucketing."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.usage_bucketing import aggregate_rows_to_local_buckets, local_bucket_start_utc_naive


def test_local_hour_bucket_tehran_half_hour_offset():
    """UTC hour boundaries are :30 in Iran; local hour buckets should start at local :00."""
    tehran = ZoneInfo("Asia/Tehran")
    # 12:00 UTC = 15:30 in Tehran (UTC+3:30); local hour started 15:00 Tehran = 11:30 UTC
    ts = datetime(2026, 1, 15, 12, 0, 0)
    start = local_bucket_start_utc_naive(ts, 3600, tehran)
    assert start == datetime(2026, 1, 15, 11, 30, 0)


def test_local_hour_bucket_utc_zone():
    utc = ZoneInfo("UTC")
    ts = datetime(2026, 1, 15, 12, 45, 0)
    start = local_bucket_start_utc_naive(ts, 3600, utc)
    assert start == datetime(2026, 1, 15, 12, 0, 0)


def test_aggregate_minute_rows_hourly_tehran():
    tehran = ZoneInfo("Asia/Tehran")
    # Two UTC minutes in same Tehran hour 15:00–16:00 (11:30–12:30 UTC)
    rows = [
        (datetime(2026, 1, 15, 11, 45, 0), 100, 200),
        (datetime(2026, 1, 15, 12, 15, 0), 50, 50),
    ]
    out = aggregate_rows_to_local_buckets(rows, 3600, tehran)
    assert len(out) == 1
    b, rx, tx = out[0]
    assert b == datetime(2026, 1, 15, 11, 30, 0)
    assert rx == 150
    assert tx == 250


def test_iso_roundtrip_naive_utc():
    b = datetime(2026, 1, 15, 11, 30, 0)
    iso = b.replace(tzinfo=timezone.utc).isoformat()
    assert iso.startswith("2026-01-15T11:30:00")
    assert "+00:00" in iso or "Z" in iso.replace("Z", "+00:00")
