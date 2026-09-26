"""Маршруты запуска задач: файл поставки `routes.json` и чтение таблицы `routes`.

Таблица маршрутов (пресеты конвейеров и маршруты роя) живёт в базе —
таблице `routes` (см. :mod:`listik.routes_store`). `routes.json` в корне репозитория
(`SOURCE_PATH`) — только затравка при установке: этот модуль читает, разбирает и
проверяет файл (`load`/`validate`), а первичный ввоз в пустую таблицу делает
`listik.routes_store` (при `listik init` и старте сервера). Больше файл не перечитывает
никто и ни с чем не сверяет: записи в базе главнее. В образце у каждой записи
есть `command`: конвейер запускает `claude -p` со скилом `/feature-pipeline:{route}`.

Читатели берут маршруты только из базы (`state(conn)`), файл в обход ввоза не читает
никто. Кеша нет: правка записи в базе видна сразу, перезапуск сервера не нужен.

Формат (версия 1)::

    {"version": 1, "routes": [ {...}, ... ]}

Запись маршрута:
  * `key` — `^[a-z0-9][a-z0-9-]*$`, уникален в файле;
  * `kind` — `"pipeline"` или `"swarm"`;
  * `title` — непустая строка;
  * `hint` — строка, по умолчанию `""`;
  * `visible` — именно JSON `true`/`false`;
  * `roles` — обязателен: ключи только из `spec`/`critic`/`impl`/`judge`; у `pipeline`
    режима скила значение — `{provider, label, title}` из непустых строк (провайдер —
    `claude`/`glm`/`openai`/`grok`/`deepseek`), у роя — `{harness, argv?, prompt?}`;
  * `icon` — необязательный уровень маршрута для иконки на доске, одно из
    `xhigh`/`high`/`medium`/`low`/`xlow`/`direct`. Если поля нет, уровень выводится из самой
    ключа (`fallback_icon`): часть ключа до первого `-`, если она из того же набора
    (`xhigh-pipeline` → `xhigh`); у записи без
    выводимого уровня (`opus-pipeline`) иконки нет; явный `null` — иконки нет. Неизвестное значение — не ошибка
    файла, а предупреждение (listik-itg8): запись получает уровень по ключу, поле
    `icon_error` с причиной и текст в `warnings` ответа `GET /api/routes`; строка уходит
    в stderr (у демона — в `listik.log`), а остальные записи и автостарт работают как обычно;
  * `command` — необязательный непустой массив непустых строк, argv запуска.

Лишние поля на любом уровне — ошибка (защита от опечаток вроде `visble`).
В `command` допустимы только подстановки `{task_id}`, `{project}`, `{route}`, `{cwd}`,
`{worktree}`, `{branch}`; любая другая фигурная скобка (включая `{{`) — ошибка.
Подставляет значения `launcher`: целиком в элемент массива, без shell и без повторной
подстановки внутри значения.

Метки карточки для маршрута выводит `labels_for(conn, ...)` — по ним сервер помечает
задачу и переписывает метки при смене маршрута.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from dataclasses import dataclass, field

from . import paths, util

SOURCE_PATH = paths.ROOT_DIR / "routes.json"

VERSION = 1
KINDS = ("pipeline", "swarm")
ROLE_KEYS = ("spec", "critic", "impl", "judge")
PROVIDERS = ("claude", "glm", "openai", "grok", "deepseek", "devin")
# Способ исполнения маршрута (listik-2gry): `skill` — конвейер-скил,
# `swarm` — рой: Listik поднимает по процессу на этап и сам ведёт этапы.
DRIVERS = ("skill", "swarm")
# Уровни маршрута — значения поля `icon`; подписи и иконки для доски лежат в
# `web/src/lib/dictionaries.ts` (`ROUTE_ICONS`).
ROUTE_ICONS = ("xhigh", "high", "medium", "low", "xlow", "direct")
PLACEHOLDERS = ("task_id", "project", "route", "cwd", "worktree", "branch",
                "stage", "role", "harness")

ROOT_FIELDS = ("version", "routes")
RECORD_FIELDS = ("key", "kind", "title", "hint", "visible", "icon", "roles", "strip", "command",
                 "driver")
ROLE_FIELDS = ("provider", "label", "title", "skill", "params")
#: Поля ячейки роли маршрута `kind=swarm`: харнесс из каталога (`harnesses`),
#: свой argv и свой промпт — последним аргументом. Пустая ячейка (роль не задана)
#: — этап пропускается.
SWARM_ROLE_FIELDS = ("harness", "argv", "prompt")
#: Обязательные поля ячейки роли; `skill`/`params` необязательны и в результате
#: проверки появляются только тогда, когда были во входе (иначе экспорт начал бы
#: писать `"skill": null` во все старые записи).
ROLE_REQUIRED = ("provider", "label", "title")
#: Скил-запускатор роли — `плагин:скил` (`pi:pi-delegate`, `grok:delegate`).
#: Существование скила на диске здесь не проверяется: файл маршрутов ввозится и на
#: машине без каталога `plugins/`; наличие сверяет слой HTTP по `skills.launcher_info`.
SKILL_RE = re.compile(r"^[a-z0-9][a-z0-9-]*:[a-z0-9][a-z0-9-]*$")
PARAM_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
PARAMS_MAX_KEYS = 20

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
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


def _validate_params(value, where: str) -> dict:
    """Параметры запускатора: плоский объект скалярных значений.

    `bool` проверяется до `int` намеренно — `isinstance(True, int)` истинно, и без
    этого порядка `true` разбиралось бы как число.
    """
    if not isinstance(value, dict):
        raise _err(where, "должен быть объектом с параметрами запускатора")
    if len(value) > PARAMS_MAX_KEYS:
        raise _err(where, f"не больше {PARAMS_MAX_KEYS} параметров")
    params: dict = {}
    for name, item in value.items():
        if not isinstance(name, str) or not PARAM_KEY_RE.match(name):
            raise _err(f"{where}.{name}",
                       "имя параметра — строчные буквы, цифры и подчёркивание, начиная с буквы")
        if isinstance(item, bool) or isinstance(item, (str, int, float)):
            params[name] = item
            continue
        raise _err(f"{where}.{name}", "допустимы только строка, число и true/false")
    return params


def _validate_role_cell(cell, where: str) -> dict:
    if not isinstance(cell, dict):
        raise _err(where, "должна быть объектом {provider, label, title}")
    _extra_fields(cell, ROLE_FIELDS, where)
    for name in ROLE_REQUIRED:
        value = _present(cell, name, where)
        if not _text(value):
            raise _err(f"{where}.{name}", "непустая строка")
    if cell["provider"] not in PROVIDERS:
        raise _err(f"{where}.provider",
                   f"неизвестный провайдер {cell['provider']!r}, допустимы: {', '.join(PROVIDERS)}")
    out = {"provider": cell["provider"], "label": cell["label"], "title": cell["title"]}
    if "skill" in cell:
        skill = cell["skill"]
        if not isinstance(skill, str) or not SKILL_RE.match(skill):
            raise _err(f"{where}.skill",
                       'ожидается "плагин:скил" из строчных букв, цифр и дефисов')
        out["skill"] = skill
    if "params" in cell:
        if "skill" not in out:
            raise _err(f"{where}.params", "без skill параметры некуда передать")
        out["params"] = _validate_params(cell["params"], f"{where}.params")
    return out


def _validate_roles(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise _err(where, "должен быть объектом с ролями spec/critic/impl/judge")
    if not value:
        raise _err(where, "нужна хотя бы одна роль")
    roles: dict = {}
    for role in value:
        if role not in ROLE_KEYS:
            raise _err(f"{where}.{role}", f"неизвестная роль, допустимы: {', '.join(ROLE_KEYS)}")
        roles[role] = _validate_role_cell(value[role], f"{where}.{role}")
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
        rest = PLACEHOLDER_RE.sub("", element)
        if "{" in rest or "}" in rest:
            raise _err(item_where, "фигурные скобки допустимы только в подстановках "
                                   + ", ".join("{" + name + "}" for name in PLACEHOLDERS))
        for name in PLACEHOLDER_RE.findall(element):
            if name not in PLACEHOLDERS:
                raise _err(item_where, f"неизвестная подстановка {{{name}}}, допустимы: "
                                       + ", ".join("{" + n + "}" for n in PLACEHOLDERS))
        command.append(element)
    return command


def fallback_icon(kind: str, key: str) -> str | None:
    """Уровень маршрута для записи без поля `icon`.

    Берётся часть ключа до первого `-` (`xhigh-pipeline` → `xhigh`), но только
    если она из `ROUTE_ICONS`: у `opus-pipeline` или `pidi` уровня нет, и иконка
    не выдумывается (`None`). `kind` на уровень не влияет.
    """
    prefix = key.split("-", 1)[0]
    return prefix if prefix in ROUTE_ICONS else None


def _validate_icon(item: dict, kind: str, key: str, where: str,
                   warnings: list[str] | None = None) -> tuple[str | None, str | None]:
    """Поле `icon`: явный уровень, иначе фолбэк по записи; `None` — иконки нет.

    Неизвестный уровень не отменяет весь файл (listik-itg8): запись получает
    фолбэк по ключу, предупреждение добавляется в `warnings`, а второй элемент
    результата — `icon_error` с причиной, по которой явный уровень не принят
    (его отдаёт `GET /api/routes` — доска помечает такую иконку как недоступную).
    """
    if "icon" not in item:
        return fallback_icon(kind, key), None
    value = item["icon"]
    if value is None:
        return None, None  # явный null — иконки нет (так пишет бэкап маршрутов)
    if value in ROUTE_ICONS:
        return value, None
    fallback = fallback_icon(kind, key)
    error = (f"неизвестный уровень {value!r}, допустимы: " + ", ".join(ROUTE_ICONS))
    if fallback is not None:
        warning = f"{where}.icon: {error}; беру уровень из ключа {key!r}: {fallback!r}"
    else:
        warning = f"{where}.icon: {error}; из ключа {key!r} уровень не выводится — иконки не будет"
    if warnings is not None:
        warnings.append(warning)
    return fallback, warning


def _validate_route(item, where: str, warnings: list[str] | None = None) -> dict:
    if not isinstance(item, dict):
        raise _err(where, "запись должна быть объектом")
    _extra_fields(item, RECORD_FIELDS, where)

    key = _present(item, "key", where)
    if not isinstance(key, str) or not KEY_RE.match(key):
        raise _err(f"{where}.key", "ключ должен подходить под ^[a-z0-9][a-z0-9-]*$")

    kind = _present(item, "kind", where)
    if kind not in KINDS:
        raise _err(f"{where}.kind", 'должен быть "pipeline" или "swarm"')

    driver = item.get("driver")
    if driver is not None and driver not in DRIVERS:
        raise _err(f"{where}.driver", f"допустимы: {', '.join(DRIVERS)}")

    title = _present(item, "title", where)
    if not _text(title):
        raise _err(f"{where}.title", "непустая строка")

    hint = item.get("hint", "")
    if not isinstance(hint, str):
        raise _err(f"{where}.hint", "должна быть строкой")

    visible = _present(item, "visible", where)
    if not isinstance(visible, bool):
        raise _err(f"{where}.visible", "должно быть true или false, не строка и не число")

    icon, icon_error = _validate_icon(item, kind, key, where, warnings)
    record = {"key": key, "kind": kind, "title": title, "hint": hint, "visible": visible,
              "icon": icon}
    # Поле появляется только у записи с непринятым `icon`: у остальных записей
    # набор полей не меняется, а доска по нему рисует «иконка недоступна».
    if icon_error is not None:
        record["icon_error"] = icon_error

    if kind == "pipeline":
        if "roles" not in item:
            raise _err(f"{where}.roles", "обязательно для pipeline")
        # Пайплайн роя (`driver: swarm`) хранит ячейки роя `{harness, argv?, prompt?}`,
        # как `kind: swarm`, — так его и проверяем, иначе бэкап таблицы не читается.
        if driver == "swarm":
            record["roles"] = validate_swarm_roles(item["roles"], f"{where}.roles")
        else:
            record["roles"] = _validate_roles(item["roles"], f"{where}.roles")
        record["driver"] = driver or "skill"
    else:
        if "roles" not in item:
            raise _err(f"{where}.roles", "обязательно для swarm")
        # Каталог харнессов файлу недоступен: проверяется только форма ячеек
        # и наличие команды у самой роли (argv) — слой базы проверит строже.
        record["roles"] = validate_swarm_roles(item["roles"], f"{where}.roles")
        record["driver"] = "swarm"

    record["command"] = (validate_command(item["command"], f"{where}.command")
                         if "command" in item else None)
    return record


def validate(obj, warnings: list[str] | None = None) -> list[dict]:
    """Проверить разобранный JSON по формату версии 1.

    При первой же ошибке бросает `RoutesError` с путём до поля (`routes[3].roles.impl.provider`)
    и причиной по-русски. Возвращает нормализованные записи: `hint` по умолчанию подставлен,
    `icon` — явный уровень или фолбэк по записи (`None` — иконки нет), `command` — список
    или `None`.

    Неизвестный `icon` ошибкой не считается: запись получает фолбэк по ключу и поле
    `icon_error`, а тексты предупреждений складываются в переданный `warnings` (listik-itg8).
    """
    if not isinstance(obj, dict):
        raise _err("routes.json", 'корень — объект {"version": 1, "routes": [...]}')
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
        record = _validate_route(item, f"routes[{i}]", warnings)
        if record["key"] in seen:
            raise _err(f"routes[{i}].key", f"дубликат ключа {record['key']!r}")
        seen.add(record["key"])
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

    Файл при этом рабочий: автостарт включён, запись получила фолбэк, — поэтому
    «нужен ты» не пишем, в отличие от `_failed`.
    """
    print(f"routes.json: предупреждение: {warning}", file=sys.stderr, flush=True)


def load(path=SOURCE_PATH) -> RoutesState:
    """Прочитать, разобрать и проверить файл. Никогда не бросает исключений.

    Любая ошибка (нет файла, нет прав, путь — каталог, битый JSON, `RoutesError`)
    даёт `ok=False`, текст ошибки и строку в stderr. Неизвестный `icon` — не ошибка:
    запись получает фолбэк, каждый такой случай идёт в `warnings` состояния и
    отдельной строкой в stderr. Состояние модуля не меняет и в базу не пишет: ввоз
    делает :mod:`listik.routes_store`.
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
    а пустой список по правилу «чужие метки не трогаем» ничего не портит.
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
    except KeyError:  # errors.NotFound — маршрута нет: метки не выдумываем
        return []
    if record["kind"] == "swarm":
        # Рой ведёт сам Listik, харнесс меняется по этапам — общего исполнителя
        # в метке нет, только ключ маршрута.
        return [f"process:{record['key']}"]
    return ["harness:claude", f"process:{record['key']}"]
