"""`list -t` через HTTP и итоговая строка `listik list` по `total` (listik-jcu0, listik-71c2).

CLI шлёт тип в `GET /api/tasks` параметром `issue_type`, сервер читал только `type` — фильтр
по типу молча терялся на HTTP-пути. Итоговая строка печатала размер страницы, а не `total`.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
from unittest import mock

from listik import paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from tests.test_cli_errors import CliErrorCase


def _load_cli():
    """Загрузить `bin/listik` как модуль — у файла нет расширения `.py`."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_type_total_test", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


CLI = _load_cli()


def new(conn, title: str, issue_type: str) -> str:
    return store.create_task(conn, title=title, project="demo", issue_type=issue_type)["id"]


def ids(res: dict) -> set[str]:
    return {t["id"] for t in res["tasks"]}


class HandlerTypeAliasTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.bug = new(self.conn, "баг", "bug")
        self.task = new(self.conn, "задача", "task")

    def get(self, query: dict):
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            return server.handle("GET", "/api/tasks", query, {}, authed=True)

    def test_issue_type_filters(self) -> None:
        status, res = self.get({"issue_type": "bug"})
        self.assertEqual(status, 200)
        self.assertEqual(ids(res), {self.bug})
        self.assertEqual(res["total"], 1)

    def test_empty_type_keeps_issue_type(self) -> None:
        status, res = self.get({"type": "", "issue_type": "bug"})
        self.assertEqual(status, 200)
        self.assertEqual(ids(res), {self.bug})
        self.assertEqual(res["total"], 1)

    def test_type_filters(self) -> None:
        status, res = self.get({"type": "bug"})
        self.assertEqual(status, 200)
        self.assertEqual(ids(res), {self.bug})
        self.assertEqual(res["total"], 1)

    def test_type_wins_over_issue_type(self) -> None:
        status, res = self.get({"type": "bug", "issue_type": "task"})
        self.assertEqual(status, 200)
        self.assertEqual(ids(res), {self.bug})


class PrintTasksTotalTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        new(self.conn, "первая", "task")
        new(self.conn, "вторая", "bug")
        self.tasks = store.list_tasks(self.conn)["tasks"]

    def output(self, *args, **kwargs) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            CLI.print_tasks(*args, **kwargs)
        return buf.getvalue()

    def last_line(self, *args, **kwargs) -> str:
        return [line for line in self.output(*args, **kwargs).splitlines() if line.strip()][-1]

    def test_no_total_counts_tasks(self) -> None:
        self.assertEqual(self.last_line(self.tasks), "всего: 2")

    def test_total_above_page(self) -> None:
        self.assertEqual(self.last_line(self.tasks, total=5), "показано 2 из 5")

    def test_total_equal_page(self) -> None:
        self.assertEqual(self.last_line(self.tasks, total=2), "всего: 2")

    def test_empty_with_total(self) -> None:
        # Пустая страница при известном полном числе (--offset за концом выборки):
        # «пусто» соврало бы, задачи есть — listik-n1aw.
        out = self.output([], total=3)
        self.assertNotIn("пусто", out)
        self.assertNotIn("всего", out)
        self.assertEqual(self.last_line([], total=3), "показано 0 из 3")

    def test_empty_no_total(self) -> None:
        for total in (None, 0):
            with self.subTest(total=total):
                out = self.output([], total=total)
                self.assertIn("пусто", out)
                self.assertNotIn("показано", out)


class ListTypeHttpTests(TempDbTestCase):
    """`listik list -t` через `GET /api/tasks`: сервер в процессе теста, CLI без `--local`."""

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
        self.bugs = {new(self.conn, "баг 1", "bug"), new(self.conn, "баг 2", "bug")}
        self.task = new(self.conn, "задача", "task")

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

    def test_type_filter_json(self) -> None:
        p = self.run_cli("list", "-p", "demo", "-t", "bug", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("сервер Listik не отвечает", p.stderr)
        res = json.loads(p.stdout)
        self.assertEqual(ids(res), self.bugs)
        self.assertEqual(res["total"], 2)

    def test_type_filter_text_total(self) -> None:
        p = self.run_cli("list", "-p", "demo", "-t", "bug", "-n", "1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("показано 1 из 2", p.stdout)
        self.assertNotIn(self.task, p.stdout)


class ListTotalLocalTests(CliErrorCase):
    def setUp(self) -> None:
        super().setUp()
        self.bug = new(self.conn, "баг", "bug")
        new(self.conn, "задача 1", "task")
        new(self.conn, "задача 2", "task")

    def test_page_below_total(self) -> None:
        p = self.run_cli("list", "-p", "demo", "-n", "2")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("показано 2 из 3", p.stdout)
        self.assertNotIn("всего: 2", p.stdout)

    def test_full_page(self) -> None:
        p = self.run_cli("list", "-p", "demo")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("всего: 3", p.stdout)
        self.assertNotIn("показано", p.stdout)

    def test_type_filter_json(self) -> None:
        p = self.run_cli("list", "-p", "demo", "-t", "bug", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        res = json.loads(p.stdout)
        self.assertEqual(ids(res), {self.bug})
        self.assertEqual(res["total"], 1)

    def test_empty_project(self) -> None:
        p = self.run_cli("list", "-p", "нет-такого")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("пусто", p.stdout)
        self.assertNotIn("всего", p.stdout)
        self.assertNotIn("показано", p.stdout)
