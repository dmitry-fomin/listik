"""Tests for listik.documents.index_document / index_task_documents on a temp database."""
from __future__ import annotations

import unittest

from listik import db as db_mod, documents, store
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


class JournalPathDecisionTests(TempDbTestCase):
    """journal_path — старое имя decision: индексируется, только когда decision_path пуст."""

    def _write(self, name: str) -> str:
        path = self.tmp_path / name
        path.write_text(f"# {name}\n\nТекст {name}.\n", encoding="utf-8")
        return str(path)

    def test_decision_path_wins_over_journal_path(self) -> None:
        decision, journal = self._write("decision.md"), self._write("journal.md")
        task = store.create_task(self.conn, title="decision и journal", project=None,
                                 decision_path=decision, journal_path=journal)

        docs = documents.index_task_documents(self.conn, task["id"])

        decisions = [d for d in docs if d["kind"] == "decision"]
        self.assertEqual([d["path"] for d in decisions], [decision])
        rows = self.conn.execute(
            "SELECT count(*) FROM documents WHERE task_id=? AND kind='decision'",
            (task["id"],)).fetchone()[0]
        self.assertEqual(rows, 1)

    def test_journal_path_alone_is_the_decision_document(self) -> None:
        journal = self._write("journal.md")
        task = store.create_task(self.conn, title="только journal", project=None,
                                 journal_path=journal)

        docs = documents.index_task_documents(self.conn, task["id"])

        decisions = [d for d in docs if d["kind"] == "decision"]
        self.assertEqual([d["path"] for d in decisions], [journal])


