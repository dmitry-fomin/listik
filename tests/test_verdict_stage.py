"""Защита verdict-комментариев этапом судьи."""
from __future__ import annotations

import json
import unittest
from unittest import mock

from listik import client, db as db_mod, errors, paths, server, store
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


class VerdictFieldTests(TempDbTestCase):
    """Поле `verdict` у комментариев kind=verdict в карточке (listik-m88l)."""

    FAIL_TEXT = "VERDICT: FAIL\n1. тест x падает"

    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Вердикты", project="demo",
                                         stage="s1-spec")["id"]
        # Человек пишет вердикт на любом этапе; на s1-spec FAIL карточку не двигает.
        store.add_comment(self.conn, self.task_id, "VERDICT: PASS", kind="verdict",
                          author="me", created_at="2026-01-01T00:00:01Z")
        store.add_comment(self.conn, self.task_id, self.FAIL_TEXT, kind="verdict",
                          author="me", created_at="2026-01-01T00:00:02Z")

    def _verdicts(self, comments: list[dict]) -> dict[str, dict]:
        return {c["text"]: c for c in comments if c["kind"] == "verdict"}

    def test_pass_and_fail_verdicts_get_field(self) -> None:
        by_text = self._verdicts(store.get_task(self.conn, self.task_id)["comments"])
        self.assertEqual(by_text["VERDICT: PASS"]["verdict"], "pass")
        self.assertEqual(by_text[self.FAIL_TEXT]["verdict"], "fail")

    def test_historical_free_text_verdict_is_none(self) -> None:
        self.conn.execute(
            "INSERT INTO comments(id, task_id, author, kind, text, created_at) "
            "VALUES(?,?,?,?,?,?)",
            (f"{self.task_id}:old", self.task_id, "me", "verdict", "красный: тест падает",
             "2026-01-01T00:00:03Z"))
        self.conn.commit()
        c = self._verdicts(store.get_task(self.conn, self.task_id)["comments"])[
            "красный: тест падает"]
        self.assertIn("verdict", c)
        self.assertIsNone(c["verdict"])

    def test_other_kinds_have_no_field(self) -> None:
        for kind in ("journal", "comment"):
            store.add_comment(self.conn, self.task_id, "VERDICT: FAIL\n1. x", kind=kind,
                              author="agent:codex")
        others = [c for c in store.get_task(self.conn, self.task_id)["comments"]
                  if c["kind"] in ("journal", "comment")]
        self.assertEqual(sorted(c["kind"] for c in others), ["comment", "journal"])
        for c in others:
            self.assertNotIn("verdict", c)

    def test_http_show_carries_field_in_json(self) -> None:
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            status, task = server.handle("GET", f"/api/tasks/{self.task_id}", {}, {},
                                         authed=True)
        self.assertEqual(status, 200)
        by_text = self._verdicts(json.loads(errors.json_dumps(task))["comments"])
        self.assertEqual(by_text["VERDICT: PASS"]["verdict"], "pass")
        self.assertEqual(by_text[self.FAIL_TEXT]["verdict"], "fail")

    def test_local_call_show_carries_field(self) -> None:
        with mock.patch.object(db_mod, "init", return_value=self.conn), \
                mock.patch.object(paths, "DB_PATH", self.db_path):
            task = client.local_call("show", task_id=self.task_id)
        by_text = self._verdicts(task["comments"])
        self.assertEqual(by_text["VERDICT: PASS"]["verdict"], "pass")
        self.assertEqual(by_text[self.FAIL_TEXT]["verdict"], "fail")


if __name__ == "__main__":
    unittest.main()
