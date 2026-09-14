"""Единый формат ошибок CLI: текст человеку, JSON агенту (карточка listik-s8rq).

Проверяются все три источника ошибок:

* разбор аргументов (`ListikParser.error` вместо usage-простыни);
* ответ сервера (HTTP 404/409 → not_found/conflict);
* локальный режим, когда сервер не поднят и CLI идёт в базу напрямую.

Формат один: без `--json` — одна-две строки «ошибка: <что> — <как исправить>»
в stderr, с `--json` — объект `{"error": {code, message, hint}}` в stdout и
ненулевой код возврата. Трейсбек непойманного исключения — только в listik.log.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import unittest

from listik import paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN


class CliErrorCase(TempDbTestCase):
    """CLI в локальном режиме: временная база, временный лог, никакой сети."""

    def run_cli(self, *args, db=None, **env_extra):
        env = {**os.environ, "LISTIK_DB": str(db or self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log"), **env_extra}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def error_json(self, proc) -> dict:
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        # stdout — чистый JSON: агент парсит его, а не читает stderr.
        return json.loads(proc.stdout)["error"]

    def assert_clean_stderr(self, proc) -> None:
        self.assertNotIn("usage:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def log_text(self) -> str:
        log = self.tmp_path / "listik.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""


class ArgparseErrorTests(CliErrorCase):
    """Пункт приёмки 2: ошибка аргументов — тот же JSON, что и у API."""

    def test_bad_choice_json(self) -> None:
        p = self.run_cli("new", "проба", "--stage", "s9", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("--stage", err["message"])
        self.assertTrue(err["hint"])
        self.assert_clean_stderr(p)

    def test_missing_argument_json(self) -> None:
        p = self.run_cli("new", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("title", err["message"])
        self.assert_clean_stderr(p)

    def test_unknown_flag_json(self) -> None:
        p = self.run_cli("list", "--no-such-flag", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("--no-such-flag", err["message"])

    def test_text_error_is_short_and_without_usage(self) -> None:
        p = self.run_cli("new", "проба", "--stage", "s9")
        self.assertNotEqual(p.returncode, 0)
        lines = [line for line in p.stderr.splitlines() if line.strip()]
        self.assertLessEqual(len(lines), 2, p.stderr)
        self.assertTrue(lines[0].startswith("ошибка: "), p.stderr)
        self.assert_clean_stderr(p)
        self.assertEqual(p.stdout, "")


class PriorityTests(CliErrorCase):
    """Пункт приёмки 4: `--priority` принимает и P2, и 2 (`new`, `set`)."""

    def test_new_accepts_label_and_number(self) -> None:
        for given, expected in (("P2", 2), ("2", 2), ("P0", 0), ("0", 0), ("p4", 4)):
            p = self.run_cli("new", f"задача {given}", "-p", "demo", "--priority", given, "--json")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["priority"], expected)

    def test_set_accepts_label_and_number(self) -> None:
        task = store.create_task(self.conn, title="проба", project="demo")["id"]
        for given, expected in (("P1", 1), ("3", 3)):
            p = self.run_cli("set", task, f"priority={given}", "--json")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(json.loads(p.stdout)["priority"], expected)

    def test_bad_priority_explains_the_range(self) -> None:
        for bad in ("P9", "5", "высокий"):
            p = self.run_cli("new", "проба", "--priority", bad, "--json")
            err = self.error_json(p)
            self.assertEqual(err["code"], "bad_argument")
            self.assertIn("0–4", err["message"])
            self.assertIn("P0–P4", err["message"])
            self.assertTrue(err["hint"])
            self.assert_clean_stderr(p)

    def test_bad_priority_in_set_json(self) -> None:
        task = store.create_task(self.conn, title="проба", project="demo")["id"]
        p = self.run_cli("set", task, "priority=P9", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("0–4", err["message"])


class LocalModeErrorTests(CliErrorCase):
    """Пункт приёмки 2: локальный режим (`local_call`) даёт тот же формат."""

    def test_not_found_json(self) -> None:
        p = self.run_cli("show", "demo-nope", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "not_found")
        self.assertIn("не найдена", err["message"])
        self.assertNotIn("'", err["message"])
        self.assertTrue(err["hint"])
        self.assert_clean_stderr(p)

    def test_not_found_text(self) -> None:
        p = self.run_cli("show", "demo-nope")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("ошибка: ", p.stderr)
        self.assertIn("не найдена", p.stderr)
        self.assert_clean_stderr(p)
        self.assertEqual(p.stdout, "")

    def test_conflict_json(self) -> None:
        task = store.create_task(self.conn, title="занята", project="demo")["id"]
        first = self.run_cli("claim", task, "--holder", "dsh", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        p = self.run_cli("claim", task, "--holder", "grok", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "conflict")
        self.assertIn("удерживается", err["message"])
        self.assert_clean_stderr(p)


class InternalErrorTests(CliErrorCase):
    """Пункт приёмки 3: непойманное исключение — JSON, трейсбек только в лог."""

    def _broken_db(self):
        # LISTIK_DB — каталог: sqlite не откроет его, и это исключение никто не ловит.
        return self.tmp_path

    def test_json_error_and_traceback_in_log(self) -> None:
        p = self.run_cli("list", "--json", db=self._broken_db())
        err = self.error_json(p)
        self.assertEqual(err["code"], "internal")
        self.assertIn("OperationalError", err["message"])
        self.assertIn("listik.log", err["hint"])
        self.assertNotIn("Traceback", p.stderr)
        self.assertNotIn("Traceback", p.stdout)
        log = self.log_text()
        self.assertIn("Traceback", log)
        self.assertIn("OperationalError", log)

    def test_text_error_has_no_traceback(self) -> None:
        p = self.run_cli("list", db=self._broken_db())
        self.assertNotEqual(p.returncode, 0)
        lines = [line for line in p.stderr.splitlines() if line.strip()]
        self.assertLessEqual(len(lines), 2, p.stderr)
        self.assertTrue(lines[0].startswith("ошибка: "), p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        self.assertIn("Traceback", self.log_text())


class ApiErrorTests(TempDbTestCase):
    """Пункт приёмки 2: ошибка API — тот же JSON, сервер отдаёт свой `code`."""

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

    def error_json(self, proc) -> dict:
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)["error"]

    def test_404_from_api(self) -> None:
        p = self.run_cli("show", "demo-nope", "--json")
        err = self.error_json(p)
        self.assertEqual(err["code"], "not_found")
        self.assertIn("не найдена", err["message"])
        self.assertNotIn("'", err["message"])  # без кавычек KeyError
        self.assertNotIn("Traceback", p.stderr)

    def test_conflict_from_api(self) -> None:
        task = store.create_task(self.conn, title="занята", project="demo")["id"]
        first = self.run_cli("claim", task, "--holder", "dsh", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        p = self.run_cli("claim", task, "--holder", "grok", "--json")
        err = self.error_json(p)
        # HTTP-статус у claim — 400, но код сервер отдаёт по смыслу: conflict.
        self.assertEqual(err["code"], "conflict")

    def test_api_errors_carry_code(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            server.handle("GET", "/api/tasks/demo-nope", {}, {}, authed=True)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(ctx.exception.code, "not_found")


if __name__ == "__main__":
    unittest.main()
