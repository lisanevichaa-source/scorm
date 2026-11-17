"""Как читать вслух аббревиатуры и латиницу.

Голосовые модели путают «1С», «PLM», «НСИ» и подобное. Перед озвучкой в тексте подкаста такие слова
заменяются на то, как их произносят. Свои слова можно добавить в файл data/pronunciation.json:
{"ЕСКД": "е эс ка дэ", "Teamcenter": "тим сентер"}. Файл читается при каждой озвучке, перезапуск не нужен.
"""
from __future__ import annotations
import json
import re

from . import config

DEFAULTS = {
    "1С:PLM": "один эс пи эл эм",
    "1С": "один эс",
    "PLM": "пи эл эм",
    "ERP": "э эр пэ",
    "НСИ": "эн эс и",
    "ЕСКД": "е эс ка дэ",
    "КТПП": "ка тэ пэ пэ",
    "ИНН": "и эн эн",
    "ОГРН": "о гэ эр эн",
    "КПП": "ка пэ пэ",
    "ООО": "о о о",
    "ОАО": "о а о",
    "ПАО": "пэ а о",
    "НДС": "эн дэ эс",
    "PDF": "пэ дэ эф",
    "SCORM": "скорм",
    "LMS": "эл эм эс",
}


def _custom() -> dict[str, str]:
    path = config.DATA_DIR / "pronunciation.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def apply(text: str) -> str:
    table = {**DEFAULTS, **_custom()}
    for word in sorted(table, key=len, reverse=True):  # длинные раньше коротких: «1С:PLM» раньше «1С»
        text = re.sub(rf"(?<![\w]){re.escape(word)}(?![\w])", table[word], text)
    return text
