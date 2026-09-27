"""`ready` с `limit=0` — без ограничения на всех путях (listik-tphb).

`deps.ready_tasks` понимает `limit=0` как «без ограничения». HTTP `/api/ready` раньше
превращал 0 в 50, а локальный путь и MCP передавали 0 как есть — ответ зависел от пути.
Теперь `limit=0` одинаков везде; без параметра HTTP по-прежнему отдаёт 50.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from listik import client, deps, mcp, server, store
from tests.helpers import TempDbTestCase
from tests.test_local_bypass_warning import LocalBypassWarningCase

FREE = 55  # больше порога HTTP (50), иначе 0 → 50 не заметить


def _seed(conn) -> tuple[set[str], str]:
    """55 свободных задач `demo` F0…F54 и задача W, которая ждёт F0."""
    free = [store.create_task(conn, title=f"F{i}", project="demo")["id"] for i in range(FREE)]
    w = store.create_task(conn, title="W", project="demo")["id"]
    store.add_dep(conn, w, free[0], "blocks", created_by="me")
    return set(free), w


def _ids(res: dict) -> list[str]:
    return [t["id"] for t in res["tasks"]]


class ReadyLimitPathsTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.free, self.w = _seed(self.conn)

    def _http(self, query: dict) -> tuple[int, dict]:
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            return server.handle("GET", "/api/ready", query, {}, authed=True)

    def test_limit_zero_same_on_all_paths(self) -> None:
        status, http = self._http({"project": ["demo"], "limit": ["0"]})
        self.assertEqual(status, 200)
        with mock.patch.object(client.db_mod, "init", return_value=self.conn):
            local = client.local_call("ready", project="demo", limit=0)
        via_mcp = mcp.call_tool("listik_ready", {"project": "demo", "limit": 0}, conn=self.conn)
        direct = {t["id"] for t in deps.ready_tasks(self.conn, project="demo", limit=0)}

        for name, res in (("http", http), ("local", local), ("mcp", via_mcp)):
            with self.subTest(path=name):
                got = _ids(res)
                self.assertEqual(len(got), FREE)
                self.assertEqual(set(got), self.free)
                self.assertNotIn(self.w, got)
                self.assertEqual(set(got), direct)

    def test_http_other_limits(self) -> None:
        for query, want in (({"project": ["demo"]}, 50),
                            ({"project": ["demo"], "limit": ["abc"]}, 50),
                            ({"project": ["demo"], "limit": ["3"]}, 3)):
            with self.subTest(query=query):
                status, res = self._http(query)
                self.assertEqual(status, 200)
                got = _ids(res)
                self.assertEqual(len(got), want)
                self.assertLessEqual(set(got), self.free)

    def test_mcp_null_limit_as_absent(self) -> None:
        null = _ids(mcp.call_tool("listik_ready", {"project": "demo", "limit": None},
                                  conn=self.conn))
        absent = _ids(mcp.call_tool("listik_ready", {"project": "demo"}, conn=self.conn))
        self.assertEqual(len(null), 30)
        self.assertEqual(len(absent), 30)
        self.assertLessEqual(set(null), self.free)
        self.assertNotIn(self.w, null)
        self.assertEqual(set(null), set(absent))


class ReadyLimitCliTests(LocalBypassWarningCase):
    def test_cli_n_zero_both_paths(self) -> None:
        free, w = _seed(self.conn)
        for local in (False, True):
            with self.subTest(local=local):
                p = self.run_cli("ready", "-n", "0", "-p", "demo", "--json", local=local)
                self.assertEqual(p.returncode, 0, p.stderr)
                got = _ids(json.loads(p.stdout))
                self.assertEqual(len(got), FREE)
                self.assertEqual(set(got), free)
                self.assertNotIn(w, got)


if __name__ == "__main__":
    unittest.main()
