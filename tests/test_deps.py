"""Tests for `store.add_dep`/`store.remove_dep`/`deps.suggested` (шаг 04, порция a).

Агент на s1 может записать предложение зависимости; жёсткая `blocks` появляется только
после подтверждения человека или явной команды; при добавлении жёсткой связи проверяется
цикл.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

from listik import db as db_mod
from listik import deps as deps_mod
from listik import store
from tests.helpers import TempDbTestCase
from tests.test_owner_http import LOCAL_CONFIG, OwnerHttpCase

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
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (x,))
        self.conn.commit()
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
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (x,))
        self.conn.commit()
        p = self._run("dep", "tree", self.b)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("Traceback", p.stdout)
        self.assertIn(x, p.stdout)


class FetchPropagatesErrorsTests(TempDbTestCase):
    """`_fetch` не глотает ошибку SQL: иначе гейты claim/ready открываются при сбое (listik-nvro)."""

    def test_missing_table_raises(self) -> None:
        with self.assertRaises(sqlite3.OperationalError):
            deps_mod._fetch(self.conn, "SELECT * FROM no_such_table")


class DanglingDepsTests(TempDbTestCase):
    """Висячая строка deps (задачи нет в tasks) не роняет ready/claim/blocked (listik-my65)."""

    def _new(self, title: str) -> str:
        return store.create_task(self.conn, title=title, project="demo")["id"]

    def _drop(self, task_id: str) -> None:
        self.conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()

    def _events(self, task_id: str, kind: str) -> list:
        return self.conn.execute("SELECT * FROM events WHERE task_id = ? AND kind = ? ORDER BY id",
                                 (task_id, kind)).fetchall()

    def _dangling_blocker(self) -> tuple[str, str]:
        a, b = self._new("A"), self._new("B")
        store.add_dep(self.conn, a, b, "blocks", created_by="me", confirm=True)
        self._drop(b)
        return a, b

    def test_s1_ready_reports_missing_blocker(self) -> None:
        a, b = self._dangling_blocker()
        state = deps_mod.ready(self.conn, a)
        self.assertIs(state["ready"], False)
        self.assertIs(state["claimable"], False)
        self.assertEqual(state["verdict"], "нельзя: ждёт другие задачи")
        self.assertEqual(len(state["blocked_by"]), 1)
        blocker = state["blocked_by"][0]
        self.assertEqual(blocker["id"], b)
        self.assertIs(blocker["missing"], True)
        self.assertEqual(blocker["status"], "missing")
        self.assertIsNone(blocker["holder"])
        self.assertIsNone(blocker["holder_title"])
        self.assertEqual(blocker["idle_age"], "—")
        self.assertIs(blocker["stale"], False)
        self.assertNotIn("holder_age", blocker)
        self.assertIn(f"ждёт завершения: {b} (missing, —)", state["reasons"])

    def test_s2_claim_refuses_without_writing(self) -> None:
        a, b = self._dangling_blocker()
        with self.assertRaises(ValueError) as ctx:
            store.claim(self.conn, a, holder="dsh")
        text = str(ctx.exception)
        for part in (b, "(задача не найдена)", "Варианты", "--force"):
            self.assertIn(part, text)
        self.assertNotIn("держит", text)
        task = store.get_task(self.conn, a)
        self.assertFalse(task["holder"])
        self.assertEqual(task["status"], "open")
        self.assertEqual(self._events(a, "claim"), [])

    def test_s3_force_claim_leaves_note(self) -> None:
        a, b = self._dangling_blocker()
        task = store.claim(self.conn, a, holder="dsh", force=True)
        self.assertEqual(task["holder"], "dsh")
        notes = [e["note"] or "" for e in self._events(a, "note")]
        self.assertTrue(any("ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ" in n and b in n for n in notes), notes)

    def test_s4_blocked_tasks_lists_task(self) -> None:
        a, b = self._dangling_blocker()
        tasks = {t["id"]: t for t in deps_mod.blocked_tasks(self.conn, project="demo")}
        self.assertIn(a, tasks)
        task = tasks[a]
        self.assertEqual([x["id"] for x in task["blockers"]], [b])
        self.assertEqual(task["blocked_by"], [b])
        self.assertIs(task["blockers_idle"], True)
        self.assertIsNone(task["blocked_by_holder"])

    def test_s5_get_task_has_deps_state(self) -> None:
        a, b = self._dangling_blocker()
        state = store.get_task(self.conn, a)["deps_state"]
        self.assertIsNotNone(state)
        self.assertIs(state["claimable"], False)
        self.assertEqual(state["blocked_by"][0]["id"], b)

    def test_s6_epic_with_only_dangling_child(self) -> None:
        p, c = self._new("P"), self._new("C")
        store.add_dep(self.conn, c, p, "parent-child", created_by="me")
        self._drop(c)
        self.assertEqual(deps_mod.children(self.conn, p), [])
        state = deps_mod.ready(self.conn, p)
        self.assertEqual(state["children_open"], [])
        self.assertIs(state["can_finish"], True)
        self.assertEqual(store.claim(self.conn, p, holder="dsh")["holder"], "dsh")

    def test_s7_live_children_kept_in_deps_order(self) -> None:
        # Карточки создаются в одном порядке, связи — в другом: порядок детей — по строкам deps.
        p = self._new("P")
        d = self._new("D")
        c2 = self._new("C2")
        x = self._new("X")
        c1 = self._new("C1")
        for child in (c1, x, c2, d):
            store.add_dep(self.conn, child, p, "parent-child", created_by="me")
        store.update_task(self.conn, d, status="done")
        self._drop(x)
        self.assertEqual([c["id"] for c in deps_mod.children(self.conn, p)], [c1, c2, d])
        state = deps_mod.ready(self.conn, p)
        self.assertEqual([c["id"] for c in state["children_open"]], [c1, c2])
        self.assertIs(state["can_finish"], False)


class DanglingDepsHttpTests(OwnerHttpCase):
    """Сценарий 8 listik-my65: висячий блокер через живой сервер — отказ 400, а не 500."""

    config_text = LOCAL_CONFIG

    def test_s8_http_endpoints_survive_missing_blocker(self) -> None:
        a = self.make_task(title="A")["id"]
        b = self.make_task(title="B")["id"]
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        store.add_dep(conn, a, b, "blocks", created_by="me", confirm=True)
        conn.execute("DELETE FROM tasks WHERE id = ?", (b,))
        conn.commit()

        status, payload = self.api("POST", f"/api/tasks/{a}/claim", body={"holder": "agent:dsh"})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "conflict")
        self.assertIn(b, payload["hint"])

        data = self.data(*self.api("POST", f"/api/tasks/{a}/ready", body={}))
        self.assertIs(data["claimable"], False)

        data = self.data(*self.api("GET", "/api/blocked"))
        self.assertIn(a, [t["id"] for t in data["tasks"]])

        data = self.data(*self.api("GET", f"/api/tasks/{a}"))
        self.assertIs(data["deps_state"]["claimable"], False)


class GetTaskDepsStateDbErrorTests(TempDbTestCase):
    """Сбой базы в deps.ready доходит из get_task, а не становится deps_state: None (listik-88ef).

    Ломается projects.path: его читает только deps.ready (через worktree_conflict), а deps.dep_type
    уже раньше читает row_to_task, и такая поломка падала бы и до правки.
    """

    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="A", project="demo")["id"]
        self.conn.execute("ALTER TABLE projects RENAME COLUMN path TO dir")
        self.conn.commit()

    def test_get_task_raises_operational_error(self) -> None:
        for with_details in (True, False):
            with self.subTest(with_details=with_details):
                with self.assertRaises(sqlite3.OperationalError):
                    store.get_task(self.conn, self.task_id, with_details=with_details)


class GetTaskDepsStateDbErrorHttpTests(OwnerHttpCase):
    """Сбой базы в deps.ready на PATCH задачи — 503 server_error, а не 200 с deps_state: null (listik-88ef)."""

    config_text = LOCAL_CONFIG

    def test_patch_task_reports_db_error(self) -> None:
        a = self.make_task(title="A")["id"]
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        conn.execute("ALTER TABLE projects RENAME COLUMN path TO dir")
        conn.commit()

        status, payload = self.api("PATCH", f"/api/tasks/{a}", body={"title": "B"})
        self.assertEqual(status, 503, payload)
        self.assertEqual(payload["code"], "server_error")


if __name__ == "__main__":
    unittest.main()
