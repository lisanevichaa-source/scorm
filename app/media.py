"""Видео, загруженные автором в уроки.

Файл приходит частями (как и документы), собирается в data/media/<id>.<mp4|webm> и раздаётся с поддержкой Range:
без Range не перематывается видео и вообще не играет в Safari / на iPhone.
В тексте урока видео хранится ссылкой /media/<id>.<ext>; при сборке SCORM файл копируется в пакет (см. scorm_builder).
"""
from __future__ import annotations
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import config
from .images import save_manual

router = APIRouter()

_ID_RE = re.compile(r"^[0-9a-f]{12,32}$")
MEDIA_REF_RE = re.compile(r"/media/([0-9a-f]{12,32})\.(mp4|webm|jpg|png|mp3)")  # ссылка на видео или картинку внутри HTML урока
MAX_CHUNK_BYTES = 2 * 1024 * 1024
MAX_PARTS = 600
_TYPES = {"mp4": "video/mp4", "webm": "video/webm", "jpg": "image/jpeg", "png": "image/png", "mp3": "audio/mpeg"}


def media_path(media_id: str, ext: str) -> Path:
    return config.MEDIA_DIR / f"{media_id}.{ext}"


def _sniff(path: Path) -> str | None:
    """Формат по содержимому, а не по имени: переименованный .mov или .avi не пройдёт."""
    with open(path, "rb") as f:
        head = f.read(12)
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"qt  ",):  # QuickTime .mov: в браузерах играет не везде
            return None
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    return None


@router.get("/api/media/info")
def media_info():
    return {"max_mb": config.MAX_VIDEO_MB, "max_image_mb": config.MAX_IMAGE_MB}


@router.post("/api/media/chunk")
async def media_chunk(request: Request, media_id: str, index: int, total: int):
    if not _ID_RE.match(media_id) or index < 0 or total < 1 or index >= total or total > MAX_PARTS:
        raise HTTPException(400, "Некорректные параметры загрузки.")
    data = await request.body()
    if len(data) > MAX_CHUNK_BYTES:
        raise HTTPException(400, "Часть файла слишком велика.")
    (config.MEDIA_DIR / f"{media_id}.part{index}").write_bytes(data)
    return {"ok": True}


@router.post("/api/media/complete")
def media_complete(payload: dict):
    media_id = str(payload.get("media_id", ""))
    try:
        total = int(payload.get("total", 0))
    except (TypeError, ValueError):
        total = 0
    if not _ID_RE.match(media_id) or total < 1 or total > MAX_PARTS:
        raise HTTPException(400, "Некорректные параметры загрузки.")

    parts = [config.MEDIA_DIR / f"{media_id}.part{i}" for i in range(total)]
    tmp = config.MEDIA_DIR / f"{media_id}.tmp"
    try:
        missing = [p for p in parts if not p.exists()]
        if missing:
            raise HTTPException(400, f"Загружены не все части файла ({total - len(missing)} из {total}). Повторите загрузку.")
        size = sum(p.stat().st_size for p in parts)
        is_image = payload.get("kind") == "image"
        if is_image and size > config.MAX_IMAGE_MB * 1024 * 1024:
            raise HTTPException(400, f"Картинка больше {config.MAX_IMAGE_MB} МБ.")
        if not is_image and size > config.MAX_VIDEO_MB * 1024 * 1024:
            raise HTTPException(400, f"Видео больше {config.MAX_VIDEO_MB} МБ. Сожмите его или вставьте ссылкой.")
        with open(tmp, "wb") as out:
            for p in parts:
                with open(p, "rb") as src:
                    while True:
                        buf = src.read(1024 * 1024)
                        if not buf:
                            break
                        out.write(buf)
        if is_image:
            saved = save_manual(tmp.read_bytes())
            if not saved:
                raise HTTPException(400, "Это не картинка или формат не поддерживается. Подойдут PNG, JPEG, WebP, GIF.")
            return {"url": f"/media/{saved[0]}", "width": saved[1], "height": saved[2]}
        ext = _sniff(tmp)
        if not ext:
            raise HTTPException(400, "Поддерживаются видео MP4 (H.264) и WebM. Файлы MOV, AVI, MKV сначала нужно сконвертировать в MP4.")
        tmp.replace(media_path(media_id, ext))
    finally:
        for p in parts:
            p.unlink(missing_ok=True)
        tmp.unlink(missing_ok=True)
    return {"media_id": media_id, "url": f"/media/{media_id}.{ext}", "size": size}


@router.get("/media/{name}")
def media_get(name: str, request: Request):
    m = re.fullmatch(r"([0-9a-f]{12,32})\.(mp4|webm|jpg|png|mp3)", name)
    if not m:
        raise HTTPException(404)
    path = media_path(m.group(1), m.group(2))
    if not path.exists():
        raise HTTPException(404, "Видео не найдено")
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng:
        r = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if r and (r.group(1) or r.group(2)):
            if r.group(1):
                start = int(r.group(1))
                end = int(r.group(2)) if r.group(2) else size - 1
            else:  # bytes=-N: последние N байт
                start = max(size - int(r.group(2)), 0)
            end = min(end, size - 1)
            if start > end or start >= size:
                raise HTTPException(416, headers={"Content-Range": f"bytes */{size}"})
            status = 206

    def body():
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                buf = f.read(min(1024 * 512, left))
                if not buf:
                    break
                left -= len(buf)
                yield buf

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "private, max-age=3600"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=_TYPES[m.group(2)], headers=headers)
