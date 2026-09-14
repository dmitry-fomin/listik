"""listik-9csm: health не должен показывать db_error после восстановления базы.

`_db_error` — это здоровье базы «прямо сейчас», а не история: DatabaseError фонового
прохода (`documents`/`embed`) его ставит, первый же проход без DatabaseError — снимает.
Проверяются все три пункта приёмки: сброс после успешного прохода, поведение
`/api/health` и строка `listik status`.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import pathlib
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock

from listik import embed as embed_mod
from listik import paths, server
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"

HEALTH_BASE = {
    "status": "ok", "authed": True,
    "counts": {"tasks": 0, "comments": 0, "events": 0, "embeddings": 0},
    "db": "/tmp/listik.db", "embed": {"model": "bge-m3", "ok": False},
    "routes": {"ok": True, "error": None, "path": "/tmp/r.json", "count": 11},
}


def _load_cli():
    """Загрузить `bin/listik` как модуль (у файла нет расширения .py)."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_db_error_under_test",
                                                  str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _ServerStateCase(unittest.TestCase):
    """Общее для тестов: глобальное состояние server не течёт между тестами."""

    def _isolate_server_state(self) -> None:
        self._saved_error = server._db_error
        self._saved_local = server._conn_local
        self._saved_stop = server._embed_stop
        server._db_error = None
        server._conn_local = threading.local()

        def restore() -> None:
            server._db_error = self._saved_error
            server._conn_local = self._saved_local
            server._embed_stop = self._saved_stop
        self.addCleanup(restore)

    @staticmethod
    def _patched_conn():
        return mock.patch.object(server, "get_conn", return_value=mock.MagicMock())


class BackgroundPassTests(_ServerStateCase):
    """Пункт 1 приёмки: успешный проход обнуляет `_db_error`."""

    def setUp(self) -> None:
        self._isolate_server_state()

    def test_successful_pass_clears_previous_error(self) -> None:
        server._background_db_error(
            "documents", sqlite3.DatabaseError("database disk image is malformed"))
        self.assertIsNotNone(server._db_error)
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(embed_mod, "embed_pending", return_value={}):
            server._background_pass()
        self.assertIsNone(server._db_error)

    def test_error_in_documents_survives_successful_embed(self) -> None:
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all",
                        side_effect=sqlite3.DatabaseError("database disk image is malformed")), \
             mock.patch.object(embed_mod, "embed_pending", return_value={}):
            server._background_pass()
        self.assertIsNotNone(server._db_error)
        self.assertEqual(server._db_error["where"], "documents")
        self.assertIn("malformed", server._db_error["error"])

    def test_error_in_embed_is_reported(self) -> None:
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(embed_mod, "embed_pending",
                               side_effect=sqlite3.DatabaseError("disk I/O error")):
            server._background_pass()
        self.assertIsNotNone(server._db_error)
        self.assertEqual(server._db_error["where"], "embed")
        self.assertIn("disk I/O error", server._db_error["error"])

    def test_non_db_failure_still_clears_stale_db_error(self) -> None:
        """Недоступный ollama не должен держать старую ошибку базы вечно.

        В этом проходе база читалась нормально (documents отработал), упал только
        необязательный шаг с векторами — db_error снимается, иначе health показывал бы
        вчерашнюю ошибку базы из-за флапающего ollama.
        """
        server._background_db_error("documents", sqlite3.DatabaseError("disk I/O error"))
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(embed_mod, "embed_pending",
                               side_effect=ConnectionRefusedError("ollama недоступен")):
            server._background_pass()
        self.assertIsNone(server._db_error)

    def test_non_db_failure_does_not_set_db_error(self) -> None:
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all",
                        side_effect=FileNotFoundError("нет файла документа")), \
             mock.patch.object(embed_mod, "embed_pending",
                               side_effect=ConnectionRefusedError("ollama недоступен")):
            server._background_pass()
        self.assertIsNone(server._db_error)

    def test_worker_loop_clears_error_after_successful_pass(self) -> None:
        """Сам фоновый поток, а не только `_background_pass`, снимает ошибку."""
        server._background_db_error("embed", sqlite3.DatabaseError("disk I/O error"))
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(embed_mod, "embed_pending", return_value={}):
            thread = server.start_embed_worker(interval=0.02, batch_limit=1)
            try:
                deadline = time.time() + 5
                while server._db_error is not None and time.time() < deadline:
                    time.sleep(0.01)
            finally:
                server._embed_stop.set()
                thread.join(timeout=5)
        self.assertIsNone(server._db_error)


