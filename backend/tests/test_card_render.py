"""Tests for the SVG -> PNG Telegram card renderers (no browser involved)."""

import asyncio

from backend.fair_usage_peer_status_dto import (
    FairUsagePeerStatusDTO,
    FairUsageRuleStatusItemDTO,
    FairUsageTierStatusDTO,
)
from backend.telegram.fair_usage_image import (
    build_fair_usage_card_svg,
    render_fair_usage_peer_card_png,
)
from backend.telegram.usage_chart_image import build_usage_chart_svg, render_usage_chart_png

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

GB = 1024**3


def _simple_rule(**overrides) -> FairUsageRuleStatusItemDTO:
    base = dict(
        rule_id=1,
        rule_name="Monthly cap",
        quota_mode="combined",
        download_quota_bytes=100 * GB,
        throttle_download_kbps=2000,
        throttle_upload_kbps=1000,
        scope_label="Monthly",
        scope_type="peer",
        sort_order=0,
        used_rx=42 * GB,
        used_tx=10 * GB,
        next_reset="2026-07-01T00:00:00+00:00",
    )
    base.update(overrides)
    return FairUsageRuleStatusItemDTO(**base)


def _assert_png(png: bytes):
    assert isinstance(png, bytes)
    assert png.startswith(PNG_MAGIC)
    assert len(png) > 2000  # non-trivial image


def test_fair_usage_card_single_rule():
    dto = FairUsagePeerStatusDTO(peer_id=1, rules=[_simple_rule(is_effective=True)])
    svg = build_fair_usage_card_svg(dto, "alice-laptop")
    assert "Fair Usage" in svg and "Monthly cap" in svg and "Resets:" in svg
    _assert_png(asyncio.run(render_fair_usage_peer_card_png(dto, "alice-laptop")))


def test_fair_usage_card_throttled_tiered():
    tiers = [
        FairUsageTierStatusDTO(
            tier_id=1, sort_order=0, threshold_bytes=50 * GB, name="Soft",
            throttle_download_kbps=4000, throttle_upload_kbps=2000, is_active=False,
        ),
        FairUsageTierStatusDTO(
            tier_id=2, sort_order=1, threshold_bytes=100 * GB, name="Hard",
            throttle_download_kbps=1000, throttle_upload_kbps=512, is_active=True,
        ),
    ]
    rule = _simple_rule(
        rule_name="Tiered rule", tiered=True, tiers=tiers,
        used_rx=80 * GB, used_tx=30 * GB, over_quota=True, is_effective=True,
    )
    dto = FairUsagePeerStatusDTO(
        peer_id=1, rules=[rule], rule_id=1, rule_name="Tiered rule",
        throttled=True, throttle_download_kbps=1000, throttle_upload_kbps=512,
    )
    svg = build_fair_usage_card_svg(dto, "bob-phone")
    assert "Throttled" in svg and "Hard" in svg and "active" in svg
    _assert_png(asyncio.run(render_fair_usage_peer_card_png(dto, "bob-phone")))


def test_fair_usage_card_independent_quotas():
    rule = _simple_rule(
        quota_mode="independent",
        upload_quota_bytes=20 * GB,
        used_tx=5 * GB,
        passthrough=True,
    )
    dto = FairUsagePeerStatusDTO(peer_id=1, rules=[rule, _simple_rule(rule_id=2, rule_name="Second rule")])
    svg = build_fair_usage_card_svg(dto, "peer with a fairly long name here")
    assert "Upload" in svg and "(2 rules)" in svg and "Pass" in svg
    _assert_png(asyncio.run(render_fair_usage_peer_card_png(dto, "peer with a fairly long name here")))


def test_fair_usage_card_no_rules():
    dto = FairUsagePeerStatusDTO(peer_id=1)
    _assert_png(asyncio.run(render_fair_usage_peer_card_png(dto, "empty-peer")))


def _chart_payload(mode: str, points) -> dict:
    return {
        "peerName": "alice-laptop",
        "scopeLabel": "This month",
        "mode": mode,
        "timezone": "Asia/Tehran",
        "dateCalendar": "persian",
        "points": points,
    }


def test_usage_chart_days():
    points = [
        {"day": f"2026-06-{d:02d}", "rx": d * 120 * 1024**2, "tx": d * 350 * 1024**2}
        for d in range(1, 11)
    ]
    payload = _chart_payload("days", points)
    svg = build_usage_chart_svg(payload)
    assert "alice-laptop" in svg and "Total Download:" in svg and "MB" in svg
    _assert_png(asyncio.run(render_usage_chart_png(payload)))


def test_usage_chart_raw_hours():
    points = [
        {"day": f"2026-06-10T{h:02d}:00:00+00:00", "rx": h * 30 * 1024**2, "tx": h * 90 * 1024**2}
        for h in range(0, 12)
    ]
    payload = _chart_payload("raw", points)
    payload["dateCalendar"] = "gregorian"
    _assert_png(asyncio.run(render_usage_chart_png(payload)))


def test_usage_chart_empty():
    payload = _chart_payload("days", [])
    svg = build_usage_chart_svg(payload)
    assert "No data" in svg
    _assert_png(asyncio.run(render_usage_chart_png(payload)))


def test_usage_chart_single_point():
    payload = _chart_payload("days", [{"day": "2026-06-10", "rx": 1024**3, "tx": 2 * 1024**3}])
    _assert_png(asyncio.run(render_usage_chart_png(payload)))
