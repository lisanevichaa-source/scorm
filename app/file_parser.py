"""Извлечение текста из DOCX / PPTX / PDF для последующей передачи в LLM."""
from pathlib import Path

import os
import re

# Сколько текста документов сохраняется для работы (≈330 страниц). В запрос к модели целиком он не попадает:
# документ режется на фрагменты (chunking.py), и модель получает только нужные.
MAX_CHARS = int(os.getenv("MAX_SOURCE_CHARS", "600000"))


def extract_text(path: Path, image_sink=None) -> tuple[str, int]:
    """Возвращает (текст для модели, сколько символов было в документе всего).
    image_sink(blob, caption) -> номер | None принимает картинки из DOCX и PPTX; на их месте в тексте остаётся пометка [[IMG:n]]."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text = _extract_docx(path, image_sink)
    elif suffix == ".pptx":
        text = _extract_pptx(path, image_sink)
    elif suffix == ".pdf":
        text = _extract_pdf(path)
    else:
        raise ValueError(f"Неподдерживаемый формат файла: {suffix}")
    return text[:MAX_CHARS], len(text)


_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "v": "urn:schemas-microsoft-com:vml",
}


def _x(el, expr):
    from lxml import etree
    return etree.XPath(expr, namespaces=_NS)(el)


def _paragraph_pictures(doc, p_el) -> list[bytes]:
    """Картинки абзаца в порядке следования. Учитывает группы фигур и пропускает дубли из mc:Fallback.
    Значки и элементы подписи (мелкие по размеру на странице) отбрасываются."""
    from .images import MIN_EMU_W, MIN_EMU_H
    blobs = []

    def add(rid):
        try:
            blobs.append(doc.part.related_parts[rid].blob)
        except KeyError:  # картинка по внешней ссылке
            pass

    for pic in _x(p_el, ".//pic:pic[not(ancestor::mc:Fallback)]"):
        rids = _x(pic, "./pic:blipFill/a:blip/@r:embed")
        if not rids:
            continue
        ext = _x(pic, "./pic:spPr/a:xfrm/a:ext") or _x(pic, "ancestor::*[self::wp:inline or self::wp:anchor][1]/wp:extent")
        if ext:
            try:
                if int(ext[0].get("cx")) < MIN_EMU_W or int(ext[0].get("cy")) < MIN_EMU_H:
                    continue
            except (TypeError, ValueError):
                pass
        add(rids[0])
    for rid in _x(p_el, ".//v:imagedata[not(ancestor::mc:Fallback)]/@r:id"):  # старый формат (VML)
        add(rid)
    return blobs


def _extract_docx(path: Path, image_sink=None) -> str:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = docx.Document(str(path))
    parts: list[str] = []

    def emit_images(p_el):
        if image_sink is None:
            return
        blobs = _paragraph_pictures(doc, p_el)
        if not blobs:
            return
        # подпись к картинке часто лежит в текстовом поле рядом с ней
        from .images import clean_caption
        caption = clean_caption("".join(_x(p_el, ".//w:txbxContent//w:t[not(ancestor::mc:Fallback)]/text()")))
        for blob in blobs:
            n = image_sink(blob, caption)
            if n:
                parts.append(f"[[IMG:{n}]]")
                caption = ""  # подпись относится к первой картинке

    def emit_paragraph(p: Paragraph):
        emit_images(p._p)
        t = p.text.strip()
        if not t:
            return
        style = (p.style.name if p.style is not None else "") or ""
        # заголовки помечаем «# »: по ним документ режется на разделы
        if style.lower().startswith(("heading", "заголовок", "title")) and len(t) <= 150:
            t = "# " + t
        parts.append(t)

    # тело документа по порядку: абзацы и таблицы там, где они стоят
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            emit_paragraph(Paragraph(child, doc))
        elif tag == "tbl":
            for row in Table(child, doc).rows:
                cells = [c.text.strip() for c in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
    return "\n".join(parts)


_AUTO_ALT_RE = re.compile(r"автоматическ|automatically generated|^(picture|image|рисунок|изображение|фото|screenshot)\s*\d*$|\.(png|jpe?g|gif|emf|wmf|bmp)$", re.I)
_SLIDE_MIN_SIDE = 0.06      # картинка уже или ниже 6% слайда: значок
_SLIDE_MIN_AREA = 0.03      # меньше 3% площади слайда: значок или логотип
_SLIDE_REPEAT = 3           # одна и та же картинка на трёх и более слайдах: логотип или водяной знак
_CAPTION_MAX_CHARS = 140


def _service_placeholders():
    from pptx.enum.shapes import PP_PLACEHOLDER as P
    return {P.SLIDE_NUMBER, P.FOOTER, P.DATE, P.HEADER}


_SERVICE_PLACEHOLDERS = _service_placeholders()


def _group_transform(grp_el, parent):
    """Пересчёт координат вложенной группы фигур в координаты слайда: (смещение x, y, масштаб x, y)."""
    ox, oy, sx, sy = parent
    xfrm = _x(grp_el, "./p:grpSpPr/a:xfrm")
    if not xfrm:
        return parent
    xf = xfrm[0]
    try:
        off, ext = xf.find("a:off", _NS), xf.find("a:ext", _NS)
        choff, chext = xf.find("a:chOff", _NS), xf.find("a:chExt", _NS)
        gx, gy, gw, gh = int(off.get("x")), int(off.get("y")), int(ext.get("cx")), int(ext.get("cy"))
        cx, cy, cw, ch = int(choff.get("x")), int(choff.get("y")), int(chext.get("cx")), int(chext.get("cy"))
    except (AttributeError, TypeError, ValueError):
        return parent
    kx, ky = (gw / cw if cw else 1.0), (gh / ch if ch else 1.0)
    # координаты ребёнка: x_слайда = ox + sx * (gx + (x - cx) * kx)
    return (ox + sx * (gx - cx * kx), oy + sy * (gy - cy * ky), sx * kx, sy * ky)


def _walk_slide(shapes, tr=(0.0, 0.0, 1.0, 1.0)):
    """Фигуры слайда в порядке следования (в том числе внутри групп) с их положением на слайде: (фигура, left, top, width, height)."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from _walk_slide(shape.shapes, _group_transform(shape._element, tr))
            continue
        try:
            l, t, w, h = shape.left, shape.top, shape.width, shape.height
        except Exception:
            continue
        if None in (l, t, w, h):
            continue
        ox, oy, sx, sy = tr
        yield shape, ox + sx * l, oy + sy * t, sx * w, sy * h


