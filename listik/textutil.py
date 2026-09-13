"""Работа с текстом: очистка markdown для индекса, чанкинг, хеши."""
from __future__ import annotations

import hashlib
import re

_FENCE_RE = re.compile(r"```.*?```", re.S)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_HTML_RE = re.compile(r"<[^>]+>")
_MD_MARKS_RE = re.compile(r"(?m)^\s{0,3}#{1,6}\s*|^\s{0,3}>\s?|^\s{0,3}[-*+]\s+|\*\*|__|~~")
_WS_RE = re.compile(r"[ \t]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_DIFF_LINE_RE = re.compile(r"(?m)^(diff --git|\+\+\+|---|@@).*$")


def clean_markdown(text: str | None, *, keep_code: bool = False) -> str:
    """Убирает markdown-разметку, оставляя смысловой текст для индекса."""
    if not text:
        return ""
    out = text
    if not keep_code:
        out = _FENCE_RE.sub(" ", out)
    else:
        out = re.sub(r"```[a-zA-Z0-9]*\n?", " ", out)
    out = _DIFF_LINE_RE.sub("", out)
    out = _LINK_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)
    out = _HTML_RE.sub(" ", out)
    out = _MD_MARKS_RE.sub("", out)
    out = out.replace("|", " ").replace("\\n", " ")
    out = _WS_RE.sub(" ", out)
    out = _MULTI_NL_RE.sub("\n\n", out)
    return out.strip()


def text_hash(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update((p or "").encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:32]


def clip(text: str, limit: int) -> str:
    """Обрезает текст по границе слова — для эмбеддингов."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sp = cut.rfind(" ")
    if sp > limit * 0.6:
        cut = cut[:sp]
    return cut + " …"


def is_ascii_word(word: str) -> bool:
    return all(ord(ch) < 128 for ch in word)


def fts_query(user_query: str) -> str:
    """Превращает свободный запрос в безопасное выражение FTS5.

    Каждый токен оборачивается в кавычки (спасает от синтаксиса FTS5),
    кириллица дополнительно ищется префиксом.
    """
    tokens = re.findall(r"[\w\-.]+", user_query, flags=re.UNICODE)
    tokens = [t for t in tokens if t.strip("-._")]
    if not tokens:
        return ""
    parts = []
    for tok in tokens:
        safe = tok.replace('"', "")
        parts.append(f'"{safe}"')
    return " AND ".join(parts)


def fts_query_or(user_query: str) -> str:
    """Мягкий вариант: ИЛИ вместо И — чтобы широкий запрос что-то находил."""
    tokens = re.findall(r"[\w\-.]+", user_query, flags=re.UNICODE)
    tokens = [t for t in tokens if t.strip("-._")]
    if not tokens:
        return ""
    return " OR ".join(f'"{t.replace(chr(34), "")}"' for t in tokens)
