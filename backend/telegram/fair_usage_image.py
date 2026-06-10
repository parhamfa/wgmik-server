"""Render the fair-usage peer status card as SVG -> PNG (no browser).

Mirrors the layout of the former frontend/src/pages/FairUsageRender.tsx page.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..calendar_utils import (
    DATE_CALENDAR_PERSIAN,
    PERSIAN_MONTH_NAMES,
    app_date_calendar,
    gregorian_to_jalali,
    normalize_date_calendar,
)
from ..fair_usage_peer_status_dto import FairUsagePeerStatusDTO, FairUsageRuleStatusItemDTO
from ..settings import settings
from .svg_render import (
    COLORS,
    fmt_bytes,
    pill,
    progress_bar,
    render_svg_to_png,
    svg_document,
    svg_text,
    svg_rect,
    text_width,
    truncate_to_width,
)

logger = logging.getLogger("wgmik.telegram.fu_image")

CARD_W = 520.0
PAD = 20.0
CONTENT_W = CARD_W - 2 * PAD
BOX_PAD = 12.0
BOX_W = CONTENT_W
BOX_INNER_W = BOX_W - 2 * BOX_PAD

_SHIELD_PATH = "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"


def _app_tz() -> ZoneInfo:
    try:
        return ZoneInfo((settings.timezone or "UTC").strip() or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _format_reset(iso: str, *, include_time: bool) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except Exception:
        return iso
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_app_tz())
    if normalize_date_calendar(app_date_calendar()) == DATE_CALENDAR_PERSIAN:
        jy, jm, jd = gregorian_to_jalali(local.year, local.month, local.day)
        base = f"{PERSIAN_MONTH_NAMES[jm - 1]} {jd}, {jy}"
    else:
        base = f"{local.strftime('%b')} {local.day}, {local.year}"
    return f"{base}, {local.strftime('%H:%M')}" if include_time else base


def _effective_throttle(fr: FairUsageRuleStatusItemDTO) -> tuple[int, int, str]:
    if fr.tiered and fr.tiers:
        active = next((t for t in fr.tiers if t.is_active), None)
        if active:
            name = (active.name or "").strip()
            label = f"{fr.rule_name} · {name}" if name else fr.rule_name
            return active.throttle_download_kbps, active.throttle_upload_kbps, label
    return fr.throttle_download_kbps, fr.throttle_upload_kbps, fr.rule_name


def _mbps(kbps: int) -> str:
    return f"{kbps / 1000:.1f}"


def _label_value_row(y: float, label: str, value: str, x: float, w: float) -> str:
    return (
        svg_text(x, y + 12, label, size=12, color=COLORS["gray-500"])
        + svg_text(x + w, y + 12, value, size=12, color=COLORS["gray-500"], anchor="end")
    )


def _rule_box(fr: FairUsageRuleStatusItemDTO, x: float, y: float) -> tuple[str, float]:
    """Render one rule box at (x, y); returns (svg, height)."""
    body: list[str] = []
    cy = y + BOX_PAD
    ix = x + BOX_PAD

    # --- Header row: "Rule: <name>" badges left, pills right ---
    pills_svg: list[str] = []
    px = x + BOX_W - BOX_PAD
    pill_specs = [
        ("Pass" if fr.passthrough else "Stop",
         COLORS["amber-50"] if fr.passthrough else COLORS["gray-100"],
         COLORS["amber-700"] if fr.passthrough else COLORS["gray-600"]),
        ((fr.scope_type or "").capitalize() or "—", COLORS["indigo-50"], COLORS["indigo-700"]),
        (fr.scope_label or "", COLORS["gray-100"], COLORS["gray-700"]),
        (f"#{fr.sort_order or 0}", COLORS["gray-100"], COLORS["gray-700"]),
    ]
    for label, bg, fg in pill_specs:
        if not label:
            continue
        s, w = pill(px, cy, label, bg=bg, fg=fg, anchor="end")
        pills_svg.append(s)
        px -= w + 6
    pills_w = (x + BOX_W - BOX_PAD) - px

    text_y = cy + 13
    tx = ix
    prefix = "Rule: "
    body.append(svg_text(tx, text_y, prefix, size=12, color=COLORS["gray-600"]))
    tx += text_width(prefix, 12)
    suffix_w = 0.0
    if fr.over_quota:
        suffix_w += 8 + text_width("Matched", 12)
    if fr.is_effective:
        suffix_w += 8 + text_width("Effective", 12)
    name_max = BOX_INNER_W - pills_w - 16 - (tx - ix) - suffix_w
    name = truncate_to_width(fr.rule_name or "", 12, max(40.0, name_max), weight=500)
    body.append(svg_text(tx, text_y, name, size=12, color=COLORS["gray-900"], weight=500))
    tx += text_width(name, 12, 500)
    if fr.over_quota:
        tx += 8
        body.append(svg_text(tx, text_y, "Matched", size=12, color=COLORS["amber-700"]))
        tx += text_width("Matched", 12)
    if fr.is_effective:
        tx += 8
        body.append(svg_text(tx, text_y, "Effective", size=12, color=COLORS["indigo-700"]))
    body.extend(pills_svg)
    cy += 18 + 8

    # --- Usage bars / tiers ---
    if fr.tiered and fr.tiers:
        used = fr.used_rx + fr.used_tx
        cap = max(max(t.threshold_bytes for t in fr.tiers), 1)
        body.append(_label_value_row(cy, "Combined usage (tiered)",
                                     f"{fmt_bytes(used)} / max {fmt_bytes(cap)}", ix, BOX_INNER_W))
        cy += 16 + 8
        pct = min(100.0, round(used / cap * 100))
        body.append(progress_bar(ix, cy, BOX_INNER_W, pct,
                                 fill=COLORS["amber-500"] if fr.over_quota else COLORS["gray-900"]))
        cy += 8 + 4 + 4  # bar + grid gap + mt-1
        for t in fr.tiers:
            row_h = 26.0
            bg = COLORS["amber-100"] if t.is_active else COLORS["gray-100"]
            fg = COLORS["amber-900"] if t.is_active else COLORS["gray-600"]
            body.append(svg_rect(ix, cy, BOX_INNER_W, row_h, fill=bg, rx=8))
            label = f"≥ {fmt_bytes(t.threshold_bytes)}"
            tname = (t.name or "").strip()
            if tname:
                label += f" · {tname}"
            if t.is_active:
                label += " · active"
            right = f"{_mbps(t.throttle_download_kbps)}/{_mbps(t.throttle_upload_kbps)} Mbps"
            mid = cy + row_h / 2 + 11 * 0.36
            body.append(svg_text(ix + 8, mid, label, size=11, color=fg))
            body.append(svg_text(ix + BOX_INNER_W - 8, mid, right, size=11, color=fg, anchor="end"))
            cy += row_h + 6
        cy -= 6  # no trailing gap after last tier row
        cy += 8
    else:
        used = fr.used_rx + fr.used_tx if fr.quota_mode == "combined" else fr.used_rx
        limit = fr.download_quota_bytes
        pct = min(100.0, round(used / limit * 100)) if limit > 0 else 0.0
        head = "Total usage" if fr.quota_mode == "combined" else "Download"
        body.append(_label_value_row(cy, head, f"{fmt_bytes(used)} / {fmt_bytes(limit)} ({pct:.0f}%)",
                                     ix, BOX_INNER_W))
        cy += 16 + 4
        body.append(progress_bar(ix, cy, BOX_INNER_W, pct,
                                 fill=COLORS["amber-500"] if fr.over_quota else COLORS["gray-900"]))
        cy += 8 + 8

        if fr.quota_mode == "independent" and fr.upload_quota_bytes:
            cy += 4  # gap-3 between the two bars
            up_limit = max(1, fr.upload_quota_bytes)
            up_pct = min(100.0, round(fr.used_tx / up_limit * 100))
            body.append(_label_value_row(
                cy, "Upload",
                f"{fmt_bytes(fr.used_tx)} / {fmt_bytes(fr.upload_quota_bytes)} ({up_pct:.0f}%)",
                ix, BOX_INNER_W))
            cy += 16 + 4
            body.append(progress_bar(ix, cy, BOX_INNER_W, up_pct,
                                     fill=COLORS["amber-500"] if fr.over_quota else COLORS["gray-900"]))
            cy += 8 + 8

    # --- Next reset ---
    if fr.next_reset:
        formatted = _format_reset(fr.next_reset, include_time=fr.scope_period_unit == "hour")
        body.append(svg_text(ix, cy + 12, f"Resets: {formatted}", size=12, color=COLORS["gray-500"]))
        cy += 16 + 8

    cy += BOX_PAD - 8
    height = cy - y
    ring = "#fcd34d99" if fr.over_quota else COLORS["gray-200"]
    bg = "#fef6e7" if fr.over_quota else "#fcfcfd"  # amber-500/5 and gray-50/50 over white
    box = svg_rect(x + 0.5, y + 0.5, BOX_W - 1, height - 1, fill=bg, rx=12, stroke=ring)
    return box + "".join(body), height


def build_fair_usage_card_svg(
    dto: FairUsagePeerStatusDTO,
    peer_label: str,
    lang: str = "en",
) -> str:
    rules = dto.rules or []
    effective = next((r for r in rules if r.is_effective), None)
    if effective is None and dto.rule_id is not None:
        effective = next((r for r in rules if r.rule_id == dto.rule_id), None)

    body: list[str] = []
    y = PAD

    # --- Header ---
    icon_scale = 16 / 24
    body.append(
        f'<g transform="translate({PAD},{y + 1}) scale({icon_scale})">'
        f'<path d="{_SHIELD_PATH}" fill="none" stroke="{COLORS["gray-500"]}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/></g>'
    )
    hx = PAD + 16 + 8
    header_baseline = y + 13
    body.append(svg_text(hx, header_baseline, "Fair Usage", size=14, color=COLORS["gray-700"], weight=500))
    hx += text_width("Fair Usage", 14, 500) + 8
    if len(rules) > 1:
        body.append(svg_text(hx, header_baseline, f"({len(rules)} rules)", size=11, color=COLORS["gray-500"]))

    # Right side: status pill (+ throttle label to its right, so label is rightmost).
    right_x = CARD_W - PAD
    if dto.throttled:
        if effective is not None:
            dl, ul, rule_label = _effective_throttle(effective)
        else:
            dl, ul = dto.throttle_download_kbps, dto.throttle_upload_kbps
            rule_label = dto.rule_name or "Rule"
        throttle_text = f"{rule_label}: {_mbps(dl)}/{_mbps(ul)} Mbps"
        throttle_text = truncate_to_width(throttle_text, 11, CONTENT_W * 0.5)
        body.append(svg_text(right_x, header_baseline, throttle_text, size=11, color=COLORS["amber-700"], anchor="end"))
        right_x -= text_width(throttle_text, 11) + 8
    status_label = "Throttled" if dto.throttled else "Normal"
    status_bg = COLORS["amber-100"] if dto.throttled else COLORS["green-100"]
    status_fg = COLORS["amber-800"] if dto.throttled else COLORS["green-800"]
    s, _w = pill(right_x, y, status_label, bg=status_bg, fg=status_fg, anchor="end")
    body.append(s)

    y += 18 + 16  # header height + grid gap-4

    # --- Rule boxes ---
    if rules:
        for fr in rules:
            box_svg, box_h = _rule_box(fr, PAD, y)
            body.append(box_svg)
            y += box_h + 16
        y -= 16
    else:
        body.append(svg_text(PAD, y + 12, "No fair-usage rules apply.", size=12, color=COLORS["gray-500"]))
        y += 16

    # --- Footer: peer name ---
    y += 12
    footer = truncate_to_width(peer_label or "Peer", 10, CONTENT_W)
    body.append(svg_text(PAD, y + 10, footer, size=10, color=COLORS["gray-400"]))
    y += 14

    return svg_document(CARD_W, y + PAD, "".join(body))


async def render_fair_usage_peer_card_png(
    dto: FairUsagePeerStatusDTO,
    peer_label: str,
    lang: str = "en",
) -> bytes:
    """Render the fair-usage card to PNG bytes (SVG via resvg, off the event loop)."""
    svg = build_fair_usage_card_svg(dto, peer_label, lang)
    return await asyncio.to_thread(render_svg_to_png, svg)
