"""Screenshot usage charts (same Recharts as the web peer page) for Telegram."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import quote

from sqlalchemy.orm import Session

from ..api.routes import compute_peer_usage_points
from ..fair_usage_usage import app_zoneinfo
from .screenshot_config import SCREENSHOT_DEVICE_SCALE_FACTOR

logger = logging.getLogger("wgmik.telegram.usage_chart")

_FRONTEND_ORIGIN = "http://web"


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
    Map bot scope (today / week / month) to the same usage series as the web chart.

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
    if scope == "week":
        end = now_utc
        start = end - timedelta(days=7)
        pts = compute_peer_usage_points(
            db, peer_id, "daily", start=start, end=end, all_time=False
        )
        return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "days")
    if scope == "month":
        end = now_utc
        start = end - timedelta(days=31)
        pts = compute_peer_usage_points(
            db, peer_id, "daily", start=start, end=end, all_time=False
        )
        return ([{"day": p.day, "rx": p.rx, "tx": p.tx} for p in pts], "days")
    return ([], "days")


async def render_usage_chart_png(payload: dict[str, Any]) -> bytes:
    """Screenshot ``/render/usage-chart`` with JSON in the URL hash."""
    from playwright.async_api import async_playwright

    raw = json.dumps(payload, separators=(",", ":"))
    fragment = quote(raw, safe="")
    url = f"{_FRONTEND_ORIGIN}/render/usage-chart#{fragment}"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 720, "height": 900},
            device_scale_factor=SCREENSHOT_DEVICE_SCALE_FACTOR,
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            card = page.locator("#usage-chart-card")
            await card.wait_for(state="visible", timeout=15000)
            # Recharts + ResponsiveContainer need a tick to finish layout after paint.
            await asyncio.sleep(0.5)
            png = await card.screenshot(type="png", scale="device")
        finally:
            await context.close()
            await browser.close()

    return png
