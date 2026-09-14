"""listik-cfzk: штатные backup/restore базы через sqlite backup API.

Проверяется пункт приёмки 1: копия снимается согласованно и при живом сервере
(данные из WAL попадают в копию, чего не даёт обычный cp), restore отказывает,
пока сервер работает, умеет останавливать его (`--stop`), не делает молчаливый
откат назад и убирает -wal/-shm прежней базы.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

from listik import backup as backup_mod
from listik import errors, paths, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN


def _titles(path) -> list[str]:
    conn = sqlite3.connect(str(path))
    try:
        return [row[0] for row in conn.execute("SELECT title FROM tasks ORDER BY title")]
    finally:
        conn.close()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BackupTests(TempDbTestCase):
    """Пункт 1 приёмки: копия через sqlite backup API, а не копирование файла."""

    def setUp(self) -> None:
        super().setUp()
        store.create_task(self.conn, title="первая", project="listik")
        self.conn.commit()

    def test_backup_is_a_usable_copy(self) -> None:
        info = backup_mod.backup(self.db_path)
        copy = pathlib.Path(info["backup"])
        self.assertTrue(copy.is_file())
        self.assertEqual(info["integrity"], "ok")
        self.assertEqual(info["counts"]["tasks"], 1)
        self.assertEqual(_titles(copy), ["первая"])
        # Копия — один файл: своих -wal/-shm у неё быть не должно.
        self.assertFalse(pathlib.Path(str(copy) + "-wal").exists())
        self.assertFalse(pathlib.Path(str(copy) + "-shm").exists())

    def test_backup_catches_wal_data(self) -> None:
        """cp файла базы теряет WAL, sqlite backup API — нет (ради этого всё и затевалось)."""
        store.create_task(self.conn, title="из WAL", project="listik")
        self.conn.commit()
        wal = pathlib.Path(str(self.db_path) + "-wal")
        self.assertTrue(wal.exists() and wal.stat().st_size > 0, "WAL должен быть не пуст")

        naive = self.tmp_path / "naive-copy.db"
        shutil.copyfile(self.db_path, naive)
        try:
            naive_titles = _titles(naive)
        except sqlite3.DatabaseError:
            # У cp-копии может не быть даже схемы: она целиком лежит в WAL.
            naive_titles = []
        self.assertNotIn("из WAL", naive_titles, "обычный cp не видит данные из WAL")

        info = backup_mod.backup(self.db_path)
        self.assertEqual(sorted(_titles(info["backup"])), ["из WAL", "первая"])

    def test_backup_refuses_existing_file(self) -> None:
        first = backup_mod.backup(self.db_path)
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.backup(self.db_path, first["backup"])
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("--force", ctx.exception.hint)

        again = backup_mod.backup(self.db_path, first["backup"], overwrite=True)
        self.assertEqual(again["counts"]["tasks"], 1)

    def test_backup_without_db(self) -> None:
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.backup(self.tmp_path / "нет-такой.db")
        self.assertEqual(ctx.exception.code, errors.NOT_FOUND)

    def test_default_path_is_next_to_db_and_ignored_by_git(self) -> None:
        default = backup_mod.default_backup_path(self.db_path)
        self.assertEqual(default.parent, self.db_path.parent)
        self.assertTrue(default.name.startswith("listik.db.bak-"))
        # Именно этот шаблон закрыт .gitignore (listik.db.bak-*).
        info = backup_mod.backup(self.db_path)
        self.assertEqual(pathlib.Path(info["backup"]).parent, self.db_path.parent)


class RestoreTests(TempDbTestCase):
    """Пункт 1 приёмки: restore отказывает при работающем сервере и не теряет данные."""

    def setUp(self) -> None:
        super().setUp()
        # Тесты не должны зависеть от того, что на машине есть чужой сервер Listik:
        # «работающий сервер» подставляется только там, где это и проверяется.
        patch = mock.patch.object(backup_mod, "running_server", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)
        store.create_task(self.conn, title="первая", project="listik")
        self.conn.commit()
        self.copy = pathlib.Path(backup_mod.backup(self.db_path)["backup"])
        store.create_task(self.conn, title="вторая", project="listik")
        self.conn.commit()

    def test_restore_returns_old_state_and_keeps_safety_copy(self) -> None:
        report = backup_mod.restore(self.copy, self.db_path, force=True)
        self.assertEqual(report["integrity"], "ok")
        self.assertEqual(report["counts"]["tasks"], 1)
        self.assertEqual(_titles(self.db_path), ["первая"])
        safety = pathlib.Path(report["safety_copy"])
        self.assertTrue(safety.is_file())
        self.assertEqual(sorted(_titles(safety)), ["вторая", "первая"])
        # Старый журнал прежней базы убран: с новым файлом он несовместим.
        self.assertFalse(pathlib.Path(str(self.db_path) + "-wal").exists())
        self.assertFalse(pathlib.Path(str(self.db_path) + "-shm").exists())

    def test_restore_refuses_while_server_runs(self) -> None:
        with mock.patch.object(backup_mod, "running_server",
                               return_value={"pid": 4242, "how": "тест"}):
            with self.assertRaises(errors.ListikError) as ctx:
                backup_mod.restore(self.copy, self.db_path)
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("listik stop", ctx.exception.hint)
        self.assertIn("--stop", ctx.exception.hint)
        # Ничего не тронуто: в базе по-прежнему две задачи.
        self.assertEqual(sorted(_titles(self.db_path)), ["вторая", "первая"])
        self.assertEqual(len(list(self.tmp_path.glob("*.bak-pre-restore-*"))), 0)

    def test_restore_stop_flag_stops_server_first(self) -> None:
        with mock.patch.object(backup_mod, "running_server",
                               return_value={"pid": 4242, "how": "тест"}), \
             mock.patch.object(backup_mod, "stop_server",
                               return_value={"stopped": True, "pid": 4242}) as stopped:
            report = backup_mod.restore(self.copy, self.db_path, stop=True, force=True)
        stopped.assert_called_once()
        self.assertTrue(report["server"]["stopped"])
        self.assertEqual(_titles(self.db_path), ["первая"])

    def test_restore_refuses_when_server_started_again(self) -> None:
        """Между --stop и подменой файла сервер мог подняться снова — тогда отказ."""
        with mock.patch.object(backup_mod, "running_server",
                               return_value={"pid": 4242, "how": "тест"}), \
             mock.patch.object(backup_mod, "stop_server",
                               return_value={"stopped": True, "pid": 4242}):
            with self.assertRaises(errors.ListikError) as ctx:
                backup_mod.restore(self.copy, self.db_path, stop=True)
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("--force", ctx.exception.hint)
        self.assertEqual(sorted(_titles(self.db_path)), ["вторая", "первая"])
        # Недоделанная замена не осталась мусором рядом с базой.
        self.assertEqual(list(self.tmp_path.glob("*.restore-*")), [])

    def test_restore_refuses_foreign_file(self) -> None:
        foreign = self.tmp_path / "чужая.db"
        conn = sqlite3.connect(foreign)
        conn.execute("CREATE TABLE t(x)")
        conn.commit()
        conn.close()
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.restore(foreign, self.db_path)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertEqual(sorted(_titles(self.db_path)), ["вторая", "первая"])

    def test_restore_refuses_broken_copy(self) -> None:
        broken = self.tmp_path / "битая.db"
        broken.write_bytes(b"this is not sqlite")
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.restore(broken, self.db_path)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)

    def test_restore_missing_copy(self) -> None:
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.restore(self.tmp_path / "нет.db", self.db_path)
        self.assertEqual(ctx.exception.code, errors.NOT_FOUND)

    def test_rollback_needs_force(self) -> None:
        """В копии 1 задача, в текущей 2 — молчаливый откат назад запрещён."""
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.restore(self.copy, self.db_path)
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("--force", ctx.exception.hint)
        self.assertEqual(sorted(_titles(self.db_path)), ["вторая", "первая"])

    def test_restore_into_missing_db(self) -> None:
        self.conn.close()
        for side in backup_mod.sidecar_paths(self.db_path):
            side.unlink(missing_ok=True)
        self.db_path.unlink()
        report = backup_mod.restore(self.copy, self.db_path)
        self.assertIsNone(report["safety_copy"])
        self.assertEqual(_titles(self.db_path), ["первая"])


class _FakeHealthServer:
    """Мини-сервер Listik: отвечает на /api/health и называет свою базу.

    По этому ответу `listik restore` в отдельном процессе понимает, что сервер
    работает, и отказывается подменять файл (в тестах настоящее Listik не трогаем).
    """

    def __init__(self, db_path: pathlib.Path):
        import http.server
        import threading

        payload = json.dumps({"ok": True, "data": {
            "status": "ok", "authed": True, "db": str(db_path),
        }}).encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.split("?")[0] != "/api/health":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # тихо
                pass

        self.httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


class RunningServerTests(TempDbTestCase):
    """Кого `restore` считает работающим сервером именно этой базы."""

    def setUp(self) -> None:
        super().setUp()
        self.config = self.tmp_path / "config.toml"
        self.config.write_text('[auth]\ntoken = "t"\n'
                               '[server]\nhost = "127.0.0.1"\nport = 59999\n',
                               encoding="utf-8")
        patch = mock.patch.object(paths, "CONFIG_PATH", self.config)
        patch.start()
        self.addCleanup(patch.stop)

    def test_health_with_same_db_is_our_server(self) -> None:
        with mock.patch("listik.client.health",
                        return_value={"authed": True, "db": str(self.db_path)}), \
             mock.patch("listik.server.read_pid", return_value=777):
            info = backup_mod.running_server(self.db_path)
        self.assertEqual(info["pid"], 777)
        self.assertIn("http://127.0.0.1:59999", info["how"])

    def test_health_with_other_db_is_not_our_server(self) -> None:
        with mock.patch("listik.client.health",
                        return_value={"authed": True, "db": str(self.tmp_path / "чужой.db")}), \
             mock.patch("listik.server.read_pid", return_value=777):
            self.assertIsNone(backup_mod.running_server(self.db_path))

    def test_health_without_pid_file_looks_at_port(self) -> None:
        with mock.patch("listik.client.health",
                        return_value={"authed": True, "db": str(self.db_path)}), \
             mock.patch("listik.server.read_pid", return_value=None), \
             mock.patch("listik.server.port_holder",
                        return_value=(888, "python3 bin/listik serve --daemon")):
            info = backup_mod.running_server(self.db_path)
        self.assertEqual(info["pid"], 888)

    def test_unauthed_health_falls_back_to_pid_file_of_install(self) -> None:
        """Токен не принят — но pid-файл установки всё равно про нашу базу."""
        with mock.patch.object(paths, "ROOT_DIR", self.tmp_path), \
             mock.patch("listik.client.health", return_value={"authed": False}), \
             mock.patch("listik.server.read_pid", return_value=4242):
            info = backup_mod.running_server(self.db_path)
        self.assertEqual(info, {"pid": 4242, "how": "listik.pid"})

    def test_unauthed_health_falls_back_to_port(self) -> None:
        with mock.patch.object(paths, "ROOT_DIR", self.tmp_path), \
             mock.patch("listik.client.health", return_value=None), \
             mock.patch("listik.server.read_pid", return_value=None), \
             mock.patch("listik.server.port_holder",
                        return_value=(555, "python3 bin/listik serve")):
            info = backup_mod.running_server(self.db_path)
        self.assertEqual(info["pid"], 555)
        self.assertIn("порт 59999", info["how"])

    def test_nothing_running(self) -> None:
        with mock.patch.object(paths, "ROOT_DIR", self.tmp_path), \
             mock.patch("listik.client.health", return_value=None), \
             mock.patch("listik.server.read_pid", return_value=None), \
             mock.patch("listik.server.port_holder", return_value=None):
            self.assertIsNone(backup_mod.running_server(self.db_path))


class StopServerTests(unittest.TestCase):
    """`restore --stop` убивает только проверенный `listik serve`."""

    def test_refuses_foreign_pid(self) -> None:
        with mock.patch.object(backup_mod, "_process_command",
                               return_value="/usr/sbin/sshd -D"):
            with self.assertRaises(errors.ListikError) as ctx:
                backup_mod.stop_server({"pid": 1, "how": "listik.pid"})
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("не `listik serve`", ctx.exception.message)

    def test_refuses_when_process_cannot_be_checked(self) -> None:
        """Нет ps — нельзя убедиться, что pid наш: отказ, а не «уже остановлен»."""
        with mock.patch.object(backup_mod, "_process_command", return_value=None):
            with self.assertRaises(errors.ListikError) as ctx:
                backup_mod.stop_server({"pid": 4242, "how": "listik.pid"})
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("listik stop", ctx.exception.hint)

    def test_already_finished_is_not_an_error(self) -> None:
        with mock.patch.object(backup_mod, "_process_command", return_value=""):
            result = backup_mod.stop_server({"pid": 4242, "how": "listik.pid"})
        self.assertFalse(result["stopped"])

    def test_refuses_without_pid(self) -> None:
        with self.assertRaises(errors.ListikError) as ctx:
            backup_mod.stop_server({"pid": None, "how": "http://127.0.0.1:8787"})
        self.assertEqual(ctx.exception.code, errors.CONFLICT)


class BackupRestoreCliTests(TempDbTestCase):
    """Пункт 1 приёмки: команды `listik backup` / `listik restore` и их вывод."""

    def setUp(self) -> None:
        super().setUp()
        store.create_task(self.conn, title="через CLI", project="listik")
        self.conn.commit()
        self.config = self.tmp_path / "config.toml"
        # Свой порт: проверка «сервер работает» не должна находить чужой Listik.
        self.config.write_text(f'[auth]\ntoken = "cli-test"\n'
                               f'[server]\nhost = "127.0.0.1"\nport = {_free_port()}\n',
                               encoding="utf-8")

    def run_cli(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(self.config),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_backup_and_restore_json(self) -> None:
        proc = self.run_cli("backup", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        info = json.loads(proc.stdout)
        copy = pathlib.Path(info["backup"])
        self.assertTrue(copy.is_file())
        self.assertEqual(info["counts"]["tasks"], 1)

        store.create_task(self.conn, title="лишняя", project="listik")
        self.conn.commit()
        proc = self.run_cli("restore", str(copy), "--force", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual(report["counts"]["tasks"], 1)
        self.assertEqual(_titles(self.db_path), ["через CLI"])

    def test_backup_human_output(self) -> None:
        proc = self.run_cli("backup")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("копия:", proc.stdout)
        self.assertIn("integrity_check: ok", proc.stdout)

    def test_restore_missing_copy_is_json_error(self) -> None:
        proc = self.run_cli("restore", str(self.tmp_path / "нет.db"), "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = json.loads(proc.stdout)["error"]
        self.assertEqual(err["code"], errors.NOT_FOUND)
        self.assertNotIn("Traceback", proc.stderr)

    def test_restore_refuses_while_server_runs(self) -> None:
        copy = backup_mod.backup(self.db_path)["backup"]
        server = _FakeHealthServer(self.db_path)
        self.addCleanup(server.stop)
        self.config.write_text(f'[auth]\ntoken = "cli-test"\n'
                               f'[server]\nhost = "127.0.0.1"\nport = {server.port}\n',
                               encoding="utf-8")
        proc = self.run_cli("restore", str(copy), "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = json.loads(proc.stdout)["error"]
        self.assertEqual(err["code"], errors.CONFLICT)
        self.assertIn("--stop", err["hint"])
        self.assertIn("listik stop", err["hint"])
        # База не тронута: сервер работает, подмены файла не было.
        self.assertEqual(_titles(self.db_path), ["через CLI"])


if __name__ == "__main__":
    unittest.main()

