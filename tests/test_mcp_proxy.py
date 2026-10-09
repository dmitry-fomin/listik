"""stdio-MCP по цели из `.listik.toml`: прокси на общий сервер (listik-r69k, порция d).

Стенд: сервер Listik в процессе, в серверном режиме (`users = ["ann", "bob"]`, токен
`srv-tok-8f3a`); заголовки каждого запроса записывает обёртка над `Handler._authed`.
Настоящий `bin/listik mcp` запускается подпроцессом из временного «клона» (`git init` +
`.listik.toml`) со своим `LISTIK_CONFIG` (`[remote]` на этот сервер, `[auth] owner = "ann"`,
`[auth] token = "loc-tok-31c7"`) и своей `LISTIK_DB`, которой в режиме прокси быть не должно.
Номера в docstring тестов — пункты `listik-r69k.check-d.md`.
"""
from __future__ import annotations

import http.server
import json
import os
import pathlib
import queue
import sqlite3
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from listik import client, db as db_mod, fence as fence_mod, mcp, server, store
from tests.test_mcp_all_tools import CASES, OWNER, PROJECT, _LaunchCleanup, _dead_port, _task
from tests.test_mcp_http import McpHttpCase
from tests.test_mcp_stdio_notify import LISTIK_BIN, tool_call

SRV_TOKEN = "srv-tok-8f3a"
LOC_TOKEN = "loc-tok-31c7"
SECRETS = (SRV_TOKEN, LOC_TOKEN)
SERVER_CONFIG = (f'[auth]\ntoken = "{SRV_TOKEN}"\n\n'
                 '[server]\nmode = "server"\nusers = ["ann", "bob"]\n')
#: Переменные окружения тестового процесса, которые не должны протечь в подпроцесс.
CLEAN_ENV = ("LISTIK_PROJECT", "LISTIK_ACTOR", "LISTIK_OWNER",
             fence_mod.ENV_TASK, fence_mod.ENV_GENERATION, fence_mod.ENV_DISPATCH)
NOTIFICATION = {"jsonrpc": "2.0", "method": "notifications/initialized"}


def rpc(method: str, params: dict | None = None, rid: int = 1) -> dict:
    message = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        message["params"] = params
    return message


def payload(result: dict) -> object:
    return json.loads(result["content"][0]["text"])


class Stdio:
    """Живой `bin/listik mcp`: строка в stdin, ответы из stdout по очереди."""

    def __init__(self, cwd: pathlib.Path, env: dict) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, str(LISTIK_BIN), "mcp"], cwd=str(cwd), env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        self.lines: queue.Queue = queue.Queue()
        self.out: list[str] = []
        self.err: list[str] = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self.out, self.lines),
                         daemon=True).start()
        self._err_pump = threading.Thread(target=self._pump,
                                          args=(self.proc.stderr, self.err, None), daemon=True)
        self._err_pump.start()

    @staticmethod
    def _pump(stream, sink: list, lines: queue.Queue | None) -> None:
        for line in stream:
            sink.append(line)
            if lines is not None:
                lines.put(line)

    def send(self, message) -> None:
        text = message if isinstance(message, str) else json.dumps(message, ensure_ascii=False)
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def reply(self, timeout: float = 30.0) -> dict:
        return json.loads(self.lines.get(timeout=timeout))

    def ask(self, message) -> dict:
        self.send(message)
        return self.reply()

    def finish(self) -> tuple[str, str]:
        self.proc.stdin.close()
        self.proc.wait(timeout=30)
        self._err_pump.join(timeout=5)
        return "".join(self.out), "".join(self.err)

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:  # noqa: BLE001 — уже закрыт
                pass


class FakeServer:
    """Подставной HTTP-сервер: `reply(handler)` отвечает на POST, запросы — в `hits`."""

    def __init__(self, reply) -> None:
        hits = self.hits = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                hits.append((self.path, dict(self.headers), self.rfile.read(length)))
                try:
                    reply(self)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # клиент ушёл по своему таймауту

            def log_message(self, *args) -> None:
                pass

        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


