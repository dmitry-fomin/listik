"""Tests for `store.add_dep`/`store.remove_dep`/`deps.suggested` (шаг 04, порция a).

Агент на s1 может записать предложение зависимости; жёсткая `blocks` появляется только
после подтверждения человека или явной команды; при добавлении жёсткой связи проверяется
цикл.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from listik import deps as deps_mod
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def _dep_rows(conn, issue_id, depends_on):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM deps WHERE issue_id=? AND depends_on=?", (issue_id, depends_on))]


class AddDepSuggestionTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_agent_without_confirm_records_suggestion_regardless_of_stage(self) -> None:
        # задача без этапа
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        self.assertTrue(out["suggested"])
        self.assertEqual(out["dep_type"], "suggested-blocks")
        self.assertEqual(out["requested_dep_type"], "blocks")
        rows = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dep_type"], "suggested-blocks")

    def test_agent_without_confirm_on_s1_also_suggests(self) -> None:
        store.update_task(self.conn, self.b, stage="s1-spec")
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        self.assertTrue(out["suggested"])
        rows = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dep_type"], "suggested-blocks")

    def test_suggestion_does_not_block_readiness(self) -> None:
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        self.assertTrue(deps_mod.ready(self.conn, self.b)["ready"])
        ready_ids = [t["id"] for t in deps_mod.ready_tasks(self.conn)]
        self.assertIn(self.b, ready_ids)
        row = self.conn.execute("SELECT blocked_by FROM tasks WHERE id=?", (self.b,)).fetchone()
        self.assertEqual(row["blocked_by"], "[]")

    def test_human_writes_hard_immediately(self) -> None:
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="me")
        self.assertFalse(out["suggested"])
        self.assertEqual(out["dep_type"], "blocks")

    def test_agent_with_confirm_writes_hard(self) -> None:
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex",
                            confirm=True)
        self.assertFalse(out["suggested"])
        self.assertEqual(out["dep_type"], "blocks")

    def test_empty_actor_writes_hard(self) -> None:
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by=None)
        self.assertFalse(out["suggested"])
        self.assertEqual(out["dep_type"], "blocks")

    def test_unknown_actor_with_agent_prefix_suggests(self) -> None:
        # actors.resolve не знает "agent:mcp" и вернёт kind human — правило префикса важнее.
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:mcp")
        self.assertTrue(out["suggested"])


class McpDepsActorTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_mcp_without_actor_is_agent(self) -> None:
        from listik import mcp
        from listik import paths
        with mock.patch.object(paths, "DB_PATH", self.db_path):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LISTIK_ACTOR", None)
                out = mcp.call_tool("listik_deps", {"id": self.b, "depends_on": self.a})
        self.assertTrue(out["suggested"])
        rows = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(rows[0]["created_by"], "agent:mcp")

    def test_mcp_with_env_actor(self) -> None:
        from listik import mcp
        from listik import paths
        with mock.patch.object(paths, "DB_PATH", self.db_path):
            with mock.patch.dict(os.environ, {"LISTIK_ACTOR": "agent:codex"}):
                out = mcp.call_tool("listik_deps", {"id": self.b, "depends_on": self.a})
        self.assertTrue(out["suggested"])
        rows = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(rows[0]["created_by"], "agent:codex")

    def test_mcp_with_explicit_human_actor(self) -> None:
        from listik import mcp
        from listik import paths
        with mock.patch.object(paths, "DB_PATH", self.db_path):
            out = mcp.call_tool("listik_deps", {"id": self.b, "depends_on": self.a,
                                                "actor": "me"})
        self.assertFalse(out["suggested"])
        self.assertEqual(out["dep_type"], "blocks")


class ConfirmPromotesSuggestionTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_confirm_replaces_suggestion_with_hard_and_blocks(self) -> None:
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="me")
        rows = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dep_type"], "blocks")
        self.assertTrue(out["promoted"])
        self.assertTrue(out["confirmed"])
        blockers = [b["id"] for b in deps_mod.blockers(self.conn, self.b)]
        self.assertIn(self.a, blockers)
        ready_ids = [t["id"] for t in deps_mod.ready_tasks(self.conn)]
        self.assertNotIn(self.b, ready_ids)

    def test_repeat_same_hard_dep_is_not_created_again(self) -> None:
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="me")
        out = store.add_dep(self.conn, self.b, self.a, "blocks", created_by="me")
        self.assertFalse(out["created"])
        rows = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(len(rows), 1)


class CycleAndSelfLinkTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]
        self.c = store.create_task(self.conn, title="C", project="demo")["id"]

    def test_self_link_raises(self) -> None:
        with self.assertRaises(ValueError):
            store.add_dep(self.conn, self.a, self.a, "blocks", created_by="me")

    def test_two_node_cycle_rejected(self) -> None:
        store.add_dep(self.conn, self.a, self.b, "blocks", created_by="me")
        with self.assertRaises(ValueError) as ctx:
            store.add_dep(self.conn, self.b, self.a, "blocks", created_by="me")
        text = str(ctx.exception)
        self.assertIn("цикл", text)
        self.assertIn(self.a, text)
        self.assertIn(self.b, text)
        self.assertEqual(deps_mod.cycles(self.conn), [])
        # A уже законно заблокирована B (первый вызов); отклонённая попытка B->A
        # не должна была ничего изменить.
        row_a = self.conn.execute("SELECT blocked_by FROM tasks WHERE id=?", (self.a,)).fetchone()
        self.assertEqual(json.loads(row_a["blocked_by"]), [self.b])
        row_b = self.conn.execute("SELECT blocked_by FROM tasks WHERE id=?", (self.b,)).fetchone()
        self.assertEqual(json.loads(row_b["blocked_by"]), [])

    def test_three_node_cycle_rejected(self) -> None:
        store.add_dep(self.conn, self.a, self.b, "blocks", created_by="me")
        store.add_dep(self.conn, self.b, self.c, "blocks", created_by="me")
        with self.assertRaises(ValueError) as ctx:
            store.add_dep(self.conn, self.c, self.a, "blocks", created_by="me")
        text = str(ctx.exception)
        self.assertIn("цикл", text)
        self.assertIn(self.a, text)
        self.assertIn(self.c, text)
        self.assertEqual(deps_mod.cycles(self.conn), [])


class SoftLinksRegressionTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_related_and_supersedes_are_soft(self) -> None:
        store.add_dep(self.conn, self.b, self.a, "related", created_by="me", confirm=True)
        store.add_dep(self.conn, self.b, self.a, "supersedes", created_by="me", confirm=True)
        task = store.get_task(self.conn, self.b)
        self.assertNotIn(self.a, task["blocked_by"])
        soft_types = {x["dep_type"] for x in task["deps_state"]["soft_links"]}
        self.assertIn("related", soft_types)
        self.assertIn("supersedes", soft_types)
        self.assertTrue(deps_mod.ready(self.conn, self.b)["ready"])


class ReadyTasksTests(TempDbTestCase):
    def test_two_independent_portions_both_ready_then_blocked_becomes_ready(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        store.add_dep(self.conn, b, a, "blocks", created_by="me")
        ready_ids = [t["id"] for t in deps_mod.ready_tasks(self.conn)]
        self.assertIn(a, ready_ids)
        self.assertNotIn(b, ready_ids)
        store.update_task(self.conn, a, status="done")
        ready_ids = [t["id"] for t in deps_mod.ready_tasks(self.conn)]
        self.assertIn(b, ready_ids)


class RemoveDepTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_remove_without_type_removes_both_blocks_and_suggested(self) -> None:
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
            (self.b, self.a, "blocks", "me"))
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
            (self.b, self.a, "suggested-blocks", "agent:codex"))
        self.conn.commit()
        out = store.remove_dep(self.conn, self.b, self.a)
        self.assertEqual(out["removed"], 2)
        self.assertIn("blocks", out["dep_types"])
        self.assertIn("suggested-blocks", out["dep_types"])
        self.assertEqual(_dep_rows(self.conn, self.b, self.a), [])

    def test_remove_with_explicit_type_removes_only_that_type(self) -> None:
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
            (self.b, self.a, "blocks", "me"))
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
            (self.b, self.a, "suggested-blocks", "agent:codex"))
        self.conn.commit()
        out = store.remove_dep(self.conn, self.b, self.a, dep_type="suggested-blocks")
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["dep_types"], ["suggested-blocks"])
        remaining = _dep_rows(self.conn, self.b, self.a)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["dep_type"], "blocks")


class DepsSuggestedTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_suggested_contains_pending_then_empty_after_confirm(self) -> None:
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        items = deps_mod.suggested(self.conn)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["issue_id"], self.b)
        self.assertEqual(items[0]["depends_on"], self.a)
        self.assertEqual(items[0]["created_by"], "agent:codex")
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="me")
        self.assertEqual(deps_mod.suggested(self.conn), [])

    def test_project_filter(self) -> None:
        other = store.create_task(self.conn, title="Other", project="other")["id"]
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        store.add_dep(self.conn, other, self.a, "blocks", created_by="agent:codex")
        items = deps_mod.suggested(self.conn, project="demo")
        ids = {i["issue_id"] for i in items}
        self.assertIn(self.b, ids)
        self.assertNotIn(other, ids)

    def test_closed_dependent_task_is_not_shown(self) -> None:
        store.add_dep(self.conn, self.b, self.a, "blocks", created_by="agent:codex")
        store.update_task(self.conn, self.b, status="done")
        self.assertEqual(deps_mod.suggested(self.conn), [])


class CliDepTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        cwd = str(LISTIK_BIN.parent.parent)
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=cwd,
        )

    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]

    def test_add_from_agent_creates_suggestion(self) -> None:
        p = self._run("dep", "add", self.b, self.a, "--actor", "agent:codex")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("предложение", p.stdout)

    def test_add_creating_cycle_fails_without_traceback(self) -> None:
        r1 = self._run("dep", "add", self.b, self.a, "--actor", "me")
        self.assertEqual(r1.returncode, 0, r1.stderr)
        p = self._run("dep", "add", self.a, self.b, "--actor", "me")
        self.assertEqual(p.returncode, 1)
        self.assertIn("цикл", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_confirm_json_reports_confirmed_true(self) -> None:
        self._run("dep", "add", self.b, self.a, "--actor", "agent:codex")
        p = self._run("dep", "confirm", self.b, self.a, "--actor", "me", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertTrue(data["confirmed"])

    def test_suggested_json_is_a_list(self) -> None:
        self._run("dep", "add", self.b, self.a, "--actor", "agent:codex")
        p = self._run("dep", "suggested", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    def test_show_prints_suggested_blockers(self) -> None:
        self._run("dep", "add", self.b, self.a, "--actor", "agent:codex")
        p = self._run("show", self.b)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("предложены блокеры", p.stdout)

    def test_show_and_tree_survive_dangling_soft_link(self) -> None:
        x = store.create_task(self.conn, title="X", project="demo")["id"]
        store.add_dep(self.conn, self.b, x, "relates-to", created_by="me")
        store.delete_task(self.conn, x)
        p = self._run("show", self.b)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("Traceback", p.stdout)
        self.assertIn(f"связано (не блокирует): {x} (relates-to, задача не найдена)", p.stdout)
        p2 = self._run("dep", "tree", self.b)
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertNotIn("Traceback", p2.stdout)

    def test_dep_tree_survives_dangling_hard_link(self) -> None:
        """graph() used b[k]/w[k] for a dict without holder_title on a missing blocker."""
        x = store.create_task(self.conn, title="X", project="demo")["id"]
        store.add_dep(self.conn, self.b, x, "blocks", created_by="me", confirm=True)
        store.delete_task(self.conn, x)
        p = self._run("dep", "tree", self.b)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("Traceback", p.stdout)
        self.assertIn(x, p.stdout)


if __name__ == "__main__":
    unittest.main()
