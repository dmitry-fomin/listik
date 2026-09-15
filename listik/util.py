"""Небольшие общие утилиты для модулей Listik.

Здесь собраны только операции без предметной логики: JSON использует уже
единый контракт из :mod:`listik.errors`, конфигурация — штатный загрузчик, а
работа со временем и путями имеет одну точку входа.
"""
from __future__ import annotations

import os
import json
import time
from datetime import datetime
from pathlib import Path

from . import errors

# JSON-контракт уже определён в errors.py; это алиасы, а не второй формат.
json_dumps = errors.json_dumps
json_loads = errors.json_loads
JSONDecodeError = json.JSONDecodeError


def load_config(path: Path | str | None = None) -> dict:
    """Загрузить конфигурацию штатным загрузчиком Listik."""
    from . import config
    return config.load(Path(path) if path is not None else None)


def routing(project: str | None = None, conn=None) -> dict:
    """Получить эффективный маршрут, загрузив конфигурацию штатным способом."""
    from . import config
    return config.routing(project, conn=conn)


def allowed_harnesses(project: str | None, stage: str | None, conn=None) -> list[str]:
    """Список разрешённых исполнителей из эффективной конфигурации."""
    from . import config
    return config.allowed_harnesses(project, stage, conn=conn)


def path(value: Path | str) -> Path:
    """Привести значение к :class:`~pathlib.Path` без дополнительных эффектов."""
    return Path(value)


def expanduser(value: Path | str) -> Path:
    """Привести путь и раскрыть ``~`` (только там, где это делалось раньше)."""
    return path(value).expanduser()


def resolved(value: Path | str) -> Path:
    """Нестрого разрешить путь, сохранив поведение pathlib по умолчанию."""
    return path(value).resolve()


def read_text(value: Path | str, *, encoding: str = "utf-8") -> str:
    """Прочитать текстовый файл с единым кодированием."""
    return path(value).read_text(encoding=encoding)


def stamp() -> str:
    """Локальная отметка времени для имён файлов резервных копий."""
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")


def monotonic() -> float:
    return time.monotonic()


def wall_time() -> float:
    return time.time()


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def env_path(name: str, default: Path | str) -> Path:
    """Прочитать путь из переменной окружения с прежним fallback."""
    value = os.environ.get(name)
    return path(value) if value else path(default)