def _crop_blob(shape, blob: bytes) -> bytes:
    """Если картинка на слайде обрезана, берём только видимую часть (в файле лежит оригинал целиком)."""
    try:
        cl, cr, ct, cb = shape.crop_left, shape.crop_right, shape.crop_top, shape.crop_bottom
    except AttributeError:
        return blob
    if not any(v > 0.005 for v in (cl, cr, ct, cb)) or any(v < 0 for v in (cl, cr, ct, cb)) or cl + cr >= 0.98 or ct + cb >= 0.98:
        return blob
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(blob))
        im.load()
        w, h = im.size
        im = im.crop((int(w * cl), int(h * ct), int(w * (1 - cr)), int(h * (1 - cb))))
        buf = io.BytesIO()
        im.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return blob


def _alt_text(shape) -> str:
    try:
        alt = (_x(shape._element, "./p:nvPicPr/p:cNvPr/@descr") or [""])[0].strip()
    except Exception:
        return ""
    if len(alt) < 8 or _AUTO_ALT_RE.search(alt) or not (re.search("[А-Яа-я]", alt) or len(alt.split()) >= 3):
        return ""  # пустое, служебное («Picture 3», «main-book-image») или автоматически созданное описание
    return alt


def _slide_pictures(slide, sw: int, sh: int, is_first: bool) -> list[dict]:
    """Картинки слайда, достойные урока: без значков, фонов и (на титульном слайде) фотографии докладчика."""
    from pptx.shapes.picture import Picture
    import hashlib

    title_id = slide.shapes.title.shape_id if slide.shapes.title is not None else None
    items = list(_walk_slide(slide.shapes))
    has_text = any(getattr(s, "has_text_frame", False) and s.has_text_frame and s.text_frame.text.strip() for s, *_ in items)
    pics = []
    for z, (shape, l, t, w, h) in enumerate(items):
        if not isinstance(shape, Picture):
            continue
        try:
            blob = shape.image.blob
        except Exception:  # картинка по внешней ссылке
            continue
        if w < sw * _SLIDE_MIN_SIDE or h < sh * _SLIDE_MIN_SIDE or w * h < sw * sh * _SLIDE_MIN_AREA:
            continue
        if w * h >= sw * sh * 0.92 and has_text and z == 0:  # фон на весь слайд
            continue
        pics.append({"blob": blob, "shape": shape, "l": l, "t": t, "w": w, "h": h, "sha": hashlib.sha1(blob).hexdigest()})
    if is_first:
        pics = []  # титульный слайд: фото докладчика и оформление
    # подписи: короткий текст прямо под картинкой и над ней не бывает; берём ближайший снизу, пересекающийся по горизонтали
    cands = []
    for shape, l, t, w, h in items:
        if not getattr(shape, "has_text_frame", False) or not shape.has_text_frame or shape.shape_id == title_id:
            continue
        if shape.is_placeholder and shape.placeholder_format.type in _SERVICE_PLACEHOLDERS:
            continue  # номер слайда, дата, колонтитул
        txt = " ".join(shape.text_frame.text.split())
        if (12 <= len(txt) <= _CAPTION_MAX_CHARS and len(txt.split()) >= 3 and re.match(r"\w", txt) and "<number>" not in txt
                and len([p for p in shape.text_frame.paragraphs if p.text.strip()]) <= 2):
            cands.append((txt, l, t, w, h))
    taken: dict[int, tuple[float, int]] = {}
    for pi, pic in enumerate(pics):
        best = None
        for ci, (txt, l, t, w, h) in enumerate(cands):
            gap = t - (pic["t"] + pic["h"])
            overlap = min(pic["l"] + pic["w"], l + w) - max(pic["l"], l)
            if -sh * 0.03 <= gap <= sh * 0.12 and overlap >= min(pic["w"], w) * 0.3:
                if best is None or gap < best[0]:
                    best = (gap, ci)
        if best:
            prev = taken.get(best[1])
            if prev is None or best[0] < prev[0]:
                taken[best[1]] = (best[0], pi)
    for ci, (gap, pi) in taken.items():
        pics[pi]["caption"] = cands[ci][0]
    for pic in pics:
        pic.setdefault("caption", "")
        if not pic["caption"]:
            pic["caption"] = _alt_text(pic["shape"])
    pics.sort(key=lambda p: (round(p["t"] / (sh * 0.1)), p["l"]))
    return pics


