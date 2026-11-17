from __future__ import annotations
import base64
from .models import Course, Chapter, Lesson, CourseSetupRequest
from .chunking import Chunk
from .images import place_images
from . import chunking
from . import config
from . import llm
from . import cover_icons
from . import cover_builder

# Подробность уроков: (слов в тексте урока, число проверочных заданий, лимит ответа модели в токенах)
SCOPE_HINT = {
    "brief": "Основное: только ключевые темы, без второстепенных деталей. 2-3 главы, всего 4-6 уроков.",
    "standard": "Стандартный курс: 3-5 глав, всего 8-14 уроков.",
    "full": (
        "Полный охват: охвати ВЕСЬ исходный материал, ничего существенного не пропускай. Главы и уроки должны "
        "следовать разделам и подразделам исходных материалов по порядку (по оглавлению и заголовкам, если они есть). "
        "Число глав и уроков определяется содержанием: обычно 5-10 глав и 15-40 уроков."
    ),
}

LESSON_DETAIL = {
    "brief": ("150-250", 1, 3000),
    "standard": ("300-500", 1, 4500),
    "full": ("500-800", 2, 7000),
}
DEFAULT_DETAIL = "standard"

STRUCTURE_SYSTEM_PROMPT = """Ты — методист, который проектирует структуру короткого учебного курса \
для сотрудников компании. Отвечай СТРОГО валидным JSON без пояснений и без markdown-разметки."""

STRUCTURE_JSON_SCHEMA_HINT = """
Формат ответа (строго JSON, без комментариев):
{
  "course_title": "Короткое название курса (до 60 символов)",
  "topic": "Тема курса одним предложением",
  "audience": "Для кого курс, одним предложением",
  "chapters": [
    {
      "title": "Название главы",
      "lessons": [
        {"title": "Название урока"}
      ]
    }
  ]
}
"""

# Когда есть исходный документ, у каждого урока просим номера фрагментов, на которых он основан
STRUCTURE_JSON_SCHEMA_HINT_WITH_CHUNKS = STRUCTURE_JSON_SCHEMA_HINT.replace(
    '{"title": "Название урока"}', '{"title": "Название урока", "chunks": [3, 4]}'
)

CHUNKS_HINT = """Исходные материалы разбиты на пронумерованные фрагменты вида [#номер Заголовок, место]. \
По этим фрагментам позже будет написан текст каждого урока, поэтому у каждого урока в поле "chunks" укажи номера \
фрагментов, на которых он основан (обычно от 1 до 4, подряд идущие). Номера бери только из списка выше. \
Уроки и главы располагай в порядке материала."""

CHUNKS_HINT_FULL = " Каждый фрагмент должен попасть хотя бы в один урок: ничего существенного не теряй."

DIGEST_NOTE = ("Документ большой, поэтому у каждого фрагмента показано только его начало; "
               "сами фрагменты длиннее и будут целиком переданы при написании уроков.")


def _budget(limit: int) -> int:
    """Бюджет символов: настройка раздела, но не больше общего LLM_MAX_SOURCE_CHARS (если он задан)."""
    from . import config
    cap = config.LLM_MAX_SOURCE_CHARS
    return min(limit, cap) if cap else limit


def build_structure_prompt(setup: CourseSetupRequest, chunks: list[Chunk]) -> str:
    parts = []
    if setup.description:
        parts.append(f"Описание курса: {setup.description}")
    else:
        parts.append("Описание курса не задано: определи тему курса сам по исходным материалам.")
    if setup.audience:
        parts.append(f"Целевая аудитория: {setup.audience}")
    else:
        parts.append("Целевая аудитория не задана: определи её по материалам (сотрудники компании).")
    parts += [
        f"Охват курса: {SCOPE_HINT.get(setup.scope, SCOPE_HINT['standard'])}",
        f"Язык курса: {setup.language}",
    ]
    if setup.desired_structure:
        parts.append(
            f"Сотрудник предложил свой порядок тем — максимально следуй ему:\n{setup.desired_structure}"
        )
    if chunks:
        text, shortened = chunking.digest(chunks, _budget(config.STRUCTURE_SOURCE_CHARS))
        parts.append(f"Исходные материалы (используй как основу содержания):\n{text}")
        if shortened:
            parts.append(DIGEST_NOTE)
        parts.append(CHUNKS_HINT + (CHUNKS_HINT_FULL if setup.scope == "full" else ""))
        parts.append(STRUCTURE_JSON_SCHEMA_HINT_WITH_CHUNKS)
    else:
        parts.append(STRUCTURE_JSON_SCHEMA_HINT)
    return "\n\n".join(parts)


def _clean_refs(raw, valid: set[int]) -> list[int]:
    out: list[int] = []
    for r in raw if isinstance(raw, list) else []:
        try:
            r = int(str(r).lstrip("#"))
        except ValueError:
            continue
        if r in valid and r not in out:
            out.append(r)
    return sorted(out)


