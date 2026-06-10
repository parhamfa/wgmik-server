"""Render usage charts (same style as the web peer page) as SVG -> PNG for Telegram."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from ..api.routes import compute_peer_usage_points
from ..fair_usage_usage import app_zoneinfo
from ..calendar_utils import (
    DATE_CALENDAR_PERSIAN,
    app_date_calendar,
    gregorian_to_jalali,
    normalize_date_calendar,
    selected_calendar_month_bounds_utc,
    selected_month_bounds_utc,
)
from .svg_render import (
    COLORS,
    fmt_bytes,
    monotone_path,
    nice_ticks,
    render_svg_to_png,
    svg_document,
    svg_text,
    text_width,
    truncate_to_width,
)

logger = logging.getLogger("wgmik.telegram.usage_chart")


def _calendar_day_start_utc(now_utc: datetime) -> datetime:
    """Start of the calendar day in ``settings.timezone`` (via ``app_zoneinfo()``), as UTC."""
    tz = app_zoneinfo()
    now_local = now_utc.astimezone(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc)


def usage_points_for_tg_menu(
    db: Session,
    peer_id: int,
    scope: str,
    now_utc: datetime | None = None,
) -> tuple[list[dict[str, Any]], Literal["days", "raw"]]:
    """
    Map bot scope (today / month / alltime) to the same usage series as the web chart.

    ``today`` is midnight → now in the app timezone (not rolling last 24 hours).
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    if scope == "today":
        day_start = _calendar_day_start_utc(now_utc)
        pts = compute_peer_usage_points(
            db,
            peer_id,
            "raw",
            start=day_start,
            end=now_utc,
            interval=3600,
        )
        return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "raw")
    if scope == "month":
        start, end = selected_month_bounds_utc(now_utc, app_zoneinfo(), app_date_calendar())
        end = min(end, now_utc)
        pts = compute_peer_usage_points(
            db, peer_id, "daily", start=start, end=end, all_time=False
        )
        return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "days")
    if scope == "alltime":
        pts = compute_peer_usage_points(db, peer_id, "daily", all_time=True)
        return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "days")
    return ([], "days")


