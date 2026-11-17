"""Фоновая генерация уроков.

Задачи живут в памяти процесса (один поток на курс). Этого достаточно для одного экземпляра приложения;
для нескольких экземпляров и устойчивости к перезапускам понадобится настоящая очередь (Redis + воркеры).
После перезапуска контейнера задача прерывается, уже готовые уроки сохранены на диске.
"""
from __future__ import annotations
import itertools
import re
import threading
import time

from . import podcast, storage
from .generator import generate_lesson_content
from .media import MEDIA_REF_RE
from .models import QUIZ_TYPES

# Блок видео из редактора: <div class="video-block" ...><video|iframe ...></video|iframe></div> (вложенных div нет)
VIDEO_BLOCK_RE = re.compile(
    r'(?:<div class="video-block[^"]*"[^>]*>.*?</div>|<figure class="lesson-image" data-manual[^>]*>.*?</figure>)', re.S)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _find_lesson(course, lesson_id):
    for ch in course.chapters:
        for ls in ch.lessons:
            if ls.id == lesson_id:
                return ch, ls
    return None, None


def generate_and_commit(course_id: str, lesson_id: str, quiz_type: str | None):
    """Генерирует урок без блокировки хранилища (запрос к модели долгий), а результат записывает
    в свежую копию курса под замком, чтобы не затереть правки, которые пользователь внёс за это время."""
    course = storage.load_course(course_id)
    chapter, lesson = _find_lesson(course, lesson_id)
    if lesson is None:
        return None
    chunks = storage.course_chunks(course.setup.upload_ids)
    images = storage.course_images(course.setup.upload_ids)
    # картинки, которые уже стоят в других уроках, второй раз не вставляем
    used = {m.group(1) + "." + m.group(2) for ch in course.chapters for ls in ch.lessons if ls.id != lesson_id
            for m in MEDIA_REF_RE.finditer(ls.content_html or "")}
    generate_lesson_content(course, chapter, lesson, chunks, preferred_quiz_type=quiz_type, images=images, used_files=used)

    with storage.LOCK:
        fresh = storage.load_course(course_id)
        _, target = _find_lesson(fresh, lesson_id)
        if target is None:  # урок удалили, пока он генерировался
            return None
        # видео и картинки, добавленные автором вручную, не должны пропасть при перегенерации: переносим в конец нового текста
        kept = VIDEO_BLOCK_RE.findall(target.content_html or "")
        target.content_html = lesson.content_html + "".join(k for k in kept if k not in lesson.content_html)
        target.quizzes = lesson.quizzes
        target.generated = True
        storage.save_course(fresh)
        return target


def _public(job: dict) -> dict:
    return {
        "status": job["status"],          # running / done / cancelled
        "total": job["total"],
        "done": job["done"],              # обработано уроков (включая неудачные)
        "failed": len(job["errors"]),
        "errors": job["errors"][:5],
        "current": job["current"],
        "current_id": job.get("current_id"),
    }


def _key(course_id: str, kind: str) -> str:
    return course_id if kind == "lessons" else f"{course_id}:{kind}"


def status(course_id: str, kind: str = "lessons") -> dict:
    with _jobs_lock:
        job = _jobs.get(_key(course_id, kind))
        return _public(job) if job else {"status": "idle", "total": 0, "done": 0, "failed": 0, "errors": [], "current": None}


def cancel(course_id: str, kind: str = "lessons") -> dict:
    with _jobs_lock:
        job = _jobs.get(_key(course_id, kind))
        if job and job["status"] == "running":
            job["cancel"] = True
    return status(course_id, kind)


def any_running(course_id: str) -> bool:
    return status(course_id)["status"] == "running" or status(course_id, "podcast")["status"] == "running"


def start_generate_all(course_id: str) -> dict:
    """Запускает генерацию всех ещё не написанных уроков. Если задача уже идёт, возвращает её состояние."""
    with _jobs_lock:
        job = _jobs.get(course_id)
        if job and job["status"] == "running":
            return _public(job)

        course = storage.load_course(course_id)
        type_cycle = itertools.cycle(QUIZ_TYPES)  # чередуем механики заданий по всему курсу
        queue = []
        for ch in course.chapters:
            for ls in ch.lessons:
                quiz_type = next(type_cycle)
                if not ls.generated and not ls.content_html.strip():  # уроки с текстом автора не трогаем
                    queue.append((ls.id, ls.title, quiz_type))

        job = {"status": "running" if queue else "done", "total": len(queue), "done": 0,
               "errors": [], "current": None, "cancel": False, "started": time.time()}
        _jobs[course_id] = job

    if queue:
        threading.Thread(target=_run, args=(queue, job, lambda it: generate_and_commit(course_id, it[0], it[2])), daemon=True).start()
    return _public(job)


def start_podcasts(course_id: str, lesson_ids: list[str] | None = None, stage: str = "all") -> dict:
    """Подкасты в фоне. lesson_ids=None: все уроки с текстом, у которых подкаста ещё нет или текст урока изменился."""
    key = _key(course_id, "podcast")
    with _jobs_lock:
        job = _jobs.get(key)
        if job and job["status"] == "running":
            return _public(job)

        course = storage.load_course(course_id)
        queue = []
        for ch in course.chapters:
            for ls in ch.lessons:
                if lesson_ids is not None:
                    if ls.id in lesson_ids:
                        queue.append((ls.id, ls.title))
                    continue
                has_text = len(podcast.lesson_plain_text(ls.content_html).split()) >= 15
                pod = ls.podcast
                if has_text and (not pod or not pod.audio or pod.text_changed):
                    queue.append((ls.id, ls.title))

        job = {"status": "running" if queue else "done", "total": len(queue), "done": 0,
               "errors": [], "current": None, "cancel": False, "started": time.time()}
        _jobs[key] = job

    if queue:
        threading.Thread(target=_run, args=(queue, job, lambda it: podcast.generate_and_commit(course_id, it[0], stage)), daemon=True).start()
    return _public(job)


def _run(queue: list, job: dict, fn) -> None:
    try:
        for item in queue:
            lesson_id, title = item[0], item[1]
            if job["cancel"]:
                job["status"] = "cancelled"
                return
            job["current"] = title
            job["current_id"] = lesson_id
            try:
                fn(item)
            except Exception as e:  # ошибка одного урока не должна останавливать остальные
                job["errors"].append({"lesson_id": lesson_id, "title": title, "error": str(e)[:300]})
            job["done"] += 1
        job["status"] = "done"
    except Exception as e:  # например, курс удалён во время работы
        job["errors"].append({"lesson_id": None, "title": "", "error": str(e)[:300]})
        job["status"] = "done"
    finally:
        job["current"] = None
        job["current_id"] = None
