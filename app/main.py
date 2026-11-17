from __future__ import annotations
import itertools
import random
import re
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import quote
import logging
import secrets

from . import auth, cleanup, config, storage
from .file_parser import extract_text
from .images import ImageCollector
from .models import Share, Course, CourseSetupRequest, StructureReorderRequest, new_id, QUIZ_TYPES
from .generator import generate_structure, generate_lesson_content, generate_chapter_cover
from . import jobs, media, podcast, tts
from .scorm_builder import build_scorm_package
from . import llm as llm_module
from .llm import LLMError

app = FastAPI(title="SCORM Course Builder")
app.include_router(auth.router)
app.include_router(media.router)


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Без входа доступны только адреса /auth/*. На /api и /media без входа отвечаем 401, на остальные страницы перенаправляем на вход."""
    path = request.url.path
    if path.startswith("/auth/"):
        return await call_next(request)
    user = auth.current_user(request)
    if not user:
        if path.startswith("/api/") or path.startswith("/media/"):
            return JSONResponse({"detail": "Требуется вход"}, status_code=401)
        target = path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse("/auth/login?next=" + quote(target))
    request.state.user = user
    request.state.access = "admin" if auth.is_admin(user) else None
    denied = auth.check_request(request, user)
    if denied is not None:
        return denied
    return await call_next(request)


# SessionMiddleware добавляется после auth_gate, поэтому выполняется раньше него и сессия там уже доступна
_secret = config.SESSION_SECRET
if auth.enabled() and not _secret:
    _secret = secrets.token_urlsafe(48)
    logging.getLogger("auth").warning("SESSION_SECRET не задан: сессии будут сбрасываться при перезапуске. Задайте длинную случайную строку.")
app.add_middleware(SessionMiddleware, secret_key=_secret or "off", max_age=config.SESSION_HOURS * 3600,
                   same_site="lax", https_only=config.OIDC_REDIRECT_URI.startswith("https://"))
if not auth.enabled():
    logging.getLogger("auth").warning("AUTH_MODE=off: вход отключён, доступ открыт всем. Только для разработки!")
elif config.OIDC_ISSUER and not (config.OIDC_CLIENT_ID and config.OIDC_CLIENT_SECRET):
    logging.getLogger("auth").warning("OIDC_CLIENT_ID / OIDC_CLIENT_SECRET не заданы: вход не заработает.")
if auth.enabled():
    _n = storage.assign_legacy_owner()
    if _n:
        logging.getLogger("auth").info("Курсам без владельца назначен владелец: %d", _n)


# ---------- 1. Загрузка исходных файлов ----------

def _process_upload(upload_id: str, dest: Path, filename: str) -> dict:
    """Извлекает текст из собранного файла и сохраняет его для последующей генерации."""
    collector = ImageCollector()
    try:
        text, total_chars = extract_text(dest, collector)
    except Exception as e:
        raise HTTPException(400, f"Не удалось обработать файл: {e}")
    if not text.strip():
        raise HTTPException(400, "В файле не найден текст. Возможно, это скан или картинки: нужен документ с текстовым слоем.")

    # картинки, чьи пометки обрезаны вместе с хвостом документа, не нужны
    kept = [it for it in collector.items if f"[[IMG:{it['n']}]]" in text]
    storage.save_upload_images(upload_id, kept)  # раньше текста: по времени изменения текста обновляется кэш
    storage.save_upload_text(upload_id, text)
    return {
        "upload_id": upload_id, "filename": filename,
        "chars_extracted": len(text), "chars_total": total_chars,
        "truncated": total_chars > len(text),
        "images": len(kept),
    }


def _check_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in (".ppt", ".doc"):
        new = suffix + "x"
        raise HTTPException(400, f"Старый формат {suffix.upper()} не поддерживается. Откройте файл в Office, выберите «Сохранить как» и сохраните как {new.upper()}.")
    if suffix not in (".docx", ".pptx", ".pdf"):
        raise HTTPException(400, "Поддерживаются только DOCX, PPTX и PDF.")
    return suffix


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Загрузка одним запросом (для небольших файлов и скриптов)."""
    suffix = _check_suffix(file.filename)
    upload_id = new_id()
    dest = config.UPLOADS_DIR / f"{upload_id}{suffix}"
    content = await file.read()
    if len(content) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(400, f"Файл больше {config.MAX_UPLOAD_MB} МБ.")
    dest.write_bytes(content)
    return _process_upload(upload_id, dest, file.filename)


# Загрузка по частям: браузер режет файл на куски по 512 КБ, и каждый проходит через прокси отдельным запросом.
# Так большие файлы не упираются в ограничение размера запроса на прокси (Amvera, корпоративный шлюз).

_ID_RE = re.compile(r"^[0-9a-f]{12,32}$")
MAX_CHUNK_BYTES = 2 * 1024 * 1024


@app.post("/api/upload/chunk")
async def upload_chunk(request: Request, upload_id: str, index: int, total: int):
    if not _ID_RE.match(upload_id) or index < 0 or total < 1 or index >= total or total > 400:
        raise HTTPException(400, "Некорректные параметры загрузки.")
    data = await request.body()
    if len(data) > MAX_CHUNK_BYTES:
        raise HTTPException(400, "Часть файла слишком велика.")
    (config.UPLOADS_DIR / f"{upload_id}.part{index}").write_bytes(data)
    return {"ok": True}


@app.post("/api/upload/complete")
def upload_complete(payload: dict):
    upload_id = str(payload.get("upload_id", ""))
    filename = str(payload.get("filename", ""))
    total = int(payload.get("total", 0))
    if not _ID_RE.match(upload_id) or total < 1 or total > 400:
        raise HTTPException(400, "Некорректные параметры загрузки.")
    suffix = _check_suffix(filename)
    parts = [config.UPLOADS_DIR / f"{upload_id}.part{i}" for i in range(total)]
    try:
        missing = [p for p in parts if not p.exists()]
        if missing:
            raise HTTPException(400, f"Загружены не все части файла ({total - len(missing)} из {total}). Повторите загрузку.")
        size = sum(p.stat().st_size for p in parts)
        if size > config.MAX_UPLOAD_MB * 1024 * 1024:
            raise HTTPException(400, f"Файл больше {config.MAX_UPLOAD_MB} МБ.")
        dest = config.UPLOADS_DIR / f"{upload_id}{suffix}"
        with open(dest, "wb") as out:
            for p in parts:
                out.write(p.read_bytes())
    finally:
        for p in parts:
            p.unlink(missing_ok=True)
    return _process_upload(upload_id, dest, filename)


# ---------- 2. Генерация структуры курса (экран 1 -> экран 2) ----------

@app.post("/api/courses")
def create_course(setup: CourseSetupRequest, request: Request):
    chunks = storage.course_chunks(setup.upload_ids)
    if not chunks and not setup.description.strip():
        raise HTTPException(400, "Загрузите файл или опишите курс словами.")
    try:
        course = generate_structure(setup, chunks)
    except LLMError as e:
        raise HTTPException(502, f"Ошибка генерации структуры: {e}")
    user = request.state.user
    course.owner, course.owner_name = user["email"], user.get("name", "")
    storage.save_course(course)
    return _course_payload(course, "owner" if not auth.is_admin(user) else "admin")


def _course_payload(course, access: str | None) -> dict:
    """Курс для интерфейса: с ролью текущего пользователя; список доступа видят только владелец и администратор."""
    data = course.model_dump()
    data["access"] = access
    if access not in ("owner", "admin"):
        data.pop("shares", None)
    return data


@app.get("/api/courses")
def list_courses(request: Request):
    user = request.state.user
    out = []
    for item in storage.list_courses():
        class _C:  # лёгкая обёртка над сводкой для проверки прав
            owner = item["owner"]
            shares = [type("S", (), s) for s in item["shares"]]
        access = auth.course_access(_C, user)
        if access is None:
            continue
        item = dict(item, access=access)
        if access not in ("owner", "admin"):
            item.pop("shares", None)
        out.append(item)
    return out


@app.delete("/api/courses/{course_id}")
def delete_course(course_id: str):
    storage.delete_course(course_id)
    return {"ok": True}


@app.get("/api/courses/{course_id}")
def get_course(course_id: str, request: Request):
    try:
        course = storage.load_course(course_id)
    except FileNotFoundError:
        raise HTTPException(404, "Курс не найден")
    return _course_payload(course, getattr(request.state, "access", None))


@app.get("/api/courses/{course_id}/shares")
def get_shares(course_id: str):
    course = storage.load_course(course_id)
    return {"owner": course.owner, "owner_name": course.owner_name,
            "shares": [{"email": x.email, "role": x.role} for x in course.shares]}


@app.put("/api/courses/{course_id}/shares")
def put_shares(course_id: str, payload: dict):
    with storage.LOCK:
        course = storage.load_course(course_id)
        try:
            items = auth.normalize_shares(payload.get("shares"), course.owner)
        except ValueError as e:
            raise HTTPException(400, str(e))
        course.shares = [Share(**x) for x in items]
        storage.save_course(course)
    return {"shares": items}


# ---------- 3. Редактирование структуры (переместить/удалить главы и уроки) ----------

@app.put("/api/courses/{course_id}/structure")
def update_structure(course_id: str, payload: StructureReorderRequest):
    if jobs.any_running(course_id):
        raise HTTPException(409, "Идёт генерация уроков или подкастов. Дождитесь окончания или остановите её.")
    with storage.LOCK:
        course = storage.load_course(course_id)
        course.chapters = payload.chapters
        if payload.title is not None:
            course.title = payload.title.strip()
        storage.save_course(course)
        return course


@app.post("/api/courses/{course_id}/confirm-structure")
def confirm_structure(course_id: str):
    with storage.LOCK:
        course = storage.load_course(course_id)
        course.status = "structure_confirmed"
        storage.save_course(course)
        return course


# ---------- 4. Генерация контента уроков (экран 3) ----------

@app.post("/api/courses/{course_id}/lessons/{lesson_id}/generate")
def generate_lesson(course_id: str, lesson_id: str):
    try:
        lesson = jobs.generate_and_commit(course_id, lesson_id, random.choice(QUIZ_TYPES))
    except FileNotFoundError:
        raise HTTPException(404, "Курс не найден")
    except LLMError as e:
        raise HTTPException(502, f"Ошибка генерации урока: {e}")
    if lesson is None:
        raise HTTPException(404, "Урок не найден")
    return lesson


# Генерация всех уроков идёт в фоне: запрос сразу возвращается, прогресс запрашивается отдельно.

@app.post("/api/courses/{course_id}/generate-all")
def generate_all_lessons(course_id: str):
    try:
        return jobs.start_generate_all(course_id)
    except FileNotFoundError:
        raise HTTPException(404, "Курс не найден")


@app.get("/api/courses/{course_id}/generate-all/status")
def generate_all_status(course_id: str):
    return jobs.status(course_id)


@app.post("/api/courses/{course_id}/generate-all/cancel")
def generate_all_cancel(course_id: str):
    return jobs.cancel(course_id)


@app.put("/api/courses/{course_id}/lessons/{lesson_id}")
def update_lesson(course_id: str, lesson_id: str, payload: dict):
    with storage.LOCK:
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
        pod = lesson.podcast
        if pod:
            if "content_html" in payload:  # текст урока изменился: подкаст может не совпадать с ним
                pod.text_changed = podcast.text_hash(podcast.lesson_plain_text(lesson.content_html)) != pod.lesson_hash
            if "podcast_script" in payload and payload["podcast_script"] != pod.script:
                pod.script = str(payload["podcast_script"])
                pod.audio_outdated = bool(pod.audio)

        storage.save_course(course)
        return lesson


# ---------- 4а. Подкасты: полный пересказ урока голосом диктора ----------

@app.get("/api/podcast/info")
def podcast_info():
    return {"configured": tts.is_configured(), "provider": tts.provider() or None}


@app.post("/api/courses/{course_id}/podcasts")
def start_podcasts(course_id: str, payload: dict):
    """payload: {"lesson_ids": [...] | null, "stage": "all" | "audio"}. Без lesson_ids берутся все уроки без подкаста."""
    if not tts.is_configured():
        raise HTTPException(400, "Озвучка не настроена на сервере: задайте TTS_PROVIDER (см. README).")
    stage = "audio" if payload.get("stage") == "audio" else "all"
    ids = payload.get("lesson_ids")
    try:
        return jobs.start_podcasts(course_id, ids if isinstance(ids, list) else None, stage)
    except FileNotFoundError:
        raise HTTPException(404, "Курс не найден")


@app.get("/api/courses/{course_id}/podcasts/status")
def podcasts_status(course_id: str):
    return jobs.status(course_id, "podcast")


@app.post("/api/courses/{course_id}/podcasts/cancel")
def podcasts_cancel(course_id: str):
    return jobs.cancel(course_id, "podcast")


@app.delete("/api/courses/{course_id}/lessons/{lesson_id}/podcast")
def delete_podcast(course_id: str, lesson_id: str):
    lesson = podcast.remove(course_id, lesson_id)
    if lesson is None:
        raise HTTPException(404, "Урок не найден")
    return lesson


# ---------- 4б. Обложки глав (YandexART) ----------

@app.post("/api/courses/{course_id}/chapters/{chapter_id}/generate-cover")
def generate_cover(course_id: str, chapter_id: str):
    with storage.LOCK:
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
    with storage.LOCK:
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
    with storage.LOCK:
        course = storage.load_course(course_id)
        try:
            zip_path = build_scorm_package(course)
        except ValueError as e:  # например, видео удалено с сервера
            raise HTTPException(400, str(e))
        course.status = "built"
        storage.save_course(course)
        return {"download_url": f"/api/courses/{course_id}/download",
                "size_mb": round(zip_path.stat().st_size / 1024 / 1024, 1), "limit_mb": config.SCORM_MAX_MB}


@app.get("/api/courses/{course_id}/download")
def download_course(course_id: str):
    zip_path = config.BUILDS_DIR / f"{course_id}.zip"
    if not zip_path.exists():
        raise HTTPException(404, "Пакет ещё не собран")
    return FileResponse(zip_path, media_type="application/zip", filename=f"course_{course_id}.zip")


# ---------- Превью урока (тот же плеер, что попадает в SCORM) ----------

PREVIEW_EXTRA_CSS = """<style>html,body{margin:0}:root{--lms-top:0px}</style>"""


@app.get("/api/courses/{course_id}/preview/{lesson_id}", response_class=HTMLResponse)
def preview_lesson(course_id: str, lesson_id: str):
    import json
    from .scorm_builder import _env

    try:
        course = storage.load_course(course_id)
    except FileNotFoundError:
        raise HTTPException(404, "Курс не найден")
    chapter = next((c for c in course.chapters for l in c.lessons if l.id == lesson_id), None)
    if not chapter:
        raise HTTPException(404, "Урок не найден")
    lesson = next(l for l in chapter.lessons if l.id == lesson_id)
    data = {
        "title": course.title or "Курс",
        "chapters": [{
            "id": chapter.id, "title": chapter.title, "cover_image": None,
            "lessons": [{"id": lesson.id, "title": lesson.title,
                         "content_html": lesson.content_html, "quizzes": lesson.quizzes,
                         "podcast": ({"audio": lesson.podcast.audio, "seconds": lesson.podcast.seconds}
                                     if lesson.podcast and lesson.podcast.audio else None)}],
        }],
    }
    html = _env.get_template("player.html.j2").render(
        title=course.title or "Курс", lang=course.setup.language,
        course_json=json.dumps(data, ensure_ascii=False).replace("</", "<\\/"),
    )
    html = html.replace('href="player.css"', 'href="/preview-assets/player.css"')
    html = html.replace('src="scorm_api.js"', 'src="/preview-assets/scorm_api.js"')
    html = html.replace('src="player.js"', 'src="/preview-assets/player.js"')
    html = html.replace("</head>", PREVIEW_EXTRA_CSS + "</head>")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/preview-assets/player.css")
def preview_player_css():
    from fastapi.responses import Response
    base = Path(__file__).resolve().parent / "scorm_assets"
    css = (base / "player.css").read_text(encoding="utf-8") + "\n" + (base / "lesson_content.css").read_text(encoding="utf-8")
    return Response(css, media_type="text/css")


@app.get("/preview-assets/{name}")
def preview_asset(name: str):
    if name not in ("player.js", "scorm_api.js"):
        raise HTTPException(404)
    return FileResponse(Path(__file__).resolve().parent / "scorm_assets" / name, media_type="application/javascript")


# ---------- Общие стили текста урока (те же, что попадают в SCORM-пакет) ----------

@app.get("/lesson_content.css")
def lesson_content_css():
    css_path = Path(__file__).resolve().parent / "scorm_assets" / "lesson_content.css"
    return FileResponse(css_path, media_type="text/css")


# ---------- Фронтенд (статика) ----------

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.get("/api/admin/cleanup")
def cleanup_preview(request: Request):
    """Показывает, сколько неиспользуемых файлов можно удалить. Только администратор."""
    if not auth.is_admin(request.state.user):
        raise HTTPException(403, "Только для администраторов.")
    try:
        return cleanup.report(cleanup.scan())
    except cleanup.CleanupError as e:
        raise HTTPException(409, str(e))


@app.post("/api/admin/cleanup")
def cleanup_run(request: Request):
    if not auth.is_admin(request.state.user):
        raise HTTPException(403, "Только для администраторов.")
    try:
        items = cleanup.scan()
    except cleanup.CleanupError as e:
        raise HTTPException(409, str(e))
    return cleanup.report(items, deleted=cleanup.apply(items))


@app.get("/api/llm/usage")
def llm_usage():
    """Сколько запросов и токенов ушло к модели с запуска приложения (для расчёта нагрузки)."""
    return llm_module.usage()


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

