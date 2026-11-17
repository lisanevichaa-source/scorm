import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

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

for d in (DATA_DIR, UPLOADS_DIR, COURSES_DIR, BUILDS_DIR):
    d.mkdir(parents=True, exist_ok=True)

MAX_UPLOAD_MB = 30
