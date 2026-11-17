from __future__ import annotations
import json
import shutil
import zipfile
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from . import config
from .models import Course

ASSETS_DIR = Path(__file__).resolve().parent / "scorm_assets"
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _course_title(course: Course) -> str:
    # Короткий заголовок берём из описания курса (первые слова) — можно расширить полем "title" позже.
    desc = course.setup.description.strip()
    return (desc[:70] + "…") if len(desc) > 70 else desc or "Курс"


def build_scorm_package(course: Course) -> Path:
    """Собирает SCORM 1.2 zip и возвращает путь к файлу."""
    build_dir = config.BUILDS_DIR / course.id
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    title = _course_title(course)

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
                            "content_html": ls.content_html,
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
        course_id=course.id, title=title
    )

    (build_dir / "player.html").write_text(player_html, encoding="utf-8")
    (build_dir / "imsmanifest.xml").write_text(manifest_xml, encoding="utf-8")
    shutil.copy(ASSETS_DIR / "player.css", build_dir / "player.css")
    shutil.copy(ASSETS_DIR / "player.js", build_dir / "player.js")
    shutil.copy(ASSETS_DIR / "scorm_api.js", build_dir / "scorm_api.js")

    zip_path = config.BUILDS_DIR / f"{course.id}.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in build_dir.iterdir():
            zf.write(f, arcname=f.name)

    return zip_path
