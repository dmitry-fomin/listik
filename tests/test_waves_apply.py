"""`apply_resource_blocks` — запись ресурсных рёбер: `deps.apply_resource_blocks`,
`listik waves --apply`, `POST /api/waves/apply`, MCP `listik_waves` с `apply` (listik-aoid,
порция c).

Расчёт волн (`deps.waves`) не меняется здесь — только запись найденного плана в базу.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from unittest import mock

from listik import client as client_mod
from listik import db as db_mod
from listik import deps, errors, mcp, paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_owner_http import AUTH, LOCAL_CONFIG, SERVER_CONFIG, OwnerHttpCase
from tests.test_waves import _no_route, _task

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
REPO_ROOT = LISTIK_BIN.parent.parent


def _stale_edge(conn, issue_id: str, depends_on: str, created_by: str = "old") -> None:
    """Ресурсное ребро, поставленное как бы прошлым проходом планировщика — не
    обязательно с текущим RESOURCE_BLOCK_AUTHOR, чтобы отличать «своё» от «чужого»
    только по dep_type/issue_id, как это делает `apply_resource_blocks`."""
    conn.execute(
        "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
        "VALUES(?, ?, 'resource-blocks', ?)",
        (issue_id, depends_on, created_by),
    )
    conn.commit()


def _all_deps(conn):
    return set(conn.execute(
        "SELECT issue_id, depends_on, dep_type, created_by FROM deps").fetchall())


def _all_deps_rows(conn):
    return {(r["issue_id"], r["depends_on"], r["dep_type"], r["created_by"])
            for r in _all_deps(conn)}


class ApplySetsEdgesTests(TempDbTestCase):
    def test_sets_resource_edge(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)

        out = deps.apply_resource_blocks(self.conn, project="demo")

        row = self.conn.execute(
            "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (c, a)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["created_by"], deps.RESOURCE_BLOCK_AUTHOR)
        self.assertEqual(out["added"], [[a, c]])
        self.assertEqual(out["removed"], [])
        self.assertEqual(out["kept"], 0)


class AuthorAlwaysMachineTests(TempDbTestCase):
    def test_local_call(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        with mock.patch.object(client_mod.db_mod, "init", return_value=self.conn):
            client_mod.local_call("waves_apply", project="demo")
        row = self.conn.execute(
            "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (c, a)).fetchone()
        self.assertEqual(row["created_by"], deps.RESOURCE_BLOCK_AUTHOR)

    def test_mcp_call_tool(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        mcp.call_tool("listik_waves", {"project": "demo", "apply": True},
                      conn=self.conn, owner="ann")
        row = self.conn.execute(
            "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (c, a)).fetchone()
        self.assertEqual(row["created_by"], deps.RESOURCE_BLOCK_AUTHOR)

    def test_author_prefix_marks_agent(self) -> None:
        # `actors.resolve` сам по себе не знает про этот конкретный ключ (нет ни
        # хинта, ни алиаса в базе) — так же, как `store.add_dep`/`store.update_task`
        # распознают агента: по префиксу `agent:` в самой строке.
        self.assertTrue(deps.RESOURCE_BLOCK_AUTHOR.startswith("agent:"))


class AuthorAlwaysMachineHttpTests(OwnerHttpCase):
    config_text = SERVER_CONFIG

    def _seed(self) -> tuple[str, str]:
        conn = db_mod.init(self.db_path)
        try:
            a = store.create_task(conn, title="A", project="demo", priority=0, as_owner="ann")["id"]
            c = store.create_task(conn, title="C", project="demo", priority=2, as_owner="ann")["id"]
            for tid in (a, c):
                conn.execute("UPDATE tasks SET launch_route='nano' WHERE id=?", (tid,))
                store.update_task(conn, tid, write_scope=["pkg/alpha.py"], as_owner="ann")
            conn.commit()
            return a, c
        finally:
            conn.close()

    def test_http_owner_and_actor_ignored(self) -> None:
        a, c = self._seed()
        headers = dict(AUTH)
        headers["X-Listik-Owner"] = "ann"
        status, _, payload = self.call(
            "POST", "/api/waves/apply", headers, {"project": "demo", "actor": "ann"})
        self.assertEqual(status, 200, payload)
        conn = db_mod.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=? "
                "AND dep_type='resource-blocks'", (c, a)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["created_by"], deps.RESOURCE_BLOCK_AUTHOR)


class ClaimGateTests(TempDbTestCase):
    def test_gate_after_apply(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        deps.apply_resource_blocks(self.conn, project="demo")

        state = deps.ready(self.conn, c)
        self.assertFalse(state["claimable"])
        self.assertEqual(state["blocked_by"][0]["id"], a)
        self.assertEqual(state["blocked_by"][0]["dep_title"], "ресурсный блокер")

        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, c, holder="x")
        text = str(cm.exception)
        self.assertIn(a, text)
        self.assertIn("--force", text)

        out = store.claim(self.conn, a, holder="x")
        self.assertEqual(out["holder"], "x")

        c_row = store.get_task(self.conn, c)
        self.assertIn(a, c_row["blocked_by"])


class IdempotenceTests(TempDbTestCase):
    def test_second_apply_no_changes(self) -> None:
        _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        deps.apply_resource_blocks(self.conn, project="demo")
        before = _all_deps_rows(self.conn)

        out2 = deps.apply_resource_blocks(self.conn, project="demo")

        self.assertEqual(out2["added"], [])
        self.assertEqual(out2["removed"], [])
        self.assertEqual(out2["kept"], 1)
        self.assertEqual(_all_deps_rows(self.conn), before)


class StaleRemovedTests(TempDbTestCase):
    def test_stale_edge_removed(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        _stale_edge(self.conn, b, a)
        deps.refresh_task(self.conn, b)
        self.conn.commit()

        out = deps.apply_resource_blocks(self.conn, project="demo")

        row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (b, a)).fetchone()
        self.assertIsNone(row)
        self.assertEqual(out["removed"], [[a, b]])
        b_row = store.get_task(self.conn, b)
        self.assertEqual(b_row["blocked_by"], [])


class ClosedRemovesEdgeTests(TempDbTestCase):
    def test_closed_task_edge_removed_on_next_apply(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/alpha.py",), priority=1)
        deps.apply_resource_blocks(self.conn, project="demo")
        row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (b, a)).fetchone()
        self.assertIsNotNone(row)

        store.update_task(self.conn, a, status="done", actor="автор")

        out2 = deps.apply_resource_blocks(self.conn, project="demo")
        self.assertEqual(out2["removed"], [[a, b]])


class SemanticUntouchedTests(TempDbTestCase):
    def test_blocks_and_suggested_survive(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        d = _task(self.conn, "d", scope=("pkg/delta.py",), priority=2)
        e = _task(self.conn, "e", scope=("pkg/epsilon.py",), priority=3)

        store.add_dep(self.conn, b, a, "blocks", created_by="автор")
        _stale_edge(self.conn, b, a)
        store.add_dep(self.conn, e, d, "blocks", created_by="agent:claude")  # suggested-blocks

        before_soft = {r for r in _all_deps_rows(self.conn) if r[2] != "resource-blocks"}

        deps.apply_resource_blocks(self.conn, project="demo")

        after_soft = {r for r in _all_deps_rows(self.conn) if r[2] != "resource-blocks"}
        self.assertEqual(before_soft, after_soft)
        resource_row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (b, a)).fetchone()
        self.assertIsNone(resource_row)


class ForeignProjectTests(TempDbTestCase):
    def test_other_project_untouched(self) -> None:
        x = _task(self.conn, "x", project="other", scope=("pkg/x.py",))
        y = _task(self.conn, "y", project="other", scope=("pkg/y.py",))
        _stale_edge(self.conn, y, x)

        deps.apply_resource_blocks(self.conn, project="demo")

        row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (y, x)).fetchone()
        self.assertIsNotNone(row)


class ForeignStageTests(TempDbTestCase):
    def test_stage_scope_respected(self) -> None:
        s1 = _task(self.conn, "s1a", scope=("pkg/s1a.py",), stage="s1-spec")
        s1b = _task(self.conn, "s1b", scope=("pkg/s1a.py",), stage="s1-spec")
        _stale_edge(self.conn, s1b, s1)

        s3 = _task(self.conn, "s3a", scope=("pkg/s3a.py",), stage="s3-impl")
        s3b = _task(self.conn, "s3b", scope=("pkg/s3b.py",), stage="s3-impl")
        _stale_edge(self.conn, s3b, s3)

        deps.apply_resource_blocks(self.conn, project="demo", stage="s3-impl")

        s1_row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (s1b, s1)).fetchone()
        self.assertIsNotNone(s1_row)
        s3_row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (s3b, s3)).fetchone()
        self.assertIsNone(s3_row)


class UnroutableUnscopedTests(TempDbTestCase):
    def test_stale_edges_removed_no_new(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        no_route = _no_route(self.conn, "nr", priority=1)
        no_scope = _task(self.conn, "ns", scope=(), priority=2)
        _stale_edge(self.conn, no_route, a)
        _stale_edge(self.conn, no_scope, a)

        out = deps.apply_resource_blocks(self.conn, project="demo")

        for tid in (no_route, no_scope):
            row = self.conn.execute(
                "SELECT 1 FROM deps WHERE issue_id=? AND dep_type='resource-blocks'",
                (tid,)).fetchone()
            self.assertIsNone(row)
        self.assertNotIn([a, no_route], out["added"])
        self.assertNotIn([a, no_scope], out["added"])


class CycleTests(TempDbTestCase):
    def test_cycle_rejected_nothing_written(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        for issue, dep in ((a, c), (b, a), (c, b)):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', 'human')", (issue, dep))
        self.conn.commit()
        before = _all_deps_rows(self.conn)

        with self.assertRaises(errors.ListikError) as cm:
            deps.apply_resource_blocks(self.conn, project="demo")
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertTrue(any(x in str(cm.exception) for x in (a, b, c)))
        self.assertEqual(_all_deps_rows(self.conn), before)


class RollbackTests(TempDbTestCase):
    def test_rollback_on_error_leaves_deps_untouched(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        # Устаревшее ребро b<-a, чтобы был хотя бы один DELETE перед INSERT'ами.
        _stale_edge(self.conn, b, a)
        before = _all_deps_rows(self.conn)

        # `refresh_task` кладут после INSERT/DELETE, до `commit` — сбой здесь
        # должен откатить уже выполненные (но не закоммиченные) DELETE/INSERT.
        with mock.patch.object(deps, "refresh_task", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                deps.apply_resource_blocks(self.conn, project="demo")

        self.assertEqual(_all_deps_rows(self.conn), before)


class CliTests(TempDbTestCase):
    def _run(self, *args, env_extra=None):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        env.pop("LISTIK_PROJECT", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )

    def test_cli_apply_json(self) -> None:
        _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        p = self._run("waves", "--project", "demo", "--apply", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        for key in ("added", "removed", "kept", "waves"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["added"]), 1)

    def test_cli_apply_text_then_repeat(self) -> None:
        _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        p = self._run("waves", "--project", "demo", "--apply")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("записано рёбер:", p.stdout)

        p2 = self._run("waves", "--project", "demo", "--apply")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("рёбра без изменений", p2.stdout)

    def test_cli_apply_cycle_error(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        for issue, dep in ((a, c), (b, a), (c, b)):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', 'human')", (issue, dep))
        self.conn.commit()
        p = self._run("waves", "--project", "demo", "--apply", "--json")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("conflict", p.stdout)


class CliLiveServerBypassTests(TempDbTestCase):
    """`--local --apply` при живом сервере на той же базе: предупреждение об обходе доски."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = self.tmp_path / "config.toml"
        paths.CONFIG_PATH.write_text(LOCAL_CONFIG, encoding="utf-8")
        self._saved_conn = (server._conn_made, server._conn_local)
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        server._conn_made, server._conn_local = self._saved_conn
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths
        super().tearDown()

    def test_local_apply_with_live_server_warns(self) -> None:
        _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        env.pop("LISTIK_PROJECT", None)
        p = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "--host", "127.0.0.1",
             "--port", str(self.port), "waves", "--project", "demo", "--apply"],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("--local", p.stderr)
        self.assertIn("доска не получит событие", p.stderr)


class HttpTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def _seed(self) -> tuple[str, str]:
        conn = db_mod.init(self.db_path)
        try:
            a = store.create_task(conn, title="A", project="demo", priority=0)["id"]
            c = store.create_task(conn, title="C", project="demo", priority=2)["id"]
            for tid in (a, c):
                conn.execute("UPDATE tasks SET launch_route='nano' WHERE id=?", (tid,))
                store.update_task(conn, tid, write_scope=["pkg/alpha.py"])
            conn.commit()
            return a, c
        finally:
            conn.close()

    def test_http_200_and_repeat(self) -> None:
        self._seed()
        status, _, payload = self.call("POST", "/api/waves/apply", AUTH, {"project": "demo"})
        self.assertEqual(status, 200, payload)
        self.assertIn("added", payload["data"])
        self.assertIn("generated_at", payload["data"])
        self.assertEqual(len(payload["data"]["added"]), 1)

        status2, _, payload2 = self.call("POST", "/api/waves/apply", AUTH, {"project": "demo"})
        self.assertEqual(status2, 200, payload2)
        self.assertEqual(payload2["data"]["added"], [])

    def test_http_400_missing_and_empty_project(self) -> None:
        status, _, payload = self.call("POST", "/api/waves/apply", AUTH, {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "bad_argument")

        status2, _, payload2 = self.call("POST", "/api/waves/apply", AUTH, {"project": ""})
        self.assertEqual(status2, 400)
        self.assertEqual(payload2["code"], "bad_argument")

    def test_http_409_on_cycle(self) -> None:
        conn = db_mod.init(self.db_path)
        try:
            a = _task(conn, "a", scope=("pkg/a.py",), priority=0)
            b = _task(conn, "b", scope=("pkg/b.py",), priority=1)
            c = _task(conn, "c", scope=("pkg/c.py",), priority=2)
            for issue, dep in ((a, c), (b, a), (c, b)):
                conn.execute(
                    "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                    "VALUES(?, ?, 'blocks', 'human')", (issue, dep))
            conn.commit()
        finally:
            conn.close()
        status, _, payload = self.call("POST", "/api/waves/apply", AUTH, {"project": "demo"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "conflict")

    def test_http_405_on_get(self) -> None:
        status, _, payload = self.call("GET", "/api/waves/apply?project=demo", AUTH)
        self.assertEqual(status, 405)


class McpTests(TempDbTestCase):
    def test_apply_true_writes(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        got = mcp.call_tool("listik_waves", {"project": "demo", "apply": True}, conn=self.conn)
        self.assertIn("added", got)
        row = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? "
            "AND dep_type='resource-blocks'", (c, a)).fetchone()
        self.assertIsNotNone(row)

    def test_apply_absent_does_not_touch_db(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        got = mcp.call_tool("listik_waves", {"project": "demo"}, conn=self.conn)
        self.assertNotIn("added", got)
        row = self.conn.execute(
            "SELECT 1 FROM deps WHERE dep_type='resource-blocks'").fetchone()
        self.assertIsNone(row)

    def test_not_a_write_tool(self) -> None:
        self.assertNotIn("listik_waves", mcp.WRITE_TOOLS)

    def test_schema_no_actor_required_project(self) -> None:
        tool = next(t for t in mcp.TOOLS if t["name"] == "listik_waves")
        self.assertNotIn("actor", tool["inputSchema"]["properties"])
        self.assertEqual(tool["inputSchema"]["required"], ["project"])
