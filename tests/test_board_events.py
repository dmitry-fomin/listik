"""События доске: какой путь записи шлёт SSE-кадр, а какой — нет (listik-1p86).

Доска обновляется без перезагрузки подпиской на `GET /api/stream`; кадры рассылает
`server.publish` из HTTP-обработчика (`server.handle`) и из транспорта `/mcp`
(`Handler._mcp`). Здесь закреплён контракт рассылки:

* каждая запись по HTTP (задача, проект, документ, зависимости) шлёт кадр;
* чтения не шлют ничего — иначе `listik dep tree` или открытие карточки будило бы
  все открытые доски (единственный найденный перекос рассылки, listik-1p86);
* `mcp.WRITE_TOOLS` не отстаёт от пишущих инструментов MCP: инструмент, который
  пишет, но не попал в список, молча не будил бы доску.

Пути мимо сервера (`listik --local`, MCP stdio, импортёры) событий не шлют по
устройству: `publish` живёт в процессе сервера. Это описано в отчёте задачи, а не
закреплено тестом — тест «события нет» запрещал бы починку.
"""
from __future__ import annotations

import ast
import json
import pathlib
import queue
import threading
import unittest

from listik import mcp, paths, server, store
from tests.helpers import TempDbTestCase

#: Вызовы store/documents, после которых состояние доски меняется.
MUTATING_CALLS = frozenset({
    "create_task", "update_task", "claim", "heartbeat", "next_stage", "add_comment",
    "set_needs_owner", "add_dep", "remove_dep", "put_document", "remember",
})

#: Пишущие инструменты MCP, которым событие доске не нужно: память на доске не видна.
NOT_ON_BOARD = frozenset({"listik_remember"})


