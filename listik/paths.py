"""Пути и константы хаба задач Listik.

Код лежит в репозитории Listik (сервер `bin/listik`, пакет `listik/`, доска `web/`),
данные — в каталоге данных: `LISTIK_HOME`, а без него — по-прежнему корень репозитория.
Отдельной папки hub больше нет.
"""
from __future__ import annotations

import os
from pathlib import Path

# Корень репозитория Listik — родитель пакета listik/. Отсюда считается только код.
ROOT_DIR = Path(__file__).resolve().parent.parent
# Каталог данных: `LISTIK_HOME` (пустая строка — «не задана»), иначе корень репозитория.
# Отсюда считаются база, конфиг, лог, pid-файл и каталог логов запусков.
_HOME = (os.environ.get("LISTIK_HOME") or "").strip()
DATA_DIR = Path(_HOME).expanduser() if _HOME else ROOT_DIR
DB_PATH = Path(os.environ.get("LISTIK_DB", DATA_DIR / "listik.db"))
CONFIG_PATH = Path(os.environ.get("LISTIK_CONFIG", DATA_DIR / "config.toml"))
# Лог сервера и непойманных исключений CLI: трейсбеки пишутся только сюда.
LOG_PATH = Path(os.environ.get("LISTIK_LOG", DATA_DIR / "listik.log"))
PID_PATH = DATA_DIR / "listik.pid"
LOGS_DIR = DATA_DIR / "logs"
WEB_DIR = ROOT_DIR / "web"

# Корень, внутри которого ищутся проекты
PROJECTS_ROOT = Path(os.environ.get("LISTIK_PROJECTS_ROOT", Path.home() / "Projects"))

# Ollama
OLLAMA_URL = os.environ.get("LISTIK_OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("LISTIK_EMBED_MODEL", "bge-m3")
EMBED_DIM = int(os.environ.get("LISTIK_EMBED_DIM", "1024"))
EMBED_BATCH = int(os.environ.get("LISTIK_EMBED_BATCH", "16"))
# bge-m3 держит 8192 токена, но длинные тексты режем: больше контекста — больше времени
EMBED_MAX_CHARS = int(os.environ.get("LISTIK_EMBED_MAX_CHARS", "6000"))

DEFAULT_PORT = int(os.environ.get("LISTIK_PORT", "8787"))
