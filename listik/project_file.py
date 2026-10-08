"""Файл проекта `.listik.toml`: на какой сервер и в какой проект идут задачи репозитория.

Файл коммитится вместе с репозиторием, поэтому в нём только два ключа — `server`
(`"local"` или URL общего сервера) и `project` (slug). Токенов и путей там быть не
должно: любой другой ключ — отказ.
"""
from __future__ import annotations

import os
import tomllib

from . import config as config_mod
from . import errors

FILENAME = ".listik.toml"
_KEYS = ("server", "project")


def find(start: str | os.PathLike) -> dict | None:
    """Первый `.listik.toml` от `start` вверх, не выше корня git-репозитория.

    Без `realpath` (на macOS `/var` — симлинк) и без `getcwd`: чистая функция.
    Нет файла — `None`; есть — `{"path", "root", "server", "project"}`.
    """
    current = os.path.abspath(os.fspath(start))
    while True:
        candidate = os.path.join(current, FILENAME)
        if os.path.isfile(candidate):
            return _read(candidate)
        if os.path.exists(os.path.join(current, ".git")):
            return None
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def _read(path: str) -> dict:
    def bad(reason: str) -> errors.ListikError:
        return errors.ListikError(
            f"{path}: {reason}", code=errors.BAD_ARGUMENT,
            hint=f'в {FILENAME} два ключа: server = "local" или URL, project = "<slug>"')

    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise bad(f"файл не читается ({exc.strerror or exc})") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise bad("файл не в UTF-8") from exc
    except tomllib.TOMLDecodeError as exc:
        raise bad(f"не TOML ({exc})") from exc
    extra = sorted(key for key in data if key not in _KEYS)
    if extra:
        raise bad(f"недопустимые ключи: {', '.join(extra)} — секретам и путям здесь не место")
    server = data.get("server")
    if server is None:
        raise bad("нет ключа server")
    if not isinstance(server, str):
        raise bad("server должен быть строкой")
    if server.lower() == "local":
        server = "local"
    else:
        try:
            server = config_mod.normalize_url(server)
        except errors.BadArgument as exc:
            raise bad(f"server: {exc}") from exc
    project = data.get("project")
    if project is None:
        raise bad("нет ключа project")
    if not isinstance(project, str):
        raise bad("project должен быть строкой")
    if not project:
        raise bad("project пустой")
    if project != project.strip():
        raise bad("project с пробелами по краям")
    if project.startswith(("/", "~")):
        raise bad("project — slug проекта, а не путь")
    return {"path": path, "root": os.path.dirname(path), "server": server, "project": project}
