"""HTTP API и доска Listik (stdlib, без внешних зависимостей).

Один процесс — одна точка записи в базу. Слушает только 127.0.0.1 и требует токен
из config.toml в корне Listik. Агенты (dsh/grok/claude/codex) ходят сюда же, поэтому API
делает всё: и чтение, и запись, и переходы этапов.
"""
from __future__ import annotations

import json
import mimetypes
import os
import queue
import signal
import sqlite3
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import actors as actors_mod
from . import config as config_mod
from . import db as db_mod
from . import deps as deps_mod
from . import embed as embed_mod
from . import errors as errors_mod
from . import launcher as launcher_mod
from . import mcp
from . import paths
from . import routes as routes_mod
from . import search as search_mod
from . import store

# Соединение с базой — своё на каждый поток.
#
# ThreadingHTTPServer обслуживает запросы в отдельных потоках (доска шлёт
# /api/board, /api/stats, /api/ready и /api/blocked параллельно, плюс SSE и
# фоновый досчёт векторов). Одно соединение sqlite3 на всех — это не сериализация,
# а гонка: объект connection не потокобезопасен, и параллельные запросы в него
# давали то «database disk image is malformed», то «bad parameter or other API
# misuse», то IndexError на строках-результатах. Писателей это ломало бы тихо:
# сервер отвечает 500, а доска показывает «ошибка операции».
# Поэтому соединение живёт в threading.local, а пишет всё равно SQLite: WAL
# плюс busy_timeout в `db.connect` сериализуют запись между соединениями.
_conn_local = threading.local()
_conn_lock = threading.Lock()
_conn_made = False
_subs: list[queue.Queue] = []
_subs_lock = threading.Lock()


def get_conn():
    """Соединение текущего потока; схема и миграции применяются один раз за процесс."""
    global _conn_made
    conn = getattr(_conn_local, "conn", None)
    if conn is not None:
        return conn
    with _conn_lock:
        if not _conn_made:
            conn = db_mod.init()
            _conn_made = True
        else:
            conn = db_mod.connect()
    _conn_local.conn = conn
    return conn


def close_thread_conn() -> None:
    """Закрыть соединение текущего потока (listik-sxcd).

    Поток-обработчик ThreadingHTTPServer живёт одно HTTP-соединение; без явного
    закрытия его sqlite-соединение ждало сборщика мусора и держало fd на базе и на
    уже удалённых WAL. Вызывается в конце каждого потока-обработчика.
    """
    conn = getattr(_conn_local, "conn", None)
    if conn is None:
        return
    _conn_local.conn = None
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


# Последняя ошибка базы в фоновом потоке — отдаётся в /api/health, чтобы не молчать.
_db_error: dict | None = None


def _background_db_error(where: str, exc: Exception) -> None:
    """DatabaseError в фоне: громко в лог, в health, и переоткрыть соединение потока.

    Если базу или WAL подменили под работающим сервером, старое соединение ловит
    «database disk image is malformed» бесконечно; свежее соединение видит новый файл.
    """
    global _db_error
    _db_error = {"where": where, "error": f"{type(exc).__name__}: {exc}", "at": store.now_iso()}
    print(f"[{where}] ОШИБКА БАЗЫ: {type(exc).__name__}: {exc} — переоткрываю соединение",
          flush=True)
    close_thread_conn()


_embed_stop = threading.Event()


def start_embed_worker(interval: float = 45.0, batch_limit: int = 200) -> threading.Thread:
    """Фоновый досчёт векторов.

    Задачи создаются и правятся постоянно, а поиск должен находить свежее.
    Раз в `interval` секунд добираем то, для чего вектора ещё нет или текст изменился.
    Векторная ветка необязательна для работы, поэтому любые ошибки (в том числе
    недоступный ollama) только логируются.
    """
    def loop() -> None:
        while not _embed_stop.wait(interval):
            try:
                from . import documents as documents_mod
                res = documents_mod.refresh_all(get_conn())
                if res.get("reindexed"):
                    print(f"[documents] переиндексировано: {res['reindexed']}", flush=True)
                    publish("documents", res)
            except sqlite3.DatabaseError as exc:
                _background_db_error("documents", exc)
            except Exception as exc:  # noqa: BLE001
                print(f"[documents] пропуск: {type(exc).__name__}: {exc}", flush=True)
            try:
                # соединение своё на поток, поэтому лок больше не нужен
                res = embed_mod.embed_pending(get_conn(), limit=batch_limit, verbose=False)
                if res.get("embedded"):
                    search_mod.invalidate_vectors()
                    print(f"[embed] досчитано векторов: {res['embedded']}", flush=True)
                    publish("embed", res)
            except sqlite3.DatabaseError as exc:
                _background_db_error("embed", exc)
            except Exception as exc:  # noqa: BLE001
                print(f"[embed] пропуск: {type(exc).__name__}: {exc}", flush=True)

    thread = threading.Thread(target=loop, name="listik-embed", daemon=True)
    thread.start()
    return thread


