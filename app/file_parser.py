"""Извлечение текста из DOCX / PPTX / PDF для последующей передачи в LLM."""
from pathlib import Path

MAX_CHARS = 60_000  # ограничение, чтобы не раздувать промпт к LLM


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        text = _extract_docx(path)
    elif suffix == ".pptx":
        text = _extract_pptx(path)
    elif suffix == ".pdf":
        text = _extract_pdf(path)
    else:
        raise ValueError(f"Неподдерживаемый формат файла: {suffix}")
    return text[:MAX_CHARS]


def _extract_docx(path: Path) -> str:
    import docx

    doc = docx.Document(str(path))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_pptx(path: Path) -> str:
    from pptx import Presentation

    prs = Presentation(str(path))
    parts = []
    for i, slide in enumerate(prs.slides, start=1):
        slide_lines = [f"--- Слайд {i} ---"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    line = "".join(run.text for run in para.runs).strip()
                    if line:
                        slide_lines.append(line)
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells]
                    if any(cells):
                        slide_lines.append(" | ".join(cells))
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
