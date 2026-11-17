"""Нарезка исходных документов на фрагменты.

Документ целиком не помещается в запрос к модели (у Yandex GPT контекст ограничен), поэтому:
  1. документ режется на пронумерованные фрагменты по заголовкам и абзацам;
  2. для проектирования структуры модель получает «дайджест» всего документа (заголовки + начало каждого фрагмента)
     и сама указывает, на каких фрагментах основан каждый урок;
  3. при написании урока в запрос попадают только его фрагменты (а если привязки нет - самые близкие по словам).

Всё детерминировано: фрагменты вычисляются из сохранённого текста и не хранятся отдельно.
"""
from __future__ import annotations
import math
import re
from collections import Counter
from dataclasses import dataclass

TARGET = 3500    # желаемый размер фрагмента, символов
HARD = 6000      # абзац длиннее режется на части
MIN_SIZE = 800   # заголовок начинает новый фрагмент, только если в текущем уже не меньше стольких символов

_PAGE_RE = re.compile(r"^--- (Страница|Слайд) (\d+) ---$")
_HEAD_MULTI = re.compile(r"^\d{1,2}(?:\.\d{1,2}){1,3}[.)]?\s+\S")      # 1.2 Название, 2.3.1 Название
_HEAD_SINGLE = re.compile(r"^\d{1,2}[.)]\s+[A-ZА-ЯЁ]")                 # 3. Название
_HEAD_WORD = re.compile(r"^(?:Глава|Раздел|Часть|Приложение|Chapter|Section|Part)\s+\S", re.I)


IMG_MARK_RE = re.compile(r"^\[\[Рисунок (\d+)\]\][^\n]*$", re.M)   # «[[Рисунок 3]] подпись» (см. images.py)


@dataclass
class Chunk:
    id: int
    title: str
    text: str
    loc: str = ""  # «стр. 12» / «слайд 5» - где фрагмент начинается

    def image_ids(self) -> list[int]:
        return [int(k) for k in IMG_MARK_RE.findall(self.text)]

    def label(self) -> str:
        head = f"#{self.id}"
        if self.title:
            head += f" {self.title[:80]}"
        if self.loc:
            head += f", {self.loc}"
        return f"[{head}]"


def _is_heading(line: str) -> bool:
    if line.startswith("# "):  # заголовок, помеченный при разборе файла (стили DOCX, заголовки слайдов)
        return True
    if len(line) > 90 or " | " in line or line[-1:] in ".;,:!?":
        return False
    if _HEAD_WORD.match(line) or _HEAD_MULTI.match(line):
        return True
    if _HEAD_SINGLE.match(line) and len(line) <= 60:
        return True
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 4 and line.upper() == line and len(line) <= 80  # ЗАГОЛОВОК ЗАГЛАВНЫМИ


def _pieces(line: str) -> list[str]:
    """Слишком длинный абзац режем по предложениям."""
    if len(line) <= HARD:
        return [line]
    out, cur = [], ""
    for sent in re.split(r"(?<=[.!?])\s+", line):
        while len(sent) > HARD:  # предложение без точек (таблица, перечень): режем жёстко
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:TARGET])
            sent = sent[TARGET:]
        if cur and len(cur) + len(sent) > TARGET:
            out.append(cur)
            cur = ""
        cur = f"{cur} {sent}".strip()
    if cur:
        out.append(cur)
    return out


def split_chunks(text: str, start_id: int = 0) -> list[Chunk]:
    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_len = 0
    cur_title = cur_loc = ""
    active_title = loc = ""

    def flush():
        nonlocal cur, cur_len
        body = "\n".join(cur).strip()
        if body:
            chunks.append(Chunk(start_id + len(chunks), cur_title, body, cur_loc))
        cur, cur_len = [], 0

    def begin():
        nonlocal cur_title, cur_loc
        cur_title, cur_loc = active_title, loc

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _PAGE_RE.match(line)
        if m:
            loc = ("стр. " if m.group(1) == "Страница" else "слайд ") + m.group(2)
            continue
        if _is_heading(line):
            clean = line[2:].strip() if line.startswith("# ") else line
            if not clean:
                continue
            if cur_len >= MIN_SIZE:
                flush()
            active_title = clean
            if not cur:
                begin()
            cur.append(clean)
            cur_len += len(clean) + 1
            continue
        for piece in _pieces(line):
            if cur and cur_len + len(piece) > TARGET and cur_len >= MIN_SIZE:
                flush()
            if not cur:
                begin()
            cur.append(piece)
            cur_len += len(piece) + 1
    flush()
    return chunks


# ---------- дайджест для структуры ----------

def digest(chunks: list[Chunk], budget: int) -> tuple[str, bool]:
    """Текст всех фрагментов с номерами. Если документ не влезает в budget символов, у каждого фрагмента
    показывается только начало. Возвращает (текст, сокращён ли он)."""
    chunks = [Chunk(c.id, c.title, IMG_MARK_RE.sub("", c.text).strip(), c.loc) for c in chunks]  # для структуры рисунки не нужны
    heads = [c.label() for c in chunks]
    full = sum(len(h) + len(c.text) + 2 for h, c in zip(heads, chunks))
    shortened = full > budget
    if not shortened:
        return "\n\n".join(f"{h}\n{c.text}" for h, c in zip(heads, chunks)), False

    overhead = sum(len(h) + 2 for h in heads)
    k = (budget - overhead) // max(len(chunks), 1)
    k = 0 if k < 100 else min(k, 1500)
    parts = []
    for h, c in zip(heads, chunks):
        if k:
            ex = c.text[:k]
            if len(c.text) > k:
                ex = ex.rsplit(" ", 1)[0] + " …"
            parts.append(f"{h}\n{ex}")
        else:
            parts.append(h)
    out = "\n\n".join(parts)
    if len(out) > budget:  # заголовков тысячи: оставляем сколько влезет
        out = out[:budget].rsplit("\n", 1)[0] + "\n[…список фрагментов сокращён…]"
    return out, True


