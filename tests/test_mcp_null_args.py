"""`null` в числовых аргументах MCP-инструментов — то же, что ключ не передан (listik-ds5l).

MCP-клиенты присылают необязательный аргумент как `"limit": null`; раньше
`int(args.get("limit", N))` падал на нём с `TypeError`.
"""
from __future__ import annotations

import unittest
from unittest import mock

from listik import mcp
from listik import search as search_mod
from listik import store
from tests.helpers import TempDbTestCase


def _call(conn, name: str, args: dict):
    return mcp.call_tool(name, args, conn=conn)


class McpNullArgsTests(TempDbTestCase):
    def test_search_null_limit(self) -> None:
        store.create_task(self.conn, title="Зюзельбрюк проверки поиска", project="demo")
        args = {"query": "Зюзельбрюк", "mode": "text"}

        def hits(extra: dict) -> list:
            res = _call(self.conn, "listik_search", {**args, **extra})
            return [(r["id"], r["hits"]) for r in res["results"]]

        null = hits({"limit": None})
        self.assertTrue(null)
        self.assertEqual(null, hits({}))

    def test_list_null_limit(self) -> None:
        for i in range(3):
            store.create_task(self.conn, title=f"T{i}", project="demo")
        null = _call(self.conn, "listik_list", {"project": "demo", "limit": None})
        absent = _call(self.conn, "listik_list", {"project": "demo"})
        self.assertEqual(null["limit"], 50)
        ids = [t["id"] for t in null["tasks"]]
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids, [t["id"] for t in absent["tasks"]])

    def test_create_null_priority(self) -> None:
        res = _call(self.conn, "listik_create", {"title": "P", "project": "demo",
                                                 "priority": None})
        self.assertEqual(res["priority"], 2)
        self.assertEqual(store.get_task(self.conn, res["id"])["priority"], 2)

    def test_dep_tree_null_depth(self) -> None:
        a, b, c, d, e = (store.create_task(self.conn, title=t, project="demo")["id"]
                         for t in "ABCDE")
        for waiter, blocker in ((e, d), (d, c), (c, b), (b, a)):
            store.add_dep(self.conn, waiter, blocker, "blocks", created_by="me")

        def chain(extra: dict) -> list[str]:
            node = _call(self.conn, "listik_dep_tree", {"id": e, **extra})["waits_for"]
            ids = []
            while node:
                self.assertEqual(len(node), 1)
                ids.append(node[0]["id"])
                node = node[0]["up"]
            return ids

        self.assertEqual(chain({"depth": None}), [d, c, b])
        self.assertEqual(chain({}), [d, c, b])

    def test_memory_null_limit_both_branches(self) -> None:
        store.remember(self.conn, "первая заметка про кваргл", key="k1", project="demo")
        store.remember(self.conn, "вторая заметка", key="k2", project="demo")
        # Векторная ветка `search_memories` ходит в Ollama — в тесте она не нужна.
        with mock.patch.object(search_mod.embed, "ollama_embed", side_effect=RuntimeError):
            for extra in ({}, {"query": "кваргл"}):
                with self.subTest(args=extra):
                    null = _call(self.conn, "listik_memory", {**extra, "limit": None})["items"]
                    self.assertTrue(null)
                    self.assertEqual(null, _call(self.conn, "listik_memory", extra)["items"])

    def test_timeline_null_limit(self) -> None:
        store.create_task(self.conn, title="A", project="demo")
        store.create_task(self.conn, title="B", project="demo")
        null = _call(self.conn, "listik_timeline", {"limit": None})["items"]
        self.assertTrue(null)
        self.assertEqual(null, _call(self.conn, "listik_timeline", {})["items"])

    def test_deps_suggested_null_limit(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _call(self.conn, "listik_deps", {"id": b, "depends_on": a, "actor": "agent:codex"})
        null = _call(self.conn, "listik_deps_suggested", {"limit": None})["items"]
        self.assertTrue(null)
        self.assertEqual(null, _call(self.conn, "listik_deps_suggested", {})["items"])

    def test_handle_tools_call_with_null_not_error(self) -> None:
        store.create_task(self.conn, title="T", project="demo")
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_list",
                                      "arguments": {"project": "demo", "limit": None}}},
                          conn=self.conn)
        self.assertFalse(resp["result"].get("isError"), resp["result"])


if __name__ == "__main__":
    unittest.main()
