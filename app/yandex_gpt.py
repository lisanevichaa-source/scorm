"""Обёртка над YandexGPT (Yandex Foundation Models API).

Документация: https://yandex.cloud/ru/docs/foundation-models/concepts/yandexgpt/models
Эндпоинт completion: https://llm.api.cloud.yandex.net/foundationModels/v1/completion
"""
from __future__ import annotations
import json
import re
import requests

from . import config


class YandexGPTError(RuntimeError):
    pass


def _check_credentials():
    if not config.YANDEX_API_KEY or not config.YANDEX_FOLDER_ID:
        raise YandexGPTError(
            "Не заданы YANDEX_API_KEY / YANDEX_FOLDER_ID (переменные окружения)."
        )


def call_yandex_gpt(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.4,
    max_tokens: int = 4000,
) -> str:
    """Делает запрос к YandexGPT и возвращает текст ответа ассистента."""
    _check_credentials()

    model_uri = f"gpt://{config.YANDEX_FOLDER_ID}/{config.YANDEX_MODEL}/latest"

    payload = {
        "modelUri": model_uri,
        "completionOptions": {
            "stream": False,
            "temperature": temperature,
            "maxTokens": str(max_tokens),
        },
        "messages": [
            {"role": "system", "text": system_prompt},
            {"role": "user", "text": user_prompt},
        ],
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Api-Key {config.YANDEX_API_KEY}",
        "x-folder-id": config.YANDEX_FOLDER_ID,
    }

    resp = requests.post(config.YANDEX_GPT_URL, headers=headers, json=payload, timeout=90)
    if resp.status_code != 200:
        raise YandexGPTError(f"YandexGPT вернул ошибку {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    try:
        return data["result"]["alternatives"][0]["message"]["text"]
    except (KeyError, IndexError) as e:
        raise YandexGPTError(f"Неожиданный формат ответа YandexGPT: {data}") from e


def extract_json(raw_text: str) -> dict | list:
    """LLM иногда оборачивает JSON в ```json ... ``` или добавляет пояснения — вычищаем это."""
    text = raw_text.strip()
    text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    # На случай, если модель добавила текст до/после JSON — вырезаем первый валидный блок {...} или [...]
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if not match:
        raise YandexGPTError(f"Не удалось найти JSON в ответе модели: {raw_text[:300]}")
    candidate = match.group(1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise YandexGPTError(f"Невалидный JSON от модели: {e}\n{candidate[:500]}") from e
