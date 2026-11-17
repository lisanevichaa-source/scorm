from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field
import uuid


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class CourseSetupRequest(BaseModel):
    """Данные с первого экрана (рис. 1)."""
    duration: Literal["short", "medium", "long"] = "short"  # до10 / 10-20 / 15-30 мин
    language: str = "ru"
    description: str
    audience: str
    desired_structure: Optional[str] = None  # если сотрудник сам перечислил темы
    upload_ids: list[str] = Field(default_factory=list)  # id загруженных и распарсенных файлов


class MatchingPair(BaseModel):
    left: str
    right: str


class MatchingQuiz(BaseModel):
    type: Literal["matching"] = "matching"
    question: str
    pairs: list[MatchingPair] = Field(default_factory=list)


class ChoiceQuiz(BaseModel):
    type: Literal["single_choice"] = "single_choice"
    question: str
    options: list[str] = Field(default_factory=list)
    correct_index: int = 0


class TrueFalseQuiz(BaseModel):
    type: Literal["true_false"] = "true_false"
    statement: str
    correct: bool = True


class SequenceQuiz(BaseModel):
    type: Literal["sequence"] = "sequence"
    question: str
    items: list[str] = Field(default_factory=list)  # перечислены в ПРАВИЛЬНОМ порядке


Quiz = MatchingQuiz | ChoiceQuiz | TrueFalseQuiz | SequenceQuiz
QUIZ_TYPES = ["matching", "single_choice", "true_false", "sequence"]


class Lesson(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    content_html: str = ""  # текст урока (сгенерированный / отредактированный)
    quizzes: list[dict] = Field(default_factory=list)  # сериализованные MatchingQuiz/ChoiceQuiz
    generated: bool = False  # True после того, как контент сгенерирован LLM


class Chapter(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    lessons: list[Lesson] = Field(default_factory=list)
    cover_image: Optional[str] = None  # data:image/png;base64,... — обложка главы (YandexART)
    cover_prompt: Optional[str] = None  # промпт, по которому обложка сгенерирована (для повторной генерации)


class Course(BaseModel):
    id: str = Field(default_factory=new_id)
    setup: CourseSetupRequest
    chapters: list[Chapter] = Field(default_factory=list)
    status: Literal["draft_structure", "structure_confirmed", "ready_to_build", "built"] = "draft_structure"


class StructureReorderRequest(BaseModel):
    chapters: list[Chapter]
