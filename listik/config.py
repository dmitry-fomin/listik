"""Конфигурация хаба Listik.

Формат — TOML (читается встроенным tomllib), файл config.toml в корне Listik.
Токен создаётся при первом запуске сервера, права 0600.
"""
from __future__ import annotations

import copy
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import tomllib
import urllib.parse
from pathlib import Path
from typing import Any

from . import errors as errors_mod
from . import paths

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": paths.DEFAULT_PORT,
        # Ключей `mode`/`users` здесь намеренно нет, как и `[assistant]`: `save()`
        # пишет весь merged-словарь, и всё из DEFAULTS попадало бы в чужой
        # config.toml при первом же `ensure_token`. Режим и список людей читают
        # `server_mode()`/`users()` прямо из файла, с запасным значением.
    },
    "auth": {
        "token": "",
    },
    "embed": {
        "enabled": True,
        "model": paths.EMBED_MODEL,
        "url": paths.OLLAMA_URL,
        "batch": paths.EMBED_BATCH,
        "max_chars": paths.EMBED_MAX_CHARS,
    },
    # Раздела [assistant] здесь нет намеренно: это пользовательская настройка
    # (api_key), а не дефолт. С ним в DEFAULTS `ensure_token`/`save` дописывали бы
    # в чужой config.toml `[assistant]` с пустым api_key. Дефолты помощника
    # (base_url, model) живут в `assistant.settings()`.
    "import": {
        "projects_root": str(paths.PROJECTS_ROOT),
        "max_depth": 6,
    },
    "routing": {
        "transitions": {
            # После подготовки ТЗ следующий harness забирает этап ревью сам.
            # Без явного --holder переход освобождает держателя.
            "s1-spec:s2-review": "handoff",
            "s2-review:s3-impl": "handoff",
            "s3-impl:s4-judge": "sticky",
            "s4-judge:s3-impl": "sticky-return",
            "s4-judge:done": "handoff",
        },
        "return_window_hours": 24,
        "projects": {},
    },
    "board": {
        "stale_hours": 24,       # сколько часов без heartbeat считать бросок
        "wip_warn_hours": 8,     # сколько часов на этапе до жёлтого
        "assign_warn_minutes": 15,  # сколько минут «выдана, но не взята» до сигнала
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


#: Ключи routing, которые больше не поддерживаются, но встречаются в старых
#: config.toml и переопределениях проектов. `default_process` валидировался, однако
#: ни на что не влиял: `next_stage` всегда идёт по `PIPELINE_STAGES` (listik-sqh6).
#: `harnesses` ограничивал, какому исполнителю разрешён этап, — с каталогом
#: харнессов (`harnesses`, админка) разрешение не нужно: `claim` его больше не
#: проверяет. Оба ключа читаются из старых конфигов и молча игнорируются.
LEGACY_ROUTING_KEYS = frozenset({"default_process", "harnesses"})


def without_legacy_routing(obj: dict) -> dict:
    """Копия таблицы маршрутизации без устаревших ключей (`LEGACY_ROUTING_KEYS`)."""
    return {key: value for key, value in obj.items() if key not in LEGACY_ROUTING_KEYS}


def load(path: Path | None = None) -> dict:
    cfg_path = Path(path or paths.CONFIG_PATH)
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)
    # DEFAULTS глубоко копируем: _merge отдаёт вложенные словари по ссылке, и
    # мутация загруженного конфига (ensure_token дописывает токен) иначе навсегда
    # оседала бы в DEFAULTS — следующие load() возвращали бы токен, которого нет
    # в файле, а тесты зависели бы от порядка запуска.
    cfg = _merge(copy.deepcopy(DEFAULTS), data)
    _drop_legacy_routing(cfg)
    return cfg


