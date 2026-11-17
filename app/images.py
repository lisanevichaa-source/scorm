"""Картинки из исходных документов: сохранение, сжатие и расстановка в уроках.

Картинка из документа получает номер K (сквозной по курсу). В тексте для модели на её месте стоит пометка
«[[Рисунок K]] подпись». Модель переносит пометку в нужное место урока, а мы заменяем её на <figure>.
Если модель пометку не поставила, картинка добавляется в конец урока, которому она принадлежит.
"""
from __future__ import annotations
import hashlib
import html as _html
import io
import re
import uuid

from . import config

MAX_SIDE = 1600           # длинная сторона после сжатия, px (скриншоты должны читаться)
MIN_SIDE = 100            # меньшие картинки считаем значками и отбрасываем
MIN_EMU_W, MIN_EMU_H = 1_000_000, 600_000   # меньше ~2.8 x 1.7 см на странице: значок, линия, элемент подписи
KEEP_JPEG_BYTES = 600_000
PNG_MAX_BYTES = 400_000   # PNG оставляем, пока не тяжелее: у скриншотов с текстом он чётче JPEG
MAX_PER_LESSON = 10       # столько картинок модель может поставить в один урок (в презентациях скриншотов много)
MAX_FALLBACK = 3          # а столько мы добавим сами в конец, если модель пометку не поставила

IMG_LINE_RE = re.compile(r"^\[\[Рисунок (\d+)\]\][^\n]*$", re.M)   # строка с пометкой в тексте для модели
_BLOCK_MARK_RE = re.compile(r"<p>\s*\[{1,2}\s*Рисунок\s*(\d+)\s*\]{1,2}\s*</p>")
_INLINE_MARK_RE = re.compile(r"\[{1,2}\s*Рисунок\s*(\d+)\s*\]{1,2}")


def _encode(blob: bytes, strict: bool = True):
    """Возвращает (bytes, ext, width, height) или None, если файл не картинка. strict отбрасывает ещё и мелкие
    значки и узкие линии (для картинок из документов); картинки, которые автор вставил сам, принимаются любые."""
    from PIL import Image, ImageOps
    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
    except Exception:  # EMF/WMF и прочее, что Pillow не читает
        return None
    fmt = (im.format or "").upper()
    w, h = im.size
    if strict and (w < MIN_SIDE or h < MIN_SIDE or max(w, h) / max(min(w, h), 1) > 12):
        return None

    if fmt == "JPEG" and max(w, h) <= MAX_SIDE and len(blob) <= KEEP_JPEG_BYTES:
        return blob, "jpg", w, h  # не перекодируем: лишнее сжатие только портит

    im = ImageOps.exif_transpose(im)
    if max(im.size) > MAX_SIDE:
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    w, h = im.size
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)

    if fmt != "JPEG":
        buf = io.BytesIO()
        (im.convert("RGBA") if has_alpha else im.convert("RGB")).save(buf, "PNG", optimize=True)
        if len(buf.getvalue()) <= PNG_MAX_BYTES or has_alpha:
            return buf.getvalue(), "png", w, h

    rgb = im.convert("RGB")
    buf = io.BytesIO()
    rgb.save(buf, "JPEG", quality=85, optimize=True)
    return buf.getvalue(), "jpg", w, h


class ImageCollector:
    """Принимает картинки по мере чтения документа, сохраняет их в data/media и ведёт список."""

    def __init__(self):
        self.items: list[dict] = []
        self._seen: set[str] = set()

    def __call__(self, blob: bytes, caption: str = "") -> int | None:
        digest = hashlib.sha1(blob).hexdigest()
        if digest in self._seen:  # повторы (логотип в каждом разделе) не нужны
            return None
        self._seen.add(digest)
        enc = _encode(blob)
        if not enc:
            return None
        data, ext, w, h = enc
        name = f"{uuid.uuid4().hex[:24]}.{ext}"
        (config.MEDIA_DIR / name).write_bytes(data)
        n = len(self.items) + 1
        self.items.append({"n": n, "file": name, "w": w, "h": h, "caption": caption})
        return n


def save_manual(blob: bytes) -> tuple[str, int, int] | None:
    """Картинка, загруженная автором: (имя файла в media, ширина, высота) или None, если это не картинка."""
    enc = _encode(blob, strict=False)
    if not enc:
        return None
    data, ext, w, h = enc
    name = f"{uuid.uuid4().hex[:24]}.{ext}"
    (config.MEDIA_DIR / name).write_bytes(data)
    return name, w, h


def clean_caption(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    text = re.sub(r"^[\s\-–—:.]+", "", text)  # подпись часто начинается с «– », а слово «Рисунок N» вынесено в картинку
    return text[:200]


def figure_html(img: dict) -> str:
    cap = (img.get("caption") or "").strip()
    alt = _html.escape(cap or "Иллюстрация", quote=True)
    out = f'<figure class="lesson-image"><img src="/media/{img["file"]}" alt="{alt}">'
    if cap:
        out += f"<figcaption>{_html.escape(cap)}</figcaption>"
    return out + "</figure>"


def place_images(html: str, owned: list[int], by_id: dict[int, dict], used_files: set[str],
                 fallback_ok: set[int] | None = None) -> str:
    """Заменяет пометки «[[Рисунок K]]» в тексте урока на картинки; не поставленные моделью добавляет в конец."""
    placed: list[int] = []
    added: list[int] = []

    def ok(k: int, limit: int = MAX_PER_LESSON) -> bool:
        return k in owned and k in by_id and k not in placed and by_id[k]["file"] not in used_files \
            and len(placed) < limit

    def block(m):
        k = int(m.group(1))
        if not ok(k):
            return ""
        placed.append(k)
        return figure_html(by_id[k])

    html = _BLOCK_MARK_RE.sub(block, html or "")
    html = _INLINE_MARK_RE.sub("", html)  # пометки внутри абзаца или чужие номера убираем
    for k in owned:  # модель пометку не поставила: добавляем в конец те картинки, что явно относятся к теме урока
        if len(added) < MAX_FALLBACK and ok(k) and (fallback_ok is None or k in fallback_ok):
            placed.append(k)
            added.append(k)
            html += figure_html(by_id[k])
    return html