class BoardEventsCase(TempDbTestCase):
    """Временная база + подписка на `publish` вместо живого SSE-соединения."""

    def setUp(self) -> None:
        super().setUp()
        self._saved = (paths.DB_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        server._conn_made = False
        server._conn_local = threading.local()
        self.events: queue.Queue = queue.Queue()
        with server._subs_lock:
            server._subs.append(self.events)

    def tearDown(self) -> None:
        with server._subs_lock:
            if self.events in server._subs:
                server._subs.remove(self.events)
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        paths.DB_PATH, server._conn_made, server._conn_local = self._saved
        super().tearDown()

    # --- вызовы и события
    def call(self, method: str, path: str, body: dict | None = None,
             query: dict | None = None):
        return server.handle(method, path, query or {}, body or {}, authed=True)

    def drain(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return

    def expect_event(self, method: str, path: str, *, action: str, kind: str = "task",
                     task_id: str | None = None, body: dict | None = None,
                     query: dict | None = None):
        self.drain()
        status, out = self.call(method, path, body, query)
        self.assertIn(status, (200, 201), out)
        try:
            event = json.loads(self.events.get(timeout=2))
        except queue.Empty:  # pragma: no cover - текст для диагноза
            self.fail(f"{method} {path} не отправил событие доске")
        self.assertEqual(event["kind"], kind)
        self.assertEqual(event["payload"].get("action"), action)
        if task_id is not None:
            self.assertEqual(event["payload"].get("id"), task_id)
        self.assertTrue(event.get("at"))
        return out

    def assert_silent(self, method: str, path: str, body: dict | None = None,
                      query: dict | None = None) -> None:
        self.drain()
        status, out = self.call(method, path, body, query)
        self.assertIn(status, (200, 201), out)
        self._assert_no_event(method, path)

    def assert_silent_error(self, method: str, path: str, body: dict | None = None,
                            query: dict | None = None) -> None:
        """Отказ операции — тоже не событие."""
        self.drain()
        with self.assertRaises(server.ApiError):
            self.call(method, path, body, query)
        self._assert_no_event(method, path)

    def _assert_no_event(self, method: str, path: str) -> None:
        try:
            event = json.loads(self.events.get_nowait())
        except queue.Empty:
            return
        self.fail(f"{method} {path} — состояние не менялось, а разослало {event!r}")


class TaskWriteEvents(BoardEventsCase):
    """Записи задачи по HTTP шлют кадр с её id."""

    def test_create_publishes_created(self) -> None:
        self.drain()
        status, task = self.call("POST", "/api/tasks", {"title": "Новая"})
        self.assertEqual(status, 201)
        event = json.loads(self.events.get(timeout=2))
        self.assertEqual(event["kind"], "task")
        self.assertEqual(event["payload"], {"id": task["id"], "action": "created"})

    def test_action_writes_publish(self) -> None:
        cases = (
            ("claim", {"holder": "dsh"}),
            ("heartbeat", {"holder": "dsh", "note": "работаю"}),
            ("stage", {"holder": "dsh"}),
            ("comment", {"text": "привет", "author": "agent:dsh"}),
            ("needs-owner", {"value": True, "note": "вопрос", "actor": "agent:dsh"}),
            ("release", {"actor": "agent:dsh"}),
            ("done", {"actor": "agent:dsh", "result": "готово"}),
        )
        for action, body in cases:
            with self.subTest(action=action):
                task_id = store.create_task(self.conn, title=f"Задача {action}")["id"]
                self.expect_event("POST", f"/api/tasks/{task_id}/{action}", body=body,
                                  action=action, task_id=task_id)

    def test_patch_and_delete_publish(self) -> None:
        task_id = store.create_task(self.conn, title="Правка")["id"]
        self.expect_event("PATCH", f"/api/tasks/{task_id}", body={"priority": 1},
                          action="updated", task_id=task_id)
        self.expect_event("DELETE", f"/api/tasks/{task_id}", action="deleted", task_id=task_id)

    def test_deps_add_and_remove_publish(self) -> None:
        first = store.create_task(self.conn, title="A")["id"]
        second = store.create_task(self.conn, title="B")["id"]
        self.expect_event("POST", f"/api/tasks/{first}/deps",
                          body={"depends_on": second, "dep_type": "relates-to"},
                          action="deps", task_id=first)
        self.expect_event("DELETE", f"/api/tasks/{first}/deps/{second}",
                          query={"dep_type": "relates-to"}, action="deps", task_id=first)

    def test_document_put_publishes(self) -> None:
        task_id = store.create_task(self.conn, title="Документ")["id"]
        self.expect_event("PUT", f"/api/tasks/{task_id}/documents/spec",
                          body={"content": "# ТЗ"}, action="document", task_id=task_id)


class ProjectWriteEvents(BoardEventsCase):
    """Проекты — тоже доска: добавление, правка и удаление шлют кадр."""

    def test_project_writes_publish(self) -> None:
        self.expect_event("POST", "/api/projects", body={"slug": "demo", "title": "Demo"},
                          action="created", kind="project")
        self.expect_event("PATCH", "/api/projects/demo", body={"title": "Demo 2"},
                          action="updated", kind="project")
        self.expect_event("DELETE", "/api/projects/demo", body={"force": 1},
                          action="removed", kind="project")


class ReadPathsAreSilent(BoardEventsCase):
    """Чтения не рассылают кадров, даже когда идут POST-ом."""

    def setUp(self) -> None:
        super().setUp()
        self.dep = store.create_task(self.conn, title="Зависимость")["id"]
        self.task = store.create_task(self.conn, title="Задача")["id"]
        status, _ = self.call("POST", f"/api/tasks/{self.task}/deps",
                              {"depends_on": self.dep, "dep_type": "relates-to"})
        self.assertEqual(status, 200)

    def test_reads_are_silent(self) -> None:
        cases = (
            ("GET", "/api/tasks", None, None),
            ("GET", "/api/board", None, None),
            ("GET", "/api/stats", None, None),
            ("GET", f"/api/tasks/{self.task}", None, None),
            ("GET", f"/api/tasks/{self.task}/context", None, {"stage": "s1-spec"}),
            ("GET", f"/api/tasks/{self.task}/deps", None, None),
            ("POST", f"/api/tasks/{self.task}/deps", {"depth": 2}, None),
            ("POST", f"/api/tasks/{self.task}/ready", None, None),
            ("POST", f"/api/tasks/{self.task}/mentions", None, None),
            ("POST", f"/api/tasks/{self.task}/deps", None, None),
        )
        for method, path, body, query in cases:
            with self.subTest(f"{method} {path}"):
                self.assert_silent(method, path, body, query)

    def test_failed_write_is_silent(self) -> None:
        """Отказ записи (claim без держателя) не будит доску."""
        self.assert_silent_error("POST", f"/api/tasks/{self.task}/claim", {})
        self.assert_silent_error("POST", "/api/tasks/нет-такой/comment", {"text": "x"})


class WriteToolsCoverage(unittest.TestCase):
    """`WRITE_TOOLS` не отстаёт от пишущих инструментов MCP."""

    def test_write_tools_exist_in_tools(self) -> None:
        names = {tool["name"] for tool in mcp.TOOLS}
        self.assertTrue(mcp.WRITE_TOOLS <= names, sorted(mcp.WRITE_TOOLS - names))

    def test_every_mutating_tool_is_registered(self) -> None:
        """Разбор `call_tool`: инструмент, зовущий пишущую функцию, обязан быть в списке."""
        tree = ast.parse(pathlib.Path(mcp.__file__).read_text(encoding="utf-8"))
        func = next(node for node in ast.walk(tree)
                    if isinstance(node, ast.FunctionDef) and node.name == "call_tool")
        mutating: set[str] = set()
        for statement in func.body:
            name = self._tool_name(statement)
            if name is None:
                continue
            for call in ast.walk(statement):
                if isinstance(call, ast.Call) and self._func_name(call.func) in MUTATING_CALLS:
                    mutating.add(name)
                    break
        # Страховка от переписывания call_tool: пустой разбор — не «всё хорошо»,
        # а сломанный тест.
        self.assertGreaterEqual(len(mutating), 10, sorted(mutating))
        self.assertEqual(mutating - mcp.WRITE_TOOLS, set(NOT_ON_BOARD))

    @staticmethod
    def _tool_name(statement: ast.stmt) -> str | None:
        """Имя инструмента из `if name == "listik_x": ...`."""
        test = getattr(statement, "test", None)
        if not isinstance(statement, ast.If) or not isinstance(test, ast.Compare):
            return None
        if not (isinstance(test.left, ast.Name) and test.left.id == "name"):
            return None
        if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
            return None
        value = test.comparators[0]
        return value.value if isinstance(value, ast.Constant) else None

    @staticmethod
    def _func_name(func: ast.expr) -> str:
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return ""
