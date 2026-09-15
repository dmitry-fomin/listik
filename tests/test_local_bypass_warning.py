"""`listik --local` при живом сервере: предупреждение о событии доски (listik-f6kt).

`--local` — осознанный обход сервера: запись уходит прямо в SQLite, и SSE-кадр доске
не уходит (docs/API.md, «События доске»). Молчать об этом нельзя: агент решит, что доска
обновилась. Поэтому пишущая команда с `--local` и живым сервером печатает в stderr то
же «доска не получит событие», что и фолбэк без сервера, — а stdout с `--json`
остаётся разбираемым JSON.

Чтения при этом молчат: `--local` для них ничего не теряет, и лишний health-запрос
(вместе с предупреждением) им не нужен. Когда сервера нет, `--local` — обычный режим
работы, а не обход, и предупреждения тоже нет.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import unittest

from listik import paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN

WARNING = "доска не получит событие"


class LocalBypassWarningCase(TempDbTestCase):
    """Временная база + живой сервер на случайном порту (не 8787)."""

    #: Глобальное состояние `listik.server`, которое сервер в этом же процессе
    #: успевает потрогать (см. `get_conn` → `_watch_tick`).
    _SERVER_GLOBALS = ("_conn_made", "_conn_local", "_fingerprint", "_db_replaced",
                       "_db_error", "_last_watch")

    def setUp(self) -> None:
        super().setUp()
        self._saved = {name: getattr(server, name) for name in self._SERVER_GLOBALS}
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        paths.DB_PATH = self.db_path
        self.config_path = self.tmp_path / "config.toml"
        paths.CONFIG_PATH = self.config_path
        self.config_path.write_text('[auth]\ntoken = "test-token"\n', encoding="utf-8")
        server._conn_made = False
        server._conn_local = threading.local()
        # Своя база — не «подмена» для надзора за файлом: без сброса отпечатка
        # `get_conn()` печатал бы в вывод тестов чужое «[watch] ПОДМЕНА ФАЙЛА БАЗЫ».
        server._fingerprint = None
        server._db_replaced = None
        server._db_error = None
        server._last_watch = 0.0
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths
        for name, value in self._saved.items():
            setattr(server, name, value)
        super().tearDown()

    @staticmethod
    def free_port() -> int:
        """Порт, который точно никто не слушает: «сервер не поднят» без гонки с 8787."""
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return int(s.getsockname()[1])

    def run_cli(self, *args, local: bool = True, port: int | None = None):
        cmd = [sys.executable, str(LISTIK_BIN)]
        if local:
            cmd.append("--local")
        cmd += ["--port", str(self.port if port is None else port), *args]
        env = {**os.environ, "LISTIK_CONFIG": str(self.config_path),
               "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run(cmd, capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))


class WriteCommandWarnsTests(LocalBypassWarningCase):
    """Приёмка 1: пишущая команда с --local при живом сервере предупреждает."""

    def test_new_warns_and_still_writes(self) -> None:
        p = self.run_cli("new", "проба", "-p", "demo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(WARNING, p.stderr)
        self.assertIn("--local", p.stderr)
        row = self.conn.execute("SELECT title FROM tasks WHERE project = 'demo'").fetchone()
        self.assertIsNotNone(row, "запись не доехала до базы")
        self.assertEqual(row["title"], "проба")

    def test_json_stdout_stays_parsable(self) -> None:
        p = self.run_cli("new", "проба json", "-p", "demo", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        task = json.loads(p.stdout)  # stdout — чистый JSON, предупреждение только в stderr
        self.assertEqual(task["title"], "проба json")
        self.assertIn(WARNING, p.stderr)

    def test_json_error_keeps_stdout_parsable(self) -> None:
        # Ошибка пишущей команды тоже не должна мешать разбору stdout: предупреждение
        # уходит в stderr, а не в JSON.
        p = self.run_cli("comment", "demo-nope", "текст", "-k", "journal", "--json")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["error"]["code"], "not_found")
        self.assertIn(WARNING, p.stderr)

    def test_write_ops_warn_not_only_create(self) -> None:
        task = store.create_task(self.conn, title="проба", project="demo")["id"]
        cases = (
            ("comment", task, "текст", "-k", "journal"),
            ("claim", task, "--holder", "dsh"),
            ("heartbeat", task, "--holder", "dsh", "--note", "работаю"),
            ("set", task, "priority=P1"),
            ("stage", task),
        )
        for args in cases:
            with self.subTest(cmd=args[0]):
                p = self.run_cli(*args)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertIn(WARNING, p.stderr, args)

    def test_projects_write_warns_and_read_is_silent(self) -> None:
        store.add_project(self.conn, path=str(self.tmp_path), slug="demo")
        p = self.run_cli("projects", "demo", "--routing", "{}")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(WARNING, p.stderr)

        quiet = self.run_cli("projects", "demo")
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(quiet.stderr, "")


class ReadCommandSilentTests(LocalBypassWarningCase):
    """Приёмка 2: чтения с --local молчат (и не опрашивают сервер зря)."""

    def test_reads_do_not_warn(self) -> None:
        task = store.create_task(self.conn, title="проба", project="demo",
                                 stage="s3-impl", description="д")["id"]
        reads = (
            ("show", task),
            ("list",),
            ("board",),
            ("stats",),
            ("ready",),
            ("context", task, "--stage", "s3-impl"),
            ("dep", "tree", task),
            ("timeline",),
        )
        for args in reads:
            with self.subTest(cmd=args[0]):
                p = self.run_cli(*args)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(p.stderr, "", args)

    def test_reads_stay_silent_with_json(self) -> None:
        task = store.create_task(self.conn, title="проба", project="demo")["id"]
        for args in (("show", task, "--json"), ("list", "--json")):
            with self.subTest(cmd=args[0]):
                p = self.run_cli(*args)
                self.assertEqual(p.returncode, 0, p.stderr)
                self.assertEqual(p.stderr, "", args)
                json.loads(p.stdout)
                self.assertNotIn(WARNING, p.stdout)


class ServerDownTests(LocalBypassWarningCase):
    """Без сервера --local — обычный режим: предупреждения об обходе нет."""

    def test_local_write_without_server_is_silent(self) -> None:
        p = self.run_cli("new", "проба", "-p", "demo", port=self.free_port())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr, "")

    def test_fallback_warning_still_works_without_local(self) -> None:
        # Существующее поведение не сломано: без --local и без сервера CLI
        # предупреждает, что ушёл в базу напрямую.
        p = self.run_cli("new", "проба", "-p", "demo", local=False, port=self.free_port())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("не отвечает", p.stderr)
        self.assertIn(WARNING, p.stderr)


class ServerUpWithoutLocalTests(LocalBypassWarningCase):
    """Живой сервер + команда без --local: запись идёт через сервер, шума нет."""

    def test_no_warning_when_going_through_server(self) -> None:
        p = self.run_cli("new", "проба", "-p", "demo", "--json", local=False)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stderr, "")
        self.assertEqual(json.loads(p.stdout)["title"], "проба")


if __name__ == "__main__":
    unittest.main()
