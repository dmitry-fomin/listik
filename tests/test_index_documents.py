"""Tests for listik.documents.index_document / index_task_documents on a temp database."""
from __future__ import annotations

import unittest

from listik import documents, store
from tests.helpers import TempDbTestCase


def _counts(conn, doc_id):
    docs = conn.execute("SELECT count(*) FROM documents WHERE id=?", (doc_id,)).fetchone()[0]
    chunks = conn.execute("SELECT count(*) FROM document_chunks WHERE document_id=?", (doc_id,)).fetchone()[0]
    fts = conn.execute("SELECT count(*) FROM document_chunk_fts WHERE document_id=?", (doc_id,)).fetchone()[0]
    return docs, chunks, fts


class IndexDocumentTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="doc test task", project=None)
        self.spec_path = self.fixture_copy("long-spec.md")

    def test_first_index_creates_one_document_and_matching_chunks(self) -> None:
        result = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")
        self.assertEqual(result["revision"], 1)
        docs, chunks, fts = _counts(self.conn, result["id"])
        self.assertEqual(docs, 1)
        self.assertGreater(chunks, 0)
        self.assertEqual(chunks, fts)

    def test_reindex_unchanged_file_is_a_noop(self) -> None:
        first = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")
        events_before = self.conn.execute(
            "SELECT count(*) FROM events WHERE task_id=? AND kind='document_error'", (self.task["id"],)
        ).fetchone()[0]
        docs_before, chunks_before, fts_before = _counts(self.conn, first["id"])

        second = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")

        self.assertEqual(second["revision"], 1)
        self.assertEqual(second["updated_at"], first["updated_at"])
        docs_after, chunks_after, fts_after = _counts(self.conn, first["id"])
        self.assertEqual((docs_before, chunks_before, fts_before), (docs_after, chunks_after, fts_after))
        events_after = self.conn.execute(
            "SELECT count(*) FROM events WHERE task_id=? AND kind='document_error'", (self.task["id"],)
        ).fetchone()[0]
        self.assertEqual(events_before, events_after)
        self.assertEqual(events_after, 0)

    def test_appending_a_section_bumps_revision_and_adds_chunks(self) -> None:
        first = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")
        _, chunks_before, _ = _counts(self.conn, first["id"])

        with self.spec_path.open("a", encoding="utf-8") as f:
            f.write("\n## Дописанный раздел\n\nНовый текст, дописанный после первой индексации.\n")

        second = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")
        self.assertEqual(second["revision"], 2)
        _, chunks_after, fts_after = _counts(self.conn, first["id"])
        expected_new = len(documents.split_markdown(
            "\n## Дописанный раздел\n\nНовый текст, дописанный после первой индексации.\n"))
        self.assertEqual(chunks_after, chunks_before + expected_new)

        chunk_ids = {r["id"] for r in self.conn.execute(
            "SELECT id FROM document_chunks WHERE document_id=?", (first["id"],))}
        fts_chunk_ids = {r["chunk_id"] for r in self.conn.execute(
            "SELECT chunk_id FROM document_chunk_fts WHERE document_id=?", (first["id"],))}
        self.assertTrue(fts_chunk_ids.issubset(chunk_ids))
        self.assertEqual(fts_after, len(chunk_ids))

    def test_shortening_the_file_leaves_no_orphan_chunks(self) -> None:
        first = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")

        short_content = "# Долгая спецификация\n\nКороткий текст вместо длинного документа.\n"
        self.spec_path.write_text(short_content, encoding="utf-8")
        second = documents.index_document(self.conn, self.task["id"], str(self.spec_path), kind="spec")

        expected_chunks = len(documents.split_markdown(short_content))
        max_ordinal = self.conn.execute(
            "SELECT max(ordinal) FROM document_chunks WHERE document_id=?", (first["id"],)
        ).fetchone()[0]
        self.assertEqual(max_ordinal, expected_chunks)
        _, chunks_after, fts_after = _counts(self.conn, first["id"])
        self.assertEqual(chunks_after, expected_chunks)
        self.assertEqual(fts_after, expected_chunks)
        self.assertEqual(second["revision"], 2)


class CreateTaskIndexesDocumentsTests(TempDbTestCase):
    def test_create_task_indexes_spec_and_checklist(self) -> None:
        spec_path = self.fixture_copy("long-spec.md")
        checklist_path = self.fixture_copy("checklist.md")
        task = store.create_task(
            self.conn, title="task with docs", project=None,
            spec_path=str(spec_path), checklist_path=str(checklist_path),
        )
        rows = list(self.conn.execute(
            "SELECT kind FROM documents WHERE task_id=? ORDER BY kind", (task["id"],)
        ))
        kinds = {r["kind"] for r in rows}
        self.assertEqual(kinds, {"spec", "checklist"})
        for kind in ("spec", "checklist"):
            doc_id = self.conn.execute(
                "SELECT id FROM documents WHERE task_id=? AND kind=?", (task["id"], kind)
            ).fetchone()["id"]
            chunks = self.conn.execute(
                "SELECT count(*) FROM document_chunks WHERE document_id=?", (doc_id,)
            ).fetchone()[0]
            self.assertGreater(chunks, 0)


if __name__ == "__main__":
    unittest.main()