def publish(kind: str, payload: dict) -> None:
    """Рассылка событий подписчикам SSE (доска обновляется без перезагрузки)."""
    message = json.dumps({"kind": kind, "at": store.now_iso(), "payload": payload},
                         ensure_ascii=False)
    with _subs_lock:
        dead = []
        for q in _subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _subs.remove(q)


# ------------------------------------------------------------------ утилиты

def as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "true", "yes", "on")


def as_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class ApiError(Exception):
    def __init__(self, status: int, message: str, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        # Машинный код ошибки (см. listik/errors.py): CLI и агенты ветвятся по нему,
        # а не по HTTP-статусу. Явный code важнее статуса: «задача уже удерживается» —
        # это 400 по контракту API, но по смыслу conflict.
        self.code = code or errors_mod.code_for_status(status)


def api_error(status: int, exc: BaseException) -> ApiError:
    """ApiError по пойманному исключению: сообщение без кавычек KeyError + код по типу."""
    return ApiError(status, errors_mod.message_of(exc), code=errors_mod.code_of(exc))


def need(body: dict, key: str):
    value = body.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ApiError(400, f"не передан обязательный параметр: {key}")
    return value


# ------------------------------------------------------------------ обработчики

def handle(method: str, path: str, query: dict, body: dict, authed: bool = False) -> tuple[int, object]:
    conn = get_conn()
    parts = [p for p in path.strip("/").split("/") if p]
    q1 = lambda k, d=None: query.get(k, [d])[0] if isinstance(query.get(k), list) else query.get(k, d)  # noqa: E731

    if path == "/api/health":
        cfg = config_mod.load()
        data = {
            "status": "ok",
            "version": "0.1.0",
            "now": store.now_iso(),
            "embed": {"model": cfg["embed"]["model"]},
            "authed": authed,
        }
        if authed and _db_error is not None:
            data["db_error"] = _db_error
        # Проба живости отдаётся без токена (по ней CLI понимает, поднят ли сервер),
        # поэтому подробности о базе — только авторизованному.
        if authed:
            routes_state = routes_mod.current()
            data.update({
                "db": str(paths.DB_PATH),
                "counts": db_mod.counts(conn),
                "embed": embed_mod.health(cfg["embed"]["model"]),
                "routes": {
                    "ok": routes_state.ok,
                    "error": routes_state.error,
                    "path": routes_state.path,
                    "count": len(routes_state.routes),
                },
            })
        return 200, data

    if path == "/api/routes":
        # Данные — из состояния, загруженного один раз при старте: правка файла на
        # ходу сервер не перечитывает. `command` наружу не отдаём — это argv запуска.
        state = routes_mod.current()
        return 200, {
            "ok": state.ok,
            "error": state.error,
            "path": state.path,
            "routes": [{k: v for k, v in record.items() if k != "command"}
                       for record in state.routes],
        }

    if path == "/api/meta":
        return 200, {
            "projects": store.list_projects(conn, include_archived=as_bool(q1("archived", False))),
            "actors": store.list_actors(conn),
            "facets": store.facet_values(conn),
            "statuses": store.STATUS_TITLES,
            "stages": store.STAGE_TITLES,
            "priorities": store.PRIORITY_TITLES,
        }

    # --- репозитории (проекты) для настроек доски: добавить, скрыть, убрать
    if path == "/api/projects":
        if method == "GET":
            return 200, {"projects": store.list_all_projects(conn),
                         "root": str(paths.PROJECTS_ROOT)}
        if method == "POST":
            try:
                project = store.add_project(
                    conn, path=body.get("path"), slug=body.get("slug"),
                    title=body.get("title"), kind=body.get("kind") or "native")
            except ValueError as exc:
                raise api_error(400, exc) from exc
            publish("project", {"slug": project["slug"],
                                "action": "created" if project.get("created") else "updated"})
            return 201, project

    if len(parts) == 3 and parts[0] == "api" and parts[1] == "projects":
        slug = urllib.parse.unquote(parts[2])
        if method in ("PATCH", "PUT"):
            try:
                project = store.update_project(
                    conn, slug, title=body.get("title"), path=body.get("path"),
                    color=body.get("color"), archived=body.get("archived"),
                    kind=body.get("kind"), routing=body.get("routing"))
            except KeyError as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise api_error(400, exc) from exc
            publish("project", {"slug": slug, "action": "updated"})
            return 200, project
        if method == "DELETE":
            try:
                # force можно передать и телом, и строкой запроса: DELETE с телом
                # поддерживают не все клиенты, а удаление проекта с задачами — редкое
                force = as_bool(body.get("force", False)) or as_bool(q1("force", False))
                out = store.remove_project(conn, slug, force=force)
            except KeyError as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise api_error(409, exc) from exc
            publish("project", {"slug": slug, "action": "removed"})
            return 200, out

    if path == "/api/stats":
        return 200, store.stats(conn, project=q1("project"))

    if path == "/api/board":
        return 200, store.board(
            conn,
            group_by=q1("group_by", "status"),
            project=q1("project"),
            include_closed=as_bool(q1("include_closed", False)),
            limit_per_column=as_int(q1("limit"), 300) or 300,
        )

    if path == "/api/ready":
        return 200, {
            "tasks": deps_mod.ready_tasks(
                conn, project=q1("project"), stage=q1("stage"), harness=q1("harness"),
                include_occupied=as_bool(q1("include_occupied", False)),
                limit=as_int(q1("limit"), 50) or 50),
            "cycles": deps_mod.cycles(conn),
            "generated_at": store.now_iso(),
        }

    if path == "/api/blocked":
        return 200, {
            "tasks": deps_mod.blocked_tasks(
                conn, project=q1("project"), limit=as_int(q1("limit"), 100) or 100),
            "generated_at": store.now_iso(),
        }

    if path == "/api/deps/suggested":
        return 200, {
            "items": deps_mod.suggested(conn, project=q1("project"),
                                        limit=as_int(q1("limit"), 100) or 100),
            "generated_at": store.now_iso(),
        }

    if path == "/api/timeline":
        return 200, {"items": store.task_timeline(conn, limit=as_int(q1("limit"), 100) or 100)}

    if path == "/api/tasks" and method == "GET":
        return 200, store.list_tasks(
            conn,
            project=q1("project"), status=q1("status"), stage=q1("stage"),
            assignee=q1("assignee"), holder=q1("holder"),
            needs_owner=as_bool(q1("needs_owner", False)),
            issue_type=q1("type"), label=q1("label"), text=q1("text"),
            include_closed=as_bool(q1("include_closed", False)),
            include_archived=as_bool(q1("include_archived", False)),
            limit=as_int(q1("limit"), 200) or 200, offset=as_int(q1("offset"), 0) or 0,
            order=q1("order", "updated") or "updated",
        )

    if path == "/api/tasks" and method == "POST":
        autostart = as_bool(body.get("autostart", False))
        route = body.get("route")
        if route is not None and not isinstance(route, str):
            raise ApiError(400, "route должен быть строкой")
        # Автостарт без маршрута запускать нечего: задача не создаётся вовсе.
        if autostart and not (route or "").strip():
            raise ApiError(400, "autostart: нужен непустой route")
        task = store.create_task(
            conn,
            title=need(body, "title"),
            project=body.get("project"),
            description=body.get("description", ""),
            acceptance=body.get("acceptance", ""),
            design=body.get("design", ""),
            notes=body.get("notes", ""),
            issue_type=body.get("type") or body.get("issue_type") or "task",
            status=body.get("status", "open"),
            priority=as_int(body.get("priority"), 2),
            assignee=body.get("assignee"),
            stage=body.get("stage"),
            labels=body.get("labels") or [],
            spec_path=body.get("spec_path"),
            checklist_path=body.get("checklist_path"),
            review_path=body.get("review_path"),
            decision_path=body.get("decision_path"),
            journal_path=body.get("journal_path"),
            external_ref=body.get("external_ref"),
            source=body.get("source", "native"),
            task_id=body.get("id"),
            created_by=body.get("actor") or body.get("created_by"),
            needs_owner=as_bool(body.get("needs_owner", False)),
            harness=body.get("harness"),
            autostart=autostart,
            route=route,
        )
        if autostart:
            # Процесс не ждём: start возвращается сразу после Popen, отказ (битый
            # routes.json, нет маршрута/command/каталога) не отменяет создание задачи.
            launcher_mod.start(conn, task["id"], notify=publish)
            task = store.get_task(conn, task["id"])
        publish("task", {"id": task["id"], "action": "created"})
        return 201, task

    if len(parts) >= 2 and parts[0] == "api" and parts[1] == "tasks":
        if len(parts) == 5 and parts[3] == "documents":
            # Документы задачи содержимым: на удалённом сервере файлов проектов нет,
            # поэтому текст приезжает телом запроса и читается из базы.
            tid, kind = parts[2], urllib.parse.unquote(parts[4])
            from . import documents
            if method == "PUT":
                content = body.get("content")
                if not isinstance(content, str):
                    raise ApiError(400, "не передан обязательный параметр: content")
                try:
                    out = documents.put_document(conn, tid, kind, content,
                                                 path=body.get("path"), actor=body.get("actor"))
                except KeyError as exc:
                    raise api_error(404, exc) from exc
                except ValueError as exc:
                    raise api_error(400, exc) from exc
                publish("task", {"id": tid, "action": "document"})
                return 200, out
            if method == "GET":
                try:
                    out = documents.get_document(conn, tid, kind)
                except KeyError as exc:
                    raise api_error(404, exc) from exc
                except ValueError as exc:
                    raise api_error(400, exc) from exc
                return 200, out
            raise ApiError(405, "метод не поддерживается")
        if len(parts) == 5 and parts[3] == "deps" and method == "DELETE":
            tid, dep_id = parts[2], urllib.parse.unquote(parts[4])
            out = store.remove_dep(conn, tid, dep_id, dep_type=q1("dep_type"))
            publish("task", {"id": tid, "action": "deps"})
            return 200, out
        if len(parts) == 3:
            tid = parts[2]
            if method == "GET":
                try:
                    task = store.get_task(conn, tid, with_details=as_bool(q1("details", True)))
                    if as_bool(q1("deps", True)):
                        task["deps_state"] = deps_mod.ready(conn, tid)
                    return 200, task
                except KeyError as exc:
                    raise api_error(404, exc) from exc
            if method in ("PATCH", "PUT"):
                fields = {k: v for k, v in body.items()
                          if k in store.UPDATABLE}
                try:
                    task = store.update_task(conn, tid, actor=body.get("actor"),
                                             harness=body.get("harness"),
                                             note=body.get("note"), **fields)
                except KeyError as exc:
                    raise api_error(404, exc) from exc
                publish("task", {"id": tid, "action": "updated"})
                return 200, task
            if method == "DELETE":
                store.delete_task(conn, tid)
                publish("task", {"id": tid, "action": "deleted"})
                return 200, {"deleted": tid}
        if len(parts) == 4:
            tid, action = parts[2], parts[3]
            if action == "context" and method == "GET":
                try:
                    from . import documents
                    out = documents.context(conn, tid, q1("stage") or "s1-spec",
                                             portion=q1("portion"),
                                             max_chars=as_int(q1("max_chars"), None))
                except KeyError as exc:
                    raise api_error(404, exc) from exc
                return 200, out
            try:
                if action == "claim":
                    out = store.claim(conn, tid, holder=need(body, "holder"),
                                      harness=body.get("harness"), note=body.get("note"),
                                      force=as_bool(body.get("force", False)))
                elif action == "heartbeat":
                    out = store.heartbeat(conn, tid, holder=need(body, "holder"),
                                          note=body.get("note"), harness=body.get("harness"))
                elif action == "stage":
                    out = store.next_stage(conn, tid, holder=body.get("holder"),
                                           note=body.get("note"), harness=body.get("harness"),
                                           to_stage=body.get("to") or body.get("stage"))
                elif action == "comment":
                    out = store.add_comment(conn, tid, need(body, "text"),
                                            author=body.get("author") or body.get("actor"),
                                            kind=body.get("kind", "comment"),
                                            harness=body.get("harness"))
                elif action == "deps" and body.get("depends_on"):
                    out = store.add_dep(conn, tid, need(body, "depends_on"),
                                        body.get("dep_type", "blocks"), body.get("actor"), confirm=as_bool(body.get("confirm", False)))
                elif action == "deps":
                    out = deps_mod.graph(conn, tid, depth=as_int(body.get("depth"), 3) or 3)
                elif action == "ready":
                    out = deps_mod.ready(conn, tid)
                elif action == "mentions":
                    out = deps_mod.mentioned(conn, tid, limit=as_int(body.get("limit"), 50) or 50)
                elif action == "needs-owner":
                    out = store.set_needs_owner(conn, tid, value=as_bool(body.get("value", True)),
                                                text=body.get("note"), actor=body.get("actor"),
                                                harness=body.get("harness"))
                elif action == "release":
                    out = store.update_task(conn, tid, actor=body.get("actor"),
                                            holder="", note=body.get("note") or "освободил")
                elif action == "done":
                    out = store.update_task(conn, tid, actor=body.get("actor"),
                                            status="done", stage="done",
                                            result=body.get("result", ""),
                                            close_reason=body.get("reason") or body.get("result"),
                                            note=body.get("note"))
                else:
                    raise ApiError(404, f"неизвестное действие: {action}")
            except KeyError as exc:
                raise api_error(404, exc) from exc
            except ValueError as exc:
                raise api_error(400, exc) from exc
            publish("task", {"id": tid, "action": action})
            return 200, out

    if path == "/api/search":
        query_text = q1("q") or q1("query") or ""
        if not query_text:
            raise ApiError(400, "нужен параметр q")
        res = search_mod.search(
            conn, query_text,
            limit=as_int(q1("limit"), 15) or 15,
            project=q1("project"), status=q1("status"), stage=q1("stage"),
            actor=q1("actor"), needs_owner=as_bool(q1("needs_owner", False)),
            mode=q1("mode", "hybrid") or "hybrid",
        )
        return 200, res

    if path == "/api/memory":
        q = q1("q")
        if q:
            items = search_mod.search_memories(
                conn, q, limit=as_int(q1("limit"), 20) or 20, project=q1("project"),
                mode=q1("mode", "hybrid") or "hybrid")
            return 200, items
        rows = conn.execute(
            "SELECT key, project, body, updated_at FROM memories "
            "ORDER BY updated_at DESC LIMIT ?", (as_int(q1("limit"), 50) or 50,)).fetchall()
        return 200, [dict(r) for r in rows]

    if path == "/api/embed" and method == "POST":
        cfg = config_mod.load()
        res = embed_mod.embed_pending(
            conn, limit=as_int(body.get("limit"), 0) or 0,
            kinds=body.get("kinds", "task,comment,chunk"),
            model=cfg["embed"]["model"], verbose=False,
        )
        return 200, res

    if path == "/api/events":
        limit = as_int(q1("limit"), 50) or 50
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return 200, {"items": [dict(r) for r in rows]}

    raise ApiError(404, f"нет такого эндпоинта: {method} {path}")


# ------------------------------------------------------------------ HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "listik/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # тише в консоли
        if self.path.startswith("/api/health"):
            return
        if self.server.quiet:  # type: ignore[attr-defined]
            return
        print(f"  {self.address_string()} {fmt % args}", flush=True)

    # --- helpers
    def _token(self) -> str:
        cfg = config_mod.load()
        return (cfg.get("auth") or {}).get("token", "")

    def _authed(self, query: dict) -> bool:
        token = self._token()
        if not token:
            return True
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:].strip() == token:
            return True
        if self.headers.get("X-Listik-Token") == token:
            return True
        return bool(query.get("token") and query["token"][0] == token)

    def _send(self, status: int, payload: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization,Content-Type,X-Listik-Token")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,PUT,DELETE,OPTIONS")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _json(self, status: int, data) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, message: str, code: str | None = None) -> None:
        body = {"ok": False, "error": message,
                "code": code or errors_mod.code_for_status(status)}
        self._json(status, body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, f"невалидный JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ApiError(400, "тело запроса должно быть объектом JSON")
        return data

    # --- методы
    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"", "text/plain")

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path

        if path == "/mcp":
            # Транспорт MCP — только POST, токен здесь не проверяется.
            payload = json.dumps({"ok": False, "error": "MCP: только POST"},
                                 ensure_ascii=False).encode("utf-8")
            return self._send(405, payload, "application/json; charset=utf-8", {"Allow": "POST"})

        if path.startswith("/.well-known/"):
            # MCP-клиент ищет тут OAuth-метаданные и принимает HTML SPA-фолбэка за них.
            return self._error(404, "нет такого эндпоинта")

        if path == "/api/stream":
            if not self._authed(query):
                return self._error(401, "нужен токен")
            return self._stream()

        if path.startswith("/api/"):
            # Проба живости отвечает и без токена: по ней CLI понимает, поднят ли сервер.
            # Но подробности о базе отдаются только авторизованному клиенту.
            authed = self._authed(query)
            if path != "/api/health" and not authed:
                return self._error(401, "нужен токен: Authorization: Bearer <token>")
            try:
                status, data = handle("GET", path, query, {}, authed=authed)
            except ApiError as exc:
                return self._error(exc.status, exc.message, exc.code)
            except Exception as exc:  # noqa: BLE001
                return self._error(500, f"{type(exc).__name__}: {exc}", errors_mod.INTERNAL)
            return self._json(status, {"ok": True, "data": data})

        return self._static(path, query)

    def do_POST(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/mcp":
            # У /mcp своя авторизация, поэтому до общей проверки токена.
            return self._mcp()
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authed(query):
            return self._error(401, "нужен токен")
        try:
            body = self._read_body()
            status, data = handle("POST", parsed.path, query, body, authed=True)
        except ApiError as exc:
            return self._error(exc.status, exc.message, exc.code)
        except Exception as exc:  # noqa: BLE001
            return self._error(500, f"{type(exc).__name__}: {exc}", errors_mod.INTERNAL)
        return self._json(status, {"ok": True, "data": data})

    def do_PATCH(self):  # noqa: N802
        self._write_method("PATCH")

    def do_PUT(self):  # noqa: N802
        self._write_method("PUT")

    def do_DELETE(self):  # noqa: N802
        self._write_method("DELETE")

    def _write_method(self, method: str) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/mcp":
            # PUT/PATCH/DELETE на /mcp — только 405, без проверки токена.
            payload = json.dumps({"ok": False, "error": "MCP: только POST"},
                                 ensure_ascii=False).encode("utf-8")
            return self._send(405, payload, "application/json; charset=utf-8", {"Allow": "POST"})
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authed(query):
            return self._error(401, "нужен токен")
        try:
            body = self._read_body()
            status, data = handle(method, parsed.path, query, body, authed=True)
        except ApiError as exc:
            return self._error(exc.status, exc.message, exc.code)
        except Exception as exc:  # noqa: BLE001
            return self._error(500, f"{type(exc).__name__}: {exc}", errors_mod.INTERNAL)
        return self._json(status, {"ok": True, "data": data})

    # --- MCP: минимальное подмножество транспорта Streamable HTTP
    def _mcp(self) -> None:
        """Один POST — одно JSON-RPC-сообщение, ответ обычным JSON, без SSE и сессий.

        Соединение на время запроса берётся из `get_conn()`, разбор сообщения — общий
        с stdio (`mcp.handle`). После успешного ответа пишущие инструменты шлют событие
        доске; на сам ответ событие не влияет и отправляется уже после него.
        """
        def plain(status: int, message: str, extra: dict | None = None) -> None:
            body = json.dumps({"ok": False, "error": message},
                              ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", extra)

        def rpc_error(status: int, code: int, message: str, rid) -> None:
            body = json.dumps({"jsonrpc": "2.0", "id": rid,
                               "error": {"code": code, "message": message}},
                              ensure_ascii=False).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8")

        # 1. Защита от DNS rebinding: браузер шлёт Origin, MCP-клиенты — нет.
        if self.headers.get("Origin"):
            self.close_connection = True
            return plain(403, "запросы с Origin к /mcp запрещены", {"Connection": "close"})

        # 2. Своя авторизация: ?token= в строке запроса здесь не принимается.
        token = self._token()
        if token:
            auth = self.headers.get("Authorization", "")
            bearer = auth.startswith("Bearer ") and auth[7:].strip() == token
            if not bearer and self.headers.get("X-Listik-Token") != token:
                self.close_connection = True
                return plain(401, "нужен токен: Authorization: Bearer <token>",
                             {"WWW-Authenticate": 'Bearer realm="listik"',
                              "Connection": "close"})

        # 3. Тело больше 5 МБ не читаем, а соединение закрываем: непрочитанное тело
        # испортило бы следующий запрос на keep-alive.
        length = as_int(self.headers.get("Content-Length"), 0) or 0
        if length > 5 * 1024 * 1024:
            self.close_connection = True
            return plain(413, "тело больше 5 МБ", {"Connection": "close"})

        # 4. Тело и разбор JSON.
        raw = self.rfile.read(length) if length > 0 else b""
        try:
            request = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return rpc_error(400, -32700, f"невалидный JSON: {exc}", None)

        # 5. Один POST — одно сообщение; пакеты и мусор не поддерживаются.
        rid = request.get("id") if isinstance(request, dict) else None
        if isinstance(request, list):
            return rpc_error(400, -32600, "пакетные запросы не поддерживаются", None)
        if not isinstance(request, dict) or not isinstance(request.get("method"), str):
            return rpc_error(400, -32600, "неверный запрос JSON-RPC", rid)

        # 6. Разбор сообщения — тот же, что у stdio.
        try:
            response = mcp.handle(request, conn=get_conn())
        except Exception as exc:  # noqa: BLE001
            return rpc_error(500, -32603, f"{type(exc).__name__}: {exc}", rid)

        # 7. Уведомление — отвечать нечем.
        if response is None:
            return self._send(202, b"", "application/json")

        # 8. Обычный ответ: JSON-RPC как есть, без обёртки ok/data.
        self._json(200, response)

        # 9. Событие доске — уже после ответа; любой сбой здесь на ответ не влияет.
        try:
            if request["method"] == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                result = response.get("result") or {}
                if name in mcp.WRITE_TOOLS and not result.get("isError"):
                    args = params.get("arguments") or {}
                    task_id = args.get("id")
                    if name == "listik_create":
                        task_id = json.loads(result["content"][0]["text"])["id"]
                    if task_id:
                        publish("task", {"id": task_id, "action": name})
        except Exception:  # noqa: BLE001
            pass

    # --- статика доски
    def _static(self, path: str, query: dict) -> None:
        """Отдаёт собранную доску (web/dist). Неизвестный путь — SPA-фолбэк на index.html."""
        dist = paths.WEB_DIR / "dist"
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = (dist / rel).resolve()
        if not str(target).startswith(str(dist.resolve())) or not target.is_file():
            target = dist / "index.html"
            if not target.is_file():
                return self._board_not_built()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        payload = target.read_bytes()
        cache = "no-store" if target.name == "index.html" else "max-age=60"
        self._send(200, payload, ctype, {"Cache-Control": cache})

    def _board_not_built(self) -> None:
        html = """<!doctype html><html lang="ru"><meta charset="utf-8">
<title>Listik — доска не собрана</title>
<style>body{font:15px/1.6 -apple-system,system-ui,sans-serif;max-width:720px;margin:12vh auto;padding:0 24px;color:#1c1c1e}
code{background:#f2f2f7;padding:2px 6px;border-radius:4px}pre{background:#f2f2f7;padding:12px;border-radius:8px;overflow:auto}</style>
<h1>API работает, доска ещё не собрана</h1>
<p>Сервер Listik отвечает (проверить: <code>/api/health</code>), но собранного фронта нет.</p>
<pre>cd ~/Projects/Listik/web
npm install
npm run build</pre>
<p>Для разработки доски можно поднять vite: <code>npm run dev</code> → <code>http://127.0.0.1:5173</code>
(запросы к <code>/api</code> проксируются на этот сервер).</p>
<p>Ссылка с токеном: <code>listik token</code></p></html>"""
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8", {"Cache-Control": "no-store"})

    # --- SSE
    def _stream(self) -> None:
        q: queue.Queue = queue.Queue(maxsize=100)
        with _subs_lock:
            _subs.append(q)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(b": listik stream\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=15)
                    self.wfile.write(f"data: {message}\n\n".encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with _subs_lock:
                if q in _subs:
                    _subs.remove(q)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, quiet: bool = False):
        super().__init__(addr, handler)
        self.quiet = quiet

    def process_request_thread(self, request, client_address):
        # Поток на соединение: его sqlite-соединение закрываем вместе с потоком.
        try:
            super().process_request_thread(request, client_address)
        finally:
            close_thread_conn()


def make_server(host: str, port: int, quiet: bool = False) -> Server:
    return Server((host, port), Handler, quiet=quiet)


def port_holder(port: int) -> tuple[int, str] | None:
    """Кто слушает порт: (pid, командная строка) по lsof, None — никто или lsof нет."""
    import subprocess
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                             capture_output=True, text=True, timeout=5).stdout.split()
        pid = int(out[0])
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    return pid, cmd


def is_listik_serve(cmd: str) -> bool:
    return "listik" in cmd and " serve" in cmd


def bind_or_explain(host: str, port: int, quiet: bool) -> Server:
    """make_server, но занятый порт — понятное сообщение и код 1 вместо трейсбека."""
    import errno
    try:
        return make_server(host, port, quiet=quiet)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        msg = f"порт {host}:{port} уже занят"
        holder = port_holder(port)
        if holder and is_listik_serve(holder[1]):
            msg += (f" другим сервером Listik (pid {holder[0]}, pid-файла нет)\n"
                    f"остановить: listik stop")
        elif holder:
            msg += f" процессом pid {holder[0]}: {holder[1]}\nосвободите порт или задайте другой: --port"
        else:
            msg += "\nосвободите порт или задайте другой: --port"
        raise SystemExit(msg) from None


def pid_file() -> Path:
    return paths.ROOT_DIR / "listik.pid"


def log_file() -> Path:
    return paths.LOG_PATH


def read_pid() -> int | None:
    try:
        pid = int(pid_file().read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except OSError:
        return None
    return pid


def daemonize() -> None:
    """Уйти в фон отдельной сессией, чтобы терминал не убивал сервер."""
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    log = open(log_file(), "a", buffering=1)  # noqa: SIM115 — живёт до конца процесса
    os.dup2(log.fileno(), sys.stdout.fileno())
    os.dup2(log.fileno(), sys.stderr.fileno())
    devnull = open(os.devnull, "rb")  # noqa: SIM115
    os.dup2(devnull.fileno(), sys.stdin.fileno())


def serve(host: str | None = None, port: int | None = None, quiet: bool = False,
          background: bool = False, no_embed: bool = False) -> None:
    # Сервер поднимают и фоновым запуском: без этого SIGHUP от закрытия терминала
    # убивает процесс, и команды агентов молча уходят в локальный режим.
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    except (AttributeError, ValueError):
        pass
    cfg = config_mod.load()
    token = (cfg.get("auth") or {}).get("token") or ""
    if not token:
        cfg, token = config_mod.ensure_token(cfg)
    host = host or cfg["server"]["host"]
    port = int(port or cfg["server"]["port"])

    if background:
        existing = read_pid()
        if existing:
            print(f"сервер уже запущен (pid {existing}); остановить: listik stop")
            return
        # Сокет биндим до fork: после daemonize уже нельзя заводить потоки и
        # открывать соединения в родителе — на macOS fork из многопоточного процесса падает.
        httpd = bind_or_explain(host, port, quiet=True)
        url = f"http://{host}:{port}/?token={token}"
        print(f"Listik в фоне: {url}")
        print(f"лог:  {log_file()}")
        print(f"pid:  {pid_file()}")
        daemonize()
        pid_file().write_text(str(os.getpid()))
        conn = get_conn()
        # После daemonize: сообщение об ошибке routes.json должно попасть в listik.log.
        routes_mod.init_at_startup()
        launcher_mod.recover(conn, notify=publish)
        if not no_embed:
            start_embed_worker()
        try:
            httpd.serve_forever()
        finally:
            httpd.server_close()
            try:
                pid_file().unlink()
            except OSError:
                pass
        return

    # Порт занимаем первым: при занятом порте не трогаем launcher и не заводим потоки.
    httpd = bind_or_explain(host, port, quiet=quiet)
    conn = get_conn()
    routes_mod.init_at_startup()
    launcher_mod.recover(conn, notify=publish)
    if not no_embed:
        start_embed_worker()
    url = f"http://{host}:{port}/?token={token}"
    print(f"Listik слушает http://{host}:{port}")
    print(f"доска:          {url}")
    print(f"база:           {paths.DB_PATH}")
    print(f"токен:          {token}")
    print("Ctrl+C — остановить")
    try:
        pid_file().write_text(str(os.getpid()))
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nостановлен")
    finally:
        httpd.server_close()
        try:
            pid_file().unlink()
        except OSError:
            pass
