"""Очистка неиспользуемых файлов в data/.

Удаляются только файлы, на которые не ссылается ни один курс:
- видео, картинки и аудио из data/media, которых нет ни в одном уроке и ни в списке картинок нужных документов;
- загруженные документы (и их текст и картинки), не привязанные ни к одному курсу и старше CLEANUP_UPLOAD_DAYS;
- обрывки незавершённых загрузок (.part, .tmp);
- сборки (папка и zip) курсов, которых больше нет.
Свежие файлы (моложе CLEANUP_MIN_AGE_HOURS) не трогаются: их могли только что загрузить и ещё не вставить в урок.
Запуск: администратор в интерфейсе («Мои курсы» → «Очистка файлов») или `python -m app.cleanup` (показывает, `--apply` удаляет).
"""
from __future__ import annotations
import json
import re
import shutil
import sys
import time
from pathlib import Path

from . import config, storage

MEDIA_FILE_RE = re.compile(r"^[0-9a-f]{12,32}\.(mp4|webm|jpg|png|mp3)$")
MEDIA_REF_RE = re.compile(r"/media/([0-9a-f]{12,32}\.(?:mp4|webm|jpg|png|mp3))")
PART_RE = re.compile(r"\.(part\d+|tmp)$")
UPLOAD_ID_RE = re.compile(r"^([0-9a-f]{12,32})(?:\.|$)")


class CleanupError(RuntimeError):
    pass


def _size(path: Path) -> int:
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return path.stat().st_size


def _age_hours(path: Path, now: float) -> float:
    return (now - path.stat().st_mtime) / 3600


def scan(min_age_hours: float | None = None, upload_days: float | None = None) -> list[dict]:
    """Список того, что можно удалить: [{kind, path, name, bytes}]. Ничего не удаляет."""
    min_age = config.CLEANUP_MIN_AGE_HOURS if min_age_hours is None else min_age_hours
    upload_age = (config.CLEANUP_UPLOAD_DAYS if upload_days is None else upload_days) * 24
    now = time.time()
    found: list[dict] = []

    def add(kind: str, path: Path):
        found.append({"kind": kind, "path": path, "name": path.name, "bytes": _size(path)})

    with storage.LOCK:
        # 1. что нужно курсам
        course_ids: set[str] = set()
        used_uploads: set[str] = set()
        used_media: set[str] = set()
        for path in config.COURSES_DIR.glob("*.json"):
            try:
                text = path.read_text(encoding="utf-8")
                data = json.loads(text)
            except (OSError, ValueError) as e:
                # битый файл курса: неизвестно, какие файлы ему нужны, поэтому ничего не удаляем
                raise CleanupError(f"Не удалось прочитать курс {path.name}: очистка остановлена, чтобы не удалить нужное.") from e
            course_ids.add(path.stem)
            used_media.update(MEDIA_REF_RE.findall(text))
            used_uploads.update((data.get("setup") or {}).get("upload_ids") or [])

        # 2. загруженные документы
        uploads: dict[str, list[Path]] = {}
        for path in config.UPLOADS_DIR.iterdir():
            m = UPLOAD_ID_RE.match(path.name)
            if not m or not path.is_file():
                continue
            if PART_RE.search(path.name):
                if _age_hours(path, now) >= min_age:
                    add("обрывок загрузки", path)
                continue
            uploads.setdefault(m.group(1), []).append(path)
        removed_uploads: set[str] = set()
        for uid, files in uploads.items():
            if uid in used_uploads:
                continue
            if min(_age_hours(f, now) for f in files) >= upload_age:
                removed_uploads.add(uid)
                for f in files:
                    add("документ без курса", f)
        # картинки оставшихся документов нужны, пока документ может использоваться
        for uid in uploads:
            if uid in removed_uploads:
                continue
            for item in storage.load_upload_images(uid):
                if item.get("file"):
                    used_media.add(item["file"])

        # 3. медиа
        for path in config.MEDIA_DIR.iterdir():
            if not path.is_file():
                continue
            if PART_RE.search(path.name):
                if _age_hours(path, now) >= min_age:
                    add("обрывок загрузки", path)
            elif MEDIA_FILE_RE.match(path.name) and path.name not in used_media and _age_hours(path, now) >= min_age:
                add("медиа без курса", path)

        # 4. сборки удалённых курсов
        for path in config.BUILDS_DIR.iterdir():
            if path.stem not in course_ids and (path.is_dir() or path.suffix == ".zip"):
                add("сборка без курса", path)
    return found


def apply(items: list[dict]) -> int:
    n = 0
    for it in items:
        p: Path = it["path"]
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink(missing_ok=True)
            n += 1
        except OSError:
            continue
    return n


def report(items: list[dict], deleted: int | None = None) -> dict:
    by_kind: dict[str, dict] = {}
    for it in items:
        k = by_kind.setdefault(it["kind"], {"files": 0, "bytes": 0})
        k["files"] += 1
        k["bytes"] += it["bytes"]
    total = sum(it["bytes"] for it in items)
    return {
        "files": len(items), "mb": round(total / 1024 / 1024, 1), "deleted": deleted,
        "by_kind": [{"kind": k, "files": v["files"], "mb": round(v["bytes"] / 1024 / 1024, 1)} for k, v in by_kind.items()],
    }


def main(argv: list[str]) -> int:
    try:
        items = scan()
    except CleanupError as e:
        print(e)
        return 1
    rep = report(items)
    for k in rep["by_kind"]:
        print(f"{k['kind']}: {k['files']} шт., {k['mb']} МБ")
    print(f"Итого: {rep['files']} файлов, {rep['mb']} МБ")
    if "--apply" in argv:
        print(f"Удалено: {apply(items)}")
    elif items:
        print("Это только показ. Чтобы удалить, запустите с --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
