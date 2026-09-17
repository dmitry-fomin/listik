"""listik-l2fy: `stop` без pid-файла и понятная ошибка `serve` при занятом порте.

Проверяются правки коммита d27c507:
  * `bin/listik cmd_stop` — если pid-файла нет, сервер Listik ищется по порту через lsof:
    живой сервер останавливается, чужой процесс не трогается (код 1), свободный порт — код 0;
  * `listik/server.py` — `port_holder`/`is_listik_serve`/`bind_or_explain`: занятый порт даёт
    понятное сообщение и код 1 вместо трейсбека; bind перенесён в начало `serve()`, поэтому
    при занятом порте launcher не трогается и фоновые потоки не заводятся. `is_listik_serve`
    считает сервером только строку, где `listik` — имя бинаря, а `serve` — отдельный токен
    после него (`listik-l2fy/.../vite serve`, `listik-helper/bin/app serve` и `--mode server`
    сервером не считаются — см. `test_is_listik_serve_rejects_lookalikes`).

Настоящий сервер Listik тесты не поднимают и не останавливают. Слушателей держат сокеты и
процессы-заглушки во временном каталоге, конфиг и база тоже временные (`LISTIK_CONFIG`,
`LISTIK_DB`). Если в корне репозитория лежит `listik.pid`, тесты остановки пропускаются:
иначе `stop` остановил бы настоящий сервер.

Оговорка про песочницу: `port_holder` берёт pid из `lsof`, а командную строку — из `ps`, и в
песочнице агента запуск `ps` запрещён (`Operation not permitted`), поэтому там `port_holder`
штатно возвращает None. Тесты, которым нужна командная строка, подставляют свой `ps` первым в
PATH (настоящий `lsof` при этом остаётся настоящим — pid берётся именно из него); отдельные
тесты с настоящим `ps` пропускаются, если `ps` недоступен.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from listik import paths, server

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"
PID_FILE = REPO_DIR / "listik.pid"

# Заглушка сервера Listik: держит порт и выглядит для `lsof`/`ps` как `listik serve <порт>`
# (файл специально назван `listik` и запускается как `python3 <путь>/listik serve <порт>`).
FAKE_SERVE_PY = """\
import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", int(sys.argv[2])))
s.listen(5)
time.sleep(300)
"""

# Чужой процесс: порт держит, но на `listik serve` не похож.
FOREIGN_PY = """\
import socket, sys, time
s = socket.socket()
s.bind(("127.0.0.1", int(sys.argv[1])))
s.listen(5)
time.sleep(300)
"""


def _load_cli():
    """Загрузить `bin/listik` как модуль (у файла нет расширения .py)."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_port_conflicts", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _listen() -> socket.socket:
    """Слушающий сокет на свободном порту 127.0.0.1."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    return s


def _has_lsof() -> bool:
    return shutil.which("lsof") is not None


def _has_ps() -> bool:
    """Доступен ли настоящий `ps` (в песочнице агента он запрещён)."""
    try:
        result = subprocess.run(["ps", "-o", "command=", "-p", str(os.getpid())],
                                capture_output=True, text=True, timeout=5)
    except OSError:
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _fake_ps_bin(root: pathlib.Path, command: str) -> pathlib.Path:
    """Каталог с подставным `ps`, который сообщает заданную командную строку.

    Только `ps`: `lsof` тесты не подменяют, pid настоящий. Внутри — встроенные команды
    `/bin/sh` (`read`/`echo`), чтобы не зависеть от внешних утилит песочницы.
    """
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    cmd_file = bin_dir / "ps-command.txt"
    cmd_file.write_text(command.rstrip("\n") + "\n", encoding="utf-8")
    script = bin_dir / "ps"
    script.write_text(f'#!/bin/sh\nread -r line < "{cmd_file}"\necho "$line"\n', encoding="utf-8")
    script.chmod(0o755)
    return bin_dir


def _path_with(bin_dir: pathlib.Path, env: dict | None = None) -> dict:
    base = dict(os.environ if env is None else env)
    base["PATH"] = f"{bin_dir}{os.pathsep}{base.get('PATH', '')}"
    return base


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _wait_port(port: int, proc: subprocess.Popen | None = None, timeout: float = 5.0) -> bool:
    """Дождаться, пока порт начнут слушать (и процесс-заглушка при этом жив)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), 0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _wait_holder(port: int, timeout: float = 5.0) -> tuple[int, str] | None:
    """Дождаться, пока lsof увидит того, кто слушает порт (у lsof есть лаг)."""
    deadline = time.monotonic() + timeout
    holder = None
    while time.monotonic() < deadline:
        holder = server.port_holder(port)
        if holder:
            return holder
        time.sleep(0.05)
    return holder


