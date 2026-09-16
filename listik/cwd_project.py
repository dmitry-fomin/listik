"""Определение проекта по рабочему каталогу.

Чистая функция без обращений к базе, серверу и `os.getcwd()`: список проектов и
cwd передаёт вызывающий (CLI). Так резолвер тестируется юнит-тестами без
инфраструктуры, а решение «ходить ли за списком» остаётся в `bin/listik`.
"""
from __future__ import annotations

import os
from typing import Any


def _field(project: Any, key: str) -> Any:
    """Поле проекта: dict и sqlite3.Row читаются одинаково (у Row нет `.get`)."""
    try:
        return project[key]
    except (KeyError, IndexError, TypeError):
        return None


def _normalize(path: Any) -> str | None:
    """`~` разворачивается, путь приводится к абсолютному, симлинки разрешаются.

    Несуществующий каталог не отбрасывается: сравнение идёт по строке пути, а
    `realpath` разрешает существующий префикс и достраивает остаток.
    """
    if path is None:
        return None
    text = str(path).strip()
    if not text:
        return None
    return os.path.realpath(os.path.expanduser(text))


def _segments(path: str) -> list[str]:
    return [part for part in path.split(os.sep) if part]


def _is_prefix(root: str, path: str) -> bool:
    """`path` равен `root` или лежит внутри него — сравнение по сегментам.

    Сегментное сравнение, а не строковое: `/a/bc` не предок `/a/bcd`.
    """
    root_parts = _segments(root)
    if not root_parts:
        return path.startswith(os.sep)
    return _segments(path)[: len(root_parts)] == root_parts


def detect(projects: list[dict], cwd: str, *,
           projects_root: str | None = None) -> str | None:
    """Slug проекта, которому принадлежит `cwd`, или None.

    Правила: `projects.path` — точное совпадение или предок cwd (самое длинное
    совпадение побеждает, поэтому вложенный проект перебивает объемлющий);
    пустой `path` не участвует; архивированные проекты участвуют наравне;
    каталог внутри `.worktrees/<id>` — тот же проект. Если по `path` ничего не
    совпало, первый сегмент cwd после `projects_root` трактуется как slug — но
    только когда проект с таким slug (без учёта регистра) есть в списке.
    """
    norm_cwd = _normalize(cwd)
    if norm_cwd is None:
        return None

    best_slug: str | None = None
    best_len = -1
    for project in projects or []:
        slug = (_field(project, "slug") or "").strip()
        path = _normalize(_field(project, "path"))
        if not slug or path is None:
            continue
        if _is_prefix(path, norm_cwd) and len(path) > best_len:
            best_slug, best_len = slug, len(path)
    if best_slug is not None:
        return best_slug

    root = _normalize(projects_root)
    if not root or not _is_prefix(root, norm_cwd):
        return None
    rest = _segments(norm_cwd)[len(_segments(root)):]
    if not rest:
        return None
    candidate = rest[0]
    for project in projects or []:
        slug = (_field(project, "slug") or "").strip()
        if slug and slug.casefold() == candidate.casefold():
            return slug
    return None
