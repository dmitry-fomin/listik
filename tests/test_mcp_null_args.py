"""`null` в аргументах MCP-инструментов — то же, что ключ не передан.

Числовые читает `_int_arg` (listik-ds5l); остальные ключи верхнего уровня со
значением `None` отбрасывает нормализация в начале `mcp.call_tool`
(listik-ngm2). Вложенные значения (внутри `fields` у `listik_update`) доходят
до store как есть — их смысл определяет store, а не транспорт.
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

    def test_needs_owner_null_value(self) -> None:
        task = store.create_task(self.conn, title="T", project="demo")
        _call(self.conn, "listik_needs_owner",
              {"id": task["id"], "value": None, "text": "вопрос"})
        self.assertTrue(store.get_task(self.conn, task["id"])["needs_owner"])

    def test_list_null_order(self) -> None:
        for i in range(3):
            store.create_task(self.conn, title=f"T{i}", project="demo")
        null = _call(self.conn, "listik_list", {"project": "demo", "order": None})
        absent = _call(self.conn, "listik_list", {"project": "demo"})
        self.assertEqual([t["id"] for t in null["tasks"]],
                         [t["id"] for t in absent["tasks"]])

    def test_create_null_optionals(self) -> None:
        res = _call(self.conn, "listik_create",
                    {"title": "T", "project": "demo", "type": None,
                     "description": None, "acceptance": None})
        self.assertEqual(res["issue_type"], "task")
        self.assertEqual(res["description"], "")
        self.assertEqual(res["acceptance"], "")

    def test_comment_null_kind(self) -> None:
        task = store.create_task(self.conn, title="T", project="demo")
        res = _call(self.conn, "listik_comment",
                    {"id": task["id"], "text": "привет", "kind": None})
        self.assertEqual(res["kind"], "comment")

    def test_deps_null_dep_type(self) -> None:
        a, b, c, d = (store.create_task(self.conn, title=t, project="demo")["id"]
                      for t in "ABCD")
        _call(self.conn, "listik_deps",
              {"id": a, "depends_on": b, "dep_type": None, "actor": "agent:test"})
        _call(self.conn, "listik_deps",
              {"id": c, "depends_on": d, "actor": "agent:test"})

        def dep_type(issue_id: str, depends_on: str) -> str:
            return self.conn.execute(
                "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?",
                (issue_id, depends_on)).fetchone()["dep_type"]

        self.assertEqual(dep_type(a, b), dep_type(c, d))

    def test_board_null_group_by(self) -> None:
        store.create_task(self.conn, title="T", project="demo")
        null = _call(self.conn, "listik_board", {"group_by": None})
        absent = _call(self.conn, "listik_board", {})
        del null["generated_at"], absent["generated_at"]
        self.assertEqual(null, absent)

    def test_handle_null_required_arg_same_as_missing(self) -> None:
        def call(arguments: dict) -> dict:
            return mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": "listik_create",
                                          "arguments": arguments}}, conn=self.conn)

        null = call({"project": "demo", "title": None})
        absent = call({"project": "demo"})
        self.assertTrue(null["result"].get("isError"), null)
        self.assertEqual(null["result"]["content"][0]["text"],
                         absent["result"]["content"][0]["text"])

    def test_falsy_args_not_dropped(self) -> None:
        task = store.create_task(self.conn, title="T", project="demo")
        store.set_needs_owner(self.conn, task["id"], value=True)
        _call(self.conn, "listik_needs_owner", {"id": task["id"], "value": False})
        self.assertFalse(store.get_task(self.conn, task["id"])["needs_owner"])
        res = _call(self.conn, "listik_create",
                    {"title": "T2", "project": "demo", "description": ""})
        self.assertEqual(res["description"], "")

    def test_search_null_mode(self) -> None:
        store.create_task(self.conn, title="Зюзельбрюк проверки поиска", project="demo")
        with mock.patch.object(search_mod.embed, "ollama_embed", side_effect=RuntimeError):
            null = _call(self.conn, "listik_search", {"query": "Зюзельбрюк", "mode": None})
            absent = _call(self.conn, "listik_search", {"query": "Зюзельбрюк"})
        self.assertTrue(null["results"])
        self.assertEqual([(r["id"], r["hits"]) for r in null["results"]],
                         [(r["id"], r["hits"]) for r in absent["results"]])

    def test_update_nested_null_fields(self) -> None:
        task = store.create_task(self.conn, title="T", project="demo",
                                 spec_path="/x/spec.md")
        with mock.patch.object(store, "update_task", wraps=store.update_task) as spy:
            _call(self.conn, "listik_update",
                  {"id": task["id"], "fields": {"spec_path": None}})
        self.assertIsNone(spy.call_args.kwargs["spec_path"])
        self.assertEqual(store.get_task(self.conn, task["id"])["spec_path"], "/x/spec.md")

    def test_args_dict_not_mutated(self) -> None:
        task = store.create_task(self.conn, title="T", project="demo")
        args = {"id": task["id"], "value": None, "text": "вопрос"}
        _call(self.conn, "listik_needs_owner", args)
        self.assertEqual(args, {"id": task["id"], "value": None, "text": "вопрос"})


if __name__ == "__main__":
    unittest.main()
