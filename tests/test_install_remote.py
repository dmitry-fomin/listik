"""listik-r69k, порция e: `install.sh` спрашивает про общий сервер и имя на нём.

Обвязка — из `tests/test_install_script.py`: функции модуля и методы `InstallScriptTests`,
взятые в этот класс как атрибуты (сам класс не наследуется — иначе все его тесты
прогнались бы второй раз).

«Общий сервер» — `http.server` на `127.0.0.1:0` с подставным `/api/health`: отвечает
заданным телом и пишет полученные заголовки в файл. Все вызовы `listik` идут через
шпиона — `python3`-обёртку первой в `PATH`, которая пишет свой argv в лог и делает
`exec` настоящего интерпретатора: так видно, что токена нет ни в одном argv.
"""
from __future__ import annotations

import errno
import http.server
import json
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import threading
import time
import tomllib
import unittest

# Модулем, а не `from … import InstallScriptTests`: имя класса в этом модуле unittest
# нашёл бы и прогнал все его тесты второй раз.
from tests import test_install_script as base
from tests.test_install_script import INSTALL_SH, SH, TAR, VERSION, free_port

TOKEN = "s3cret-X9Q7"
QUESTION = "Подключиться к общему серверу Listik?"
URL_PROMPT = "адрес сервера (https://…): "
TOKEN_PROMPT = "общий токен сервера: "
NAME_PROMPT = "как вас писать на сервере ["
LIST_PROMPT = base.InstallScriptTests.PROMPT
UNCHECKED = "ok (сервер не ответил, проверка позже: listik status)"
NO_TOKEN = "не удалось (нет токена: LISTIK_REMOTE_TOKEN)"
#: Флаги, которые закрывают все прочие вопросы установщика.
QUIET = ("--service", "no", "--mcp", "no", "--plugins", "no", "--swarm", "no",
         "--codex-network", "no")


def health(**data) -> dict:
    return {"ok": True, "data": {"status": "ok", "authed": True, "mode": "local", **data}}


class FakeRemote:
    """Подставной общий сервер: `/api/health` отвечает `body` со статусом `status`."""

    def __init__(self, headers_log: pathlib.Path, *, status: int = 200, body=None) -> None:
        raw = json.dumps(health() if body is None else body).encode("utf-8")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                with headers_log.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"path": self.path, **dict(self.headers)}) + "\n")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args) -> None:  # noqa: D102
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


