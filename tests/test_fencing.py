"""Ограждение записи по поколению запуска и карантин отвергнутых записей
(listik-go61, порция b).

HTTP — временная база и живой сервер (образец — `tests/test_owner_http.py`); `seed`
готовит `generation`/`dispatch_id` прямым `UPDATE`, без запуска процесса (образец
`AutostartTestCase.seed` из `tests/test_autostart.py`, здесь — свой, потому что
`TempDbTestCase` его не даёт). Изоляция локального режима (`client.local_call`,
CLI): `paths.DB_PATH`/`db_mod.init` подменены на временную базу теста — иначе они
открыли бы боевой `listik.db` (образец — `CliTests.setUp` в `tests/test_autostart.py`).
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from listik import client
from listik import db as db_mod
from listik import errors
from listik import fence
from listik import mcp
from listik import paths
from listik import server
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CONFIG = f'[auth]\ntoken = "{TOKEN}"\n'


def _load_cli():
    """Загрузить `bin/listik` как модуль (у файла нет расширения .py)."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_fencing", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


# ------------------------------------------------------------------ обвязка HTTP

class FencingHttpCase(TempDbTestCase):
    """Временная база (см. `TempDbTestCase`) + временный конфиг + живой сервер."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._saved_conn = (server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        paths.CONFIG_PATH = self.tmp_path / "config.toml"
        paths.CONFIG_PATH.write_text(CONFIG, encoding="utf-8")
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths
        server._conn_made, server._conn_local = self._saved_conn
        super().tearDown()

    # --- запросы -----------------------------------------------------------
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
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw or b"null")
            except json.JSONDecodeError:
                payload = {"raw": raw.decode("utf-8", "replace")}
            return exc.code, payload

    def api(self, method: str, path: str, *, fence_headers: dict | None = None,
            body: dict | None = None):
        headers = dict(AUTH)
        if fence_headers:
            headers.update(fence_headers)
        return self.call(method, path, headers, body)

    def data(self, status: int, payload: dict):
        self.assertIn(status, (200, 201), payload)
        return payload["data"]

    def make_task(self, **body) -> dict:
        status, payload = self.api("POST", "/api/tasks", body={"title": "T", **body})
        return self.data(status, payload)

    def get_task(self, task_id: str, *, rejected: bool = False) -> dict:
        path = f"/api/tasks/{task_id}" + ("?rejected=1" if rejected else "")
        status, payload = self.api("GET", path)
        return self.data(status, payload)

    # --- база напрямую, тем же файлом (WAL — видно сразу) -------------------
    def seed(self, task_id: str, *, generation: int, dispatch_id: str | None) -> None:
        self.conn.execute("UPDATE tasks SET generation = ?, dispatch_id = ? WHERE id = ?",
                          (generation, dispatch_id, task_id))
        self.conn.commit()

    def row(self, task_id: str) -> dict:
        return dict(self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())

    def events(self, task_id: str, kind: str | None = None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE task_id = ? ORDER BY id", (task_id,)).fetchall()
        return [dict(r) for r in rows if kind is None or r["kind"] == kind]

    def token_headers(self, task_id: str, generation: int, dispatch_id: str | None = None) -> dict:
        headers = {"X-Listik-Task": task_id, "X-Listik-Generation": str(generation)}
        if dispatch_id is not None:
            headers["X-Listik-Dispatch"] = dispatch_id
        return headers


# ------------------------------------------------------------------ 1-4: HTTP

class GuardedOperationsTests(FencingHttpCase):
    """Пункт 1: каждая ограждаемая HTTP-операция отвечает 409 `revoked` устаревшему
    токену, не меняет карточку и уводит запись в карантин."""

    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task()
        self.tid = self.task["id"]
        self.seed(self.tid, generation=2, dispatch_id="cur")
        self.before = self.row(self.tid)

    def _assert_revoked(self, status: int, payload: dict, *, op: str, dispatch_id="old"):
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "revoked")
        self.assertIn("полномочия", payload["error"])
        self.assertIn("отозваны", payload["error"])
        # Карточка не изменилась вовсе.
        self.assertEqual(self.row(self.tid), self.before)
        rejected = self.events(self.tid, "rejected")
        self.assertEqual(len(rejected), 1, rejected)
        ev = rejected[0]
        self.assertEqual(ev["from_value"], "1")
        self.assertEqual(ev["to_value"], "2")
        note = json.loads(ev["note"])
        self.assertEqual(note["op"], op)
        self.assertEqual(note["dispatch_id"], dispatch_id)
        self.assertEqual(note["current_dispatch_id"], "cur")
        return note

    def test_done(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/done",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={"result": "готово"})
        note = self._assert_revoked(status, payload, op="done")
        self.assertEqual(note["args"]["result"], "готово")
        self.assertIsNone(self.row(self.tid)["closed_at"])
        self.assertEqual(self.row(self.tid)["status"], self.before["status"])

    def test_stage(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/stage",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={"to": "s3-impl"})
        self._assert_revoked(status, payload, op="stage")
        self.assertEqual(self.comments_count(), 0)

    def test_comment(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.tid, 1, "old"),
            body={"text": "зомби текст", "kind": "journal"})
        note = self._assert_revoked(status, payload, op="comment")
        self.assertEqual(note["args"]["text"], "зомби текст")
        self.assertEqual(self.comments_count(), 0)

    def test_claim(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/claim",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={"holder": "agent:dsh"})
        self._assert_revoked(status, payload, op="claim")

    def test_heartbeat(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/heartbeat",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={"holder": "agent:dsh"})
        self._assert_revoked(status, payload, op="heartbeat")

    def test_needs_owner(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/needs-owner",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={"value": True})
        self._assert_revoked(status, payload, op="needs-owner")

    def test_release(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/release",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={})
        self._assert_revoked(status, payload, op="release")

    def test_patch_title(self):
        status, payload = self.api(
            "PATCH", f"/api/tasks/{self.tid}",
            fence_headers=self.token_headers(self.tid, 1, "old"), body={"title": "зомби"})
        self._assert_revoked(status, payload, op="update")
        self.assertEqual(self.row(self.tid)["title"], self.before["title"])

    def test_deps_add(self):
        other = self.make_task(title="блокер")
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/deps",
            fence_headers=self.token_headers(self.tid, 1, "old"),
            body={"depends_on": other["id"]})
        self._assert_revoked(status, payload, op="dep_add")
        self.assertEqual(self.deps_count(), 0)

    def test_deps_remove(self):
        other = self.make_task(title="блокер")
        self.conn.execute("INSERT INTO deps(issue_id, depends_on, dep_type) VALUES (?,?,?)",
                          (self.tid, other["id"], "blocks"))
        self.conn.commit()
        status, payload = self.api(
            "DELETE",
            f"/api/tasks/{self.tid}/deps/{other['id']}",
            fence_headers=self.token_headers(self.tid, 1, "old"))
        self._assert_revoked(status, payload, op="dep_remove")
        self.assertEqual(self.deps_count(), 1)

    def test_put_documents(self):
        status, payload = self.api(
            "PUT", f"/api/tasks/{self.tid}/documents/spec",
            fence_headers=self.token_headers(self.tid, 1, "old"),
            body={"content": "зомби-спека"})
        self._assert_revoked(status, payload, op="document")
        self.assertEqual(self.documents_count(), 0)

    # -- helpers
    def comments_count(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM comments WHERE task_id = ?", (self.tid,)).fetchone()[0]

    def deps_count(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM deps WHERE issue_id = ?", (self.tid,)).fetchone()[0]

    def documents_count(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM documents WHERE task_id = ?", (self.tid,)).fetchone()[0]


# ------------------------------------------------------------------ 2-3: совпадения

class TokenMatchTests(FencingHttpCase):
    """Пункты 2-3: текущее поколение проходит, чужой dispatch/большее поколение — нет."""

    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task()
        self.tid = self.task["id"]
        self.seed(self.tid, generation=2, dispatch_id="cur")

    def test_current_generation_and_dispatch_passes(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.tid, 2, "cur"),
            body={"text": "живой", "kind": "journal"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(self.events(self.tid, "rejected")), 0)

    def test_current_generation_without_dispatch_passes(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.tid, 2, None),
            body={"text": "живой", "kind": "journal"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(self.events(self.tid, "rejected")), 0)

    def test_current_generation_wrong_dispatch_is_revoked(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.tid, 2, "other"),
            body={"text": "зомби", "kind": "journal"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "revoked")
        rejected = self.events(self.tid, "rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["from_value"], "2")
        self.assertEqual(rejected[0]["to_value"], "2")

    def test_generation_greater_than_current_is_revoked(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.tid, 5, "x"),
            body={"text": "зомби", "kind": "journal"})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["code"], "revoked")


# ------------------------------------------------------------------ 4: границы

class UnaffectedRequestsTests(FencingHttpCase):
    """Пункт 4: чужая карточка, запрос без токена, несуществующая задача."""

    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task()
        self.tid = self.task["id"]
        self.other = self.make_task(title="другая")
        self.seed(self.tid, generation=2, dispatch_id="cur")
        self.seed(self.other["id"], generation=1, dispatch_id="old")

    def test_token_for_other_task_does_not_guard(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.other["id"], 1, "old"),
            body={"text": "не моя карточка", "kind": "journal"})
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(self.events(self.tid, "rejected")), 0)

    def test_request_without_token_passes(self):
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment", body={"text": "человек", "kind": "journal"})
        self.assertEqual(status, 200, payload)

    def test_write_to_missing_task_is_plain_404(self):
        for method, path, body in (
            ("PATCH", "/api/tasks/nope", {"title": "x"}),
            ("PUT", "/api/tasks/nope/documents/spec", {"content": "x"}),
            ("POST", "/api/tasks/nope/comment", {"text": "x"}),
        ):
            with self.subTest(method=method, path=path):
                status_tok, payload_tok = self.api(
                    method, path, fence_headers=self.token_headers("nope", 1, "old"), body=body)
                status_plain, payload_plain = self.api(method, path, body=body)
                self.assertEqual(status_tok, 404, payload_tok)
                self.assertEqual(status_plain, 404, payload_plain)
                self.assertEqual(payload_tok["code"], "not_found")
                self.assertEqual(payload_plain["code"], "not_found")
                self.assertEqual(len(self.events("nope", "rejected")), 0)


# ------------------------------------------------------------------ 5: карантин не течёт

class QuarantineHiddenTests(FencingHttpCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task()
        self.tid = self.task["id"]
        self.seed(self.tid, generation=2, dispatch_id="cur")
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment",
            fence_headers=self.token_headers(self.tid, 1, "old"),
            body={"text": "секретный зомби-текст", "kind": "journal"})
        self.assertEqual(status, 409, payload)
        # Живое событие той же задачи — фильтр не должен вырезать лишнее.
        status, payload = self.api(
            "POST", f"/api/tasks/{self.tid}/comment", body={"text": "живой", "kind": "journal"})
        self.assertEqual(status, 200, payload)

    def test_get_task_hides_rejected_by_default(self):
        task = self.get_task(self.tid)
        self.assertNotIn("rejected", task)
        for ev in task["events"]:
            self.assertNotEqual(ev["kind"], "rejected")

    def test_get_task_with_flag_shows_rejected(self):
        task = self.get_task(self.tid, rejected=True)
        self.assertIn("rejected", task)
        self.assertEqual(len(task["rejected"]), 1)
        item = task["rejected"][0]
        self.assertEqual(item["generation"], 1)
        self.assertEqual(item["current_generation"], 2)
        self.assertEqual(item["args"]["text"], "секретный зомби-текст")

    def test_context_does_not_leak_quarantine_text(self):
        from listik import documents
        out = documents.context(self.conn, self.tid, "s3-impl")
        self.assertNotIn("секретный зомби-текст", json.dumps(out, ensure_ascii=False))

    def test_mcp_show_never_returns_rejected(self):
        result = mcp.call_tool("listik_show", {"id": self.tid}, conn=self.conn, owner=None)
        self.assertNotIn("rejected", result)

    def test_timeline_and_events_exclude_quarantine_but_keep_the_rest(self):
        items = store.task_timeline(self.conn)
        self.assertTrue(any(i["task_id"] == self.tid and i["kind"] == "comment" for i in items))
        self.assertFalse(any(i["kind"] == "rejected" for i in items))
        blob = json.dumps(items, ensure_ascii=False)
        self.assertNotIn("секретный зомби-текст", blob)

        items_by_project = store.task_timeline(self.conn, project=self.task.get("project"))
        self.assertFalse(any(i["kind"] == "rejected" for i in items_by_project))

        status, payload = self.api("GET", "/api/timeline")
        data = self.data(status, payload)
        self.assertFalse(any(i["kind"] == "rejected" for i in data["items"]))

        status, payload = self.api("GET", "/api/events")
        data = self.data(status, payload)
        self.assertFalse(any(i["kind"] == "rejected" for i in data["items"]))

        result = mcp.call_tool("listik_timeline", {}, conn=self.conn, owner=None)
        self.assertFalse(any(i["kind"] == "rejected" for i in result["items"]))
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("секретный зомби-текст", blob)


# ------------------------------------------------------------------ 6: CLI show

class CliShowTests(FencingHttpCase):
    """Пункт 6: `listik show`/`--rejected` в локальном режиме."""

    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task()
        self.tid = self.task["id"]
        self.seed(self.tid, generation=2, dispatch_id="cur")
        store.add_comment(self.conn, self.tid, "живой", author="agent:x", kind="journal")
        fence.quarantine(
            self.conn, self.tid, fence.Token(self.tid, 1, "old"), op="comment",
            args={"text": "карантинный текст"}, current_generation=2, current_dispatch="cur",
            actor="agent:zombie", harness=None)
        self.cli = _load_cli()
        self._db_patch = mock.patch.object(paths, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        self._init_patch = mock.patch.object(db_mod, "init", return_value=self.conn)
        self._init_patch.start()
        self.addCleanup(self._init_patch.stop)

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_show_without_flag_hides_quarantine_text(self):
        code, out, _ = self.run_cli(["--local", "show", self.tid])
        self.assertEqual(code, 0)
        self.assertNotIn("карантинный текст", out)
        self.assertNotIn("карантин", out)

    def test_show_with_flag_prints_quarantine_section(self):
        code, out, _ = self.run_cli(["--local", "show", self.tid, "--rejected"])
        self.assertEqual(code, 0)
        self.assertIn("отвергнутые записи — карантин (1):", out)
        self.assertIn("карантинный текст", out)

    def test_show_json_with_flag_has_rejected_key(self):
        code, out, _ = self.run_cli(["--local", "show", self.tid, "--rejected", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("rejected", payload)
        self.assertEqual(len(payload["rejected"]), 1)


# ------------------------------------------------------------------ 7: local_call

class LocalFallbackTests(TempDbTestCase):
    """Пункт 7: `client.local_call` — зомби не пишет, текущее поколение проходит."""

    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="T")
        self.tid = self.task["id"]
        self.conn.execute("UPDATE tasks SET generation = 2, dispatch_id = 'cur' WHERE id = ?",
                          (self.tid,))
        self.conn.commit()
        self._db_patch = mock.patch.object(paths, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        self._init_patch = mock.patch.object(db_mod, "init", return_value=self.conn)
        self._init_patch.start()
        self.addCleanup(self._init_patch.stop)

    def test_stale_token_rejects_and_leaves_no_comment(self):
        with self.assertRaises(errors.Revoked):
            client.local_call("comment", fence={"task_id": self.tid, "generation": 1,
                                                "dispatch_id": "old"},
                              task_id=self.tid, text="зомби", author="agent:x")
        comments = self.conn.execute(
            "SELECT count(*) FROM comments WHERE task_id = ?", (self.tid,)).fetchone()[0]
        self.assertEqual(comments, 0)
        rejected = self.conn.execute(
            "SELECT count(*) FROM events WHERE task_id = ? AND kind = 'rejected'",
            (self.tid,)).fetchone()[0]
        self.assertEqual(rejected, 1)

    def test_current_generation_writes_comment(self):
        out = client.local_call("comment", fence={"task_id": self.tid, "generation": 2,
                                                   "dispatch_id": "cur"},
                                task_id=self.tid, text="живой", author="agent:x")
        self.assertTrue(out)
        comments = self.conn.execute(
            "SELECT count(*) FROM comments WHERE task_id = ?", (self.tid,)).fetchone()[0]
        self.assertEqual(comments, 1)


# ------------------------------------------------------------------ 8: MCP stdio

class McpStdioTests(TempDbTestCase):
    """Пункт 8: `mcp.handle` по stdio (окружение) и без заголовков (HTTP)."""

    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="T")
        self.tid = self.task["id"]
        self.other = store.create_task(self.conn, title="другая")
        self.conn.execute("UPDATE tasks SET generation = 2, dispatch_id = 'cur' WHERE id = ?",
                          (self.tid,))
        self.conn.commit()

    def _rpc(self, task_id: str, text: str) -> dict:
        return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "listik_comment",
                          "arguments": {"id": task_id, "text": text}}}

    def test_stdio_zombie_env_is_rejected(self):
        with mock.patch.dict(os.environ, {fence.ENV_TASK: self.tid, fence.ENV_GENERATION: "1",
                                          fence.ENV_DISPATCH: "old"}):
            response = mcp.handle(self._rpc(self.tid, "зомби"), conn=self.conn)
        self.assertTrue(response["result"]["isError"])
        text = response["result"]["content"][0]["text"]
        self.assertIn("полномочия отозваны", text)
        comments = self.conn.execute(
            "SELECT count(*) FROM comments WHERE task_id = ?", (self.tid,)).fetchone()[0]
        self.assertEqual(comments, 0)

    def test_stdio_zombie_env_on_other_task_passes(self):
        with mock.patch.dict(os.environ, {fence.ENV_TASK: self.tid, fence.ENV_GENERATION: "1",
                                          fence.ENV_DISPATCH: "old"}):
            response = mcp.handle(self._rpc(self.other["id"], "чужая карточка"), conn=self.conn)
        self.assertFalse(response["result"].get("isError"))
        comments = self.conn.execute(
            "SELECT count(*) FROM comments WHERE task_id = ?", (self.other["id"],)).fetchone()[0]
        self.assertEqual(comments, 1)

    def test_http_without_headers_passes(self):
        response = mcp.handle(self._rpc(self.tid, "по http без токена"), conn=self.conn,
                              owner=None, fence=None)
        self.assertFalse(response["result"].get("isError"))


class McpDepsRemoveTests(TempDbTestCase):
    """`listik_deps action=rm` ограждён так же, как добавление связи."""

    def setUp(self) -> None:
        super().setUp()
        self.tid = store.create_task(self.conn, title="T")["id"]
        self.blocker = store.create_task(self.conn, title="блокер")["id"]
        self.other = store.create_task(self.conn, title="другая")["id"]
        self.conn.execute("UPDATE tasks SET generation = 2, dispatch_id = 'cur' WHERE id = ?",
                          (self.tid,))
        for issue in (self.tid, self.other):
            self.conn.execute("INSERT INTO deps(issue_id, depends_on, dep_type) VALUES (?,?,?)",
                              (issue, self.blocker, "blocks"))
        self.conn.commit()

    def _rm(self, task_id: str, token):
        return mcp.call_tool("listik_deps", {"action": "rm", "id": task_id,
                                             "depends_on": self.blocker},
                             conn=self.conn, owner=None, fence=token)

    def _deps(self, task_id: str) -> int:
        return self.conn.execute("SELECT count(*) FROM deps WHERE issue_id = ?",
                                 (task_id,)).fetchone()[0]

    def test_stale_token_is_revoked_and_quarantined(self):
        with self.assertRaises(errors.Revoked):
            self._rm(self.tid, fence.Token(self.tid, 1, "old"))
        self.assertEqual(self._deps(self.tid), 1)
        rejected = fence.list_rejected(self.conn, self.tid)
        self.assertEqual(len(rejected), 1, rejected)
        self.assertEqual(rejected[0]["op"], "deps")

    def test_current_token_removes(self):
        self._rm(self.tid, fence.Token(self.tid, 2, "cur"))
        self.assertEqual(self._deps(self.tid), 0)
        self.assertEqual(fence.list_rejected(self.conn, self.tid), [])

    def test_no_token_removes(self):
        self._rm(self.tid, None)
        self.assertEqual(self._deps(self.tid), 0)

    def test_token_for_other_task_does_not_guard(self):
        self._rm(self.other, fence.Token(self.tid, 1, "old"))
        self.assertEqual(self._deps(self.other), 0)
        self.assertEqual(fence.list_rejected(self.conn, self.other), [])


# ------------------------------------------------------------------ 9: клиент

class ClientHeadersTests(unittest.TestCase):
    """Пункт 9: `client.request` ставит три заголовка, `bin/listik call()` передаёт
    токен по обоим путям, и подсказка `revoked` приходит по HTTP-коду."""

    def test_request_sets_three_headers(self):
        token = fence.Token("t1", 3, "d1")
        captured = {}

        class FakeResp:
            def __init__(self):
                self.status = 200

            def read(self):
                return json.dumps({"ok": True, "data": {"id": "t1"}}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch.object(client, "token", return_value=""):
            client.request("POST", "/api/tasks/t1/comment", body={"text": "x"}, fence=token)
        req = captured["req"]
        self.assertTrue(req.has_header("X-listik-task"))
        self.assertEqual(req.get_header("X-listik-task"), "t1")
        self.assertEqual(req.get_header("X-listik-generation"), "3")
        self.assertEqual(req.get_header("X-listik-dispatch"), "d1")

    def _capture_health(self, fn, tok):
        """Вызывает fn() с моком urlopen и token → tok; возвращает (результат, Request)."""
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"ok": True, "data": {"status": "ok"}}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            return FakeResp()

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
             mock.patch.object(client, "token", return_value=tok), \
             mock.patch.dict(os.environ, {"LISTIK_OWNER": "who"}):
            result = fn()
        return result, captured["req"]

    def test_is_up_probe_sends_no_token_and_no_owner(self):
        up, req = self._capture_health(client.is_up, "t")
        self.assertTrue(up)
        self.assertFalse(req.has_header("Authorization"))
        self.assertFalse(req.has_header("X-listik-owner"))

    def test_health_still_sends_token_and_owner(self):
        data, req = self._capture_health(client.health, "t")
        self.assertIsNotNone(data)
        self.assertEqual(req.get_header("Authorization"), "Bearer t")
        self.assertEqual(req.get_header("X-listik-owner"), "who")

    def test_is_up_with_empty_token(self):
        up, req = self._capture_health(client.is_up, "")
        self.assertTrue(up)
        self.assertFalse(req.has_header("Authorization"))

    def test_cli_call_passes_fence_env_to_both_paths(self):
        cli = _load_cli()

        class FakeArgs:
            local = False
            host = None
            port = None
            owner = None

        with mock.patch.dict(os.environ, {fence.ENV_TASK: "t1", fence.ENV_GENERATION: "4",
                                          fence.ENV_DISPATCH: "d4"}):
            with mock.patch.object(client, "is_up", return_value=True), \
                 mock.patch.object(client, "request", return_value={"id": "t1"}) as req_mock:
                cli.call("comment", FakeArgs(), "/api/tasks/t1/comment", method="POST",
                        body={"text": "x"}, local_kwargs={"task_id": "t1", "text": "x"})
            self.assertEqual(req_mock.call_args.kwargs["fence"],
                             fence.Token("t1", 4, "d4"))

            with mock.patch.object(client, "is_up", return_value=False), \
                 mock.patch.object(client, "local_call", return_value={"id": "t1"}) as local_mock:
                cli.call("comment", FakeArgs(), "/api/tasks/t1/comment", method="POST",
                        body={"text": "x"}, local_kwargs={"task_id": "t1", "text": "x"})
            self.assertEqual(local_mock.call_args.kwargs["fence"],
                             fence.Token("t1", 4, "d4"))

    def test_request_revoked_code_gets_revoked_hint_not_conflict_hint(self):
        def make_http_error(code, payload):
            body = json.dumps(payload).encode("utf-8")
            return urllib.error.HTTPError("http://x", code, "err", None, io.BytesIO(body))

        with mock.patch("urllib.request.urlopen",
                        side_effect=make_http_error(409, {"ok": False, "code": "revoked",
                                                          "error": "полномочия отозваны"})), \
             mock.patch.object(client, "token", return_value=""):
            with self.assertRaises(errors.ListikError) as ctx:
                client.request("POST", "/api/tasks/t1/comment", body={"text": "x"})
        self.assertEqual(ctx.exception.code, "revoked")
        self.assertEqual(ctx.exception.hint, errors.REVOKED_HINT)

        with mock.patch("urllib.request.urlopen",
                        side_effect=make_http_error(409, {"ok": False, "code": "conflict",
                                                          "error": "занято"})), \
             mock.patch.object(client, "token", return_value=""):
            with self.assertRaises(errors.ListikError) as ctx:
                client.request("POST", "/api/tasks/t1/comment", body={"text": "x"})
        self.assertEqual(ctx.exception.code, "conflict")
        self.assertEqual(ctx.exception.hint, errors.hint_for_status(409))


# ------------------------------------------------------------------ 10: from_env/headers

class TokenParsingTests(unittest.TestCase):
    def test_from_env_without_task_id_is_none(self):
        self.assertIsNone(fence.from_env({}))

    def test_from_env_non_integer_generation_is_none(self):
        self.assertIsNone(fence.from_env({fence.ENV_TASK: "t1", fence.ENV_GENERATION: "abc"}))

    def test_from_env_generation_without_dispatch(self):
        token = fence.from_env({fence.ENV_TASK: "t1", fence.ENV_GENERATION: "7"})
        self.assertEqual(token, fence.Token("t1", 7, None))

    def test_from_headers_symmetric_with_to_headers(self):
        token = fence.Token("t1", 7, "d1")
        headers = fence.to_headers(token)
        self.assertEqual(fence.from_headers(headers), token)

        token_no_dispatch = fence.Token("t1", 7, None)
        headers2 = fence.to_headers(token_no_dispatch)
        self.assertEqual(fence.from_headers(headers2), token_no_dispatch)


# ------------------------------------------------------------------ 11: ошибка CLI

class CliErrorBothPathsTests(FencingHttpCase):
    """Пункт 11: CLI печатает «ошибка: полномочия…» и в локальном, и в серверном режиме."""

    def setUp(self) -> None:
        super().setUp()
        self.task = self.make_task()
        self.tid = self.task["id"]
        self.seed(self.tid, generation=2, dispatch_id="cur")
        self.cli = _load_cli()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _zombie_env(self):
        return {fence.ENV_TASK: self.tid, fence.ENV_GENERATION: "1", fence.ENV_DISPATCH: "old"}

    def test_local_mode(self):
        with mock.patch.object(paths, "DB_PATH", self.db_path), \
             mock.patch.object(db_mod, "init", return_value=self.conn), \
             mock.patch.dict(os.environ, self._zombie_env()):
            code, _, err = self.run_cli(["--local", "comment", self.tid, "зомби",
                                        "-k", "journal"])
        self.assertEqual(code, 1)
        # `--local` при живом сервере ещё печатает предупреждение о байпасе — оно
        # не часть проверяемого текста ошибки, ищем строку ошибки среди строк stderr.
        lines = err.splitlines()
        self.assertTrue(any(line.startswith("ошибка: полномочия на задачу") for line in lines), err)
        self.assertIn("остановись", err)
        self.assertNotIn("посмотри состояние карточки", err)

    def test_local_mode_json(self):
        with mock.patch.object(paths, "DB_PATH", self.db_path), \
             mock.patch.object(db_mod, "init", return_value=self.conn), \
             mock.patch.dict(os.environ, self._zombie_env()):
            code, out, _ = self.run_cli(["--local", "comment", self.tid, "зомби",
                                        "-k", "journal", "--json"])
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["code"], "revoked")
        self.assertIn("остановись", payload["error"]["hint"])

    def test_http_mode(self):
        # is_up зафиксирован: иначе медленный /api/health уводит call() в локальный
        # фолбэк, и тест молча проверяет не HTTP-путь. Сам запрос идёт на тестовый сервер.
        with mock.patch.dict(os.environ, self._zombie_env()), \
             mock.patch.object(client, "is_up", return_value=True) as is_up_mock:
            argv = ["--host", "127.0.0.1", "--port", str(self.port),
                    "comment", self.tid, "зомби", "-k", "journal"]
            code, _, err = self.run_cli(argv)
        is_up_mock.assert_called_once()
        self.assertEqual(code, 1)
        self.assertNotIn("не отвечает", err)
        lines = err.splitlines()
        self.assertTrue(any(line.startswith("ошибка: полномочия на задачу") for line in lines), err)
        self.assertIn("остановись", err)
        self.assertNotIn("посмотри состояние карточки", err)

    def test_http_mode_json(self):
        with mock.patch.dict(os.environ, self._zombie_env()), \
             mock.patch.object(client, "is_up", return_value=True) as is_up_mock:
            argv = ["--host", "127.0.0.1", "--port", str(self.port),
                    "comment", self.tid, "зомби", "-k", "journal", "--json"]
            code, out, _ = self.run_cli(argv)
        is_up_mock.assert_called_once()
        self.assertEqual(code, 1)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["code"], "revoked")
        self.assertIn("остановись", payload["error"]["hint"])


if __name__ == "__main__":
    unittest.main()