def _send(handler, status: int, body: bytes = b"", headers: dict | None = None) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


class ProxyCase(_LaunchCleanup, McpHttpCase):
    """Сервер в серверном режиме + клон с `.listik.toml` + конфиг и база клиента."""

    config_text = SERVER_CONFIG

    def setUp(self) -> None:
        super().setUp()
        self._patch_logs()
        db_mod.init(self.db_path).close()
        self.conn = self.connect_db()
        self.tmp = pathlib.Path(self._tmp.name)
        self.ctx = {"tmp": self.tmp, "owner": OWNER, "project": PROJECT}
        self.seen: list[tuple[str, dict]] = []
        original = server.Handler._authed
        seen = self.seen

        def recording(handler, *args, **kwargs):
            seen.append((handler.path, {k.lower(): v for k, v in handler.headers.items()}))
            return original(handler, *args, **kwargs)

        patch = mock.patch.object(server.Handler, "_authed", recording)
        patch.start()
        self.addCleanup(patch.stop)
        self.url = f"http://127.0.0.1:{self.port}"
        self.clone = self.tmp / "clone"
        self.clone.mkdir()
        subprocess.run(["git", "init", "-q", str(self.clone)], check=True)
        self.client_config = self.tmp / "client.toml"
        self.client_db = self.tmp / "client.db"
        self.write_project(self.url)
        self.write_client()

    def tearDown(self) -> None:
        self._kill_launched(self.conn)
        super().tearDown()

    # --- стенд
    def write_project(self, server_url: str, project: str = PROJECT) -> None:
        (self.clone / ".listik.toml").write_text(
            f'server = "{server_url}"\nproject = "{project}"\n', encoding="utf-8")

    def write_client(self, remote_url: str | None = "", token: str = SRV_TOKEN,
                     owner: str | None = OWNER, server_mode: bool = False) -> None:
        """remote_url: "" — этот сервер, None — без `[remote]`. `[server]` — на мёртвый порт:
        локальный stdio не должен слать notify настоящему серверу на 8787."""
        text = ""
        if remote_url is not None:
            text += f'[remote]\nurl = "{remote_url or self.url}"\ntoken = "{token}"\n\n'
        text += f'[auth]\ntoken = "{LOC_TOKEN}"\n'
        if owner:
            text += f'owner = "{owner}"\n'
        text += f'\n[server]\nhost = "127.0.0.1"\nport = {_dead_port()}\n'
        if server_mode:
            text += 'mode = "server"\nusers = ["ann"]\n'
        self.client_config.write_text(text, encoding="utf-8")

    def stdio(self, **extra_env: str) -> Stdio:
        env = {k: v for k, v in os.environ.items() if k not in CLEAN_ENV}
        env.update(LISTIK_DB=str(self.client_db), LISTIK_CONFIG=str(self.client_config),
                   LISTIK_LOG=str(self.tmp / "client.log"))
        env.update(extra_env)
        proc = Stdio(self.clone, env)
        self.addCleanup(proc.kill)
        return proc

    def finish(self, proc: Stdio, *, local_db: bool = False) -> tuple[str, str]:
        """Закрыть stdin; токенов нет в выводе; базы клиента нет (кроме локального режима)."""
        out, err = proc.finish()
        for secret in SECRETS:
            self.assertNotIn(secret, out)
            self.assertNotIn(secret, err)
        if not local_db:
            self.assertFalse(self.client_db.exists(), "прокси создал локальную базу")
        return out, err

    def call(self, proc: Stdio, name: str, arguments: dict, rid: int = 1) -> dict:
        reply = proc.ask(tool_call(name, arguments, rid))
        self.assertEqual(reply.get("id"), rid, reply)
        self.assertIn("result", reply, reply)
        return reply["result"]

    def ok(self, proc: Stdio, name: str, arguments: dict, rid: int = 1) -> object:
        result = self.call(proc, name, arguments, rid)
        self.assertFalse(result.get("isError"), f"{name}: {result}")
        return payload(result)

    def mcp_hits(self) -> list[dict]:
        return [headers for path, headers in self.seen if path == "/mcp"]

    def fake(self, reply) -> FakeServer:
        srv = FakeServer(reply)
        self.addCleanup(srv.stop)
        return srv

    def assert_rpc_error(self, reply: dict, rid, *needles: str) -> str:
        self.assertEqual(reply.get("id"), rid, reply)
        self.assertEqual(reply["error"]["code"], -32603, reply)
        message = reply["error"]["message"]
        for needle in needles:
            self.assertIn(needle, message)
        return message