class _TempDirTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = pathlib.Path(self.tmp.name)


class PortHolderTests(_TempDirTests):
    """`port_holder`/`is_listik_serve` — кто держит порт."""

    def test_free_port_has_no_holder(self) -> None:
        self.assertIsNone(server.port_holder(_free_port()))

    def test_without_lsof_no_holder(self) -> None:
        with mock.patch("subprocess.run", side_effect=OSError("lsof не найден")):
            self.assertIsNone(server.port_holder(12345))

    def test_broken_lsof_output_no_holder(self) -> None:
        with mock.patch("subprocess.run") as run:
            run.return_value.stdout = "не число"
            self.assertIsNone(server.port_holder(12345))

    def test_ps_failure_gives_no_holder(self) -> None:
        """lsof нашёл pid, но `ps` недоступен — port_holder молча возвращает None.

        Так ведёт себя песочница агента (запуск `ps` запрещён): `stop` тогда считает, что
        сервера нет. Тесты с командной строкой поэтому подставляют свой `ps`.
        """
        calls: list[str] = []

        def run(cmd, **kwargs):
            calls.append(cmd[0])
            if cmd[0] == "lsof":
                return subprocess.CompletedProcess(cmd, 0, stdout="4242\n", stderr="")
            raise PermissionError(1, "Operation not permitted")

        with mock.patch("subprocess.run", side_effect=run):
            self.assertIsNone(server.port_holder(12345))
        self.assertEqual(calls, ["lsof", "ps"])

    @unittest.skipUnless(_has_lsof(), "нет lsof")
    def test_live_listener_reported_with_pid_and_command(self) -> None:
        sock = _listen()
        self.addCleanup(sock.close)
        fake = _fake_ps_bin(self.tmp_path, "python3 ./bin/listik serve --daemon")
        with mock.patch.dict(os.environ, _path_with(fake)):
            holder = _wait_holder(sock.getsockname()[1])
        self.assertIsNotNone(holder, "lsof не увидел собственный слушающий сокет")
        assert holder is not None  # для типов
        self.assertEqual(holder[0], os.getpid())          # pid — настоящий, из lsof
        self.assertEqual(holder[1], "python3 ./bin/listik serve --daemon")  # строка — из ps

    @unittest.skipUnless(_has_lsof() and _has_ps(), "нет lsof/ps")
    def test_real_ps_command_line_is_read(self) -> None:
        sock = _listen()
        self.addCleanup(sock.close)
        holder = _wait_holder(sock.getsockname()[1])
        self.assertIsNotNone(holder)
        assert holder is not None  # для типов
        self.assertEqual(holder[0], os.getpid())
        self.assertIn("unittest", holder[1])

    def test_is_listik_serve_recognises_server_only(self) -> None:
        self.assertTrue(server.is_listik_serve("python3 ./bin/listik serve --daemon"))
        self.assertTrue(server.is_listik_serve("/opt/listik/bin/listik serve"))
        self.assertFalse(server.is_listik_serve("python3 ./bin/listik status"))
        self.assertFalse(server.is_listik_serve("python3 app.py serve --port 80"))
        self.assertFalse(server.is_listik_serve("nginx: master process /usr/sbin/nginx"))
        self.assertFalse(server.is_listik_serve(""))

    def test_is_listik_serve_rejects_lookalikes(self) -> None:
        """Возврат с судейства: подстрока «listik» в пути и «serve» в «server» — не сервер."""
        # vite serve из worktree listik-l2fy: имя бинаря — vite, listik лишь часть каталога.
        self.assertFalse(server.is_listik_serve(
            "/Users/me/Projects/Listik-wt/listik-l2fy/web/node_modules/.bin/vite serve"))
        # listik-helper/bin/app: имя бинаря — app, listik лишь часть каталога.
        self.assertFalse(server.is_listik_serve("/opt/listik-helper/bin/app serve --port 8080"))
        # «serve» — подстрока «server»: отдельного токена serve нет.
        self.assertFalse(server.is_listik_serve("python3 /opt/listik/bin/listik --mode server"))
        self.assertFalse(server.is_listik_serve("python3 /opt/listik/bin/listik service start"))
        # serve раньше бинаря listik: подкоманда serve идёт после имени бинаря.
        self.assertFalse(server.is_listik_serve("serve /opt/listik/bin/listik --daemon"))

    def test_is_listik_serve_handles_quoted_path(self) -> None:
        self.assertTrue(server.is_listik_serve('python3 "/opt/My Listik/bin/listik" serve --daemon'))


