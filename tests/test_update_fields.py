"""Неизвестный ключ правки карточки и харнесс локальных `new`/`set` (listik-3urk).

Ключ правки вне `store.UPDATABLE` (+ алиас `route`) — `bad_argument` во всех путях:
store, HTTP, MCP; отказ атомарный. Без сервера `new`/`set` пишут харнесс в события.
"""
from __future__ import annotations

import json
from unittest import mock

from listik import errors, mcp, server, store
from tests.test_cli_errors import CliErrorCase


class StoreTests(CliErrorCase):
    def setUp(self) -> None:
        super().setUp()
        self.tid = store.create_task(self.conn, title="проба", project="demo")["id"]

    def test_unknown_key_rejected(self) -> None:
        before = store.get_task(self.conn, self.tid)
        with self.assertRaises(errors.BadArgument) as cm:
            store.update_task(self.conn, self.tid, prority=1)
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("неизвестное поле задачи:"), msg)
        for word in ("prority", "priority", "route"):
            self.assertIn(word, msg)
        after = store.get_task(self.conn, self.tid)
        self.assertEqual(after["priority"], before["priority"])
        self.assertEqual(after["updated_at"], before["updated_at"])

    def test_refusal_is_atomic(self) -> None:
        with self.assertRaises(errors.BadArgument):
            store.update_task(self.conn, self.tid, status="in_progress", prority=1)
        self.assertEqual(store.get_task(self.conn, self.tid)["status"], "open")
        n = self.conn.execute("SELECT COUNT(*) FROM events WHERE task_id=? AND kind='status'",
                              (self.tid,)).fetchone()[0]
        self.assertEqual(n, 0)


class ServerTests(CliErrorCase):
    def setUp(self) -> None:
        super().setUp()
        conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        conn_patch.start()
        self.addCleanup(conn_patch.stop)
        publish_patch = mock.patch.object(server, "publish")
        self.publish = publish_patch.start()
        self.addCleanup(publish_patch.stop)
        self.tid = store.create_task(self.conn, title="старый", project="demo")["id"]

    def patch(self, body: dict):
        return server.handle("PATCH", f"/api/tasks/{self.tid}", {}, body, authed=True)

    def assert_bad_argument(self, body: dict) -> None:
        with self.assertRaises(server.ApiError) as cm:
            self.patch(body)
        self.assertEqual(cm.exception.status, 400, body)
        self.assertEqual(cm.exception.code, "bad_argument", body)

    def test_unknown_key(self) -> None:
        self.assert_bad_argument({"prority": 1})
        self.assertEqual(store.get_task(self.conn, self.tid)["priority"], 2)
        self.publish.assert_not_called()

    def test_as_owner_in_body(self) -> None:
        self.assert_bad_argument({"as_owner": "x", "title": "новый"})
        self.assertEqual(store.get_task(self.conn, self.tid)["title"], "старый")

    def test_service_keys_pass(self) -> None:
        status, task = self.patch({"priority": 1, "actor": "agent:claude",
                                   "harness": "claude", "note": "проба"})
        self.assertEqual(status, 200)
        self.assertEqual(task["priority"], 1)


class McpTests(CliErrorCase):
    def setUp(self) -> None:
        super().setUp()
        self.tid = store.create_task(self.conn, title="проба", project="demo")["id"]

    def test_unknown_and_service_keys_in_fields(self) -> None:
        for fields in ({"prority": 1}, {"note": "x"}):
            with self.assertRaises(errors.BadArgument, msg=fields):
                mcp.call_tool("listik_update", {"id": self.tid, "fields": fields},
                              conn=self.conn)


class LocalHarnessTests(CliErrorCase):
    def harness_of(self, tid: str, kind: str) -> list:
        return [r[0] for r in self.conn.execute(
            "SELECT harness FROM events WHERE task_id=? AND kind=?", (tid, kind))]

    def test_new_writes_harness(self) -> None:
        p = self.run_cli("new", "Проба", "-p", "demo", "--harness", "claude", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        tid = json.loads(p.stdout)["id"]
        self.assertEqual(self.harness_of(tid, "created"), ["claude"])

    def test_set_writes_harness(self) -> None:
        tid = store.create_task(self.conn, title="проба", project="demo")["id"]
        p = self.run_cli("set", tid, "status=in_progress", "--harness", "claude", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.harness_of(tid, "status"), ["claude"])
