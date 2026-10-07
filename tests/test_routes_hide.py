"""`listik routes --hide KEY…` (listik-jdo8, порция a): оба пути — по базе и через сервер."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

from listik import paths, routes_store, server
from tests.test_claim import LISTIK_BIN
from tests.test_routes_db import REPO_DIR, RoutesDbTestCase

TOKEN = "test-token"


def _load_warning() -> str:
    from tests.test_autostart import _load_cli
    return _load_cli().LOCAL_BYPASS_WARNING


class HideBase(RoutesDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()
        routes_store.create_route(self.conn, key="swarm-x", kind="swarm", title="рой",
                                  visible=True, roles={"impl": {"harness": "dsh"}})
        self.config = self.tmp_path / "config.toml"
        self.config.write_text(f'[auth]\ntoken = "{TOKEN}"\n', encoding="utf-8")
        self.port = None

    def run_cli(self, *argv, local=True):
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_HOME": str(self.tmp_path / "data"), "LISTIK_CONFIG": str(self.config),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        cmd = [sys.executable, str(LISTIK_BIN)]
        if self.port is not None:
            cmd += ["--port", str(self.port)]
        if local:
            cmd.append("--local")
        return subprocess.run([*cmd, "routes", *argv], capture_output=True, text=True,
                              env=env, cwd=str(REPO_DIR))

    def visible(self, key: str) -> bool:
        return routes_store.get_route(self.conn, key)["visible"]

    def all_visible(self) -> dict:
        return {r["key"]: r["visible"] for r in routes_store.list_routes(self.conn)}


class HideLocalTests(HideBase):
    def test_hides_pipeline_and_swarm_dedup(self) -> None:
        before = routes_store.get_route(self.conn, "xlow-pipeline")
        p = self.run_cli("--hide", "xlow-pipeline", "swarm-x", "xlow-pipeline", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout), {"hidden": ["xlow-pipeline", "swarm-x"]})
        self.assertFalse(self.visible("xlow-pipeline"))
        self.assertFalse(self.visible("swarm-x"))
        after = routes_store.get_route(self.conn, "xlow-pipeline")
        for field in ("kind", "title", "hint", "roles", "command", "position"):
            self.assertEqual(before.get(field), after.get(field), field)

    def test_text_output_and_repeat_hide(self) -> None:
        self.run_cli("--hide", "nano-pipeline")
        p = self.run_cli("--hide", "nano-pipeline", "low-pipeline")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(p.stdout.strip(), "скрыты: nano-pipeline, low-pipeline")

    def test_missing_keys_change_nothing(self) -> None:
        before = self.all_visible()
        p = self.run_cli("--hide", "nope-1", "xlow-pipeline", "nope-2", "--json")
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        err = json.loads(p.stdout)["error"]
        self.assertEqual(err["code"], "not_found")
        self.assertIn("nope-1", err["message"])
        self.assertIn("nope-2", err["message"])
        self.assertEqual(self.all_visible(), before)

    def test_conflicting_flags_exit_2(self) -> None:
        before = self.all_visible()
        for extra in (["--reimport"], ["--from", "x.json"], ["--reimport", "--from", "x.json"]):
            p = self.run_cli("--hide", "xlow-pipeline", *extra)
            self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
            self.assertIn("--hide", p.stdout + p.stderr)
        self.assertEqual(self.all_visible(), before)

    def test_hide_without_keys_exit_2(self) -> None:
        p = self.run_cli("--hide")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)


class HideServerTests(HideBase):
    def setUp(self) -> None:
        super().setUp()
        self._saved = (paths.DB_PATH, paths.CONFIG_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = self.config
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        (paths.DB_PATH, paths.CONFIG_PATH, server._conn_made, server._conn_local) = self._saved
        super().tearDown()

    def test_hides_via_server(self) -> None:
        p = self.run_cli("--hide", "swarm-x", "low-pipeline", "--json", local=False)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout), {"hidden": ["swarm-x", "low-pipeline"]})
        self.assertNotIn("--local", p.stderr)
        self.assertFalse(self.visible("swarm-x"))
        self.assertFalse(self.visible("low-pipeline"))

    def test_missing_via_server_changes_nothing(self) -> None:
        before = self.all_visible()
        p = self.run_cli("--hide", "low-pipeline", "nope-a", "nope-b", "--json", local=False)
        self.assertEqual(p.returncode, 1, p.stdout + p.stderr)
        err = json.loads(p.stdout)["error"]
        self.assertEqual(err["code"], "not_found")
        self.assertIn("nope-a", err["message"])
        self.assertIn("nope-b", err["message"])
        self.assertEqual(self.all_visible(), before)

    def test_local_warns_while_server_up(self) -> None:
        p = self.run_cli("--hide", "low-pipeline", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(_load_warning(), p.stderr)
        self.assertEqual(json.loads(p.stdout), {"hidden": ["low-pipeline"]})
        self.assertFalse(self.visible("low-pipeline"))
