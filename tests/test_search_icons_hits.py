"""Иконки статусов/этапов в store, порядок hits и точный фильтр этапа (listik-w72e).

Ollama по-настоящему не дёргается: `embed.ollama_embed` подменяется заглушкой,
которая по умолчанию падает — векторная ветка деградирует молча, как и в бою без
Ollama. `search._VEC_CACHE` процессный и переживает отдельные временные базы,
поэтому сбрасывается в setUp и после каждой прямой записи в `embeddings`.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from listik import embed, paths, search, store
from tests.helpers import TempDbTestCase

ORTHO = [1.0, 0.0, 0.0, 0.0]


def _default_stub(*_args, **_kwargs):
    raise RuntimeError("ollama недоступна в тестах")


class IconDictTests(unittest.TestCase):
    def test_status_icon_covers_all_status_titles(self) -> None:
        self.assertEqual(set(store.STATUS_ICON), set(store.STATUS_TITLES))
        self.assertEqual(store.STATUS_ICON, {
            "open": "○", "in_progress": "▶", "blocked": "■",
            "review": "◐", "done": "✓", "cancelled": "✕",
        })

    def test_stage_icon_covers_all_stage_titles(self) -> None:
        self.assertEqual(set(store.STAGE_ICON), set(store.STAGE_TITLES))
        self.assertEqual(store.STAGE_ICON, {
            "s1-spec": "ТЗ", "s2-review": "крит", "s3-impl": "код",
            "s4-judge": "судья", "done": "готово",
        })


class CosineContractTests(unittest.TestCase):
    def test_identical_vectors_score_one(self) -> None:
        self.assertAlmostEqual(embed.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0)

    def test_zero_norm_scores_zero(self) -> None:
        self.assertEqual(embed.cosine([0.0, 0.0], [1.0, 2.0]), 0.0)
        self.assertEqual(embed.cosine([1.0, 2.0], [0.0, 0.0]), 0.0)
        self.assertEqual(embed.cosine([1.0, 2.0], [0.0, 0.0], na=1.0, nb=0.0), 0.0)


class SearchIconsHitsTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        search.invalidate_vectors()
        self._patcher = patch.object(embed, "ollama_embed", Mock(side_effect=_default_stub))
        self.ollama_embed = self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.addCleanup(search.invalidate_vectors)

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

    def _head_lines(self, res: dict) -> list[str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            search.print_results(res)
        heads = []
        for line in buf.getvalue().splitlines():
            tok = line.strip().split(" ", 1)[0]
            if tok.endswith(".") and tok[:-1].isdigit():
                heads.append(line)
        return heads

    def test_print_results_draws_real_icons(self) -> None:
        done_t = store.create_task(
            self.conn, title="альфа маркер иконок", project="listik", status="done")
        cancelled_t = store.create_task(
            self.conn, title="бета маркер иконок", project="listik", status="cancelled")
        review_t = store.create_task(
            self.conn, title="гамма маркер иконок", project="listik", status="review")

        res = search.search(self.conn, "маркер", mode="text")
        self.assertEqual(len(res["results"]), 3)
        want = {done_t["id"]: "✓", cancelled_t["id"]: "✕", review_t["id"]: "◐"}

        heads = self._head_lines(res)
        self.assertEqual(len(heads), 3)
        for line, r in zip(heads, res["results"]):
            self.assertIn(r["id"], line)
            self.assertIn(want[r["id"]], line)
            self.assertNotIn("?", line)

    def test_vector_branch_skips_zero_norm_and_wrong_dim(self) -> None:
        good = store.create_task(self.conn, title="векторная хорошая", project="listik")
        zero = store.create_task(self.conn, title="векторная нулевая", project="listik")
        short = store.create_task(self.conn, title="векторная короткая", project="listik")
        self._insert_vec(good["id"], good["id"], list(ORTHO))
        self._insert_vec(zero["id"], zero["id"], [0.0, 0.0, 0.0, 0.0])
        self._insert_vec(short["id"], short["id"], [1.0, 0.0])
        self.ollama_embed.side_effect = lambda texts, model=paths.EMBED_MODEL: [list(ORTHO)]

        vec_out = search.vector(self.conn, "векторная", 200)
        self.assertEqual([v["doc_id"] for v in vec_out], [good["id"]])
        self.assertTrue(all(v["score"] > 0 for v in vec_out))

        res = search.search(self.conn, "векторная", mode="vector")
        self.assertEqual([r["id"] for r in res["results"]], [good["id"]])

    def test_hits_sorted_by_rrf_and_best_hit_is_first(self) -> None:
        task = store.create_task(
            self.conn, title="иглоколка в заголовке", project="listik")
        comment = store.add_comment(
            self.conn, task["id"], "иглоколка в комментарии", author="me", kind="journal")

        res = search.search(self.conn, "иглоколка", mode="text")
        row = next(r for r in res["results"] if r["id"] == task["id"])
        self.assertGreaterEqual(len(row["hits"]), 2)
        self.assertLessEqual(len(row["hits"]), 4)
        self.assertEqual({h["kind"] for h in row["hits"]}, {"task", "comment"})
        self.assertIn(comment["id"], [h["doc_id"] for h in row["hits"]])
        self.assertEqual(row["hits"][0]["kind"], row["best_hit"]["kind"])
        self.assertEqual(row["hits"][0]["doc_id"], row["best_hit"]["doc_id"])
        rrfs = [h["rrf"] for h in row["hits"]]
        self.assertEqual(rrfs, sorted(rrfs, reverse=True))

    def test_stage_filter_matches_exact_or_prefix_only(self) -> None:
        task = store.create_task(
            self.conn, title="этапная карточка", project="listik", stage="s3-impl")

        res = search.search(self.conn, "этапная", mode="text", stage="impl")
        self.assertNotIn(task["id"], [r["id"] for r in res["results"]])

        for stage in ("s3-impl", "s3"):
            res = search.search(self.conn, "этапная", mode="text", stage=stage)
            self.assertIn(task["id"], [r["id"] for r in res["results"]])


if __name__ == "__main__":
    unittest.main()
