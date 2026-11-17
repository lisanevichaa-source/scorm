"""Процедурная генерация обложек глав: градиентный фон + коллаж тематических иконок.
Полностью офлайн, без обращений к внешним API — быстро, бесплатно, детерминированно."""
from __future__ import annotations
import hashlib

from .cover_icons import render_icon

PALETTES: dict[str, dict[str, str]] = {
    "indigo": {"bg1": "#eef0ff", "bg2": "#c9ccfb", "badge": "#ffffff", "icon": "#5b4bda"},
    "teal":   {"bg1": "#e6fbf7", "bg2": "#a9e9dc", "badge": "#ffffff", "icon": "#0f9488"},
    "orange": {"bg1": "#fff3e6", "bg2": "#fcd3a0", "badge": "#ffffff", "icon": "#d9730d"},
    "blue":   {"bg1": "#e8f2ff", "bg2": "#b9d7fb", "badge": "#ffffff", "icon": "#1d5fd6"},
    "green":  {"bg1": "#eefbea", "bg2": "#bfe8ac", "badge": "#ffffff", "icon": "#2e8b32"},
    "slate":  {"bg1": "#eef1f5", "bg2": "#c3ccd8", "badge": "#ffffff", "icon": "#334155"},
    "rose":   {"bg1": "#fff0f3", "bg2": "#f9c3cf", "badge": "#ffffff", "icon": "#be123c"},
    "amber":  {"bg1": "#fff8e1", "bg2": "#fbdd8a", "badge": "#ffffff", "icon": "#b8860b"},
}


def _stable_hash(text: str) -> int:
    return int(hashlib.md5(text.encode("utf-8")).hexdigest(), 16)


def select_palette(seed_text: str) -> str:
    names = sorted(PALETTES.keys())
    return names[_stable_hash(seed_text) % len(names)]


# Позиции бейджей с иконками (cx, cy — доли ширины/высоты; r — радиус в px при 960x540).
# Нижняя часть (ниже ~0.72 высоты) оставлена свободной — там в плеере рисуется подложка с заголовком.
_BADGE_SLOTS = [
    (0.14, 0.24, 78),
    (0.84, 0.20, 66),
    (0.90, 0.55, 86),
    (0.15, 0.58, 60),
]

_DECOR_CIRCLES = [
    (0.04, 0.9, 90, 0.10),
    (0.97, 0.95, 130, 0.08),
    (0.55, 0.02, 70, 0.07),
]


def build_cover_svg(icon_names: list[str], palette: str, width: int = 960, height: int = 540) -> str:
    p = PALETTES.get(palette, PALETTES["indigo"])
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">']
    parts.append(
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0%" stop-color="{p["bg1"]}"/>'
        f'<stop offset="100%" stop-color="{p["bg2"]}"/>'
        f'</linearGradient></defs>'
        f'<rect width="{width}" height="{height}" fill="url(#bg)"/>'
    )

    for fx, fy, r, op in _DECOR_CIRCLES:
        parts.append(f'<circle cx="{fx*width:.0f}" cy="{fy*height:.0f}" r="{r}" fill="#ffffff" opacity="{op}"/>')

    for i, name in enumerate(icon_names[:len(_BADGE_SLOTS)]):
        fx, fy, badge_r = _BADGE_SLOTS[i]
        cx, cy = fx * width, fy * height
        icon_size = badge_r * 1.15
        parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{badge_r}" fill="{p["badge"]}" opacity="0.92"/>')
        parts.append(
            f'<g transform="translate({cx - icon_size/2:.1f},{cy - icon_size/2:.1f}) scale({icon_size/100:.4f})">'
            + render_icon(name, p["icon"]) + "</g>"
        )

    parts.append("</svg>")
    return "".join(parts)
