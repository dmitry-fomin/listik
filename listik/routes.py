"""Маршруты запуска задач: файл поставки `routes.json` и чтение таблицы `routes`.

Таблица маршрутов (пресеты конвейеров и маршруты роя) живёт в базе —
таблице `routes` (см. :mod:`listik.routes_store`). `routes.json` в корне репозитория
(`SOURCE_PATH`) — только затравка при установке: этот модуль читает, разбирает и
проверяет файл (`load`/`validate`), а первичный ввоз в пустую таблицу делает
`listik.routes_store` (при `listik init` и старте сервера) и `listik routes --reimport`.
Больше файл не перечитывает никто и ни с чем не сверяет: записи в базе главнее.

Читатели берут маршруты только из базы (`state(conn)`), файл в обход ввоза не читает
никто. Кеша нет: правка записи в базе видна сразу, перезапуск сервера не нужен.

Формат (версия 2)::

    {"version": 2, "routes": [ {...}, ... ]}

В файле — только раскладки ролей конвейеров плагина `listik-pipeline`; маршруты роя
живут только в базе. Запись файла — ровно поля (все обязательны):
  * `key` — `^[a-z0-9][a-z0-9-]*$`, уникален в файле, равен `lp-<skill>`;
  * `plugin` — `listik-pipeline` (`skills.PIPELINE_PLUGINS`);
  * `skill` — имя каталога скила-режима `plugins/listik-pipeline/skills/<skill>`; скилы
    ролей (`skills.ROLE_SKILLS`) — не режимы и ошибка;
  * `roles` — объект с ролями `writer`/`critic`/`implementer`/`lens`/`judge`
    (`PIPELINE_ROLE_KEYS`); `{}` допустим (так бэкап пишет конвейер без ролей). Значение
    роли — непустой массив исполнителей: у `critic` 1–10, у остальных ровно один.
    Исполнитель — `{harness, agent?, model?, channel?, effort}`: `harness` из
    `PIPELINE_HARNESSES`; `effort` обязателен; `agent` (`listik-pipeline:<имя>`) и `model`
    (`opus`/`sonnet`/`haiku`) обязательны у `claude`, `agent` — только у него; `channel`
    обязателен и допустим только у `pi`; `model` у остальных — необязательная непустая
    строка. Проверка — `validate_pipeline_roles`, её же зовёт слой базы.

Лишние поля на любом уровне — ошибка (защита от опечаток вроде `skil`). `validate`
отдаёт записи в форме слоя базы: `kind="pipeline"`, `title`/`hint` — из `SKILL.md`
режима (нет скила — `title` = имя скила `skill`, `hint` = `""`), `visible=True`, `icon` — из
необязательного поля `icon` записи (уровень из `ROUTE_ICONS`), без него — по ключу
(`fallback_icon`), `command=None`.

У записей в базе (не в файле) может быть `command` — argv запуска. В нём допустимы
только подстановки `{task_id}`, `{project}`, `{route}`, `{cwd}`, `{worktree}`, `{branch}`,
`{stage}`, `{role}`, `{harness}`, `{plugin}`, `{skill}`; любая другая фигурная скобка
(включая `{{`) — ошибка. `{plugin}` — значение поля `plugin` записи, `{skill}` — ключ без
префикса плагина (`lp-high` → `high`); у записи без `plugin` обе пустые. Подставляет
значения `launcher`: целиком в элемент массива, без shell и без повторной подстановки.

Метки карточки для маршрута выводит `labels_for(conn, ...)` — по ним сервер помечает
задачу и переписывает метки при смене маршрута.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass, field

from . import errors, paths, skills, util

SOURCE_PATH = paths.ROOT_DIR / "routes.json"

VERSION = 2
KINDS = ("pipeline", "swarm")
#: Роли маршрута роя (`kind=swarm`).
ROLE_KEYS = ("spec", "critic", "impl", "judge")
#: Роли конвейера (`kind=pipeline`) в формате v2.
PIPELINE_ROLE_KEYS = ("writer", "critic", "implementer", "lens", "judge")
#: Харнессы исполнителя роли конвейера.
PIPELINE_HARNESSES = ("claude", "pi", "devin", "codex", "grok")
#: Модели исполнителя `harness=claude`.
CLAUDE_MODELS = ("opus", "sonnet", "haiku")
EXECUTOR_FIELDS = ("harness", "agent", "model", "channel", "effort")
CRITICS_MAX = 10
# Уровни маршрута — значения поля `icon`; подписи и иконки для доски лежат в
# `web/src/lib/dictionaries.ts` (`ROUTE_ICONS`).
ROUTE_ICONS = ("xhigh", "high", "medium", "low", "xlow", "direct")
PLACEHOLDERS = ("task_id", "project", "route", "cwd", "worktree", "branch",
                "stage", "role", "harness", "plugin", "skill")

ROOT_FIELDS = ("version", "routes")
#: Поля записи файла v2; `icon` необязателен.
RECORD_FIELDS = ("key", "plugin", "skill", "roles", "icon")
#: Поля ячейки роли маршрута `kind=swarm`: харнесс из каталога (`harnesses`),
#: свой argv и свой промпт — последним аргументом. Пустая ячейка (роль не задана)
#: — этап пропускается.
SWARM_ROLE_FIELDS = ("harness", "argv", "prompt")

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
AGENT_RE = re.compile(r"^listik-pipeline:[a-z0-9][a-z0-9-]*$")
PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")

#: Префиксы меток маршрута на карточке: их выводит сервер из `launch_route`
#: (см. `labels_for`) и он же заменяет при смене маршрута. Доска их только показывает.
LABEL_PREFIXES = ("harness:", "process:")


class RoutesError(ValueError):
    """Ошибка проверки `routes.json`: в тексте — путь до поля и причина по-русски."""


@dataclass
class RoutesState:
    """Состояние маршрутов из файла при ввозе или из базы при работе.

    `ok=False` выключает автостарт; `error` объясняет причину. Предупреждения
    проверки файла относятся только к ввозу; у состояния базы `warnings=[]`.
    """

    ok: bool
    error: str | None
    path: str
    routes: list[dict] = field(default_factory=list)
    by_key: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ проверка

def _err(where: str, reason: str) -> RoutesError:
    return RoutesError(f"{where}: {reason}")


def _at(where: str, name: str) -> str:
    return f"{where}.{name}" if where else name


def _present(obj: dict, name: str, where: str):
    if name not in obj:
        raise _err(_at(where, name), "обязательное поле отсутствует")
    return obj[name]


def _text(value) -> bool:
    """Непустая строка; пробельная строка тоже пустая."""
    return isinstance(value, str) and bool(value.strip())


def _extra_fields(obj: dict, allowed: tuple, where: str) -> None:
    for name in obj:
        if name not in allowed:
            raise _err(_at(where, name), "лишнее поле")


def _validate_executor(item, where: str) -> dict:
    """Исполнитель роли конвейера; ошибка одна — первая по порядку проверок."""
    if not isinstance(item, dict):
        raise _err(where, "исполнитель — объект {harness, agent?, model?, channel?, effort}")
    _extra_fields(item, EXECUTOR_FIELDS, where)
    harness = _present(item, "harness", where)
    if not isinstance(harness, str) or harness not in PIPELINE_HARNESSES:
        raise _err(f"{where}.harness", "допустимы " + ", ".join(PIPELINE_HARNESSES))
    if "agent" in item and harness != "claude":
        raise _err(f"{where}.agent", "только у harness=claude")
    if "channel" in item and harness != "pi":
        raise _err(f"{where}.channel", "только у harness=pi")
    required = {"claude": ("agent", "model"), "pi": ("channel",)}.get(harness, ())
    for name in ("agent", "model", "channel", "effort"):
        if name in required or name == "effort":
            _present(item, name, where)
    if "agent" in item and (not isinstance(item["agent"], str)
                            or not AGENT_RE.fullmatch(item["agent"])):
        raise _err(f"{where}.agent", "ожидается listik-pipeline:<имя>")
    if "model" in item:
        if harness == "claude":
            if item["model"] not in CLAUDE_MODELS:
                raise _err(f"{where}.model", "у claude допустимы " + ", ".join(CLAUDE_MODELS))
        elif not _text(item["model"]):
            raise _err(f"{where}.model", "непустая строка")
    for name in ("channel", "effort"):
        if name in item and (not isinstance(item[name], str) or not KEY_RE.fullmatch(item[name])):
            raise _err(f"{where}.{name}", f"строка под {KEY_RE.pattern}")
    return {name: item[name] for name in EXECUTOR_FIELDS if name in item}


def validate_pipeline_roles(value, where: str, *, allow_empty: bool) -> dict:
    """Расклад ролей конвейера v2: `{роль: [исполнитель, …]}`.

    Роли — `PIPELINE_ROLE_KEYS`; у `critic` 1–`CRITICS_MAX` исполнителей, у остальных
    ровно один. `allow_empty=True` — пустой `{}` допустим (файл, бэкап); иначе —
    `нужна хотя бы одна роль` (слой базы). Каталог харнессов не сверяется: каналы `pi`
    сверяет `routes_store._check_roles`. Ошибка — `RoutesError` с путём до поля.
    """
    if not isinstance(value, dict):
        raise _err(where, "должен быть объектом с ролями " + "/".join(PIPELINE_ROLE_KEYS))
    if not value and not allow_empty:
        raise _err(where, "нужна хотя бы одна роль")
    roles: dict = {}
    for role, cell in value.items():
        role_where = f"{where}.{role}"
        if role not in PIPELINE_ROLE_KEYS:
            raise _err(role_where, "неизвестная роль, допустимы: " + ", ".join(PIPELINE_ROLE_KEYS))
        if not isinstance(cell, list) or not cell:
            raise _err(role_where, "непустой массив исполнителей")
        if role == "critic":
            if len(cell) > CRITICS_MAX:
                raise _err(role_where, f"не больше {CRITICS_MAX} исполнителей")
        elif len(cell) > 1:
            raise _err(role_where, f"у роли {role} ровно один исполнитель")
        roles[role] = [_validate_executor(item, f"{role_where}[{j}]") for j, item in enumerate(cell)]
    return roles


def check_placeholders(text: str, where: str) -> str:
    """Те же подстановки, что у `command`, но для одной строки (промпт, hint)."""
    rest = PLACEHOLDER_RE.sub("", text)
    if "{" in rest or "}" in rest:
        raise _err(where, "фигурные скобки допустимы только в подстановках "
                          + ", ".join("{" + name + "}" for name in PLACEHOLDERS))
    for name in PLACEHOLDER_RE.findall(text):
        if name not in PLACEHOLDERS:
            raise _err(where, f"неизвестная подстановка {{{name}}}, допустимы: "
                              + ", ".join("{" + n + "}" for n in PLACEHOLDERS))
    return text


def _validate_swarm_role_cell(cell, where: str, harnesses) -> dict:
    """Ячейка роли роя: `{harness, argv?, prompt?}`.

    `harness` — ключ из каталога `harnesses` (словарь `key → запись` или None —
    тогда только форма ключа). Команда роли: свой `argv`, иначе argv харнесса
    по умолчанию; ни того ни другого — роль не годится («нет роли с командой»).
    Без каталога (None) наличие команды не проверяется: argv по умолчанию неизвестен.
    `prompt` — строка с теми же подстановками, что у argv; идёт последним
    аргументом argv при запуске.
    """
    if not isinstance(cell, dict):
        raise _err(where, "должна быть объектом {harness, argv?, prompt?} или null")
    _extra_fields(cell, SWARM_ROLE_FIELDS, where)
    harness = _present(cell, "harness", where)
    if not isinstance(harness, str) or not KEY_RE.match(harness):
        raise _err(f"{where}.harness", "ключ харнесса под ^[a-z0-9][a-z0-9-]*$")
    record = harnesses.get(harness) if isinstance(harnesses, dict) else None
    if harnesses is not None and record is None:
        raise _err(f"{where}.harness", f"харнесса {harness!r} нет в каталоге")
    argv = cell.get("argv")
    if argv is not None:
        argv = validate_command(argv, f"{where}.argv")
    default_argv = record.get("argv") if record else None
    # Без каталога (`harnesses is None` — проверка файла) argv харнесса по умолчанию
    # неизвестен: ячейка без своего argv допустима, команду сверит слой базы при ввозе.
    if not argv and not default_argv and harnesses is not None:
        raise _err(f"{where}.argv",
                   "нет команды: задайте argv у роли или по умолчанию у харнесса")
    prompt = cell.get("prompt")
    if prompt is not None:
        if not isinstance(prompt, str):
            raise _err(f"{where}.prompt", "ожидается строка")
        prompt = check_placeholders(prompt, f"{where}.prompt")
        if not prompt.strip():
            prompt = None
    out = {"harness": harness}
    if argv:
        out["argv"] = argv
    if prompt is not None:
        out["prompt"] = prompt
    return out


def validate_swarm_roles(value, where: str, harnesses=None) -> dict:
    """Расклад ролей `kind=swarm`: те же ключи spec/critic/impl/judge, ячейка —
    `{harness, argv?, prompt?}` или `null`/пропуск — этап пропускается.

    Хотя бы одна роль обязана быть задана: маршрут роя без единой роли с
    командой сохранять нельзя.
    """
    if not isinstance(value, dict):
        raise _err(where, "должен быть объектом с ролями spec/critic/impl/judge")
    roles: dict = {}
    for role, cell in value.items():
        if role not in ROLE_KEYS:
            raise _err(f"{where}.{role}", f"неизвестная роль, допустимы: {', '.join(ROLE_KEYS)}")
        if cell is None:
            continue
        roles[role] = _validate_swarm_role_cell(cell, f"{where}.{role}", harnesses)
    if not roles:
        raise _err(where, "нужна хотя бы одна роль с харнессом и командой")
    return roles


def validate_command(value, where: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise _err(where, "непустой массив строк")
    command: list[str] = []
    for i, element in enumerate(value):
        item_where = f"{where}[{i}]"
        if not isinstance(element, str) or not element:
            raise _err(item_where, "непустая строка")
        command.append(check_placeholders(element, item_where))
    return command


def check_plugin(kind, key, value, where: str):
    """Поле `plugin` записи маршрута в базе: `None` (нет поля или `null`) либо плагин конвейера.

    Плагин — только у `kind=pipeline`, из `skills.PIPELINE_PLUGINS`, и ключ записи
    начинается с `<префикс плагина>-` (`lp-`) с непустым остатком. Нарушение —
    `RoutesError` с путём до поля (`where` — путь до записи, `""` — корень).
    """
    if value is None:
        return None
    if kind != "pipeline":
        raise _err(_at(where, "plugin"), "поле только у kind=pipeline")
    if not isinstance(value, str) or value not in skills.PIPELINE_PLUGINS:
        raise _err(_at(where, "plugin"), "допустимы " + ", ".join(skills.PIPELINE_PLUGINS))
    if skills.skill_of(value, key) is None:
        prefix = skills.PIPELINE_KEY_PREFIX[value]
        raise _err(_at(where, "key"), f"у плагина {value} ключ начинается с '{prefix}-'")
    return value


def fallback_icon(key: str, kind: str = "pipeline") -> str | None:
    """Уровень маршрута для записи без поля `icon`.

    Первое правило, для обоих видов, — часть ключа до первого `-`, если она из
    `ROUTE_ICONS` (`xhigh-team` → `xhigh`). Второе, только для конвейера
    (`kind == "pipeline"`), — часть после первого `-` (`full-high` → `high`,
    `cc-xlow` → `xlow`). У `pidi` уровня нет, и иконка не
    выдумывается (`None`). У маршрута роя `icon` — вес партии: ключ вроде
    `team-high` без явного `icon` веса не получает (`fallback_icon("x-high", "swarm")`
    → `None`).
    """
    prefix, _, rest = key.partition("-")
    if prefix in ROUTE_ICONS:
        return prefix
    if kind == "pipeline" and rest in ROUTE_ICONS:
        return rest
    return None


def _validate_route(item, where: str) -> dict:
    """Запись файла v2 → запись в форме слоя базы; ошибка одна — первая по порядку."""
    if not isinstance(item, dict):
        raise _err(where, "запись должна быть объектом")
    _extra_fields(item, RECORD_FIELDS, where)

    key = _present(item, "key", where)
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise _err(f"{where}.key", "ключ должен подходить под ^[a-z0-9][a-z0-9-]*$")

    plugin = _present(item, "plugin", where)
    if not isinstance(plugin, str) or plugin not in skills.PIPELINE_PLUGINS:
        raise _err(f"{where}.plugin", "допустимы " + ", ".join(skills.PIPELINE_PLUGINS))

    skill = _present(item, "skill", where)
    if not isinstance(skill, str) or not KEY_RE.fullmatch(skill):
        raise _err(f"{where}.skill", f"имя каталога скила под {KEY_RE.pattern}")
    if skill in skills.ROLE_SKILLS:
        raise _err(f"{where}.skill", f"{skill!r} — скил роли, а не режим")

    prefix = skills.PIPELINE_KEY_PREFIX[plugin]
    expected = f"{prefix}-{skill}"
    if key != expected:
        raise _err(f"{where}.key",
                   f"у плагина {plugin} ключ — {prefix}-<skill> (ожидается {expected!r})")

    icon = item.get("icon")
    if icon is not None and icon not in ROUTE_ICONS:
        raise _err(f"{where}.icon", "допустимы " + ", ".join(ROUTE_ICONS))

    info = skills.skill_info(key, plugin)
    return {"key": key, "kind": "pipeline", "plugin": plugin,
            "title": info["title"] if info else skill,
            "hint": info["hint"] if info else "",
            "visible": True, "icon": icon or fallback_icon(key), "command": None}


def validate(obj, warnings: list[str] | None = None) -> list[dict]:
    """Проверить разобранный JSON по формату версии 2.

    При первой же ошибке бросает `RoutesError` с путём до поля
    (`routes[3].roles.critic[1].channel`) и причиной по-русски. Возвращает записи в форме
    слоя базы (см. модуль-docstring). `warnings` оставлен ради сигнатуры: у v2
    предупреждений нет, список не пополняется.
    """
    if not isinstance(obj, dict):
        raise _err("routes.json", f'корень — объект {{"version": {VERSION}, "routes": [...]}}')
    _extra_fields(obj, ROOT_FIELDS, "")
    version = _present(obj, "version", "")
    if isinstance(version, bool) or not isinstance(version, int) or version != VERSION:
        raise _err("version", f"должна быть ровно целая {VERSION}")
    routes = _present(obj, "routes", "")
    if not isinstance(routes, list):
        raise _err("routes", "должен быть массивом записей")

    out: list[dict] = []
    seen: set[str] = set()
    for i, item in enumerate(routes):
        where = f"routes[{i}]"
        record = _validate_route(item, where)
        if record["key"] in seen:
            raise _err(f"{where}.key", f"дубликат ключа {record['key']!r}")
        seen.add(record["key"])
        roles = _present(item, "roles", where)
        record["roles"] = validate_pipeline_roles(roles, f"{where}.roles", allow_empty=True)
        out.append(record)
    return out


# ------------------------------------------------------------------ загрузка

def _describe(exc: BaseException) -> str:
    if isinstance(exc, RoutesError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


def _failed(error: str, path) -> RoutesState:
    """Состояние с ошибкой + строка в stderr (у демона stderr идёт в listik.log)."""
    print(f"routes.json: {error} — автостарт выключен, нужен ты", file=sys.stderr, flush=True)
    return RoutesState(ok=False, error=error, path=str(path), routes=[], by_key={})


def _warned(warning: str) -> None:
    """Предупреждение проверки в stderr (у демона — в listik.log).

    Файл при этом рабочий: автостарт включён, — поэтому «нужен ты» не пишем, в
    отличие от `_failed`. У формата v2 предупреждений нет.
    """
    print(f"routes.json: предупреждение: {warning}", file=sys.stderr, flush=True)


def load(path=SOURCE_PATH) -> RoutesState:
    """Прочитать, разобрать и проверить файл. Никогда не бросает исключений.

    Любая ошибка (нет файла, нет прав, путь — каталог, битый JSON, `RoutesError`)
    даёт `ok=False`, текст ошибки и строку в stderr. `warnings` у формата v2 всегда
    пуст (поле оставлено ради сигнатуры). Состояние модуля не меняет и в базу не
    пишет: ввоз делает :mod:`listik.routes_store`.
    """
    file = util.path(path)
    warnings: list[str] = []
    try:
        obj = util.json_loads(util.read_text(file))
        records = validate(obj, warnings)
    except Exception as exc:  # noqa: BLE001 — наружу не бросаем ничего
        return _failed(_describe(exc), file)
    for warning in warnings:
        _warned(warning)
    return RoutesState(ok=True, error=None, path=str(file), routes=records,
                       by_key={record["key"]: record for record in records},
                       warnings=warnings)


def state(conn) -> RoutesState:
    """Маршруты из базы в форме `RoutesState`.

    Записи — `routes_store.list_routes(conn)` в порядке `position, key`; `ok=True`,
    `path` — путь к базе (`paths.DB_PATH`), `warnings=[]`. Недоступная база
    (`sqlite3.DatabaseError`) — `ok=False` и текст ошибки: сервер не должен падать
    из-за неё, а `GET /api/routes` и `/api/health` отдают понятный отказ.
    """
    from . import routes_store  # цикл: routes_store импортирует routes
    try:
        records = routes_store.list_routes(conn)
    except sqlite3.DatabaseError as exc:
        return RoutesState(ok=False, error=_describe(exc), path=str(paths.DB_PATH),
                           routes=[], by_key={})
    return RoutesState(ok=True, error=None, path=str(paths.DB_PATH), routes=records,
                       by_key={record["key"]: record for record in records},
                       warnings=[])


def is_route_label(label: str) -> bool:
    """Метка маршрута (`harness:<x>`/`process:<y>`) — её ставит и снимает сервер."""
    return label.startswith(LABEL_PREFIXES)


def labels_for(conn, route_key: str | None) -> list[str]:
    """Метки карточки для маршрута — те же, что ставит форма «Новая задача» на доске.

    У `pipeline` — оркестратор `claude` и ключ записи (`harness:claude`,
    `process:<key>`): конвейер ведёт claude, а провайдеры ролей у записей разные.
    Пустой или неизвестный ключ — пустой список: метки не выдумываем. Недоступная
    база (`sqlite3.DatabaseError`) тоже даёт пустой список и строку в stderr: метки —
    вспомогательные данные, из-за базы ни `claim`, ни создание задачи падать не должны,
    а пустой список по правилу «чужие метки не трогаем» ничего не портит. Голый
    `KeyError` (баг в коде, не «маршрута нет») не глотается.
    """
    from . import routes_store  # цикл: routes_store импортирует routes
    key = (route_key or "").strip()
    if not key:
        return []
    try:
        record = routes_store.get_route(conn, key)
    except sqlite3.DatabaseError as exc:
        print(f"routes: метки недоступны: {_describe(exc)}", file=sys.stderr, flush=True)
        return []
    except errors.NotFound:  # маршрута нет: метки не выдумываем
        return []
    if record["kind"] == "swarm":
        # Рой ведёт сам Listik, харнесс меняется по
        # этапам — общего исполнителя в метке нет, только ключ маршрута.
        return [f"process:{record['key']}"]
    return ["harness:claude", f"process:{record['key']}"]
