"""Tests for the full MCP tool set of «задачи и очередь» (шаг 08, порция a).

`call_tool` принимает готовое соединение (`conn=`) и не открывает своё, `handle`
пробрасывает его дальше. Набор инструментов расширен до 28: добавлены release,
inbox, memory, remember, projects, actors, timeline, deps_suggested, cycles;
у `listik_done` появился `note`, у `listik_heartbeat` — `harness`, а запись
заметки переехала из CLI в `store.remember`.

Каждая проверка чек-листа `step-08.check-a.md` (пункты 1–16) — отдельный тест.
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import unittest
from unittest import mock

from listik import mcp
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"

OLD_TOOLS = {
    "listik_search", "listik_list", "listik_show", "listik_create", "listik_update",
    "listik_context", "listik_claim", "listik_heartbeat", "listik_stage", "listik_comment",
    "listik_needs_owner", "listik_done", "listik_ready", "listik_blocked", "listik_can_take",
    "listik_dep_tree", "listik_board", "listik_stats", "listik_deps",
}
NEW_TOOLS = {
    "listik_release", "listik_inbox", "listik_memory", "listik_remember", "listik_projects",
    "listik_actors", "listik_timeline", "listik_deps_suggested", "listik_cycles",
}
REQUIRED_BY_TOOL = {"listik_release": ["id"], "listik_remember": ["text"]}


def _tools_list(conn) -> dict[str, dict]:
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, conn=conn)
    return {t["name"]: t for t in resp["result"]["tools"]}


def _call_error(conn, name, args) -> dict:
    resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}, conn=conn)
    return resp["result"]


def _memory_keys(out) -> list[str]:
    return [item["key"] for item in out["items"]]


class McpToolsTests(TempDbTestCase):
    # ---- 1. список инструментов
    def test_tools_list_returns_exactly_28_names(self) -> None:
        names = set(_tools_list(self.conn))
        self.assertEqual(names, OLD_TOOLS | NEW_TOOLS)
        self.assertEqual(len(names), 28)

    # ---- 2. схемы новых инструментов и новые параметры старых
    def test_new_tool_schemas_and_new_params(self) -> None:
        tools = _tools_list(self.conn)
        for name in sorted(NEW_TOOLS):
            schema = tools[name]["inputSchema"]
            self.assertEqual(schema["type"], "object", name)
            self.assertTrue(tools[name]["description"].strip(), name)
            self.assertEqual(schema.get("required") or [], REQUIRED_BY_TOOL.get(name, []), name)
        done_props = tools["listik_done"]["inputSchema"]["properties"]
        self.assertIn("note", done_props)
        hb_props = tools["listik_heartbeat"]["inputSchema"]["properties"]
        self.assertIn("harness", hb_props)

    # ---- 3. release
    def test_release_clears_holder_and_writes_release_event(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        mcp.call_tool("listik_claim", {"id": task_id, "holder": "agent:claude"}, conn=self.conn)
        out = mcp.call_tool("listik_release", {"id": task_id, "actor": "agent:claude",
                                               "note": "ухожу"}, conn=self.conn)
        self.assertEqual(out["holder"], "")
        self.assertIn("ухожу", [e["note"] for e in out["events"] if e["kind"] == "release"])

        mcp.call_tool("listik_claim", {"id": task_id, "holder": "agent:claude"}, conn=self.conn)
        out = mcp.call_tool("listik_release", {"id": task_id}, conn=self.conn)
        self.assertEqual(out["holder"], "")
        self.assertIn("освободил", [e["note"] for e in out["events"] if e["kind"] == "release"])

    # ---- 4. inbox
    def test_inbox_splits_questions_and_dropped(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        store.set_needs_owner(self.conn, task_id, value=True, text="Q")
        fresh_id = store.create_task(self.conn, title="Свежая")["id"]

        out = mcp.call_tool("listik_inbox", {}, conn=self.conn)
        self.assertEqual(set(out.keys()), {"questions", "dropped"})
        self.assertIn(task_id, [t["id"] for t in out["questions"]])
        self.assertNotIn(task_id, [t["id"] for t in out["dropped"]])
        self.assertNotIn(fresh_id, [t["id"] for t in out["questions"]])

    # ---- 5. remember: upsert по ключу
    def test_remember_returns_key_project_and_upserts(self) -> None:
        out = mcp.call_tool("listik_remember", {"text": "заметка", "key": "k1",
                                                "project": "demo"}, conn=self.conn)
        self.assertEqual(out, {"key": "k1", "project": "demo"})

        mcp.call_tool("listik_remember", {"text": "новая", "key": "k1",
                                          "project": "demo"}, conn=self.conn)
        rows = self.conn.execute("SELECT body FROM memories WHERE key='k1'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "новая")
        fts = self.conn.execute(
            "SELECT count(*) n FROM memory_fts WHERE memory_key='k1'").fetchone()
        self.assertEqual(fts["n"], 1)

    # ---- 6. remember: значения по умолчанию
    def test_remember_defaults_project_and_key(self) -> None:
        out = mcp.call_tool("listik_remember", {"text": "x"}, conn=self.conn)
        self.assertEqual(out["project"], "personal")
        self.assertRegex(out["key"], re.compile(r"^note/\d+$"))

    # ---- 7. memory без query: список, фильтр по проекту, лимит
    def test_memory_without_query_lists_and_filters(self) -> None:
        mcp.call_tool("listik_remember", {"text": "заметка", "key": "k1",
                                          "project": "demo"}, conn=self.conn)

        out = mcp.call_tool("listik_memory", {}, conn=self.conn)
        self.assertIn("k1", _memory_keys(out))
        out = mcp.call_tool("listik_memory", {"project": "demo"}, conn=self.conn)
        self.assertIn("k1", _memory_keys(out))
        out = mcp.call_tool("listik_memory", {"project": "other"}, conn=self.conn)
        self.assertNotIn("k1", _memory_keys(out))

        for i in range(25):
            mcp.call_tool("listik_remember", {"text": f"т{i}", "key": f"n{i}"}, conn=self.conn)
        out = mcp.call_tool("listik_memory", {}, conn=self.conn)
        self.assertEqual(len(out["items"]), 20)

    # ---- 8. memory с query: лексический поиск
    def test_memory_query_finds_note(self) -> None:
        mcp.call_tool("listik_remember", {"text": "новая", "key": "k1",
                                          "project": "demo"}, conn=self.conn)
        out = mcp.call_tool("listik_memory", {"query": "новая"}, conn=self.conn)
        self.assertEqual(set(out.keys()), {"items"})
        self.assertIn("k1", _memory_keys(out))

    # ---- 9. remember: пустой текст — ошибка инструмента
    def test_remember_blank_text_is_tool_error(self) -> None:
        result = _call_error(self.conn, "listik_remember", {"text": "   "})
        self.assertTrue(result["isError"])
        self.assertIn("пустой текст заметки", result["content"][0]["text"])
        rows = self.conn.execute("SELECT count(*) n FROM memories").fetchone()
        self.assertEqual(rows["n"], 0)

    # ---- 10. actors, timeline, cycles
    def test_actors_timeline_cycles_shapes(self) -> None:
        self.assertIsInstance(mcp.call_tool("listik_actors", {}, conn=self.conn)["actors"], list)

        store.create_task(self.conn, title="Задача")
        items = mcp.call_tool("listik_timeline", {"limit": 5}, conn=self.conn)["items"]
        self.assertLessEqual(len(items), 5)

        self.assertIsInstance(mcp.call_tool("listik_cycles", {}, conn=self.conn)["cycles"], list)

    # ---- 11. deps_suggested
    def test_deps_suggested_lists_agent_proposal(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        mcp.call_tool("listik_deps", {"id": b, "depends_on": a,
                                      "actor": "agent:codex"}, conn=self.conn)
        items = mcp.call_tool("listik_deps_suggested", {}, conn=self.conn)["items"]
        self.assertIn((b, a), [(i["issue_id"], i["depends_on"]) for i in items])

    # ---- 12. projects
    def test_projects_include_archived_flag(self) -> None:
        store.upsert_project(self.conn, "demo")
        store.upsert_project(self.conn, "old", archived=1)

        slugs = [p["slug"] for p in mcp.call_tool("listik_projects", {}, conn=self.conn)["projects"]]
        self.assertIn("demo", slugs)
        self.assertNotIn("old", slugs)

        slugs = [p["slug"] for p in mcp.call_tool(
            "listik_projects", {"include_archived": True}, conn=self.conn)["projects"]]
        self.assertIn("demo", slugs)
        self.assertIn("old", slugs)

    # ---- 13. done с note
    def test_done_accepts_note(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        out = mcp.call_tool("listik_done", {"id": task_id, "result": "r",
                                            "note": "итог"}, conn=self.conn)
        self.assertEqual(out["status"], "done")
        self.assertEqual(out["stage"], "done")
        self.assertIn("итог", [e["note"] for e in out["events"] if e["kind"] == "status"])

    # ---- 14. heartbeat с harness
    def test_heartbeat_passes_harness_to_store(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        mcp.call_tool("listik_claim", {"id": task_id, "holder": "agent:claude"}, conn=self.conn)
        with mock.patch.object(store, "heartbeat", wraps=store.heartbeat) as spy:
            mcp.call_tool("listik_heartbeat", {"id": task_id, "holder": "agent:claude",
                                               "harness": "claude", "note": "жив"}, conn=self.conn)
        self.assertEqual(spy.call_args.kwargs.get("harness"), "claude")

    # ---- 15. соединение
    def test_call_tool_uses_passed_connection_and_does_not_close_it(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        out = mcp.call_tool("listik_show", {"id": task_id}, conn=self.conn)
        self.assertEqual(out["id"], task_id)
        self.conn.execute("SELECT 1")

        with mock.patch.object(mcp, "_conn",
                               side_effect=AssertionError("_conn не должен вызываться")):
            out = mcp.call_tool("listik_show", {"id": task_id}, conn=self.conn)
        self.assertEqual(out["id"], task_id)

    # ---- 16. CLI: remember идёт через store.remember
    def test_cli_local_remember_writes_note(self) -> None:
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        p = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "remember", "текст", "--key", "k2"],
            capture_output=True, text=True, check=True, env=env,
            cwd=str(LISTIK_BIN.parent.parent),
        )
        self.assertEqual(p.stdout.strip(), "запомнено: k2")
        row = self.conn.execute("SELECT body FROM memories WHERE key='k2'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["body"], "текст")


if __name__ == "__main__":
    unittest.main()
