"""Пути и константы хаба задач Listik.

Всё лежит прямо в корне репозитория Listik: база, конфиг, сервер (`bin/listik`),
пакет `listik/` и доска `web/`. Отдельной папки hub больше нет.
"""
from __future__ import annotations

import os
from pathlib import Path

# Корень репозитория Listik — родитель пакета listik/
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("LISTIK_DB", ROOT_DIR / "listik.db"))
CONFIG_PATH = Path(os.environ.get("LISTIK_CONFIG", ROOT_DIR / "config.toml"))
# Лог сервера и непойманных исключений CLI: трейсбеки пишутся только сюда.
LOG_PATH = Path(os.environ.get("LISTIK_LOG", ROOT_DIR / "listik.log"))
WEB_DIR = ROOT_DIR / "web"

# Корень, внутри которого ищутся проекты с .beads
PROJECTS_ROOT = Path(os.environ.get("LISTIK_PROJECTS_ROOT", Path.home() / "Projects"))

# Ollama
OLLAMA_URL = os.environ.get("LISTIK_OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("LISTIK_EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.environ.get("LISTIK_EMBED_DIM", "1024"))
EMBED_BATCH = int(os.environ.get("LISTIK_EMBED_BATCH", "16"))
# bge-m3 держит 8192 токена, но длинные тексты режем: больше контекста — больше времени
EMBED_MAX_CHARS = int(os.environ.get("LISTIK_EMBED_MAX_CHARS", "6000"))

DEFAULT_PORT = int(os.environ.get("LISTIK_PORT", "8787"))
