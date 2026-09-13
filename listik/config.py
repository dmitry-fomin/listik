"""Конфигурация хаба Listik.

Формат — TOML (читается встроенным tomllib), файл config.toml в корне Listik.
Токен создаётся при первом запуске сервера, права 0600.
"""
from __future__ import annotations

import secrets
import tomllib
import json
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
    "import": {
        # Откуда разово забираем задачи из старых .beads
        "projects_root": str(paths.PROJECTS_ROOT),
        "max_depth": 6,
    },
    "routing": {
        "default_process": ["s1-spec", "s2-review", "s3-impl", "s4-judge"],
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


def load(path: Path | None = None) -> dict:
    cfg_path = Path(path or paths.CONFIG_PATH)
    data: dict = {}
    if cfg_path.exists():
        with cfg_path.open("rb") as fh:
            data = tomllib.load(fh)
    return _merge(DEFAULTS, data)


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
            lines.append("[" + ".".join(path) + "]")
        for key, val in scalars:
            lines.append(f"{key} = {value(val)}")
        if scalars:
            lines.append("")
        for key, val in obj.items():
            if isinstance(val, dict):
                table(path + [str(key)], val)

    for section, values in cfg.items():
        if isinstance(values, dict):
            table([str(section)], values)
        else:
            lines.append(f"{section} = {value(values)}")
    return "\n".join(lines).rstrip() + "\n"


def save(cfg: dict, path: Path | None = None) -> Path:
    cfg_path = Path(path or paths.CONFIG_PATH)
    cfg_path.write_text(_dump(cfg), encoding="utf-8")
    cfg_path.chmod(0o600)
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
    return base

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

    Принимает словарь с любым подмножеством ключей `harnesses`, `default_process`,
    `transitions`, `return_window_hours`. Поднимает ``ValueError`` с текстом на
    русском, если форма не соответствует ожидаемой. Пустой словарь — валиден
    (значит «нет переопределений»).
    """
    if not isinstance(obj, dict):
        raise ValueError("routing: ожидается объект (словарь)")
    from . import store as store_mod
    stages = set(store_mod.PIPELINE_STAGES)

    out: dict[str, Any] = {}
    for key, value in obj.items():
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
        elif key == "default_process":
            if not isinstance(value, list):
                raise ValueError("routing: default_process должен быть списком этапов")
            clean_stages: list[str] = []
            seen_stages: set[str] = set()
            for stage in value:
                if stage not in stages:
                    raise ValueError(f"routing: неизвестный этап в default_process: {stage}")
                if stage in seen_stages:
                    raise ValueError(f"routing: default_process содержит дубль: {stage}")
                seen_stages.add(stage)
                clean_stages.append(stage)
            out["default_process"] = clean_stages
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
    r = routing(project, conn=conn)
    return str((r.get("transitions") or {}).get(f"{from_stage}:{to_stage}", "handoff"))
