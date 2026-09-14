"""Переход этапа в done закрывает задачу, как `listik done` (listik-rku8)."""
from __future__ import annotations

import unittest

from listik import store
from tests.helpers import TempDbTestCase


class StageDoneClosesTests(TempDbTestCase):
    def _claimed(self, stage: str | None = None) -> str:
        task = store.create_task(self.conn, title="Закрыть этапом", project="demo", stage=stage)
        store.claim(self.conn, task["id"], holder="dsh", harness="dsh")
        return task["id"]

    def assertClosed(self, out: dict) -> None:
        self.assertEqual(out["stage"], "done")
        self.assertEqual(out["status"], "done")
        self.assertTrue(out["closed_at"])
        self.assertFalse(out["holder"])

    def test_explicit_to_done_closes(self) -> None:
        tid = self._claimed()
        self.assertClosed(store.next_stage(self.conn, tid, to_stage="done", holder="dsh"))

    def test_implicit_after_judge_closes(self) -> None:
        tid = self._claimed(stage="s4-judge")
        self.assertClosed(store.next_stage(self.conn, tid))

    def test_repeat_to_done_is_noop(self) -> None:
        tid = self._claimed()
        store.next_stage(self.conn, tid, to_stage="done")
        out = store.next_stage(self.conn, tid, to_stage="done")
        self.assertTrue(out.get("unchanged"))
        self.assertClosed(out)


if __name__ == "__main__":
    unittest.main()
