"""Id комментария: миллисекунда и 32 бита случайного суффикса (listik-csoa, listik-i5i0).

Раньше суффикс был трёхзначным и при коллизии в одну миллисекунду вставка
повторялась в цикле. Теперь суффикс — `secrets.token_hex(4)`, вставка одна,
без повторов: два комментария в одну миллисекунду получают разные id.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from unittest import mock

from listik import store
from tests.helpers import TempDbTestCase

FROZEN = datetime(2026, 9, 21, 12, 0, 0, 123000, tzinfo=timezone.utc)
FROZEN_ISO = "2026-09-21T12:00:00Z"


class CommentIdTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Id комментария", project="demo")["id"]
        patcher = mock.patch.object(store, "datetime", wraps=datetime)
        self.addCleanup(patcher.stop)
        patcher.start().now.return_value = FROZEN

    def rows(self):
        return self.conn.execute(
            "SELECT id, created_at FROM comments WHERE task_id=? ORDER BY rowid",
            (self.task_id,)).fetchall()

    def comment_events(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=? AND kind='comment'",
            (self.task_id,)).fetchone()[0]

    def test_same_millisecond_comments_get_distinct_ids(self):
        store.add_comment(self.conn, self.task_id, "первый", author="a1", kind="journal")
        store.add_comment(self.conn, self.task_id, "второй", author="a2", kind="comment")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["id"], rows[1]["id"])
        pattern = rf"^{re.escape(self.task_id)}:\d+:[0-9a-f]{{8}}$"
        for row in rows:
            self.assertRegex(row["id"], pattern)
            self.assertEqual(row["created_at"], FROZEN_ISO)
        self.assertEqual(self.comment_events(), 2)

    def test_suffix_comes_from_token_hex(self):
        with mock.patch.object(store.secrets, "token_hex", return_value="deadbeef") as tok:
            out = store.add_comment(self.conn, self.task_id, "текст", author="a1",
                                    kind="journal")
        self.assertTrue(out["id"].endswith(":deadbeef"))
        tok.assert_called_once_with(4)
