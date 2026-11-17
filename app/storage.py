"""Файловое хранилище курсов.

Для продакшена (многопользовательская работа, конкурентный доступ) стоит заменить
на БД (Postgres/SQLite) — интерфейс ниже специально узкий, чтобы замена была локальной.
"""
from __future__ import annotations
import json
import re
import threading
from functools import lru_cache
from pathlib import Path
from . import config
from .chunking import Chunk, split_chunks
from .models import Course


# Замок для цикла «прочитать - изменить - записать» (фоновая генерация и правки пользователя идут параллельно)
LOCK = threading.RLock()


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


def save_upload_images(upload_id: str, items: list[dict]) -> None:
    (config.UPLOADS_DIR / f"{upload_id}.images.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def load_upload_images(upload_id: str) -> list[dict]:
    path = config.UPLOADS_DIR / f"{upload_id}.images.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []


_IMG_MARK = re.compile(r"\[\[IMG:(\d+)\]\]")


@lru_cache(maxsize=8)
def _material_cached(key: tuple) -> tuple:
    """Фрагменты и картинки всех файлов курса. Номера рисунков сквозные: «Рисунок 1», «Рисунок 2»... по всем файлам."""
    chunks: list[Chunk] = []
    images: list[dict] = []
    for uid, _mtime in key:
        local = {it["n"]: it for it in load_upload_images(uid)}
        base = len(images)
        order = {n: base + i + 1 for i, n in enumerate(sorted(local))}

        def repl(m):
            n = int(m.group(1))
            if n not in order:
                return ""
            cap = local[n].get("caption") or ""
            return f"[[Рисунок {order[n]}]]" + (f" {cap}" if cap else "")

        text = _IMG_MARK.sub(repl, load_upload_text(uid))
        chunks += split_chunks(text, start_id=len(chunks))
        images += [{**local[n], "k": order[n]} for n in sorted(local)]
    return tuple(chunks), tuple(images)


def _material(upload_ids: list[str]) -> tuple:
    key = []
    for uid in upload_ids:
        p = config.UPLOADS_DIR / f"{uid}.txt"
        if p.exists():
            key.append((uid, p.stat().st_mtime_ns))
    return _material_cached(tuple(key))


def course_chunks(upload_ids: list[str]) -> list[Chunk]:
    """Фрагменты всех загруженных файлов курса с общей сквозной нумерацией."""
    return list(_material(upload_ids)[0])


def course_images(upload_ids: list[str]) -> dict[int, dict]:
    """Картинки из документов курса: {номер рисунка: {file, caption, w, h, k}}."""
    return {im["k"]: im for im in _material(upload_ids)[1]}


def list_courses() -> list[dict]:
    """Краткая сводка по всем курсам, новые сверху (для экрана «Мои курсы»)."""
    items = []
    for path in config.COURSES_DIR.glob("*.json"):
        try:
            c = Course.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue  # битый файл не должен ломать список
        lessons = [l for ch in c.chapters for l in ch.lessons]
        items.append({
            "id": c.id,
            "title": c.title or c.setup.description[:60] or "Без названия",
            "status": c.status,
            "lessons": len(lessons),
            "generated": sum(1 for l in lessons if l.generated),
            "updated": path.stat().st_mtime,
            "owner": c.owner,
            "owner_name": c.owner_name,
            "shares": [{"email": x.email, "role": x.role} for x in c.shares],
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def delete_course(course_id: str) -> None:
    for path in (config.COURSES_DIR / f"{course_id}.json", config.BUILDS_DIR / f"{course_id}.zip"):
        if path.exists():
            path.unlink()


def assign_legacy_owner() -> int:
    """Курсам без владельца (созданным до появления входа) назначается LEGACY_OWNER_EMAIL или первый администратор."""
    owner = config.LEGACY_OWNER_EMAIL or (config.ADMIN_EMAILS[0] if config.ADMIN_EMAILS else "")
    if not owner:
        return 0
    n = 0
    with LOCK:
        for path in config.COURSES_DIR.glob("*.json"):
            try:
                c = Course.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not c.owner:
                c.owner = owner
                save_course(c)
                n += 1
    return n