class BindOrExplainTests(_TempDirTests):
    """Занятый порт у `serve`: понятное сообщение и код 1 вместо трейсбека."""

    def setUp(self) -> None:
        super().setUp()
        self.sock = _listen()
        self.port = int(self.sock.getsockname()[1])
        self.addCleanup(self.sock.close)

    def test_free_port_binds(self) -> None:
        port = _free_port()
        httpd = server.bind_or_explain("127.0.0.1", port, quiet=True)
        try:
            self.assertEqual(httpd.server_address[1], port)
        finally:
            httpd.server_close()

    def test_busy_port_by_foreign_process(self) -> None:
        with mock.patch.object(server, "port_holder", return_value=(4242, "nginx: worker process")):
            with self.assertRaises(SystemExit) as cm:
                server.bind_or_explain("127.0.0.1", self.port, quiet=True)
        msg = str(cm.exception)
        self.assertIn(f"порт 127.0.0.1:{self.port} уже занят", msg)
        self.assertIn("pid 4242", msg)
        self.assertIn("nginx", msg)
        self.assertIn("--port", msg)
        self.assertNotIn("listik stop", msg)

    def test_busy_port_by_listik_server(self) -> None:
        cmd = "python3 /opt/listik/bin/listik serve --daemon"
        with mock.patch.object(server, "port_holder", return_value=(777, cmd)):
            with self.assertRaises(SystemExit) as cm:
                server.bind_or_explain("127.0.0.1", self.port, quiet=True)
        msg = str(cm.exception)
        self.assertIn("другим сервером Listik", msg)
        self.assertIn("pid 777", msg)
        self.assertIn("listik stop", msg)

    def test_busy_port_without_lsof_still_explains(self) -> None:
        with mock.patch.object(server, "port_holder", return_value=None):
            with self.assertRaises(SystemExit) as cm:
                server.bind_or_explain("127.0.0.1", self.port, quiet=True)
        msg = str(cm.exception)
        self.assertIn("уже занят", msg)
        self.assertIn("--port", msg)

    @unittest.skipUnless(_has_lsof(), "нет lsof")
    def test_busy_port_names_real_holder_pid(self) -> None:
        fake = _fake_ps_bin(self.tmp_path, "nginx: worker process")
        with mock.patch.dict(os.environ, _path_with(fake)):
            with self.assertRaises(SystemExit) as cm:
                server.bind_or_explain("127.0.0.1", self.port, quiet=True)
        msg = str(cm.exception)
        self.assertIn(f"порт 127.0.0.1:{self.port} уже занят", msg)
        self.assertIn(f"pid {os.getpid()}", msg)  # pid настоящий, из lsof
        self.assertIn("nginx", msg)
        self.assertIn("--port", msg)

    def test_other_oserror_is_not_swallowed(self) -> None:
        for err in (errno.EACCES, errno.EADDRNOTAVAIL):
            with mock.patch.object(server, "make_server",
                                   side_effect=OSError(err, os.strerror(err))):
                with self.assertRaises(OSError):
                    server.bind_or_explain("127.0.0.1", 1, quiet=True)


