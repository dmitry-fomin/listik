"""Защита verdict-комментариев этапом судьи."""
from __future__ import annotations

import unittest

from listik import store
from tests.helpers import TempDbTestCase


class VerdictStageGuardTests(TempDbTestCase):
    def test_verdict_is_saved_as_comment_outside_judge_stage(self) -> None:
        task_id = store.create_task(self.conn, title="Реализация", project="demo",
                                    stage="s3-impl")["id"]

        out = store.add_comment(self.conn, task_id, "VERDICT: PASS", kind="verdict",
                                author="agent:codex", harness="codex")

        self.assertEqual(out["kind"], "comment")
        self.assertFalse(out["verdict_accepted"])
        self.assertIn("не принят", out["message"])
        row = self.conn.execute(
            "SELECT kind, text FROM comments WHERE task_id = ?", (task_id,)).fetchone()
        self.assertEqual(tuple(row), ("comment", "VERDICT: PASS"))
        self.assertEqual(store.get_task(self.conn, task_id)["stage"], "s3-impl")

    def test_unknown_agent_verdict_is_saved_as_comment_outside_judge_stage(self) -> None:
        task_id = store.create_task(self.conn, title="Неизвестный агент", project="demo",
                                    stage="s3-impl")["id"]

        out = store.add_comment(self.conn, task_id, "VERDICT: PASS", kind="verdict",
                                author="agent:mcp")

        self.assertEqual(out["kind"], "comment")
        self.assertFalse(out["verdict_accepted"])

    def test_verdict_is_allowed_on_judge_stage(self) -> None:
        task_id = store.create_task(self.conn, title="Проверка", project="demo",
                                    stage="s4-judge")["id"]

        out = store.add_comment(self.conn, task_id, "VERDICT: PASS", kind="verdict",
                                author="agent:codex", harness="codex")

        self.assertEqual(out["kind"], "verdict")
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM comments WHERE task_id = ? AND kind = 'verdict'",
            (task_id,)).fetchone()[0], 1)

    def test_human_verdict_is_allowed_on_judge_stage(self) -> None:
        task_id = store.create_task(self.conn, title="Проверка человеком", project="demo",
                                    stage="s4-judge")["id"]

        out = store.add_comment(self.conn, task_id, "VERDICT: PASS", kind="verdict",
                                author="me")

        self.assertEqual(out["kind"], "verdict")
        self.assertTrue(out["verdict_accepted"])

    def test_human_verdict_is_accepted_before_judge_stage(self) -> None:
        task_id = store.create_task(self.conn, title="Ранний verdict", project="demo",
                                    stage="s1-spec")["id"]

        out = store.add_comment(self.conn, task_id, "VERDICT: PASS", kind="verdict",
                                author="human")

        self.assertEqual(out["kind"], "verdict")
        self.assertTrue(out["verdict_accepted"])
        self.assertEqual(self.conn.execute(
            "SELECT kind FROM comments WHERE task_id = ?", (task_id,)).fetchone()[0], "verdict")

    def test_verdict_text_remains_an_ordinary_comment_for_other_kinds(self) -> None:
        task_id = store.create_task(self.conn, title="Журнал", project="demo",
                                    stage="s3-impl")["id"]

        out = store.add_comment(self.conn, task_id, "VERDICT: PASS", kind="journal",
                                author="agent:codex")

        self.assertEqual(out["kind"], "journal")


if __name__ == "__main__":
    unittest.main()