def _drop_legacy_routing(cfg: dict) -> None:
    """Выкинуть устаревшие ключи `[routing]` прямо при загрузке (listik-sqh6).

    `default_process` больше не поддерживается, но старый config.toml с ним обязан
    читаться. Чистим и корень `[routing]`, и переопределения проектов
    `[routing.projects.<slug>]`: тогда ключ не всплывёт ни в действующей таблице, ни
    в config.toml, перезаписанном `ensure_token`/`save`.
    """
    routing_cfg = cfg.get("routing")
    if not isinstance(routing_cfg, dict):
        return
    cfg["routing"] = without_legacy_routing(routing_cfg)
    projects_cfg = cfg["routing"].get("projects")
    if isinstance(projects_cfg, dict):
        cfg["routing"]["projects"] = {
            slug: without_legacy_routing(value) if isinstance(value, dict) else value
            for slug, value in projects_cfg.items()
        }


_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(key: object) -> str:
    """TOML-ключ: голый, если синтаксис позволяет, иначе — в кавычках.

    Голыми в TOML могут быть только ключи из ``[A-Za-z0-9_-]``.  Ключи вроде
    ``s1-spec:s2-review`` (переходы конвейера) или slug проекта с «/» и «.»
    без кавычек делают файл невалидным либо молча распадаются на лишние
    таблицы, поэтому такие ключи экранируем.
    """
    text = str(key)
    if _BARE_KEY_RE.match(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _dump(cfg: dict) -> str:
    """Serialize the (small) config tree as valid TOML.

    The old writer rendered nested routing dictionaries as quoted Python reprs.  A
    token created on a fresh checkout therefore silently destroyed the routing
    configuration on the next read.  Keep this dependency-free and support the
    values used by Listik (scalars, arrays and nested tables).
    """
    def value(v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        return json.dumps(str(v), ensure_ascii=False)

    lines: list[str] = []

    def table(path: list[str], obj: dict) -> None:
        scalars = [(k, v) for k, v in obj.items() if not isinstance(v, dict)]
        if path:
            lines.append("[" + ".".join(_key(part) for part in path) + "]")
        for key, val in scalars:
            lines.append(f"{_key(key)} = {value(val)}")
        if scalars:
            lines.append("")
        for key, val in obj.items():
            if isinstance(val, dict):
                table(path + [str(key)], val)

    # Скаляры корня пишем до таблиц: иначе TOML-парсер отнёс бы их к последней
    # открытой таблице (например, `[routing.projects."demo"]`).
    root_scalars = [(k, v) for k, v in cfg.items() if not isinstance(v, dict)]
    for key, val in root_scalars:
        lines.append(f"{_key(key)} = {value(val)}")
    if root_scalars:
        lines.append("")
    for section, values in cfg.items():
        if isinstance(values, dict):
            table([str(section)], values)
    return "\n".join(lines).rstrip() + "\n"


def _atomic_write(cfg_path: Path, text: str) -> Path:
    """Записать конфиг целиком через временный файл (0600, без обрезка посередине)."""
    # resolve() — чтобы не подменить символическую ссылку config.toml обычным файлом.
    target = cfg_path.resolve()
    # Каталог данных создаётся только при записи: при LISTIK_HOME его может ещё не быть.
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                    prefix=target.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    target.chmod(0o600)
    return cfg_path


def save(cfg: dict, path: Path | None = None) -> Path:
    cfg_path = Path(path or paths.CONFIG_PATH)
    return _atomic_write(cfg_path, _dump(cfg))


def auth_token(cfg: dict) -> str:
    """Токен из `[auth] token`; нет раздела, ключа или значение пустое — пустая строка."""
    return (cfg.get("auth") or {}).get("token") or ""


def ensure_token(cfg: dict | None = None) -> tuple[dict, str]:
    """Возвращает (cfg, token), создавая файл конфига и токен при необходимости."""
    cfg = cfg or load()
    token = auth_token(cfg)
    if not token:
        token = secrets.token_urlsafe(24)
        cfg.setdefault("auth", {})["token"] = token
        save(cfg)
    return cfg, token


def swarm_enabled(cfg: dict | None = None) -> bool:
    """Включён ли рой: `[swarm] enabled` в config.toml.

    Ключа нет — выключен. Раздел целиком в DEFAULTS не кладём: рядом лежат
    ключ и модель проходов plan/rescope, и `ensure_token` не должен их выдумывать.
    В серверном режиме (`[server] mode = "server"`) рой выключен всегда: на общем
    сервере он не нужен (listik-r69k).
    """
    cfg = load() if cfg is None else cfg
    if is_server_mode(cfg):
        return False
    section = cfg.get("swarm")
    if not isinstance(section, dict) or "enabled" not in section:
        return False
    value = section["enabled"]
    if isinstance(value, bool):
        return value
    raise ValueError(f"[swarm] enabled: ожидается true или false, а не {value!r}")


_TABLE_RE = re.compile(r"^\s*\[\s*([^\]]+?)\s*\]\s*(?:#.*)?$")


def _patch_value(text: str, table: str, key: str, literal: str) -> str:
    """Вписать `key = literal` в таблицу `[table]`, не переписывая остальной файл.

    Комментарии и чужие таблицы остаются как были. Полный `save()` сюда не годится:
    он материализует DEFAULTS и стирает комментарии. Таблицы нет — она дописывается
    в конец; ключ есть — заменяется его строка; нет — вставляется сразу за заголовком.
    """
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    section_at: int | None = None
    key_at: int | None = None
    in_table = False
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        header = _TABLE_RE.match(body)
        if header:
            name = header.group(1).strip().strip('"').strip("'")
            in_table = name == table
            if in_table and section_at is None:
                section_at = index
            continue
        if in_table and body.lstrip().startswith(key) and "=" in body.split("#", 1)[0]:
            found = body.split("=", 1)[0].strip()
            if found == key and key_at is None:
                key_at = index
    assignment = f"{key} = {literal}"
    if section_at is None:
        base = text
        if base and not base.endswith(("\n", "\r")):
            base += newline
        if base and not base.endswith(newline * 2):
            base += newline
        return base + f"[{table}]{newline}{assignment}{newline}"
    if key_at is not None:
        old = lines[key_at]
        ending = "\r\n" if old.endswith("\r\n") else ("\n" if old.endswith("\n") else newline)
        indent = re.match(r"^(\s*)", old).group(1)
        lines[key_at] = f"{indent}{assignment}{ending}"
        return "".join(lines)
    header = lines[section_at]
    ending = "\r\n" if header.endswith("\r\n") else "\n"
    if not header.endswith("\n"):  # заголовок — последняя строка без перевода
        lines[section_at] = header + newline
        ending = newline
    lines.insert(section_at + 1, f"{assignment}{ending}")
    return "".join(lines)


def _patch_swarm_enabled(text: str, enabled: bool) -> str:
    """Вписать `enabled` в таблицу `[swarm]` (см. `_patch_value`)."""
    return _patch_value(text, "swarm", "enabled", "true" if enabled else "false")


def set_swarm_enabled(enabled: bool, path: Path | None = None) -> None:
    """Записать `[swarm] enabled`, не трогая остальные ключи, комментарии и таблицы."""
    if not isinstance(enabled, bool):
        raise ValueError("enabled: ожидается bool")
    cfg_path = Path(path or paths.CONFIG_PATH)
    if cfg_path.is_file():
        text = cfg_path.read_text(encoding="utf-8")
        if text.strip():
            try:
                tomllib.loads(text)
            except tomllib.TOMLDecodeError as exc:
                raise ValueError(f"config.toml не читается: {exc}") from exc
        new = _patch_swarm_enabled(text, enabled)
    else:
        literal = "true" if enabled else "false"
        new = f"[swarm]\nenabled = {literal}\n"
    try:
        parsed = tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"config.toml после записи [swarm] enabled не читается: {exc}") from exc
    section = parsed.get("swarm")
    if not isinstance(section, dict) or section.get("enabled") is not enabled:
        raise ValueError("не удалось записать [swarm] enabled")
    _atomic_write(cfg_path, new)


def normalize_url(value: object) -> str:
    """Адрес общего сервера в одном виде: `http(s)://хост[:порт][/префикс]`.

    Схема и хост — в нижнем регистре, порт по умолчанию (80/443) и хвостовой `/`
    снимаются, IPv6 остаётся в скобках. userinfo, query, fragment, кривой порт —
    `errors.BadArgument` с причиной.
    """
    if not isinstance(value, str):
        raise errors_mod.BadArgument(f"адрес сервера должен быть строкой, а не {value!r}")
    text = value.strip()

    def bad(reason: str) -> errors_mod.BadArgument:
        return errors_mod.BadArgument(f"адрес сервера {text!r}: {reason}")

    if not text:
        raise bad("пустой")
    if any(ch.isspace() or not ch.isprintable() for ch in text):
        raise bad("пробелы и непечатные символы недопустимы")
    try:
        parts = urllib.parse.urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise bad(f"не разбирается ({exc})") from exc
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https") or not text.lower().startswith(scheme + "://"):
        raise bad("нужна схема http:// или https://")
    if "@" in parts.netloc:
        raise bad("логин и пароль в адресе недопустимы")
    if "?" in text or "#" in text:
        raise bad("query (?…) и fragment (#…) недопустимы")
    host = (parts.hostname or "").lower()
    if not host:
        raise bad("нет хоста")
    if parts.netloc.endswith(":"):
        raise bad("пустой порт")
    if port == 0:
        raise bad("порт должен быть 1–65535")
    if ":" in host:
        host = f"[{host}]"
    if port is not None and port != {"http": 80, "https": 443}[scheme]:
        host = f"{host}:{port}"
    return f"{scheme}://{host}{parts.path.rstrip('/')}"


def same_server(a: str, b: str) -> bool:
    """Один ли это сервер: сравнение адресов после `normalize_url`."""
    return normalize_url(a) == normalize_url(b)


def remote(cfg: dict | None = None) -> dict | None:
    """Общий сервер клиента из `[remote]`: `{"url", "token"}` или `None`, если не задан."""
    cfg = load() if cfg is None else cfg
    section = cfg.get("remote")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise errors_mod.BadArgument("remote: в config.toml ожидается таблица [remote]")
    url = section.get("url")
    if url is None or url == "":
        return None
    try:
        normalized = normalize_url(url)
    except errors_mod.BadArgument as exc:
        raise errors_mod.BadArgument(f"remote.url = {url!r} в config.toml: {exc}") from exc
    token = section.get("token", "")
    if not isinstance(token, str):
        raise errors_mod.BadArgument("remote.token в config.toml: ожидается строка")
    return {"url": normalized, "token": token}


def _plain_table(text: str, parsed: dict, name: str) -> None:
    """`name` в файле — либо нет, либо обычная таблица `[name]`; иначе ValueError."""
    if name not in parsed:
        return
    has_header = any(
        (m := _TABLE_RE.match(line)) and m.group(1).strip().strip('"').strip("'") == name
        for line in text.splitlines())
    if not isinstance(parsed[name], dict) or not has_header:
        raise ValueError(f"config.toml: {name} задан не таблицей [{name}] "
                         "(инлайн, точечные ключи или скаляр) — поправь файл руками")


def set_remote(url: str, token: str, owner: str | None = None,
               path: Path | None = None) -> None:
    """Записать `[remote] url/token` (и `[auth] owner`, если передан), не трогая остальное."""
    url = normalize_url(url)
    if not isinstance(token, str):
        raise ValueError("token: ожидается строка")
    owner = owner.strip() if isinstance(owner, str) else None
    cfg_path = Path(path or paths.CONFIG_PATH)
    text = cfg_path.read_text(encoding="utf-8") if cfg_path.is_file() else ""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"config.toml не читается: {exc}") from exc
    _plain_table(text, before, "remote")
    if owner:
        _plain_table(text, before, "auth")

    def lit(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    # token раньше url: новая строка встаёт сразу за заголовком, url окажется первым
    new = _patch_value(text, "remote", "token", lit(token))
    new = _patch_value(new, "remote", "url", lit(url))
    if owner:
        new = _patch_value(new, "auth", "owner", lit(owner))
    expected = copy.deepcopy(before)
    expected.setdefault("remote", {}).update(url=url, token=token)
    if owner:
        expected.setdefault("auth", {})["owner"] = owner
    try:
        parsed = tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"config.toml после записи [remote] не читается: {exc}") from exc
    if parsed != expected:
        raise ValueError("не удалось записать [remote]: файл после правки не совпал с ожидаемым")
    _atomic_write(cfg_path, new)


