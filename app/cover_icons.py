"""Набор простых флэт-иконок (построены из базовых SVG-примитивов — rect/circle/line/path)
и алгоритм подбора иконок под тему главы по ключевым словам, без обращения к каким-либо API.
"""
from __future__ import annotations

ICONS: dict[str, list[dict]] = {
    "document": [
        {"type": "rect", "x": 22, "y": 10, "w": 56, "h": 80, "rx": 6},
        {"type": "line", "x1": 32, "y1": 30, "x2": 68, "y2": 30, "stroke": 6, "inverse": True},
        {"type": "line", "x1": 32, "y1": 46, "x2": 68, "y2": 46, "stroke": 6, "inverse": True},
        {"type": "line", "x1": 32, "y1": 62, "x2": 56, "y2": 62, "stroke": 6, "inverse": True},
    ],
    "folder": [
        {"type": "path", "d": "M10,30 L40,30 L48,20 L90,20 L90,80 L10,80 Z"},
    ],
    "warehouse": [
        {"type": "polygon", "points": "10,45 50,15 90,45"},
        {"type": "rect", "x": 15, "y": 45, "w": 70, "h": 40},
        {"type": "rect", "x": 42, "y": 65, "w": 16, "h": 20, "inverse": True},
    ],
    "factory": [
        {"type": "rect", "x": 10, "y": 50, "w": 80, "h": 35},
        {"type": "rect", "x": 20, "y": 25, "w": 12, "h": 25},
        {"type": "rect", "x": 44, "y": 15, "w": 12, "h": 35},
        {"type": "circle", "cx": 26, "cy": 18, "r": 6, "opacity": 0.6},
        {"type": "circle", "cx": 50, "cy": 8, "r": 6, "opacity": 0.6},
    ],
    "chart_up": [
        {"type": "rect", "x": 16, "y": 55, "w": 16, "h": 32},
        {"type": "rect", "x": 42, "y": 38, "w": 16, "h": 49},
        {"type": "rect", "x": 68, "y": 18, "w": 16, "h": 69},
    ],
    "chart_pie": [
        {"type": "circle", "cx": 50, "cy": 50, "r": 40},
        {"type": "path", "d": "M50,50 L50,10 A40,40 0 0 1 85,65 Z", "opacity": 0.55},
    ],
    "gear": [
        {"type": "circle", "cx": 50, "cy": 50, "r": 24},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(0 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(45 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(90 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(135 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(180 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(225 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(270 50 50)"},
        {"type": "rect", "x": 44, "y": 2, "w": 12, "h": 18, "rx": 3, "transform": "rotate(315 50 50)"},
    ],
    "checklist": [
        {"type": "rect", "x": 20, "y": 12, "w": 60, "h": 76, "rx": 6},
        {"type": "rect", "x": 35, "y": 6, "w": 30, "h": 10, "rx": 3},
        {"type": "path", "d": "M30,38 L42,50 L64,28", "fill": "none", "stroke": 7, "inverse": True},
        {"type": "line", "x1": 30, "y1": 62, "x2": 70, "y2": 62, "stroke": 6, "inverse": True},
        {"type": "line", "x1": 30, "y1": 76, "x2": 70, "y2": 76, "stroke": 6, "inverse": True},
    ],
    "shield": [
        {"type": "path", "d": "M50,6 L86,20 L86,48 C86,72 70,88 50,96 C30,88 14,72 14,48 L14,20 Z"},
        {"type": "path", "d": "M34,48 L46,60 L68,34", "fill": "none", "stroke": 7, "inverse": True},
    ],
    "box": [
        {"type": "polygon", "points": "50,8 90,26 50,44 10,26"},
        {"type": "polygon", "points": "10,26 50,44 50,92 10,74"},
        {"type": "polygon", "points": "90,26 50,44 50,92 90,74", "opacity": 0.7},
    ],
    "truck": [
        {"type": "rect", "x": 6, "y": 38, "w": 52, "h": 32, "rx": 4},
        {"type": "rect", "x": 58, "y": 50, "w": 30, "h": 20, "rx": 4},
        {"type": "circle", "cx": 26, "cy": 78, "r": 10},
        {"type": "circle", "cx": 72, "cy": 78, "r": 10},
    ],
    "laptop": [
        {"type": "rect", "x": 16, "y": 16, "w": 68, "h": 46, "rx": 4},
        {"type": "rect", "x": 8, "y": 66, "w": 84, "h": 9, "rx": 4},
    ],
    "money": [
        {"type": "circle", "cx": 38, "cy": 38, "r": 30},
        {"type": "circle", "cx": 64, "cy": 64, "r": 30, "opacity": 0.6},
    ],
    "building": [
        {"type": "rect", "x": 25, "y": 10, "w": 50, "h": 82},
        {"type": "rect", "x": 34, "y": 22, "w": 10, "h": 10, "inverse": True},
        {"type": "rect", "x": 56, "y": 22, "w": 10, "h": 10, "inverse": True},
        {"type": "rect", "x": 34, "y": 42, "w": 10, "h": 10, "inverse": True},
        {"type": "rect", "x": 56, "y": 42, "w": 10, "h": 10, "inverse": True},
        {"type": "rect", "x": 34, "y": 62, "w": 10, "h": 10, "inverse": True},
        {"type": "rect", "x": 56, "y": 62, "w": 10, "h": 10, "inverse": True},
    ],
    "book": [
        {"type": "path", "d": "M50,22 C40,14 18,14 10,20 L10,80 C18,74 40,74 50,82 C60,74 82,74 90,80 L90,20 C82,14 60,14 50,22 Z"},
        {"type": "line", "x1": 50, "y1": 22, "x2": 50, "y2": 82, "stroke": 4, "opacity": 0.5, "inverse": True},
    ],
    "people": [
        {"type": "circle", "cx": 35, "cy": 28, "r": 16},
        {"type": "circle", "cx": 65, "cy": 28, "r": 16, "opacity": 0.65},
        {"type": "path", "d": "M10,90 C10,66 23,55 35,55 C47,55 58,66 58,90 Z"},
        {"type": "path", "d": "M42,90 C42,66 55,55 65,55 C77,55 90,66 90,90 Z", "opacity": 0.65},
    ],
    "lock": [
        {"type": "rect", "x": 22, "y": 46, "w": 56, "h": 44, "rx": 8},
        {"type": "path", "d": "M32,46 L32,30 C32,16 68,16 68,30 L68,46", "fill": "none", "stroke": 8},
        {"type": "circle", "cx": 50, "cy": 68, "r": 7, "inverse": True},
    ],
    "clock": [
        {"type": "circle", "cx": 50, "cy": 50, "r": 40, "fill": "none", "stroke": 7},
        {"type": "line", "x1": 50, "y1": 50, "x2": 50, "y2": 24, "stroke": 6},
        {"type": "line", "x1": 50, "y1": 50, "x2": 68, "y2": 58, "stroke": 6},
    ],
    "target": [
        {"type": "circle", "cx": 50, "cy": 50, "r": 40, "fill": "none", "stroke": 8},
        {"type": "circle", "cx": 50, "cy": 50, "r": 24, "fill": "none", "stroke": 8},
        {"type": "circle", "cx": 50, "cy": 50, "r": 8},
    ],
    "lightbulb": [
        {"type": "circle", "cx": 50, "cy": 40, "r": 28},
        {"type": "rect", "x": 37, "y": 64, "w": 26, "h": 14, "rx": 4},
        {"type": "line", "x1": 50, "y1": 2, "x2": 50, "y2": 12, "stroke": 5, "opacity": 0.6},
        {"type": "line", "x1": 14, "y1": 22, "x2": 23, "y2": 29, "stroke": 5, "opacity": 0.6},
        {"type": "line", "x1": 86, "y1": 22, "x2": 77, "y2": 29, "stroke": 5, "opacity": 0.6},
    ],
    "email": [
        {"type": "rect", "x": 8, "y": 22, "w": 84, "h": 58, "rx": 6, "fill": "none", "stroke": 6},
        {"type": "path", "d": "M10,25 L50,58 L90,25", "fill": "none", "stroke": 6},
    ],
    "calendar": [
        {"type": "rect", "x": 14, "y": 20, "w": 72, "h": 66, "rx": 6},
        {"type": "rect", "x": 28, "y": 6, "w": 8, "h": 20, "rx": 3, "opacity": 0.7},
        {"type": "rect", "x": 64, "y": 6, "w": 8, "h": 20, "rx": 3, "opacity": 0.7},
        {"type": "line", "x1": 14, "y1": 40, "x2": 86, "y2": 40, "stroke": 4, "opacity": 0.5, "inverse": True},
    ],
    "magnifier": [
        {"type": "circle", "cx": 40, "cy": 40, "r": 28, "fill": "none", "stroke": 9},
        {"type": "line", "x1": 61, "y1": 61, "x2": 90, "y2": 90, "stroke": 11},
    ],
    "handshake": [
        {"type": "path", "d": "M8,45 L32,45 L48,30 L62,40 L78,24 L92,38 L92,52 L78,38 L62,54 L48,44 L32,58 L8,58 Z"},
    ],
}


def _icon_names() -> list[str]:
    return list(ICONS.keys())


# Ключевые слова (рус.) -> иконка. Порядок важен только для стабильности,
# дедупликация по названию иконки делает порядок не критичным для результата.
KEYWORD_MAP: dict[str, str] = {
    "докум": "document", "справк": "document", "инструкц": "document",
    "папк": "folder", "архив": "folder",
    "склад": "warehouse", "хранени": "warehouse",
    "завод": "factory", "производ": "factory", "цех": "factory",
    "рост": "chart_up", "продаж": "chart_up", "выручк": "chart_up", "показател": "chart_up",
    "отчет": "chart_pie", "аналитик": "chart_pie", "статистик": "chart_pie",
    "настрой": "gear", "конфигурац": "gear", "систем": "gear", "параметр": "gear",
    "чек-лист": "checklist", "проверк": "checklist", "контрол": "checklist",
    "безопасност": "shield", "охран": "shield", "риск": "shield",
    "упаковк": "box", "товар": "box", "посылк": "box", "груз": "box",
    "достав": "truck", "логистик": "truck", "транспорт": "truck",
    "компьютер": "laptop", "программ": "laptop", "софт": "laptop", "по ": "laptop",
    "бухгалт": "money", "финанс": "money", "деньг": "money", "оплат": "money", "зарплат": "money",
    "здани": "building", "офис": "building", "компани": "building", "подразделен": "building",
    "обучени": "book", "курс": "book", "книг": "book", "методик": "book",
    "сотрудник": "people", "команд": "people", "персонал": "people", "клиент": "people",
    "пароль": "lock", "доступ": "lock", "защит": "lock",
    "время": "clock", "график": "clock", "срок": "clock", "расписан": "clock",
    "цель": "target", "задач": "target", "план": "calendar",
    "идея": "lightbulb", "иннова": "lightbulb",
    "почт": "email", "письм": "email", "коммуникац": "email",
    "сделк": "handshake", "партнер": "handshake", "договор": "handshake", "перегово": "handshake",
    "поиск": "magnifier", "анализ": "magnifier",
}

DEFAULT_ICONS = ["book", "target", "gear", "chart_up", "checklist", "building"]


def select_icons(chapter_title: str, lesson_titles: list[str], max_icons: int = 4) -> list[str]:
    text = (chapter_title + " " + " ".join(lesson_titles)).lower()
    matched: list[str] = []
    for kw, icon in KEYWORD_MAP.items():
        if kw in text and icon not in matched:
            matched.append(icon)
        if len(matched) >= max_icons:
            break
    for d in DEFAULT_ICONS:
        if len(matched) >= max_icons:
            break
        if d not in matched:
            matched.append(d)
    return matched[:max_icons]


def render_icon(name: str, color: str, inverse_color: str = "#ffffff") -> str:
    """Рендерит иконку как набор SVG-элементов в локальной системе координат 0..100."""
    shapes = ICONS.get(name, ICONS["document"])
    parts = []
    for s in shapes:
        t = s["type"]
        opacity = s.get("opacity", 1)
        fill_color = inverse_color if s.get("inverse") else color
        transform_attr = f' transform="{s["transform"]}"' if "transform" in s else ""

        if t == "rect":
            rx = s.get("rx", 0)
            parts.append(
                f'<rect x="{s["x"]}" y="{s["y"]}" width="{s["w"]}" height="{s["h"]}" '
                f'rx="{rx}" fill="{fill_color}" opacity="{opacity}"{transform_attr}/>'
            )
        elif t == "circle":
            if s.get("fill") == "none":
                sw = s.get("stroke", 6)
                parts.append(
                    f'<circle cx="{s["cx"]}" cy="{s["cy"]}" r="{s["r"]}" fill="none" '
                    f'stroke="{fill_color}" stroke-width="{sw}" opacity="{opacity}"{transform_attr}/>'
                )
            else:
                parts.append(
                    f'<circle cx="{s["cx"]}" cy="{s["cy"]}" r="{s["r"]}" fill="{fill_color}" '
                    f'opacity="{opacity}"{transform_attr}/>'
                )
        elif t == "line":
            sw = s.get("stroke", 6)
            parts.append(
                f'<line x1="{s["x1"]}" y1="{s["y1"]}" x2="{s["x2"]}" y2="{s["y2"]}" '
                f'stroke="{fill_color}" stroke-width="{sw}" stroke-linecap="round" '
                f'opacity="{opacity}"{transform_attr}/>'
            )
        elif t == "polygon":
            parts.append(
                f'<polygon points="{s["points"]}" fill="{fill_color}" opacity="{opacity}"{transform_attr}/>'
            )
        elif t == "path":
            if s.get("fill") == "none":
                sw = s.get("stroke", 6)
                parts.append(
                    f'<path d="{s["d"]}" fill="none" stroke="{fill_color}" stroke-width="{sw}" '
                    f'stroke-linecap="round" stroke-linejoin="round" opacity="{opacity}"{transform_attr}/>'
                )
            else:
                parts.append(f'<path d="{s["d"]}" fill="{fill_color}" opacity="{opacity}"{transform_attr}/>')
    return "".join(parts)
