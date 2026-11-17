import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# --- Какая модель генерирует текст ---
# yandex            - YandexGPT (по умолчанию)
# openai_compatible - любая модель за OpenAI-совместимым API (vLLM, SGLang, Ollama и т.п.), например Qwen во внутреннем контуре
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "yandex").strip().lower()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "").rstrip("/")      # например http://llm.internal:8000/v1
LLM_API_KEY = os.getenv("LLM_API_KEY", "")                     # может быть пустым во внутреннем контуре
LLM_MODEL = os.getenv("LLM_MODEL", "")                         # имя модели на сервере
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "180"))             # секунд на один запрос
LLM_JSON_MODE = os.getenv("LLM_JSON_MODE", "1") == "1"         # просить сервер вернуть именно JSON (response_format)
LLM_DISABLE_THINKING = os.getenv("LLM_DISABLE_THINKING", "1") == "1"  # выключить режим размышлений Qwen3 (enable_thinking=false)
LLM_MAX_SOURCE_CHARS = int(os.getenv("LLM_MAX_SOURCE_CHARS", "0"))    # обрезать исходные документы до N символов, 0 - не обрезать
LLM_RETRIES = int(os.getenv("LLM_RETRIES", "4"))               # повторы при 429, 5xx и таймаутах (пауза растёт: 3, 6, 12, 24 с)
LLM_MAX_CONCURRENCY = int(os.getenv("LLM_MAX_CONCURRENCY", "3"))  # одновременных запросов к модели на всё приложение, 0 - без ограничения
LLM_MODEL_STRUCTURE = os.getenv("LLM_MODEL_STRUCTURE", "")     # другая модель только для структуры курса (например gpt-oss-120b); пусто - LLM_MODEL
LLM_CA_BUNDLE = os.getenv("LLM_CA_BUNDLE", "")                 # путь к файлу корпоративных сертификатов (CA), если шлюз с внутренним сертификатом
# Сколько исходного текста попадает в один запрос (символов). Для Yandex GPT значения подобраны под контекст 32k токенов,
# для Qwen с большим контекстом их можно поднять.
STRUCTURE_SOURCE_CHARS = int(os.getenv("STRUCTURE_SOURCE_CHARS", "40000"))  # дайджест документа для структуры курса
LESSON_SOURCE_CHARS = int(os.getenv("LESSON_SOURCE_CHARS", "14000"))        # фрагменты документа для одного урока

# --- Озвучка подкастов ---
# yandex            - Yandex SpeechKit (облако)
# openai_compatible - любой сервер с OpenAI-совместимым /v1/audio/speech (локальный TTS во внутреннем контуре)
# пусто             - озвучка выключена, кнопки подкаста в интерфейсе неактивны
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "").strip().lower()
TTS_BASE_URL = os.getenv("TTS_BASE_URL", "").rstrip("/")      # например http://tts.internal:8000/v1
TTS_API_KEY = os.getenv("TTS_API_KEY", "")
TTS_MODEL = os.getenv("TTS_MODEL", "")                         # имя модели на сервере озвучки
TTS_VOICE = os.getenv("TTS_VOICE", "")                         # голос (для Yandex по умолчанию alena)
TTS_SPEED = float(os.getenv("TTS_SPEED", "1.0"))
TTS_TIMEOUT = int(os.getenv("TTS_TIMEOUT", "300"))             # секунд на один кусок текста
TTS_CHUNK_CHARS = int(os.getenv("TTS_CHUNK_CHARS", "0"))       # длина куска текста за запрос, 0 - по умолчанию для провайдера

# --- Yandex GPT ---
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY", "")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID", "")
# Модель: yandexgpt / yandexgpt-lite / yandexgpt-32k и т.д.
YANDEX_MODEL = os.getenv("YANDEX_MODEL", "yandexgpt")
YANDEX_GPT_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Обложки глав генерируются процедурно (иконки + градиент, app/cover_builder.py) —
# без обращения к YandexART, так что отдельных настроек для этого не требуется.

# --- Хранилище данных (для MVP — локальная папка; в проде лучше вынести в S3/БД) ---
DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
COURSES_DIR = DATA_DIR / "courses"
BUILDS_DIR = DATA_DIR / "builds"
MEDIA_DIR = DATA_DIR / "media"  # видео, загруженные автором в уроки

for d in (DATA_DIR, UPLOADS_DIR, COURSES_DIR, BUILDS_DIR, MEDIA_DIR):
    d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = 30
MAX_IMAGE_MB = int(os.getenv("MAX_IMAGE_MB", "20"))   # размер картинки, загружаемой автором в урок
MAX_VIDEO_MB = int(os.getenv("MAX_VIDEO_MB", "150"))  # размер одного видео; учтите лимит загрузки пакета в вашей LMS

SCORM_MAX_MB = int(os.getenv("SCORM_MAX_MB", "500"))  # лимит размера SCORM-файла в вашем портале; при приближении к нему интерфейс предупредит

# ---------- Вход через корпоративный OpenID Connect ----------
OIDC_ISSUER = os.getenv("OIDC_ISSUER", "").rstrip("/")          # адрес провайдера, у которого есть /.well-known/openid-configuration
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_URI = os.getenv("OIDC_REDIRECT_URI", "")          # https://<адрес приложения>/auth/callback, должен совпадать с зарегистрированным
OIDC_SCOPES = os.getenv("OIDC_SCOPES", "openid email profile")
OIDC_EMAIL_CLAIM = os.getenv("OIDC_EMAIL_CLAIM", "email")       # поле токена с адресом почты (по нему определяются владелец и доступ)
OIDC_NAME_CLAIM = os.getenv("OIDC_NAME_CLAIM", "name")
OIDC_TOKEN_AUTH = os.getenv("OIDC_TOKEN_AUTH", "post")          # как передавать секрет клиента: post или basic
OIDC_CA_BUNDLE = os.getenv("OIDC_CA_BUNDLE", "") or os.getenv("LLM_CA_BUNDLE", "")
OIDC_END_SESSION = os.getenv("OIDC_END_SESSION", "0") == "1"    # при выходе завершать сессию и у провайдера (нужен зарегистрированный адрес возврата /auth/logged-out)
AUTH_MODE = os.getenv("AUTH_MODE", "oidc" if OIDC_ISSUER else "off").strip().lower()  # oidc или off (только для разработки)
SESSION_SECRET = os.getenv("SESSION_SECRET", "")                # длинная случайная строка; без неё сессии сбрасываются при перезапуске
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
ADMIN_EMAILS = [e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]
LEGACY_OWNER_EMAIL = os.getenv("LEGACY_OWNER_EMAIL", "").strip().lower()  # владелец курсов, созданных до появления входа; пусто - первый администратор

# ---------- Очистка неиспользуемых файлов ----------
CLEANUP_MIN_AGE_HOURS = float(os.getenv("CLEANUP_MIN_AGE_HOURS", "24"))  # свежие файлы не удаляются: их могли только что загрузить
CLEANUP_UPLOAD_DAYS = float(os.getenv("CLEANUP_UPLOAD_DAYS", "7"))       # документ без курса удаляется через столько дней