#: Режимы работы хаба: «локальный» (один человек, владелец задачи не нужен) и
#: «серверный» (общий Listik, у задачи есть владелец-человек из `server.users`).
SERVER_MODES = ("local", "server")


def server_mode(cfg: dict | None = None) -> str:
    """Режим хаба из `[server] mode`; по умолчанию — `"local"`."""
    cfg = load() if cfg is None else cfg
    value = (cfg.get("server") or {}).get("mode", "local")
    if not isinstance(value, str) or value not in SERVER_MODES:
        raise ValueError(
            f"server.mode: ожидается \"local\" или \"server\", а не {value!r}")
    return value


def is_server_mode(cfg: dict | None = None) -> bool:
    """True, если хаб поднят в серверном режиме (владелец задачи включён)."""
    return server_mode(cfg) == "server"


def users(cfg: dict | None = None) -> list[str]:
    """Закрытый список людей из `[server] users`; по умолчанию пустой."""
    cfg = load() if cfg is None else cfg
    value = (cfg.get("server") or {}).get("users", [])
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("server.users: ожидается список имён")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"server.users: непустое имя-строка ожидается, а не {item!r}")
        out.append(item)
    return out


def check_owner(owner: str | None, cfg: dict | None = None) -> str | None:
    """Владелец, каким его надо записать: `None` — «не представился».

    В локальном режиме владелец игнорируется полностью (даже мусорный), поэтому
    всегда `None`. В серверном пустое значение — то же «не представился», а имя
    не из `server.users` — `errors.BadArgument`. Регистр не нормализуется: имя
    в конфиге и есть каноническое.
    """
    cfg = load() if cfg is None else cfg
    if not is_server_mode(cfg):
        return None
    if owner is None:
        return None
    value = owner.strip() if isinstance(owner, str) else str(owner).strip()
    if not value:
        return None
    if value not in users(cfg):
        raise errors_mod.BadArgument(f"владелец {value!r} не в списке server.users")
    return value


