"""CLI по цели: общий сервер из `.listik.toml`, без молчаливого фолбэка (listik-r69k, порция b).

«Общий сервер» — сервер Listik в процессе теста со своей временной базой и конфигом
(`[server] mode = "server"`, `users = ["ann", "bob"]`). CLI — `bin/listik` подпроцессом
со своим конфигом и базой клиента (`LISTIK_CONFIG`/`LISTIK_DB`/`LISTIK_LOG`), `cwd` —
временный git-клон с `.listik.toml`, `HOME` — временный. Заголовки, дошедшие до
сервера, пишет подмена `server.Handler._authed`.
"""
from __future__ import annotations

import json
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest import mock

from listik import client, errors, paths, server, store
from listik import db as db_mod

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"
REMOTE_TOKEN = "tok-remote-7f3a"
LOCAL_TOKEN = "tok-local-91bc"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def git(cwd, *argv) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv], cwd=cwd,
                   check=True, capture_output=True)


class RemoteCliCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # realpath: cwd подпроцесса на macOS — /private/var/…, а не /var/…
        tmp = pathlib.Path(self._tmp.name).resolve()
        # --- общий сервер
        self._saved = (paths.DB_PATH, paths.CONFIG_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = tmp / "server.db"
        paths.CONFIG_PATH = tmp / "server.toml"
        paths.CONFIG_PATH.write_text(
            f'[auth]\ntoken = "{REMOTE_TOKEN}"\n\n[server]\nmode = "server"\n'
            'users = ["ann", "bob"]\n', encoding="utf-8")
        conn = db_mod.init()
        store.add_project(conn, slug="demo")
        store.add_project(conn, slug="other")
        conn.close()
        self.seen: list[dict] = []
        original = server.Handler._authed

        def spy(handler, *a, **kw):
            self.seen.append({"path": handler.path, "headers": dict(handler.headers)})
            return original(handler, *a, **kw)

        self._spy = mock.patch.object(server.Handler, "_authed", spy)
        self._spy.start()
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        # --- клиент
        self.home = tmp / "home"
        self.home.mkdir()
        self.client_dir = tmp / "client"
        self.client_dir.mkdir()
        self.client_db = self.client_dir / "listik.db"
        self.client_cfg = self.client_dir / "config.toml"
        self.write_client_cfg(self.url)
        self.clone = tmp / "clone"
        self.clone.mkdir()
        self.write_project_file(self.url)
        git(self.clone, "init", "-q", "-b", "main")
        git(self.clone, "add", ".")
        git(self.clone, "commit", "-q", "-m", "init")

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self._spy.stop()
        paths.DB_PATH, paths.CONFIG_PATH, server._conn_made, server._conn_local = self._saved
        self._tmp.cleanup()

    # --- помощники
    def write_client_cfg(self, remote_url: str | None, remote_token: str = REMOTE_TOKEN,
                         mode: str | None = None) -> None:
        text = (f'[auth]\nowner = "ann"\ntoken = "{LOCAL_TOKEN}"\n\n'
                f'[server]\nhost = "127.0.0.1"\nport = {free_port()}\n')
        if mode:
            text += f'mode = "{mode}"\n'
        if remote_url:
            text += f'\n[remote]\nurl = "{remote_url}"\ntoken = "{remote_token}"\n'
        self.client_cfg.write_text(text, encoding="utf-8")

    def write_project_file(self, server_value: str, project: str = "demo") -> None:
        (self.clone / ".listik.toml").write_text(
            f'server = "{server_value}"\nproject = "{project}"\n', encoding="utf-8")

    def cli(self, *argv, cwd=None, stdin: str = "") -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items()
               if k not in ("LISTIK_OWNER", "LISTIK_ACTOR", "LISTIK_PROJECT",
                            "LISTIK_REMOTE_TOKEN", "LISTIK_TASK", "LISTIK_GENERATION")}
        env.update(HOME=str(self.home), LISTIK_HOME=str(self.client_dir),
                   LISTIK_CONFIG=str(self.client_cfg), LISTIK_DB=str(self.client_db),
                   LISTIK_LOG=str(self.client_dir / "listik.log"), USER="unix-user")
        return subprocess.run([sys.executable, str(LISTIK_BIN), *argv],
                              cwd=str(cwd or self.clone), env=env, input=stdin,
                              capture_output=True, text=True, timeout=60)

    def json_of(self, proc: subprocess.CompletedProcess):
        return json.loads(proc.stdout)

    def server_rows(self, sql: str, *params) -> list:
        conn = sqlite3.connect(paths.DB_PATH)
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def client_rows(self) -> int:
        """Сколько строк в задачах/комментариях/событиях/заметках локальной базы клиента."""
        if not self.client_db.exists():
            return 0
        conn = sqlite3.connect(self.client_db)
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
            return sum(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for t in ("tasks", "comments", "events", "memories") if t in names)
        finally:
            conn.close()

    def make_task(self, project: str = "demo") -> str:
        proc = self.cli("new", "задача", "--project", project, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return self.json_of(proc)["id"]

    def assert_tokens(self) -> None:
        auths = [s["headers"].get("Authorization") for s in self.seen]
        self.assertTrue(auths)
        self.assertEqual(set(auths), {f"Bearer {REMOTE_TOKEN}"})


class RemoteTargetTests(RemoteCliCase):
    def test_writes_and_reads_go_to_remote_with_remote_token(self) -> None:
        task_id = self.make_task()
        rows = self.server_rows("SELECT project, created_by FROM tasks WHERE id = ?", task_id)
        self.assertEqual(rows, [("demo", "ann")])
        proc = self.cli("list")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(task_id, proc.stdout)
        self.assertIn("# проект из .listik.toml: demo (все проекты: --project all)", proc.stderr)
        self.assert_tokens()
        self.assertEqual({s["headers"].get("X-Listik-Owner") for s in self.seen}, {"ann"})
        self.assertEqual(self.client_rows(), 0)

    def test_owner_flag_is_default_actor(self) -> None:
        proc = self.cli("--owner", "bob", "new", "от боба", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        task_id = self.json_of(proc)["id"]
        self.assertEqual(self.server_rows("SELECT created_by FROM tasks WHERE id = ?", task_id),
                         [("bob",)])

    def test_dep_cycles_from_ready(self) -> None:
        proc = self.cli("dep", "cycles")
        self.assertEqual((proc.returncode, proc.stdout.strip()), (0, "циклов нет"), proc.stderr)
        self.assertTrue(any(s["path"].startswith("/api/ready") for s in self.seen))
        self.assertEqual(self.client_rows(), 0)
        self.assertFalse(self.client_db.exists())

    def test_remember_unsupported(self) -> None:
        proc = self.cli("remember", "факт", "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.json_of(proc)["error"]["code"], errors.UNSUPPORTED)
        self.assertIn("listik_remember", proc.stdout)
        self.assertFalse(self.client_db.exists())

    def test_watch_unsupported(self) -> None:
        proc = self.cli("watch", "--project", "demo", "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(self.json_of(proc)["error"]["code"], errors.UNSUPPORTED)

    def test_wrong_remote_token_unauthorized(self) -> None:
        self.write_client_cfg(self.url, remote_token="wrong")
        proc = self.cli("list", "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = self.json_of(proc)["error"]
        self.assertEqual(err["code"], errors.UNAUTHORIZED)
        self.assertIn(f"listik remote set {self.url}", err["hint"])
        self.assertNotIn("wrong", proc.stdout + proc.stderr)

    def test_status_remote(self) -> None:
        proc = self.cli("status")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout
        self.assertIn(f"сервер: {self.url} (общий, из {self.clone / '.listik.toml'})", out)
        self.assertIn("проект: demo", out)
        self.assertIn(f"доска:  {self.url}/\n", out)
        for absent in (REMOTE_TOKEN, LOCAL_TOKEN, "pid", "данные сервера", "код сервера",
                       "база:", "несовпадение"):
            self.assertNotIn(absent, out)
        data = self.json_of(self.cli("status", "--json"))
        self.assertEqual((data["target"], data["url"], data["pid"], data["server"]),
                         ("remote", self.url, None, "up"))
        self.assertEqual(data["project_file"], str(self.clone / ".listik.toml"))
        self.assertEqual(data["diagnostics"]["installation"], "remote")
        self.assertEqual(data["diagnostics"]["token"], "accepted")
        self.assertNotIn(REMOTE_TOKEN, json.dumps(data))
        self.assert_tokens()

    def test_status_local_target_fields(self) -> None:
        self.write_project_file("local")
        data = self.json_of(self.cli("status", "--json"))
        self.assertEqual(data["target"], "local")
        self.assertEqual(data["project_file"], str(self.clone / ".listik.toml"))
        (self.clone / ".listik.toml").unlink()
        data = self.json_of(self.cli("status", "--json"))
        self.assertEqual((data["target"], data["project_file"]), ("local", None))
        self.assertEqual(self.seen, [])

    def test_worktree_in_own_clone(self) -> None:
        task_id = self.make_task()
        proc = self.cli("worktree", task_id, "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        tree = self.clone / ".worktrees" / task_id
        self.assertEqual(pathlib.Path(self.json_of(proc)["path"]).resolve(), tree.resolve())
        card = self.server_rows("SELECT worktree FROM tasks WHERE id = ?", task_id)[0][0]
        self.assertEqual(pathlib.Path(card).resolve(), tree.resolve())
        # Из дерева задачи корень — основной клон, а не само дерево.
        second = self.make_task()
        proc = self.cli("worktree", second, "--json", cwd=tree)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(pathlib.Path(self.json_of(proc)["path"]).resolve(),
                         (self.clone / ".worktrees" / second).resolve())

    def test_worktree_other_project_refused(self) -> None:
        task_id = self.make_task("other")
        proc = self.cli("worktree", task_id, "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = self.json_of(proc)["error"]
        self.assertEqual(err["code"], errors.BAD_ARGUMENT)
        self.assertIn("(demo)", err["message"])

    def test_projects_add_without_path(self) -> None:
        conn = sqlite3.connect(paths.DB_PATH)
        conn.execute("DELETE FROM projects WHERE slug = 'demo'")
        conn.commit()
        conn.close()
        proc = self.cli("projects", "--add", ".")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "проект demo добавлен на общий сервер (без каталога)")
        self.assertEqual(self.server_rows("SELECT path FROM projects WHERE slug = 'demo'"),
                         [(None,)])

    def test_local_commands_never_touch_remote(self) -> None:
        for argv in (["swarm", "status", "--json"], ["remote", "--json"]):
            with self.subTest(argv=argv):
                proc = self.cli(*argv)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertEqual(self.seen, [])
        (self.clone / ".listik.toml").write_text("server = 1\n", encoding="utf-8")
        proc = self.cli("swarm", "status", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_broken_project_file_reported(self) -> None:
        (self.clone / ".listik.toml").write_text("server = 1\n", encoding="utf-8")
        proc = self.cli("list")
        self.assertNotEqual(proc.returncode, 0)
        self.assertTrue(proc.stderr.startswith("ошибка:"), proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        proc = self.cli("list", "--json")
        self.assertEqual(self.json_of(proc)["error"]["code"], errors.BAD_ARGUMENT)

    def test_local_flag_is_explicit_bypass(self) -> None:
        line = f"! --local: проект живёт на {self.url}, запись уйдёт в локальную базу этой машины"
        self.write_client_cfg(None)  # и без [remote] это не ошибка
        proc = self.cli("--local", "new", "мимо сервера", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(line, proc.stderr)
        self.assertEqual(self.json_of(proc)["project"], "demo")
        proc = self.cli("--local", "list")
        self.assertNotIn(line, proc.stderr)
        self.assertEqual(self.seen, [])


class TargetMismatchTests(RemoteCliCase):
    def test_no_remote_config(self) -> None:
        self.write_client_cfg(None)
        proc = self.cli("list", "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = self.json_of(proc)["error"]
        self.assertEqual(err["code"], errors.BAD_ARGUMENT)
        self.assertIn(self.url, err["message"])
        self.assertIn(str(self.clone / ".listik.toml"), err["message"])
        self.assertIn(f"listik remote set {self.url}", err["hint"])
        self.assertEqual(self.seen, [])

    def test_other_remote_configured(self) -> None:
        self.write_client_cfg("https://elsewhere.example")
        proc = self.cli("list", "--json")
        err = self.json_of(proc)["error"]
        self.assertEqual(err["code"], errors.BAD_ARGUMENT)
        self.assertIn(self.url, err["message"])
        self.assertIn("https://elsewhere.example", err["message"])
        self.assertEqual(self.seen, [])

    def test_server_machine_without_remote_is_local(self) -> None:
        self.write_client_cfg(None, mode="server")
        data = self.json_of(self.cli("status", "--json"))
        self.assertEqual(data["target"], "local")
        self.assertEqual(self.seen, [])


class UnreachableTests(RemoteCliCase):
    def setUp(self) -> None:
        super().setUp()
        dead = f"http://127.0.0.1:{free_port()}"
        self.dead = dead
        self.write_client_cfg(dead)
        self.write_project_file(dead)

    def test_no_local_fallback(self) -> None:
        commands = (["list"], ["new", "x"], ["show", "demo-0001"],
                    ["comment", "demo-0001", "текст"], ["ready"], ["dep", "cycles"],
                    ["projects"], ["routes", "--hide", "x"], ["routes", "--reimport"],
                    ["search", "x"], ["memory"])
        for argv in commands:
            with self.subTest(argv=argv):
                proc = self.cli(*argv, "--json")
                self.assertNotEqual(proc.returncode, 0)
                err = self.json_of(proc)["error"]
                self.assertEqual(err["code"], errors.UNREACHABLE)
                self.assertIn(f"общий сервер {self.dead} не отвечает", err["message"])
                self.assertIn("listik status", err["hint"])
                self.assertNotIn(REMOTE_TOKEN, proc.stdout + proc.stderr)
        proc = self.cli("list")
        self.assertIn(f"общий сервер {self.dead} не отвечает", proc.stderr)
        self.assertNotIn("напрямую по базе", proc.stderr)
        self.assertEqual(self.client_rows(), 0)
        self.assertFalse(self.client_db.exists())
        log = self.client_dir / "listik.log"
        self.assertNotIn(REMOTE_TOKEN, log.read_text(encoding="utf-8") if log.exists() else "")

    def test_status_down(self) -> None:
        proc = self.cli("status")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"сервер: {self.dead} не отвечает", proc.stdout)
        self.assertNotIn("listik serve --daemon", proc.stdout)
        data = self.json_of(self.cli("status", "--json"))
        self.assertEqual((data["server"], data["target"]), ("down", "remote"))

    def test_stop_hook_silent(self) -> None:
        proc = self.cli("lint", "--stop-hook", stdin="{}")
        self.assertEqual((proc.returncode, proc.stdout, proc.stderr), (0, "", ""))
        self.assertFalse(self.client_db.exists())


class ClientTargetUnitTests(unittest.TestCase):
    def tearDown(self) -> None:
        client.use_target(None)

    def test_https_prefix_and_remote_token(self) -> None:
        client.use_target(client.Target("remote", "https://h/listik", "rt", None))
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append((req.full_url, req.get_header("Authorization")))
            raise urllib.error.URLError("down")

        with mock.patch.object(client.urllib.request, "urlopen", fake_urlopen):
            with self.assertRaises(errors.ListikError) as ctx:
                client.request("GET", "/api/tasks", query={"limit": 1})
        self.assertEqual(ctx.exception.code, errors.UNREACHABLE)
        self.assertTrue(seen[0][0].startswith("https://h/listik/api/tasks"), seen)
        self.assertEqual(seen[0][1], "Bearer rt")

    def test_timeout_and_broken_response_unreachable(self) -> None:
        import http.client
        client.use_target(client.Target("remote", "https://h", "rt", None))
        for exc in (TimeoutError("timed out"), http.client.RemoteDisconnected("gone"),
                    ConnectionResetError("reset")):
            with self.subTest(exc=exc):
                with mock.patch.object(client.urllib.request, "urlopen", side_effect=exc):
                    with self.assertRaises(errors.ListikError) as ctx:
                        client.request("GET", "/api/tasks")
                self.assertEqual(ctx.exception.code, errors.UNREACHABLE)
        # Локальная цель — прежнее ApiDown, его ловит фолбэк call().
        client.use_target(client.Target("local", "http://127.0.0.1:9", "lt", None))
        with mock.patch.object(client.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            with self.assertRaises(client.ApiDown):
                client.request("GET", "/api/tasks")

    def test_explicit_host_ignores_target(self) -> None:
        client.use_target(client.Target("remote", "https://h/listik", "rt", None))
        self.assertTrue(client.base_url("127.0.0.1", 9).startswith("http://127.0.0.1:9"))
        self.assertNotEqual(client.token("127.0.0.1", 9), "rt")

    def test_resolve_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            (root / ".git").mkdir()
            cfg = root / "config.toml"
            with mock.patch.object(paths, "CONFIG_PATH", cfg):
                cfg.write_text(f'[auth]\ntoken = "{LOCAL_TOKEN}"\n[remote]\n'
                               f'url = "https://h/listik/"\ntoken = "{REMOTE_TOKEN}"\n',
                               encoding="utf-8")
                none = client.resolve_target(tmp)
                self.assertEqual((none.kind, none.token, none.project_file),
                                 ("local", LOCAL_TOKEN, None))
                (root / ".listik.toml").write_text(
                    'server = "https://H/listik"\nproject = "p"\n', encoding="utf-8")
                remote = client.resolve_target(tmp)
                self.assertEqual((remote.kind, remote.base_url, remote.token),
                                 ("remote", "https://h/listik", REMOTE_TOKEN))
                local = client.resolve_target(tmp, local=True)
                self.assertEqual((local.kind, local.token), ("local", LOCAL_TOKEN))
                self.assertEqual(local.project_file["project"], "p")
                explicit = client.resolve_target(tmp, port=9)
                self.assertEqual((explicit.kind, explicit.base_url),
                                 ("local", "http://127.0.0.1:9"))
                # Битый config.toml: удалённую цель не выбрать — ошибка; --local — не отказ.
                cfg.write_text('token = "x" [\n', encoding="utf-8")
                with self.assertRaises(ValueError):
                    client.resolve_target(tmp)
                broken = client.resolve_target(tmp, local=True)
                self.assertEqual((broken.kind, broken.project_file["project"]), ("local", "p"))
                client.use_target(broken)
                try:
                    with self.assertRaises(ValueError):  # как раньше: [server] не прочитать
                        client.base_url()
                finally:
                    client.use_target(None)


if __name__ == "__main__":
    unittest.main()