class HealthEndpointTests(TempDbTestCase, _ServerStateCase):
    """Пункты 1–2 приёмки: /api/health отдаёт и забывает `db_error`."""

    TOKEN = "db-error-test"

    def setUp(self) -> None:
        super().setUp()
        self._isolate_server_state()
        self._config_path = self.tmp_path / "config.toml"
        self._config_path.write_text(f'[auth]\ntoken = "{self.TOKEN}"\n', encoding="utf-8")
        patches = [
            mock.patch.object(paths, "CONFIG_PATH", self._config_path),
            mock.patch.object(server, "get_conn", return_value=self.conn),
            mock.patch.object(embed_mod, "health", return_value={"ok": False, "model": "тест"}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _health(self, authed: bool = True) -> dict:
        status, body = server.handle("GET", "/api/health", {}, {}, authed=authed)
        self.assertEqual(status, 200)
        return body

    def test_authed_health_reports_background_error(self) -> None:
        server._background_db_error(
            "documents", sqlite3.DatabaseError("database disk image is malformed"))
        data = self._health()
        self.assertEqual(data["db_error"]["where"], "documents")
        self.assertIn("malformed", data["db_error"]["error"])
        self.assertTrue(data["db_error"]["at"])

    def test_unauthorized_health_hides_db_error(self) -> None:
        server._background_db_error("embed", sqlite3.DatabaseError("disk I/O error"))
        data = self._health(authed=False)
        self.assertNotIn("db_error", data)
        self.assertNotIn("counts", data)

    def test_health_forgets_error_after_successful_pass(self) -> None:
        server._background_db_error("embed", sqlite3.DatabaseError("disk I/O error"))
        self.assertIn("db_error", self._health())
        with self._patched_conn(), \
             mock.patch("listik.documents.refresh_all", return_value={}), \
             mock.patch.object(embed_mod, "embed_pending", return_value={}):
            server._background_pass()
        self.assertNotIn("db_error", self._health())


class StatusCommandTests(_ServerStateCase):
    """Пункт 2 приёмки: `listik status` показывает db_error, а не молчит о нём."""

    def setUp(self) -> None:
        self._isolate_server_state()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cfg = pathlib.Path(self._tmp.name) / "config.toml"
        cfg.write_text('[auth]\ntoken = "status-db-error"\n', encoding="utf-8")
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

    def test_db_error_line(self) -> None:
        code, text = self._run_status({
            **HEALTH_BASE,
            "db_error": {"where": "embed", "at": "2026-09-14T07:00:00Z",
                         "error": "sqlite3.DatabaseError: disk I/O error"},
        })
        self.assertEqual(code, 0)
        self.assertIn("база:   ОШИБКА в фоне [embed]", text)
        self.assertIn("disk I/O error", text)

    def test_no_line_when_db_is_healthy(self) -> None:
        code, text = self._run_status(dict(HEALTH_BASE))
        self.assertEqual(code, 0)
        self.assertNotIn("ОШИБКА в фоне", text)

    def test_no_line_when_token_rejected(self) -> None:
        """`db_error` отдаётся только авторизованному — status не выдумывает его."""
        code, text = self._run_status({
            "status": "ok", "authed": False,
            "db_error": {"where": "embed", "at": "2026-09-14T07:00:00Z",
                         "error": "sqlite3.DatabaseError: disk I/O error"},
        })
        self.assertEqual(code, 0)
        self.assertNotIn("ОШИБКА в фоне", text)


if __name__ == "__main__":
    unittest.main()
