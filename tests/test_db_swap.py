"""listik-cfzk: сервер замечает подмену файла базы или WAL.

Проверяется пункт приёмки 2. Подмена и удаление ловятся по inode файла базы/WAL
(а DatabaseError — по исключению), все соединения переоткрываются на новый файл,
а факт подмены виден в `/api/health`, `listik status` и listik.log.

Глобальное состояние `listik.server` изолируется на каждый тест: база — своя
временная (`paths.DB_PATH`), конфиг — временный, реестр соединений и поколение
схемы сбрасываются.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from listik import backup as backup_mod
from listik import db as db_mod
from listik import embed as embed_mod
from listik import errors, paths, server, store

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"


def _load_cli():
    """Загрузить `bin/listik` как модуль (у файла нет расширения .py)."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_swap_under_test",
                                                  str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class SwapStateCase(unittest.TestCase):
    """Своя временная база и чистое глобальное состояние server на каждый тест."""

    _GLOBALS = ("_conn_local", "_conn_made", "_conns", "_db_generation",
                "_schema_generation", "_fingerprint", "_fingerprint_path", "_db_replaced", "_db_error",
                "_last_watch", "_dbwatch_stop", "_dbwatch_thread")

    def setUp(self) -> None:
        self._saved = {name: getattr(server, name) for name in self._GLOBALS}
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = pathlib.Path(self._tmp.name)
        self.db_path = self.tmp_path / "listik.db"
        self.config_path = self.tmp_path / "config.toml"
        self.config_path.write_text('[auth]\ntoken = "swap-test"\n', encoding="utf-8")

        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = self.config_path
        server._conn_local = threading.local()
        server._conn_made = False
        server._conns = {}
        server._db_generation = 0
        server._schema_generation = -1
        server._fingerprint = None
        server._fingerprint_path = None
        server._db_replaced = None
        server._db_error = None
        server._last_watch = 0.0
        server._dbwatch_stop = threading.Event()
        server._dbwatch_thread = None
        self.addCleanup(self._restore_state)
        # Строки «[watch] …» — ожидаемый вывод подмены; держим их в буфере теста.
        self.watch_output = io.StringIO()
        redirect = contextlib.redirect_stdout(self.watch_output)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

    def _restore_state(self) -> None:
        # Сначала остановить поток этого теста, пока глобалы ещё его (listik-2gn8).
        self.assertTrue(server.stop_db_watch(), "поток надзора не остановился")
        for conn in list(server._conns.values()) + [getattr(server._conn_local, "conn", None)]:
            if conn is None:
                continue
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        for name, value in self._saved.items():
            setattr(server, name, value)
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths

    # --- помощники
    def make_task(self, title: str) -> sqlite3.Connection:
        conn = server.get_conn()
        store.create_task(conn, title=title, project="listik")
        conn.commit()
        return conn

    def swap_db_file(self) -> None:
        """Подменить файл базы, как это делает restore: у файла новый inode."""
        other = self.tmp_path / "other.db"
        db_mod.init(other).close()
        os.replace(other, self.db_path)
        for side in backup_mod.sidecar_paths(self.db_path):
            side.unlink(missing_ok=True)