class StaleDocumentTests(TempDbTestCase):
    """index_task_documents удаляет строки documents, чей путь карточке больше не соответствует."""

    def _write(self, name: str) -> str:
        path = self.tmp_path / name
        path.write_text(f"# {name}\n\nТекст {name}.\n", encoding="utf-8")
        return str(path)

    def _rows(self, task_id: str) -> list[tuple[str, str]]:
        return sorted((r["kind"], r["path"]) for r in self.conn.execute(
            "SELECT kind, path FROM documents WHERE task_id=?", (task_id,)))

    def _row(self, task_id: str, kind: str, path: str):
        return self.conn.execute("SELECT * FROM documents WHERE task_id=? AND kind=? AND path=?",
                                 (task_id, kind, path)).fetchone()

    def _insert_legacy(self, task_id: str, kind: str, path: str) -> str:
        doc_id = f"legacy-{kind}-{task_id}"
        self.conn.execute(
            "INSERT INTO documents(id,task_id,kind,path,revision,content_hash,title,status) "
            "VALUES(?,?,?,?,1,'x',?, 'ok')", (doc_id, task_id, kind, path, path))
        documents._insert_chunks(self.conn, task_id, doc_id, "# Старое\n\nСтарый текст.\n")
        self.conn.commit()
        self.assertGreater(_counts(self.conn, doc_id)[1], 0)
        return doc_id

    def test_context_commits_drop_visible_to_second_connection(self) -> None:
        spec = self._write("spec.md")
        task = store.create_task(self.conn, title="context и второе соединение", project=None,
                                 spec_path=spec)
        legacy = self._insert_legacy(task["id"], "spec", "old-spec.md")
        chunk_id = self.conn.execute(
            "SELECT id FROM document_chunks WHERE document_id=? LIMIT 1", (legacy,)).fetchone()[0]
        self.conn.execute(
            "INSERT INTO embeddings(doc_id,doc_kind,task_id,model,dim,vec,text_hash,embedded_at) "
            "VALUES(?,'chunk',?,'m',1,x'00','h','t')", (chunk_id, task["id"]))
        self.conn.commit()

        documents.context(self.conn, task["id"], "s1-spec")

        other = db_mod.connect(self.db_path)
        try:
            self.assertEqual(_counts(other, legacy), (0, 0, 0))
            self.assertEqual(other.execute(
                "SELECT count(*) FROM embeddings WHERE doc_id=?", (chunk_id,)).fetchone()[0], 0)
        finally:
            other.close()

    def test_drop_writes_no_events_and_returns_current_docs(self) -> None:
        spec, review = self._write("spec.md"), self._write("review.md")
        task = store.create_task(self.conn, title="события и возврат", project=None,
                                 spec_path=spec, review_path=review)
        self._insert_legacy(task["id"], "spec", "old-spec.md")
        self._insert_legacy(task["id"], "notes", "notes.md")
        count = "SELECT count(*) FROM events WHERE task_id=?"
        before = self.conn.execute(count, (task["id"],)).fetchone()[0]

        docs = documents.index_task_documents(self.conn, task["id"])

        self.assertEqual(self.conn.execute(count, (task["id"],)).fetchone()[0], before)
        self.assertEqual([(d["kind"], d["path"]) for d in docs], [("spec", spec), ("review", review)])
        self.assertEqual(docs, [documents.document_json(self.conn, d["id"]) for d in docs])

    def test_legacy_journal_row_dropped_when_decision_path_set(self) -> None:
        decision, journal = self._write("decision.md"), self._write("journal.md")
        task = store.create_task(self.conn, title="decision и journal", project=None,
                                 decision_path=decision, journal_path=journal)
        live = self._row(task["id"], "decision", decision)
        legacy = self._insert_legacy(task["id"], "decision", journal)

        documents.index_task_documents(self.conn, task["id"])

        self.assertEqual(self._rows(task["id"]), [("decision", decision)])
        self.assertEqual(_counts(self.conn, legacy), (0, 0, 0))
        after = self._row(task["id"], "decision", decision)
        self.assertEqual((after["id"], after["revision"]), (live["id"], live["revision"]))

    def test_spec_path_change_drops_old_row_and_chunks(self) -> None:
        old, new = self._write("a.md"), self._write("b.md")
        task = store.create_task(self.conn, title="смена spec", project=None, spec_path=old)
        old_id = self._row(task["id"], "spec", old)["id"]

        store.update_task(self.conn, task["id"], spec_path=new)

        self.assertEqual(self._rows(task["id"]), [("spec", new)])
        self.assertEqual(_counts(self.conn, old_id), (0, 0, 0))
        self.assertEqual([d["path"] for d in store.task_documents(self.conn, task["id"])], [new])

    def test_cleared_review_path_drops_rows(self) -> None:
        review = self._write("review.md")
        task = store.create_task(self.conn, title="очистка review", project=None, review_path=review)

        store.update_task(self.conn, task["id"], review_path="")

        self.assertEqual(self._rows(task["id"]), [])

    def test_put_document_with_new_path_leaves_one_spec_row(self) -> None:
        old = self._write("old.md")
        task = store.create_task(self.conn, title="put с новым путём", project=None, spec_path=old)

        documents.put_document(self.conn, task["id"], "spec", "# Новый\n\nтекст\n", path="new.md")

        self.assertEqual(self._rows(task["id"]), [("spec", "new.md")])

    def test_uploaded_row_dropped_after_path_change(self) -> None:
        task = store.create_task(self.conn, title="upload и смена пути", project=None)
        up = documents.put_document(self.conn, task["id"], "spec", "# Старый\n\nтекст\n", path="old.md")

        store.update_task(self.conn, task["id"], spec_path="new.md")

        self.assertIsNone(self._row(task["id"], "spec", "old.md"))
        self.assertEqual(_counts(self.conn, up["id"]), (0, 0, 0))

    def test_current_upload_row_kept(self) -> None:
        task = store.create_task(self.conn, title="upload по текущему пути", project=None)
        up = documents.put_document(self.conn, task["id"], "spec", "# Текст\n\nтело\n", path="cur.md")
        self._insert_legacy(task["id"], "spec", "stale.md")

        documents.index_task_documents(self.conn, task["id"])

        row = self._row(task["id"], "spec", "cur.md")
        self.assertEqual((row["id"], row["revision"], row["source"], row["content"]),
                         (up["id"], up["revision"], "upload", "# Текст\n\nтело\n"))
        self.assertEqual(self._rows(task["id"]), [("spec", "cur.md")])

    def test_unknown_kind_row_dropped(self) -> None:
        task = store.create_task(self.conn, title="чужой kind", project=None)
        legacy = self._insert_legacy(task["id"], "notes", "notes.md")

        self.assertEqual(documents.index_task_documents(self.conn, task["id"]), [])

        self.assertEqual(self._rows(task["id"]), [])
        self.assertEqual(_counts(self.conn, legacy), (0, 0, 0))

    def test_journal_only_row_kept_with_same_id_and_revision(self) -> None:
        journal = self._write("journal.md")
        task = store.create_task(self.conn, title="только journal", project=None, journal_path=journal)
        before = self._row(task["id"], "decision", journal)
        self._insert_legacy(task["id"], "decision", "old-decision.md")

        documents.index_task_documents(self.conn, task["id"])

        after = self._row(task["id"], "decision", journal)
        self.assertEqual((after["id"], after["revision"]), (before["id"], before["revision"]))
        self.assertEqual(self._rows(task["id"]), [("decision", journal)])

    def test_all_paths_cleared_drops_everything_but_not_other_task(self) -> None:
        paths = {f: self._write(f"{f}.md") for f in
                 ("spec_path", "checklist_path", "review_path", "decision_path", "journal_path")}
        task = store.create_task(self.conn, title="все пути", project=None, **paths)
        other = store.create_task(self.conn, title="соседка", project=None,
                                  spec_path=paths["spec_path"])
        other_before = self._rows(other["id"])
        other_doc = self._row(other["id"], "spec", paths["spec_path"])
        other_counts = _counts(self.conn, other_doc["id"])
        self.assertTrue(self._rows(task["id"]))

        store.update_task(self.conn, task["id"], **{f: "" for f in paths})

        self.assertEqual(self._rows(task["id"]), [])
        self.assertEqual(self._rows(other["id"]), other_before)
        self.assertEqual(_counts(self.conn, other_doc["id"]), other_counts)

    def test_other_task_with_same_spec_path_untouched(self) -> None:
        old, new = self._write("shared.md"), self._write("new.md")
        first = store.create_task(self.conn, title="первая", project=None, spec_path=old)
        second = store.create_task(self.conn, title="вторая", project=None, spec_path=old)
        second_doc = self._row(second["id"], "spec", old)
        second_counts = _counts(self.conn, second_doc["id"])

        store.update_task(self.conn, first["id"], spec_path=new)

        self.assertEqual(self._rows(first["id"]), [("spec", new)])
        after = self._row(second["id"], "spec", old)
        self.assertEqual((after["id"], after["revision"]), (second_doc["id"], second_doc["revision"]))
        self.assertEqual(_counts(self.conn, second_doc["id"]), second_counts)


if __name__ == "__main__":
    unittest.main()
