"""Подкаст к уроку: полный пересказ урока голосом диктора.

1. Текст урока (HTML) превращается в простой текст.
2. Модель переписывает его как речь для диктора (podcast_script): разговорно, без списков, аббревиатуры по слогам.
3. tts.synthesize() озвучивает скрипт, MP3 сохраняется в data/media и подключается к уроку.
"""
from __future__ import annotations
import hashlib
import re
from html.parser import HTMLParser

from . import config, llm, storage, tts
from .models import Course, Chapter, Lesson, Podcast

_BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "li", "blockquote", "tr", "br", "ul", "ol", "figure"}
_SKIP = {"video", "iframe", "script", "style", "figcaption"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skip += 1
        if tag in _BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def lesson_plain_text(html: str) -> str:
    """Текст урока без разметки; видео, картинки и подписи не входят."""
    p = _Text()
    p.feed(html or "")
    text = "".join(p.out)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


SCRIPT_SYSTEM_PROMPT = """Ты — автор аудиоматериалов для корпоративного обучения. Пишешь текст, который диктор \
прочитает вслух. Отвечай СТРОГО валидным JSON без пояснений и без markdown-разметки вне значений полей."""


def build_script_prompt(course: Course, chapter: Chapter, lesson: Lesson, text: str, target_words: int) -> str:
    return f"""Курс: {course.setup.description or course.title}
Аудитория: {course.setup.audience or "сотрудники компании"}
Глава: {chapter.title}
Урок: {lesson.title}

Текст урока:
{text}

Перепиши этот урок как подкаст: полный пересказ для слушателя, который не будет читать.

Правила:
- Перескажи ВСЁ содержание урока: все факты, шаги, условия, исключения, предупреждения. Ничего существенного не пропускай и ничего не придумывай. Порядок такой же, как в уроке.
- Говори от лица диктора, на «вы», живо и просто. Короткие фразы, до 20 слов. Между частями ставь связки: «Теперь перейдём к...», «Обратите внимание...».
- Начни с одной фразы о том, о чём этот урок. В конце коротко повтори 2-3 главные мысли.
- Это текст для озвучки: никаких списков, нумерации, таблиц, markdown, скобок, ссылок, эмодзи и заголовков. Перечисления передавай словами («сначала», «затем», «после этого»), шаги тоже.
- Числа, даты и единицы пиши словами так, как их читают вслух. Аббревиатуры и латиницу пиши так, как они звучат по-русски (например, «PLM» как «пи эл эм»).
- Не ссылайся на рисунки и скриншоты («как видно на рисунке»). Если на них важная информация, передай её словами.
- Объём: около {target_words} слов.

Формат ответа (строго JSON):
{{"script": "Текст для диктора одним связным текстом, абзацы разделяй пустой строкой."}}
"""


def generate_script(course: Course, chapter: Chapter, lesson: Lesson, text: str) -> str:
    words = len(text.split())
    target = max(250, min(1800, int(words * 1.15)))
    max_tokens = min(8000, target * 3 + 600)
    prompt = build_script_prompt(course, chapter, lesson, text, target)
    raw = llm.call_llm(SCRIPT_SYSTEM_PROMPT, prompt, temperature=0.5, max_tokens=max_tokens)
    data = llm.extract_json(raw)
    script = str(data.get("script", "")).strip() if isinstance(data, dict) else ""
    # на случай, если модель всё же оставила разметку
    script = re.sub(r"<[^>]+>", "", script)
    script = re.sub(r"[*_#`]+", "", script)
    if len(script.split()) < 30:
        raise llm.LLMError("Модель вернула слишком короткий текст подкаста. Попробуйте ещё раз.")
    return script


def _find(course: Course, lesson_id: str):
    for ch in course.chapters:
        for ls in ch.lessons:
            if ls.id == lesson_id:
                return ch, ls
    return None, None


def _remove_audio(url: str) -> None:
    m = re.fullmatch(r"/media/([0-9a-f]{12,32}\.mp3)", url or "")
    if m:
        (config.MEDIA_DIR / m.group(1)).unlink(missing_ok=True)


def generate_and_commit(course_id: str, lesson_id: str, stage: str = "all"):
    """stage="all": новый скрипт и озвучка; stage="audio": озвучить уже имеющийся скрипт (например, после правки).
    Долгие запросы идут без блокировки хранилища, результат записывается в свежую копию курса."""
    course = storage.load_course(course_id)
    chapter, lesson = _find(course, lesson_id)
    if lesson is None:
        return None
    text = lesson_plain_text(lesson.content_html)
    old = lesson.podcast or Podcast()

    if stage == "audio" and old.script.strip():
        script, lesson_hash = old.script, old.lesson_hash
    else:
        if len(text.split()) < 15:
            raise ValueError("В уроке пока нет текста. Сначала напишите или сгенерируйте урок.")
        script, lesson_hash = generate_script(course, chapter, lesson, text), text_hash(text)

    data = tts.synthesize(script)
    url = tts.save_mp3(data)
    seconds = tts.mp3_seconds(data)

    with storage.LOCK:
        fresh = storage.load_course(course_id)
        _, target = _find(fresh, lesson_id)
        if target is None:  # урок удалили во время озвучки
            _remove_audio(url)
            return None
        prev = target.podcast
        if prev and prev.audio:
            _remove_audio(prev.audio)
        edited_meanwhile = bool(prev and prev.script.strip() and prev.script != script and stage == "audio")
        target.podcast = Podcast(
            script=script, audio=url, seconds=seconds, lesson_hash=lesson_hash,
            text_changed=text_hash(lesson_plain_text(target.content_html)) != lesson_hash,
            audio_outdated=edited_meanwhile,
        )
        storage.save_course(fresh)
        return target


def remove(course_id: str, lesson_id: str):
    with storage.LOCK:
        course = storage.load_course(course_id)
        _, lesson = _find(course, lesson_id)
        if lesson is None:
            return None
        if lesson.podcast and lesson.podcast.audio:
            _remove_audio(lesson.podcast.audio)
        lesson.podcast = None
        storage.save_course(course)
        return lesson
