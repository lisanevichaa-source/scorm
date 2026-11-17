"""Единая точка обращения к языковой модели.

Провайдер выбирается переменной окружения LLM_PROVIDER:
  yandex            - YandexGPT (Yandex Foundation Models API), по умолчанию
  openai_compatible - любая модель за OpenAI-совместимым API (vLLM, SGLang, Ollama...),
                      например Qwen, развёрнутая во внутреннем контуре

Остальной код работает только с call_llm() и extract_json(), поэтому смена модели
не требует правок промптов и логики курса.
"""
from __future__ import annotations
import json
import logging
import re
import threading
import time

import requests

from . import config


log = logging.getLogger("llm")


class LLMError(RuntimeError):
    pass


# Старое имя исключения - чтобы не ломать импорты
YandexGPTError = LLMError

_RETRY_STATUSES = (429, 500, 502, 503, 504)


def provider_name() -> str:
    return config.LLM_PROVIDER


# Общий лимит одновременных запросов: корпоративный шлюз разрешает автоматике 1-3 потока на модель,
# поэтому остальные запросы (другие курсы, подкасты) ждут здесь своей очереди.
_slots = threading.BoundedSemaphore(config.LLM_MAX_CONCURRENCY) if config.LLM_MAX_CONCURRENCY > 0 else None
_usage_lock = threading.Lock()
_dropped: set[str] = set()  # параметры, которые сервер уже отклонил: дальше их не отправляем
_usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "waiting": 0, "active": 0}


def usage() -> dict:
    """Счётчик запросов и токенов с момента запуска (если сервер возвращает usage)."""
    with _usage_lock:
        return dict(_usage, provider=config.LLM_PROVIDER, max_concurrency=config.LLM_MAX_CONCURRENCY)


def _count(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    with _usage_lock:
        _usage["requests"] += 1
        _usage["prompt_tokens"] += int(prompt_tokens or 0)
        _usage["completion_tokens"] += int(completion_tokens or 0)


def _verify():
    return config.LLM_CA_BUNDLE or True


def _delay(resp, attempt: int) -> float:
    ra = resp.headers.get("Retry-After") if resp is not None else None
    if ra and ra.isdigit():
        return min(int(ra), 60)
    return min(3 * (2 ** attempt), 30)


def _post_with_retry(url: str, headers: dict, payload: dict, label: str) -> requests.Response:
    last = None
    if _slots:
        with _usage_lock:
            _usage["waiting"] += 1
        _slots.acquire()
        with _usage_lock:
            _usage["waiting"] -= 1
    with _usage_lock:
        _usage["active"] += 1
    try:
        for attempt in range(config.LLM_RETRIES + 1):
            resp = None
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT, verify=_verify())
            except requests.exceptions.SSLError as e:
                raise LLMError(f"{label}: ошибка сертификата ({str(e)[:200]}). Укажите файл корпоративных сертификатов в LLM_CA_BUNDLE.") from e
            except (requests.Timeout, requests.ConnectionError) as e:
                last = LLMError(f"{label}: нет ответа от сервера ({e.__class__.__name__}). Проверьте адрес и сеть.")
            else:
                if resp.status_code not in _RETRY_STATUSES:
                    return resp
                last = LLMError(f"{label} вернул ошибку {resp.status_code}: {resp.text[:300]}")
            if attempt < config.LLM_RETRIES:
                log.warning("%s, повтор %d из %d", last, attempt + 1, config.LLM_RETRIES)
                time.sleep(_delay(resp, attempt))
        raise last
    finally:
        with _usage_lock:
            _usage["active"] -= 1
        if _slots:
            _slots.release()


# ---------- YandexGPT ----------