class ServeOrderTests(_TempDirTests):
    """`serve` занимает порт первым: при занятом порте launcher и потоки не трогаются."""

    def setUp(self) -> None:
        super().setUp()
        self.sock = _listen()
        self.port = int(self.sock.getsockname()[1])
        self.addCleanup(self.sock.close)
        cfg_path = self.tmp_path / "config.toml"
        cfg_path.write_text(
            '[server]\nhost = "127.0.0.1"\nport = %d\n[auth]\ntoken = "port-conflicts"\n'
            % self.port, encoding="utf-8")
        patch = mock.patch.object(paths, "CONFIG_PATH", cfg_path)
        patch.start()
        self.addCleanup(patch.stop)
        # serve() ставит SIGHUP в SIG_IGN на весь процесс — вернём как было.
        self.addCleanup(signal.signal, signal.SIGHUP, signal.getsignal(signal.SIGHUP))

    def _serve_on_busy_port(self, **kwargs) -> None:
        with mock.patch.object(server, "get_conn") as get_conn, \
             mock.patch.object(server.launcher_mod, "recover") as recover, \
             mock.patch.object(server, "start_embed_worker") as embed, \
             mock.patch.object(server.routes_store, "ensure_imported") as routes, \
             mock.patch.object(server, "daemonize") as daemonize, \
             contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit):
                server.serve(host="127.0.0.1", port=self.port, quiet=True, **kwargs)
        get_conn.assert_not_called()
        recover.assert_not_called()
        embed.assert_not_called()
        routes.assert_not_called()
        daemonize.assert_not_called()

    def test_foreground_serve_does_not_touch_launcher(self) -> None:
        self._serve_on_busy_port()

    def test_background_serve_does_not_fork(self) -> None:
        with mock.patch.object(server, "read_pid", return_value=None):
            self._serve_on_busy_port(background=True)

    def test_daemon_with_live_pid_file_does_not_bind(self) -> None:
        """Живой pid-файл: `serve --daemon` сообщает и выходит, порт не трогает."""
        pid_path = self.tmp_path / "listik.pid"
        pid_path.write_text(str(os.getpid()), encoding="utf-8")
        out = io.StringIO()
        with mock.patch.object(paths, "PID_PATH", pid_path), \
             mock.patch.object(server, "bind_or_explain") as bind, \
             mock.patch.object(server, "daemonize") as daemonize, \
             contextlib.redirect_stdout(out):
            server.serve(host="127.0.0.1", port=self.port, quiet=True, background=True)
        self.assertIn("сервер уже запущен", out.getvalue())
        bind.assert_not_called()
        daemonize.assert_not_called()


