"""Tests for `store.set_needs_owner` (шаг 02, порция a).

`needs-owner` должен всегда писать вопрос/ответ в историю карточки (комментарий +
событие), даже если флаг уже стоит в нужном значении, и не терять второй вопрос.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

from listik import search as search_mod
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def _comments(conn, task_id, kind=None):
    task = store.get_task(conn, task_id)
    comments = task["comments"]
    if kind is not None:
        comments = [c for c in comments if c["kind"] == kind]
    return comments


def _events(conn, task_id, kind):
    return conn.execute(
        "SELECT * FROM events WHERE task_id = ? AND kind = ? ORDER BY id", (task_id, kind)
    ).fetchall()


class SetNeedsOwnerTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Задача", project="demo")["id"]

    def test_first_question_writes_comment_and_event(self) -> None:
        out = store.set_needs_owner(self.conn, self.task_id, value=True, text="Q1")
        self.assertTrue(out["needs_owner"])
        questions = _comments(self.conn, self.task_id, kind="question")
        self.assertEqual([c["text"] for c in questions], ["Q1"])
        events = _events(self.conn, self.task_id, "question")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["note"], "Q1")

    def test_second_question_while_flag_already_set_is_not_lost(self) -> None:
        store.set_needs_owner(self.conn, self.task_id, value=True, text="Q1")
        out = store.set_needs_owner(self.conn, self.task_id, value=True, text="Q2")
        questions = _comments(self.conn, self.task_id, kind="question")
        self.assertEqual([c["text"] for c in questions], ["Q1", "Q2"])
        events = _events(self.conn, self.task_id, "question")
        self.assertEqual(len(events), 2)
        self.assertTrue(out["needs_owner"])
        self.assertNotIn("unchanged", out)

    def test_answer_clears_flag_and_writes_answer_comment(self) -> None:
        store.set_needs_owner(self.conn, self.task_id, value=True, text="Q1")
        out = store.set_needs_owner(self.conn, self.task_id, value=False, text="Ответ автора")
        self.assertFalse(out["needs_owner"])
        answers = _comments(self.conn, self.task_id, kind="answer")
        self.assertEqual([c["text"] for c in answers], ["Ответ автора"])
        events = _events(self.conn, self.task_id, "answer")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["note"], "Ответ автора")

    def test_without_text_no_comment_but_event_written(self) -> None:
        out = store.set_needs_owner(self.conn, self.task_id, value=True)
        self.assertTrue(out["needs_owner"])
        self.assertEqual(_comments(self.conn, self.task_id), [])
        self.assertEqual(len(_events(self.conn, self.task_id, "question")), 1)

        out2 = store.set_needs_owner(self.conn, self.task_id, value=False)
        self.assertFalse(out2["needs_owner"])
        self.assertEqual(_comments(self.conn, self.task_id), [])
        self.assertEqual(len(_events(self.conn, self.task_id, "answer")), 1)

    def test_question_text_is_findable_by_search(self) -> None:
        store.set_needs_owner(self.conn, self.task_id, value=True, text="квазипериодический вопрос")
        res = search_mod.search(self.conn, "квазипериодический", mode="text")
        ids = [r["id"] for r in res["results"]]
        self.assertIn(self.task_id, ids)

    def test_response_shape_matches_get_task(self) -> None:
        out = store.set_needs_owner(self.conn, self.task_id, value=True, text="Q1")
        expected = set(store.get_task(self.conn, self.task_id).keys())
        self.assertEqual(set(out.keys()), expected)
        self.assertNotIn("unchanged", out)

    def test_unknown_task_raises_key_error(self) -> None:
        with self.assertRaises(KeyError):
            store.set_needs_owner(self.conn, "нет-такой", value=True, text="Q")

    def test_cli_local_two_questions_json_output_is_clean(self) -> None:
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        cwd = str(LISTIK_BIN.parent.parent)

        p1 = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "needs-owner", self.task_id,
             "Q-cli", "--json"],
            capture_output=True, text=True, check=True, env=env, cwd=cwd,
        )
        json.loads(p1.stdout)

        p2 = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "needs-owner", self.task_id,
             "Q-cli-2", "--json"],
            capture_output=True, text=True, check=True, env=env, cwd=cwd,
        )
        json.loads(p2.stdout)

        task = store.get_task(self.conn, self.task_id)
        questions = [c["text"] for c in task["comments"] if c["kind"] == "question"]
        self.assertEqual(questions, ["Q-cli", "Q-cli-2"])

    def test_cli_local_unknown_task_fails_with_message(self) -> None:
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        cwd = str(LISTIK_BIN.parent.parent)
        p = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "needs-owner", "нет-такой", "Q"],
            capture_output=True, text=True, env=env, cwd=cwd,
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertTrue(p.stderr.strip())


if __name__ == "__main__":
    unittest.main()