def _call_yandex(system_prompt: str, user_prompt: str, temperature: float, max_tokens: int) -> str:
    if not config.YANDEX_API_KEY or not config.YANDEX_FOLDER_ID:
        raise LLMError("Не заданы YANDEX_API_KEY / YANDEX_FOLDER_ID (переменные окружения).")

    payload = {
        "modelUri": f"gpt://{config.YANDEX_FOLDER_ID}/{config.YANDEX_MODEL}/latest",
        "completionOptions": {"stream": False, "temperature": temperature, "maxTokens": str(max_tokens)},
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
    resp = _post_with_retry(config.YANDEX_GPT_URL, headers, payload, "YandexGPT")
    if resp.status_code != 200:
        raise LLMError(f"YandexGPT вернул ошибку {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    u = (data.get("result") or {}).get("usage") or {}
    _count(u.get("inputTextTokens"), u.get("completionTokens"))
    try:
        return data["result"]["alternatives"][0]["message"]["text"]
    except (KeyError, IndexError) as e:
        raise LLMError(f"Неожиданный формат ответа YandexGPT: {data}") from e


# ---------- OpenAI-совместимый сервер (Qwen и др.) ----------

def _call_openai_compatible(system_prompt: str, user_prompt: str, temperature: float, max_tokens: int, stage: str = "") -> str:
    if not config.LLM_BASE_URL or not config.LLM_MODEL:
        raise LLMError("Не заданы LLM_BASE_URL / LLM_MODEL (переменные окружения).")

    model = (config.LLM_MODEL_STRUCTURE if stage == "structure" and config.LLM_MODEL_STRUCTURE else config.LLM_MODEL)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if config.LLM_JSON_MODE:
        payload["response_format"] = {"type": "json_object"}
    if config.LLM_DISABLE_THINKING:
        # vLLM и SGLang передают это в шаблон чата Qwen3; другие серверы параметр игнорируют
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY:
        headers["Authorization"] = f"Bearer {config.LLM_API_KEY}"

    for extra in _dropped:
        payload.pop(extra, None)
    url = f"{config.LLM_BASE_URL}/chat/completions"
    resp = _post_with_retry(url, headers, payload, "Сервер модели")
    # Шлюзы вроде LiteLLM могут отклонять необязательные параметры: пробуем без них
    for extra in ("chat_template_kwargs", "response_format"):
        if resp.status_code == 400 and extra in payload and (extra in resp.text or "extra" in resp.text.lower() or "unsupported" in resp.text.lower()):
            log.warning("Сервер не принял параметр %s, повторяем без него", extra)
            payload.pop(extra)
            _dropped.add(extra)
            resp = _post_with_retry(url, headers, payload, "Сервер модели")
    if resp.status_code != 200:
        raise LLMError(f"Сервер модели вернул ошибку {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    u = data.get("usage") or {}
    _count(u.get("prompt_tokens"), u.get("completion_tokens"))
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Неожиданный формат ответа сервера модели: {str(data)[:500]}") from e
    if choice.get("finish_reason") == "length":
        raise LLMError("Ответ модели оборвался по длине (max_tokens). Увеличьте лимит или сократите запрос.")
    return text


# ---------- общий интерфейс ----------

def call_llm(system_prompt: str, user_prompt: str, temperature: float = 0.4, max_tokens: int = 4000, stage: str = "") -> str:
    """Отправляет запрос выбранной модели и возвращает текст ответа."""
    if config.LLM_PROVIDER == "yandex":
        return _call_yandex(system_prompt, user_prompt, temperature, max_tokens)
    if config.LLM_PROVIDER in ("openai_compatible", "openai", "qwen"):
        return _call_openai_compatible(system_prompt, user_prompt, temperature, max_tokens, stage)
    raise LLMError(f"Неизвестный LLM_PROVIDER: {config.LLM_PROVIDER}. Допустимо: yandex, openai_compatible.")


def limit_source(text: str) -> str:
    """Обрезает исходные документы под контекст модели (LLM_MAX_SOURCE_CHARS, 0 - без ограничения)."""
    n = config.LLM_MAX_SOURCE_CHARS
    if n and len(text) > n:
        return text[:n] + "\n[...документ обрезан...]"
    return text


def extract_json(raw_text: str) -> dict | list:
    """Модель иногда оборачивает JSON в ```json ... ```, добавляет пояснения или (Qwen) блок <think>."""
    text = re.sub(r"<think>.*?</think>", "", raw_text, flags=re.DOTALL)
    text = re.sub(r"^.*?</think>", "", text, flags=re.DOTALL)  # закрывающий тег без открывающего
    text = text.strip()
    text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    if not match:
        raise LLMError(f"Не удалось найти JSON в ответе модели: {raw_text[:300]}")
    candidate = match.group(1)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMError(f"Невалидный JSON от модели: {e}\n{candidate[:500]}") from e