def _assign_orphans(lessons: list[Lesson], chunk_ids: list[int]) -> None:
    """Для полного охвата: фрагменты, которые модель не отдала ни одному уроку, присоединяем к ближайшему
    предыдущему уроку, чтобы часть документа не выпала из курса."""
    covered = {r for l in lessons for r in l.source_refs}
    anchors = sorted(((max(l.source_refs), l) for l in lessons if l.source_refs), key=lambda x: x[0])
    if not anchors:
        return
    for cid in chunk_ids:
        if cid in covered:
            continue
        target = anchors[0][1]
        for top, l in anchors:
            if top <= cid:
                target = l
        target.source_refs = sorted(set(target.source_refs) | {cid})


def generate_structure(setup: CourseSetupRequest, chunks: list[Chunk]) -> Course:
    prompt = build_structure_prompt(setup, chunks)
    raw = llm.call_llm(STRUCTURE_SYSTEM_PROMPT, prompt, temperature=0.4, max_tokens=6000 if setup.scope == "full" else 4000, stage="structure")
    data = llm.extract_json(raw)

    valid = {c.id for c in chunks}
    chapters = []
    all_lessons: list[Lesson] = []
    for ch in data.get("chapters", []):
        lessons = [Lesson(title=l["title"], source_refs=_clean_refs(l.get("chunks"), valid)) for l in ch.get("lessons", [])]
        all_lessons += lessons
        chapters.append(Chapter(title=ch["title"], lessons=lessons))
    if setup.scope == "full" and chunks:
        _assign_orphans(all_lessons, [c.id for c in chunks])

    # то, что автор не заполнил, берём из ответа модели - дальше оно нужно промптам уроков
    if not setup.description:
        setup.description = (data.get("topic") or data.get("course_title") or "").strip()
    if not setup.audience:
        setup.audience = (data.get("audience") or "").strip()
    title = (data.get("course_title") or setup.description or "Новый курс").strip()[:80]

    course = Course(title=title, setup=setup, chapters=chapters, status="draft_structure")
    return course


LESSON_SYSTEM_PROMPT = """Ты — методист и автор учебного контента. Пишешь материал одного урока \
короткого корпоративного курса. Отвечай СТРОГО валидным JSON без пояснений, без markdown-разметки \
вне значений полей."""

QUIZ_TYPE_SHAPES = {
    "matching": """{
      "type": "matching",
      "question": "Сопоставь понятие и его описание",
      "pairs": [{"left": "...", "right": "..."}, {"left": "...", "right": "..."}, {"left": "...", "right": "..."}]
    } // 3-4 пары""",
    "single_choice": """{
      "type": "single_choice",
      "question": "Текст вопроса с одним правильным ответом",
      "options": ["Вариант 1", "Вариант 2", "Вариант 3", "Вариант 4"],
      "correct_index": 0
    } // ровно 4 варианта, correct_index — индекс правильного (0..3)""",
    "true_false": """{
      "type": "true_false",
      "statement": "Утверждение по материалу урока, которое нужно оценить как верное/неверное",
      "correct": true
    }""",
    "sequence": """{
      "type": "sequence",
      "question": "Расставь шаги/этапы в правильном порядке",
      "items": ["Шаг 1", "Шаг 2", "Шаг 3", "Шаг 4"]
    } // items перечислены в ПРАВИЛЬНОМ порядке, 3-5 пунктов""",
}


def build_lesson_quiz_hint(preferred_quiz_type: str | None, words: str = "300-500", quiz_count: int = 1) -> str:
    types = list(QUIZ_TYPE_SHAPES)
    if preferred_quiz_type in QUIZ_TYPE_SHAPES:
        start = types.index(preferred_quiz_type)
        chosen = [types[(start + i) % len(types)] for i in range(quiz_count)]  # разные типы подряд
        shapes = "\n".join(f'- тип "{t}":\n{QUIZ_TYPE_SHAPES[t]}' for t in chosen)
        if quiz_count == 1:
            type_instruction = (
                f'Сделай ровно ОДНО проверочное задание ТИПА "{chosen[0]}" по материалу урока, '
                f"строго по схеме этого типа:\n{QUIZ_TYPE_SHAPES[chosen[0]]}"
            )
        else:
            order = ", ".join(f'"{t}"' for t in chosen)
            type_instruction = (
                f"Сделай ровно {quiz_count} проверочных задания РАЗНЫХ типов, в таком порядке: {order}. "
                f"Каждое по материалу урока и строго по схеме своего типа:\n{shapes}"
            )
    else:
        all_shapes = "\n".join(f"- {t}: {s}" for t, s in QUIZ_TYPE_SHAPES.items())
        type_instruction = (
            f"Сделай ровно {quiz_count} проверочных заданий (если больше одного, то разных типов) — выбери САМЫЕ подходящие "
            f"типы под материал урока из списка ниже:\n{all_shapes}"
        )
    quiz_word = "один объект теста" if quiz_count == 1 else f"{quiz_count} объекта тестов"
    return f"""
Формат ответа (строго JSON):
{{
  "content_html": "<p>Текст урока в HTML...</p>",
  "quizzes": [ <ровно {quiz_word} по схемам ниже> ]
}}

Правила для content_html:
- Используй ТОЛЬКО теги: p, h3, ul, ol, li, strong, em, u, a, blockquote, hr и врезки.
- Врезка — это <div class="callout" data-callout data-variant="info"><p>...</p></div>, где data-variant:
  info — важный факт, tip — практический совет, warning — предупреждение.
- Структурируй материал: короткие абзацы, подзаголовки h3 для смысловых частей, нумерованный список ol
  для последовательности шагов, ul для перечислений, ключевые термины выделяй strong.
- Добавь врезки с важными фактами, советами и предупреждениями по теме урока (одну-две на урок).
- Передай из исходных материалов все существенные детали: названия, шаги, параметры, условия, исключения.
  Не обобщай слишком сильно и не пропускай важное ради краткости.
- Не используй inline-стили, теги h1/h2, таблицы и картинки. Не заключай весь текст в один div.
- Объём — {words} слов.

{type_instruction}
"""


