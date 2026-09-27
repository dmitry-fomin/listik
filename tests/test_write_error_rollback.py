"""Упавшая запись откатывается во всех трёх точках входа (listik-mqr6).

Функции store коммитят только на успешном пути. `add_dep` с циклом жёстких связей
успевает записать алиас актора (`actors.remember`) и только потом бросает
`ValueError`. Без отката эта запись держит блокировку записи SQLite, а на долгоживущем
соединении (MCP stdio, поток HTTP keep-alive) уходит в базу со следующим успешным
вызовом. Здесь проверяется, что `client.local_call`, `mcp.handle` и сервер откатывают
транзакцию упавшего вызова, а исходная ошибка не подменяется.
"""
from __future__ import annotations

import gc
import http.client
import json
import sqlite3
from unittest import mock

from listik import client, fence, mcp, store
from listik import db as db_mod
from tests.helpers import TempDbTestCase
from tests.test_local_bypass_warning import LocalBypassWarningCase

PROBE = "rollback-probe"


def make_cycle(conn) -> tuple[str, str, str]:
    """A, B, C и жёсткие B → C, C → A: связь A → B с confirm замыкает цикл."""
    a, b, c = (store.create_task(conn, title=t, project="demo")["id"] for t in "ABC")
    store.add_dep(conn, b, c, "blocks", created_by="me")
    store.add_dep(conn, c, a, "blocks", created_by="me")
    return a, b, c


def probe_aliases(conn) -> int:
    return conn.execute("SELECT count(*) FROM actor_aliases WHERE raw = ?", (PROBE,)).fetchone()[0]


class CycleFixture:
    """Фикстура и «второй писатель» для обоих видов базовых классов."""

    def second_writer_commits(self, task_id: str) -> None:
        other = sqlite3.connect(self.db_path, timeout=1)
        try:
            other.execute("UPDATE tasks SET updated_at = updated_at WHERE id = ?", (task_id,))
            other.commit()
        except sqlite3.OperationalError as exc:
            self.fail(f"второй писатель не смог записать: {exc}")
        finally:
            other.close()


class LocalCallRollbackTests(CycleFixture, TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a, self.b, self.c = make_cycle(self.conn)
        self.assertEqual(probe_aliases(self.conn), 0)
        self.opened: list[sqlite3.Connection] = []
        self.addCleanup(lambda: [c.close() for c in self.opened])

    def open_conn(self, *_args, **_kwargs):
        conn = db_mod.connect(self.db_path)
        self.opened.append(conn)
        return conn

    def no_gc(self) -> None:
        # Сборщик мог бы закрыть брошенное соединение сам, и тест прошёл бы без правки.
        gc.disable()
        self.addCleanup(gc.enable)

    def test_failed_op_releases_write_lock(self):
        self.no_gc()
        with mock.patch.object(client.db_mod, "init", side_effect=self.open_conn):
            with self.assertRaises(ValueError) as ctx:
                client.local_call("dep_add", issue_id=self.a, depends_on=self.b,
                                  dep_type="blocks", created_by=PROBE, confirm=True)
        self.assertIn("цикл", str(ctx.exception))
        self.second_writer_commits(self.a)
        [conn] = self.opened
        self.assertEqual(probe_aliases(conn), 0)
        self.assertFalse(conn.in_transaction)

    def test_rollback_error_does_not_mask_original(self):
        def boom(conn, *_args, **_kwargs):
            conn.close()
            raise ValueError("boom")

        with mock.patch.object(client.db_mod, "init", side_effect=self.open_conn), \
                mock.patch.object(store, "add_dep", side_effect=boom):
            with self.assertRaises(ValueError) as ctx:
                client.local_call("dep_add", issue_id=self.a, depends_on=self.b,
                                  dep_type="blocks", created_by=PROBE, confirm=True)
        self.assertEqual(str(ctx.exception), "boom")

    def test_guard_error_rolls_back(self):
        self.no_gc()

        def guard(conn, *_args, **_kwargs):
            conn.execute("INSERT INTO actor_aliases(raw, actor) VALUES(?, ?)", (PROBE, PROBE))
            raise RuntimeError("guard boom")

        token = fence.Token(task_id=self.a, generation=1, dispatch_id="x")
        with mock.patch.object(client.db_mod, "init", side_effect=self.open_conn), \
                mock.patch.object(client.fence_mod, "guard", side_effect=guard):
            with self.assertRaises(RuntimeError) as ctx:
                client.local_call("comment", task_id=self.a, text="после отказа", fence=token)
        self.assertEqual(str(ctx.exception), "guard boom")
        self.second_writer_commits(self.a)
        [conn] = self.opened
        self.assertEqual(probe_aliases(conn), 0)


class McpRollbackTests(CycleFixture, TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a, self.b, self.c = make_cycle(self.conn)

    def call(self, conn, name: str, arguments: dict) -> dict:
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": name, "arguments": arguments}}
        return mcp.handle(request, conn=conn, owner=None, fence=None)["result"]

    def test_failed_tool_call_rolls_back(self):
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        result = self.call(conn, "listik_deps", {"id": self.a, "depends_on": self.b,
                                                 "dep_type": "blocks", "actor": PROBE,
                                                 "confirm": True})
        self.assertTrue(result.get("isError"))
        self.assertIn("цикл", result["content"][0]["text"])
        self.assertFalse(conn.in_transaction)
        self.second_writer_commits(self.a)
        result = self.call(conn, "listik_comment", {"id": self.c, "text": "после отказа",
                                                    "actor": "me"})
        self.assertFalse(result.get("isError"), result)
        self.assertEqual(probe_aliases(conn), 0)


class ServerRollbackTests(CycleFixture, LocalBypassWarningCase):
    def setUp(self) -> None:
        super().setUp()
        self.a, self.b, self.c = make_cycle(self.conn)

    def test_failed_request_rolls_back_keepalive_connection(self):
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        web = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            web.request("POST", f"/api/tasks/{self.a}/deps", headers=headers,
                         body=json.dumps({"depends_on": self.b, "dep_type": "blocks",
                                          "actor": PROBE, "confirm": True}))
            response = web.getresponse()
            body = json.loads(response.read())
            self.assertGreaterEqual(response.status, 400)
            self.assertFalse(body["ok"])
            self.assertFalse(response.will_close, "keep-alive потерян: сценарий ничего не проверит")

            self.second_writer_commits(self.a)

            web.request("POST", f"/api/tasks/{self.c}/comment", headers=headers,
                         body=json.dumps({"text": "после отказа", "author": "me"}))
            response = web.getresponse()
            response.read()
            self.assertEqual(response.status, 200)
        finally:
            web.close()
        other = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(probe_aliases(other), 0)
        finally:
            other.close()