def merge_usage_points(series: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Sum several per-peer point lists into one aggregate series, bucket by bucket."""
    merged: dict[str, list[int]] = {}
    for points in series:
        for p in points:
            bucket = merged.setdefault(str(p.get("day")), [0, 0])
            bucket[0] += int(p.get("rx") or 0)
            bucket[1] += int(p.get("tx") or 0)
    return [{"day": day, "rx": rx, "tx": tx} for day, (rx, tx) in sorted(merged.items())]


def usage_points_for_week(
    db: Session,
    peer_id: int,
    now_utc: datetime | None = None,
) -> tuple[list[dict[str, Any]], Literal["days"]]:
    """Daily usage points for the current week (honors the week_start_day setting)."""
    from datetime import time, timedelta

    from ..fair_usage_usage import get_week_start_day

    now_utc = now_utc or datetime.now(timezone.utc)
    tz = app_zoneinfo()
    today = now_utc.astimezone(tz).date()
    days_since = (today.weekday() - get_week_start_day(db)) % 7
    week_start = today - timedelta(days=days_since)
    start_local = datetime.combine(week_start, time.min).replace(tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc)
    pts = compute_peer_usage_points(
        db, peer_id, "daily", start=start_utc, end=now_utc, all_time=False
    )
    return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "days")


def usage_points_for_selected_calendar_month(
    db: Session,
    peer_id: int,
    cal_year: int,
    cal_month: int,
    now_utc: datetime | None = None,
) -> tuple[list[dict[str, Any]], Literal["days"]]:
    """Daily usage points for one panel-calendar month (Gregorian or Persian), capped at now."""
    now_utc = now_utc or datetime.now(timezone.utc)
    start_utc, end_utc = selected_calendar_month_bounds_utc(
        cal_year, cal_month, app_zoneinfo(), app_date_calendar()
    )
    end = min(end_utc, now_utc)
    pts = compute_peer_usage_points(
        db, peer_id, "daily", start=start_utc, end=end, all_time=False
    )
    return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "days")


# Card geometry mirroring the former UsageChartRender.tsx (p-5 card, 560px chart, h-56).
_CARD_W = 600.0
_PAD = 20.0
_CHART_W = 560.0
_CHART_H = 224.0
_Y_AXIS_W = 60.0
_X_AXIS_H = 30.0
_MARGIN_TOP = 10.0
_MARGIN_RIGHT = 10.0


def _tz(payload_tz: str | None) -> ZoneInfo:
    try:
        return ZoneInfo((payload_tz or "UTC").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _day_label(value: str, tz: ZoneInfo, calendar: str) -> str:
    """Match formatCalendarDayLabel(short): 'M/D' (Gregorian) or 'jm/jd' (Persian)."""
    try:
        d = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return value
    local = d.astimezone(tz)
    if normalize_date_calendar(calendar) == DATE_CALENDAR_PERSIAN:
        _jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
        return f"{jm}/{jd}"
    return f"{local.month}/{local.day}"


def _time_label(value: str, tz: ZoneInfo) -> str:
    try:
        d = datetime.fromisoformat(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(tz).strftime("%H:%M")
    except Exception:
        return value


def build_usage_chart_svg(payload: dict[str, Any]) -> str:
    points: list[dict[str, Any]] = payload.get("points") or []
    mode = payload.get("mode") or "days"
    tz = _tz(payload.get("timezone"))
    calendar = payload.get("dateCalendar") or "gregorian"

    body: list[str] = []
    y = _PAD

    peer_name = truncate_to_width(str(payload.get("peerName") or ""), 14, _CHART_W, weight=500)
    body.append(svg_text(_PAD, y + 12, peer_name, size=14, color=COLORS["gray-800"], weight=500))
    y += 18 + 4
    body.append(svg_text(_PAD, y + 10, str(payload.get("scopeLabel") or ""), size=12, color=COLORS["gray-500"]))
    y += 16 + 12

    chart_x = _PAD
    chart_y = y
    if not points:
        body.append(svg_text(
            chart_x + _CHART_W / 2, chart_y + _CHART_H / 2 + 5,
            "No data", size=14, color=COLORS["gray-500"], anchor="middle",
        ))
        y += _CHART_H
        return svg_document(_CARD_W, y + _PAD, "".join(body))

    plot_l = chart_x + _Y_AXIS_W
    plot_r = chart_x + _CHART_W - _MARGIN_RIGHT
    plot_t = chart_y + _MARGIN_TOP
    plot_b = chart_y + _CHART_H - _X_AXIS_H
    plot_w = plot_r - plot_l
    plot_h = plot_b - plot_t

    rx_vals = [max(0, int(p.get("rx") or 0)) for p in points]
    tx_vals = [max(0, int(p.get("tx") or 0)) for p in points]
    mib = 1024 * 1024
    # Ticks computed in MiB so the axis labels come out round.
    ticks = [t * mib for t in nice_ticks(max(max(rx_vals), max(tx_vals), 1) / mib)]
    y_max = ticks[-1]

    def sx(i: int) -> float:
        if len(points) == 1:
            return plot_l + plot_w / 2
        return plot_l + plot_w * i / (len(points) - 1)

    def sy(v: float) -> float:
        return plot_b - (v / y_max) * plot_h

    # Gradient fills (25% -> 0 opacity), same as the recharts defs.
    body.append(
        '<defs>'
        '<linearGradient id="tg-g2" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{COLORS["chart-fill-1"]}" stop-opacity="0.25"/>'
        f'<stop offset="100%" stop-color="{COLORS["chart-fill-1"]}" stop-opacity="0"/>'
        '</linearGradient>'
        '<linearGradient id="tg-g3" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{COLORS["chart-fill-2"]}" stop-opacity="0.25"/>'
        f'<stop offset="100%" stop-color="{COLORS["chart-fill-2"]}" stop-opacity="0"/>'
        '</linearGradient>'
        '</defs>'
    )

    # Horizontal grid + Y tick labels ("N MB").
    for tick in ticks:
        ty = sy(tick)
        body.append(
            f'<line x1="{plot_l:.1f}" y1="{ty:.1f}" x2="{plot_r:.1f}" y2="{ty:.1f}" '
            f'stroke="{COLORS["chart-grid"]}" stroke-width="1"/>'
        )
        label = f"{tick / mib:.0f} MB"
        body.append(svg_text(plot_l - 8, ty + 4, label, size=12, color=COLORS["chart-tick"], anchor="end"))

    # X tick labels (skip to ~8 max, always keep the last one).
    n = len(points)
    step = max(1, -(-n // 8))  # ceil
    shown = sorted({*range(0, n, step), n - 1})
    for i in shown:
        raw = str(points[i].get("day") or "")
        label = _day_label(raw, tz, calendar) if mode == "days" else _time_label(raw, tz)
        body.append(svg_text(sx(i), plot_b + 16, label, size=12, color=COLORS["chart-tick"], anchor="middle"))

    # Areas: tx first, rx on top (same draw order as the React chart).
    for key, vals, stroke, grad in (
        ("tx", tx_vals, COLORS["chart-line-1"], "tg-g2"),
        ("rx", rx_vals, COLORS["chart-line-2"], "tg-g3"),
    ):
        pts = [(sx(i), sy(v)) for i, v in enumerate(vals)]
        line = monotone_path(pts)
        if not line:
            continue
        area = f"{line}L{pts[-1][0]:.2f},{plot_b:.2f}L{pts[0][0]:.2f},{plot_b:.2f}Z"
        body.append(f'<path d="{area}" fill="url(#{grad})" stroke="none"/>')
        body.append(f'<path d="{line}" fill="none" stroke="{stroke}" stroke-width="2"/>')

    y += _CHART_H + 8

    # Totals footer, centered.
    tot_rx = sum(rx_vals)
    tot_tx = sum(tx_vals)
    parts = [("Total Download: ", fmt_bytes(tot_tx)), ("Total Upload: ", fmt_bytes(tot_rx))]
    gap = 24.0
    total_w = sum(text_width(a, 12) + text_width(b, 12, 500) for a, b in parts) + gap
    fx = _PAD + (_CHART_W - total_w) / 2
    baseline = y + 12
    for label, value in parts:
        body.append(svg_text(fx, baseline, label, size=12, color=COLORS["gray-500"]))
        fx += text_width(label, 12)
        body.append(svg_text(fx, baseline, value, size=12, color=COLORS["gray-700"], weight=500))
        fx += text_width(value, 12, 500) + gap
    y += 16

    return svg_document(_CARD_W, y + _PAD, "".join(body))


async def render_usage_chart_png(payload: dict[str, Any]) -> bytes:
    """Render the usage chart card to PNG bytes (SVG via resvg, off the event loop)."""
    svg = build_usage_chart_svg(payload)
    return await asyncio.to_thread(render_svg_to_png, svg)