class StopCliTests(_TempDirTests):
    """`cmd_stop` без pid-файла: поиск сервера по порту и честный код возврата."""

    def setUp(self) -> None:
        super().setUp()
        self.cli = _load_cli()
        self.cfg_path = self.tmp_path / "config.toml"
        self.cfg_path.write_text('[server]\nport = 8765\n[auth]\ntoken = "stop-test"\n',
                                 encoding="utf-8")
        patch = mock.patch.object(paths, "CONFIG_PATH", self.cfg_path)
        patch.start()
        self.addCleanup(patch.stop)
        self.args = argparse.Namespace(port=None)

    def _stop(self) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.cli.cmd_stop(self.args)
        return code, out.getvalue()

    def test_no_pid_no_holder_says_not_running(self) -> None:
        with mock.patch.object(server, "read_pid", return_value=None), \
             mock.patch.object(server, "port_holder", return_value=None) as holder, \
             mock.patch.object(os, "kill") as kill:
            code, text = self._stop()
        self.assertEqual(code, 0)
        self.assertIn("сервер не запущен", text)
        holder.assert_called_once_with(8765)  # порт взят из config.toml
        kill.assert_not_called()

    def test_no_pid_foreign_holder_exits_1_and_is_not_killed(self) -> None:
        with mock.patch.object(server, "read_pid", return_value=None), \
             mock.patch.object(server, "port_holder",
                               return_value=(4242, "nginx: worker process")), \
             mock.patch.object(os, "kill") as kill:
            code, text = self._stop()
        self.assertEqual(code, 1)
        self.assertIn("чужим процессом", text)
        self.assertIn("4242", text)
        self.assertIn("nginx", text)
        kill.assert_not_called()

    def test_no_pid_listik_holder_is_stopped(self) -> None:
        cmd = "python3 /opt/listik/bin/listik serve --daemon"
        with mock.patch.object(server, "read_pid", return_value=None), \
             mock.patch.object(server, "port_holder", return_value=(4242, cmd)), \
             mock.patch.object(os, "kill") as kill:
            code, text = self._stop()
        self.assertEqual(code, 0)
        kill.assert_called_once_with(4242, signal.SIGTERM)
        self.assertIn("остановлен (pid 4242)", text)

    def test_pid_file_wins_and_port_is_not_probed(self) -> None:
        with mock.patch.object(server, "read_pid", return_value=555), \
             mock.patch.object(server, "port_holder") as holder, \
             mock.patch.object(os, "kill") as kill:
            code, text = self._stop()
        self.assertEqual(code, 0)
        kill.assert_called_once_with(555, signal.SIGTERM)
        holder.assert_not_called()
        self.assertIn("остановлен (pid 555)", text)

    def test_explicit_port_wins_over_config(self) -> None:
        self.args.port = 9999
        with mock.patch.object(server, "read_pid", return_value=None), \
             mock.patch.object(server, "port_holder", return_value=None) as holder, \
             mock.patch.object(os, "kill") as kill:
            code, _ = self._stop()
        self.assertEqual(code, 0)
        holder.assert_called_once_with(9999)
        kill.assert_not_called()