# ---------- подбор фрагментов под урок ----------

_WORD = re.compile(r"[a-zа-яё0-9]{3,}")


def _stems(s: str, minlen: int = 3) -> list[str]:
    # грубая «основа»: первые 5 букв, чтобы «настройка / настройки / настройку» совпадали
    return [w[:5] for w in _WORD.findall(s.lower().replace("ё", "е")) if len(w) >= minlen]


def image_contexts(chunks: list[Chunk]) -> dict[int, tuple[str, str]]:
    """Для каждого рисунка: (подпись, текст перед ним). По ним решаем, к какому уроку рисунок относится."""
    out = {}
    for c in chunks:
        for m in IMG_MARK_RE.finditer(c.text):
            caption = m.group(0).split("]]", 1)[1].strip()
            out[int(m.group(1))] = (caption, c.text[max(0, m.start() - 300):m.start()])
    return out


def image_relevance(lesson_title: str, caption: str, before: str, ignore: frozenset = frozenset()) -> int:
    """Сколько значимых слов названия урока встречается в подписи (вес 3) и в тексте перед рисунком (вес 1).
    ignore - слова, общие для всех рисунков (например, «справочник»): по ним рисунки не различить."""
    words = set(_stems(lesson_title, 5)) - ignore
    cap, ctx = set(_stems(caption, 5)), set(_stems(before, 5))
    return 3 * len(words & cap) + len(words & ctx)


def common_image_words(contexts: dict[int, tuple[str, str]], ids: list[int]) -> frozenset:
    """Слова, которые есть в подписи каждого из рисунков урока: по ним рисунки не различить."""
    if len(ids) < 2:
        return frozenset()
    sets = [set(_stems(contexts[k][0], 5)) for k in ids if k in contexts]
    return frozenset(set.intersection(*sets)) if len(sets) >= 2 else frozenset()


def rank(chunks: list[Chunk], lesson_title: str, chapter_title: str = "") -> list[tuple[float, Chunk]]:
    """Фрагменты по убыванию близости к теме урока (простой BM25-подобный счёт по основам слов)."""
    query: Counter = Counter()
    for s in _stems(lesson_title):
        query[s] += 2
    for s in _stems(chapter_title):
        query[s] += 1
    tfs = []
    for c in chunks:
        tf = Counter(_stems(c.text))
        for s in _stems(c.title):
            tf[s] += 3  # совпадение с заголовком раздела весит больше
        tfs.append(tf)
    n = len(chunks)
    df = Counter()
    for tf in tfs:
        df.update(tf.keys())
    scored = []
    for c, tf in zip(chunks, tfs):
        score = sum(w * math.log(1 + n / df[s]) * tf[s] / (tf[s] + 1.5) for s, w in query.items() if s in tf)
        scored.append((score, c))
    scored.sort(key=lambda x: (-x[0], x[1].id))
    return scored


def owned_chunks(chunks: list[Chunk], refs: list[int], lesson_title: str, chapter_title: str) -> list[Chunk]:
    """Фрагменты, к которым относится урок: указанные при создании структуры, а без них - самые близкие по словам."""
    by_id = {c.id: c for c in chunks}
    pool = [by_id[r] for r in dict.fromkeys(refs) if r in by_id]
    if pool:
        return pool
    return [c for score, c in rank(chunks, lesson_title, chapter_title) if score > 0][:2]


def select_for_lesson(chunks: list[Chunk], refs: list[int], lesson_title: str,
                      chapter_title: str, budget: int) -> tuple[str, list[int]]:
    """Текст исходных материалов для одного урока, не длиннее budget символов (кроме одного крупного фрагмента),
    и номера рисунков, которые принадлежат этому уроку (стоят в его фрагментах). Пометки чужих рисунков из текста убираются."""
    if not chunks:
        return "", []
    own = owned_chunks(chunks, refs, lesson_title, chapter_title)
    if sum(len(c.text) for c in chunks) <= budget:  # документ небольшой: даём целиком, как раньше
        chosen = chunks
    else:
        by_id = {c.id: c for c in chunks}
        pool = [by_id[r] for r in dict.fromkeys(refs) if r in by_id]
        if pool:
            order = [c for _, c in rank(pool, lesson_title, chapter_title)]
        else:  # привязки нет (урок добавлен вручную или курс старый): ищем по словам
            order = [c for score, c in rank(chunks, lesson_title, chapter_title) if score > 0] or chunks[:3]
        chosen, used = [], 0
        for c in order:
            if chosen and used + len(c.text) > budget:
                continue
            chosen.append(c)
            used += len(c.text)
        chosen.sort(key=lambda c: c.id)
    own_ids = {c.id for c in own}
    mine = [k for c in chosen if c.id in own_ids for k in c.image_ids()]
    keep = set(mine)

    def strip_foreign(text: str) -> str:
        return IMG_MARK_RE.sub(lambda m: m.group(0) if int(m.group(1)) in keep else "", text)

    text = "\n\n".join(
        f"[{c.title}]\n{strip_foreign(c.text)}" if c.title and not c.text.startswith(c.title) else strip_foreign(c.text)
        for c in chosen
    )
    return re.sub(r"\n{3,}", "\n\n", text), mine
