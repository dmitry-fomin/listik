"""`listik list --stale` отбирает брошенные до `--limit`, `--json` — только их (listik-8lpa).

100 здоровых задач в работе свежее трёх брошенных по `updated_at`: окно `-n 10` по всем задачам
в работе брошенных не видит, поэтому фильтр обязан работать на стороне источника
(`health=dead`), а не на полученной странице.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone

from listik import paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from tests.test_cli_errors import CliErrorCase


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed(case) -> None:
    """100 здоровых в работе и брошенные A, S, R; порядок `updated_at DESC`: здоровые, A, S, R."""
    conn = case.conn

    def sql(query: str, *params) -> None:
        conn.execute(query, params)
        conn.commit()

    def new(title: str) -> str:
        return store.create_task(conn, title=title, project="demo", stage="s1-spec")["id"]

    case.healthy = []
    for i in range(100):
        tid = new(f"здоровая {i}")
        sql("UPDATE tasks SET status = 'in_progress', holder = 'dsh', holder_at = ?, "
            "updated_at = ? WHERE id = ?",
            _ago(0.1), (datetime(2026, 9, 10, tzinfo=timezone.utc) + timedelta(minutes=i))
            .strftime("%Y-%m-%dT%H:%M:%SZ"), tid)
        case.healthy.append(tid)
    a, s, r = new("брошена A"), new("брошена S"), new("брошена R")
    sql("UPDATE tasks SET status = 'in_progress', holder = NULL WHERE id = ?", a)
    sql("UPDATE tasks SET status = 'in_progress', holder = 'dsh', holder_at = ? WHERE id = ?",
        _ago(30), s)
    sql("UPDATE tasks SET status = 'review', holder = 'dsh', holder_at = ? WHERE id = ?",
        _ago(30), r)
    for tid, ts in ((a, "2026-09-01T10:00:00Z"), (s, "2026-08-31T10:00:00Z"),
                    (r, "2026-08-30T10:00:00Z")):
        sql("UPDATE tasks SET updated_at = ? WHERE id = ?", ts, tid)
    case.a, case.s, case.r = a, s, r
    case.dead = [a, s, r]
    case.oldest_healthy = case.healthy[0]

    def health(tid: str) -> str:
        return store.task_health(store.get_task(conn, tid, with_details=False))

    for tid in case.dead:
        case.assertEqual(health(tid), "dead", tid)
    for tid in (case.oldest_healthy, case.healthy[50], case.healthy[-1]):
        case.assertNotEqual(health(tid), "dead", tid)


def task_ids(payload: dict) -> list[str]:
    return [t["id"] for t in payload["tasks"]]


class ListStaleLocalTests(CliErrorCase):
    def setUp(self) -> None:
        super().setUp()
        seed(self)

    def list_json(self, *args) -> dict:
        p = self.run_cli("list", "-p", "demo", *args, "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def test_stale_found_past_limit(self) -> None:
        res = self.list_json("--stale", "-n", "10")
        self.assertEqual(task_ids(res), self.dead)
        self.assertEqual(res["total"], 3)
        for t in res["tasks"]:
            self.assertTrue(t["stale"] or t["abandoned"], t["id"])

    def test_stale_limit_cuts_filtered(self) -> None:
        res = self.list_json("--stale", "-n", "2")
        self.assertEqual(task_ids(res), self.dead[:2])
        self.assertEqual(res["total"], 3)

    def test_stale_text(self) -> None:
        p = self.run_cli("list", "-p", "demo", "--stale", "-n", "10")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        for tid in self.dead:
            self.assertIn(tid, p.stdout)
        self.assertNotIn(self.oldest_healthy, p.stdout)
        for tid in self.healthy:
            self.assertNotIn(tid, p.stdout)
        self.assertNotIn("пусто", p.stdout)

    def test_stale_with_running_status(self) -> None:
        self.assertEqual(task_ids(self.list_json("--stale", "--status", "review")), [self.r])
        self.assertEqual(task_ids(self.list_json("--stale", "--status", "in_progress")),
                         [self.a, self.s])

    def test_stale_with_other_status_json(self) -> None:
        p = self.run_cli("list", "-p", "demo", "--stale", "--status", "open", "--json")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        err = self.error_json(p)
        self.assertEqual(err["code"], "bad_argument")
        for part in ("--stale", "--status", "open"):
            self.assertIn(part, err["message"])
        self.assertTrue(err["hint"])
        self.assertNotIn("Traceback", p.stderr)

    def test_stale_with_other_status_text(self) -> None:
        p = self.run_cli("list", "-p", "demo", "--stale", "--status", "open")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        first = next(line for line in p.stderr.splitlines() if line.strip())
        self.assertTrue(first.startswith("ошибка:"), p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_plain_list_unchanged(self) -> None:
        res = self.list_json("-n", "10")
        self.assertEqual(len(res["tasks"]), 10)
        self.assertEqual(res["total"], 103)


class ListStaleHttpTests(TempDbTestCase):
    """Тот же отбор через `GET /api/tasks`: сервер в процессе теста, CLI без `--local`."""

    TOKEN = "test-token"

    def setUp(self) -> None:
        super().setUp()
        self._saved = (paths.DB_PATH, paths.CONFIG_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = self.tmp_path / "config.toml"
        paths.CONFIG_PATH.write_text(f'[auth]\ntoken = "{self.TOKEN}"\n', encoding="utf-8")
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        seed(self)

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        (paths.DB_PATH, paths.CONFIG_PATH, server._conn_made, server._conn_local) = self._saved
        super().tearDown()

    def run_cli(self, *args):
        env = {**os.environ, "LISTIK_CONFIG": str(self.tmp_path / "config.toml"),
               "LISTIK_DB": str(self.db_path), "LISTIK_LOG": str(self.tmp_path / "cli.log")}
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--port", str(self.port), *args],
            capture_output=True, text=True, env=env, cwd=str(LISTIK_BIN.parent.parent))

    def test_stale_found_past_limit(self) -> None:
        p = self.run_cli("list", "-p", "demo", "--stale", "-n", "10", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("сервер Listik не отвечает", p.stderr)
        res = json.loads(p.stdout)
        self.assertEqual(task_ids(res), self.dead)
        self.assertEqual(res["total"], 3)