class ProxyWorks(ProxyCase):
    def test_initialize_and_tools_list(self) -> None:
        """1."""
        proc = self.stdio()
        init = proc.ask(rpc("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual(init["result"]["serverInfo"]["name"], "listik")
        tools = proc.ask(rpc("tools/list", rid=2))["result"]["tools"]
        self.assertEqual({t["name"] for t in tools}, {t["name"] for t in mcp.TOOLS})
        for name in ("listik_delete", "listik_portions", "listik_restart", "listik_revoke",
                     "listik_launch", "listik_mentions"):
            self.assertIn(name, {t["name"] for t in tools})
        self.finish(proc)
        self.assertEqual(len(self.mcp_hits()), 2)

    def test_every_case_through_proxy(self) -> None:
        """2, 3, 13: все CASES — без isError, в базе сервера; токены — только серверный."""
        proc = self.stdio()
        for rid, (name, case) in enumerate(CASES.items(), start=1):
            with self.subTest(tool=name):
                fx = case.setup(self.conn, dict(self.ctx))
                case.check(self, fx, self.ok(proc, name, case.args(fx), rid))
        self.finish(proc)
        created = self.conn.execute("SELECT count(*) n FROM tasks WHERE title = 'Новая'")
        self.assertEqual(created.fetchone()["n"], 1)
        hits = self.mcp_hits()
        self.assertEqual(len(hits), len(CASES))
        for headers in hits:
            self.assertEqual(headers["authorization"], f"Bearer {SRV_TOKEN}")
            self.assertEqual(headers["x-listik-owner"], OWNER)
        for _, headers in self.seen:
            self.assertNotIn(LOC_TOKEN, json.dumps(headers))

    def test_create_project_defaults(self) -> None:
        """4."""
        proc = self.stdio()
        made = self.ok(proc, "listik_create", {"title": "T"}, 1)
        row = store.get_task(self.conn, made["id"])
        self.assertEqual((row["project"], row["owner"]), (PROJECT, OWNER))
        other = self.ok(proc, "listik_create", {"title": "T", "project": "other"}, 2)
        self.assertEqual(store.get_task(self.conn, other["id"])["project"], "other")
        empty = self.ok(proc, "listik_create", {"title": "T", "project": ""}, 3)
        self.assertNotEqual(store.get_task(self.conn, empty["id"])["project"], PROJECT)
        self.finish(proc)

    def test_list_project_defaults(self) -> None:
        """5."""
        demo = _task(self.conn, self.ctx, "Демо")
        other = _task(self.conn, {**self.ctx, "project": "other"}, "Другая")
        store.remember(self.conn, "заметка другого проекта", key="other-note", project="other")
        proc = self.stdio()
        listed = self.ok(proc, "listik_list", {}, 1)["tasks"]
        self.assertIn(demo, [t["id"] for t in listed])
        self.assertEqual({t["project"] for t in listed}, {PROJECT})
        everything = [t["id"] for t in self.ok(proc, "listik_list", {"project": "all"}, 2)["tasks"]]
        self.assertIn(other, everything)
        self.assertIn(demo, everything)
        memory = self.ok(proc, "listik_memory", {}, 3)
        self.assertIn("other-note", [i["key"] for i in memory["items"]])
        self.finish(proc)

        proc = self.stdio(LISTIK_PROJECT="other")
        listed = self.ok(proc, "listik_list", {}, 1)["tasks"]
        self.assertEqual([t["id"] for t in listed], [other])
        self.finish(proc)

    def test_waves(self) -> None:
        """6. `all` у волн уходит как есть: сервер считает волны проекта с этим именем
        (пусто), а не «всех проектов» — снятый ключ дал бы отказ `deps._waves`."""
        _task(self.conn, self.ctx)
        proc = self.stdio()
        as_is = self.ok(proc, "listik_waves", {"project": "all"}, 1)
        self.assertEqual((as_is["project"], as_is["waves"]), ("all", []))
        waves = self.ok(proc, "listik_waves", {}, 2)
        self.assertEqual(waves["project"], PROJECT)
        self.assertIsInstance(waves["waves"], list)
        self.finish(proc)

    def test_comment_author(self) -> None:
        """7."""
        tid = _task(self.conn, self.ctx)
        proc = self.stdio(LISTIK_ACTOR="agent:claude")
        cases = [({}, "agent:claude"), ({"author": "agent:grok"}, "agent:grok"),
                 ({"actor": "agent:codex"}, "agent:codex")]
        for rid, (extra, expected) in enumerate(cases, start=1):
            with self.subTest(extra=extra):
                made = self.ok(proc, "listik_comment", {"id": tid, "text": "т", **extra}, rid)
                self.assertEqual(made["author"], expected)
        self.finish(proc)

        proc = self.stdio()
        made = self.ok(proc, "listik_comment", {"id": tid, "text": "без автора"}, 1)
        self.assertEqual(made["author"], mcp._mcp_actor())  # окружение сервера
        self.assertNotEqual(made["author"], OWNER)
        self.finish(proc)
        authors = [r["author"] for r in self.conn.execute(
            "SELECT author FROM comments WHERE task_id = ? ORDER BY created_at, rowid", (tid,))]
        self.assertEqual(authors[:3], ["agent:claude", "agent:grok", "agent:codex"])

    def test_claim_goes_with_owner_header(self) -> None:
        """8."""
        tid = _task(self.conn, {**self.ctx, "owner": "bob"})
        proc = self.stdio()
        refused = self.call(proc, "listik_claim", {"id": tid, "holder": "agent:claude"}, 1)
        self.assertTrue(refused.get("isError"), refused)
        self.assertIn("bob", refused["content"][0]["text"])
        self.finish(proc)
        proc = self.stdio(LISTIK_OWNER="bob")
        taken = self.ok(proc, "listik_claim", {"id": tid, "holder": "agent:claude"}, 1)
        self.assertEqual(taken["holder"], "agent:claude")
        self.finish(proc)

    def test_no_owner_no_header(self) -> None:
        """9."""
        self.write_client(owner=None)
        proc = self.stdio()
        proc.ask(tool_call("listik_list", {}))
        self.finish(proc)
        hits = self.mcp_hits()
        self.assertEqual(len(hits), 1)
        self.assertNotIn("x-listik-owner", hits[0])

    def test_notification_is_silent(self) -> None:
        """10."""
        proc = self.stdio()
        proc.send(NOTIFICATION)
        reply = proc.ask(rpc("ping", rid=7))
        self.assertEqual(reply, {"jsonrpc": "2.0", "id": 7, "result": {}})
        out, _ = self.finish(proc)
        self.assertEqual(len(out.splitlines()), 1)

    def test_stale_fence(self) -> None:
        """12."""
        tid = _task(self.conn, self.ctx)
        self.conn.execute("UPDATE tasks SET generation = 3 WHERE id = ?", (tid,))
        self.conn.commit()
        before = dict(store.get_task(self.conn, tid))
        proc = self.stdio(**{fence_mod.ENV_TASK: tid, fence_mod.ENV_GENERATION: "2"})
        result = self.call(proc, "listik_update", {"id": tid, "fields": {"priority": 1}})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("полномочия отозваны", result["content"][0]["text"])
        self.finish(proc)
        after = store.get_task(self.conn, tid)
        for field in ("priority", "updated_at", "generation"):
            self.assertEqual(after[field], before[field], field)

    def test_bad_lines_stay_local(self) -> None:
        """20."""
        proc = self.stdio()
        proc.send("{не json")
        self.assertEqual(proc.reply()["error"]["code"], -32700)
        for line in ("[]", "1", '"x"'):
            reply = proc.ask(line)
            self.assertEqual(reply["error"]["code"], -32600, reply)
            self.assertIsNone(reply["id"])
        self.assertEqual(self.mcp_hits(), [])
        self.assertEqual(proc.ask(rpc("ping", rid=2))["result"], {})
        self.finish(proc)
        self.assertEqual(len(self.mcp_hits()), 1)


class ProxyFailures(ProxyCase):
    def test_dead_server(self) -> None:
        """14."""
        dead = f"http://127.0.0.1:{_dead_port()}"
        self.write_project(dead)
        self.write_client(remote_url=dead)
        proc = self.stdio()
        first = self.assert_rpc_error(proc.ask(tool_call("listik_create", {"title": "T"}, 1)),
                                      1, "не отвечает", dead)
        proc.send(NOTIFICATION)
        second = self.assert_rpc_error(proc.ask(tool_call("listik_create", {"title": "T"}, 2)),
                                       2, "не отвечает", dead)
        self.assertEqual(first, second)
        out, _ = self.finish(proc)
        self.assertEqual(len(out.splitlines()), 2)

    def test_timeout_is_unknown_result(self) -> None:
        """15."""
        slow = self.fake(lambda h: (time.sleep(2), _send(h, 200, b"{}")))
        target = client.Target("remote", slow.url, SRV_TOKEN, None)
        request = tool_call("listik_create", {"title": "T"}, 5)
        started = time.monotonic()
        late = mcp.proxy_message(target, request, timeout=0.5)
        self.assertLess(time.monotonic() - started, 1.8)
        self.assertEqual((late["id"], late["error"]["code"]), (5, -32603))
        self.assertIn("результат неизвестен", late["error"]["message"])
        self.assertNotIn(SRV_TOKEN, late["error"]["message"])
        dead = client.Target("remote", f"http://127.0.0.1:{_dead_port()}", SRV_TOKEN, None)
        down = mcp.proxy_message(dead, request, timeout=0.5)
        self.assertNotEqual(late["error"]["message"], down["error"]["message"])
        self.assertIsNone(mcp.proxy_message(target, NOTIFICATION, timeout=0.5))

    def test_redirect_is_not_followed(self) -> None:
        """16."""
        second = self.fake(lambda h: _send(h, 200, b"{}"))
        first = self.fake(lambda h: _send(h, 301, b"", {"Location": f"{second.url}/mcp"}))
        self.write_project(first.url)
        self.write_client(remote_url=first.url)
        proc = self.stdio()
        self.assert_rpc_error(proc.ask(rpc("tools/list")), 1, "301", "перенаправляет")
        self.finish(proc)
        self.assertEqual(len(first.hits), 1)
        self.assertEqual(second.hits, [])

    def test_multiline_reply_is_one_line(self) -> None:
        """11."""
        body = {"jsonrpc": "2.0", "id": 1, "result": {"a": [1, 2], "b": {"c": "д"}}}
        fake = self.fake(lambda h: _send(
            h, 200, json.dumps(body, indent=2, ensure_ascii=False).encode("utf-8")))
        self.write_project(fake.url)
        self.write_client(remote_url=fake.url)
        proc = self.stdio()
        self.assertEqual(proc.ask(rpc("tools/list")), body)
        out, _ = self.finish(proc)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertEqual(json.loads(out), body)
        headers = {k.lower(): v for k, v in fake.hits[0][1].items()}
        self.assertEqual(headers["authorization"], f"Bearer {SRV_TOKEN}")

    def _assert_config_error(self, *needles: str) -> None:
        proc = self.stdio()
        self.assert_rpc_error(proc.ask(rpc("initialize", {})), 1, "listik remote set", *needles)
        proc.send(NOTIFICATION)
        self.assert_rpc_error(proc.ask(tool_call("listik_create", {"title": "T"}, 2)), 2,
                              "listik remote set", *needles)
        self.assert_rpc_error(proc.ask(rpc("tools/list", rid=3)), 3, "listik remote set")
        out, _ = self.finish(proc)
        self.assertEqual(len(out.splitlines()), 3)
        self.assertEqual(proc.proc.returncode, 0)
        self.assertEqual(self.mcp_hits(), [])

    def test_no_remote_is_config_error(self) -> None:
        """17."""
        self.write_client(remote_url=None)
        self._assert_config_error(self.url)

    def test_other_remote_is_config_error(self) -> None:
        """18."""
        other = f"http://127.0.0.1:{_dead_port()}"
        self.write_client(remote_url=other)
        self._assert_config_error(self.url, other)

    def test_wrong_token_is_401(self) -> None:
        """19."""
        self.write_client(token="wrong-tok-55e1")
        proc = self.stdio()
        self.assert_rpc_error(proc.ask(rpc("tools/list")), 1, "401", "listik remote set")
        out, err = self.finish(proc)
        self.assertNotIn("wrong-tok-55e1", out + err)


class LocalTargets(ProxyCase):
    def _local_rows(self, sql: str, params=()) -> list:
        conn = sqlite3.connect(self.client_db)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    def test_server_machine_is_local(self) -> None:
        """21."""
        self.write_client(remote_url=None, server_mode=True)
        proc = self.stdio(LISTIK_OWNER=OWNER)
        made = self.ok(proc, "listik_create", {"title": "T"})
        self.finish(proc, local_db=True)
        rows = self._local_rows("SELECT project FROM tasks WHERE id = ?", (made["id"],))
        self.assertEqual([r["project"] for r in rows], [PROJECT])
        self.assertIsNone(self.conn.execute("SELECT 1 FROM tasks WHERE id = ?",
                                            (made["id"],)).fetchone())
        self.assertEqual(self.seen, [])

    def test_local_project_file(self) -> None:
        """23."""
        self.write_project("local")
        proc = self.stdio(LISTIK_ACTOR="agent:claude")
        demo = self.ok(proc, "listik_create", {"title": "T"}, 1)
        self.assertEqual(demo["project"], PROJECT)
        other = self.ok(proc, "listik_create", {"title": "O", "project": "other"}, 2)
        listed = [t["id"] for t in self.ok(proc, "listik_list", {}, 3)["tasks"]]
        self.assertEqual(listed, [demo["id"]])
        everything = [t["id"] for t in self.ok(proc, "listik_list", {"project": "all"}, 4)["tasks"]]
        self.assertEqual(set(everything), {demo["id"], other["id"]})
        note = self.ok(proc, "listik_comment", {"id": demo["id"], "text": "т"}, 5)
        self.assertEqual(note["author"], "agent:claude")
        self.finish(proc, local_db=True)
        self.assertEqual(self.seen, [])

    def test_local_substitution_adds_no_actor(self) -> None:
        """23: локальный режим автора не подставляет — его даёт `_mcp_actor` по-старому."""
        request = tool_call("listik_comment", {"id": "x", "text": "т"})
        with mock.patch.dict(os.environ, {"LISTIK_ACTOR": "agent:claude"}):
            local = mcp.with_defaults(request, {"project": PROJECT}, proxy=False)
            proxied = mcp.with_defaults(request, {"project": PROJECT}, proxy=True)
        self.assertNotIn("actor", local["params"]["arguments"])
        self.assertEqual(proxied["params"]["arguments"]["actor"], "agent:claude")
        self.assertNotIn("actor", request["params"]["arguments"])


if __name__ == "__main__":
    unittest.main()
