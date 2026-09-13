"""Tests for chunk-level search enrichment, vector hits and refresh_all (порция 01.c).

Ollama is never hit for real: `embed.ollama_embed` is patched at setUp with a stub that
raises by default, so any test relying on the vector branch must install its own stub and
can assert it was actually called. `search._VEC_CACHE` is a process-global keyed only by
model name, shared across every temp database in a single `unittest discover` run, so it is
invalidated in setUp and after every direct write to `embeddings`.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from listik import documents, embed, paths, search, store
from tests.helpers import TempDbTestCase

UNIQUE_PHRASE = "Абзац номер 013 большого раздела"
ORTHO = {
    "e1": [1.0, 0.0, 0.0, 0.0],
    "e2": [0.0, 1.0, 0.0, 0.0],
    "e3": [0.0, 0.0, 1.0, 0.0],
    "e4": [0.0, 0.0, 0.0, 1.0],
}


def _default_stub(*_args, **_kwargs):
    raise RuntimeError("ollama недоступна в тестах")


class ChunkSearchTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        search.invalidate_vectors()
        self._patcher = patch.object(embed, "ollama_embed", Mock(side_effect=_default_stub))
        self.ollama_embed = self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(search.invalidate_vectors)

        self.spec_path = self.fixture_copy("long-spec.md")
        self.task_a = store.create_task(
            self.conn, title="поиск по чанкам A", project="listik", spec_path=str(self.spec_path))
        self.task_b = store.create_task(
            self.conn, title="задача без документов гвоздь", project="listik")

        doc_row = self.conn.execute(
            "SELECT id FROM documents WHERE task_id=? AND kind='spec'", (self.task_a["id"],)
        ).fetchone()
        self.doc_id = doc_row["id"]
        self.chunk_row = self.conn.execute(
            "SELECT * FROM document_chunks WHERE document_id=? AND text LIKE ?",
            (self.doc_id, f"%{UNIQUE_PHRASE}%"),
        ).fetchone()
        self.assertIsNotNone(self.chunk_row, "тестовая фикстура должна содержать уникальную фразу")

    def _insert_vec(self, doc_id: str, task_id: str, vec: list[float]) -> None:
        self.conn.execute(
            "INSERT INTO embeddings(doc_id, doc_kind, task_id, project, model, dim, vec, "
            "text_hash, embedded_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, "
            "vec=excluded.vec, text_hash=excluded.text_hash, embedded_at=excluded.embedded_at",
            (doc_id, "chunk", task_id, "listik", paths.EMBED_MODEL, len(vec),
             embed.vec_to_blob(vec), "test-hash", store.now_iso()))
        self.conn.commit()
        search.invalidate_vectors()

    def test_text_search_finds_task_a_via_chunk_with_section_metadata(self) -> None:
        res = search.search(self.conn, UNIQUE_PHRASE, mode="text")
        self.assertEqual(res["results"][0]["id"], self.task_a["id"])
        best = res["results"][0]["best_hit"]
        self.assertEqual(best["kind"], "chunk")
        self.assertEqual(best["heading"], self.chunk_row["heading"])
        self.assertIn("Долгая спецификация", best["breadcrumb"] or "")
        self.assertEqual(best["path"], str(self.spec_path))
        self.assertIsInstance(best["start_line"], int)
        self.assertIsInstance(best["end_line"], int)
        self.assertEqual(best["start_line"], self.chunk_row["start_line"])
        self.assertEqual(best["end_line"], self.chunk_row["end_line"])

    def test_text_search_finds_task_b_by_title_with_null_chunk_fields(self) -> None:
        res = search.search(self.conn, "гвоздь", mode="text")
        ids = [r["id"] for r in res["results"]]
        self.assertIn(self.task_b["id"], ids)
        row = next(r for r in res["results"] if r["id"] == self.task_b["id"])
        self.assertEqual(row["best_hit"]["kind"], "task")
        self.assertIsNone(row["best_hit"]["heading"])

    def test_vector_branch_degrades_silently_and_matches_text_mode(self) -> None:
        # search.vector() is checked in isolation first: the porcion boundary forbids
        # touching search_memories, which — independently of this порция — also calls
        # embed.ollama_embed for mode="hybrid"/"vector" and would inflate a global call
        # count taken from the full search.search(mode="hybrid") below. Isolating the
        # chunk-vector call is what actually proves the branch reached Ollama instead of
        # exiting early on `if not rows: return []`.
        self._insert_vec(self.chunk_row["id"], self.task_a["id"], ORTHO["e1"])

        vec_out = search.vector(self.conn, UNIQUE_PHRASE, 200)
        self.assertEqual(vec_out, [])
        self.assertEqual(self.ollama_embed.call_count, 1)

        text_res = search.search(self.conn, UNIQUE_PHRASE, mode="text")
        hybrid_res = search.search(self.conn, UNIQUE_PHRASE, mode="hybrid")

        self.assertEqual([r["id"] for r in hybrid_res["results"]],
                         [r["id"] for r in text_res["results"]])

    def test_vector_hits_are_enriched_from_document_chunks(self) -> None:
        self._insert_vec(self.chunk_row["id"], self.task_a["id"], ORTHO["e1"])
        self.ollama_embed.side_effect = lambda texts, model=paths.EMBED_MODEL: [list(ORTHO["e1"])]

        res = search.search(self.conn, UNIQUE_PHRASE, mode="vector")
        ids = [r["id"] for r in res["results"]]
        self.assertIn(self.task_a["id"], ids)
        row = next(r for r in res["results"] if r["id"] == self.task_a["id"])
        best = row["best_hit"]
        self.assertEqual(best["kind"], "chunk")
        self.assertTrue(best["heading"])
        self.assertTrue(best["breadcrumb"])
        self.assertTrue(best["path"])
        self.assertIsInstance(best["start_line"], int)

    def test_hybrid_merge_keeps_lexical_snippet_and_sums_rrf(self) -> None:
        self._insert_vec(self.chunk_row["id"], self.task_a["id"], ORTHO["e1"])
        self.ollama_embed.side_effect = lambda texts, model=paths.EMBED_MODEL: [list(ORTHO["e1"])]

        text_res = search.search(self.conn, UNIQUE_PHRASE, mode="text")
        hybrid_res = search.search(self.conn, UNIQUE_PHRASE, mode="hybrid")

        text_row = next(r for r in text_res["results"] if r["id"] == self.task_a["id"])
        hybrid_row = next(r for r in hybrid_res["results"] if r["id"] == self.task_a["id"])
        hybrid_best = hybrid_row["best_hit"]
        text_best = text_row["best_hit"]

        self.assertEqual(hybrid_best["kind"], "chunk")
        self.assertIn("[[", hybrid_best["snippet"])
        self.assertTrue(hybrid_best["heading"])
        self.assertTrue(hybrid_best["breadcrumb"])
        self.assertGreater(hybrid_best["rrf"], text_best["rrf"])

    def test_orphan_chunk_embedding_is_dropped_before_aggregation(self) -> None:
        orphan_id = f"{self.doc_id}:9999"
        self._insert_vec(orphan_id, self.task_a["id"], ORTHO["e2"])
        self.ollama_embed.side_effect = lambda texts, model=paths.EMBED_MODEL: [list(ORTHO["e2"])]

        res = search.search(self.conn, UNIQUE_PHRASE, mode="vector")
        self.assertEqual(res["results"], [])

        self._insert_vec(self.chunk_row["id"], self.task_a["id"], ORTHO["e2"])
        res_without_orphan_isolated = search.search(self.conn, UNIQUE_PHRASE, mode="vector")
        score_with_orphan = next(
            r["score"] for r in res_without_orphan_isolated["results"] if r["id"] == self.task_a["id"])

        # Remove the orphan row and re-run: the score for A must not depend on the orphan
        # sharing the same query vector — it was excluded from ranking either way.
        self.conn.execute("DELETE FROM embeddings WHERE doc_id=?", (orphan_id,))
        self.conn.commit()
        search.invalidate_vectors()
        res_baseline = search.search(self.conn, UNIQUE_PHRASE, mode="vector")
        score_baseline = next(
            r["score"] for r in res_baseline["results"] if r["id"] == self.task_a["id"])
        self.assertEqual(score_with_orphan, score_baseline)

    def test_reindex_after_shrinking_file_drops_old_chunk_embeddings(self) -> None:
        old_chunk_ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM document_chunks WHERE document_id=?", (self.doc_id,))]
        for cid in old_chunk_ids:
            self._insert_vec(cid, self.task_a["id"], ORTHO["e3"])

        self.spec_path.write_text("# Долгая спецификация\n\nОдин короткий абзац.\n",
                                  encoding="utf-8")
        documents.index_document(self.conn, self.task_a["id"], str(self.spec_path), kind="spec")

        remaining = self.conn.execute(
            "SELECT count(*) FROM embeddings WHERE doc_kind='chunk' AND doc_id IN ({})".format(
                ",".join("?" * len(old_chunk_ids))), old_chunk_ids
        ).fetchone()[0]
        self.assertEqual(remaining, 0)


class RefreshAllTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spec_path = self.fixture_copy("long-spec.md")
        self.task = store.create_task(
            self.conn, title="refresh all task", project="listik", spec_path=str(self.spec_path))

    def _revision(self) -> int:
        return self.conn.execute(
            "SELECT revision FROM documents WHERE task_id=? AND kind='spec'", (self.task["id"],)
        ).fetchone()[0]

    def test_refresh_all_reindexes_changed_file_once(self) -> None:
        with self.spec_path.open("a", encoding="utf-8") as f:
            f.write("\n## Дописано\n\nНовый текст после первой индексации.\n")

        res = documents.refresh_all(self.conn)
        self.assertEqual(res["reindexed"], 1)
        self.assertEqual(self._revision(), 2)

        res_again = documents.refresh_all(self.conn)
        self.assertEqual(res_again["reindexed"], 0)
        self.assertEqual(self._revision(), 2)

    def test_refresh_all_skips_closed_task(self) -> None:
        store.update_task(self.conn, self.task["id"], status="done")
        revision_before = self._revision()

        with self.spec_path.open("a", encoding="utf-8") as f:
            f.write("\n## После закрытия\n\nЭтот текст не должен переиндексироваться.\n")

        res = documents.refresh_all(self.conn)
        self.assertEqual(res["checked"], 0)
        self.assertEqual(self._revision(), revision_before)


if __name__ == "__main__":
    unittest.main()
