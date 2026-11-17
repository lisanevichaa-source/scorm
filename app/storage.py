"""Файловое хранилище для MVP.

Для продакшена (многопользовательская работа, конкурентный доступ) стоит заменить
на БД (Postgres/SQLite) — интерфейс ниже специально узкий, чтобы замена была локальной.
"""
from __future__ import annotations
import json
from pathlib import Path
from . import config
from .models import Course


def save_course(course: Course) -> None:
    path = config.COURSES_DIR / f"{course.id}.json"
    path.write_text(course.model_dump_json(indent=2), encoding="utf-8")


def load_course(course_id: str) -> Course:
    path = config.COURSES_DIR / f"{course_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"Курс {course_id} не найден")
    return Course.model_validate_json(path.read_text(encoding="utf-8"))


def save_upload_text(upload_id: str, text: str) -> None:
    path = config.UPLOADS_DIR / f"{upload_id}.txt"
    path.write_text(text, encoding="utf-8")


def load_upload_text(upload_id: str) -> str:
    path = config.UPLOADS_DIR / f"{upload_id}.txt"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def combined_source_text(upload_ids: list[str]) -> str:
    texts = [load_upload_text(uid) for uid in upload_ids]
    return "\n\n".join(t for t in texts if t)
