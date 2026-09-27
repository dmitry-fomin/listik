"""Набор типов задачи `store.ISSUE_TYPES` и его проверка на бэкенде (listik-7syz, порция a)."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from listik import client
from listik import db as db_mod
from listik import errors
from listik import mcp
from listik import server
from listik import store
from listik import voice
from tests.helpers import TempDbTestCase
from tests.test_cli_errors import CliErrorCase

EXPECTED = {
    "epic": "эпик", "task": "задача", "bug": "баг", "feature": "фича",
    "chore": "рутина", "decision": "решение", "question": "вопрос",
}


def _count(conn) -> int:
    return conn.execute("SELECT count(*) FROM tasks").fetchone()[0]


class IssueTypesTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        store.upsert_project(self.conn, "demo")

    def _http(self, method: str, path: str, body: dict):
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            return server.handle(method, path, {}, body, authed=True)

    def _legacy(self, title: str = "старая") -> str:
        tid = store.create_task(self.conn, title=title, project="demo")["id"]
        self.conn.execute("UPDATE tasks SET issue_type = 'docs' WHERE id = ?", (tid,))
        self.conn.commit()
        return tid

    def _snapshot(self, tid: str) -> tuple:
        t = self.conn.execute("SELECT issue_type FROM tasks WHERE id = ?", (tid,)).fetchone()[0]
        n = self.conn.execute("SELECT count(*) FROM events WHERE task_id = ?",
                              (tid,)).fetchone()[0]
        return t, n

    def test_constant(self) -> None:
        self.assertEqual(list(store.ISSUE_TYPES.items()), list(EXPECTED.items()))

    def test_create_rejects_unknown(self) -> None:
        for bad in ("docs", "", None):
            with self.assertRaises(errors.BadArgument):
                store.create_task(self.conn, title="t", project="demo", issue_type=bad)
        self.assertEqual(_count(self.conn), 0)

    def test_create_accepts_all(self) -> None:
        for kind in store.ISSUE_TYPES:
            task = store.create_task(self.conn, title=kind, project="demo", issue_type=kind)
            self.assertEqual(store.get_task(self.conn, task["id"])["issue_type"], kind)

    def test_create_allow_legacy(self) -> None:
        tid = store.create_task(self.conn, title="t", project="demo", issue_type="docs",
                                allow_legacy_type=True)["id"]
        self.assertEqual(self._snapshot(tid)[0], "docs")

    def test_http_post_rejects(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self._http("POST", "/api/tasks", {"title": "t", "project": "demo", "type": "docs"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertEqual(_count(self.conn), 0)

    def test_mcp_create_rejects(self) -> None:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_create",
                                      "arguments": {"title": "t", "project": "demo",
                                                    "type": "docs"}}}, conn=self.conn)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("bad_argument", resp["result"]["content"][0]["text"])
        self.assertEqual(_count(self.conn), 0)

    def test_update_rejects_unknown(self) -> None:
        tid = store.create_task(self.conn, title="t", project="demo")["id"]
        before = self._snapshot(tid)
        with self.assertRaises(errors.BadArgument):
            store.update_task(self.conn, tid, issue_type="docs")
        self.assertEqual(self._snapshot(tid), before)
        self.assertEqual(before[0], "task")

    def test_update_legacy_card(self) -> None:
        tid = self._legacy()
        store.update_task(self.conn, tid, title="новое")
        store.update_task(self.conn, tid, issue_type="docs")
        row = store.get_task(self.conn, tid)
        self.assertEqual((row["title"], row["issue_type"]), ("новое", "docs"))
        store.update_task(self.conn, tid, issue_type="bug")
        self.assertEqual(self._snapshot(tid)[0], "bug")

    def test_http_patch(self) -> None:
        tid = store.create_task(self.conn, title="t", project="demo")["id"]
        before = self._snapshot(tid)
        for body in ({"issue_type": "docs"}, {"allow_legacy_type": True, "issue_type": "docs"},
                     {"allow_legacy_type": True}):
            with self.assertRaises(server.ApiError) as ctx:
                self._http("PATCH", f"/api/tasks/{tid}", body)
            self.assertEqual(ctx.exception.status, 400, body)
            self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT, body)
        self.assertEqual(self._snapshot(tid), before)

    def test_update_fields_reject_bypass(self) -> None:
        tid = store.create_task(self.conn, title="t", project="demo")["id"]
        before = self._snapshot(tid)
        with mock.patch.object(db_mod, "init", return_value=self.conn):
            with self.assertRaises(errors.BadArgument):
                client.local_call("update", task_id=tid, issue_type="docs",
                                  allow_legacy_type=True)
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_update",
                                      "arguments": {"id": tid, "fields": {
                                          "issue_type": "docs",
                                          "allow_legacy_type": True}}}}, conn=self.conn)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("bad_argument", resp["result"]["content"][0]["text"])
        self.assertEqual(self._snapshot(tid), before)

    def test_meta(self) -> None:
        _, data = self._http("GET", "/api/meta", {})
        self.assertEqual(data["issue_types"], store.ISSUE_TYPES)
        self.assertEqual(list(data["issue_types"]), list(store.ISSUE_TYPES))
        with mock.patch.object(db_mod, "init", return_value=self.conn):
            local = client.local_call("meta")
        self.assertEqual(local["issue_types"], store.ISSUE_TYPES)

    def test_mcp_enum_and_voice(self) -> None:
        tools = {t["name"]: t for t in mcp.TOOLS}
        enum = tools["listik_create"]["inputSchema"]["properties"]["type"]["enum"]
        self.assertEqual(enum, list(store.ISSUE_TYPES))
        self.assertEqual(voice.TYPES, tuple(store.ISSUE_TYPES))
        self.assertIn('"type": "' + "|".join(store.ISSUE_TYPES) + ' или null"',
                      voice.DRAFT_SYSTEM_PROMPT)

    def test_sync_epic_restores_legacy_type(self) -> None:
        tid = self._legacy("родитель")
        child = store.create_task(self.conn, title="ребёнок", project="demo")["id"]
        store.add_dep(self.conn, child, tid, "parent-child")
        self.assertEqual(self._snapshot(tid)[0], "epic")
        store.remove_dep(self.conn, child, tid, "parent-child")
        self.assertEqual(self._snapshot(tid)[0], "docs")


class IssueTypeCliTests(CliErrorCase):
    def test_new_rejects_unknown_type(self) -> None:
        p = self.run_cli("new", "t", "-t", "docs", "-p", "demo")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(_count(self.conn), 0)

    def test_new_accepts_feature(self) -> None:
        p = self.run_cli("new", "t", "-t", "feature", "-p", "demo", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        tid = json.loads(p.stdout)["id"]
        row = self.conn.execute("SELECT issue_type FROM tasks WHERE id = ?", (tid,)).fetchone()
        self.assertEqual(row[0], "feature")


if __name__ == "__main__":
    unittest.main()
