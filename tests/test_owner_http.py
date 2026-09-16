"""Владелец-человек в HTTP, `/mcp` и stdio-MCP (listik-xt69, порция b).

Идентичность запроса — только заголовок `X-Listik-Owner`: ни строки запроса, ни
тела. Сервер поднимается в процессе, по одному на тест; изоляция базы и конфига —
подменой `paths.DB_PATH`/`paths.CONFIG_PATH` и сбросом `server._conn_made`/
`server._conn_local` (образец — `tests/test_mcp_http.py`). Реальные `config.toml`
и `listik.db` не читаются и не пишутся.

Два конфига: локальный (только `[auth] token`) и серверный (плюс `[server]
mode = "server"`, `users = ["ann", "bob"]`, без `[auth] owner`).
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from listik import db as db_mod
from listik import errors, mcp, paths, server

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
LOCAL_CONFIG = f'[auth]\ntoken = "{TOKEN}"\n'
SERVER_CONFIG = LOCAL_CONFIG + '\n[server]\nmode = "server"\nusers = ["ann", "bob"]\n'


class OwnerHttpCase(unittest.TestCase):
    """Временная база + временный конфиг + живой сервер на свободном порту."""

    config_text = SERVER_CONFIG

    def setUp(self) -> None:
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._saved_conn = (server._conn_made, server._conn_local)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        self.db_path = tmp / "listik.db"
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = tmp / "config.toml"
        paths.CONFIG_PATH.write_text(self.config_text, encoding="utf-8")
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
        if getattr(self, "srv", None) is not None:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    # --- запросы
    def call(self, method: str, path: str, headers: dict | None = None,
             body: dict | None = None, timeout: float = 10.0):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     method=method)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw or b"null")
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", "replace")}
            return exc.code, dict(exc.headers), payload

    def api(self, method: str, path: str, *, owner: str | None = None,
            body: dict | None = None, token: bool = True):
        headers = dict(AUTH) if token else {}
        if owner is not None:
            headers["X-Listik-Owner"] = owner
        status, headers_out, payload = self.call(method, path, headers, body)
        self.headers_out = headers_out
        return status, payload

    def data(self, status: int, payload: dict):
        self.assertIn(status, (200, 201), payload)
        return payload["data"]

    def create(self, header: str | None = None, *, title: str = "T", **body):
        """POST /api/tasks: `header` — идентичность, **body — поля тела (в т.ч. owner)."""
        status, payload = self.api("POST", "/api/tasks", owner=header,
                                   body={"title": title, "project": "demo", **body})
        return status, payload

    def make_task(self, header: str | None = None, *, title: str = "T", **body) -> dict:
        status, payload = self.create(header, title=title, **body)
        return self.data(status, payload)

    def get_task(self, task_id: str) -> dict:
        status, payload = self.api("GET", f"/api/tasks/{task_id}")
        return self.data(status, payload)


class TestCreate(OwnerHttpCase):
    """Пункты 1–4: владелец у создания задачи."""

    def test_header_becomes_owner(self):  # 1
        status, payload = self.create("ann")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["data"]["owner"], "ann")

    def test_no_owner_is_bad_argument(self):  # 2
        status, payload = self.create()
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertIn("владельца", payload["error"])

    def test_blank_header_is_no_owner(self):  # 2
        status, payload = self.create("   ")
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertIn("владельца", payload["error"])

    def test_body_owner_wins(self):  # 3
        self.assertEqual(self.make_task("ann", owner="bob")["owner"], "bob")
        status, payload = self.create(None, owner="bob")
        self.assertEqual(status, 201, payload)
        self.assertEqual(payload["data"]["owner"], "bob")

    def test_unknown_owner(self):  # 4
        status, payload = self.create("carol")
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertIn("carol", payload["error"])


class TestHolderActions(OwnerHttpCase):
    """Пункты 5–9: claim/heartbeat/stage чужой и своей задачи."""

    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task("ann")
        self.tid = self.task["id"]

    def test_foreign_claim_forbidden(self):  # 5
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/claim", owner="bob",
                                   body={"holder": "agent:dsh"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["code"], "forbidden")
        self.assertIn("ann", payload["error"])
        fresh = self.get_task(self.tid)
        self.assertIsNone(fresh["holder"])
        self.assertEqual(fresh["updated_at"], self.task["updated_at"])

    def test_owner_in_body_does_not_help(self):  # 6
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/claim", owner="bob",
                                   body={"holder": "agent:dsh", "owner": "ann"})
        self.assertEqual(status, 403, payload)

    def test_own_claim_ok(self):  # 7
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/claim", owner="ann",
                                   body={"holder": "agent:dsh"})
        task = self.data(status, payload)
        self.assertEqual(task["holder"], "agent:dsh")
        self.assertEqual(task["owner"], "ann")

    def test_claim_without_owner_and_unknown_owner(self):  # 8
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/claim",
                                   body={"holder": "agent:dsh"})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")
        self.assertIn("от чьего имени", payload["error"])
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/claim", owner="carol",
                                   body={"holder": "agent:dsh"})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")

    def test_heartbeat_and_stage(self):  # 9
        self.data(*self.api("POST", f"/api/tasks/{self.tid}/claim", owner="ann",
                            body={"holder": "agent:dsh"}))
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/heartbeat", owner="bob",
                                   body={"holder": "agent:dsh"})
        self.assertEqual(status, 403, payload)
        status, payload = self.api("POST", f"/api/tasks/{self.tid}/stage", owner="bob",
                                   body={"holder": "agent:codex"})
        self.assertEqual(status, 403, payload)

    def test_stage_without_holder_ignores_owner(self):  # 9
        before = self.get_task(self.tid)["stage"]
        task = self.data(*self.api("POST", f"/api/tasks/{self.tid}/stage", owner="bob", body={}))
        self.assertNotEqual(task["stage"], before)
        # Незнакомое имя без holder тоже не смотрится.
        self.data(*self.api("POST", f"/api/tasks/{self.tid}/stage", owner="carol", body={}))


class TestPatch(OwnerHttpCase):
    """Пункт 10: PATCH чужой задачи и смена владельца."""

    def setUp(self) -> None:
        super().setUp()
        self.tid = self.make_task("ann")["id"]

    def test_foreign_patch_forbidden(self):
        status, payload = self.api("PATCH", f"/api/tasks/{self.tid}", owner="bob",
                                   body={"title": "x"})
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload["code"], "forbidden")
        self.assertEqual(self.get_task(self.tid)["title"], "T")
        # Тело 403 — прежнего формата, без ключа hint.
        self.assertEqual(set(payload), {"ok", "error", "code"})
        self.assertIs(payload["ok"], False)

    def test_patch_without_owner_and_by_owner(self):
        self.data(*self.api("PATCH", f"/api/tasks/{self.tid}", body={"title": "x"}))
        task = self.data(*self.api("PATCH", f"/api/tasks/{self.tid}", owner="ann",
                                   body={"title": "y"}))
        self.assertEqual(task["title"], "y")

    def test_owner_field(self):
        task = self.data(*self.api("PATCH", f"/api/tasks/{self.tid}", owner="bob",
                                   body={"owner": "bob"}))
        self.assertEqual(task["owner"], "bob")
        status, payload = self.api("PATCH", f"/api/tasks/{self.tid}", owner="bob",
                                   body={"owner": "carol"})
        self.assertEqual(status, 400, payload)
        task = self.data(*self.api("PATCH", f"/api/tasks/{self.tid}", owner="bob",
                                   body={"owner": ""}))
        self.assertIsNone(task["owner"])


class TestReads(OwnerHttpCase):
    """Пункты 11–12: фильтр «свои + без владельца» у списков и доски."""

    def setUp(self) -> None:
        super().setUp()
        self.ann = self.make_task("ann", title="A")["id"]
        self.bob = self.make_task("bob", title="B")["id"]
        # Задача без владельца заводится прямым SQL: `create` в серверном режиме
        # без владельца не даёт.
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        conn.execute("UPDATE tasks SET owner = NULL WHERE id = ?", (self.bob,))
        conn.commit()
        self.free = self.bob
        self.bob = self.make_task("bob", title="B2")["id"]

    def ids(self, tasks) -> set:
        return {t["id"] for t in tasks}

    def test_list_filtered(self):  # 11
        res = self.data(*self.api("GET", "/api/tasks", owner="ann"))
        self.assertEqual(res["total"], 2)
        self.assertEqual(self.ids(res["tasks"]), {self.ann, self.free})
        res = self.data(*self.api("GET", "/api/tasks"))
        self.assertEqual(res["total"], 3)

    def test_board_and_ready_filtered(self):  # 11
        board = self.data(*self.api("GET", "/api/board", owner="ann"))
        seen = set()
        for column in board.get("columns") or []:
            seen |= self.ids(column.get("tasks") or [])
        seen |= self.ids(board.get("ready") or [])
        self.assertNotIn(self.bob, seen)
        self.assertIn(self.free, seen)
        ready = self.data(*self.api("GET", "/api/ready", owner="ann"))
        self.assertNotIn(self.bob, self.ids(ready["tasks"]))
        self.assertIn(self.free, self.ids(ready["tasks"]))

    def test_unknown_owner_on_reads(self):  # 12
        for path in ("/api/tasks", "/api/board", "/api/ready"):
            status, payload = self.api("GET", path, owner="carol")
            self.assertEqual(status, 400, (path, payload))
            self.assertEqual(payload["code"], "bad_argument", path)


class TestHealthAndHeaders(OwnerHttpCase):
    """Пункты 13–16: health, CORS, независимость токена и владельца."""

    def test_health(self):  # 13
        status, payload = self.api("GET", "/api/health", token=False)
        data = self.data(status, payload)
        self.assertEqual(data["mode"], "server")
        self.assertNotIn("users", data)
        self.assertNotIn("owner", data)
        data = self.data(*self.api("GET", "/api/health", owner=" ann "))
        self.assertEqual(data["users"], ["ann", "bob"])
        self.assertEqual(data["owner"], "ann")
        data = self.data(*self.api("GET", "/api/health"))
        self.assertIsNone(data["owner"])

    def test_cors_header(self):  # 14
        self.api("GET", "/api/health")
        allow = self.headers_out["Access-Control-Allow-Headers"]
        for name in ("Authorization", "Content-Type", "X-Listik-Token", "X-Listik-Owner"):
            self.assertIn(name, allow)

    def test_token_and_owner_are_independent(self):  # 15
        status, payload = self.api("GET", "/api/tasks", owner="ann", token=False)
        self.assertEqual(status, 401, payload)


class TestLocalMode(OwnerHttpCase):
    """Пункты 17–19: локальный режим владельца не знает."""

    config_text = LOCAL_CONFIG

    def test_create_ignores_owner(self):  # 17
        self.assertIsNone(self.make_task()["owner"])
        self.assertIsNone(self.make_task("carol")["owner"])
        self.assertIsNone(self.make_task(None, owner="ann")["owner"])

    def test_foreign_task_is_not_foreign(self):  # 18
        tid = self.make_task()["id"]
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        conn.execute("UPDATE tasks SET owner = 'ann' WHERE id = ?", (tid,))
        conn.commit()
        task = self.data(*self.api("POST", f"/api/tasks/{tid}/claim", owner="bob",
                                   body={"holder": "agent:dsh"}))
        self.assertEqual(task["holder"], "agent:dsh")
        self.data(*self.api("PATCH", f"/api/tasks/{tid}", owner="bob", body={"title": "x"}))

    def test_list_and_health(self):  # 19
        self.make_task(title="A")
        self.make_task(title="B")
        res = self.data(*self.api("GET", "/api/tasks", owner="ann"))
        self.assertEqual(res["total"], 2)
        data = self.data(*self.api("GET", "/api/health", owner="ann"))
        self.assertEqual(data["mode"], "local")
        self.assertEqual(data["users"], [])
        self.assertIsNone(data["owner"])


class TestMcp(OwnerHttpCase):
    """Пункты 20–24: владелец у транспорта `/mcp` и у stdio."""

    def rpc(self, payload: dict, owner: str | None = None):
        headers = dict(AUTH)
        if owner is not None:
            headers["X-Listik-Owner"] = owner
        status, _, body = self.call("POST", "/mcp", headers, payload)
        self.assertEqual(status, 200, body)
        return body

    def tool(self, name: str, arguments: dict, owner: str | None = None) -> dict:
        return self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                         "params": {"name": name, "arguments": arguments}}, owner)["result"]

    def payload_of(self, result: dict):
        self.assertFalse(result.get("isError"), result)
        return json.loads(result["content"][0]["text"])

    def test_claim(self):  # 20
        tid = self.make_task("ann")["id"]
        result = self.tool("listik_claim", {"id": tid, "holder": "agent:dsh"}, owner="bob")
        self.assertTrue(result.get("isError"), result)
        self.assertIn("ann", result["content"][0]["text"])
        result = self.tool("listik_claim", {"id": tid, "holder": "agent:dsh"}, owner="ann")
        self.assertIn("holder", self.payload_of(result))

    def test_create(self):  # 21
        task = self.payload_of(self.tool("listik_create", {"title": "M"}, owner="ann"))
        self.assertEqual(task["owner"], "ann")
        task = self.payload_of(self.tool("listik_create", {"title": "M", "owner": "bob"},
                                         owner="ann"))
        self.assertEqual(task["owner"], "bob")
        result = self.tool("listik_create", {"title": "M"})
        self.assertTrue(result.get("isError"), result)
        self.assertIn("владельца", result["content"][0]["text"])

    def test_reads(self):  # 22
        ann = self.make_task("ann", title="A")["id"]
        free = self.make_task("ann", title="F")["id"]
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        conn.execute("UPDATE tasks SET owner = NULL WHERE id = ?", (free,))
        conn.commit()
        bob = self.make_task("bob", title="B")["id"]
        listed = {t["id"] for t in self.payload_of(
            self.tool("listik_list", {}, owner="ann"))["tasks"]}
        self.assertEqual(listed, {ann, free})
        ready = {t["id"] for t in self.payload_of(
            self.tool("listik_ready", {}, owner="ann"))["tasks"]}
        self.assertNotIn(bob, ready)
        board = self.payload_of(self.tool("listik_board", {}, owner="ann"))
        seen = set()
        for column in board.get("columns") or []:
            seen |= {t["id"] for t in column.get("tasks") or []}
        self.assertNotIn(bob, seen)

    def test_tools_list_describes_owner(self):  # 23
        tools = self.rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
        by_name = {t["name"]: t for t in tools}
        self.assertIn("owner", by_name["listik_create"]["inputSchema"]["properties"])
        self.assertIn("X-Listik-Owner", by_name["listik_claim"]["description"])
        self.assertIn("LISTIK_OWNER", by_name["listik_claim"]["description"])

    def test_http_never_reads_env(self):
        """У HTTP единственный источник имени — заголовок.

        `LISTIK_OWNER` в окружении процесса сервера — это настройка stdio-транспорта;
        запрос без `X-Listik-Owner` значит «клиент не представился», а не «возьми
        имя из окружения», иначе любой анонимный клиент работал бы от лица env.
        """
        with mock.patch.dict(os.environ, {"LISTIK_OWNER": "ann"}):
            result = self.tool("listik_create", {"title": "из окружения"})
            self.assertTrue(result.get("isError"), result)
            self.assertIn("владельца", result["content"][0]["text"])
            # Карточки от имени env не появилось.
            listed = self.payload_of(self.tool("listik_list", {}, owner="ann"))["tasks"]
            self.assertEqual(listed, [])
            tid = self.make_task("ann")["id"]
            bob = self.make_task("bob")["id"]
            # Чтение без заголовка — «никто», выдача не сужается до задач env.
            anon = {t["id"] for t in self.payload_of(self.tool("listik_list", {}))["tasks"]}
            self.assertEqual(anon, {tid, bob})
            claimed = self.tool("listik_claim", {"id": tid, "holder": "agent:dsh"})
            self.assertTrue(claimed.get("isError"), claimed)

    def test_stdio_reads_env(self):  # 24
        tid = self.make_task("ann")["id"]
        conn = db_mod.connect(self.db_path)
        self.addCleanup(conn.close)
        args = {"id": tid, "holder": "agent:dsh"}
        with mock.patch.dict(os.environ, {"LISTIK_OWNER": "bob"}):
            with self.assertRaises(errors.Forbidden):
                mcp.call_tool("listik_claim", args, conn=conn)
            response = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                   "params": {"name": "listik_claim", "arguments": args}},
                                  conn=conn)
            self.assertTrue(response["result"].get("isError"), response)
            self.assertIn("ann", response["result"]["content"][0]["text"])
        with mock.patch.dict(os.environ, {"LISTIK_OWNER": "ann"}):
            out = mcp.call_tool("listik_claim", args, conn=conn)
        self.assertEqual(out["holder"], "agent:dsh")


if __name__ == "__main__":
    unittest.main()