class WatchTests(SwapStateCase):
    """Подмена файла базы и WAL ловится по inode, соединения переоткрываются."""

    def test_swap_of_db_file_is_detected_and_connections_reopen(self) -> None:
        old = self.make_task("до подмены")
        server._watch_tick(force=True)  # базовая линия
        self.swap_db_file()

        event = server._watch_tick(force=True)
        self.assertIsNotNone(event)
        self.assertTrue(event["kind"].startswith("db"), event["kind"])
        self.assertIn("заменён", event["detail"])
        self.assertEqual(server._db_generation, 1)
        self.assertTrue(server._db_replaced["kind"].startswith("db"))
        self.assertEqual(server._db_error["where"], "watch")

        # Старое соединение закрыто, новое смотрит на подменённый файл.
        with self.assertRaises(sqlite3.ProgrammingError):
            old.execute("SELECT 1")
        new = server.get_conn()
        self.assertIsNot(new, old)
        self.assertEqual(new.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_missing_db_file_is_detected(self) -> None:
        self.make_task("до удаления")
        server._watch_tick(force=True)
        os.unlink(self.db_path)
        for side in backup_mod.sidecar_paths(self.db_path):
            side.unlink(missing_ok=True)

        event = server._watch_tick(force=True)
        self.assertIsNotNone(event)
        self.assertIn("исчез", event["detail"])
        self.assertTrue(event["kind"].startswith("db"), event["kind"])

    def test_wal_disappearance_is_detected_while_connections_open(self) -> None:
        self.make_task("с WAL")
        server._watch_tick(force=True)
        wal = server._wal_path()
        self.assertTrue(wal.exists(), "у открытого соединения WAL должен быть")
        os.unlink(wal)

        event = server._watch_tick(force=True)
        self.assertIsNotNone(event)
        self.assertEqual(event["kind"], "wal")
        self.assertIn("WAL исчез", event["detail"])

    def test_normal_writes_are_not_a_swap(self) -> None:
        conn = self.make_task("раз")
        server._watch_tick(force=True)
        for i in range(5):
            store.create_task(conn, title=f"ещё {i}", project="listik")
        conn.commit()
        self.assertIsNone(server._watch_tick(force=True))
        self.assertIsNone(server._db_replaced)

    def test_closing_last_connection_is_not_a_swap(self) -> None:
        """sqlite сам удаляет -wal вместе с последним соединением — это не подмена."""
        self.make_task("задача")
        server._watch_tick(force=True)
        server.close_thread_conn()
        self.assertIsNone(server._watch_tick(force=True))

    def test_switching_db_path_is_not_a_swap(self) -> None:
        """Другой `paths.DB_PATH` (следующий тест, своя база) — новая базовая линия (listik-mfpl)."""
        self.make_task("в первой базе")
        server._watch_tick(force=True)
        other = self.tmp_path / "next-test.db"
        db_mod.init(other).close()
        paths.DB_PATH = other

        self.assertIsNone(server._watch_tick())  # без force: смена пути не ждёт интервала
        self.assertEqual(server._fingerprint_path, other)
        self.assertIsNone(server._db_replaced)
        self.assertEqual(server._db_generation, 0)
        self.assertNotIn("ПОДМЕНА", self.watch_output.getvalue())

        # Подмена уже нового файла по-прежнему замечается.
        replacement = self.tmp_path / "replacement.db"
        db_mod.init(replacement).close()
        os.replace(replacement, other)
        event = server._watch_tick(force=True)
        self.assertIsNotNone(event)
        self.assertIn("файл базы заменён", event["detail"])


class FingerprintDiffTests(unittest.TestCase):
    """Чистая логика сравнения замеров: что считается подменой, а что нет."""

    def test_nothing_changed(self) -> None:
        self.assertIsNone(server._fingerprint_diff(
            {"db": (1, 2), "wal": (1, 3)}, {"db": (1, 2), "wal": (1, 3)}, 3))

    def test_wal_appearing_is_normal(self) -> None:
        self.assertIsNone(server._fingerprint_diff(
            {"db": (1, 2), "wal": None}, {"db": (1, 2), "wal": (1, 3)}, 1))

    def test_wal_missing_without_connections_is_normal(self) -> None:
        self.assertIsNone(server._fingerprint_diff(
            {"db": (1, 2), "wal": (1, 3)}, {"db": (1, 2), "wal": None}, 0))

    def test_wal_missing_with_connections_is_a_swap(self) -> None:
        event = server._fingerprint_diff(
            {"db": (1, 2), "wal": (1, 3)}, {"db": (1, 2), "wal": None}, 1)
        self.assertEqual(event["kind"], "wal")
        self.assertIn("исчез", event["detail"])

    def test_wal_replaced(self) -> None:
        event = server._fingerprint_diff(
            {"db": (1, 2), "wal": (1, 3)}, {"db": (1, 2), "wal": (1, 8)}, 1)
        self.assertEqual(event["kind"], "wal")
        self.assertIn("WAL заменён", event["detail"])

    def test_db_and_wal_replaced_together(self) -> None:
        event = server._fingerprint_diff(
            {"db": (1, 2), "wal": (1, 3)}, {"db": (1, 9), "wal": (1, 8)}, 0)
        self.assertEqual(event["kind"], "db+wal")
        self.assertIn("заменён", event["detail"])


class WatchThreadTests(SwapStateCase):
    """Фоновый поток замечает подмену и без запросов к серверу."""

    def test_watch_thread_detects_swap(self) -> None:
        self.make_task("до подмены")
        server.start_db_watch(interval=0.02)
        self.swap_db_file()

        deadline = time.time() + 5
        while server._db_replaced is None and time.time() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(server._db_replaced, "надзор не заметил подмену файла")
        self.assertIn("заменён", server._db_replaced["detail"])

    def test_stop_ends_thread_and_is_idempotent(self) -> None:
        thread = server.start_db_watch(interval=0.02)
        self.assertTrue(thread.is_alive())
        self.assertTrue(server.stop_db_watch())
        self.assertFalse(thread.is_alive())
        self.assertIsNone(server._dbwatch_thread)
        self.assertTrue(server.stop_db_watch())

    def test_restart_replaces_previous_thread(self) -> None:
        first = server.start_db_watch(interval=0.02)
        second = server.start_db_watch(interval=0.02)
        self.assertFalse(first.is_alive())
        self.assertTrue(second.is_alive())
        self.assertTrue(server.stop_db_watch())
        self.assertFalse(second.is_alive())

    def test_serve_stops_watch_on_shutdown(self) -> None:
        started: list[threading.Thread] = []
        real_start = server.start_db_watch

        def fake_httpd(*_a, **_k):
            httpd = mock.Mock()
            httpd.serve_forever.side_effect = KeyboardInterrupt
            return httpd

        def start(*a, **k):
            started.append(real_start(interval=0.02))
            return started[-1]

        with mock.patch.object(server, "bind_or_explain", side_effect=fake_httpd), \
                mock.patch.object(server, "start_db_watch", side_effect=start), \
                mock.patch.object(server.launcher_mod, "recover"), \
                mock.patch.object(server, "pid_file", return_value=self.tmp_path / "pid"), \
                contextlib.redirect_stdout(io.StringIO()):
            server.serve(no_embed=True, quiet=True)
        self.assertEqual(len(started), 1)
        self.assertFalse(started[0].is_alive(), "поток надзора жив после shutdown")
        self.assertIsNone(server._dbwatch_thread)


class ReportTests(SwapStateCase):
    """Подмена видна в health, DatabaseError — в health и в ответе API."""

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(embed_mod, "health",
                                  return_value={"ok": False, "model": "тест"})
        patch.start()
        self.addCleanup(patch.stop)

    def test_health_reports_replacement(self) -> None:
        self.make_task("до подмены")
        server._watch_tick(force=True)
        self.swap_db_file()
        server._watch_tick(force=True)

        status, body = server.handle("GET", "/api/health", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertIn("db_replaced", body)
        self.assertTrue(body["db_replaced"]["kind"].startswith("db"),
                        body["db_replaced"]["kind"])
        self.assertIn("заменён", body["db_replaced"]["detail"])
        self.assertTrue(body["db_replaced"]["at"])

    def test_health_hides_replacement_from_anonymous(self) -> None:
        server._db_replaced = {"kind": "db", "at": "2026-09-14T00:00:00Z",
                               "detail": "файл базы заменён"}
        _status, body = server.handle("GET", "/api/health", {}, {}, authed=False)
        self.assertNotIn("db_replaced", body)

    def test_http_database_error_is_loud_and_reopens(self) -> None:
        self.make_task("до ошибки")
        status, message, code = server.error_response(
            sqlite3.DatabaseError("database disk image is malformed"))
        self.assertEqual(status, 503)
        self.assertEqual(code, errors.SERVER_ERROR)
        self.assertIn("malformed", message)
        self.assertEqual(server._db_error["where"], "http")
        self.assertEqual(server._db_generation, 1)

    def test_closed_connection_is_reported_as_retryable(self) -> None:
        status, message, code = server.error_response(
            sqlite3.ProgrammingError("Cannot operate on a closed database"))
        self.assertEqual(status, 503)
        self.assertEqual(code, errors.SERVER_ERROR)
        self.assertIn("повтори запрос", message)

    def test_integrity_error_is_not_a_db_failure(self) -> None:
        status, _message, code = server.error_response(
            sqlite3.IntegrityError("UNIQUE constraint failed"))
        self.assertEqual((status, code), (500, errors.INTERNAL))
        self.assertIsNone(server._db_error)

    def test_successful_background_pass_keeps_replacement_record(self) -> None:
        server._db_replaced = {"kind": "db", "at": "2026-09-14T00:00:00Z",
                               "detail": "файл базы заменён"}
        server._db_error = {"where": "watch", "at": "2026-09-14T00:00:00Z",
                            "error": "файл базы подменён"}
        with mock.patch.object(server, "get_conn", return_value=mock.MagicMock()), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(embed_mod, "embed_pending", return_value={}):
            server._background_pass()
        self.assertIsNone(server._db_error)
        self.assertIsNotNone(server._db_replaced)

    def test_invalidation_closes_foreign_thread_connections(self) -> None:
        conn = self.make_task("в главном потоке")
        box: dict = {}

        def worker() -> None:
            box["conn"] = server.get_conn()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        self.assertIn("conn", box)

        server._background_db_error("http", sqlite3.DatabaseError("disk I/O error"))
        with self.assertRaises(sqlite3.ProgrammingError):
            box["conn"].execute("SELECT 1")
        new = server.get_conn()
        self.assertIsNot(new, conn)
        self.assertEqual(server._db_generation, 1)


class StatusCommandTests(unittest.TestCase):
    """Пункт 2 приёмки: `listik status` говорит о подмене, а не молчит."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cfg = pathlib.Path(self._tmp.name) / "config.toml"
        cfg.write_text('[auth]\ntoken = "status-swap"\n', encoding="utf-8")
        patch = mock.patch.object(paths, "CONFIG_PATH", cfg)
        patch.start()
        self.addCleanup(patch.stop)
        self.cli = _load_cli()

    def _run_status(self, health) -> tuple[int, str]:
        args = argparse.Namespace(host=None, port=None, json=False)
        out = io.StringIO()
        with mock.patch.object(self.cli.client, "health", return_value=health), \
             mock.patch.object(server, "read_pid", return_value=None), \
             contextlib.redirect_stdout(out):
            code = self.cli.cmd_status(args)
        return code, out.getvalue()

    def _health(self, **extra) -> dict:
        return {
            "status": "ok", "authed": True,
            "counts": {"tasks": 1, "comments": 0, "events": 0, "embeddings": 0},
            "db": "/tmp/listik.db", "embed": {"model": "bge-m3", "ok": False},
            "routes": {"ok": True, "error": None, "path": "/tmp/r.json", "count": 11},
            **extra,
        }

    def test_replacement_line(self) -> None:
        code, text = self._run_status(self._health(db_replaced={
            "kind": "db", "at": "2026-09-14T07:00:00Z",
            "detail": "файл базы заменён", "before": {"db": [1, 2], "wal": None},
            "after": {"db": [1, 9], "wal": None}}))
        self.assertEqual(code, 0)
        self.assertIn("ПОДМЕНА ФАЙЛА [db] 2026-09-14T07:00:00Z: файл базы заменён", text)
        self.assertIn("listik.log", text)

    def test_no_line_without_replacement(self) -> None:
        code, text = self._run_status(self._health())
        self.assertEqual(code, 0)
        self.assertNotIn("ПОДМЕНА ФАЙЛА", text)

    def test_no_line_when_token_rejected(self) -> None:
        code, text = self._run_status({"status": "ok", "authed": False, "db_replaced": {
            "kind": "db", "at": "2026-09-14T07:00:00Z", "detail": "файл базы заменён"}})
        self.assertEqual(code, 0)
        self.assertNotIn("ПОДМЕНА ФАЙЛА", text)


if __name__ == "__main__":
    unittest.main()
