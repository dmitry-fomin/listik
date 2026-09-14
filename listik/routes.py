"""Маршруты запуска задач: `routes.json` в репозитории и его рабочая копия.

Таблица маршрутов (пресеты конвейеров и ряд «просто исполнитель») раньше была зашита
в доску (`web/src/lib/pipelines.ts`). Теперь её читает сервер: источник — `routes.json`
в корне репозитория (`SOURCE_PATH`), при старте сервера он один раз копируется в
`RUNTIME_PATH` (`$LISTIK_ROUTES`, иначе `~/.config/listik/routes.json`), если копии ещё
нет. Автор правит рабочую копию и вписывает команды; файл в репозитории остаётся
образцом без `command`.

Формат (версия 1)::

    {"version": 1, "routes": [ {...}, ... ]}

Запись маршрута:
  * `key` — `^[a-z0-9][a-z0-9-]*$`, уникален в файле;
  * `kind` — `"pipeline"` или `"direct"`;
  * `title` — непустая строка;
  * `hint` — строка, по умолчанию `""`;
  * `visible` — именно JSON `true`/`false`;
  * `roles` — обязателен для `pipeline` и запрещён для `direct`: ключи только из
    `spec`/`critic`/`impl`/`judge`, значение — `{provider, label, title}` из непустых
    строк (провайдер — `claude`/`glm`/`openai`/`grok`/`deepseek`);
  * `strip` — необязательная одна иконка вместо таблицы ролей: `{label}` плюс ровно
    одно из `provider`/`glyph`;
  * `icon` — необязательный уровень маршрута для иконки на доске, одно из
    `xhigh`/`high`/`medium`/`low`/`direct`. Если поля нет, уровень выводится из самой
    записи (`fallback_icon`): у `direct` это `direct`, у `pipeline` — часть ключа до
    первого `-`, если она из того же набора (`xhigh-pipeline` → `xhigh`); у записи без
    выводимого уровня (`feature-pipeline`) иконки нет. Рабочая копия `routes.json`,
    созданная до появления поля, поэтому продолжает работать без правок. Неизвестное
    значение — не ошибка файла, а предупреждение (listik-itg8): запись получает
    уровень по ключу, поле `icon_error` с причиной и текст в `warnings` ответа
    `GET /api/routes`; строка уходит в stderr (у демона — в `listik.log`), а
    остальные записи и автостарт работают как обычно;
  * `harness` — обязателен для `direct` и запрещён для `pipeline` (`claude`, `dsh`,
    `codex`, `grok`, `gemini`);
  * `command` — необязательный непустой массив непустых строк, argv запуска.

Лишние поля на любом уровне — ошибка (защита от опечаток вроде `visble`). В `command`
допустимы только подстановки `{task_id}`, `{project}`, `{route}`, `{cwd}`, `{title}`;
любая другая фигурная скобка (включая `{{`) — ошибка. Подставляет значения порция b:
целиком в элемент массива, без shell и без повторной подстановки внутри значения.

Глиф `strip.glyph` — имя ключа верхнего уровня из `web/src/lib/icons.ts`. Если файла
иконок нет или в нём не нашлось ни одного имени, проверка `glyph` сводится к формату
`^[a-z][a-z0-9-]*$` — сервер не должен отказываться работать из-за отсутствующей доски.
Имя в верном формате, которого нет в icons.ts, — предупреждение, а не ошибка файла
(listik-uiza): `strip.glyph` становится `None`, у `strip` появляется `glyph_error`, текст
уходит в `warnings`.

Файл читается в `init_at_startup` (сервер) и в `load_local` (CLI без сервера и MCP по
stdio); `current()` и обработчики API файл не читают, поэтому правка `routes.json` во время
работы сервера ничего не меняет до перезапуска. Метки карточки для маршрута выводит
`labels_for` — по ним сервер помечает задачу и переписывает метки при смене маршрута.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

SOURCE_PATH = paths.ROOT_DIR / "routes.json"
RUNTIME_PATH = (Path(os.environ["LISTIK_ROUTES"]) if os.environ.get("LISTIK_ROUTES")
                else Path.home() / ".config" / "listik" / "routes.json")

VERSION = 1
KINDS = ("pipeline", "direct")
ROLE_KEYS = ("spec", "critic", "impl", "judge")
PROVIDERS = ("claude", "glm", "openai", "grok", "deepseek")
HARNESSES = ("claude", "dsh", "codex", "grok", "gemini")
# Уровни маршрута — значения поля `icon`; подписи и иконки для доски лежат в
# `web/src/lib/dictionaries.ts` (`ROUTE_ICONS`).
ROUTE_ICONS = ("xhigh", "high", "medium", "low", "direct")
PLACEHOLDERS = ("task_id", "project", "route", "cwd", "title")

ROOT_FIELDS = ("version", "routes")
RECORD_FIELDS = ("key", "kind", "title", "hint", "visible", "icon", "roles", "strip", "harness",
                 "command")
ROLE_FIELDS = ("provider", "label", "title")
STRIP_FIELDS = ("label", "provider", "glyph")

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
GLYPH_RE = re.compile(r"^[a-z][a-z0-9-]*$")
# Имя иконки верхнего уровня в icons.ts: ровно два пробела отступа и `: {`.
# Имя может быть в кавычках — так записываются имена с дефисом (`'route-xhigh': {`).
ICON_LINE_RE = re.compile(r"""^  ['"]?([a-zA-Z][a-zA-Z0-9-]*)['"]?: \{""")
PLACEHOLDER_RE = re.compile(r"\{([^{}]*)\}")
ICONS_PATH = paths.WEB_DIR / "src" / "lib" / "icons.ts"

