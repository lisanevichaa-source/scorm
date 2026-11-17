from __future__ import annotations
import base64
from .models import Course, Chapter, Lesson, CourseSetupRequest
from . import yandex_gpt as gpt
from . import cover_icons
from . import cover_builder

DURATION_HINT = {
    "short": "до 10 минут (коротко, 1-2 главы, по 2-3 урока)",
    "medium": "10-20 минут (3-4 главы, по 2-4 урока)",
    "long": "15-30 минут (4-6 глав, по 3-5 уроков)",
}

STRUCTURE_SYSTEM_PROMPT = """Ты — методист, который проектирует структуру короткого учебного курса \
для сотрудников компании. Отвечай СТРОГО валидным JSON без пояснений и без markdown-разметки."""

STRUCTURE_JSON_SCHEMA_HINT = """
Формат ответа (строго JSON, без комментариев):
{
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


def build_structure_prompt(setup: CourseSetupRequest, source_text: str) -> str:
    parts = [
        f"Описание курса: {setup.description}",
        f"Целевая аудитория: {setup.audience}",
        f"Желаемая длительность: {DURATION_HINT.get(setup.duration, setup.duration)}",
        f"Язык курса: {setup.language}",
    ]
    if setup.desired_structure:
        parts.append(
            f"Сотрудник предложил свой порядок тем — максимально следуй ему:\n{setup.desired_structure}"
        )
    if source_text:
        parts.append(f"Исходные материалы (используй как основу содержания):\n{source_text}")
    parts.append(STRUCTURE_JSON_SCHEMA_HINT)
    return "\n\n".join(parts)


def generate_structure(setup: CourseSetupRequest, source_text: str) -> Course:
    prompt = build_structure_prompt(setup, source_text)
    raw = gpt.call_yandex_gpt(STRUCTURE_SYSTEM_PROMPT, prompt, temperature=0.4)
    data = gpt.extract_json(raw)

    chapters = []
    for ch in data.get("chapters", []):
        lessons = [Lesson(title=l["title"]) for l in ch.get("lessons", [])]
        chapters.append(Chapter(title=ch["title"], lessons=lessons))

    course = Course(setup=setup, chapters=chapters, status="draft_structure")
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


def build_lesson_quiz_hint(preferred_quiz_type: str | None) -> str:
    if preferred_quiz_type and preferred_quiz_type in QUIZ_TYPE_SHAPES:
        type_instruction = (
            f'Сделай ровно ОДНО проверочное задание ТИПА "{preferred_quiz_type}" по материалу урока, '
            f"строго по схеме этого типа:\n{QUIZ_TYPE_SHAPES[preferred_quiz_type]}"
        )
    else:
        all_shapes = "\n".join(f"- {t}: {s}" for t, s in QUIZ_TYPE_SHAPES.items())
        type_instruction = (
            "Сделай ровно ОДНО проверочное задание — выбери САМЫЙ подходящий тип под материал урока "
            f"из списка ниже:\n{all_shapes}"
        )
    return f"""
Формат ответа (строго JSON):
{{
  "content_html": "<p>Текст урока в простом HTML (p, ul/li, b, strong)...</p>",
  "quizzes": [ <ровно один объект теста по схеме ниже> ]
}}
{type_instruction}
"""


def build_lesson_prompt(
    course: Course, chapter: Chapter, lesson: Lesson, source_text: str,
    preferred_quiz_type: str | None = None,
) -> str:
    parts = [
        f"Курс: {course.setup.description}",
        f"Аудитория: {course.setup.audience}",
        f"Глава: {chapter.title}",
        f"Урок: {lesson.title}",
    ]
    if source_text:
        parts.append(f"Исходные материалы по теме (используй факты из них):\n{source_text}")
    parts.append(build_lesson_quiz_hint(preferred_quiz_type))
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
    course: Course, chapter: Chapter, lesson: Lesson, source_text: str,
    preferred_quiz_type: str | None = None,
) -> Lesson:
    prompt = build_lesson_prompt(course, chapter, lesson, source_text, preferred_quiz_type)
    raw = gpt.call_yandex_gpt(LESSON_SYSTEM_PROMPT, prompt, temperature=0.5)
    data = gpt.extract_json(raw)

    lesson.content_html = data.get("content_html", "")
    lesson.quizzes = data.get("quizzes", [])
    lesson.generated = True
    return lesson
