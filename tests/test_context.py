"""Tests for the per-stage `context` contract (шаг 01, порция d)."""
from __future__ import annotations

import json
import pathlib
import subprocess
import tempfile
import time
import unittest

from listik import documents, store
from tests.helpers import TempDbTestCase

STAGES = ("s1-spec", "s2-review", "s3-impl", "s4-judge")


def _walk(value, path=""):
    """Yield (path, key, value) for every key found anywhere in a nested structure."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield path, k, v
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")


def _contains_substring(value, needle: str) -> bool:
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_contains_substring(v, needle) for v in value.values())
    if isinstance(value, list):
        return any(_contains_substring(v, needle) for v in value)
    return False


class ContextContractTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.spec_path = self.fixture_copy("long-spec.md")
        self.checklist_path = self.fixture_copy("checklist.md")
        self.task = store.create_task(
            self.conn, title="контекст задачи", project="listik", stage="s1-spec",
            spec_path=str(self.spec_path), checklist_path=str(self.checklist_path),
            acceptance="требований первого подраздела",
        )
        self.tid = self.task["id"]

        # Hard blocker with a holder, so deps_state carries age fields to scrub.
        self.blocker = store.create_task(self.conn, title="блокер задачи", project="listik")
        store.claim(self.conn, self.blocker["id"], holder="human-holder")
        store.add_dep(self.conn, self.tid, self.blocker["id"], dep_type="blocks", confirm=True)
        # An agent's hypothesis at s1 becomes a soft `suggested-blocks` edge, not a hard one.
        store.add_dep(self.conn, self.tid, self.blocker["id"], dep_type="waits-for",
                      confirm=False, created_by="claude")

        self.review1 = store.add_comment(self.conn, self.tid, "первый текст ревью, не должен просочиться на s1",
                                         author="human", kind="review")
        time.sleep(0.005)
        self.review2 = store.add_comment(self.conn, self.tid, "второй текст ревью, последний по времени",
                                         author="human", kind="review")
        time.sleep(0.005)
        self.journal_comment = store.add_comment(self.conn, self.tid, "запись журнала о решении",
                                                 author="human", kind="journal")
        time.sleep(0.005)
        self.verdict_comment = store.add_comment(self.conn, self.tid, "VERDICT: PASS\nтекст вердикта, не должен просочиться на s1",
                                                 author="human", kind="verdict")

        for _ in range(3):  # s1 -> s2 -> s3 -> s4, leaves stage-transition events behind
            store.next_stage(self.conn, self.tid)

    def ctx(self, stage: str, **kwargs) -> dict:
        return documents.context(self.conn, self.tid, stage, **kwargs)

    # ---------------------------------------------------------------- shape

    def test_same_key_set_at_every_stage(self) -> None:
        key_sets = {stage: set(self.ctx(stage).keys()) for stage in STAGES}
        first = key_sets[STAGES[0]]
        for stage, keys in key_sets.items():
            self.assertEqual(keys, first, f"ключи ответа на {stage} отличаются")

    def test_task_has_no_detail_keys_at_any_stage(self) -> None:
        forbidden = {"comments", "events", "documents", "dependencies", "dependents", "deps_state"}
        for stage in STAGES:
            task = self.ctx(stage)["task"]
            for key in forbidden:
                self.assertNotIn(key, task, f"{key} утёк в task на {stage}")

    def test_s1_hides_review_and_verdict_text(self) -> None:
        out = self.ctx("s1-spec")
        dumped = json.dumps(out, ensure_ascii=False)
        self.assertNotIn(self.verdict_comment["text"], dumped)
        self.assertNotIn(self.review1["text"], dumped)
        self.assertNotIn(self.review2["text"], dumped)

    def test_s3_hides_first_review_keeps_last(self) -> None:
        out = self.ctx("s3-impl")
        dumped = json.dumps(out, ensure_ascii=False)
        self.assertNotIn(self.review1["text"], dumped)
        self.assertIn(self.review2["text"], dumped)

    # ------------------------------------------------------------ stability

    def test_no_age_or_stale_fields_anywhere(self) -> None:
        out = self.ctx("s3-impl")
        for _path, key, _value in _walk(out):
            self.assertFalse(key.endswith("_age"), f"{key} утёк в ответ")
            self.assertFalse(key.endswith("_hours"), f"{key} утёк в ответ")
            self.assertNotIn(key, ("stale", "stale_holder", "abandoned"))

    def test_dependencies_has_no_reasons_key(self) -> None:
        out = self.ctx("s3-impl")
        self.assertNotIn("reasons", out["dependencies"])

    def test_no_age_phrases_in_serialized_response(self) -> None:
        out = self.ctx("s3-impl")
        dumped = json.dumps(out, ensure_ascii=False)
        self.assertNotIn(" ч)", dumped)
        self.assertNotIn(" мин)", dumped)

    def test_repeated_calls_are_byte_stable(self) -> None:
        for stage in STAGES:
            first = json.dumps(self.ctx(stage), ensure_ascii=False, sort_keys=True)
            second = json.dumps(self.ctx(stage), ensure_ascii=False, sort_keys=True)
            self.assertEqual(first, second, f"{stage} не стабилен между вызовами")

    # ------------------------------------------------------------------ s1

    def test_s1_has_every_chunk_of_spec_and_checklist(self) -> None:
        out = self.ctx("s1-spec")
        expected = sum(d["chunk_count"] for d in out["documents"])
        self.assertEqual(len(out["chunks"]), expected)
        self.assertFalse(out["limits"]["truncated"])
        self.assertEqual(out["reviews"], [])
        self.assertIsNone(out["verdict"])
        self.assertIsNone(out["worktree"])
        blocks = {r["block"] for r in out["reasons"]}
        self.assertIn("card", blocks)
        self.assertIn("acceptance", blocks)
        self.assertIn("dependencies", blocks)
        chunk_reasons = [r for r in out["reasons"] if r["block"] == "chunk"]
        self.assertEqual(len(chunk_reasons), len(out["chunks"]))

    def test_s1_with_small_max_chars_is_truncated_with_reason(self) -> None:
        out = self.ctx("s1-spec", max_chars=5000)
        self.assertTrue(out["limits"]["truncated"])
        self.assertIn(str(self.spec_path), out["limits"]["reason"])
        self.assertGreater(out["limits"]["dropped_chunks"], 0)

    # ------------------------------------------------------------------ s2

    def test_s2_has_both_reviews(self) -> None:
        out = self.ctx("s2-review")
        self.assertEqual(len(out["reviews"]), 2)

    # ------------------------------------------------------------------ s3

    def test_s3_without_portion_uses_checklist_and_lexical_layers(self) -> None:
        s1 = self.ctx("s1-spec")
        out = self.ctx("s3-impl")
        checklist_chunks = [c for c in out["chunks"] if c["kind"] == "checklist"]
        spec_chunks = [c for c in out["chunks"] if c["kind"] == "spec"]
        self.assertTrue(checklist_chunks)
        self.assertTrue(all(c["reason"] == "чек-лист целиком" for c in checklist_chunks))
        self.assertTrue(spec_chunks)
        self.assertTrue(all("лексическое совпадение" in c["reason"] for c in spec_chunks))
        self.assertTrue(any("Первый подраздел" in (c.get("heading") or "") for c in spec_chunks))
        self.assertLess(len(out["chunks"]), len(s1["chunks"]))
        self.assertEqual(len(out["reviews"]), 1)
        self.assertEqual(len(out["dependencies"]["hard"]), 1)
        self.assertEqual(out["dependencies"]["suggested"], [])

    def test_s3_with_portion_matches_only_that_heading(self) -> None:
        out = self.ctx("s3-impl", portion="Первый подраздел")
        spec_chunks = [c for c in out["chunks"] if c["kind"] == "spec"]
        self.assertTrue(spec_chunks)
        for c in spec_chunks:
            self.assertIn("совпадение с порцией", c["reason"])
            self.assertIn("Первый подраздел", (c.get("heading") or "") + (c.get("breadcrumb") or ""))

    def test_s3_with_unknown_portion_falls_back_to_lexical(self) -> None:
        out = self.ctx("s3-impl", portion="раздел которого нет в файле")
        spec_chunks = [c for c in out["chunks"] if c["kind"] == "spec"]
        self.assertTrue(spec_chunks)
        self.assertTrue(all("лексическое совпадение" in c["reason"] for c in spec_chunks))

    def test_s3_with_no_acceptance_and_no_title_match_falls_back_to_start(self) -> None:
        task = store.create_task(
            self.conn, title="ярлык без совпадений", project="listik", stage="s3-impl",
            spec_path=str(self.spec_path),
        )
        out = documents.context(self.conn, task["id"], "s3-impl")
        spec_chunks = [c for c in out["chunks"] if c["kind"] == "spec"]
        self.assertTrue(spec_chunks)
        self.assertTrue(all("начало документа" in c["reason"] for c in spec_chunks))

    def test_s3_gets_last_red_verdict_only(self) -> None:
        # Последний вердикт зелёный — правок нет, блока нет.
        self.assertIsNone(self.ctx("s3-impl")["verdict"])
        time.sleep(0.005)
        store.add_comment(self.conn, self.tid, "VERDICT: FAIL\n1. старый пункт",
                          author="human", kind="verdict")
        time.sleep(0.005)
        red = store.add_comment(self.conn, self.tid, "VERDICT: FAIL\n1. README.md:17 — поправь",
                                author="human", kind="verdict")
        out = self.ctx("s3-impl")
        self.assertEqual(out["verdict"]["text"], red["text"])
        self.assertIn("verdict", {r["block"] for r in out["reasons"]})
        # Текстовый вывод CLI: правки вердикта — первым блоком, до acceptance и чанков.
        p = subprocess.run(
            ["python3", str(LISTIK_BIN), "--local", "context", self.tid, "--stage", "s3-impl"],
            capture_output=True, text=True,
            env={**__import__("os").environ, "LISTIK_DB": str(self.db_path)})
        self.assertEqual(p.returncode, 0, p.stderr)
        text = p.stdout
        self.assertIn("README.md:17", text)
        self.assertLess(text.index("README.md:17"), text.index("acceptance:"))
        self.assertNotIn("старый пункт", text)

    # ------------------------------------------------------------------ s4

    def test_s4_has_last_verdict_and_full_journal(self) -> None:
        out = self.ctx("s4-judge")
        self.assertIsNotNone(out["verdict"])
        self.assertEqual(out["verdict"]["text"], self.verdict_comment["text"])
        kinds = {j["kind"] for j in out["journal"]}
        self.assertIn("journal", kinds)
        self.assertIn("stage", kinds)
        journal_item = next(j for j in out["journal"] if j["kind"] == "journal")
        self.assertEqual(journal_item["id"], self.journal_comment["id"])
        # Порядок журнала — (ts, kind, id) с секундным ts: первым среди событий
        # этапа может оказаться создание карточки (from=None), если оно попало в
        # предыдущую секунду. Проверяем именно переход конвейера.
        stage_item = next(j for j in out["journal"]
                          if j["kind"] == "stage" and j["from"] is not None)
        self.assertIsNotNone(stage_item["from"])
        self.assertIsNotNone(stage_item["to"])
        self.assertTrue(stage_item["id"].startswith("event:"))
        for item in out["journal"]:
            self.assertIn("ts", item)
        self.assertEqual(
            out["journal"],
            sorted(out["journal"], key=lambda j: (j["ts"], j["kind"], j["id"])),
        )

    def test_s4_worktree_with_commit_and_dirty_file(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo = pathlib.Path(repo_dir)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "a.txt").write_text("one\n", encoding="utf-8")
            subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                 "commit", "-q", "-m", "init"],
                cwd=repo, check=True,
            )
            (repo / "a.txt").write_text("two\n", encoding="utf-8")

            store.update_task(self.conn, self.tid, worktree=str(repo))
            out = self.ctx("s4-judge")
            wt = out["worktree"]
            self.assertTrue(wt["exists"])
            self.assertTrue(wt["git"])
            self.assertEqual(len(wt["head"]), 40)
            self.assertTrue(all(c in "0123456789abcdef" for c in wt["head"]))
            self.assertTrue(wt["branch"])
            self.assertTrue(any("a.txt" in line for line in wt["changed_files"]))
            self.assertIn("a.txt", wt["diff_stat"])

    def test_s4_worktree_repo_without_commits(self) -> None:
        with tempfile.TemporaryDirectory() as repo_dir:
            repo = pathlib.Path(repo_dir)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
            (repo / "b.txt").write_text("uncommitted\n", encoding="utf-8")

            store.update_task(self.conn, self.tid, worktree=str(repo))
            out = self.ctx("s4-judge")
            wt = out["worktree"]
            self.assertTrue(wt["exists"])
            self.assertTrue(wt["git"])
            self.assertIsNone(wt["head"])
            self.assertEqual(wt["diff_stat"], "")
            self.assertTrue(any("b.txt" in line for line in wt["changed_files"]))
            self.assertEqual(wt["reason"], "нет коммитов")

    def test_s4_worktree_absent_missing_and_non_git(self) -> None:
        task_no_wt = store.create_task(self.conn, title="без worktree", project="listik")
        out = documents.context(self.conn, task_no_wt["id"], "s4-judge")
        self.assertEqual(out["worktree"]["exists"], False)

        with tempfile.TemporaryDirectory() as tmp:
            missing = pathlib.Path(tmp) / "gone"
            store.update_task(self.conn, task_no_wt["id"], worktree=str(missing))
            out = documents.context(self.conn, task_no_wt["id"], "s4-judge")
            self.assertEqual(out["worktree"]["exists"], False)

            no_git = pathlib.Path(tmp) / "plain"
            no_git.mkdir()
            store.update_task(self.conn, task_no_wt["id"], worktree=str(no_git))
            out = documents.context(self.conn, task_no_wt["id"], "s4-judge")
            self.assertEqual(out["worktree"]["exists"], True)
            self.assertEqual(out["worktree"]["git"], False)

    # ------------------------------------------------------------ no documents

    def test_task_without_documents_returns_empty_chunks_and_documents(self) -> None:
        task = store.create_task(self.conn, title="без документов", project="listik")
        for stage in STAGES:
            out = documents.context(self.conn, task["id"], stage)
            self.assertEqual(out["documents"], [])
            self.assertEqual(out["chunks"], [])


LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


class TextFormatCliTests(TempDbTestCase):
    """`--format text` used to print `worktree` via bare f-string interpolation (a raw
    Python dict repr) and silently drop `suggested` blockers entirely — both fixed to
    render readably, matching what the JSON contract already carries."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["python3", str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True,
            env={**__import__("os").environ, "LISTIK_DB": str(self.db_path)},
            cwd=str(LISTIK_BIN.parent.parent),
        )

    def test_worktree_is_rendered_not_repr(self) -> None:
        task = store.create_task(self.conn, title="s4 worktree", project="listik")
        store.update_task(self.conn, task["id"], worktree=str(LISTIK_BIN.parent.parent), stage="s4-judge")
        p = self._run("context", task["id"], "--stage", "s4-judge")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("'exists':", p.stdout)
        self.assertNotIn("'changed_files':", p.stdout)
        self.assertIn("worktree:", p.stdout)
        self.assertIn("ветка:", p.stdout)

    def test_suggested_blockers_are_printed_at_s1(self) -> None:
        blocker = store.create_task(self.conn, title="blocker", project="listik")["id"]
        task = store.create_task(self.conn, title="needs blocker", project="listik")
        store.add_dep(self.conn, task["id"], blocker, "blocks", created_by="agent:claude")
        p = self._run("context", task["id"], "--stage", "s1-spec")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("предложенные блокеры", p.stdout)
        self.assertIn(blocker, p.stdout)


if __name__ == "__main__":
    unittest.main()
