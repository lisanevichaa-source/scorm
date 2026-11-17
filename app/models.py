from __future__ import annotations
from typing import Optional, Literal
from pydantic import BaseModel, Field
import uuid


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class CourseSetupRequest(BaseModel):
    """Данные с первого экрана (рис. 1)."""
    scope: str = "standard"  # охват курса: brief (основное) / standard / full (по всему документу)
    duration: str = "10"  # устарело: раньше задавалась длительность в минутах; оставлено, чтобы читались старые курсы
    detail: str = "standard"  # подробность уроков: brief / standard / full
    language: str = "ru"
    description: str = ""  # можно не заполнять: тема берётся из загруженного файла
    audience: str = ""
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


class Podcast(BaseModel):
    """Аудиоверсия урока: текст для диктора и озвучка."""
    script: str = ""
    audio: str = ""            # /media/<id>.mp3
    seconds: int = 0
    lesson_hash: str = ""      # хеш текста урока, по которому написан скрипт
    text_changed: bool = False   # текст урока изменился после написания скрипта
    audio_outdated: bool = False  # скрипт изменён после озвучки


class Lesson(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    content_html: str = ""  # текст урока (сгенерированный / отредактированный)
    quizzes: list[dict] = Field(default_factory=list)  # сериализованные MatchingQuiz/ChoiceQuiz
    generated: bool = False  # True после того, как контент сгенерирован LLM
    podcast: Optional[Podcast] = None
    source_refs: list[int] = Field(default_factory=list)  # номера фрагментов исходного документа, на которых основан урок


class Chapter(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str
    lessons: list[Lesson] = Field(default_factory=list)
    cover_image: Optional[str] = None  # data:image/png;base64,... — обложка главы (YandexART)
    cover_prompt: Optional[str] = None  # промпт, по которому обложка сгенерирована (для повторной генерации)


class Share(BaseModel):
    email: str
    role: Literal["view", "edit"] = "view"


class Course(BaseModel):
    id: str = Field(default_factory=new_id)
    owner: Optional[str] = None  # email владельца (из корпоративного OpenID); None у курсов, созданных до появления входа
    owner_name: str = ""
    shares: list[Share] = Field(default_factory=list)  # коллеги, которым владелец открыл курс
    title: str = ""  # название курса (показывается в шапке и в SCORM-плеере)
    setup: CourseSetupRequest
    chapters: list[Chapter] = Field(default_factory=list)
    status: Literal["draft_structure", "structure_confirmed", "ready_to_build", "built"] = "draft_structure"


class StructureReorderRequest(BaseModel):
    chapters: list[Chapter]
    title: Optional[str] = None
