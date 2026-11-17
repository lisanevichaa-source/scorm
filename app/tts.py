"""Озвучка текста. Провайдер выбирается переменной TTS_PROVIDER (см. config.py).

Остальной код работает только с synthesize(): смена голосовой модели не требует правок логики подкастов.
Результат - MP3. Длинный текст режется на куски по предложениям, куски озвучиваются по очереди
и склеиваются (MP3-кадры можно склеивать простым объединением байтов).
"""
from __future__ import annotations
import io
import re
import uuid

import requests

from . import config, pronunciation


class TTSError(RuntimeError):
    pass


_DEFAULT_CHUNK = {"yandex": 4000, "openai_compatible": 1800}
_RETRY = (429, 500, 502, 503, 504)


def provider() -> str:
    p = config.TTS_PROVIDER
    return "openai_compatible" if p in ("openai", "openai_compatible") else p


def is_configured() -> bool:
    if provider() == "yandex":
        return bool(config.YANDEX_API_KEY and config.YANDEX_FOLDER_ID)
    if provider() == "openai_compatible":
        return bool(config.TTS_BASE_URL and config.TTS_MODEL)
    return False


def split_text(text: str, limit: int) -> list[str]:
    """Режет текст на куски не длиннее limit по абзацам и предложениям."""
    chunks: list[str] = []
    cur = ""

    def push():
        nonlocal cur
        if cur.strip():
            chunks.append(cur.strip())
        cur = ""

    for para in re.split(r"\n+", text.strip()):
        for sent in re.split(r"(?<=[.!?…])\s+", para.strip()):
            while len(sent) > limit:  # предложение без точек: режем по пробелу
                cut = sent.rfind(" ", 0, limit) or limit
                cut = cut if cut > 0 else limit
                if cur:
                    push()
                chunks.append(sent[:cut].strip())
                sent = sent[cut:].strip()
            if cur and len(cur) + len(sent) + 1 > limit:
                push()
            cur = f"{cur} {sent}".strip() if cur else sent
        if cur:
            cur += "\n"  # граница абзаца: пауза
    push()
    return chunks


def _post(url: str, **kw) -> requests.Response:
    last: Exception | None = None
    for attempt in range(3):
        try:
            r = requests.post(url, timeout=config.TTS_TIMEOUT, verify=config.LLM_CA_BUNDLE or True, **kw)
        except (requests.Timeout, requests.ConnectionError) as e:
            last = TTSError(f"Нет ответа от сервера озвучки ({e.__class__.__name__}). Проверьте адрес и сеть.")
        else:
            if r.status_code not in _RETRY:
                return r
            last = TTSError(f"Сервер озвучки вернул ошибку {r.status_code}: {r.text[:300]}")
        if attempt < 2:
            import time
            time.sleep(2 * (attempt + 1))
    raise last  # type: ignore[misc]


def _yandex(chunk: str) -> bytes:
    data = {
        "text": chunk, "lang": "ru-RU", "voice": config.TTS_VOICE or "alena",
        "format": "mp3", "speed": str(config.TTS_SPEED), "folderId": config.YANDEX_FOLDER_ID,
    }
    r = _post("https://tts.api.cloud.yandex.net/speech/v1/tts:synthesize",
              headers={"Authorization": f"Api-Key {config.YANDEX_API_KEY}"}, data=data)
    if r.status_code != 200:
        raise TTSError(f"SpeechKit вернул ошибку {r.status_code}: {r.text[:300]}")
    return r.content


def _openai(chunk: str) -> bytes:
    payload = {"model": config.TTS_MODEL, "input": chunk, "response_format": "mp3", "speed": config.TTS_SPEED}
    if config.TTS_VOICE:
        payload["voice"] = config.TTS_VOICE
    headers = {"Content-Type": "application/json"}
    if config.TTS_API_KEY:
        headers["Authorization"] = f"Bearer {config.TTS_API_KEY}"
    r = _post(f"{config.TTS_BASE_URL}/audio/speech", headers=headers, json=payload)
    if r.status_code != 200:
        raise TTSError(f"Сервер озвучки вернул ошибку {r.status_code}: {r.text[:300]}")
    if r.content[:4] == b"RIFF":
        raise TTSError("Сервер озвучки вернул WAV, а нужен MP3. Включите формат mp3 на сервере озвучки.")
    return r.content


def synthesize(text: str, on_progress=None) -> bytes:
    """Озвучивает text, возвращает MP3."""
    if not is_configured():
        raise TTSError("Озвучка не настроена: задайте TTS_PROVIDER и связанные переменные (см. README).")
    text = pronunciation.apply(text)
    limit = config.TTS_CHUNK_CHARS or _DEFAULT_CHUNK.get(provider(), 1800)
    chunks = split_text(text, limit)
    if not chunks:
        raise TTSError("Нет текста для озвучки.")
    call = _yandex if provider() == "yandex" else _openai
    parts = []
    for i, chunk in enumerate(chunks):
        parts.append(call(chunk))
        if on_progress:
            on_progress(i + 1, len(chunks))
    return b"".join(parts)


def mp3_seconds(data: bytes) -> int:
    try:
        from mutagen.mp3 import MP3
        return int(round(MP3(io.BytesIO(data)).info.length))
    except Exception:
        return 0


def save_mp3(data: bytes) -> str:
    """Сохраняет MP3 в data/media и возвращает адрес /media/<id>.mp3."""
    name = f"{uuid.uuid4().hex[:24]}.mp3"
    (config.MEDIA_DIR / name).write_bytes(data)
    return f"/media/{name}"
