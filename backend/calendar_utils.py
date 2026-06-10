from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from .settings import settings

DATE_CALENDAR_GREGORIAN = "gregorian"
DATE_CALENDAR_PERSIAN = "persian"
DATE_CALENDARS = {DATE_CALENDAR_GREGORIAN, DATE_CALENDAR_PERSIAN}

PERSIAN_MONTH_NAMES = [
    "Farvardin",
    "Ordibehesht",
    "Khordad",
    "Tir",
    "Mordad",
    "Shahrivar",
    "Mehr",
    "Aban",
    "Azar",
    "Dey",
    "Bahman",
    "Esfand",
]


def normalize_date_calendar(value: str | None) -> str:
    value = (value or DATE_CALENDAR_GREGORIAN).strip().lower()
    return value if value in DATE_CALENDARS else DATE_CALENDAR_GREGORIAN


def app_date_calendar() -> str:
    return normalize_date_calendar(getattr(settings, "date_calendar", DATE_CALENDAR_GREGORIAN))


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

    gy -= 1600
    gm -= 1
    gd -= 1

    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm):
        g_day_no += g_days_in_month[i]
    if gm > 1 and ((gy + 1600) % 4 == 0 and ((gy + 1600) % 100 != 0 or (gy + 1600) % 400 == 0)):
        g_day_no += 1
    g_day_no += gd

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    jm = 0
    while jm < 11 and j_day_no >= j_days_in_month[jm]:
        j_day_no -= j_days_in_month[jm]
        jm += 1
    return jy, jm + 1, j_day_no + 1


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]

    jy -= 979
    jm -= 1
    jd -= 1

    j_day_no = 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4
    for i in range(jm):
        j_day_no += j_days_in_month[i]
    j_day_no += jd

    g_day_no = j_day_no + 79
    gy = 1600 + 400 * (g_day_no // 146097)
    g_day_no %= 146097

    leap = True
    if g_day_no >= 36525:
        g_day_no -= 1
        gy += 100 * (g_day_no // 36524)
        g_day_no %= 36524
        if g_day_no >= 365:
            g_day_no += 1
        else:
            leap = False

    gy += 4 * (g_day_no // 1461)
    g_day_no %= 1461

    if g_day_no >= 366:
        leap = False
        g_day_no -= 1
        gy += g_day_no // 365
        g_day_no %= 365

    gm = 0
    while gm < 11:
        dim = g_days_in_month[gm] + (1 if gm == 1 and leap else 0)
        if g_day_no < dim:
            break
        g_day_no -= dim
        gm += 1
    return gy, gm + 1, g_day_no + 1


def is_jalali_leap_year(jy: int) -> bool:
    gy, gm, gd = jalali_to_gregorian(jy, 1, 1)
    ny, nm, nd = jalali_to_gregorian(jy + 1, 1, 1)
    return (date(ny, nm, nd) - date(gy, gm, gd)).days == 366


def days_in_selected_month(year: int, month: int, calendar: str | None = None) -> int:
    calendar = normalize_date_calendar(calendar)
    if calendar == DATE_CALENDAR_PERSIAN:
        if month <= 6:
            return 31
        if month <= 11:
            return 30
        return 30 if is_jalali_leap_year(year) else 29
    return monthrange(year, month)[1]


def selected_calendar_month_start(local_date: date, calendar: str | None = None) -> date:
    calendar = normalize_date_calendar(calendar)
    if calendar == DATE_CALENDAR_PERSIAN:
        jy, jm, _ = gregorian_to_jalali(local_date.year, local_date.month, local_date.day)
        gy, gm, gd = jalali_to_gregorian(jy, jm, 1)
        return date(gy, gm, gd)
    return local_date.replace(day=1)


def add_selected_calendar_months(local_month_start: date, delta: int, calendar: str | None = None) -> date:
    calendar = normalize_date_calendar(calendar)
    if calendar == DATE_CALENDAR_PERSIAN:
        jy, jm, _ = gregorian_to_jalali(local_month_start.year, local_month_start.month, local_month_start.day)
        jm += delta
        jy += (jm - 1) // 12
        jm = (jm - 1) % 12 + 1
        gy, gm, gd = jalali_to_gregorian(jy, jm, 1)
        return date(gy, gm, gd)

    y, m = local_month_start.year, local_month_start.month + delta
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


def selected_calendar_date_parts(local_date: date, calendar: str | None = None) -> tuple[int, int, int]:
    calendar = normalize_date_calendar(calendar)
    if calendar == DATE_CALENDAR_PERSIAN:
        return gregorian_to_jalali(local_date.year, local_date.month, local_date.day)
    return local_date.year, local_date.month, local_date.day


def selected_calendar_to_gregorian_date(year: int, month: int, day: int, calendar: str | None = None) -> date:
    calendar = normalize_date_calendar(calendar)
    day = max(1, min(day, days_in_selected_month(year, month, calendar)))
    if calendar == DATE_CALENDAR_PERSIAN:
        gy, gm, gd = jalali_to_gregorian(year, month, day)
        return date(gy, gm, gd)
    return date(year, month, day)


def add_selected_calendar_months_to_parts(year: int, month: int, delta: int) -> tuple[int, int]:
    month += delta
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return year, month


def selected_month_cycle_bounds_utc(
    now_utc: datetime,
    tz: ZoneInfo,
    calendar: str | None = None,
    *,
    reset_day: int = 1,
    count: int = 1,
) -> tuple[datetime, datetime]:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    calendar = normalize_date_calendar(calendar)
    reset_day = max(1, min(31, int(reset_day or 1)))
    count = max(1, int(count or 1))
    now_local = now_utc.astimezone(tz)
    sy, sm, _sd = selected_calendar_date_parts(now_local.date(), calendar)

    current_reset_date = selected_calendar_to_gregorian_date(sy, sm, reset_day, calendar)
    if now_local.date() < current_reset_date:
        start_y, start_m = add_selected_calendar_months_to_parts(sy, sm, -1)
    else:
        start_y, start_m = sy, sm

    start_y, start_m = add_selected_calendar_months_to_parts(start_y, start_m, -(count - 1))
    end_y, end_m = add_selected_calendar_months_to_parts(start_y, start_m, count)
    start_date = selected_calendar_to_gregorian_date(start_y, start_m, reset_day, calendar)
    end_date = selected_calendar_to_gregorian_date(end_y, end_m, reset_day, calendar)
    start_local = datetime.combine(start_date, time.min).replace(tzinfo=tz)
    end_local = datetime.combine(end_date, time.min).replace(tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def selected_month_bounds_utc(
    now_utc: datetime,
    tz: ZoneInfo,
    calendar: str | None = None,
    *,
    count: int = 1,
) -> tuple[datetime, datetime]:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    calendar = normalize_date_calendar(calendar)
    count = max(1, int(count or 1))
    now_local = now_utc.astimezone(tz)
    this_start = selected_calendar_month_start(now_local.date(), calendar)
    start_date = add_selected_calendar_months(this_start, -(count - 1), calendar)
    end_date = add_selected_calendar_months(start_date, count, calendar)
    start_local = datetime.combine(start_date, time.min).replace(tzinfo=tz)
    end_local = datetime.combine(end_date, time.min).replace(tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def selected_calendar_month_bounds_utc(
    calendar_year: int,
    calendar_month: int,
    tz: ZoneInfo,
    calendar: str | None = None,
) -> tuple[datetime, datetime]:
    """
    UTC [start, end) for the given panel-calendar month (Gregorian or Persian),
    using local midnight boundaries in ``tz`` — same geometry as ``selected_month_bounds_utc``.
    """
    calendar = normalize_date_calendar(calendar)
    start_date = selected_calendar_to_gregorian_date(calendar_year, calendar_month, 1, calendar)
    end_date = add_selected_calendar_months(start_date, 1, calendar)
    start_local = datetime.combine(start_date, time.min).replace(tzinfo=tz)
    end_local = datetime.combine(end_date, time.min).replace(tzinfo=tz)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def format_app_datetime(dt_utc: datetime, *, include_time: bool = True, calendar: str | None = None) -> str:
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo((settings.timezone or "UTC").strip() or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    local = dt_utc.astimezone(tz)
    calendar = normalize_date_calendar(calendar or app_date_calendar())
    if calendar == DATE_CALENDAR_PERSIAN:
        jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
        base = f"{PERSIAN_MONTH_NAMES[jm - 1]} {jd}, {jy}"
    else:
        base = local.strftime("%Y-%m-%d")
    return f"{base} {local.strftime('%H:%M')}" if include_time else base
