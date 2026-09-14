"""listik-sxcd: сервер не должен копить соединения sqlite (и fd на listik.db)."""
from __future__ import annotations

import fcntl
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest import mock

from listik import db as db_mod
from listik import paths, server


def _db_fds(db_path: pathlib.Path) -> int | None:
    """Число открытых fd процесса на файл базы; None, если платформа не умеет."""
    target = str(db_path.resolve())
    count = 0
    if sys.platform.startswith("linux"):
        for fd in os.listdir("/proc/self/fd"):
            try:
                if os.readlink(f"/proc/self/fd/{fd}") == target:
                    count += 1
            except OSError:
                pass
        return count
    get_path = getattr(fcntl, "F_GETPATH", 50 if sys.platform == "darwin" else None)
    if get_path is None:
        return None
    for fd in range(3, 4096):
        try:
            raw = fcntl.fcntl(fd, get_path, b"\0" * 1024)
        except OSError:
            continue
        if os.path.realpath(raw.split(b"\0", 1)[0].decode()) == target:
            count += 1
    return count


class ConnLeakCase(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = (paths.DB_PATH, server._conn_made, server._conn_local)
        self._saved_cfg = paths.CONFIG_PATH
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = pathlib.Path(self._tmp.name) / "listik.db"
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = pathlib.Path(self._tmp.name) / "config.toml"
        paths.CONFIG_PATH.write_text('[auth]\ntoken = "t"\n', encoding="utf-8")
        server._conn_made = False
        server._conn_local = threading.local()
        self.opened: list[sqlite3.Connection] = []

        def track(opener):
            def wrapper(*a, **kw):
                conn = opener(*a, **kw)
                self.opened.append(conn)
                return conn
            return wrapper
        self._patches = [mock.patch.object(db_mod, "connect", track(db_mod.connect))]
        for p in self._patches:
            p.start()
        server.get_conn()  # схема в главном потоке, как в serve
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        server.close_thread_conn()
        for conn in self.opened:
            conn.close()
        for p in self._patches:
            p.stop()
        paths.DB_PATH, server._conn_made, server._conn_local = self._saved
        paths.CONFIG_PATH = self._saved_cfg
        self._tmp.cleanup()

    def _get(self, path: str) -> None:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                     headers={"Authorization": "Bearer t"})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()

    def test_request_threads_close_connections(self) -> None:
        self._get("/api/health")
        before = _db_fds(self.db_path)
        for _ in range(40):
            self._get("/api/health")
            self._get("/api/tasks")
        # потоки-обработчики завершаются асинхронно — дождаться их
        deadline = time.time() + 5
        def open_count():
            n = 0
            for c in self.opened:
                try:
                    c.execute("SELECT 1")
                    n += 1
                except sqlite3.ProgrammingError:
                    pass
            return n
        while open_count() > 1 and time.time() < deadline:
            time.sleep(0.05)
        # живо только соединение главного потока теста
        self.assertEqual(open_count(), 1)
        after = _db_fds(self.db_path)
        if before is not None:
            self.assertLessEqual(after, before)

    def test_background_db_error_reopens_connection(self) -> None:
        old = server.get_conn()
        server._background_db_error("embed", sqlite3.DatabaseError("database disk image is malformed"))
        self.assertIsNotNone(server._db_error)
        self.assertIn("malformed", server._db_error["error"])
        with self.assertRaises(sqlite3.ProgrammingError):
            old.execute("SELECT 1")
        new = server.get_conn()
        self.assertIsNot(new, old)
        new.execute("SELECT 1")
        server._db_error = None


if __name__ == "__main__":
    unittest.main()
