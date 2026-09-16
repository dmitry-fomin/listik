"""Tests for step 08.b: task documents delivered as content via API and MCP.

Covers items 1–19 of `step-08.check-b.md`.  The HTTP branch (item 17) is exercised
by calling `server.handle(...)` directly: `server.get_conn()` keeps its connection
in `server._conn_local` (a `threading.local`) and builds it with `db.init()` from
`paths.DB_PATH`, so the test swaps `paths.DB_PATH` for the temp database and resets
`server._conn_made` / `server._conn_local`, restoring all three in `tearDown`.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import queue
import tempfile
import threading
import unittest

from listik import db as db_mod
from listik import documents, mcp, paths, server, store
from tests.helpers import TempDbTestCase

MISSING_SPEC = "docs/нет-такого.md"
DOCUMENT_KINDS = {"spec", "checklist", "review", "decision"}

_OLD_DOCUMENTS_DDL = """
CREATE TABLE documents (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'spec',
    path         TEXT NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    title        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    status       TEXT NOT NULL DEFAULT 'ok',
    error        TEXT,
    checked_at   TEXT,
    UNIQUE(task_id, kind, path),
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
)
"""


def _event_count(conn, task_id, kind):
    return conn.execute(
        "SELECT count(*) FROM events WHERE task_id=? AND kind=?", (task_id, kind)
    ).fetchone()[0]


def _uploaded_events(conn, task_id):
    return conn.execute(
        "SELECT note, actor FROM events WHERE task_id=? AND kind='document_uploaded' ORDER BY id",
        (task_id,)).fetchall()


def _chunk_count(conn, doc_id):
    return conn.execute(
        "SELECT count(*) FROM document_chunks WHERE document_id=?", (doc_id,)).fetchone()[0]


def _joined_chunks(conn, doc_id):
    return "\n".join(r["text"] for r in conn.execute(
        "SELECT text FROM document_chunks WHERE document_id=? ORDER BY ordinal", (doc_id,)))


class SchemaTests(TempDbTestCase):
    # ---- 1. схема
    def test_documents_have_source_and_content_columns(self) -> None:
        columns = {r["name"]: r for r in self.conn.execute("PRAGMA table_info(documents)")}
        self.assertIn("source", columns)
        self.assertIn("content", columns)
        self.assertEqual(columns["source"]["notnull"], 1)
        self.assertEqual(str(columns["source"]["dflt_value"]).strip("'\""), "file")
        self.assertEqual(db_mod.SCHEMA_VERSION, 8)

    def test_file_document_is_source_file_with_null_content(self) -> None:
        spec_path = self.fixture_copy("long-spec.md")
        task_id = store.create_task(self.conn, title="Задача", project=None)["id"]

        documents.index_document(self.conn, task_id, str(spec_path), kind="spec")

        row = self.conn.execute(
            "SELECT source, content FROM documents WHERE task_id=? AND kind='spec'",
            (task_id,)).fetchone()
        self.assertEqual(row["source"], "file")
        self.assertIsNone(row["content"])


class SoftMigrationTests(unittest.TestCase):
    # ---- 2. мягкая миграция
    def test_migrate_adds_source_and_content_to_old_documents_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_mod.connect(pathlib.Path(tmp) / "old.db")
            try:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute(_OLD_DOCUMENTS_DDL)
                conn.execute(
                    "INSERT INTO documents(id, task_id, kind, path, revision, content_hash, title) "
                    "VALUES('d1','t1','spec','/tmp/x.md',1,'abc','x')")
                conn.commit()

                applied = db_mod.migrate(conn)

                self.assertIn("documents.source", applied)
                self.assertIn("documents.content", applied)
                columns = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
                self.assertIn("source", columns)
                self.assertIn("content", columns)
                row = conn.execute("SELECT source, content FROM documents WHERE id='d1'").fetchone()
                self.assertEqual(row["source"], "file")
                self.assertIsNone(row["content"])
            finally:
                conn.close()


class PutDocumentTests(TempDbTestCase):
    # ---- 4. первая загрузка в задачу без путей
    def test_first_upload_creates_upload_document(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        out = documents.put_document(self.conn, task_id, "spec", "# Заголовок\n\nтекст")

        self.assertEqual(out["source"], "upload")
        self.assertEqual(out["revision"], 1)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["title"], "Заголовок")
        self.assertGreaterEqual(out["chunk_count"], 1)
        self.assertEqual(out["chunk_count"], _chunk_count(self.conn, out["id"]))
        expected_id = hashlib.sha256(
            f"{task_id}:spec:listik://{task_id}/spec.md".encode()).hexdigest()[:24]
        self.assertEqual(out["id"], expected_id)

        task = store.get_task(self.conn, task_id)
        self.assertEqual(task["spec_path"], f"listik://{task_id}/spec.md")
        events = _uploaded_events(self.conn, task_id)
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]["note"].endswith("r1"))

    # ---- 5. повтор с тем же текстом
    def test_repeat_with_same_text_keeps_revision_and_writes_no_event(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        documents.put_document(self.conn, task_id, "spec", "# Заголовок\n\nтекст")

        again = documents.put_document(self.conn, task_id, "spec", "# Заголовок\n\nтекст")

        self.assertEqual(again["revision"], 1)
        self.assertEqual(len(_uploaded_events(self.conn, task_id)), 1)

    # ---- 6. смена текста
    def test_changed_text_bumps_revision_and_replaces_chunks(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        first = documents.put_document(self.conn, task_id, "spec", "# Заголовок\n\nтекст")

        second = documents.put_document(self.conn, task_id, "spec", "# Новый\n\nдругое")

        self.assertEqual(second["revision"], 2)
        self.assertEqual(second["title"], "Новый")
        self.assertEqual(second["id"], first["id"])
        text = _joined_chunks(self.conn, second["id"])
        self.assertIn("другое", text)
        self.assertNotIn("текст", text)
        self.assertEqual(_chunk_count(self.conn, second["id"]),
                         second["chunk_count"])
        events = _uploaded_events(self.conn, task_id)
        self.assertEqual(len(events), 2)
        self.assertTrue(events[-1]["note"].endswith("r2"))

    # ---- 7. путь уже есть, файла нет
    def test_existing_missing_path_is_filled_with_upload(self) -> None:
        task = store.create_task(self.conn, title="Задача", spec_path=MISSING_SPEC)
        task_id = task["id"]
        self.assertEqual(_event_count(self.conn, task_id, "document_error"), 1)

        out = documents.put_document(self.conn, task_id, "spec", "текст")

        self.assertEqual(out["path"], MISSING_SPEC)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["source"], "upload")
        self.assertEqual(store.get_task(self.conn, task_id)["spec_path"], MISSING_SPEC)

        documents.index_task_documents(self.conn, task_id)

        row = self.conn.execute(
            "SELECT status, source FROM documents WHERE task_id=? AND kind='spec'",
            (task_id,)).fetchone()
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["source"], "upload")
        self.assertEqual(_event_count(self.conn, task_id, "document_error"), 1)

    # ---- 8. база важнее диска
    def test_uploaded_content_wins_over_disk(self) -> None:
        spec_path = self.tmp_path / "disk.md"
        spec_path.write_text("ДИСК-1", encoding="utf-8")
        task = store.create_task(self.conn, title="Задача", spec_path=str(spec_path))
        task_id = task["id"]

        uploaded = documents.put_document(self.conn, task_id, "spec", "БАЗА")
        spec_path.write_text("ДИСК-2", encoding="utf-8")

        self.assertEqual(documents.get_document(self.conn, task_id, "spec")["content"], "БАЗА")
        documents.index_task_documents(self.conn, task_id)
        revision = self.conn.execute(
            "SELECT revision FROM documents WHERE id=?", (uploaded["id"],)).fetchone()["revision"]
        self.assertEqual(revision, uploaded["revision"])
        text = "\n".join(c["text"] for c in documents.context(self.conn, task_id, "s1-spec")["chunks"])
        self.assertIn("БАЗА", text)
        self.assertNotIn("ДИСК", text)

    # ---- 9. фоновая проверка
    def test_refresh_all_keeps_uploaded_document_ok(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        documents.put_document(self.conn, task_id, "spec", "# ТЗ\n\nтекст")

        documents.refresh_all(self.conn)

        row = self.conn.execute(
            "SELECT status, source FROM documents WHERE task_id=? AND kind='spec'",
            (task_id,)).fetchone()
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["source"], "upload")

    # ---- 10. context
    def test_context_shows_uploaded_text_and_source(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        documents.put_document(self.conn, task_id, "spec", "# ТЗ\n\nтекст загруженного документа")

        out = documents.context(self.conn, task_id, "s1-spec")

        self.assertIn("текст загруженного документа", "\n".join(c["text"] for c in out["chunks"]))
        by_kind = {d["kind"]: d for d in out["documents"]}
        self.assertEqual(by_kind["spec"]["source"], "upload")

    # ---- 11. get_document для загруженного документа
    def test_get_document_for_uploaded_document(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        content = "# ТЗ\n\nтело документа"
        documents.put_document(self.conn, task_id, "spec", content)

        out = documents.get_document(self.conn, task_id, "spec")

        self.assertEqual(set(out), {"task_id", "kind", "path", "source", "revision",
                                    "content_hash", "status", "error", "content"})
        self.assertEqual(out["content"], content)
        self.assertEqual(out["source"], "upload")
        self.assertEqual(out["kind"], "spec")

    # ---- 12. get_document для файла без загрузки
    def test_get_document_reads_file_from_disk(self) -> None:
        spec_path = self.tmp_path / "on-disk.md"
        spec_path.write_text("# Файл\n\nтекст с диска", encoding="utf-8")
        task_id = store.create_task(self.conn, title="Задача", spec_path=str(spec_path))["id"]
        before = self._row_counts()

        out = documents.get_document(self.conn, task_id, "spec")

        self.assertEqual(out["source"], "file")
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["content"], "# Файл\n\nтекст с диска")
        self.assertEqual(self._row_counts(), before)

    def test_get_document_reports_missing_file_without_writing(self) -> None:
        task_id = store.create_task(self.conn, title="Задача", spec_path=MISSING_SPEC)["id"]
        before = self._row_counts()

        out = documents.get_document(self.conn, task_id, "spec")

        self.assertEqual(out["source"], "file")
        self.assertEqual(out["status"], "missing")
        self.assertIsNone(out["content"])
        self.assertTrue(out["error"])
        self.assertEqual(self._row_counts(), before)

    def _row_counts(self) -> tuple[int, int]:
        docs = self.conn.execute("SELECT count(*) FROM documents").fetchone()[0]
        events = self.conn.execute("SELECT count(*) FROM events").fetchone()[0]
        return docs, events

    # ---- 13. ошибки и границы
    def test_unknown_kind_and_unknown_task(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        with self.assertRaises(ValueError):
            documents.put_document(self.conn, task_id, "foo", "x")
        with self.assertRaises(ValueError):
            documents.get_document(self.conn, task_id, "foo")
        with self.assertRaises(KeyError):
            documents.get_document(self.conn, task_id, "review")
        with self.assertRaises(KeyError):
            documents.put_document(self.conn, "нет-такой-задачи", "spec", "x")
        with self.assertRaises(KeyError):
            documents.get_document(self.conn, "нет-такой-задачи", "spec")

    def test_content_bounds(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        with self.assertRaises(ValueError):
            documents.put_document(self.conn, task_id, "spec", "x" * 1_000_001)
        out = documents.put_document(self.conn, task_id, "spec", "x" * 1_000_000)
        self.assertEqual(out["status"], "ok")

        empty_task = store.create_task(self.conn, title="Пустая")["id"]
        empty = documents.put_document(self.conn, empty_task, "spec", "")
        self.assertEqual(empty["status"], "ok")

    def test_non_string_content_and_path(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        with self.assertRaises(ValueError):
            documents.put_document(self.conn, task_id, "spec", 5)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            documents.put_document(self.conn, task_id, "spec", "x", path=123)  # type: ignore[arg-type]

    def test_blank_path_is_treated_as_not_passed(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        out = documents.put_document(self.conn, task_id, "spec", "x", path="   ")

        self.assertEqual(out["path"], f"listik://{task_id}/spec.md")

    # ---- 14. decision через journal_path
    def test_decision_uses_journal_path_and_leaves_decision_path_empty(self) -> None:
        task_id = store.create_task(self.conn, title="Задача", journal_path="j.md")["id"]

        out = documents.put_document(self.conn, task_id, "decision", "x")

        self.assertEqual(out["path"], "j.md")
        self.assertFalse(store.get_task(self.conn, task_id)["decision_path"])
        got = documents.get_document(self.conn, task_id, "decision")
        self.assertEqual(got["path"], "j.md")
        self.assertEqual(got["content"], "x")

    # ---- 15. явный путь
    def test_explicit_path_updates_card_without_disk_probe(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        out = documents.put_document(self.conn, task_id, "checklist", "x", path="docs/c.md")

        self.assertEqual(out["path"], "docs/c.md")
        self.assertEqual(store.get_task(self.conn, task_id)["checklist_path"], "docs/c.md")
        missing = self.conn.execute(
            "SELECT count(*) FROM documents WHERE status='missing'").fetchone()[0]
        self.assertEqual(missing, 0)

    # ---- 16. карточка
    def test_get_task_documents_include_source(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        documents.put_document(self.conn, task_id, "spec", "x")

        docs = store.get_task(self.conn, task_id)["documents"]

        self.assertEqual(len(docs), 1)
        self.assertIn("source", docs[0])
        self.assertEqual(docs[0]["source"], "upload")


class HttpDocumentTests(TempDbTestCase):
    """Item 17: `handle` takes its connection from `server.get_conn()`."""

    def setUp(self) -> None:
        super().setUp()
        self._orig_db_path = paths.DB_PATH
        self._orig_conn_made = server._conn_made
        self._orig_conn_local = server._conn_local
        paths.DB_PATH = self.db_path
        server._conn_made = False
        server._conn_local = threading.local()

    def tearDown(self) -> None:
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        paths.DB_PATH = self._orig_db_path
        server._conn_made = self._orig_conn_made
        server._conn_local = self._orig_conn_local
        super().tearDown()

    def _doc_path(self, task_id: str, kind: str = "spec") -> str:
        return f"/api/tasks/{task_id}/documents/{kind}"

    def test_put_and_get_document_roundtrip(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        status, doc = server.handle("PUT", self._doc_path(task_id), {},
                                    {"content": "текст"}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(doc["source"], "upload")

        status, got = server.handle("GET", self._doc_path(task_id), {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(got["content"], "текст")

    def test_put_without_string_content_is_400(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        for body in ({}, {"content": 5}):
            with self.assertRaises(server.ApiError) as ctx:
                server.handle("PUT", self._doc_path(task_id), {}, body, authed=True)
            self.assertEqual(ctx.exception.status, 400)

    def test_put_unknown_task_is_404_and_unknown_kind_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            server.handle("PUT", self._doc_path("нет-такой-задачи"), {},
                          {"content": "x"}, authed=True)
        self.assertEqual(ctx.exception.status, 404)

        task_id = store.create_task(self.conn, title="Задача")["id"]
        with self.assertRaises(server.ApiError) as ctx:
            server.handle("PUT", self._doc_path(task_id, "foo"), {}, {"content": "x"}, authed=True)
        self.assertEqual(ctx.exception.status, 400)

    def test_get_unknown_kind_is_400_and_missing_path_is_404(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        with self.assertRaises(server.ApiError) as ctx:
            server.handle("GET", self._doc_path(task_id, "review"), {}, {}, authed=True)
        self.assertEqual(ctx.exception.status, 404)

        with self.assertRaises(server.ApiError) as ctx:
            server.handle("GET", self._doc_path(task_id, "foo"), {}, {}, authed=True)
        self.assertEqual(ctx.exception.status, 400)

    def test_other_methods_are_405(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        for method in ("POST", "DELETE"):
            with self.assertRaises(server.ApiError) as ctx:
                server.handle(method, self._doc_path(task_id), {}, {"content": "x"}, authed=True)
            self.assertEqual(ctx.exception.status, 405)

    def test_put_publishes_task_document_event(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]
        events: queue.Queue = queue.Queue()
        with server._subs_lock:
            server._subs.append(events)
        try:
            server.handle("PUT", self._doc_path(task_id), {},
                          {"content": "текст"}, authed=True)
        finally:
            with server._subs_lock:
                if events in server._subs:
                    server._subs.remove(events)

        message = json.loads(events.get_nowait())
        self.assertEqual(message["kind"], "task")
        self.assertEqual(message["payload"], {"id": task_id, "action": "document"})


class McpDocumentTests(TempDbTestCase):
    def _tools(self) -> dict[str, dict]:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, conn=self.conn)
        return {t["name"]: t for t in resp["result"]["tools"]}

    # ---- 18. MCP, схема
    def test_tools_list_has_30_tools_with_document_schemas(self) -> None:
        tools = self._tools()

        self.assertEqual(len(tools), 30)
        self.assertIn("listik_put_document", tools)
        self.assertIn("listik_get_document", tools)
        put_schema = tools["listik_put_document"]["inputSchema"]
        self.assertEqual(put_schema["required"], ["id", "kind", "content"])
        get_schema = tools["listik_get_document"]["inputSchema"]
        self.assertEqual(get_schema["required"], ["id", "kind"])
        for schema in (put_schema, get_schema):
            self.assertEqual(set(schema["properties"]["kind"]["enum"]), DOCUMENT_KINDS)

    # ---- 19. MCP, вызовы
    def test_call_tool_put_and_get_document_roundtrip(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        out = mcp.call_tool("listik_put_document",
                            {"id": task_id, "kind": "spec", "content": "текст из MCP"},
                            conn=self.conn)
        self.assertEqual(out["source"], "upload")

        got = mcp.call_tool("listik_get_document", {"id": task_id, "kind": "spec"},
                            conn=self.conn)
        self.assertEqual(got["content"], "текст из MCP")

    def test_call_tool_unknown_kind_is_tool_error(self) -> None:
        task_id = store.create_task(self.conn, title="Задача")["id"]

        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "listik_put_document",
                                      "arguments": {"id": task_id, "kind": "foo",
                                                    "content": "x"}}}, conn=self.conn)

        self.assertTrue(resp["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
