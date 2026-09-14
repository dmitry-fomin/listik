"""Поиск по точному id задачи (спека docs/specs/search-exact-id.md, listik-dug6).

`task_fts.task_id` объявлен UNINDEXED, поэтому id ищется отдельным запросом по `tasks`
и ставится первым в результатах. Ollama по-настоящему не дёргается: `embed.ollama_embed`
подменяется заглушкой, которая по умолчанию падает — векторная ветка деградирует молча,
как и в бою без Ollama. `search._VEC_CACHE` процессный и переживает отдельные временные
базы, поэтому сбрасывается в setUp и после каждой прямой записи в `embeddings`.
"""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from listik import embed, paths, search, store
from tests.helpers import TempDbTestCase

TASK_ID = "listik-b3j0"
ORTHO = [1.0, 0.0, 0.0, 0.0]


def _default_stub(*_args, **_kwargs):
    raise RuntimeError("ollama недоступна в тестах")


class SearchExactIdTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        search.invalidate_vectors()
        self._patcher = patch.object(embed, "ollama_embed", Mock(side_effect=_default_stub))
        self.ollama_embed = self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(search.invalidate_vectors)

        self.target = store.create_task(
            self.conn, task_id=TASK_ID, title="карточка, которую ищут по id", project="listik",
            description="находится только отдельным запросом по tasks")
        self.other = store.create_task(
            self.conn, title="Задача про документ и ссылки", project="listik",
            description="обычное текстовое совпадение")

    def _insert_vec(self, doc_id: str, task_id: str, vec: list[float]) -> None:
        self.conn.execute(
            "INSERT INTO embeddings(doc_id, doc_kind, task_id, project, model, dim, vec, "
            "text_hash, embedded_at) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, "
            "vec=excluded.vec, text_hash=excluded.text_hash, embedded_at=excluded.embedded_at",
            (doc_id, "task", task_id, "listik", paths.EMBED_MODEL, len(vec),
             embed.vec_to_blob(vec), "test-hash", store.now_iso()))
        self.conn.commit()
        search.invalidate_vectors()

    def _assert_first_is_id_hit(self, res: dict, expected_id: str = TASK_ID) -> dict:
        self.assertTrue(res["results"], "id-совпадение должно быть в результатах")
        row = res["results"][0]
        self.assertEqual(row["id"], expected_id)
        self.assertEqual(row["best_hit"]["kind"], "id")
        self.assertEqual(row["best_hit"]["doc_id"], expected_id)
        self.assertEqual(row["best_hit"]["snippet"], row["title"])
        self.assertIn("id", [h["kind"] for h in row["hits"]])
        # score id-совпадения выше любого RRF-результата
        self.assertGreater(row["score"],
                           max((r["score"] for r in res["results"][1:]), default=0.0))
        return row

    def test_exact_id_is_first(self) -> None:
        res = search.search(self.conn, TASK_ID, mode="text")
        self._assert_first_is_id_hit(res)

    def test_exact_id_is_case_insensitive(self) -> None:
        res = search.search(self.conn, TASK_ID.upper(), mode="text")
        self._assert_first_is_id_hit(res)

    def test_id_tail_is_first(self) -> None:
        res = search.search(self.conn, "b3j0", mode="text")
        self._assert_first_is_id_hit(res)

    def test_id_plus_words_keeps_other_results(self) -> None:
        # «карточка» есть в заголовке самой id-задачи, «документ» — в заголовке соседней:
        # id-совпадение не должно ни потерять соседа, ни задвоить саму задачу.
        res = search.search(self.conn, f"{TASK_ID} карточка документ", mode="text")
        self._assert_first_is_id_hit(res)
        ids = [r["id"] for r in res["results"]]
        self.assertIn(self.other["id"], ids[1:], "остальные результаты должны сохраниться")
        self.assertEqual(ids.count(TASK_ID), 1, "id-задача не должна дублироваться")

    def test_project_filter_excludes_id_match(self) -> None:
        found = search.search(self.conn, TASK_ID, mode="text", project="listik")
        self._assert_first_is_id_hit(found)

        missed = search.search(self.conn, TASK_ID, mode="text", project="zoloto585")
        self.assertEqual(missed["results"], [])

    def test_vector_mode_does_not_mix_in_exact_id(self) -> None:
        empty = search.search(self.conn, TASK_ID, mode="vector")
        self.assertEqual(empty["results"], [])

        # Даже когда задача честно находится векторной веткой, хита kind="id" быть не должно.
        self._insert_vec(TASK_ID, TASK_ID, ORTHO)
        self.ollama_embed.side_effect = (
            lambda texts, model=paths.EMBED_MODEL: [list(ORTHO)])
        res = search.search(self.conn, TASK_ID, mode="vector")
        self.assertEqual([r["id"] for r in res["results"]], [TASK_ID])
        row = res["results"][0]
        self.assertEqual(row["best_hit"]["kind"], "task")
        self.assertNotIn("id", [h["kind"] for h in row["hits"]])


if __name__ == "__main__":
    unittest.main()
