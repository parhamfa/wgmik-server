"""Shared SVG building + rasterization helpers for Telegram card images.

Cards are built as SVG strings in Python and rasterized to PNG with resvg
(no browser involved). Fonts are bundled in ``backend/assets/fonts`` so the
output is deterministic inside the slim container.
"""

from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

# >1 multiplies output pixel dimensions (SVG layout unchanged). 2 ~= "retina".
RENDER_SCALE = 2

FONT_FAMILY = "Vazirmatn"
FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"

# Tailwind palette subset used by the original React render pages (light theme).
COLORS = {
    "white": "#ffffff",
    "gray-50": "#f9fafb",
    "gray-100": "#f3f4f6",
    "gray-200": "#e5e7eb",
    "gray-300": "#d1d5db",
    "gray-400": "#9ca3af",
    "gray-500": "#6b7280",
    "gray-600": "#4b5563",
    "gray-700": "#374151",
    "gray-800": "#1f2937",
    "gray-900": "#111827",
    "amber-50": "#fffbeb",
    "amber-100": "#fef3c7",
    "amber-300": "#fcd34d",
    "amber-500": "#f59e0b",
    "amber-700": "#b45309",
    "amber-800": "#92400e",
    "amber-900": "#78350f",
    "green-100": "#dcfce7",
    "green-800": "#166534",
    "indigo-50": "#eef2ff",
    "indigo-700": "#4338ca",
    "red-600": "#dc2626",
    # Chart CSS variables from frontend/src/styles.css (light theme).
    "chart-line-1": "#111827",
    "chart-line-2": "#6b7280",
    "chart-tick": "#6b7280",
    "chart-grid": "rgba(0,0,0,0.08)",
    "chart-fill-1": "#111827",
    "chart-fill-2": "#6b7280",
}


def esc(value: object) -> str:
    return escape(str(value), {'"': "&quot;"})


def fmt_bytes(n: float | int) -> str:
    """Match fmtBytes() from the React render pages."""
    if not n or n <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    u = 0
    x = float(n)
    while x >= 1024 and u < len(units) - 1:
        x /= 1024
        u += 1
    if x >= 100:
        return f"{x:.0f} {units[u]}"
    if x >= 10:
        return f"{x:.1f} {units[u]}"
    return f"{x:.2f} {units[u]}"


# Approximate per-character advance widths (em) for Vazirmatn-like sans fonts.
_CHAR_EM_NARROW = set("iljIft.,:;'’|!()[]{}/ ")
_CHAR_EM_WIDE = set("mwMW@%")
_CHAR_EM_UPPER_DIGIT = set("ABCDEFGHKNOPQRSTUVXYZ0123456789≥#·")


def text_width(text: str, font_size: float, weight: int = 400) -> float:
    """Rough text width estimate; exactness is absorbed by paddings/gaps."""
    w = 0.0
    for ch in str(text):
        if ch in _CHAR_EM_NARROW:
            w += 0.32
        elif ch in _CHAR_EM_WIDE:
            w += 0.95
        elif ch in _CHAR_EM_UPPER_DIGIT:
            w += 0.68
        elif ord(ch) >= 0x0600:  # Arabic/Persian block: wider on average
            w += 0.62
        else:
            w += 0.55
    bold_factor = 1.0 + max(0, weight - 400) / 400 * 0.06
    return w * font_size * bold_factor


def truncate_to_width(text: str, font_size: float, max_width: float, weight: int = 400) -> str:
    if text_width(text, font_size, weight) <= max_width:
        return text
    ell = "…"
    out = text
    while out and text_width(out + ell, font_size, weight) > max_width:
        out = out[:-1]
    return out + ell


def svg_text(
    x: float,
    y: float,
    content: str,
    *,
    size: float,
    color: str,
    weight: int = 400,
    anchor: str = "start",
) -> str:
    """``y`` is the text baseline."""
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT_FAMILY}" font-size="{size}" '
        f'font-weight="{weight}" fill="{color}" text-anchor="{anchor}">{esc(content)}</text>'
    )


def svg_rect(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str = "none",
    rx: float = 0,
    stroke: str | None = None,
    stroke_width: float = 1,
) -> str:
    s = f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx:.1f}" fill="{fill}"'
    if stroke:
        s += f' stroke="{stroke}" stroke-width="{stroke_width}"'
    return s + "/>"