def _extract_pptx(path: Path, image_sink=None) -> str:
    from collections import Counter
    from pptx import Presentation
    from .images import clean_caption

    prs = Presentation(str(path))
    sw, sh = prs.slide_width, prs.slide_height
    slide_pics: list[list[dict]] = []
    repeats: Counter = Counter()
    if image_sink is not None:
        for i, slide in enumerate(prs.slides):
            try:
                pics = _slide_pictures(slide, sw, sh, is_first=(i == 0 and len(prs.slides) > 1))
            except Exception:  # необычная разметка слайда не должна ломать загрузку документа
                pics = []
            slide_pics.append(pics)
            repeats.update({p["sha"] for p in pics})
    parts = []
    for i, slide in enumerate(prs.slides, start=1):
        slide_lines = [f"--- Слайд {i} ---"]
        title_shape = slide.shapes.title
        title_text = ""
        for shape in slide.shapes:
            if shape.has_text_frame:
                is_title = title_shape is not None and shape.shape_id == title_shape.shape_id
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        slide_lines.append("# " + line if is_title and len(line) <= 150 else line)
                        if is_title and not title_text:
                            title_text = line
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        slide_lines.append(" | ".join(cells))
        if image_sink is not None and slide_pics:
            pics = [p for p in slide_pics[i - 1] if repeats[p["sha"]] < _SLIDE_REPEAT]
            for k, pic in enumerate(pics):
                caption = clean_caption(pic["caption"])
                if not caption and len(pics) == 1:
                    caption = clean_caption(title_text)  # единственная картинка слайда: подпись по заголовку
                n = image_sink(_crop_blob(pic["shape"], pic["blob"]), caption)
                if n:
                    slide_lines.append(f"[[IMG:{n}]]")
        if len(slide_lines) > 1:
            parts.extend(slide_lines)
    return "\n".join(parts)


def _extract_pdf(path: Path) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            if page_text.strip():
                parts.append(f"--- Страница {i} ---\n{page_text}")
    return "\n".join(parts)
