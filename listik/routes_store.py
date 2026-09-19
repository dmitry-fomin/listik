"""Доступ к таблице `routes` — хранилищу маршрутов запуска задач.

`routes.json` (см. :mod:`listik.routes`) — только затравка при установке: файл
поставки, который один раз наполняет пустую таблицу.  Этот модуль — слой базы:
первичный ввоз файла (`import_file`/`ensure_imported`) и обычные операции над
записями.  Зависимость односторонняя: `routes_store` импортирует `routes`, а не
наоборот, поэтому циклов нет.

Запись в базе и запись наружу — та же форма, что отдаёт `routes.validate`:
`{"key", "kind", "title", "hint", "visible": bool, "icon": str|None,
"position": int, "command": list[str]|None}`, плюс `"roles": dict` у
`pipeline` (пустой словарь, если ролей нет) и `"harness": str` у `direct`.
JSON-колонки (`command`, `roles`) разбираются здесь, битый JSON — пустое
значение, а не исключение.

Правила полей живут в одном месте (`_check_*`): их одинаково применяют
`update_route`, `upsert_route` и `create_route`.  Любое нарушение —
`ValueError` с именем поля и причиной по-русски.  Расклад ролей (`roles`)
проверяется той же функцией, что у файла (`routes._validate_roles`), поэтому
правка по HTTP и ввоз файла принимают ровно одно и то же.
"""
from __future__ import annotations

import json
import sqlite3
import sys

from . import errors, routes, skills, store

#: Поля, которые вообще разрешено менять точечно.  Всё остальное (`kind`, `key`,
#: `harness`, `position`) через `update_route` недоступно.  `roles` правится
#: (listik-syu8): расклад ролей задаётся из UI доски, а не только ввозом файла.
UPDATE_FIELDS = ("title", "hint", "icon", "visible", "command", "roles")

COLS = ("key", "kind", "title", "hint", "icon", "visible", "position", "harness",
        "command", "roles", "created_at", "updated_at")


# ------------------------------------------------------------------ значения

def _dumps(value):
    """Сериализовать значение колонки-JSON; `None` остаётся `None`."""
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _loads_list(text):
    if not text:
        return None
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) else None


