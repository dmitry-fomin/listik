"""Tests for document status/error tracking, `show`, and task deletion cleanup."""
from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
import unittest

from listik import db as db_mod
from listik import documents, store
from tests.helpers import TempDbTestCase


def _event_count(conn, task_id, kind):
    return conn.execute(
        "SELECT count(*) FROM events WHERE task_id=? AND kind=?", (task_id, kind)
    ).fetchone()[0]


class MissingFileTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.missing_path = str(self.tmp_path / "nope.md")
        self.task = store.create_task(
            self.conn, title="missing spec task", project=None, spec_path=self.missing_path
        )

    def test_missing_file_creates_missing_document_row(self) -> None:
        row = self.conn.execute(
            "SELECT * FROM documents WHERE task_id=? AND kind='spec'", (self.task["id"],)
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "missing")
        self.assertEqual(row["revision"], 0)
        self.assertEqual(_event_count(self.conn, self.task["id"], "document_error"), 1)

        task = store.get_task(self.conn, self.task["id"])
        self.assertEqual(len(task["documents"]), 1)
        self.assertEqual(task["documents"][0]["status"], "missing")
        self.assertIsNotNone(task["documents"][0]["error"])

    def test_repeated_indexing_of_missing_file_writes_event_once(self) -> None:
        for _ in range(3):
            documents.index_task_documents(self.conn, self.task["id"])
        self.assertEqual(_event_count(self.conn, self.task["id"], "document_error"), 1)

    def test_file_appearing_restores_document(self) -> None:
        pathlib.Path(self.missing_path).write_text(
            "# Спека\n\nПоявившийся текст.\n", encoding="utf-8"
        )
        documents.index_task_documents(self.conn, self.task["id"])

        row = self.conn.execute(
            "SELECT * FROM documents WHERE task_id=? AND kind='spec'", (self.task["id"],)
        ).fetchone()
        self.assertEqual(row["status"], "ok")
        self.assertEqual(row["revision"], 1)
        chunks = self.conn.execute(
            "SELECT count(*) FROM document_chunks WHERE document_id=?", (row["id"],)
        ).fetchone()[0]
        self.assertGreater(chunks, 0)
        self.assertEqual(_event_count(self.conn, self.task["id"], "document_restored"), 1)


class FileDisappearsTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spec_path = self.fixture_copy("long-spec.md")
        self.task = store.create_task(
            self.conn, title="disappearing spec task", project=None, spec_path=str(self.spec_path)
        )

    def test_deleted_file_keeps_previous_chunks_and_flags_missing(self) -> None:
        row_before = self.conn.execute(
            "SELECT * FROM documents WHERE task_id=? AND kind='spec'", (self.task["id"],)
        ).fetchone()
        doc_id = row_before["id"]
        chunks_before = self.conn.execute(
            "SELECT count(*) FROM document_chunks WHERE document_id=?", (doc_id,)
        ).fetchone()[0]
        self.assertGreater(chunks_before, 0)

        self.spec_path.unlink()
        documents.index_task_documents(self.conn, self.task["id"])

        row_after = self.conn.execute(
            "SELECT * FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
        self.assertEqual(row_after["status"], "missing")
        chunks_after = self.conn.execute(
            "SELECT count(*) FROM document_chunks WHERE document_id=?", (doc_id,)
        ).fetchone()[0]
        self.assertEqual(chunks_after, chunks_before)
        self.assertEqual(_event_count(self.conn, self.task["id"], "document_error"), 1)

        ctx = documents.context(self.conn, self.task["id"], "s1-spec")
        chunk_doc_ids = {c["document_id"] for c in ctx["chunks"]}
        self.assertIn(doc_id, chunk_doc_ids)
        docs_by_id = {d["id"]: d for d in ctx["documents"]}
        self.assertEqual(docs_by_id[doc_id]["status"], "missing")


class ContextStabilityTests(TempDbTestCase):
    def _dumps(self, task_id: str) -> str:
        ctx = documents.context(self.conn, task_id, "s1-spec")
        return json.dumps(ctx, ensure_ascii=False, sort_keys=True)

    def test_context_stable_while_file_present(self) -> None:
        spec_path = self.fixture_copy("long-spec.md")
        task = store.create_task(self.conn, title="stable ok", project=None, spec_path=str(spec_path))
        first = self._dumps(task["id"])
        checked_at_1 = self.conn.execute(
            "SELECT checked_at FROM documents WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        second = self._dumps(task["id"])
        checked_at_2 = self.conn.execute(
            "SELECT checked_at FROM documents WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        self.assertEqual(first, second)
        self.assertIsNotNone(checked_at_1)
        self.assertIsNotNone(checked_at_2)

    def test_context_stable_while_file_missing(self) -> None:
        missing_path = str(self.tmp_path / "gone.md")
        task = store.create_task(self.conn, title="stable missing", project=None, spec_path=missing_path)
        first = self._dumps(task["id"])
        second = self._dumps(task["id"])
        self.assertEqual(first, second)


class CheckedAtHiddenTests(TempDbTestCase):
    def test_checked_at_not_in_document_json_or_get_task(self) -> None:
        spec_path = self.fixture_copy("long-spec.md")
        task = store.create_task(self.conn, title="checked at hidden", project=None, spec_path=str(spec_path))
        doc_id = self.conn.execute(
            "SELECT id FROM documents WHERE task_id=?", (task["id"],)
        ).fetchone()[0]
        doc_json = documents.document_json(self.conn, doc_id)
        self.assertNotIn("checked_at", doc_json)

        details = store.get_task(self.conn, task["id"])
        for doc in details["documents"]:
            self.assertNotIn("checked_at", doc)

        checked_at = self.conn.execute(
            "SELECT checked_at FROM documents WHERE id=?", (doc_id,)
        ).fetchone()[0]
        self.assertIsNotNone(checked_at)


class DeleteTaskTests(TempDbTestCase):
    def test_delete_task_removes_all_derived_rows(self) -> None:
        spec_path = self.fixture_copy("long-spec.md")
        task = store.create_task(self.conn, title="to be deleted", project=None, spec_path=str(spec_path))
        task_id = task["id"]
        doc_id = self.conn.execute(
            "SELECT id FROM documents WHERE task_id=?", (task_id,)
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO embeddings(doc_id, doc_kind, task_id, project, model, dim, vec, text_hash, embedded_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, "task", task_id, None, "test-model", 1, b"\x00", "hash", store.now_iso()),
        )
        self.conn.commit()

        store.delete_task(self.conn, task_id)

        for table in ("documents", "document_chunks", "document_chunk_fts", "embeddings"):
            if table == "document_chunks":
                count = self.conn.execute(
                    "SELECT count(*) FROM document_chunks WHERE document_id=?", (doc_id,)
                ).fetchone()[0]
            else:
                count = self.conn.execute(
                    f"SELECT count(*) FROM {table} WHERE task_id=?", (task_id,)
                ).fetchone()[0]
            self.assertEqual(count, 0, f"{table} should have no rows left for {task_id}")
        self.assertIsNone(
            self.conn.execute("SELECT id FROM tasks WHERE id=?", (task_id,)).fetchone()
        )


class RemoveProjectTests(TempDbTestCase):
    def test_force_remove_cleans_document_and_embedding_rows(self) -> None:
        """remove_project(force=True) mirrored delete_task's cleanup of comments/deps/events
        but forgot document_chunk_fts/embeddings/documents — same rows delete_task clears."""
        store.upsert_project(self.conn, "todelete")
        spec_path = self.fixture_copy("long-spec.md")
        task = store.create_task(self.conn, title="in doomed project", project="todelete",
                                  spec_path=str(spec_path))
        task_id = task["id"]
        self.conn.execute(
            "INSERT INTO embeddings(doc_id, doc_kind, task_id, project, model, dim, vec, text_hash, embedded_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (task_id, "task", task_id, "todelete", "test-model", 1, b"\x00", "hash", store.now_iso()),
        )
        self.conn.commit()

        store.remove_project(self.conn, "todelete", force=True)

        for table in ("documents", "document_chunk_fts", "embeddings", "tasks"):
            count = self.conn.execute(
                f"SELECT count(*) FROM {table} WHERE task_id=?" if table != "tasks"
                else "SELECT count(*) FROM tasks WHERE id=?",
                (task_id,),
            ).fetchone()[0]
            self.assertEqual(count, 0, f"{table} should have no rows left for {task_id}")


class MigrationTests(unittest.TestCase):
    def test_migrate_adds_document_status_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = pathlib.Path(tmp) / "old.db"
            raw = sqlite3.connect(db_path)
            try:
                raw.execute(
                    """
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
                        UNIQUE(task_id, kind, path)
                    )
                    """
                )
                raw.execute(
                    "INSERT INTO documents(id, task_id, kind, path, revision, content_hash, title) "
                    "VALUES('d1','t1','spec','/tmp/x.md',1,'abc','x')"
                )
                raw.commit()
            finally:
                raw.close()

            conn = db_mod.init(db_path=db_path)
            try:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(documents)")}
                self.assertIn("status", cols)
                self.assertIn("error", cols)
                self.assertIn("checked_at", cols)
                row = conn.execute("SELECT status FROM documents WHERE id='d1'").fetchone()
                self.assertEqual(row["status"], "ok")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