@unittest.skipUnless(SH and TAR, "нужны sh и tar")
class InstallRemoteTests(unittest.TestCase):
    _base = base.InstallScriptTests.__dict__
    setUp = _base["setUp"]
    _stop_servers = _base["_stop_servers"]
    env = _base["env"]
    make_fake_tools = _base["make_fake_tools"]
    _add_bytes = _base["_add_bytes"]
    _add_tree = _base["_add_tree"]
    make_archive = _base["make_archive"]
    run_install = _base["run_install"]
    pty_session = _base["pty_session"]
    visible = _base["visible"]

    # --- обвязка ------------------------------------------------------------

    def remote(self, **kwargs) -> FakeRemote:
        srv = FakeRemote(self.tmp / "headers.log", **kwargs)
        self.addCleanup(srv.close)
        return srv

    def requests(self) -> list[dict]:
        log = self.tmp / "headers.log"
        if not log.exists():
            return []
        return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]

    def spy_env(self, **overrides: str) -> dict:
        """Окружение со шпионом argv первым в `PATH` и поддельными launchctl/claude."""
        spy = self.tmp / "spy"
        spy.mkdir(exist_ok=True)
        python = spy / "python3"
        python.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$*" >> "{self.tmp}/argv.log"\n'
                          f'exec "{sys.executable}" "$@"\n', encoding="utf-8")
        python.chmod(0o755)
        fake_dir, log = self.make_fake_tools()
        env = self.env(PATH=f"{spy}:{fake_dir}:{os.environ.get('PATH', '')}",
                       FAKE_LOG=str(log), LISTIK_PORT=str(free_port()),
                       no_proxy="127.0.0.1", NO_PROXY="127.0.0.1")
        env.update(overrides)
        return env

    def argv_log(self) -> str:
        log = self.tmp / "argv.log"
        return log.read_text(encoding="utf-8") if log.exists() else ""

    def config(self) -> dict:
        path = self.home / ".listik" / "config.toml"
        return tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def install_quiet(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        """Прогон без tty: своя сессия, stdin — /dev/null."""
        result = self.run_install("--archive", str(self.make_archive(VERSION)), *QUIET, *args,
                                  env=env or self.spy_env(), stdin=subprocess.DEVNULL,
                                  session=True, timeout=240)
        self.assert_token_hidden(result.stdout + result.stderr)
        return result

    def assert_token_hidden(self, out: str) -> None:
        self.assertNotIn(TOKEN, out, "токен напечатан установщиком")
        self.assertNotIn(TOKEN, self.argv_log(), "токен попал в argv")

    def status_line(self, out: str) -> str:
        found = re.findall(r"общий сервер: ([^\r\n]*)", out)
        self.assertEqual(len(found), 1, out)
        return found[0]

    # --- флаги и справка ----------------------------------------------------

    def test_server_flag_domain(self) -> None:
        cases = [("--server", "yes"), ("--server=",), ("--server", "ftp://host"),
                 ("--server=host:8787",)]
        for args in cases:
            with self.subTest(args=args):
                result = self.run_install(*args, "--yes", stdin=subprocess.DEVNULL)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("--server ждёт адрес http(s)://… или no, а не '", result.stderr)
        result = self.run_install("--server")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--server ждёт адрес http(s)://… или no", result.stderr)
        result = self.run_install("--owner")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--owner", result.stderr)
        self.assertFalse((self.home / ".listik").exists(), "флаг проверен после установки")

    def test_help_mentions_remote(self) -> None:
        result = self.run_install("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for text in ("--server URL|no", "--owner ИМЯ", "LISTIK_REMOTE_TOKEN",
                     "общий\n                        сервер не подключается"):
            self.assertIn(text, result.stdout)
        self.assertNotIn("--token", result.stdout)

    # --- без tty ------------------------------------------------------------

    def test_no_tty_no_flags_keeps_old_behaviour(self) -> None:
        srv = self.remote()
        for extra in ((), ("--yes",)):
            with self.subTest(extra=extra):
                env = self.spy_env(LISTIK_REMOTE_TOKEN=TOKEN)
                result = self.install_quiet(*extra, env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.status_line(result.stdout), "пропущен")
                self.assertNotIn(QUESTION, result.stdout + result.stderr)
                self.assertNotIn("remote", self.config())
                self.assertNotIn("remote set", self.argv_log())
        self.assertEqual(self.requests(), [])
        self.assertTrue(srv.url)

    def test_server_flag_env_token_ok(self) -> None:
        srv = self.remote(body=health(mode="server", users=["ann", "bob"]))
        result = self.install_quiet("--server", srv.url, "--owner", "ann",
                                    env=self.spy_env(LISTIK_REMOTE_TOKEN=TOKEN))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status_line(result.stdout), "ok")
        cfg = self.config()
        self.assertEqual(cfg["remote"], {"url": srv.url, "token": TOKEN})
        self.assertEqual(cfg["auth"]["owner"], "ann")
        req = self.requests()[-1]
        self.assertEqual(req["path"], "/api/health")
        self.assertEqual(req["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(req["X-Listik-Owner"], "ann")
        self.assertIn(f"remote set {srv.url} --owner ann --json", self.argv_log())
        self.assertIn("remote --json", self.argv_log())

    def test_owner_defaults_to_user_then_id(self) -> None:
        srv = self.remote()
        who = subprocess.run(["id", "-un"], capture_output=True, text=True).stdout.strip()
        for user, expected in (("zed", "zed"), ("", who)):
            with self.subTest(user=user):
                (self.tmp / "headers.log").unlink(missing_ok=True)
                cfg_path = self.home / ".listik" / "config.toml"
                cfg_path.unlink(missing_ok=True)
                env = self.spy_env(LISTIK_REMOTE_TOKEN=TOKEN, USER=user)
                result = self.install_quiet(f"--server={srv.url}", env=env)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.status_line(result.stdout), "ok")
                self.assertEqual(self.requests()[-1]["X-Listik-Owner"], expected)

    def test_refusals_keep_config_and_continue(self) -> None:
        cases = {
            "неверный токен": dict(status=401, body={"ok": False}),
            "чужое имя": dict(body=health(mode="server", users=["bob"])),
            "не Listik": dict(body={"ok": True}),
        }
        for name, kwargs in cases.items():
            with self.subTest(name):
                srv = self.remote(**kwargs)
                result = self.install_quiet("--server", srv.url, "--owner", "carol",
                                            env=self.spy_env(LISTIK_REMOTE_TOKEN=TOKEN))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.status_line(result.stdout), "не удалось")
                self.assertIn("'listik remote set' не выполнился", result.stderr)
                self.assertIn("маршруты:", result.stdout, "установка не дошла до конца")
                self.assertNotIn("remote", self.config())

    def test_unreachable_writes_unchecked(self) -> None:
        url = f"http://127.0.0.1:{free_port()}"
        result = self.install_quiet("--server", url, "--owner", "ann",
                                    env=self.spy_env(LISTIK_REMOTE_TOKEN=TOKEN))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.status_line(result.stdout), UNCHECKED)
        self.assertEqual(self.config()["remote"]["url"], url)

    def test_no_tty_without_token_fails_softly(self) -> None:
        srv = self.remote()
        for token_env in ({}, {"LISTIK_REMOTE_TOKEN": ""}):
            with self.subTest(env=token_env):
                result = self.install_quiet("--server", srv.url,
                                            env=self.spy_env(**token_env))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.status_line(result.stdout), NO_TOKEN)
                self.assertNotIn("remote", self.config())
        self.assertEqual(self.requests(), [])

    def test_already_configured_skips_question_even_with_yes(self) -> None:
        srv = self.remote()
        first = self.install_quiet("--server", srv.url, "--owner", "ann",
                                   env=self.spy_env(LISTIK_REMOTE_TOKEN=TOKEN))
        self.assertEqual(self.status_line(first.stdout), "ok")
        again = self.install_quiet("--yes", env=self.spy_env())
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(self.status_line(again.stdout), f"{srv.url} (уже настроен)")
        self.assertEqual(self.config()["remote"]["token"], TOKEN)
        self.assertEqual(len(self.requests()), 1, "повторная установка снова ходила на сервер")

    # --- вопросы под pty ----------------------------------------------------

    def run_answers(self, answers: list[tuple[str, bytes]], *, plain: bool = True,
                    extra_args: tuple[str, ...] = (), env: dict | None = None,
                    ) -> tuple[int, str]:
        """install.sh под pty; `answers` — (что дождаться в новом выводе, что ввести)."""
        env = env or self.spy_env()
        if plain:
            env["LISTIK_PLAIN"] = "1"
        else:
            env.pop("NO_COLOR", None)
            env.pop("CI", None)
            env["TERM"] = "xterm-256color"
        sent = 0
        pos = 0

        def step(buf: bytes) -> bytes | None:
            nonlocal sent, pos
            if sent >= len(answers):
                return None
            wait, data = answers[sent]
            if wait.encode("utf-8") in buf[pos:]:
                sent += 1
                pos = len(buf)
                return data
            return None

        # Повторная установка в тот же HOME спросила бы ещё и про маршруты.
        code, out = self.pty_session(env, None if plain else (40, 100), step, 240,
                                     extra_args=("--routes-reimport", "no", *extra_args))
        self.assertEqual(sent, len(answers), out)
        self.assert_token_hidden(out)
        return code, out

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_plain_asks_url_token_name(self) -> None:
        srv = self.remote(body=health(mode="server", users=["ann"]))
        code, out = self.run_answers([
            (LIST_PROMPT, b"1\r"),
            (LIST_PROMPT, b"1\r"),
            (QUESTION + " [y/N] ", b"y\r"),
            (URL_PROMPT, srv.url.encode() + b"\r"),
            (TOKEN_PROMPT, TOKEN.encode() + b"\r"),
            (NAME_PROMPT, b"ann\r"),
        ], env=self.spy_env(USER="zed"))
        self.assertEqual(code, 0, out)
        self.assertIn(NAME_PROMPT + "zed]: ", out)
        self.assertEqual(self.status_line(out), "ok")
        self.assertEqual(self.config()["remote"], {"url": srv.url, "token": TOKEN})
        self.assertEqual(self.requests()[-1]["X-Listik-Owner"], "ann")

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_plain_no_and_empty_url_skip(self) -> None:
        srv = self.remote()
        for answers in ([(QUESTION + " [y/N] ", b"\r")],
                        [(QUESTION + " [y/N] ", b"y\r"), (URL_PROMPT, b"\r")]):
            with self.subTest(answers=answers):
                code, out = self.run_answers(
                    [(LIST_PROMPT, b"1\r"), (LIST_PROMPT, b"1\r"), *answers])
                self.assertEqual(code, 0, out)
                self.assertEqual(self.status_line(out), "пропущен")
                self.assertNotIn(TOKEN_PROMPT, out)
        self.assertEqual(self.requests(), [])
        self.assertTrue(srv.url)

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_env_token_and_default_name(self) -> None:
        srv = self.remote()
        code, out = self.run_answers([
            (LIST_PROMPT, b"1\r"),
            (LIST_PROMPT, b"1\r"),
            (NAME_PROMPT + "zed]: ", b"\r"),
        ], extra_args=("--server", srv.url),
            env=self.spy_env(USER="zed", LISTIK_REMOTE_TOKEN=TOKEN))
        self.assertEqual(code, 0, out)
        self.assertNotIn(QUESTION, out)
        self.assertNotIn(TOKEN_PROMPT, out)
        self.assertEqual(self.status_line(out), "ok")
        self.assertEqual(self.requests()[-1]["X-Listik-Owner"], "zed")

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_pty_decorated_menu_then_text(self) -> None:
        srv = self.remote()
        keys = "Пробел отметить"
        code, out = self.run_answers([
            ("Какие нейронки", b"\r"),
            ("Какие харнессы", b"\r"),
            (QUESTION, b"y"),
            (URL_PROMPT, srv.url.encode() + b"\r"),
            (TOKEN_PROMPT, TOKEN.encode() + b"\r"),
            (NAME_PROMPT, b"ann\r"),
        ], plain=False)
        self.assertEqual(code, 0, out)
        self.assertIn(keys, out, "меню с галочками не показано")
        self.assertIn("\x1b[", out, "оформленный режим не включился")
        report = re.findall(r"общий:\s+([^\r\n]*)", self.visible(out))
        self.assertEqual([r.strip() for r in report], ["ok"], out)
        self.assertEqual(self.config()["remote"]["token"], TOKEN)
        # Строчный ввод идёт без живой строки панели: перед приглашением адреса нет
        # недорисованной строки шага.
        before = self.visible(out).split(URL_PROMPT)[0].rsplit("\n", 1)[-1]
        self.assertNotIn("✓", before)

    # --- Ctrl+C на приглашении токена ---------------------------------------

    def ctrl_c_at_token(self, *, plain: bool) -> str:
        env = self.spy_env()
        if plain:
            env["LISTIK_PLAIN"] = "1"
        else:
            env.pop("NO_COLOR", None)
            env.pop("CI", None)
            env["TERM"] = "xterm-256color"
        script = ('trap : INT; sh "$0" "$@"; echo "rc=$?"; stty -a')
        argv = [SH, "-c", script, str(INSTALL_SH), "--archive",
                str(self.make_archive(VERSION)), *QUIET, "--server", "http://127.0.0.1:9"]
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover — дочерний процесс
            try:
                import fcntl
                import struct
                import termios
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 100, 0, 0))
                os.execve(SH, argv, env)
            finally:
                os._exit(127)
        buf = b""
        sent = False
        try:
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if ready:
                    try:
                        chunk = os.read(fd, 4096)
                    except OSError as exc:
                        if exc.errno == errno.EIO:
                            break
                        raise
                    if not chunk:
                        break
                    buf += chunk
                if not sent and TOKEN_PROMPT.encode("utf-8") in buf:
                    time.sleep(0.3)
                    os.write(fd, b"\x03")
                    sent = True
            os.waitpid(pid, 0)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except OSError:
                pass
            os.close(fd)
        out = buf.decode("utf-8", errors="replace")
        self.assertTrue(sent, out)
        return out

    @unittest.skipUnless(shutil.which("stty"), "нужен stty")
    def test_ctrl_c_at_token_restores_echo(self) -> None:
        for plain in (True, False):
            with self.subTest(plain=plain):
                out = self.ctrl_c_at_token(plain=plain)
                self.assertIn("rc=130", out)
                stty = out[out.rindex("rc=130"):]
                self.assertRegex(stty, r"(?<![-\w])echo(?!\w)", "эхо не вернулось")
                self.assertNotRegex(stty, r"-echo(?!\w)", "эхо не вернулось")
                self.assertNotIn("remote", self.config())


if __name__ == "__main__":
    unittest.main()
