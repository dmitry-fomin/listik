"""Нормализация исполнителей: Фомин Дмитрий / Dmitriy Fomin / user.email -> один актор.

Правило: слияние только по явному списку алиасов (exact match по нормализованной
строке). Никакой нечёткой склейки — иначе Виталий и Водолазкин станут одним человеком.
"""
from __future__ import annotations

import re
import sqlite3

# Канонические акторы: ключ -> человекочитаемое имя
CANONICAL = {
    "me": "Дмитрий Фомин",
    "agent:claude": "Claude",
    "agent:dsh": "DeepSeek Harness",
    "agent:grok": "Grok",
    "agent:codex": "Codex",
    # agent:gemini нет: харнесс выведен из поставки, иначе seed_actors на каждом
    # init возвращал бы его строку в `actors`; резолв имени живёт в AGENT_HINTS.
    "agent:pi-glm": "pi · GLM",
    "agent:pi-deepseek": "pi · DeepSeek",
    "agent:local": "локальный субагент",
}

# Явные алиасы автора (нормализованная форма -> ключ)
ALIASES = {
    "фомин дмитрий": "me",
    "фомин дмитрий алексеевич": "me",
    "дмитрий фомин": "me",
    "дмитрий фомин алексеевич": "me",
    "dmitriy fomin": "me",
    "dmitry fomin": "me",
    "dmitri fomin": "me",
    "user.email": "me",
    "dfomin": "me",
    "d.fomin": "me",
}

AGENT_HINTS = (
    # pi-* раньше "deepseek": иначе pi-deepseek уйдёт в agent:dsh
    ("pi-glm", "agent:pi-glm"),
    ("pi-deepseek", "agent:pi-deepseek"),
    ("claude", "agent:claude"),
    ("opus", "agent:claude"),
    ("sonnet", "agent:claude"),
    ("fable", "agent:claude"),
    ("dsh", "agent:dsh"),
    ("deepseek", "agent:dsh"),
    ("grok", "agent:grok"),
    ("codex", "agent:codex"),
    ("gemini", "agent:gemini"),
)

_WS_RE = re.compile(r"\s+")


def norm(raw: str | None) -> str:
    if not raw:
        return ""
    s = raw.strip().lower().replace("ё", "е")
    s = _WS_RE.sub(" ", s)
    return s


def resolve(raw: str | None, conn: sqlite3.Connection | None = None) -> tuple[str | None, str]:
    """Возвращает (actor_key, kind). kind: human|agent|unknown.

    Неизвестный `agent:<имя>` (нет в подсказках и в actor_aliases) — агент.
    """
    n = norm(raw)
    if not n:
        return None, "unknown"

    for hint, key in AGENT_HINTS:
        if hint in n:
            return key, "agent"

    if n in ALIASES:
        return ALIASES[n], "human"

    if conn is not None:
        try:
            row = conn.execute("SELECT actor FROM actor_aliases WHERE raw = ?", (n,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row:
            actor = row["actor"]
            kind = "agent" if actor.startswith("agent:") else "human"
            return actor, kind

    if n.startswith("agent:") and len(n) > len("agent:"):
        return n, "agent"

    # Неизвестный: оставляем как есть, ключ помечаем человеком —
    # автор добавит алиас, если это тот же человек под другим именем.
    return n, "human"


def same_actor(a: str | None, b: str | None, conn: sqlite3.Connection | None = None) -> bool:
    """Один и тот же актор под разными написаниями?

    Единственное правило тождества держателей в Listik: `claude`/`agent:claude`/
    `sonnet-judge` — один актор, `dsh`/`agent:dsh`/`dsh/deepseek-flash` — один,
    `alice` и `alicia` — разные. Ничего сверх того, что уже даёт `resolve`
    (алиасы, подсказки агентов, `actor_aliases`, нормализация `norm`).

    Пустая строка (и `None`) не тождественна ничему, включая другую пустую:
    «держателя нет» — это отсутствие актора, иначе две карточки без держателя
    выглядели бы как карточки одного и того же.
    """
    if not norm(a) or not norm(b):
        return False
    return resolve(a, conn)[0] == resolve(b, conn)[0]


def remember(conn: sqlite3.Connection, raw: str | None, actor: str | None, kind: str, note: str = "") -> None:
    n = norm(raw)
    if not n or not actor:
        return
    try:
        conn.execute(
            "INSERT INTO actor_aliases(raw, actor) VALUES(?,?) "
            "ON CONFLICT(raw) DO UPDATE SET actor=excluded.actor",
            (n, actor),
        )
    except sqlite3.OperationalError:
        pass


def display(actor: str | None) -> str:
    if not actor:
        return "—"
    if actor in CANONICAL:
        return CANONICAL[actor]
    return actor
