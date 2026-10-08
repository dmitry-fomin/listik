"""`[remote]` клиента, `listik remote`, рой в серверном режиме (listik-r69k, порция a).

CLI запускается подпроцессом с `LISTIK_CONFIG`/`LISTIK_DB`/`LISTIK_LOG` во временном
каталоге; токен — только через `LISTIK_REMOTE_TOKEN` или stdin. Сервер — подставной
`http.server` на 127.0.0.1:0, сети наружу нет.
"""
from __future__ import annotations

import difflib
import http.server
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest import mock

from listik import config as config_mod
from listik import errors, paths, swarm_proc

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"
TOKEN = "tk-9f3a7c1e"


class TmpCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        self.cfg = self.tmp / "config.toml"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class NormalizeUrlTests(unittest.TestCase):
    def test_valid(self) -> None:
        cases = {
            " https://H.Example ": "https://h.example",
            "https://h:443": "https://h",
            "http://h:80/": "http://h",
            "http://h:8787": "http://h:8787",
            "https://h:80": "https://h:80",
            "HTTP://[::1]:8787": "http://[::1]:8787",
            "https://h/Listik/": "https://h/Listik",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(config_mod.normalize_url(raw), want)

    def test_invalid(self) -> None:
        for raw in ("", "h:8787", "ftp://h", "http://", "http://:80", "http://u:p@h",
                    "http://h?x=1", "http://h#f", "http://h:abc", "http://h:0",
                    "http://h:70000", "http://h:", "http://h h", "http://[::1", 5, None):
            with self.subTest(raw=raw):
                with self.assertRaises(errors.BadArgument):
                    config_mod.normalize_url(raw)

    def test_same_server(self) -> None:
        self.assertTrue(config_mod.same_server("https://h:443/", "HTTPS://h"))
        self.assertFalse(config_mod.same_server("https://h", "http://h"))


class RemoteTests(unittest.TestCase):
    def test_absent_or_empty(self) -> None:
        self.assertIsNone(config_mod.remote({}))
        self.assertIsNone(config_mod.remote({"remote": {"url": "", "token": "x"}}))

    def test_valid(self) -> None:
        self.assertEqual(config_mod.remote({"remote": {"url": "https://h:443/"}}),
                         {"url": "https://h", "token": ""})

    def test_invalid_url_names_key_and_value(self) -> None:
        with self.assertRaises(errors.BadArgument) as ctx:
            config_mod.remote({"remote": {"url": "ftp://bad-host"}})
        self.assertIn("remote.url", str(ctx.exception))
        self.assertIn("ftp://bad-host", str(ctx.exception))

    def test_scalar_remote(self) -> None:
        with self.assertRaises(errors.BadArgument):
            config_mod.remote({"remote": "x"})


class SetRemoteTests(TmpCase):
    ORIGINAL = ("# мой конфиг\n"
                "[auth]\n"
                'token = "local-board-token"\n'
                "\n"
                "[routing]\n"
                "return_window_hours = 12  # окно\n"
                "\n"
                "[swarm]\n"
                "enabled = false\n")

    def assert_only_inserted(self, before: str, after: str) -> None:
        ops = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines()).get_opcodes()
        self.assertTrue(all(op in ("equal", "insert") for op, *_ in ops), after)

    def test_creates_file_0600(self) -> None:
        config_mod.set_remote("https://H:443/", TOKEN, path=self.cfg)
        self.assertEqual(stat.S_IMODE(self.cfg.stat().st_mode), 0o600)
        parsed = tomllib.loads(self.cfg.read_text(encoding="utf-8"))
        self.assertEqual(parsed, {"remote": {"url": "https://h", "token": TOKEN}})

    def test_keeps_everything_else(self) -> None:
        self.cfg.write_text(self.ORIGINAL, encoding="utf-8")
        config_mod.set_remote("http://hub:8787", TOKEN, owner="ann", path=self.cfg)
        text = self.cfg.read_text(encoding="utf-8")
        self.assert_only_inserted(self.ORIGINAL, text)
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["auth"], {"token": "local-board-token", "owner": "ann"})
        self.assertEqual(parsed["remote"], {"url": "http://hub:8787", "token": TOKEN})
        self.assertEqual(parsed["routing"], {"return_window_hours": 12})
        self.assertEqual(stat.S_IMODE(self.cfg.stat().st_mode), 0o600)

    def test_replaces_and_owner_none_keeps_auth(self) -> None:
        original = ('[auth]\nowner = "bob"\n\n[remote]\nurl = "http://old"\n'
                    'token = "tk-old0001"\n')
        self.cfg.write_text(original, encoding="utf-8")
        config_mod.set_remote("http://new:1", TOKEN, owner=None, path=self.cfg)
        text = self.cfg.read_text(encoding="utf-8")
        self.assertTrue(text.startswith('[auth]\nowner = "bob"\n\n[remote]\n'))
        self.assertEqual(tomllib.loads(text)["remote"], {"url": "http://new:1", "token": TOKEN})
        config_mod.set_remote("http://new:1", TOKEN, owner="", path=self.cfg)
        self.assertEqual(tomllib.loads(self.cfg.read_text(encoding="utf-8"))["auth"],
                         {"owner": "bob"})

    def test_refusals_leave_file_untouched(self) -> None:
        for text in ("[auth\n", 'remote = {url = "http://h"}\n', 'remote.url = "http://h"\n',
                     'remote = "x"\n', '[[remote]]\nurl = "http://h"\n'):
            with self.subTest(text=text):
                self.cfg.write_text(text, encoding="utf-8")
                with self.assertRaises(ValueError):
                    config_mod.set_remote("http://h", TOKEN, path=self.cfg)
                self.assertEqual(self.cfg.read_text(encoding="utf-8"), text)

    def test_bad_url_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            config_mod.set_remote("ftp://h", TOKEN, path=self.cfg)
        self.assertFalse(self.cfg.exists())


