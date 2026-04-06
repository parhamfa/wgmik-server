"""Render fair-usage peer status as a PNG by screenshotting the real web UI via Playwright."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from ..fair_usage_peer_status_dto import FairUsagePeerStatusDTO
from .screenshot_config import SCREENSHOT_DEVICE_SCALE_FACTOR

logger = logging.getLogger("wgmik.telegram.fu_image")

_FRONTEND_ORIGIN = "http://web"


async def render_fair_usage_peer_card_png(
    dto: FairUsagePeerStatusDTO,
    peer_label: str,
    lang: str = "en",
) -> bytes:
    """Screenshot the /render/fair-usage page with the DTO baked into the URL hash."""
    from playwright.async_api import async_playwright

    payload = json.dumps({"status": dto.model_dump(), "peerName": peer_label}, separators=(",", ":"))
    fragment = quote(payload, safe="")
    url = f"{_FRONTEND_ORIGIN}/render/fair-usage#{fragment}"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport={"width": 600, "height": 800},
            device_scale_factor=SCREENSHOT_DEVICE_SCALE_FACTOR,
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="networkidle")
            card = page.locator("#fu-card")
            await card.wait_for(state="visible", timeout=8000)
            png = await card.screenshot(type="png", scale="device")
        finally:
            await context.close()
            await browser.close()

    return png