class _CliProcessTests(_TempDirTests):
    """Общее для тестов, которые запускают `bin/listik` отдельным процессом."""

    def setUp(self) -> None:
        super().setUp()
        self.cfg_path = self.tmp_path / "config.toml"
        self.cfg_path.write_text(
            '[server]\nhost = "127.0.0.1"\n[auth]\ntoken = "port-conflicts"\n', encoding="utf-8")
        self.env = {**os.environ,
                    "LISTIK_CONFIG": str(self.cfg_path),
                    "LISTIK_DB": str(self.tmp_path / "listik.db")}

    def _skip_if_real_server_pid(self) -> None:
        if PID_FILE.exists():
            self.skipTest(f"в корне репозитория есть {PID_FILE.name}: "
                          "команда остановила бы настоящий сервер")

    def _start_child(self, argv_for_port) -> tuple[subprocess.Popen, int]:
        """Запустить процесс-заглушку, который слушает свободный порт."""
        for _ in range(5):
            port = _free_port()
            proc = subprocess.Popen(argv_for_port(port), stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            if _wait_port(port, proc):
                self.addCleanup(_terminate, proc)
                return proc, port
            _terminate(proc)
        self.fail("не удалось занять свободный порт процессом-заглушкой")


class StopEndToEndTests(_CliProcessTests):
    """Настоящий `bin/listik stop`, когда pid-файла нет."""

    def setUp(self) -> None:
        super().setUp()
        self._skip_if_real_server_pid()

    def _run_stop(self, port: int, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--port", str(port), "stop"],
                              capture_output=True, text=True, env=env or self.env, timeout=60,
                              cwd=str(REPO_DIR))

    def test_free_port_says_not_running(self) -> None:
        result = self._run_stop(_free_port())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("сервер не запущен", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    @unittest.skipUnless(_has_lsof(), "нет lsof")
    def test_listik_server_is_stopped_by_port(self) -> None:
        script = self.tmp_path / "listik"
        script.write_text(FAKE_SERVE_PY, encoding="utf-8")
        proc, port = self._start_child(
            lambda p: [sys.executable, str(script), "serve", str(p)])
        fake = _fake_ps_bin(self.tmp_path, f"{sys.executable} {script} serve {port}")
        env = _path_with(fake, self.env)
        with mock.patch.dict(os.environ, {"PATH": env["PATH"]}):
            holder = _wait_holder(port)
        if holder is None:
            self.skipTest("lsof не видит слушающий порт в этом окружении")
        self.assertTrue(server.is_listik_serve(holder[1]), holder[1])

        result = self._run_stop(port, env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"остановлен (pid {holder[0]})", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        proc.wait(timeout=10)
        self.assertIsNotNone(proc.returncode, "заглушка `listik serve` должна была остановиться")

    @unittest.skipUnless(_has_lsof(), "нет lsof")
    def test_foreign_process_is_not_stopped(self) -> None:
        proc, port = self._start_child(
            lambda p: [sys.executable, "-c", FOREIGN_PY, str(p)])
        fake = _fake_ps_bin(self.tmp_path, "nginx: worker process")
        env = _path_with(fake, self.env)
        with mock.patch.dict(os.environ, {"PATH": env["PATH"]}):
            holder = _wait_holder(port)
        if holder is None:
            self.skipTest("lsof не видит слушающий порт в этом окружении")
        self.assertFalse(server.is_listik_serve(holder[1]), holder[1])

        result = self._run_stop(port, env=env)

        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("чужим процессом", result.stdout)
        self.assertIn(str(holder[0]), result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIsNone(proc.poll(), "чужой процесс не должен быть остановлен")


class ServeCliEndToEndTests(_CliProcessTests):
    """Настоящий `bin/listik serve` на занятом порте: сообщение, код 1, без трейсбека."""

    def setUp(self) -> None:
        super().setUp()
        self._skip_if_real_server_pid()
        self.sock = _listen()
        self.port = int(self.sock.getsockname()[1])
        self.addCleanup(self.sock.close)

    def _run_serve(self, *extra: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--port", str(self.port), "serve", *extra],
            capture_output=True, text=True, env=env or self.env, timeout=30, cwd=str(REPO_DIR))

    def test_foreground_reports_busy_port(self) -> None:
        result = self._run_serve("--no-embed")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"порт 127.0.0.1:{self.port} уже занят", result.stderr)
        self.assertIn("--port", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Listik слушает", result.stdout)
        self.assertFalse(PID_FILE.exists(), "неудачный serve не должен оставлять pid-файл")

    def test_daemon_does_not_fork_on_busy_port(self) -> None:
        result = self._run_serve("--daemon")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("уже занят", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("Listik в фоне", result.stdout)
        self.assertFalse(PID_FILE.exists(), "неудачный serve --daemon не должен оставлять pid-файл")

    @unittest.skipUnless(_has_lsof(), "нет lsof")
    def test_holder_pid_is_named(self) -> None:
        fake = _fake_ps_bin(self.tmp_path, "nginx: worker process")
        result = self._run_serve("--no-embed", env=_path_with(fake, self.env))
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"pid {os.getpid()}", result.stderr)  # слушатель — сам тест, pid из lsof
        self.assertIn("nginx", result.stderr)


if __name__ == "__main__":
    unittest.main()
