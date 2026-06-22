from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend.calendar_utils import (
    days_in_selected_month,
    gregorian_to_jalali,
    jalali_to_gregorian,
    selected_calendar_month_bounds_utc,
    selected_month_bounds_utc,
    selected_month_cycle_bounds_utc,
    utc_range_to_local_day_bounds,
)


def test_jalali_conversion_known_date():
    assert gregorian_to_jalali(2026, 4, 25) == (1405, 2, 5)
    assert jalali_to_gregorian(1405, 2, 5) == (2026, 4, 25)


def test_jalali_esfand_leap_length():
    assert days_in_selected_month(1403, 12, "persian") == 30
    assert days_in_selected_month(1404, 12, "persian") == 29


def test_selected_persian_month_bounds_in_timezone():
    start, end = selected_month_bounds_utc(
        datetime(2026, 4, 25, tzinfo=timezone.utc),
        ZoneInfo("Asia/Tehran"),
        "persian",
    )
    assert start == datetime(2026, 4, 20, 20, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 5, 21, 20, 30, tzinfo=timezone.utc)


def test_explicit_gregorian_month_bounds_utc():
    start, end = selected_calendar_month_bounds_utc(
        2026, 3, ZoneInfo("UTC"), "gregorian"
    )
    assert start == datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 4, 1, 0, 0, tzinfo=timezone.utc)


def test_explicit_persian_month_bounds_matches_current_month_window():
    """Ordibehesht 1405 from explicit API matches ``selected_month_bounds_utc`` on a date in that month."""
    now = datetime(2026, 4, 25, tzinfo=timezone.utc)
    tehran = ZoneInfo("Asia/Tehran")
    from_bounds, to_bounds = selected_month_bounds_utc(now, tehran, "persian")
    from_explicit, to_explicit = selected_calendar_month_bounds_utc(1405, 2, tehran, "persian")
    assert from_explicit == from_bounds
    assert to_explicit == to_bounds


def test_persian_month_utc_bounds_map_to_local_daily_keys():
    tehran = ZoneInfo("Asia/Tehran")
    start, end = selected_calendar_month_bounds_utc(1405, 2, tehran, "persian")

    start_day, end_day = utc_range_to_local_day_bounds(start, end, tehran)

    assert start_day == "2026-04-21"
    assert end_day == "2026-05-21"


def test_selected_persian_month_cycle_honors_reset_day():
    start, end = selected_month_cycle_bounds_utc(
        datetime(2026, 4, 25, tzinfo=timezone.utc),
        ZoneInfo("Asia/Tehran"),
        "persian",
        reset_day=5,
    )
    assert start == datetime(2026, 4, 24, 20, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 5, 25, 20, 30, tzinfo=timezone.utc)
