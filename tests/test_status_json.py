"""Шаг 12, порция c: `listik status --json` и `--local` (пункты 11–14).

`status` гоняется подпроцессом `bin/listik` с временными `LISTIK_HOME`, `HOME` и
`LISTIK_ROUTES`: ни настоящий каталог данных, ни `~/.config/listik` не трогаются.
Сервер для сценариев «up»/«unauthorized» поднимается в потоке теста так же, как в
`tests/test_mcp_http.py`: его база и конфиг подменяются атрибутами `paths`, которые
`db.connect`/`config.load` читают в момент вызова.

`--port` — глобальный флаг CLI, а не флаг подкоманды, поэтому в командной строке он
идёт до `status` (так же, как в `tests/test_data_dir.py`).
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from listik import db as db_mod
from listik import paths, server

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"
PYTHON = sys.executable

TOKEN = "test-token-status"
WRONG_TOKEN = "test-token-wrong"


def free_port() -> int:
    """Свободный порт: слушателя на нём нет."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class StatusJsonCase(unittest.TestCase):
    """Временный каталог данных и запуск CLI подпроцессом."""

    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory(prefix="listik-status-json-")
        self.addCleanup(tmpdir.cleanup)
        self.tmp = pathlib.Path(tmpdir.name)
        self.home = self.tmp / "listik-home"
        self.home.mkdir()
        self.config = self.home / "config.toml"
        self.port = free_port()

    # --- вспомогательное -------------------------------------------------

    def env(self, **overrides: str) -> dict:
        """Окружение без `LISTIK_*` и с временным `HOME`."""
        env = {k: v for k, v in os.environ.items() if not k.startswith("LISTIK_")}
        env["HOME"] = str(self.tmp / "user-home")
        env["PYTHONPATH"] = str(REPO_DIR)
        env["LISTIK_HOME"] = str(self.home)
        env["LISTIK_ROUTES"] = str(self.tmp / "routes.json")
        env.update(overrides)
        return env

    def write_config(self, *, token: str = TOKEN, port: int | None = None) -> None:
        self.config.write_text(
            f'[server]\nhost = "127.0.0.1"\nport = {self.port if port is None else port}\n'
            f'[auth]\ntoken = "{token}"\n',
            encoding="utf-8")

    def run_cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([PYTHON, str(LISTIK_BIN), *args], env=env or self.env(),
                              capture_output=True, text=True, timeout=90)

    def json_status(self, *extra: str, env: dict | None = None) -> tuple[dict, int]:
        """`status --json` и разобранный stdout; код возврата — вторым значением."""
        result = self.run_cli("--port", str(self.port), "status", "--json", *extra,
                              env=env)
        return json.loads(result.stdout), result.returncode

    @staticmethod
    def listening_socket() -> socket.socket:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(5)
        return sock

    @staticmethod
    def accepted_connections(sock: socket.socket) -> int:
        """Сколько соединений принял сокет: считаем неблокирующим accept до отказа."""
        sock.setblocking(False)
        count = 0
        while True:
            try:
                conn, _ = sock.accept()
            except BlockingIOError:
                return count
            except OSError:
                return count
            conn.close()
            count += 1

    # --- пункт 14: сценарии без сервера ----------------------------------

    def test_json_down_without_listener(self) -> None:
        self.write_config()
        result = self.run_cli("--port", str(self.port), "status", "--json")
        # stdout целиком — один JSON-объект, ничего лишнего.
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(data["server"], "down")
        self.assertIsNone(data["health"])
        self.assertEqual(data["data_dir"], str(self.home))
        self.assertEqual(data["db_path"], str(self.home / "listik.db"))
        self.assertEqual(data["version"], (REPO_DIR / "VERSION").read_text().strip())
        self.assertNotIn(TOKEN, result.stdout, "токен утёк в JSON")

    def test_local_json_does_not_touch_server(self) -> None:
        sock = self.listening_socket()
        self.addCleanup(sock.close)
        port = int(sock.getsockname()[1])
        self.write_config(port=port)
        result = self.run_cli("--port", str(port), "status", "--json", "--local")
        data = json.loads(result.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(data["server"], "skipped")
        self.assertIsNone(data["health"])
        self.assertEqual(data["data_dir"], str(self.home))
        self.assertEqual(self.accepted_connections(sock), 0,
                         "--local всё-таки опросил сервер")

    def test_local_text(self) -> None:
        sock = self.listening_socket()
        self.addCleanup(sock.close)
        port = int(sock.getsockname()[1])
        self.write_config(port=port)
        result = self.run_cli("--port", str(port), "status", "--local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("каталог данных:", result.stdout)
        self.assertIn(f"база:   {self.home / 'listik.db'}", result.stdout)
        self.assertIn("сервер: не проверялся (--local)", result.stdout)
        self.assertEqual(self.accepted_connections(sock), 0,
                         "--local всё-таки опросил сервер")

    def test_text_down_without_listener(self) -> None:
        self.write_config()
        result = self.run_cli("--port", str(self.port), "status")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("каталог данных:", result.stdout)
        self.assertIn("сервер: не отвечает", result.stdout)


class StatusJsonServerCase(StatusJsonCase):
    """Живой сервер в потоке теста: конфиг клиента и сервера — разные файлы."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._saved_conn = (server._conn_made, server._conn_local)
        self.addCleanup(self._restore_module_state)
        # База и конфиг сервера — во временном каталоге, а не в каталоге клиента:
        # иначе подмена токена у клиента меняла бы и токен сервера.
        self.server_home = self.tmp / "server-home"
        self.server_home.mkdir()
        paths.DB_PATH = self.server_home / "listik.db"
        paths.CONFIG_PATH = self.server_home / "config.toml"
        paths.CONFIG_PATH.write_text(f'[auth]\ntoken = "{TOKEN}"\n', encoding="utf-8")
        self._conns: list = []
        self._patches = [
            mock.patch.object(db_mod, "init", self._track(db_mod.init)),
            mock.patch.object(db_mod, "connect", self._track(db_mod.connect)),
        ]
        for patch in self._patches:
            patch.start()
        self.addCleanup(self._stop_server)
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.srv_port = int(self.srv.server_address[1])
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def _track(self, opener):
        def wrapper(*args, **kwargs):
            conn = opener(*args, **kwargs)
            self._conns.append(conn)
            return conn
        return wrapper

    def _stop_server(self) -> None:
        if getattr(self, "srv", None) is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None
        for conn in self._conns:
            conn.close()
        for patch in reversed(self._patches):
            patch.stop()

    def _restore_module_state(self) -> None:
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths
        server._conn_made, server._conn_local = self._saved_conn

    def json_from_server(self, *, token: str) -> tuple[dict, int]:
        self.write_config(token=token)
        result = self.run_cli("--port", str(self.srv_port), "status", "--json")
        return json.loads(result.stdout), result.returncode

    def test_json_up_with_valid_token(self) -> None:
        data, code = self.json_from_server(token=TOKEN)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["server"], "up")
        self.assertIs(data["health"]["authed"], True)
        self.assertEqual(data["health"]["status"], "ok")
        self.assertNotIn(TOKEN, json.dumps(data, ensure_ascii=False))

    def test_json_unauthorized_with_wrong_token(self) -> None:
        data, code = self.json_from_server(token=WRONG_TOKEN)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["server"], "unauthorized")
        self.assertIs(data["health"]["authed"], False)

    def test_different_installation_is_marked_even_with_valid_token(self) -> None:
        with mock.patch.object(paths, "DATA_DIR", self.server_home):
            data, code = self.json_from_server(token=TOKEN)
        self.assertEqual(code, 0, data)  # Совместимость: расхождение помечено в JSON.
        self.assertEqual(data["diagnostics"]["installation"], "mismatch")
        self.assertEqual(data["diagnostics"]["token"], "accepted")
        self.assertTrue(data["diagnostics"]["warnings"])
        self.assertEqual(data["bin_path"], str(LISTIK_BIN.resolve()))
        self.assertEqual(data["health"]["installation"]["data_dir"],
                         str(self.server_home.resolve()))
        self.assertEqual(data["health"]["installation"]["config_path"],
                         str((self.server_home / "config.toml").resolve()))

    def test_wrong_token_still_reports_server_paths_without_private_data(self) -> None:
        with mock.patch.object(paths, "DATA_DIR", self.server_home):
            data, _ = self.json_from_server(token=WRONG_TOKEN)
        self.assertEqual(data["diagnostics"]["installation"], "mismatch")
        self.assertEqual(data["diagnostics"]["token"], "rejected")
        self.assertEqual(data["health"]["installation"]["code_dir"], str(REPO_DIR.resolve()))
        for private in ("counts", "db", "db_error", "db_replaced", "routes", "runtime"):
            self.assertNotIn(private, data["health"])
        rendered = json.dumps(data)
        self.assertNotIn(TOKEN, rendered)
        self.assertNotIn(WRONG_TOKEN, rendered)

    def test_text_mismatch_explains_paths_and_repair(self) -> None:
        self.write_config(token=WRONG_TOKEN)
        before = self.config.read_bytes()
        with mock.patch.object(paths, "DATA_DIR", self.server_home):
            result = self.run_cli("--port", str(self.srv_port), "status")
        self.assertEqual(result.returncode, 0, result.stderr)
        for expected in ("ВНИМАНИЕ: несовпадение установок", "токен: не принят",
                         str(LISTIK_BIN.resolve()), str(self.home), str(self.config),
                         str(self.server_home), str(paths.CONFIG_PATH), str(REPO_DIR),
                         "LISTIK_HOME/LISTIK_CONFIG", "Не обходите отказ через --local"):
            self.assertIn(expected, result.stdout)
        self.assertNotIn(WRONG_TOKEN, result.stdout)
        self.assertEqual(self.config.read_bytes(), before)

    def test_matching_installation_has_no_warning(self) -> None:
        self.write_config()
        with mock.patch.object(paths, "DATA_DIR", self.home), \
             mock.patch.object(paths, "CONFIG_PATH", self.config):
            data, code = self.json_from_server(token=TOKEN)
        self.assertEqual(code, 0, data)
        self.assertEqual(data["diagnostics"]["installation"], "match")
        self.assertEqual(data["diagnostics"]["token"], "accepted")
        self.assertEqual(data["diagnostics"]["warnings"], [])

    def test_config_mismatch_with_same_data_dir(self) -> None:
        with mock.patch.object(paths, "DATA_DIR", self.home):
            data, _ = self.json_from_server(token=TOKEN)
        self.assertEqual(data["diagnostics"]["installation"], "mismatch")
        self.assertIn("config_path", " ".join(data["diagnostics"]["warnings"]))

    def test_symlink_to_same_installation_is_not_a_mismatch(self) -> None:
        self.write_config()
        alias = self.tmp / "home-link"
        alias.symlink_to(self.home, target_is_directory=True)
        with mock.patch.object(paths, "DATA_DIR", self.home), \
             mock.patch.object(paths, "CONFIG_PATH", self.config):
            result = self.run_cli("--port", str(self.srv_port), "status", "--json",
                                  env=self.env(LISTIK_HOME=str(alias)))
        data = json.loads(result.stdout)
        self.assertEqual(data["diagnostics"]["installation"], "match")
        self.assertEqual(data["diagnostics"]["warnings"], [])

    def test_text_up_prints_board_link(self) -> None:
        self.write_config(token=TOKEN)
        result = self.run_cli("--port", str(self.srv_port), "status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("сервер: работает", result.stdout)
        # Порт доски берётся из конфига клиента, а не из флага --port (как и раньше).
        self.assertIn(f"доска:  http://127.0.0.1:{self.port}/?token={TOKEN}", result.stdout)


if __name__ == "__main__":
    unittest.main()