def default_owner(cfg: dict | None = None) -> str:
    """Владелец по умолчанию для этой машины — `[auth] owner` (может быть пустым)."""
    cfg = load() if cfg is None else cfg
    value = (cfg.get("auth") or {}).get("owner", "")
    return value.strip() if isinstance(value, str) else ""


def routing(project: str | None = None, conn=None) -> dict:
    """Return routing config, optionally merged with project-specific overrides."""
    cfg = load()
    base = dict(cfg.get("routing") or {})
    projects = base.pop("projects", {}) or {}
    override = projects.get(project) if project and isinstance(projects, dict) else None
    if project and conn is not None:
        try:
            row = conn.execute("SELECT routing FROM projects WHERE slug = ?", (project,)).fetchone()
            raw = row[0] if row else None
            db_override = json.loads(raw) if raw else None
        except (sqlite3.Error, ValueError) as exc:
            print(f"routing {project}: переопределение из базы не прочитано: {exc}",
                  file=sys.stderr)
            db_override = None
        if db_override is not None and not isinstance(db_override, dict):
            print(f"routing {project}: переопределение из базы не объект, а {type(db_override).__name__}",
                  file=sys.stderr)
        elif db_override is not None:
            override = _merge(override if isinstance(override, dict) else {}, db_override)
    if project and isinstance(override, dict):
        base = _merge(base, override)
    # Устаревшие ключи выкидываем в самом конце: они могли прийти и из config.toml,
    # и из переопределения проекта — наружу действующая таблица уходит уже без них.
    return without_legacy_routing(base)

