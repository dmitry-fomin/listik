"""`resource-blocks` — ресурсный блокер планировщика роя (шаг listik-s520, порция b).

Жёсткий тип, но машинный: гейтит `claim`/`ready` как `blocks`, однако через `dep add`
(CLI/HTTP/MCP) его поставить нельзя ни под каким актором — только прямым INSERT, как
это будет делать планировщик (swarm-2, не эта порция). Смысловое ребро на той же паре
пишется рядом, а не поглощается ресурсным; проверка цикла в `add_dep` ресурсные рёбра
не учитывает.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from listik import deps, errors, mcp, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from tests.test_owner_http import LOCAL_CONFIG, OwnerHttpCase


def _insert_resource_block(conn, issue_id: str, depends_on: str) -> None:
    """Ставит ресурсное ребро так, как это делает планировщик роя — прямым INSERT."""
    conn.execute(
        "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?, ?, 'resource-blocks', ?)",
        (issue_id, depends_on, "listik-swarm"),
    )
    conn.commit()
    deps.refresh_task(conn, issue_id)
    conn.commit()


# 1. Справочник
class DictionaryTests(TempDbTestCase):
    def test_hard_soft_title(self) -> None:
        self.assertIn("resource-blocks", deps.HARD_BLOCKERS)
        self.assertNotIn("resource-blocks", deps.SOFT_LINKS)
        self.assertEqual(deps.DEP_TITLES["resource-blocks"], "ресурсный блокер")

    def test_semantic_hard_excludes_resource(self) -> None:
        self.assertNotIn("resource-blocks", deps.SEMANTIC_HARD)
        self.assertIn("blocks", deps.SEMANTIC_HARD)


# 2. Гейт claim как у blocks
class ClaimGateTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, self.b, self.a)
        store.claim(self.conn, self.a, holder="dsh")

    def test_claimable_false_with_reason(self) -> None:
        state = deps.ready(self.conn, self.b)
        self.assertFalse(state["claimable"])
        self.assertEqual(state["blocked_by"][0]["id"], self.a)
        self.assertEqual(state["blocked_by"][0]["dep_title"], "ресурсный блокер")
        self.assertEqual(state["verdict"], "нельзя: ждёт другие задачи")

    def test_claim_rejected_without_force(self) -> None:
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.b, holder="dsh")
        text = str(cm.exception)
        self.assertIn(self.a, text)
        self.assertIn("--force", text)

    def test_claim_force_bypasses_and_logs(self) -> None:
        out = store.claim(self.conn, self.b, holder="dsh", force=True)
        self.assertEqual(out["holder"], "dsh")
        notes = [e["note"] or "" for e in
                 self.conn.execute("SELECT note FROM events WHERE task_id=? AND kind='note'",
                                    (self.b,)).fetchall()]
        self.assertTrue(any("ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ" in n for n in notes))


# 3. Блокер закрыт — ребро отпускает
class BlockerClosedTests(TempDbTestCase):
    def test_ready_after_blocker_done(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        store.update_task(self.conn, a, status="done", actor="автор")
        self.assertTrue(deps.ready(self.conn, b)["claimable"])
        out = store.claim(self.conn, b, holder="dsh")
        self.assertEqual(out["holder"], "dsh")
        self.assertEqual(store.get_task(self.conn, b)["blocked_by"], [])


# 4. Списки
class ListsTests(TempDbTestCase):
    def test_ready_tasks_blocked_tasks_refresh(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        ready_ids = [t["id"] for t in deps.ready_tasks(self.conn, project="demo")]
        self.assertNotIn(b, ready_ids)
        blocked = deps.blocked_tasks(self.conn, project="demo")
        row = next(t for t in blocked if t["id"] == b)
        self.assertEqual(row["blockers"][0]["dep_type"], "resource-blocks")
        self.conn.execute("UPDATE tasks SET blocked_by = '[]' WHERE id = ?", (b,))
        self.conn.commit()
        changed = deps.refresh_blocked_column(self.conn)
        self.assertGreaterEqual(changed, 1)
        task = store.get_task(self.conn, b)
        self.assertEqual(task["blocked_by"], [a])


# 5. Цикл: диагностика видит, проверка add_dep — нет
class CycleTests(TempDbTestCase):
    def test_cycles_reports_resource_edge(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
            (a, b, "blocks", "автор"))
        self.conn.commit()
        found = deps.cycles(self.conn)
        flat = {tid for cycle in found for tid in cycle}
        self.assertIn(a, flat)
        self.assertIn(b, flat)

    def test_add_dep_blocks_not_blocked_by_resource_cycle(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        out = store.add_dep(self.conn, a, b, "blocks", created_by="автор")
        self.assertTrue(out["created"])
        self.assertFalse(deps.ready(self.conn, a)["claimable"])
        self.assertFalse(deps.ready(self.conn, b)["claimable"])

    def test_add_dep_semantic_cycle_still_rejected(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        store.add_dep(self.conn, b, a, "blocks", created_by="автор")
        with self.assertRaises(ValueError) as cm:
            store.add_dep(self.conn, a, b, "blocks", created_by="автор")
        self.assertIn("цикл", str(cm.exception))


# 6. Отказ add_dep
class AddDepRejectionTests(TempDbTestCase):
    def _assert_rejected(self, **kwargs) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        with self.assertRaises(errors.BadArgument) as cm:
            store.add_dep(self.conn, b, a, "resource-blocks", **kwargs)
        self.assertIn("ставит только планировщик", str(cm.exception))
        self.assertEqual(errors.code_of(cm.exception), "bad_argument")
        n = self.conn.execute("SELECT COUNT(*) FROM deps").fetchone()[0]
        self.assertEqual(n, 0)

    def test_rejected_human(self) -> None:
        self._assert_rejected(created_by="автор")

    def test_rejected_agent(self) -> None:
        self._assert_rejected(created_by="agent:claude")

    def test_rejected_agent_with_confirm(self) -> None:
        self._assert_rejected(created_by="agent:claude", confirm=True)


# 7. Отказ по CLI
class CliRejectionTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_cli_rejects_resource_blocks(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        p = self._run("dep", "add", b, a, "--dep-type", "resource-blocks")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ставит только планировщик", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        n = self.conn.execute("SELECT COUNT(*) FROM deps").fetchone()[0]
        self.assertEqual(n, 0)

    def test_cli_json_reports_bad_argument(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        p = self._run("dep", "add", b, a, "--dep-type", "resource-blocks", "--json")
        self.assertNotEqual(p.returncode, 0)
        err = json.loads(p.stdout)["error"]
        self.assertEqual(err["code"], "bad_argument")


# 8. Отказ по HTTP
class HttpRejectionTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def test_http_rejects_resource_blocks(self) -> None:
        a = self.make_task(title="A")
        b = self.make_task(title="B")
        status, payload = self.api("POST", f"/api/tasks/{b['id']}/deps",
                                   body={"depends_on": a["id"], "dep_type": "resource-blocks"})
        self.assertEqual(status, 400)
        self.assertEqual(payload.get("code"), "bad_argument")


# 9. Отказ по MCP
class McpRejectionTests(TempDbTestCase):
    def test_call_tool_raises(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        with self.assertRaises(errors.BadArgument):
            mcp.call_tool("listik_deps", {"id": b, "depends_on": a,
                                          "dep_type": "resource-blocks",
                                          "actor": "agent:claude"}, conn=self.conn)

    def test_handle_returns_is_error(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_deps",
                                      "arguments": {"id": b, "depends_on": a,
                                                    "dep_type": "resource-blocks",
                                                    "actor": "agent:claude"}}},
                          conn=self.conn)
        result = resp["result"]
        self.assertTrue(result["isError"])
        self.assertIn("ставит только планировщик", result["content"][0]["text"])


# 10. Смысловое рядом с ресурсным
class SemanticAlongsideResourceTests(TempDbTestCase):
    def test_blocks_written_alongside_resource(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        out = store.add_dep(self.conn, b, a, "blocks", created_by="автор")
        self.assertTrue(out["created"])
        self.assertEqual(out["dep_type"], "blocks")
        types = {r["dep_type"] for r in
                 self.conn.execute("SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?",
                                    (b, a)).fetchall()}
        self.assertEqual(types, {"resource-blocks", "blocks"})

    def test_agent_suggestion_then_confirm_keeps_resource(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        out = store.add_dep(self.conn, b, a, "blocks", created_by="agent:claude")
        self.assertTrue(out["suggested"])
        self.assertEqual(out["dep_type"], "suggested-blocks")
        self.assertFalse(out["confirmed"])
        types = {r["dep_type"] for r in
                 self.conn.execute("SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?",
                                    (b, a)).fetchall()}
        self.assertIn("suggested-blocks", types)

        out2 = store.add_dep(self.conn, b, a, "blocks", created_by="автор", confirm=True)
        self.assertTrue(out2["promoted"])
        types2 = {r["dep_type"] for r in
                  self.conn.execute("SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?",
                                     (b, a)).fetchall()}
        self.assertNotIn("suggested-blocks", types2)
        self.assertIn("resource-blocks", types2)


# 11. Снятие
class RemoveTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="B", project="demo")["id"]
        for dep_type in ("resource-blocks", "blocks", "suggested-blocks"):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?)",
                (self.b, self.a, dep_type, "автор"))
        self.conn.commit()
        deps.refresh_task(self.conn, self.b)
        self.conn.commit()

    def test_remove_without_type_leaves_resource(self) -> None:
        out = store.remove_dep(self.conn, self.b, self.a)
        self.assertEqual(out["removed"], 2)
        self.assertNotIn("resource-blocks", out["dep_types"])
        types = {r["dep_type"] for r in
                 self.conn.execute("SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?",
                                    (self.b, self.a)).fetchall()}
        self.assertIn("resource-blocks", types)

    def test_remove_explicit_resource_type(self) -> None:
        store.remove_dep(self.conn, self.b, self.a)
        out = store.remove_dep(self.conn, self.b, self.a, "resource-blocks")
        self.assertEqual(out["removed"], 1)
        deps.refresh_task(self.conn, self.b)
        self.assertEqual(store.get_task(self.conn, self.b)["blocked_by"], [])

    def test_cli_removes_resource_type(self) -> None:
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        p = subprocess.run([sys.executable, str(LISTIK_BIN), "--local", "dep", "rm",
                            self.b, self.a, "--dep-type", "resource-blocks"],
                           capture_output=True, text=True, env=env,
                           cwd=str(LISTIK_BIN.parent.parent))
        self.assertEqual(p.returncode, 0, p.stderr)


# 12. Снятие агентом по трём входам
class AgentRemovalTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_cli_agent_removes_resource(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        p = self._run("--actor", "agent:claude", "dep", "rm", b, a,
                      "--dep-type", "resource-blocks")
        self.assertEqual(p.returncode, 0, p.stderr)
        n = self.conn.execute(
            "SELECT COUNT(*) FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
            (b, a, "resource-blocks")).fetchone()[0]
        self.assertEqual(n, 0)
        self.assertTrue(deps.ready(self.conn, b)["claimable"])

    def test_mcp_agent_removes_resource(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        out = mcp.call_tool("listik_deps", {"id": b, "depends_on": a, "action": "rm",
                                            "dep_type": "resource-blocks",
                                            "actor": "agent:claude"}, conn=self.conn)
        self.assertEqual(out["removed"], 1)

    def test_mcp_without_type_leaves_resource_untouched(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        _insert_resource_block(self.conn, b, a)
        out = mcp.call_tool("listik_deps", {"id": b, "depends_on": a, "action": "rm",
                                            "actor": "agent:claude"}, conn=self.conn)
        self.assertEqual(out["removed"], 0)


class HttpAgentRemovalTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def _insert(self, issue_id: str, depends_on: str) -> None:
        from listik import db as db_mod
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        _insert_resource_block(conn, issue_id, depends_on)

    def test_http_agent_removes_resource(self) -> None:
        a = self.make_task(title="A")
        b = self.make_task(title="B")
        self._insert(b["id"], a["id"])
        status, payload = self.api(
            "DELETE", f"/api/tasks/{b['id']}/deps/{a['id']}?dep_type=resource-blocks")
        out = self.data(status, payload)
        self.assertEqual(out["removed"], 1)
        self.assertEqual(out["dep_types"], ["resource-blocks"])

    def test_http_without_type_leaves_resource_untouched(self) -> None:
        a = self.make_task(title="A")
        b = self.make_task(title="B")
        self._insert(b["id"], a["id"])
        status, payload = self.api("DELETE", f"/api/tasks/{b['id']}/deps/{a['id']}")
        out = self.data(status, payload)
        self.assertEqual(out["removed"], 0)


if __name__ == "__main__":
    unittest.main()