#: Префиксы меток маршрута на карточке: их выводит сервер из `launch_route`
#: (см. `labels_for`) и он же заменяет при смене маршрута. Доска их только показывает.
LABEL_PREFIXES = ("harness:", "process:")


class RoutesError(ValueError):
    """Ошибка проверки `routes.json`: в тексте — путь до поля и причина по-русски."""


@dataclass
class RoutesState:
    """Результат загрузки файла: `ok=False` — автостарт выключен, `error` объясняет почему.

    `warnings` — замечания, которые файл не отменяют (сейчас это неизвестный `icon`
    записи): автостарт работает, запись получает уровень по ключу и поле `icon_error`,
    а тексты предупреждений уходят в `GET /api/routes` и в stderr.
    """

    ok: bool
    error: str | None
    path: str
    routes: list[dict] = field(default_factory=list)
    by_key: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


_state: RoutesState | None = None


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


def icon_names(path=ICONS_PATH) -> set[str]:
    """Имена иконок верхнего уровня из `web/src/lib/icons.ts`.

    Строки берутся между `export const icons` и первой строкой, равной `}`; из них
    подходят только строки вида `^  ([a-zA-Z][a-zA-Z0-9-]*): \\{` — ровно два пробела
    отступа, то есть верхний уровень объекта (имя может быть и в кавычках: имена с
    дефисом в JS-объекте иначе не записать). Если файла нет или имён не нашлось,
    возвращается пустое множество: тогда проверка `strip.glyph` смотрит только на
    формат `^[a-z][a-z0-9-]*$` и не зависит от собранной доски.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return set()
    names: set[str] = set()
    started = False
    for line in text.splitlines():
        if not started:
            if line.startswith("export const icons"):
                started = True
            continue
        if line == "}":
            break
        found = ICON_LINE_RE.match(line)
        if found:
            names.add(found.group(1))
    return names


def _validate_role_cell(cell, where: str) -> dict:
    if not isinstance(cell, dict):
        raise _err(where, "должна быть объектом {provider, label, title}")
    _extra_fields(cell, ROLE_FIELDS, where)
    for name in ROLE_FIELDS:
        value = _present(cell, name, where)
        if not _text(value):
            raise _err(f"{where}.{name}", "непустая строка")
    if cell["provider"] not in PROVIDERS:
        raise _err(f"{where}.provider",
                   f"неизвестный провайдер {cell['provider']!r}, допустимы: {', '.join(PROVIDERS)}")
    return {"provider": cell["provider"], "label": cell["label"], "title": cell["title"]}


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


def _validate_strip(value, where: str, warnings: list[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise _err(where, "должен быть объектом {label, provider|glyph}")
    _extra_fields(value, STRIP_FIELDS, where)
    label = _present(value, "label", where)
    if not _text(label):
        raise _err(f"{where}.label", "непустая строка")
    has_provider = "provider" in value
    has_glyph = "glyph" in value
    if has_provider == has_glyph:
        raise _err(where, "нужно ровно одно из provider или glyph")
    if has_provider:
        if value["provider"] not in PROVIDERS:
            raise _err(f"{where}.provider",
                       f"неизвестный провайдер {value['provider']!r}, допустимы: {', '.join(PROVIDERS)}")
        return {"provider": value["provider"], "label": label}
    glyph = value["glyph"]
    if not isinstance(glyph, str) or not GLYPH_RE.match(glyph):
        raise _err(f"{where}.glyph", "имя иконки должно подходить под ^[a-z][a-z0-9-]*$")
    known = icon_names(ICONS_PATH)
    if known and glyph not in known:
        # Как с `icon` (listik-itg8): опечатка в имени глифа не отменяет файл
        # (listik-uiza) — глиф не рисуется, причина в `glyph_error` и в `warnings`.
        warning = f"{where}.glyph: иконки {glyph!r} нет в icons.ts; глиф не будет показан"
        if warnings is not None:
            warnings.append(warning)
        return {"glyph": None, "label": label, "glyph_error": warning}
    return {"glyph": glyph, "label": label}


def _validate_command(value, where: str) -> list[str]:
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

    У `direct`-записи уровня в ключе нет (`dsh`, `grok`, `codex`) — она и есть
    `direct`. У `pipeline` берётся часть ключа до первого `-` (`xhigh-pipeline` →
    `xhigh`), но только если она из `ROUTE_ICONS`: у `feature-pipeline` и
    `inherit-pipeline` уровня нет, и иконка для них не выдумывается (`None`).
    """
    if kind == "direct":
        return "direct"
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
        raise _err(f"{where}.kind", 'должен быть "pipeline" или "direct"')

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
        record["roles"] = _validate_roles(item["roles"], f"{where}.roles")
        if "harness" in item:
            raise _err(f"{where}.harness", "у pipeline-записи harness быть не должно")
    else:
        if "roles" in item:
            raise _err(f"{where}.roles", "у direct-записи ролей быть не должно")
        harness = _present(item, "harness", where)
        if harness not in HARNESSES:
            raise _err(f"{where}.harness",
                       f"неизвестный харнесс {harness!r}, допустимы: {', '.join(HARNESSES)}")
        record["harness"] = harness

    if "strip" in item:
        record["strip"] = _validate_strip(item["strip"], f"{where}.strip", warnings)
    record["command"] = (_validate_command(item["command"], f"{where}.command")
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


def ensure_runtime_copy(source=SOURCE_PATH, target=RUNTIME_PATH) -> bool:
    """Скопировать `source` в `target`, если копии ещё нет.

    Существующий `target` не трогается: автор мог вписать туда команды. Возвращает
    `True`, если файл скопирован, и `False`, если копия уже была. `FileNotFoundError`,
    если копии нет и недоступен источник.
    """
    target = Path(target)
    if target.exists():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True


def load(path=SOURCE_PATH) -> RoutesState:
    """Прочитать, разобрать и проверить файл. Никогда не бросает исключений.

    Любая ошибка (нет файла, нет прав, путь — каталог, битый JSON, `RoutesError`)
    даёт `ok=False`, текст ошибки и строку в stderr. Неизвестный `icon` — не ошибка:
    запись получает фолбэк, каждый такой случай идёт в `warnings` состояния и
    отдельной строкой в stderr. Состояние модуля не меняет — его кладёт
    `init_at_startup`.
    """
    file = Path(path)
    warnings: list[str] = []
    try:
        obj = json.loads(file.read_text(encoding="utf-8"))
        records = validate(obj, warnings)
    except Exception as exc:  # noqa: BLE001 — наружу не бросаем ничего
        return _failed(_describe(exc), file)
    for warning in warnings:
        _warned(warning)
    return RoutesState(ok=True, error=None, path=str(file), routes=records,
                       by_key={record["key"]: record for record in records},
                       warnings=warnings)


def current() -> RoutesState:
    """Последнее загруженное состояние; до первой загрузки — «не загружен»."""
    if _state is None:
        return RoutesState(ok=False, error="routes.json не загружен",
                           path=str(RUNTIME_PATH), routes=[], by_key={})
    return _state


def load_local(path=None) -> RoutesState:
    """Дочитать маршруты процессу без сервера: CLI в локальном режиме и MCP по stdio.

    Сервер зовёт `init_at_startup` — копирует образец из репозитория в рабочую копию
    и читает её. Здесь только чтение той же рабочей копии (`$LISTIK_ROUTES`, иначе
    `~/.config/listik/routes.json`), а если её ещё нет — образца `routes.json` из
    репозитория: заводить чужие файлы конфигов CLI не должен. Уже загруженное
    состояние не перечитываем — как и `init_at_startup`.
    """
    global _state
    if _state is not None:
        return _state
    source = Path(path) if path is not None else (
        RUNTIME_PATH if RUNTIME_PATH.exists() else SOURCE_PATH)
    _state = load(source)
    return _state


def is_route_label(label: str) -> bool:
    """Метка маршрута (`harness:<x>`/`process:<y>`) — её ставит и снимает сервер."""
    return label.startswith(LABEL_PREFIXES)


def labels_for(route_key: str | None) -> list[str]:
    """Метки карточки для маршрута — те же, что ставит форма «Новая задача» на доске.

    У `direct` — харнесс самой записи (`harness:<harness>`, `process:direct`), у
    `pipeline` — оркестратор `claude` и ключ записи (`harness:claude`,
    `process:<key>`): конвейер ведёт claude, а провайдеры ролей у записей разные.
    Пустой или неизвестный ключ (маршрута нет в таблице, файл не загружен или битый) —
    пустой список: метки не выдумываем.
    """
    key = (route_key or "").strip()
    if not key:
        return []
    record = current().by_key.get(key)
    if not record:
        return []
    if record["kind"] == "direct":
        return [f"harness:{record['harness']}", "process:direct"]
    return ["harness:claude", f"process:{record['key']}"]


def init_at_startup(source=SOURCE_PATH, target=RUNTIME_PATH) -> RoutesState:
    """Один раз за процесс: копия в `target` (если её нет) и её загрузка в `current()`.

    Ошибка копирования не роняет сервер: `current()` получает `ok=False` с текстом
    `копирование routes.json: <ошибка>`, `load` при этом не вызывается.
    """
    global _state
    try:
        ensure_runtime_copy(source, target)
    except Exception as exc:  # noqa: BLE001 — сервер должен подняться в любом случае
        _state = _failed(f"копирование routes.json: {_describe(exc)}", target)
        return _state
    _state = load(target)
    return _state
