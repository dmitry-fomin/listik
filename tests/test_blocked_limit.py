"""`blocked` с `limit=0` — без ограничения на всех путях (listik-r3ak), и MCP `ready`/`blocked`
принимают `"limit": null` как отсутствие ключа (listik-y6lq).
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from listik import client, deps, mcp, server, store
from tests.helpers import TempDbTestCase
from tests.test_local_bypass_warning import LocalBypassWarningCase

BLOCKED = 105  # больше порога HTTP (100), иначе 0 → 100 не заметить


def _seed(conn) -> tuple[set[str], str]:
    """Открытый блокер X проекта `demo` и задачи W0…W104, каждая ждёт X."""
    x = store.create_task(conn, title="X", project="demo")["id"]
    waiting = [store.create_task(conn, title=f"W{i}", project="demo")["id"] for i in range(BLOCKED)]
    for w in waiting:
        store.add_dep(conn, w, x, "blocks", created_by="me")
    return set(waiting), x


def _ids(res: dict) -> list[str]:
    return [t["id"] for t in res["tasks"]]


class BlockedLimitPathsTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.waiting, self.x = _seed(self.conn)

    def _http(self, query: dict) -> tuple[int, dict]:
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            return server.handle("GET", "/api/blocked", query, {}, authed=True)

    def test_limit_zero_same_on_all_paths(self) -> None:
        status, http = self._http({"project": ["demo"], "limit": ["0"]})
        self.assertEqual(status, 200)
        with mock.patch.object(client.db_mod, "init", return_value=self.conn):
            local = client.local_call("blocked", project="demo", limit=0)
        via_mcp = mcp.call_tool("listik_blocked", {"project": "demo", "limit": 0}, conn=self.conn)
        direct = {t["id"] for t in deps.blocked_tasks(self.conn, project="demo", limit=0)}

        for name, res in (("http", http), ("local", local), ("mcp", via_mcp)):
            with self.subTest(path=name):
                got = _ids(res)
                self.assertEqual(len(got), BLOCKED)
                self.assertEqual(set(got), self.waiting)
                self.assertNotIn(self.x, got)
                self.assertEqual(set(got), direct)

    def test_http_other_limits(self) -> None:
        for query, want in (({"project": ["demo"]}, 100),
                            ({"project": ["demo"], "limit": ["abc"]}, 100),
                            ({"project": ["demo"], "limit": ["3"]}, 3)):
            with self.subTest(query=query):
                status, res = self._http(query)
                self.assertEqual(status, 200)
                got = _ids(res)
                self.assertEqual(len(got), want)
                self.assertLessEqual(set(got), self.waiting)

    def test_mcp_null_limit_as_absent(self) -> None:
        null = _ids(mcp.call_tool("listik_blocked", {"project": "demo", "limit": None},
                                  conn=self.conn))
        absent = _ids(mcp.call_tool("listik_blocked", {"project": "demo"}, conn=self.conn))
        self.assertEqual(len(null), 50)
        self.assertEqual(len(absent), 50)
        self.assertLessEqual(set(null), self.waiting)
        self.assertEqual(set(null), set(absent))


class BlockedLimitCliTests(LocalBypassWarningCase):
    def test_cli_n_zero_both_paths(self) -> None:
        waiting, x = _seed(self.conn)
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("blocked", "-n", "0", "-p", "demo", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                got = _ids(json.loads(p.stdout))
                self.assertEqual(len(got), BLOCKED)
                self.assertEqual(set(got), waiting)
                self.assertNotIn(x, got)


if __name__ == "__main__":
    unittest.main()
