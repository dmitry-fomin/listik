"""`listik waves` / `GET /api/waves` / MCP `listik_waves` (listik-aoid, порция b).

Расчёт волн — `deps.waves` (порция a, `tests/test_waves.py`); здесь проверяются три
внешних входа, которые его зовут, и ничего больше: CLI, HTTP, MCP.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from listik import client as client_mod
from listik import db as db_mod
from listik import deps, mcp, paths, store
from tests.helpers import TempDbTestCase
from tests.test_owner_http import AUTH, SERVER_CONFIG, OwnerHttpCase
from tests.test_waves import _task

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
REPO_ROOT = LISTIK_BIN.parent.parent


class WavesCliTests(TempDbTestCase):
    def _run(self, *args, cwd=None, env_extra=None):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        env.pop("LISTIK_PROJECT", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=str(cwd or REPO_ROOT),
        )

    def test_cli_json_scope_intersection(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        p = self._run("waves", "--project", "demo", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        for key in ("waves", "resource_blocks", "unroutable", "unscoped", "blocked", "tasks"):
            self.assertIn(key, payload)
        self.assertEqual(payload["waves"], [[a, b], [c]])
        self.assertEqual(payload["resource_blocks"], [[a, c]])

    def test_cli_text_output(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        p = self._run("waves", "--project", "demo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("волна 0:", p.stdout)
        self.assertIn("волна 1:", p.stdout)
        self.assertIn("ресурсные рёбра:", p.stdout)
        for tid in (a, b, c):
            self.assertIn(tid, p.stdout)

    def test_cli_cycle(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        for issue, dep in ((a, c), (b, a), (c, b)):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', 'human')", (issue, dep))
        self.conn.commit()

        p = self._run("waves", "--project", "demo")
        self.assertEqual(p.returncode, 1)
        self.assertIn("циклы:", p.stdout)

        p_json = self._run("waves", "--project", "demo", "--json")
        self.assertEqual(p_json.returncode, 1)
        payload = json.loads(p_json.stdout)
        self.assertTrue(payload["cycles"])

    def test_cli_no_project(self) -> None:
        p = self._run("waves", cwd=self.tmp_path)
        self.assertNotEqual(p.returncode, 0)
        combined = p.stdout + p.stderr
        self.assertIn("нужен проект", combined)
        self.assertIn("--project", combined)

        p_json = self._run("waves", "--json", cwd=self.tmp_path)
        self.assertNotEqual(p_json.returncode, 0)
        self.assertIn("bad_argument", p_json.stdout)

        p_all = self._run("waves", "--project", "all", cwd=self.tmp_path)
        self.assertNotEqual(p_all.returncode, 0)
        self.assertIn("нужен проект", p_all.stdout + p_all.stderr)

    def test_cli_project_from_env(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",))
        p = self._run("waves", "--json", env_extra={"LISTIK_PROJECT": "demo"},
                      cwd=self.tmp_path)
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        self.assertEqual(payload["waves"], [[a]])

    def test_cli_stage_filter(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0, stage="s3-impl")
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1, stage="s3-impl")
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2, stage="s1-spec")
        store.add_dep(self.conn, b, a, "blocks")
        p = self._run("waves", "--project", "demo", "--stage", "s3-impl", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        self.assertEqual(payload["waves"], [[a], [b]])
        self.assertNotIn(c, payload["tasks"])


class WavesHttpTests(OwnerHttpCase):
    config_text = SERVER_CONFIG

    def _seed(self, **kwargs):
        # Схема появляется лениво на первый запрос к серверу (`get_conn`); прямое
        # соединение до первого запроса её ещё не видит — досоздаём через `init`.
        conn = db_mod.init(self.db_path)
        try:
            # SERVER_CONFIG требует владельца у create_task — `_task` его не
            # прокидывает, поэтому создаём задачу сами, тем же приёмом.
            kwargs.setdefault("scope", ("pkg/a.py",))
            task = store.create_task(conn, title="task a", project="demo", priority=2,
                                     as_owner="ann")
            tid = task["id"]
            conn.execute("UPDATE tasks SET launch_route = 'nano' WHERE id = ?", (tid,))
            store.update_task(conn, tid, write_scope=list(kwargs["scope"]), as_owner="ann")
            conn.commit()
            return tid
        finally:
            conn.close()

    def test_http_200(self) -> None:
        self._seed(scope=("pkg/a.py",))
        status, headers, payload = self.call("GET", "/api/waves?project=demo", AUTH)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("waves", payload["data"])
        self.assertIn("generated_at", payload["data"])

    def test_http_400_missing_project(self) -> None:
        status, headers, payload = self.call("GET", "/api/waves", AUTH)
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertIn("нужен проект", payload["error"])

    def test_http_400_empty_project(self) -> None:
        status, headers, payload = self.call("GET", "/api/waves?project=", AUTH)
        self.assertEqual(status, 400)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertIn("нужен проект", payload["error"])

    def test_http_401(self) -> None:
        status, headers, payload = self.call("GET", "/api/waves?project=demo", {})
        self.assertEqual(status, 401)

    def test_http_405(self) -> None:
        status, headers, payload = self.call("POST", "/api/waves?project=demo", AUTH, {})
        self.assertEqual(status, 405)

    def test_http_owner_ignored(self) -> None:
        tid = self._seed(scope=("pkg/a.py",))
        conn = db_mod.connect(self.db_path)
        try:
            conn.execute("UPDATE tasks SET owner = 'bob' WHERE id = ?", (tid,))
            conn.commit()
        finally:
            conn.close()
        headers = dict(AUTH)
        headers["X-Listik-Owner"] = "ann"
        status, _, payload = self.call("GET", "/api/waves?project=demo", headers)
        self.assertEqual(status, 200)
        self.assertIn(tid, payload["data"]["tasks"])
        self.assertIn([tid], payload["data"]["waves"])


class WavesMcpTests(TempDbTestCase):
    def test_mcp_call_matches_deps(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",))
        expected = deps.waves(self.conn, project="demo")
        got = mcp.call_tool("listik_waves", {"project": "demo"}, self.conn, None)
        self.assertEqual(got, expected)

    def test_mcp_missing_project_is_error(self) -> None:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_waves", "arguments": {}}},
                          conn=self.conn)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("нужен проект", resp["result"]["content"][0]["text"])

    def test_mcp_empty_project_is_error(self) -> None:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_waves", "arguments": {"project": ""}}},
                          conn=self.conn)
        self.assertTrue(resp["result"]["isError"])
        self.assertIn("нужен проект", resp["result"]["content"][0]["text"])

    def test_mcp_not_a_write_tool(self) -> None:
        self.assertNotIn("listik_waves", mcp.WRITE_TOOLS)

    def test_mcp_tools_list_has_waves(self) -> None:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, conn=self.conn)
        tools = {t["name"]: t for t in resp["result"]["tools"]}
        self.assertIn("listik_waves", tools)
        self.assertEqual(tools["listik_waves"]["inputSchema"]["required"], ["project"])


class WavesFallbackTests(TempDbTestCase):
    def test_local_call_matches_deps(self) -> None:
        _task(self.conn, "a", scope=("pkg/a.py",))
        expected = deps.waves(self.conn, project="demo")
        # `local_call` открывает своё соединение через `db.init()`/`paths.DB_PATH`
        # (не принимает `conn=`), поэтому подменяем путь на ту же временную базу.
        with mock.patch.object(paths, "DB_PATH", self.db_path):
            got = client_mod.local_call("waves", project="demo")
        self.assertEqual(got, expected)
