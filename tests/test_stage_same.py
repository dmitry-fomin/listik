"""`stage --to <текущий этап>` — no-op, и голый KeyError — не «не найдено» (listik-xut1).

Баг выглядел так: после release карточка стоит на `s3-impl` без держателя,
оркестратор просит `listik stage <id> --to s3-impl --note "…"` и получает
«ошибка: assignee_title — проверь идентификатор: listik list». Внутри no-op
`update_task` возвращал сырую строку таблицы, CLI в `task_line` брал производную
подпись `t['assignee_title']`, которой у строки нет, а любой `KeyError` в
`errors.code_of` считался «не найдено».

Проверяем приёмку карточки:

1. тот же этап — no-op с понятным сообщением, заметка уходит в историю,
   держатель и `stage_at` не трогаются;
2. голый `KeyError` — `internal` (500 на сервере, трейсбек в listik.log), а не
   `not_found` с подсказкой «проверь идентификатор»;
3. сценарий release → `stage --to <текущий>` целиком, через CLI.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import threading
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

from listik import errors, paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from tests.test_cli_errors import CliErrorCase


def _events(conn, task_id: str, kind: str):
    return conn.execute(
        "SELECT * FROM events WHERE task_id = ? AND kind = ? ORDER BY id", (task_id, kind)
    ).fetchall()


class SameStageStoreTests(TempDbTestCase):
    """Пункт приёмки 1: `next_stage(to_stage=<текущий>)` ничего не меняет."""

    def setUp(self) -> None:
        super().setUp()
        self.tid = store.create_task(self.conn, title="проба", project="demo")["id"]
        store.claim(self.conn, self.tid, holder="dsh", harness="dsh")
        store.next_stage(self.conn, self.tid, to_stage="s3-impl", holder="dsh", harness="dsh")

    def test_same_stage_keeps_card_and_holder(self) -> None:
        before = store.get_task(self.conn, self.tid)
        out = store.next_stage(self.conn, self.tid, to_stage="s3-impl", holder="dsh",
                               harness="dsh", note="повтор после release")
        self.assertTrue(out["stage_unchanged"])
        self.assertTrue(out["unchanged"])
        self.assertIn("s3-impl", out["message"])
        self.assertIn("заметка записана", out["message"])

        after = store.get_task(self.conn, self.tid)
        self.assertEqual(after["stage"], "s3-impl")
        # handoff по умолчанию не должен снять держателя у задачи, которая никуда не поехала
        self.assertEqual(after["holder"], "dsh")
        self.assertEqual(after["stage_at"], before["stage_at"])
        # событие stage только одно — вход в s3-impl; перехода s3-impl → s3-impl нет
        stages = _events(self.conn, self.tid, "stage")
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[0]["to_value"], "s3-impl")

    def test_same_stage_records_note_in_history(self) -> None:
        store.next_stage(self.conn, self.tid, to_stage="s3-impl", note="вернул на тот же этап")
        notes = [r["note"] for r in _events(self.conn, self.tid, "note")]
        self.assertIn("вернул на тот же этап", notes)

    def test_same_stage_without_note_writes_no_events(self) -> None:
        before = len(self.conn.execute(
            "SELECT 1 FROM events WHERE task_id = ?", (self.tid,)).fetchall())
        out = store.next_stage(self.conn, self.tid, to_stage="s3-impl")
        after = len(self.conn.execute(
            "SELECT 1 FROM events WHERE task_id = ?", (self.tid,)).fetchall())
        self.assertTrue(out["stage_unchanged"])
        self.assertNotIn("заметка", out["message"])
        self.assertEqual(before, after)

    def test_other_stage_still_moves(self) -> None:
        """No-op только для текущего этапа: явный другой этап работает как раньше."""
        out = store.next_stage(self.conn, self.tid, to_stage="s1-spec", holder="dsh",
                               harness="dsh")
        self.assertEqual(out["stage"], "s1-spec")
        self.assertFalse(out.get("stage_unchanged"))


class ReleaseThenSameStageCliTests(CliErrorCase):
    """Пункт приёмки 3: release → `stage --to` тот же этап, локальный режим CLI."""

    def _prepared(self) -> str:
        created = self.run_cli("new", "проба", "-p", "demo", "--json")
        self.assertEqual(created.returncode, 0, created.stderr)
        tid = json.loads(created.stdout)["id"]
        self.assertEqual(self.run_cli("claim", tid, "--holder", "dsh", "--json").returncode, 0)
        self.assertEqual(self.run_cli("stage", tid, "--to", "s3-impl", "--json").returncode, 0)
        released = self.run_cli("release", tid)
        self.assertEqual(released.returncode, 0, released.stderr)
        return tid

    def test_same_stage_after_release_is_quiet_noop(self) -> None:
        tid = self._prepared()
        p = self.run_cli("stage", tid, "--to", "s3-impl", "--note", "повтор после release")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("ошибка", p.stdout + p.stderr)
        self.assertIn("этап не менялся", p.stdout)
        self.assert_clean_stderr(p)

        card = json.loads(self.run_cli("show", tid, "--json").stdout)
        self.assertEqual(card["stage"], "s3-impl")
        self.assertEqual(card["holder"], "")
        self.assertIn("повтор после release", [e["note"] for e in card["events"]])
        self.assertEqual(
            [e for e in card["events"] if e["kind"] == "stage" and e["from_value"] == "s3-impl"],
            [])

    def test_same_stage_json_reports_unchanged(self) -> None:
        tid = self._prepared()
        p = self.run_cli("stage", tid, "--to", "s3-impl", "--note", "проба", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout)
        self.assertTrue(out["stage_unchanged"])
        self.assertTrue(out["unchanged"])
        self.assertIn("s3-impl", out["message"])


class KeyErrorIsNotNotFoundTests(unittest.TestCase):
    """Пункт приёмки 2: голый KeyError не притворяется «не найдено»."""

    def test_bare_keyerror_is_internal(self) -> None:
        err = errors.as_error(KeyError("assignee_title"))
        self.assertEqual(err.code, errors.INTERNAL)
        self.assertEqual(err.message, "KeyError: assignee_title")
        self.assertNotIn("проверь идентификатор", err.hint)

    def test_not_found_class_keeps_not_found(self) -> None:
        err = errors.as_error(errors.NotFound("задача не найдена: demo-nope"))
        self.assertEqual(err.code, errors.NOT_FOUND)
        self.assertEqual(err.message, "задача не найдена: demo-nope")
        self.assertIn("проверь идентификатор", err.hint)

    def test_value_error_still_conflict(self) -> None:
        self.assertEqual(errors.code_of(ValueError("занято")), errors.CONFLICT)

    def test_server_maps_bare_keyerror_to_500_internal(self) -> None:
        status, message, code = server.error_response(KeyError("assignee_title"))
        self.assertEqual((status, code), (500, errors.INTERNAL))
        self.assertEqual(message, "KeyError: assignee_title")

    def test_server_keeps_not_found_404(self) -> None:
        exc = errors.NotFound("задача не найдена: demo-nope")
        self.assertEqual(server.api_error(404, exc).status, 404)
        self.assertEqual(server.api_error(404, exc).code, errors.NOT_FOUND)


class ServerKeyErrorHttpTests(TempDbTestCase):
    """Тот же KeyError через HTTP и CLI: 500/internal без «проверь идентификатор»."""

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
        import subprocess
        env = {**os.environ, "LISTIK_CONFIG": str(self.tmp_path / "config.toml"),
               "LISTIK_DB": str(self.db_path), "LISTIK_LOG": str(self.tmp_path / "cli.log")}
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--port", str(self.port), *args],
            capture_output=True, text=True, env=env, cwd=str(LISTIK_BIN.parent.parent))

    def test_bare_keyerror_from_handler(self) -> None:
        tid = store.create_task(self.conn, title="проба", project="demo")["id"]
        with mock.patch.object(store, "next_stage", side_effect=KeyError("assignee_title")):
            p = self.run_cli("stage", tid, "--to", "s2-review", "--json")
        self.assertNotEqual(p.returncode, 0, p.stdout + p.stderr)
        err = json.loads(p.stdout)["error"]
        self.assertEqual(err["code"], errors.INTERNAL)
        self.assertIn("assignee_title", err["message"])
        self.assertNotIn("проверь идентификатор", err["hint"])

    def test_same_stage_over_http_is_noop(self) -> None:
        """Тот же сценарий, но через поднятый сервер (серверная ветка next_stage)."""
        tid = store.create_task(self.conn, title="проба", project="demo")["id"]
        for args in (("claim", tid, "--holder", "dsh"), ("stage", tid, "--to", "s3-impl"),
                     ("release", tid)):
            p = self.run_cli(*args, "--json")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        p = self.run_cli("stage", tid, "--to", "s3-impl", "--note", "повтор", "--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        out = json.loads(p.stdout)
        self.assertTrue(out["stage_unchanged"])
        self.assertEqual(out["stage"], "s3-impl")
        self.assertEqual(out["holder"], "")
        card = json.loads(self.run_cli("show", tid, "--json").stdout)
        self.assertIn("повтор", [e["note"] for e in card["events"]])
        self.assertEqual(
            [e for e in card["events"] if e["kind"] == "stage" and e["from_value"] == "s3-impl"],
            [])
        self.assertNotIn("Traceback", p.stderr)


class LocalKeyErrorCliTests(TempDbTestCase):
    """Локальный режим: непойманный KeyError — internal, трейсбек только в listik.log."""

    def _cli(self):
        # bin/listik — исполняемый файл без .py: загрузчик задаём явно.
        loader = SourceFileLoader("listik_cli_xut1", str(LISTIK_BIN))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def test_bare_keyerror_reported_as_internal(self) -> None:
        cli = self._cli()
        tid = store.create_task(self.conn, title="проба", project="demo")["id"]
        log = self.tmp_path / "cli.log"
        out = io.StringIO()
        with mock.patch.object(paths, "DB_PATH", self.db_path), \
             mock.patch.object(paths, "LOG_PATH", log), \
             mock.patch.object(store, "next_stage", side_effect=KeyError("assignee_title")), \
             contextlib.redirect_stdout(out):
            rc = cli.main(["--local", "stage", tid, "--to", "s2-review", "--json"])
        self.assertNotEqual(rc, 0)
        err = json.loads(out.getvalue())["error"]
        self.assertEqual(err["code"], errors.INTERNAL)
        self.assertIn("assignee_title", err["message"])
        self.assertNotIn("проверь идентификатор", err["hint"])
        self.assertIn(str(log), err["hint"])
        self.assertIn("Traceback", log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
