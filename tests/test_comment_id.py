"""Id комментария без коллизий в одну миллисекунду (listik-csoa).

Два комментария одной задаче в одну миллисекунду с одинаковым случайным суффиксом
падали на `UNIQUE constraint failed: comments.id`; теперь вставка повторяется
с новым суффиксом, а побочные эффекты (UPDATE, событие) остаются одиночными.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from unittest import mock

from listik import store
from tests.helpers import TempDbTestCase

FROZEN = datetime(2026, 9, 21, 12, 0, 0, 123000, tzinfo=timezone.utc)
FROZEN_ISO = "2026-09-21T12:00:00Z"


class CommentIdCollisionTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Коллизия id", project="demo")["id"]
        patcher = mock.patch.object(store, "datetime", wraps=datetime)
        self.addCleanup(patcher.stop)
        patcher.start().now.return_value = FROZEN

    def rows(self):
        return self.conn.execute(
            "SELECT id, author, kind, text, created_at FROM comments WHERE task_id=? ORDER BY rowid",
            (self.task_id,)).fetchall()

    def comment_events(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='comment'",
            (self.task_id,)).fetchone()[0]

    def test_same_millisecond_collision_retries(self):
        with mock.patch.object(store.random, "randint", side_effect=[500, 500, 501]) as rnd:
            store.add_comment(self.conn, self.task_id, "первый", author="a1", kind="journal")
            store.add_comment(self.conn, self.task_id, "второй", author="a2", kind="comment")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        pattern = rf"^{re.escape(self.task_id)}:\d+:\d{{3}}$"
        for row in rows:
            self.assertRegex(row["id"], pattern)
            self.assertEqual(row["created_at"], FROZEN_ISO)
        self.assertTrue(rows[0]["id"].endswith(":500"))
        self.assertTrue(rows[1]["id"].endswith(":501"))
        self.assertEqual((rows[1]["author"], rows[1]["kind"], rows[1]["text"]),
                         ("a2", "comment", "второй"))
        self.assertEqual(self.comment_events(), 2)
        self.assertEqual(rnd.call_count, 3)

    def test_gives_up_after_ten_attempts(self):
        with mock.patch.object(store.random, "randint", side_effect=[500] * 11):
            store.add_comment(self.conn, self.task_id, "первый", author="a1", kind="journal")
            with self.assertRaises(sqlite3.IntegrityError):
                store.add_comment(self.conn, self.task_id, "второй", author="a2", kind="comment")
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.comment_events(), 1)