class SwarmServerModeTests(TmpCase):
    def test_swarm_enabled_false_in_server_mode(self) -> None:
        cfg = {"server": {"mode": "server"}, "swarm": {"enabled": True}}
        self.assertFalse(config_mod.swarm_enabled(cfg))
        self.assertTrue(config_mod.swarm_enabled({"swarm": {"enabled": True}}))

    def test_runtime_disabled_reason(self) -> None:
        self.cfg.write_text('[server]\nmode = "server"\n\n[swarm]\nenabled = true\n',
                            encoding="utf-8")
        with mock.patch.object(paths, "CONFIG_PATH", self.cfg), \
                mock.patch.object(swarm_proc, "_current", None):
            info = swarm_proc.runtime()
        self.assertFalse(info["enabled"])
        self.assertEqual(info["disabled_reason"], "server_mode")
        self.cfg.write_text("[swarm]\nenabled = false\n", encoding="utf-8")
        with mock.patch.object(paths, "CONFIG_PATH", self.cfg), \
                mock.patch.object(swarm_proc, "_current", None):
            self.assertNotIn("disabled_reason", swarm_proc.runtime())


class FakeServer:
    """Подставной /api/health: отвечает `status`/`body`, пишет заголовки запросов."""

    def __init__(self, status: int = 200, body: object = None, headers: dict | None = None):
        self.status, self.body, self.extra = status, body, headers or {}
        self.requests: list[dict] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.requests.append({"path": self.path, **dict(self.headers)})
                raw = outer.body if isinstance(outer.body, bytes) else \
                    json.dumps(outer.body).encode()
                self.send_response(outer.status)
                for key, value in outer.extra.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def health(**data) -> dict:
    return {"ok": True, "data": {"status": "ok", "authed": True, "mode": "local", **data}}


