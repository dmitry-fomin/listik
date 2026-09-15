"""Small, side-effect free helpers shared by :mod:`listik.store`.

Keeping these operations in one place makes the store's write paths use the
same lookup, validation and response conventions without changing the data
returned by the public API.
"""
from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from typing import Any

from . import errors

TASK_BY_ID_SQL = "SELECT * FROM tasks WHERE id = ?"
TASK_EXISTS_SQL = "SELECT 1 FROM tasks WHERE id = ?"


def task_row(conn: sqlite3.Connection, task_id: str, *, required: bool = True) -> sqlite3.Row | None:
    """Load one task row, applying the canonical ``NotFound`` error."""
    row = conn.execute(TASK_BY_ID_SQL, (task_id,)).fetchone()
    if row is None and required:
        raise errors.NotFound(f"задача не найдена: {task_id}")
    return row


def task_exists(conn: sqlite3.Connection, task_id: str) -> bool:
    return conn.execute(TASK_EXISTS_SQL, (task_id,)).fetchone() is not None


def json_list(value: Any) -> list:
    """Decode a JSON list column; malformed or non-list values mean no items."""
    try:
        parsed = json.loads(value or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def dict_rows(rows: Iterable[sqlite3.Row]) -> list[dict]:
    """Convert sqlite rows to plain dictionaries without altering their order."""
    return [dict(row) for row in rows]


def normalize_route(value: Any) -> str:
    """Validate and normalize a route field exactly as the store historically did."""
    if not isinstance(value, str):
        raise ValueError("маршрут должен быть строкой — ключом из routes.json")
    return value.strip()
