"""Клиент API: CLI и агенты ходят в сервер Listik.

Если сервер поднят — работаем через HTTP (одна точка записи, доска обновляется сама).
Если нет — CLI временно работает с базой напрямую, чтобы агент не вставал из-за
незапущенного сервера. Сервер, который уже работает, при этом не ломается: SQLite в WAL.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from . import config as config_mod
from . import db as db_mod
from . import errors
from . import fence as fence_mod
from . import paths


class ApiDown(Exception):
    pass


def _remote_or_local(*, local: bool, host: str | None, port: int | None,
                     remote, local_call):
    """Выполнить операцию через API, если сервер доступен, иначе локально."""
    if not local and is_up(host, port):
        return remote()
    return local_call()


def base_url(host: str | None = None, port: int | None = None) -> str:
    cfg = config_mod.load()
    h = host or cfg["server"]["host"]
    p = int(port or cfg["server"]["port"])
    return f"http://{h}:{p}"


def token() -> str:
    cfg = config_mod.load()
    return (cfg.get("auth") or {}).get("token", "")


def owner(explicit: str | None = None) -> str:
    """От чьего имени работаем: флаг → `LISTIK_OWNER` → `[auth] owner` → пусто.

    Пустая строка значит «не представились»: в серверном режиме такой вызов
    отклонит store, в локальном — владелец не нужен вовсе.
    """
    cfg_owner = ""
    try:
        cfg_owner = config_mod.default_owner()
    except Exception:  # noqa: BLE001 — битый config.toml не должен ломать команду
        cfg_owner = ""
    for candidate in (explicit, os.environ.get("LISTIK_OWNER"), cfg_owner):
        value = (candidate or "").strip()
        if value:
            return value
    return ""


#: Операции `local_call`, которые понимают `as_owner`. Остальным ключ не передаём:
#: у их функций store такого аргумента нет и вызов упал бы TypeError.
OWNER_LOCAL_OPS = frozenset({"list", "board", "ready", "create", "claim", "heartbeat",
                             "stage", "update"})

#: Тот же `owner()` под именем без конфликта: в `request()`/`health()` параметр
#: называется `owner` и перекрывает имя функции.
_resolve_owner = owner


def health(host: str | None = None, port: int | None = None, timeout: float = 2.0,
           owner: str | None = None) -> dict | None:
    """Состояние сервера: None — не отвечает. 401 тоже считается «отвечает»."""
    req = urllib.request.Request(f"{base_url(host, port)}/api/health")
    if token():
        req.add_header("Authorization", f"Bearer {token()}")
    who = _resolve_owner(owner)
    if who:
        req.add_header("X-Listik-Owner", who)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("data") if isinstance(data, dict) else None
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return {"status": "ok", "authed": False}
        return None
    except Exception:  # noqa: BLE001
        return None


def is_up(host: str | None = None, port: int | None = None, timeout: float = 2.0) -> bool:
    return health(host, port, timeout) is not None


def _query_string(query: dict) -> str:
    """Строка запроса без «+» и с сохранением «/»: у проектов slug бывает путём
    (Zoloto585/repo), и urlencode здесь сделал бы из него %252F."""
    parts = []
    for key, value in query.items():
        if value is None:
            continue
        text = str(value)
        safe = ",/" if ("/" in text or "," in text) else ","
        parts.append(f"{urllib.parse.quote(str(key))}={urllib.parse.quote(text, safe=safe)}")
    return "&".join(parts)


def request(method: str, path: str, *, query: dict | None = None, body: dict | None = None,
            host: str | None = None, port: int | None = None, timeout: float = 60.0,
            owner: str | None = None, fence: fence_mod.Token | None = None) -> dict:
    # Кириллица в пути (например, в ID задачи) кодируется здесь: иначе urllib падает
    # с UnicodeEncodeError ещё до запроса. Уже закодированные сегменты (%2F) целы —
    # «%» в safe.
    url = base_url(host, port) + urllib.parse.quote(path, safe="/?&=%")
    if query:
        qs = _query_string(query)
        if qs:
            url += "?" + qs
    data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token()}")
    # Идентичность — только заголовком: в строке запроса и теле её нет.
    who = _resolve_owner(owner)
    if who:
        req.add_header("X-Listik-Owner", who)
    if fence is not None:
        for key, value in fence_mod.to_headers(fence).items():
            req.add_header(key, value)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_ok = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        code = errors.code_for_status(exc.code)
        hint = errors.hint_for_status(exc.code)
        try:
            payload = json.loads(raw)
            message = payload.get("error") or raw
            # Сервер отдаёт свой код (см. listik/errors.py): он точнее статуса —
            # «задача уже удерживается» это 400, но по смыслу conflict.
            code = payload.get("code") or code
            # Подсказка по коду сильнее подсказки по статусу: 409 у `revoked` и у
            # обычного `conflict` — один и тот же HTTP-статус с разным смыслом
            # («посмотри состояние карточки…» зомби только сбило бы с толку).
            hint = errors.HINT_BY_CODE.get(code) or hint
        except json.JSONDecodeError:
            message = raw
        raise errors.ListikError(str(message).strip(), code=code, hint=hint,
                                 status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ApiDown(str(exc)) from exc
    try:
        payload = json.loads(raw_ok.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        # Отвечает не Listik (прокси, чужая страница): трейсбек агенту не поможет.
        raise errors.ListikError("сервер ответил не-JSON", code=errors.HTTP_ERROR,
                                 hint="проверь адрес и состояние сервера: listik status") from exc
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise errors.ListikError(str(payload.get("error")),
                                 code=payload.get("code") or errors.HTTP_ERROR)
    return payload.get("data") if isinstance(payload, dict) and "data" in payload else payload


# ------------------------------------------------------------------ локальный фолбэк

#: Операции `local_call`, которые пишут в конкретную карточку: ключ её id в
#: `kwargs` (обычно `task_id`, у зависимостей — `issue_id`) — по нему `fence.guard`
#: сверяет токен с текущим поколением до вызова store.
FENCED_LOCAL_OPS = {
    "update": "task_id", "needs-owner": "task_id", "claim": "task_id",
    "heartbeat": "task_id", "stage": "task_id", "comment": "task_id",
    "dep_add": "issue_id", "dep_remove": "issue_id",
}


def local_call(op: str, *, fence: fence_mod.Token | dict | None = None, **kwargs):
    """Прямая работа с базой, когда сервер не поднят."""
    from . import search as search_mod
    from . import store

    conn = db_mod.init()
    id_key = FENCED_LOCAL_OPS.get(op)
    if id_key is not None:
        token = fence_mod.from_mapping(fence) if not isinstance(fence, fence_mod.Token) else fence
        if token is not None:
            task_id = kwargs.get(id_key)
            fence_mod.guard(conn, task_id, token, op=op, args=kwargs,
                            actor=kwargs.get("actor") or kwargs.get("author")
                            or kwargs.get("created_by"),
                            harness=kwargs.get("harness"))
    if op not in OWNER_LOCAL_OPS:
        # `as_owner` — идентичность вызова, её понимают не все операции store.
        # Поле `owner` (данные карточки) остаётся: его пишут create/update.
        kwargs.pop("as_owner", None)
    if op == "meta":
        return {
            "projects": store.list_projects(conn),
            "actors": store.list_actors(conn),
            "facets": store.facet_values(conn),
            "statuses": store.STATUS_TITLES,
            "stages": store.STAGE_TITLES,
            "priorities": store.PRIORITY_TITLES,
        }
    if op == "stats":
        return store.stats(conn, project=kwargs.get("project"))
    if op == "board":
        return store.board(conn, group_by=kwargs.get("group_by", "status"),
                           project=kwargs.get("project"),
                           include_closed=kwargs.get("include_closed", False),
                           limit_per_column=kwargs.get("limit", 300),
                           as_owner=kwargs.get("as_owner"))
    if op == "list":
        return store.list_tasks(conn, **kwargs)
    if op == "show":
        task = store.get_task(conn, kwargs["task_id"],
                              with_rejected=bool(kwargs.get("rejected", False)))
        fields = kwargs.get("fields")
        # Фильтр --fields здесь же, а не на клиенте: локальный режим — это "сервера
        # нет", и неизвестное поле должно дать тот же bad_argument, что и по HTTP.
        return store.select_task_fields(task, fields) if fields else task
    if op == "context":
        from . import documents
        return documents.context(conn, kwargs["task_id"], kwargs.get("stage", "s1-spec"),
                                 portion=kwargs.get("portion"), max_chars=kwargs.get("max_chars"))
    if op == "create":
        if kwargs.get("autostart"):
            raise errors.ListikError(
                "autostart больше не поддерживается",
                code=errors.BAD_ARGUMENT, exit_code=2,
                hint="карточку с маршрутом берёт рой; запустить процесс — listik launch <id>")
        return store.create_task(conn, **kwargs)
    if op == "update":
        task_id = kwargs.pop("task_id")
        return store.update_task(conn, task_id, **kwargs)
    if op == "needs-owner":
        task_id = kwargs.pop("task_id")
        return store.set_needs_owner(conn, task_id, **kwargs)
    if op == "claim":
        task_id = kwargs.pop("task_id")
        return store.claim(conn, task_id, **kwargs)
    if op == "heartbeat":
        task_id = kwargs.pop("task_id")
        return store.heartbeat(conn, task_id, **kwargs)
    if op == "stage":
        task_id = kwargs.pop("task_id")
        return store.next_stage(conn, task_id, **kwargs)
    if op == "comment":
        task_id = kwargs.pop("task_id")
        return store.add_comment(conn, task_id, **kwargs)
    if op == "ready":
        from . import deps as deps_mod
        return {"tasks": deps_mod.ready_tasks(conn, project=kwargs.get("project"),
                                              stage=kwargs.get("stage"),
                                              include_occupied=kwargs.get("include_occupied", False),
                                              limit=kwargs.get("limit", 50),
                                              as_owner=kwargs.get("as_owner")),
                "cycles": deps_mod.cycles(conn)}
    if op == "blocked":
        from . import deps as deps_mod
        return {"tasks": deps_mod.blocked_tasks(conn, project=kwargs.get("project"),
                                                limit=kwargs.get("limit", 100))}
    if op == "waves":
        from . import deps as deps_mod
        return deps_mod.waves(conn, project=kwargs.get("project"), stage=kwargs.get("stage"))
    if op == "waves_apply":
        from . import deps as deps_mod
        return deps_mod.apply_resource_blocks(
            conn, project=kwargs.get("project") or "", stage=kwargs.get("stage"))
    if op == "swarm_plan":
        from . import swarm_llm
        return swarm_llm.plan(
            conn, project=kwargs.get("project") or "",
            stage=(kwargs.get("stage") or "").strip() or None, apply=bool(kwargs.get("apply")))
    if op == "swarm_rescope":
        from . import swarm_llm
        return swarm_llm.rescope(
            conn, project=kwargs.get("project") or "", tasks=kwargs.get("tasks"),
            drift=kwargs.get("drift"), apply=bool(kwargs.get("apply")))
    if op == "mentions":
        from . import deps as deps_mod
        return deps_mod.mentioned(conn, kwargs["task_id"], limit=kwargs.get("limit", 50))
    if op == "dep_tree":
        from . import deps as deps_mod
        return deps_mod.graph(conn, kwargs["task_id"], depth=kwargs.get("depth", 3))
    if op == "task_ready":
        from . import deps as deps_mod
        return deps_mod.ready(conn, kwargs["task_id"])
    if op == "timeline":
        return {"items": store.task_timeline(conn, limit=kwargs.get("limit", 100),
                                             project=kwargs.get("project"))}
    if op == "memory":
        return search_mod.search_memories(conn, kwargs["query"], limit=kwargs.get("limit", 20),
                                          project=kwargs.get("project"))
    if op == "embed":
        from . import embed as embed_mod
        return embed_mod.embed_pending(conn, limit=kwargs.get("limit", 0))
    if op == "search":
        return search_mod.search(conn, kwargs.pop("query"), **kwargs)
    if op == "dep_add":
        return store.add_dep(conn, kwargs["issue_id"], kwargs["depends_on"],
                             kwargs.get("dep_type", "blocks"), kwargs.get("created_by"), confirm=kwargs.get("confirm", False))
    if op == "dep_remove":
        return store.remove_dep(conn, kwargs["issue_id"], kwargs["depends_on"],
                                kwargs.get("dep_type"))
    if op == "dep_suggested":
        from . import deps as deps_mod
        return {"items": deps_mod.suggested(conn, project=kwargs.get("project"),
                                            limit=kwargs.get("limit", 100)),
                "generated_at": store.now_iso()}
    if op == "projects":
        return {"projects": store.list_all_projects(conn), "root": str(paths.PROJECTS_ROOT)}
    if op == "routes":
        from . import routes_store
        return routes_store.routes_response(conn)
    if op == "project_add":
        try:
            return store.add_project(conn, path=kwargs.get("path"), slug=kwargs.get("slug"),
                                     title=kwargs.get("title"), kind=kwargs.get("kind", "native"))
        except ValueError as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.BAD_ARGUMENT) from exc
    if op == "project_archive":
        try:
            return store.update_project(conn, kwargs["slug"], archived=1 if kwargs.get("archived") else 0)
        except errors.NotFound as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.NOT_FOUND,
                                     hint="список проектов: listik projects") from exc
    if op == "project_remove":
        try:
            return store.remove_project(conn, kwargs["slug"], force=kwargs.get("force", False))
        except errors.NotFound as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.NOT_FOUND,
                                     hint="список проектов: listik projects") from exc
        except ValueError as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.CONFLICT) from exc
    if op in ("revoke", "launch"):
        # Процесс задачи держит сервер: локальному фолбэку некому его снять/запустить.
        raise errors.ListikError(
            f"{op} выполняет только сервер: процесс задачи держит он",
            code=errors.UNSUPPORTED, hint="подними сервер: listik serve")
    if op == "project_routing":
        try:
            return store.update_project(conn, kwargs["slug"], routing=kwargs.get("routing") or {})
        except errors.NotFound as exc:
            raise errors.ListikError(
                f"проект не найден: {kwargs['slug']}", code=errors.NOT_FOUND,
                hint="добавьте его: listik projects --add <путь> [--slug …]") from exc
        except ValueError as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.BAD_ARGUMENT) from exc
    raise errors.ListikError(f"локальный режим не умеет: {op}", code=errors.UNSUPPORTED)


# ------------------------------------------------------------------ репозитории (проекты)

def list_projects(*, host: str | None = None, port: int | None = None,
                  local: bool = False) -> dict:
    """Все проекты, включая скрытые с доски (для настроек доски и `listik projects`)."""
    from . import store
    return _remote_or_local(
        local=local, host=host, port=port,
        remote=lambda: request("GET", "/api/projects", host=host, port=port),
        local_call=lambda: {"projects": store.list_all_projects(db_mod.init()),
                            "root": str(paths.PROJECTS_ROOT)},
    )


def add_project(*, path: str | None = None, slug: str | None = None, title: str | None = None,
                kind: str = "native", host: str | None = None, port: int | None = None,
                local: bool = False) -> dict:
    """Добавить репозиторий (каталог) на доску.

    Относительный путь разрешается здесь, в cwd вызывающего: сервер относительный
    `path` отклоняет 400 (`bad_argument`) — у него свой рабочий каталог, поэтому
    в API уходит уже абсолютный путь.
    """
    if path:
        path = str(Path(path).expanduser().resolve())
    body = {"path": path, "slug": slug, "title": title, "kind": kind}
    from . import store
    def local_add():
        try:
            return store.add_project(db_mod.init(), **body)
        except ValueError as exc:
            raise errors.ListikError(errors.message_of(exc),
                                     code=errors.BAD_ARGUMENT) from exc
    return _remote_or_local(
        local=local, host=host, port=port,
        remote=lambda: request("POST", "/api/projects", body=body, host=host, port=port),
        local_call=local_add,
    )


def set_project_archived(slug: str, archived: bool, *, host: str | None = None,
                         port: int | None = None, local: bool = False) -> dict:
    """Скрыть проект с доски (`archived=True`) или вернуть обратно."""
    from . import store
    def local_archive():
        try:
            return store.update_project(db_mod.init(), slug, archived=1 if archived else 0)
        except errors.NotFound as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.NOT_FOUND,
                                     hint="список проектов: listik projects") from exc
    return _remote_or_local(
        local=local, host=host, port=port,
        remote=lambda: request("PATCH", f"/api/projects/{urllib.parse.quote(slug, safe='')}",
                               body={"archived": 1 if archived else 0}, host=host, port=port),
        local_call=local_archive,
    )


def remove_project(slug: str, *, force: bool = False, host: str | None = None,
                   port: int | None = None, local: bool = False) -> dict:
    """Убрать проект из Listik. Проект с задачами — только с `force`."""
    from . import store
    def local_remove():
        try:
            return store.remove_project(db_mod.init(), slug, force=force)
        except errors.NotFound as exc:
            raise errors.ListikError(errors.message_of(exc), code=errors.NOT_FOUND,
                                     hint="список проектов: listik projects") from exc
        except ValueError as exc:
            # Как 409 у сервера: проект с задачами сначала скрывают.
            raise errors.ListikError(errors.message_of(exc), code=errors.CONFLICT) from exc
    return _remote_or_local(
        local=local, host=host, port=port,
        remote=lambda: request("DELETE", f"/api/projects/{urllib.parse.quote(slug, safe='')}",
                               query={"force": "1"} if force else None, host=host, port=port),
        local_call=local_remove,
    )


def set_project_routing(slug: str, routing: dict, *, local: bool = False,
                        host: str | None = None, port: int | None = None) -> dict:
    """Установить (или сбросить, `{}`) переопределение маршрутизации проекта.

    Возвращает словарь проекта из `store._project_with_routing` (с `routing_effective`),
    чтобы вызывающий код не перечитывал проект отдельно.
    """
    from . import store
    def local_routing():
        try:
            return store.update_project(db_mod.init(), slug, routing=routing)
        except errors.NotFound as exc:
            raise errors.ListikError(
                f"проект не найден: {slug}", code=errors.NOT_FOUND,
                hint="добавьте его: listik projects --add <путь> [--slug …]") from exc
        except ValueError as exc:
            raise errors.ListikError(errors.message_of(exc),
                                     code=errors.BAD_ARGUMENT) from exc
    return _remote_or_local(
        local=local, host=host, port=port,
        remote=lambda: request("PATCH", f"/api/projects/{urllib.parse.quote(slug, safe='')}",
                               body={"routing": routing}, host=host, port=port),
        local_call=local_routing,
    )