def _loads_dict(text) -> dict:
    if not text:
        return {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


# ------------------------------------------------------------------ проверка

def _check_title(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("title: нужна непустая строка")
    return value


def _check_hint(value) -> str:
    if not isinstance(value, str):
        raise ValueError("hint: должна быть строкой, не None")
    return value


def _check_icon(value):
    if value is not None and value not in routes.ROUTE_ICONS:
        raise ValueError(f"icon: неизвестный уровень {value!r}, допустимы: "
                         + ", ".join(routes.ROUTE_ICONS))
    return value


def _check_visible(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("visible: должно быть true или false, не 0/1 и не строка")
    return value


def _check_command(value, kind: str, *, direct_only: bool = False):
    """Проверить `command` и, если это список, нормализовать его.

    Сначала проверяется форма (через `routes.validate_command`), потом — при
    `direct_only` — право поля быть у этого `kind`: точечная правка `command`
    разрешена только у `direct`.  Ввоз и `create_route` принимают `command` у
    любого `kind`: в образце `routes.json` argv есть у всех записей, и конвейер
    без него не запустить.
    """
    if value is None:
        return None
    checked = routes.validate_command(value, "command")
    if direct_only and kind != "direct":
        raise ValueError('command: допустимо только у kind="direct"')
    return checked


def _check_roles(value, kind: str) -> dict:
    """Проверить расклад ролей той же проверкой, что у файла маршрутов.

    Роли есть только у `pipeline`: у `direct` поле запрещено самой формой записи.
    `routes._validate_roles` бросает `RoutesError` — подкласс `ValueError`, поэтому
    нарушение уходит наружу тем же путём, что и остальные поля (400 `bad_argument`).
    """
    if kind != "pipeline":
        raise ValueError('roles: допустимо только у kind="pipeline"')
    return routes._validate_roles(value, "roles")


def _prepare(record: dict) -> dict:
    """Проверить запись и вернуть значения колонок (без `position`/`created_at`)."""
    if not isinstance(record, dict):
        raise ValueError("record: должна быть запись-словарь")
    key = record.get("key")
    if not isinstance(key, str) or not routes.KEY_RE.match(key):
        raise ValueError("key: ключ должен подходить под ^[a-z0-9][a-z0-9-]*$")
    kind = record.get("kind")
    if kind not in routes.KINDS:
        raise ValueError('kind: должен быть "pipeline" или "direct"')
    title = _check_title(record.get("title"))
    hint = _check_hint(record.get("hint", ""))
    icon = _check_icon(record.get("icon"))
    visible = _check_visible(record.get("visible", False))
    command = _check_command(record.get("command"), kind)
    roles = record.get("roles")
    harness = record.get("harness")
    if kind == "pipeline":
        if harness is not None:
            raise ValueError("harness: у pipeline-записи harness быть не должно")
        if roles is None:
            roles = {}
        if not isinstance(roles, dict):
            raise ValueError("roles: должен быть объектом с ролями spec/critic/impl/judge")
        if roles:
            # Непустой расклад проверяется целиком (ячейки, `skill`, `params`), а не
            # только «это словарь»: иначе запись мимо файла могла бы положить в базу
            # расклад, который ввоз того же файла отверг бы.
            roles = _check_roles(roles, kind)
        harness = None
    else:
        if roles not in (None, {}):
            raise ValueError("roles: у direct-записи ролей быть не должно")
        roles = None
        if harness not in routes.HARNESSES:
            raise ValueError(f"harness: неизвестный харнесс {harness!r}, допустимы: "
                             + ", ".join(routes.HARNESSES))
    return {"key": key, "kind": kind, "title": title, "hint": hint, "icon": icon,
            "visible": 1 if visible else 0, "harness": harness,
            "command": _dumps(command), "roles": _dumps(roles)}


# --------------------------------------------------------------- строки/записи

def _row_to_record(row: sqlite3.Row) -> dict:
    record = {
        "key": row["key"],
        "kind": row["kind"],
        "title": row["title"],
        "hint": row["hint"],
        "visible": bool(row["visible"]),
        "icon": row["icon"],
        "position": int(row["position"]),
        "command": _loads_list(row["command"]),
    }
    if row["kind"] == "pipeline":
        record["roles"] = _loads_dict(row["roles"])
    else:
        record["harness"] = row["harness"]
    return record


def _next_position(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM routes").fetchone()
    return int(row["p"])


def list_routes(conn: sqlite3.Connection) -> list[dict]:
    """Все маршруты в порядке `position, key`."""
    rows = conn.execute("SELECT * FROM routes ORDER BY position, key").fetchall()
    return [_row_to_record(row) for row in rows]


def get_route(conn: sqlite3.Connection, key: str) -> dict:
    """Одна запись по ключу; нет такой — `errors.NotFound`."""
    row = conn.execute("SELECT * FROM routes WHERE key = ?", (key,)).fetchone()
    if row is None:
        raise errors.NotFound(f"маршрут {key!r} не найден")
    return _row_to_record(row)


def count(conn: sqlite3.Connection) -> int:
    """Число записей в таблице."""
    return int(conn.execute("SELECT COUNT(*) FROM routes").fetchone()[0])


# ------------------------------------------------------------------ запись

def _insert(conn: sqlite3.Connection, values: dict, position: int) -> None:
    now = store.now_iso()
    conn.execute(
        "INSERT INTO routes(key, kind, title, hint, icon, visible, position, harness, "
        "command, roles, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (values["key"], values["kind"], values["title"], values["hint"], values["icon"],
         values["visible"], int(position), values["harness"], values["command"],
         values["roles"], now, now),
    )


def _upsert_raw(conn: sqlite3.Connection, record: dict, *, position=None) -> None:
    """Вставка/перезапись без `commit` — для `import_file` (одна транзакция)."""
    values = _prepare(record)
    key = values["key"]
    existing = conn.execute("SELECT position FROM routes WHERE key = ?", (key,)).fetchone()
    if existing is None:
        _insert(conn, values, _next_position(conn) if position is None else position)
        return
    if position is None:
        position = existing["position"]
    conn.execute(
        "UPDATE routes SET kind=?, title=?, hint=?, icon=?, visible=?, position=?, "
        "harness=?, command=?, roles=?, updated_at=? WHERE key=?",
        (values["kind"], values["title"], values["hint"], values["icon"], values["visible"],
         int(position), values["harness"], values["command"], values["roles"],
         store.now_iso(), key),
    )


def upsert_route(conn: sqlite3.Connection, record: dict, *, position=None) -> dict:
    """Вставить или полностью перезаписать запись по ключу.

    `record` — запись в форме `routes.validate` (см. модуль-docstring).  Поля
    проверяются теми же правилами, что и в `update_route`, плюс `kind` из
    `routes.KINDS` и `harness` из `routes.HARNESSES` у `direct`.  При вставке
    проставляется `created_at`, при любой записи — `updated_at`.
    """
    _upsert_raw(conn, record, position=position)
    conn.commit()
    return get_route(conn, record["key"])


def create_route(conn: sqlite3.Connection, *, key, kind, title, hint="", icon=None,
                 visible=False, harness=None, command=None, roles=None) -> dict:
    """Новая запись, `position = max(position)+1`; дубликат ключа — `ValueError`."""
    record = {"key": key, "kind": kind, "title": title, "hint": hint, "icon": icon,
              "visible": visible, "harness": harness, "command": command, "roles": roles}
    _prepare(record)  # проверяем до обращения к базе: ключ и остальные поля
    if conn.execute("SELECT 1 FROM routes WHERE key = ?", (key,)).fetchone():
        raise ValueError(f"key: маршрут {key!r} уже есть")
    position = _next_position(conn)
    _upsert_raw(conn, record, position=position)
    conn.commit()
    return get_route(conn, key)


def update_route(conn: sqlite3.Connection, key: str, **fields) -> dict:
    """Частичная правка полей `title`, `hint`, `icon`, `visible`, `command`, `roles`.

    Любое другое имя поля (`kind`, `key`, `harness`, `position`) — `ValueError`
    с именем поля.  `command` допустим только у `kind="direct"`, `roles` — только
    у `kind="pipeline"`.  `updated_at` обновляется.
    """
    record = get_route(conn, key)
    for name in fields:
        if name not in UPDATE_FIELDS:
            raise ValueError(f"{name}: это поле нельзя менять через update_route")
    sets: list[str] = []
    params: list = []
    if "title" in fields:
        sets.append("title = ?")
        params.append(_check_title(fields["title"]))
    if "hint" in fields:
        sets.append("hint = ?")
        params.append(_check_hint(fields["hint"]))
    if "icon" in fields:
        sets.append("icon = ?")
        params.append(_check_icon(fields["icon"]))
    if "visible" in fields:
        sets.append("visible = ?")
        params.append(1 if _check_visible(fields["visible"]) else 0)
    if "command" in fields:
        sets.append("command = ?")
        params.append(_dumps(_check_command(fields["command"], record["kind"],
                                            direct_only=True)))
    if "roles" in fields:
        sets.append("roles = ?")
        params.append(_dumps(_check_roles(fields["roles"], record["kind"])))
    if not sets:
        return record
    sets.append("updated_at = ?")
    params.append(store.now_iso())
    params.append(key)
    conn.execute(f"UPDATE routes SET {', '.join(sets)} WHERE key = ?", params)
    conn.commit()
    return get_route(conn, key)


def delete_route(conn: sqlite3.Connection, key: str) -> int:
    """Удалить запись; вернуть число задач с `launch_route = key`.

    Сами задачи не трогаются: очистку `launch_route` делает порция `c` на
    уровне API.
    """
    tasks = int(conn.execute("SELECT COUNT(*) FROM tasks WHERE launch_route = ?",
                             (key,)).fetchone()[0])
    conn.execute("DELETE FROM routes WHERE key = ?", (key,))
    conn.commit()
    return tasks


def reorder(conn: sqlite3.Connection, keys) -> list[dict]:
    """Расставить `position` по порядку `keys` (0,1,2,…).

    Ключ, которого нет в базе, — `ValueError`.  Ключи базы, которых нет в
    списке, уезжают в конец, сохраняя прежний относительный порядок.
    """
    existing = [row["key"] for row in
                conn.execute("SELECT key FROM routes ORDER BY position, key").fetchall()]
    known = set(existing)
    for key in keys:
        if key not in known:
            raise ValueError(f"key: маршрута {key!r} нет в базе")
    ordered: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    ordered.extend(key for key in existing if key not in seen)
    for position, key in enumerate(ordered):
        conn.execute("UPDATE routes SET position = ? WHERE key = ?", (position, key))
    conn.commit()
    return list_routes(conn)


# ------------------------------------------------------------------ ввоз файла

def _source_path(path=None):
    if path is not None:
        return path
    return routes.SOURCE_PATH


def import_file(conn: sqlite3.Connection, path=None, *, replace=False) -> dict:
    """Первичный ввоз файла поставки `routes.json` в таблицу.

    Источник по умолчанию — файл поставки `routes.SOURCE_PATH`.  Файл читает и
    проверяет `routes.load`; ошибка файла — `routes.RoutesError`, база при этом
    не меняется (транзакция целиком).  `replace=False`: непустая таблица не
    трогается.  `replace=True`: старые записи удаляются, файл пишется заново.
    Порядок файла становится `position` 0,1,2,…
    """
    source = _source_path(path)
    state = routes.load(source)
    if not state.ok:
        raise routes.RoutesError(state.error or "routes.json не читается")
    if not replace and count(conn) > 0:
        return {"imported": 0, "skipped": True, "source": str(source), "replaced": False}
    try:
        if replace:
            conn.execute("DELETE FROM routes")
        for position, record in enumerate(state.routes):
            _upsert_raw(conn, record, position=position)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"imported": len(state.routes), "skipped": False, "source": str(source),
            "replaced": bool(replace)}


def ensure_imported(conn: sqlite3.Connection) -> dict:
    """`import_file` без `replace`, но никогда не роняет вызывающего.

    Ошибка печатается в stderr строкой `routes: ввоз не удался: <текст>` (у
    демона stderr уходит в `listik.log`) и возвращается как `{"error": ...}`.
    """
    try:
        return import_file(conn)
    except Exception as exc:  # noqa: BLE001 — ввоз не должен ронять init/старт
        message = errors.message_of(exc)
        print(f"routes: ввоз не удался: {message}", file=sys.stderr, flush=True)
        return {"error": message}


# ------------------------------------------------------------------ скилы (справочник)

def _skill_missing_warning(key: str) -> str:
    return f"маршрута {key!r}: скила /feature-pipeline:{key} нет, маршрут скрыт"


def routes_response(conn: sqlite3.Connection) -> dict:
    """Тело `GET /api/routes` — общее для HTTP-обработчика и локального фолбэка CLI.

    Записи — `routes.state(conn).routes` (то есть `list_routes`, порядок
    `position, key`) плюс у `kind=pipeline`: `skill_path` (путь к `SKILL.md` или
    `None`) и, если скилы установлены, а каталога этого скила нет, —
    `skill_missing: true` и `visible` переписанный в ответе на `False` (в базе
    значение не меняется — маршрут скрыт автоматически, а не переписан).
    Установленный Listik может быть без каталога `plugins/` вовсе
    (`skills.skills_available() is False`): тогда ни один маршрут не помечается
    «без скила», чтобы не спрятать разом все конвейеры.
    """
    state = routes.state(conn)
    warnings = list(state.warnings)
    available = skills.skills_available()
    keys = set(skills.skill_keys()) if available else set()
    out_routes: list[dict] = []
    for record in state.routes:
        record = dict(record)
        if record["kind"] == "pipeline":
            if available and record["key"] not in keys:
                record["skill_missing"] = True
                record["visible"] = False
                record["skill_path"] = None
                warnings.append(_skill_missing_warning(record["key"]))
            else:
                info = skills.skill_info(record["key"]) if available else None
                record["skill_path"] = info["skill_path"] if info else None
        out_routes.append(record)
    return {"ok": state.ok, "error": state.error, "path": state.path,
            "warnings": warnings, "routes": out_routes}


def sync_report(conn: sqlite3.Connection) -> dict:
    """Тело `GET /api/routes/sync` — сверка таблицы маршрутов со скилами.

    `skills_available=False` (нет каталога `plugins/` вовсе) — оба списка
    пустые: без установленных скилов сверять не с чем, и она не должна
    выглядеть так, будто пропали все конвейеры.
    """
    if not skills.skills_available():
        return {"skills_available": False, "missing_skill": [], "missing_route": []}
    keys = set(skills.skill_keys())
    pipelines = [r for r in list_routes(conn) if r["kind"] == "pipeline"]
    route_keys = {r["key"] for r in pipelines}
    missing_skill = [r for r in pipelines if r["key"] not in keys]
    missing_route = [skills.skill_info(key) for key in sorted(keys) if key not in route_keys]
    return {"skills_available": True, "missing_skill": missing_skill,
            "missing_route": missing_route}

