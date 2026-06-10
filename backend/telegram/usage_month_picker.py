"""Telegram month picker: which panel-calendar months have UsageDaily data for peers."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from sqlalchemy.orm import Session

from ..calendar_utils import (
    DATE_CALENDAR_PERSIAN,
    normalize_date_calendar,
    PERSIAN_MONTH_NAMES,
    selected_calendar_date_parts,
)
from ..models import UsageDaily

GREGORIAN_MONTH_SHORT = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]

# Telegram callback_data max length
YEARS_PER_PAGE = 6


def distinct_calendar_months_with_usage(
    db: Session, peer_ids: Sequence[int], calendar: str | None
) -> list[tuple[int, int]]:
    """
    Union of (panel_calendar_year, panel_calendar_month) for any day with rx+tx > 0
    for the given peers. Months sorted newest first (by year desc, month desc).
    """
    ids = [int(p) for p in peer_ids if p is not None]
    if not ids:
        return []
    cal = normalize_date_calendar(calendar)
    q = (
        db.query(UsageDaily.day)
        .filter(
            UsageDaily.peer_id.in_(ids),
            (UsageDaily.rx + UsageDaily.tx) > 0,
        )
        .distinct()
    )
    months: set[tuple[int, int]] = set()
    for (day_str,) in q.all():
        if not day_str:
            continue
        parts = day_str.split("-")
        if len(parts) != 3:
            continue
        try:
            y, mo, da = int(parts[0]), int(parts[1]), int(parts[2])
        except (TypeError, ValueError):
            continue
        try:
            d = date(y, mo, da)
        except ValueError:
            continue
        cy, cm, _ = selected_calendar_date_parts(d, cal)
        months.add((cy, cm))
    return sorted(months, key=lambda t: (t[0], t[1]), reverse=True)


def format_picker_month_button(cal_year: int, cal_month: int, calendar: str | None) -> str:
    """Short label for an inline month button (Telegram ~64 char limit per button text)."""
    cal = normalize_date_calendar(calendar)
    m = max(1, min(12, cal_month))
    if cal == DATE_CALENDAR_PERSIAN:
        return f"{PERSIAN_MONTH_NAMES[m - 1][:4]} {cal_year}"
    return f"{GREGORIAN_MONTH_SHORT[m - 1]} {cal_year}"


def format_picker_month_scope_label(cal_year: int, cal_month: int, calendar: str | None) -> str:
    """Caption / scope line for charts (full month name)."""
    cal = normalize_date_calendar(calendar)
    m = max(1, min(12, cal_month))
    if cal == DATE_CALENDAR_PERSIAN:
        return f"{PERSIAN_MONTH_NAMES[m - 1]} {cal_year}"
    return f"{GREGORIAN_MONTH_SHORT[m - 1]} {cal_year}"
