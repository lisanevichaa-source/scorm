from __future__ import annotations
import json
import shutil
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import config
from .media import MEDIA_REF_RE, media_path
from .models import Course

ASSETS_DIR = Path(__file__).resolve().parent / "scorm_assets"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _course_title(course: Course) -> str:
    if course.title.strip():
        return course.title.strip()
    # Короткий заголовок берём из описания курса (первые слова) — можно расширить полем "title" позже.
    desc = course.setup.description.strip()
    return (desc[:70] + "…") if len(desc) > 70 else desc or "Курс"


def _pack_podcasts(course: Course, build_dir: Path) -> dict[str, dict]:
    """Копирует озвучку уроков в пакет (media/) и возвращает {id урока: {audio, seconds}}."""
    out: dict[str, dict] = {}
    for ch in course.chapters:
        for ls in ch.lessons:
            pod = ls.podcast
            if not (pod and pod.audio):
                continue
            name = pod.audio.rsplit("/", 1)[-1]
            src = config.MEDIA_DIR / name
            if not src.exists():
                raise ValueError(f"Озвучка урока «{ls.title}» не найдена на сервере. Создайте подкаст заново.")
            (build_dir / "media").mkdir(exist_ok=True)
            shutil.copy(src, build_dir / "media" / name)
            out[ls.id] = {"audio": f"media/{name}", "seconds": pod.seconds}
    return out


def _pack_videos(course: Course, build_dir: Path) -> dict[str, str]:
    """Копирует видео из уроков в пакет (media/) и возвращает {id урока: html с путями внутри пакета}."""
    html_by_lesson: dict[str, str] = {}
    copied: set[str] = set()
    for ch in course.chapters:
        for ls in ch.lessons:
            def repl(m, ls=ls):
                mid, ext = m.group(1), m.group(2)
                src = media_path(mid, ext)
                if not src.exists():
                    raise ValueError(f"Видео из урока «{ls.title}» не найдено на сервере. Загрузите его в урок заново.")
                if mid not in copied:
                    (build_dir / "media").mkdir(exist_ok=True)
                    shutil.copy(src, build_dir / "media" / f"{mid}.{ext}")
                    copied.add(mid)
                return f"media/{mid}.{ext}"
            html_by_lesson[ls.id] = MEDIA_REF_RE.sub(repl, ls.content_html or "")
    return html_by_lesson


def build_scorm_package(course: Course) -> Path:
    """Собирает SCORM 1.2 zip и возвращает путь к файлу."""
    build_dir = config.BUILDS_DIR / course.id
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    title = _course_title(course)
    lesson_html = _pack_videos(course, build_dir)
    podcasts = _pack_podcasts(course, build_dir)

    course_json = json.dumps(
        {
            "title": title,
            "chapters": [
                {
                    "id": ch.id,
                    "title": ch.title,
                    "cover_image": ch.cover_image,
                    "lessons": [
                        {
                            "id": ls.id,
                            "title": ls.title,
                            "content_html": lesson_html[ls.id],
                            "podcast": podcasts.get(ls.id),
                            "quizzes": ls.quizzes,
                        }
                        for ls in ch.lessons
                    ],
                }
                for ch in course.chapters
            ],
        },
        ensure_ascii=False,
    )

    player_html = _env.get_template("player.html.j2").render(
        title=title, lang=course.setup.language, course_json=course_json
    )
    manifest_xml = _env.get_template("imsmanifest.xml.j2").render(
        course_id=course.id, title=title,
        media_files=sorted(f"media/{f.name}" for f in (build_dir / "media").glob("*")) if (build_dir / "media").exists() else [],
    )

    (build_dir / "player.html").write_text(player_html, encoding="utf-8")
    (build_dir / "imsmanifest.xml").write_text(manifest_xml, encoding="utf-8")
    # Стили текста урока общие с редактором конструктора — дописываем их к стилям плеера
    player_css = (ASSETS_DIR / "player.css").read_text(encoding="utf-8")
    content_css = (ASSETS_DIR / "lesson_content.css").read_text(encoding="utf-8")
    (build_dir / "player.css").write_text(player_css + "\n\n" + content_css, encoding="utf-8")
    shutil.copy(ASSETS_DIR / "player.js", build_dir / "player.js")
    shutil.copy(ASSETS_DIR / "scorm_api.js", build_dir / "scorm_api.js")

    zip_path = config.BUILDS_DIR / f"{course.id}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(build_dir.rglob("*")):
            if f.is_file():
                # видео уже сжато: повторное сжатие только тратит время
                comp = zipfile.ZIP_STORED if f.suffix in (".mp4", ".webm", ".mp3") else zipfile.ZIP_DEFLATED
                zf.write(f, arcname=f.relative_to(build_dir).as_posix(), compress_type=comp)

    return zip_path
