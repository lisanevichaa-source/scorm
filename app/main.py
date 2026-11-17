from __future__ import annotations
import itertools
import random
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, storage
from .file_parser import extract_text
from .models import Course, CourseSetupRequest, StructureReorderRequest, new_id, QUIZ_TYPES
from .generator import generate_structure, generate_lesson_content, generate_chapter_cover
from .scorm_builder import build_scorm_package
from .yandex_gpt import YandexGPTError

app = FastAPI(title="SCORM Course Builder")


# ---------- 1. Загрузка исходных файлов ----------

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    suffix = Path(file.filename).suffix.lower()
    if suffix not in (".docx", ".pptx", ".pdf"):
        raise HTTPException(400, "Поддерживаются только DOCX, PPTX и PDF.")

    upload_id = new_id()
    dest = config.UPLOADS_DIR / f"{upload_id}{suffix}"
    content = await file.read()
    if len(content) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"Файл больше {config.MAX_UPLOAD_MB} МБ.")
    dest.write_bytes(content)

    try:
        text = extract_text(dest)
    except Exception as e:
        raise HTTPException(400, f"Не удалось обработать файл: {e}")

    storage.save_upload_text(upload_id, text)
    return {"upload_id": upload_id, "filename": file.filename, "chars_extracted": len(text)}


# ---------- 2. Генерация структуры курса (экран 1 -> экран 2) ----------

@app.post("/api/courses")
def create_course(setup: CourseSetupRequest):
    source_text = storage.combined_source_text(setup.upload_ids)
    try:
        course = generate_structure(setup, source_text)
    except YandexGPTError as e:
        raise HTTPException(502, f"Ошибка генерации структуры: {e}")
    storage.save_course(course)
    return course


@app.get("/api/courses/{course_id}")
def get_course(course_id: str):
    try:
        return storage.load_course(course_id)
    except FileNotFoundError:
        raise HTTPException(404, "Курс не найден")


# ---------- 3. Редактирование структуры (переместить/удалить главы и уроки) ----------

@app.put("/api/courses/{course_id}/structure")
def update_structure(course_id: str, payload: StructureReorderRequest):
    course = storage.load_course(course_id)
    course.chapters = payload.chapters
    storage.save_course(course)
    return course


@app.post("/api/courses/{course_id}/confirm-structure")
def confirm_structure(course_id: str):
    course = storage.load_course(course_id)
    course.status = "structure_confirmed"
    storage.save_course(course)
    return course


# ---------- 4. Генерация контента уроков (экран 3) ----------

@app.post("/api/courses/{course_id}/lessons/{lesson_id}/generate")
def generate_lesson(course_id: str, lesson_id: str):
    course = storage.load_course(course_id)
    source_text = storage.combined_source_text(course.setup.upload_ids)

    chapter = next((c for c in course.chapters for l in c.lessons if l.id == lesson_id), None)
    if not chapter:
        raise HTTPException(404, "Урок не найден")
    lesson = next(l for l in chapter.lessons if l.id == lesson_id)

    try:
        generate_lesson_content(course, chapter, lesson, source_text, preferred_quiz_type=random.choice(QUIZ_TYPES))
    except YandexGPTError as e:
        raise HTTPException(502, f"Ошибка генерации урока: {e}")

    storage.save_course(course)
    return lesson


@app.post("/api/courses/{course_id}/generate-all")
def generate_all_lessons(course_id: str):
    course = storage.load_course(course_id)
    source_text = storage.combined_source_text(course.setup.upload_ids)
    errors = []
    type_cycle = itertools.cycle(QUIZ_TYPES)  # чередуем механики квизов по всему курсу
    for chapter in course.chapters:
        for lesson in chapter.lessons:
            quiz_type = next(type_cycle)
            if lesson.generated:
                continue
            try:
                generate_lesson_content(course, chapter, lesson, source_text, preferred_quiz_type=quiz_type)
            except YandexGPTError as e:
                errors.append({"lesson_id": lesson.id, "error": str(e)})
    storage.save_course(course)
    return {"course": course, "errors": errors}


@app.put("/api/courses/{course_id}/lessons/{lesson_id}")
def update_lesson(course_id: str, lesson_id: str, payload: dict):
    course = storage.load_course(course_id)
    chapter = next((c for c in course.chapters for l in c.lessons if l.id == lesson_id), None)
    if not chapter:
        raise HTTPException(404, "Урок не найден")
    lesson = next(l for l in chapter.lessons if l.id == lesson_id)

    if "content_html" in payload:
        lesson.content_html = payload["content_html"]
    if "quizzes" in payload:
        lesson.quizzes = payload["quizzes"]
    if "title" in payload:
        lesson.title = payload["title"]

    storage.save_course(course)
    return lesson


# ---------- 4б. Обложки глав (YandexART) ----------

@app.post("/api/courses/{course_id}/chapters/{chapter_id}/generate-cover")
def generate_cover(course_id: str, chapter_id: str):
    course = storage.load_course(course_id)
    chapter = next((c for c in course.chapters if c.id == chapter_id), None)
    if not chapter:
        raise HTTPException(404, "Глава не найдена")
    try:
        generate_chapter_cover(course, chapter)
    except Exception as e:
        raise HTTPException(500, f"Ошибка генерации обложки: {e}")
    storage.save_course(course)
    return chapter


@app.post("/api/courses/{course_id}/generate-all-covers")
def generate_all_covers(course_id: str):
    course = storage.load_course(course_id)
    errors = []
    for chapter in course.chapters:
        if chapter.cover_image:
            continue
        try:
            generate_chapter_cover(course, chapter)
        except Exception as e:
            errors.append({"chapter_id": chapter.id, "error": str(e)})
    storage.save_course(course)
    return {"course": course, "errors": errors}


# ---------- 5. Сборка SCORM-пакета ----------

@app.post("/api/courses/{course_id}/build")
def build_course(course_id: str):
    course = storage.load_course(course_id)
    zip_path = build_scorm_package(course)
    course.status = "built"
    storage.save_course(course)
    return {"download_url": f"/api/courses/{course_id}/download"}


@app.get("/api/courses/{course_id}/download")
def download_course(course_id: str):
    zip_path = config.BUILDS_DIR / f"{course_id}.zip"
    if not zip_path.exists():
        raise HTTPException(404, "Пакет ещё не собран")
    return FileResponse(zip_path, media_type="application/zip", filename=f"course_{course_id}.zip")


# ---------- Фронтенд (статика) ----------

STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