class RemoteCliTests(TmpCase):
    def setUp(self) -> None:
        super().setUp()
        self.cwd = self.tmp / "work"
        (self.cwd / ".git").mkdir(parents=True)

    def server(self, *args, **kwargs) -> FakeServer:
        srv = FakeServer(*args, **kwargs)
        self.addCleanup(srv.close)
        return srv

    def run_cli(self, *args, input: str | None = None, **env_extra):
        env = {**os.environ, "LISTIK_CONFIG": str(self.cfg),
               "LISTIK_DB": str(self.tmp / "listik.db"), "LISTIK_LOG": str(self.tmp / "listik.log"),
               "no_proxy": "127.0.0.1", "NO_PROXY": "127.0.0.1"}
        env.pop("LISTIK_OWNER", None)
        env.pop("LISTIK_REMOTE_TOKEN", None)
        env.update(env_extra)
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), *args], capture_output=True,
                              text=True, env=env, cwd=str(self.cwd), input=input or "",
                              timeout=30)
        self.assertNotIn(TOKEN, proc.stdout + proc.stderr)
        return proc

    def config_text(self) -> str:
        return self.cfg.read_text(encoding="utf-8") if self.cfg.exists() else ""

    def refused(self, proc, code: str) -> dict:
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        err = json.loads(proc.stdout)["error"]
        self.assertEqual(err["code"], code, err)
        return err

    def test_ok_sends_headers_and_writes(self) -> None:
        srv = self.server(body=health())
        proc = self.run_cli("remote", "set", srv.url + "/", "--owner", "ann", "--json",
                            LISTIK_REMOTE_TOKEN=TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out, {"url": srv.url, "owner": "ann", "config_path": str(self.cfg),
                               "checked": True})
        req = srv.requests[0]
        self.assertEqual(req["path"], "/api/health")
        self.assertEqual(req["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(req["X-Listik-Owner"], "ann")
        parsed = tomllib.loads(self.config_text())
        self.assertEqual(parsed["remote"], {"url": srv.url, "token": TOKEN})
        self.assertEqual(parsed["auth"], {"owner": "ann"})

    def test_unreachable_writes_with_warning(self) -> None:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        url = f"http://127.0.0.1:{port}"
        proc = self.run_cli("remote", "set", url, "--json", LISTIK_REMOTE_TOKEN=TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["checked"])
        self.assertIn(f"! сервер {url} не ответил — настройки записаны без проверки", proc.stderr)
        self.assertEqual(tomllib.loads(self.config_text())["remote"]["url"], url)

    def test_refusals_do_not_touch_config(self) -> None:
        original = '# мой\n[auth]\ntoken = "local-board-token"\n'
        self.cfg.write_text(original, encoding="utf-8")
        other = self.server(body=health())
        cases = [
            (dict(status=401, body={"ok": False}), "unauthorized"),
            (dict(body=health(authed=False)), "unauthorized"),
            (dict(status=404, body={"ok": False}), "bad_argument"),
            (dict(status=403, body={"ok": False}), "bad_argument"),
            (dict(status=500, body=b"oops"), "bad_argument"),
            (dict(body=b"<html>"), "bad_argument"),
            (dict(body={"ok": True}), "bad_argument"),
            (dict(body={"ok": True, "data": {"authed": True}}), "bad_argument"),
            (dict(status=302, body=b"", headers={"Location": other.url + "/api/health"}),
             "bad_argument"),
            (dict(body=health(mode="server", users=["ann", "bob"])), "bad_argument"),
        ]
        for kwargs, code in cases:
            with self.subTest(kwargs=kwargs):
                srv = self.server(**kwargs)
                err = self.refused(self.run_cli("remote", "set", srv.url, "--owner", "carol",
                                                "--json", LISTIK_REMOTE_TOKEN=TOKEN), code)
                if code == "bad_argument" and "users" not in repr(kwargs):
                    self.assertIn("не сервер Listik", err["message"])
                self.assertEqual(self.config_text(), original)
        self.assertEqual(other.requests, [], "редирект выполнен — Authorization ушёл на чужой адрес")
        self.assertIn("carol", err["message"])
        self.assertIn("ann, bob", err["message"])

    def test_server_mode_user_check_and_missing_owner(self) -> None:
        srv = self.server(body=health(mode="server", users=["ann"]))
        proc = self.run_cli("remote", "set", srv.url, LISTIK_REMOTE_TOKEN=TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("! имя на сервере не задано: --owner, LISTIK_OWNER или [auth] owner",
                      proc.stderr)
        self.assertNotIn("X-Listik-Owner", srv.requests[0])
        proc = self.run_cli("remote", "set", srv.url, LISTIK_REMOTE_TOKEN=TOKEN,
                            LISTIK_OWNER="ann")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"общий сервер: {srv.url}\nвладелец: ann\nконфиг: {self.cfg}", proc.stdout)
        self.assertEqual(tomllib.loads(self.config_text())["auth"], {"owner": "ann"})

    def test_owner_from_config_is_not_rewritten(self) -> None:
        original = '[auth]\nowner = "ann"  # я\n'
        self.cfg.write_text(original, encoding="utf-8")
        srv = self.server(body=health(mode="server"))
        proc = self.run_cli("remote", "set", srv.url, LISTIK_REMOTE_TOKEN=TOKEN)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(srv.requests[0]["X-Listik-Owner"], "ann")
        self.assertTrue(self.config_text().startswith(original))

    def test_token_from_stdin_and_bad_tokens(self) -> None:
        srv = self.server(body=health())
        proc = self.run_cli("remote", "set", srv.url, input=f"  {TOKEN}  \nrest\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(srv.requests[0]["Authorization"], f"Bearer {TOKEN}")
        self.cfg.unlink()
        for kwargs in ({"input": "\n"}, {"LISTIK_REMOTE_TOKEN": "", "input": ""},
                       {"LISTIK_REMOTE_TOKEN": "tk-a\x01b"},
                       {"LISTIK_REMOTE_TOKEN": "tk-a\nb"}):
            with self.subTest(kwargs=repr(kwargs)):
                self.refused(self.run_cli("remote", "set", srv.url, "--json", **kwargs),
                             "bad_argument")
                self.assertFalse(self.cfg.exists())
        self.assertEqual(len(srv.requests), 1)

    def test_bad_url_and_parse_errors(self) -> None:
        for argv in (("remote", "set", "ftp://x"), ("remote", "set"), ("remote", "bogus")):
            with self.subTest(argv=argv):
                proc = self.run_cli(*argv, "--json", LISTIK_REMOTE_TOKEN=TOKEN)
                self.refused(proc, "bad_argument")
                self.assertNotIn("Traceback", proc.stderr)
                self.assertFalse(self.cfg.exists())

    def test_show(self) -> None:
        proc = self.run_cli("remote", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"url": None, "owner": None,
                                                   "token_set": False, "project_file": None})
        config_mod.set_remote("http://hub:8787", TOKEN, owner="ann", path=self.cfg)
        (self.cwd / ".listik.toml").write_text('server = "http://hub:8787"\nproject = "demo"\n',
                                               encoding="utf-8")
        out = json.loads(self.run_cli("remote", "--json").stdout)
        self.assertEqual(out["url"], "http://hub:8787")
        self.assertEqual(out["owner"], "ann")
        self.assertTrue(out["token_set"])
        self.assertEqual(out["project_file"]["project"], "demo")
        text = self.run_cli("remote").stdout
        self.assertIn("общий сервер: http://hub:8787", text)
        self.assertIn("токен: задан", text)

    def test_show_reports_errors_as_fields(self) -> None:
        self.cfg.write_text('[remote]\nurl = "ftp://x"\ntoken = "tk-9f3a7c1e"\n',
                            encoding="utf-8")
        (self.cwd / ".listik.toml").write_text('server = "local"\nproject = "p"\nx = 1\n',
                                               encoding="utf-8")
        proc = self.run_cli("remote", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertIn("remote.url", out["remote_error"])
        self.assertIn(".listik.toml", out["project_file_error"])
        self.assertIsNone(out["project_file"])

    def test_swarm_in_server_mode(self) -> None:
        original = '[server]\nmode = "server"\nport = 1\n'
        self.cfg.write_text(original, encoding="utf-8")
        err = self.refused(self.run_cli("swarm", "on", "--json"), "bad_argument")
        self.assertIn("в серверном режиме рой не запускается", err["message"])
        self.assertEqual(self.config_text(), original)
        proc = self.run_cli("swarm", "status")
        self.assertIn("рой:    выключен: серверный режим", proc.stdout)
        self.assertEqual(self.run_cli("swarm", "off").returncode, 0)


if __name__ == "__main__":
    unittest.main()