def pill(
    x: float,
    y: float,
    label: str,
    *,
    bg: str,
    fg: str,
    size: float = 11,
    pad_x: float = 8,
    height: float = 18,
    anchor: str = "start",
) -> tuple[str, float]:
    """Rounded-full badge. ``x`` is the left edge (or right edge when anchor='end').

    Returns (svg, total_width).
    """
    w = text_width(label, size) + 2 * pad_x
    left = x - w if anchor == "end" else x
    parts = [
        svg_rect(left, y, w, height, fill=bg, rx=height / 2),
        svg_text(left + w / 2, y + height / 2 + size * 0.36, label, size=size, color=fg, anchor="middle"),
    ]
    return "".join(parts), w


def progress_bar(x: float, y: float, w: float, pct: float, *, fill: str, h: float = 8) -> str:
    pct = max(0.0, min(100.0, pct))
    parts = [svg_rect(x, y, w, h, fill=COLORS["gray-100"], rx=h / 2)]
    fw = w * pct / 100.0
    if fw > 0:
        parts.append(svg_rect(x, y, max(fw, h), h, fill=fill, rx=h / 2))
    return "".join(parts)


def nice_ticks(max_value: float, tick_count: int = 5) -> list[float]:
    """Nice axis ticks for domain [0, max_value], similar to recharts/d3."""
    if max_value <= 0:
        return [0.0, 1.0]
    raw_step = max_value / max(1, tick_count - 1)
    mag = 10 ** math.floor(math.log10(raw_step))
    for mult in (1, 2, 2.5, 5, 10):
        step = mult * mag
        if step >= raw_step:
            break
    top = math.ceil(max_value / step) * step
    n = int(round(top / step))
    return [i * step for i in range(n + 1)]


def monotone_path(points: list[tuple[float, float]]) -> str:
    """Cubic path through points using the Fritsch-Carlson monotone tangents
    (same shape as d3's curveMonotoneX used by recharts ``type="monotone"``)."""
    n = len(points)
    if n == 0:
        return ""
    if n == 1:
        x, y = points[0]
        return f"M{x:.2f},{y:.2f}"
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    dx = [xs[i + 1] - xs[i] for i in range(n - 1)]
    slopes = [
        (ys[i + 1] - ys[i]) / dx[i] if dx[i] != 0 else 0.0
        for i in range(n - 1)
    ]
    m = [0.0] * n
    m[0] = slopes[0]
    m[-1] = slopes[-1]
    for i in range(1, n - 1):
        if slopes[i - 1] * slopes[i] <= 0:
            m[i] = 0.0
        else:
            w1 = 2 * dx[i] + dx[i - 1]
            w2 = dx[i] + 2 * dx[i - 1]
            m[i] = (w1 + w2) / (w1 / slopes[i - 1] + w2 / slopes[i])
    d = [f"M{xs[0]:.2f},{ys[0]:.2f}"]
    for i in range(n - 1):
        h = dx[i]
        c1x = xs[i] + h / 3
        c1y = ys[i] + m[i] * h / 3
        c2x = xs[i + 1] - h / 3
        c2y = ys[i + 1] - m[i + 1] * h / 3
        d.append(f"C{c1x:.2f},{c1y:.2f} {c2x:.2f},{c2y:.2f} {xs[i + 1]:.2f},{ys[i + 1]:.2f}")
    return "".join(d)


def render_svg_to_png(svg: str, *, scale: int = RENDER_SCALE) -> bytes:
    import resvg_py

    out = resvg_py.svg_to_bytes(
        svg_string=svg,
        zoom=scale,
        skip_system_fonts=True,
        font_dirs=[str(FONTS_DIR)],
        font_family=FONT_FAMILY,
        sans_serif_family=FONT_FAMILY,
        monospace_family=FONT_FAMILY,
    )
    return bytes(out)


def svg_document(width: float, height: float, body: str, *, background: str = COLORS["white"]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
        f'viewBox="0 0 {width:.0f} {height:.0f}">'
        f'<rect x="0" y="0" width="{width:.0f}" height="{height:.0f}" fill="{background}"/>'
        f"{body}</svg>"
    )