_TRANSITION_KINDS = {"sticky", "handoff", "sticky-return"}
#: «sticky» и «sticky-return» держателя не снимают; «handoff» снимает его, если
#: `stage` не передали явного `--holder`: явный держатель — это выдача, её пишет
#: `store.next_stage`/`stage_unchanged` (listik-udop).


def validate_routing(obj: Any) -> dict:
    """Проверить и нормализовать переопределение маршрутизации проекта.

    Принимает словарь с любым подмножеством ключей `transitions`,
    `return_window_hours`. Поднимает ``errors.BadArgument`` (подкласс
    ``ValueError``) с текстом на русском, если форма не соответствует
    ожидаемой. Пустой словарь — валиден (значит «нет
    переопределений»). Устаревшие ключи (`default_process`, `harnesses`) молча
    игнорируются: старые переопределения проектов не должны ломать ни чтение,
    ни перезапись.
    """
    if not isinstance(obj, dict):
        raise errors_mod.BadArgument("routing: ожидается объект (словарь)")
    from . import store as store_mod
    stages = set(store_mod.PIPELINE_STAGES)

    out: dict[str, Any] = {}
    for key, value in obj.items():
        if key in LEGACY_ROUTING_KEYS:
            continue
        if key == "transitions":
            if not isinstance(value, dict):
                raise errors_mod.BadArgument("routing: transitions должен быть словарём")
            transitions: dict[str, str] = {}
            for tkey, tval in value.items():
                if not isinstance(tkey, str) or ":" not in tkey:
                    raise errors_mod.BadArgument(f"routing: неверный ключ перехода: {tkey}")
                left, right = tkey.split(":", 1)
                if left not in stages or (right != "done" and right not in stages):
                    raise errors_mod.BadArgument(f"routing: неизвестный этап в переходе: {tkey}")
                if tval not in _TRANSITION_KINDS:
                    raise errors_mod.BadArgument(f"routing: неизвестный вид перехода {tkey}: {tval}")
                transitions[tkey] = tval
            out["transitions"] = transitions
        elif key == "return_window_hours":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise errors_mod.BadArgument("routing: return_window_hours должен быть числом > 0")
            out["return_window_hours"] = value
        else:
            raise errors_mod.BadArgument(f"routing: неизвестный ключ {key}")
    return out

def transition_kind(project: str | None, from_stage: str | None, to_stage: str | None, conn=None) -> str:
    if not from_stage:
        # Задача без этапа ещё не была ничьим "предыдущим этапом" — заводить её в
        # конвейер не то же самое, что передавать другому harness, держателя не снимаем.
        return "sticky"
    if from_stage == to_stage:
        # Повторная выдача на том же этапе (`stage <id> --to s3-impl --holder <кто>`)
        # — не передача: держателя не снимаем, иначе выдача молча теряла бы его.
        # Ставит его `store.stage_unchanged` — явный `--holder` там ещё и пишет
        # новое назначение, сбрасывая «взята» у прошлого круга (listik-udop).
        return "sticky"
    r = routing(project, conn=conn)
    return str((r.get("transitions") or {}).get(f"{from_stage}:{to_stage}", "handoff"))
