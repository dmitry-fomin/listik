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
import tempfile
import tomllib
from pathlib import Path
from typing import Any

from . import paths

DEFAULTS: dict[str, Any] = {
    "server": {
        "host": "127.0.0.1",
        "port": paths.DEFAULT_PORT,
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
        # Откуда разово забираем задачи из старых .beads
        "projects_root": str(paths.PROJECTS_ROOT),
        "max_depth": 6,
    },
    "routing": {
        "harnesses": {
            "s1-spec": ["claude", "dsh", "codex", "grok"],
            "s2-review": ["claude", "dsh", "codex", "grok"],
            "s3-impl": ["codex", "dsh", "claude", "grok"],
            "s4-judge": ["claude", "dsh", "codex", "grok"],
        },
        "transitions": {
            "s1-spec:s2-review": "sticky",
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
#: ни на что не влиял: `next_stage` всегда идёт по `PIPELINE_STAGES`. Ключ убран
#: (listik-sqh6), но читаться старый конфиг обязан по-прежнему — молча игнорируем.
LEGACY_ROUTING_KEYS = frozenset({"default_process"})


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


def save(cfg: dict, path: Path | None = None) -> Path:
    cfg_path = Path(path or paths.CONFIG_PATH)
    text = _dump(cfg)
    # Пишем через временный файл в том же каталоге: параллельный читатель (второй
    # CLI, сервер, тесты) не увидит обрезанный TOML, а mkstemp сразу даёт 0600,
    # так что токен не засветится даже на миг. resolve() — чтобы не подменить
    # символическую ссылку config.toml обычным файлом.
    target = cfg_path.resolve()
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


def ensure_token(cfg: dict | None = None) -> tuple[dict, str]:
    """Возвращает (cfg, token), создавая файл конфига и токен при необходимости."""
    cfg = cfg or load()
    token = (cfg.get("auth") or {}).get("token") or ""
    if not token:
        token = secrets.token_urlsafe(24)
        cfg.setdefault("auth", {})["token"] = token
        save(cfg)
    return cfg, token


def routing(project: str | None = None, conn=None) -> dict:
    """Return routing config, optionally merged with project-specific overrides."""
    cfg = load()
    base = dict(cfg.get("routing") or {})
    projects = base.pop("projects", {}) or {}
    override = projects.get(project) if project and isinstance(projects, dict) else None
    if project and conn is not None:
        try:
            row = conn.execute("SELECT routing FROM projects WHERE slug = ?", (project,)).fetchone()
            if row and row[0]:
                import json
                db_override = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                if isinstance(db_override, dict):
                    override = {**(override or {}), **db_override}
        except Exception:
            pass
    if project and isinstance(override, dict):
        if isinstance(override, dict):
            for key, value in override.items():
                if isinstance(value, dict) and isinstance(base.get(key), dict):
                    merged = dict(base[key]); merged.update(value); base[key] = merged
                else:
                    base[key] = value
    # Устаревшие ключи выкидываем в самом конце: они могли прийти и из config.toml,
    # и из переопределения проекта — наружу действующая таблица уходит уже без них.
    return without_legacy_routing(base)

def allowed_harnesses(project: str | None, stage: str | None, conn=None) -> list[str]:
    # Прямая задача (без этапа) и задача на done не привязаны к этапу конвейера —
    # ограничение по s1-spec к ним неприменимо, поэтому harness не фильтруется.
    if not stage or stage == "done":
        return []
    r = routing(project, conn=conn)
    harnesses = r.get("harnesses") or {}
    if isinstance(harnesses, list):
        return [str(x) for x in harnesses]
    return [str(x) for x in (harnesses.get(stage) or [])]


_TRANSITION_KINDS = {"sticky", "handoff", "sticky-return"}


def validate_routing(obj: Any) -> dict:
    """Проверить и нормализовать переопределение маршрутизации проекта.

    Принимает словарь с любым подмножеством ключей `harnesses`, `transitions`,
    `return_window_hours`. Поднимает ``ValueError`` с текстом на русском, если
    форма не соответствует ожидаемой. Пустой словарь — валиден (значит «нет
    переопределений»). Устаревшие ключи (`default_process`) молча игнорируются:
    старые переопределения проектов не должны ломать ни чтение, ни перезапись.
    """
    if not isinstance(obj, dict):
        raise ValueError("routing: ожидается объект (словарь)")
    from . import store as store_mod
    stages = set(store_mod.PIPELINE_STAGES)

    out: dict[str, Any] = {}
    for key, value in obj.items():
        if key in LEGACY_ROUTING_KEYS:
            continue
        if key == "harnesses":
            if not isinstance(value, dict):
                raise ValueError("routing: harnesses должен быть словарём этап → список")
            harnesses: dict[str, list[str]] = {}
            for stage, names in value.items():
                if stage not in stages:
                    raise ValueError(f"routing: неизвестный этап в harnesses: {stage}")
                if not isinstance(names, list):
                    raise ValueError(f"routing: harnesses.{stage} должен быть списком")
                clean: list[str] = []
                seen: set[str] = set()
                for name in names:
                    if not isinstance(name, str) or not name.strip():
                        raise ValueError(f"routing: harnesses.{stage} содержит пустое имя")
                    if name not in seen:
                        seen.add(name)
                        clean.append(name)
                harnesses[stage] = clean
            out["harnesses"] = harnesses
        elif key == "transitions":
            if not isinstance(value, dict):
                raise ValueError("routing: transitions должен быть словарём")
            transitions: dict[str, str] = {}
            for tkey, tval in value.items():
                if not isinstance(tkey, str) or ":" not in tkey:
                    raise ValueError(f"routing: неверный ключ перехода: {tkey}")
                left, right = tkey.split(":", 1)
                if left not in stages or (right != "done" and right not in stages):
                    raise ValueError(f"routing: неизвестный этап в переходе: {tkey}")
                if tval not in _TRANSITION_KINDS:
                    raise ValueError(f"routing: неизвестный вид перехода {tkey}: {tval}")
                transitions[tkey] = tval
            out["transitions"] = transitions
        elif key == "return_window_hours":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError("routing: return_window_hours должен быть числом > 0")
            out["return_window_hours"] = value
        else:
            raise ValueError(f"routing: неизвестный ключ {key}")
    return out

def transition_kind(project: str | None, from_stage: str | None, to_stage: str | None, conn=None) -> str:
    if not from_stage:
        # Задача без этапа ещё не была ничьим "предыдущим этапом" — заводить её в
        # конвейер не то же самое, что передавать другому harness, держателя не снимаем.
        return "sticky"
    if from_stage == to_stage:
        # Повторная выдача на том же этапе (`stage <id> --to s3-impl --holder <кто>`)
        # — не передача: держателя не снимаем, иначе выдача молча теряла бы его.
        return "sticky"
    r = routing(project, conn=conn)
    return str((r.get("transitions") or {}).get(f"{from_stage}:{to_stage}", "handoff"))
