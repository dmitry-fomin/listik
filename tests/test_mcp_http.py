"""Тесты транспорта `POST /mcp` (шаг 08, порция c; пункты 1–23 `step-08.check-c.md`).

Сервер поднимается в процессе, по одному на тест. Изоляция базы и конфига — подменой
атрибутов модуля `paths`: `db.connect`/`db.init` читают `paths.DB_PATH`, а
`config.load` — `paths.CONFIG_PATH` в момент вызова, поэтому другой изоляции не нужно.
Дополнительно сбрасываются `server._conn_made` и `server._conn_local`: иначе
`get_conn()` вернул бы соединение с настоящей базой.

В тестах 401/413 с большим `Content-Length` тело не отправляется: сервер по ТЗ его не
читает вовсе, а запись 5 МБ в соединение, которое сразу закрывается, даёт клиенту RST
вместо ответа. Проверяется именно объявленный размер (заголовок `Content-Length`).
"""
from __future__ import annotations

import http.client
import json
import pathlib
import queue
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

from listik import db as db_mod
from listik import mcp, paths, server, store

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MAX_BODY = 5 * 1024 * 1024


class McpHttpCase(unittest.TestCase):
    """Временная база + временный конфиг + живой сервер на свободном порту."""

    config_text = f'[auth]\ntoken = "{TOKEN}"\n'

    def setUp(self) -> None:
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._saved_conn = (server._conn_made, server._conn_local)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        self.db_path = tmp / "listik.db"
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = tmp / "config.toml"
        paths.CONFIG_PATH.write_text(self.config_text, encoding="utf-8")
        self._queues: list[queue.Queue] = []
        self.srv = None
        # Соединения сервера открываются в потоках-обработчиках; поток умирает, а
        # соединение остаётся незакрытым, и sqlite3 ругается ResourceWarning. Запоминаем
        # их, чтобы закрыть в tearDown (поведение db.init/connect не меняется).
        self._opened: list = []
        self._db_patches = [
            mock.patch.object(db_mod, "init", self._track(db_mod.init)),
            mock.patch.object(db_mod, "connect", self._track(db_mod.connect)),
        ]
        for patch in self._db_patches:
            patch.start()
        self._start_server()

    def _track(self, opener):
        def wrapper(*args, **kwargs):
            conn = opener(*args, **kwargs)
            self._opened.append(conn)
            return conn
        return wrapper

    def tearDown(self) -> None:
        self._stop_server()
        with server._subs_lock:
            for q in self._queues:
                if q in server._subs:
                    server._subs.remove(q)
        for conn in self._opened:
            conn.close()
        for patch in reversed(self._db_patches):
            patch.stop()
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths
        server._conn_made, server._conn_local = self._saved_conn
        self._tmp.cleanup()

    # --- сервер
    def _start_server(self) -> None:
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def _stop_server(self) -> None:
        if self.srv is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    # --- запросы
    def call(self, method: str, path: str, headers: dict | None = None,
             body: bytes | None = None, timeout: float = 10.0):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=body,
                                     method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def post(self, payload, headers: dict | None = AUTH, path: str = "/mcp",
             raw: bytes | None = None, timeout: float = 10.0):
        """headers=AUTH по умолчанию; headers={} — запрос без заголовков авторизации."""
        body = raw if raw is not None else json.dumps(payload).encode("utf-8")
        merged = {"Content-Type": "application/json", **(headers or {})}
        return self.call("POST", path, merged, body, timeout)

    def declared_length(self, length: int, headers: dict | None = AUTH,
                        path: str = "/mcp"):
        """POST с объявленным Content-Length, но без тела (см. docstring модуля)."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.putrequest("POST", path)
            conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(length))
            for key, value in (AUTH if headers is None else headers).items():
                conn.putheader(key, value)
            conn.endheaders()
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def rpc(self, method: str, params=None, rid=1, headers: dict | None = AUTH,
            path="/mcp"):
        payload = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            payload["params"] = params
        return self.post(payload, headers=headers, path=path)

    def tool(self, name: str, arguments: dict, rid=1, headers: dict | None = AUTH):
        return self.rpc("tools/call", {"name": name, "arguments": arguments}, rid, headers)

    def tool_payload(self, name: str, arguments: dict, rid=1):
        status, _, raw = self.tool(name, arguments, rid)
        self.assertEqual(status, 200, f"{name}: {raw!r}")
        result = json.loads(raw)["result"]
        self.assertFalse(result.get("isError"), f"{name}: {result}")
        return json.loads(result["content"][0]["text"])

    # --- база и события
    def connect_db(self):
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        return conn

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with server._subs_lock:
            server._subs.append(q)
        self._queues.append(q)
        return q

    def make_task(self, title: str = "T", project: str = "demo") -> str:
        return self.tool_payload("listik_create", {"title": title, "project": project})["id"]


class TestInitialize(McpHttpCase):
    """Пункты 1–3: initialize, согласование версии, второй заголовок."""

    def test_initialize_response(self):  # 1
        status, headers, raw = self.rpc("initialize", {"protocolVersion": "2025-06-18"}, rid=42)
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("application/json"))
        data = json.loads(raw)
        self.assertNotIn("ok", data)
        self.assertNotIn("data", data)
        self.assertEqual(data["id"], 42)
        self.assertEqual(data["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(data["result"]["serverInfo"]["name"], "listik")
        self.assertIn("tools", data["result"]["capabilities"])

    def test_version_negotiation_http(self):  # 2
        cases = [("2025-06-18", "2025-06-18"), ("2025-03-26", "2025-03-26"),
                 ("2024-11-05", "2024-11-05"), ("1999-01-01", "2025-06-18")]
        for requested, expected in cases:
            status, _, raw = self.rpc("initialize", {"protocolVersion": requested})
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(raw)["result"]["protocolVersion"], expected, requested)
        status, _, raw = self.rpc("initialize", {})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["result"]["protocolVersion"], mcp.PROTOCOL_VERSION)

    def test_version_negotiation_stdio(self):  # 2, путь stdio
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2024-11-05"}})
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "1999-01-01"}})
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(resp["result"]["protocolVersion"], "2025-06-18")

    def test_x_listik_token_header(self):  # 3
        status, _, raw = self.rpc("initialize", {"protocolVersion": "2025-06-18"},
                                  headers={"X-Listik-Token": TOKEN})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["result"]["protocolVersion"], "2025-06-18")


class TestAuthAndLimits(McpHttpCase):
    """Пункты 4–6: авторизация, Origin, порядок проверок."""

    def test_missing_and_wrong_token(self):  # 4
        status, headers, _ = self.rpc("initialize", {}, headers={})
        self.assertEqual(status, 401)
        self.assertIn("Bearer", headers.get("WWW-Authenticate", ""))
        status, _, _ = self.rpc("initialize", {}, headers={"Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)
        status, _, _ = self.rpc("initialize", {}, headers={"X-Listik-Token": "wrong"})
        self.assertEqual(status, 401)

    def test_query_token_not_accepted(self):  # 4
        status, _, _ = self.rpc("initialize", {}, headers={},
                                path=f"/mcp?token={TOKEN}")
        self.assertEqual(status, 401)

    def test_origin_forbidden(self):  # 5
        status, _, raw = self.rpc("initialize", {},
                                  headers={**AUTH, "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("Origin", json.loads(raw)["error"])
        status, _, _ = self.rpc("initialize", {},
                                headers={"Origin": "http://evil.example",
                                         "Authorization": "Bearer wrong"})
        self.assertEqual(status, 403)
        status, _, _ = self.rpc("initialize", {"protocolVersion": "2025-06-18"},
                                headers={**AUTH, "Origin": ""})
        self.assertEqual(status, 200)

    def test_auth_checked_before_size(self):  # 6
        status, _, _ = self.declared_length(MAX_BODY + 1,
                                            headers={"Authorization": "Bearer wrong"})
        self.assertEqual(status, 401)


class TestMcpMessages(McpHttpCase):
    """Пункты 7–13: уведомления, tools/list, сценарий, ошибки разбора, размер."""

    def test_notification_gets_202(self):  # 7
        status, headers, raw = self.post({"jsonrpc": "2.0",
                                          "method": "notifications/initialized"})
        self.assertEqual(status, 202)
        self.assertEqual(raw, b"")
        self.assertEqual(headers.get("Content-Length"), "0")

    def test_tools_list(self):  # 8
        status, _, raw = self.rpc("tools/list")
        self.assertEqual(status, 200)
        tools = json.loads(raw)["result"]["tools"]
        self.assertEqual(len(tools), len(mcp.TOOLS))
        self.assertEqual(len(tools), 30)
        names = {t["name"] for t in tools}
        for name in ("listik_create", "listik_claim", "listik_put_document"):
            self.assertIn(name, names)

    def test_end_to_end_scenario(self):  # 9
        task_id = self.make_task("T", "demo")
        steps = [("listik_claim", {"id": task_id, "holder": "agent:claude"}),
                 ("listik_heartbeat", {"id": task_id, "holder": "agent:claude"}),
                 ("listik_comment", {"id": task_id, "text": "j", "kind": "journal"}),
                 ("listik_stage", {"id": task_id, "holder": "agent:claude"}),
                 ("listik_done", {"id": task_id, "result": "ok"})]
        for name, args in steps:
            status, _, raw = self.tool(name, args)
            self.assertEqual(status, 200, name)
            result = json.loads(raw)["result"]
            self.assertFalse(result.get("isError"), f"{name}: {result}")
        conn = self.connect_db()
        row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual(row["status"], "done")
        rows = conn.execute("SELECT text FROM comments WHERE task_id=?", (task_id,)).fetchall()
        self.assertIn("j", [r["text"] for r in rows])

    def test_unknown_tool_is_error(self):  # 10
        status, _, raw = self.tool("listik_nope", {})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["result"]["isError"])

    def test_unknown_method(self):  # 11
        status, _, raw = self.rpc("неведомый/метод", rid=3)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["error"]["code"], -32601)
        status, _, raw = self.post({"jsonrpc": "2.0", "method": "неведомый/метод"})
        self.assertEqual(status, 202)
        self.assertEqual(raw, b"")

    def test_invalid_json(self):  # 12
        for raw_body in (b"{not json", b"\xff\xfe\x00"):
            status, _, raw = self.post(None, raw=raw_body)
            self.assertEqual(status, 400, raw_body)
            data = json.loads(raw)
            self.assertEqual(data["error"]["code"], -32700)
            self.assertIsNone(data["id"])

    def test_batch_rejected(self):  # 12
        body = json.dumps([{"jsonrpc": "2.0", "id": 1, "method": "ping"}]).encode("utf-8")
        status, _, raw = self.post(None, raw=body)
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(raw)["error"]["code"], -32600)

    def test_object_without_method(self):  # 12
        status, _, raw = self.post({"jsonrpc": "2.0", "id": 5})
        self.assertEqual(status, 400)
        data = json.loads(raw)
        self.assertEqual(data["error"]["code"], -32600)
        self.assertEqual(data["id"], 5)

    def test_body_too_large(self):  # 13
        status, _, raw = self.declared_length(MAX_BODY + 1)
        self.assertEqual(status, 413)
        self.assertIn("5 МБ", json.loads(raw)["error"])


class TestMcpMethodsAndWellKnown(McpHttpCase):
    """Пункт 14: не-POST на /mcp и /.well-known/."""

    def test_get_mcp_is_405(self):
        status, headers, raw = self.call("GET", "/mcp", AUTH)
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(json.loads(raw)["error"], "MCP: только POST")

    def test_delete_mcp_is_405(self):
        status, headers, raw = self.call("DELETE", "/mcp", AUTH)
        self.assertEqual(status, 405)
        self.assertEqual(headers.get("Allow"), "POST")
        self.assertEqual(json.loads(raw)["error"], "MCP: только POST")

    def test_well_known_paths_are_404_json(self):
        for path in ("/.well-known/oauth-protected-resource",
                     "/.well-known/oauth-authorization-server"):
            status, headers, raw = self.call("GET", path)
            self.assertEqual(status, 404, path)
            self.assertTrue(headers["Content-Type"].startswith("application/json"), path)
            self.assertFalse(json.loads(raw)["ok"])


class TestApiRegression(McpHttpCase):
    """Пункт 15: маршруты /api не изменились."""

    def test_api_routes(self):
        status, _, _ = self.call("GET", f"/api/tasks?token={TOKEN}")
        self.assertEqual(status, 200)
        status, _, _ = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        status, _, _ = self.call("GET", "/api/tasks")
        self.assertEqual(status, 401)


class TestMcpEvents(McpHttpCase):
    """Пункты 16–18, 23: события доске от пишущих инструментов."""

    def test_comment_publishes_task_event(self):  # 16
        task_id = self.make_task()
        q = self.subscribe()
        self.tool_payload("listik_comment", {"id": task_id, "text": "привет"})
        event = json.loads(q.get(timeout=2))
        self.assertEqual(event["kind"], "task")
        self.assertEqual(event["payload"]["id"], task_id)
        self.assertEqual(event["payload"]["action"], "listik_comment")

    def test_create_publishes_new_task_id(self):  # 16
        q = self.subscribe()
        task_id = self.make_task("Событие")
        event = json.loads(q.get(timeout=2))
        self.assertEqual(event["kind"], "task")
        self.assertEqual(event["payload"]["id"], task_id)
        self.assertEqual(event["payload"]["action"], "listik_create")

    def test_read_tool_publishes_nothing(self):  # 17
        task_id = self.make_task()
        q = self.subscribe()
        self.tool_payload("listik_show", {"id": task_id})
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.2)

    def test_failed_write_tool_publishes_nothing(self):  # 17
        q = self.subscribe()
        status, _, raw = self.tool("listik_comment", {"id": "нет-такой", "text": "x"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["result"]["isError"])
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.2)

    def test_update_without_id_publishes_nothing(self):  # 17
        q = self.subscribe()
        status, _, raw = self.tool("listik_update", {"fields": {"status": "open"}})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["result"]["isError"])
        with self.assertRaises(queue.Empty):
            q.get(timeout=0.2)

    def test_publish_failure_does_not_break_response(self):  # 18
        task_id = self.make_task()
        with mock.patch.object(server, "publish", side_effect=RuntimeError("нет подписчиков")):
            status, _, raw = self.tool("listik_comment", {"id": task_id, "text": "x"})
        self.assertEqual(status, 200)
        result = json.loads(raw)["result"]
        self.assertFalse(result.get("isError"))

    def test_response_sent_before_event(self):  # 23
        task_id = self.make_task()
        with mock.patch.object(server, "publish", side_effect=lambda *a, **k: time.sleep(1)):
            started = time.monotonic()
            status, _, raw = self.tool("listik_comment", {"id": task_id, "text": "x"})
            elapsed = time.monotonic() - started
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(raw)["result"].get("isError"))
        self.assertLess(elapsed, 1.0)


class TestMcpFailures(McpHttpCase):
    """Пункты 19, 21, 22: падение обработчика, keep-alive, большой ответ."""

    def test_handler_exception_is_500(self):  # 19
        with mock.patch.object(mcp, "handle", side_effect=RuntimeError("бум")):
            status, _, raw = self.rpc("ping", rid=7)
        self.assertEqual(status, 500)
        data = json.loads(raw)
        self.assertEqual(data["error"]["code"], -32603)
        self.assertEqual(data["id"], 7)
        self.assertIn("RuntimeError", data["error"]["message"])
        status, _, raw = self.rpc("ping", rid=8)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["id"], 8)

    def test_keep_alive_after_refusal(self):  # 21
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}).encode("utf-8")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("POST", "/mcp", body=body,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            self.assertEqual(resp.status, 401)
            self.assertEqual(resp.getheader("Connection"), "close")
            resp.read()
        finally:
            conn.close()
        status, _, _ = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(status, 200)

    def test_two_posts_on_one_connection(self):  # 21
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            for rid in (1, 2):
                body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": "ping"}).encode("utf-8")
                conn.request("POST", "/mcp", body=body,
                             headers={"Content-Type": "application/json", **AUTH})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                self.assertEqual(json.loads(resp.read())["id"], rid)
        finally:
            conn.close()

    def test_large_response(self):  # 22
        conn = db_mod.init(self.db_path)
        self.addCleanup(conn.close)
        for i in range(50):
            store.create_task(conn, title=f"задача {i}", project="demo")
        conn.commit()
        payload = self.tool_payload("listik_list", {})
        self.assertEqual(len(payload["tasks"]), 50)
        self.assertEqual(payload["total"], 50)


class TestMcpWithoutToken(McpHttpCase):
    """Пункт 20: пустой токен в конфиге — проверки нет."""

    config_text = "[embed]\nenabled = false\n"

    def test_no_token_configured(self):
        status, _, raw = self.post({"jsonrpc": "2.0", "id": 1, "method": "ping"},
                                   headers={})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["id"], 1)