def build_image_hint(image_ids: list[int]) -> str:
    nums = ", ".join(str(k) for k in image_ids)
    return (
        "В исходных материалах встречаются рисунки (скриншоты, схемы) в виде пометок «[[Рисунок N]] подпись». "
        f"К этому уроку относятся рисунки: {nums}. Если рисунок помогает понять материал, вставь пометку в content_html "
        "отдельным абзацем, строго в таком виде: <p>[[Рисунок N]]</p>, сразу после абзаца, где объясняется то, что на нём показано. "
        "Каждую пометку используй не больше одного раза и только с номерами из списка выше. Саму картинку описывать не нужно: "
        "текст урока должен быть понятен и без неё."
    )


def build_lesson_prompt(
    course: Course, chapter: Chapter, lesson: Lesson, source_text: str,
    preferred_quiz_type: str | None = None, image_ids: list[int] | None = None,
) -> str:
    words, quiz_count, _ = LESSON_DETAIL.get(course.setup.detail, LESSON_DETAIL[DEFAULT_DETAIL])
    parts = [
        f"Курс: {course.setup.description}",
        f"Аудитория: {course.setup.audience}",
        f"Глава: {chapter.title}",
        f"Урок: {lesson.title}",
    ]
    if source_text:
        parts.append(f"Исходные материалы по теме (используй факты из них):\n{source_text}")
    if image_ids:
        parts.append(build_image_hint(image_ids))
    parts.append(build_lesson_quiz_hint(preferred_quiz_type, words, quiz_count))
    return "\n\n".join(parts)


def generate_chapter_cover(course: Course, chapter: Chapter) -> Chapter:
    """Процедурная обложка главы: иконки подбираются локально по ключевым словам темы,
    палитра — детерминированно по названию главы. Без обращений к внешним API."""
    lesson_titles = [l.title for l in chapter.lessons]
    icons = cover_icons.select_icons(chapter.title, lesson_titles)
    palette = cover_builder.select_palette(chapter.title)

    svg = cover_builder.build_cover_svg(icons, palette)
    b64 = base64.b64encode(svg.encode("utf-8")).decode("ascii")

    chapter.cover_image = f"data:image/svg+xml;base64,{b64}"
    chapter.cover_prompt = f"icons={icons}; palette={palette}"
    return chapter


def generate_lesson_content(
    course: Course, chapter: Chapter, lesson: Lesson, chunks: list[Chunk],
    preferred_quiz_type: str | None = None,
    images: dict[int, dict] | None = None, used_files: set[str] | None = None,
) -> Lesson:
    """images - картинки из документов курса {номер: {...}}; used_files - картинки, уже стоящие в других уроках."""
    source_text, image_ids = chunking.select_for_lesson(
        chunks, lesson.source_refs, lesson.title, chapter.title, _budget(config.LESSON_SOURCE_CHARS)
    )
    images = images or {}
    image_ids = [k for k in image_ids if k in images]
    prompt = build_lesson_prompt(course, chapter, lesson, source_text, preferred_quiz_type, image_ids)
    max_tokens = LESSON_DETAIL.get(course.setup.detail, LESSON_DETAIL[DEFAULT_DETAIL])[2]
    raw = llm.call_llm(LESSON_SYSTEM_PROMPT, prompt, temperature=0.5, max_tokens=max_tokens)
    data = llm.extract_json(raw)

    # картинки, которые модель не расставила, добавляем сами, но только самые подходящие по теме урока
    ctx = chunking.image_contexts(chunks)
    ignore = chunking.common_image_words(ctx, image_ids) | frozenset(chunking._stems(chapter.title, 5))
    score = {k: chunking.image_relevance(lesson.title, *ctx.get(k, ("", "")), ignore) for k in image_ids}
    best = max(score.values(), default=0)
    fallback_ok = {k for k, v in score.items() if v >= 1 and v >= best / 2}
    lesson.content_html = place_images(data.get("content_html", ""), image_ids, images, used_files or set(), fallback_ok)
    lesson.quizzes = data.get("quizzes", [])
    lesson.generated = True
    return lesson
