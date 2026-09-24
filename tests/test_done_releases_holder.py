"""Закрытие задачи снимает держателя (listik-ugw8)."""
from __future__ import annotations

import unittest
from unittest import mock

from listik import client, paths, store
from listik import db as db_mod
from tests.helpers import TempDbTestCase


class DoneReleasesHolderTests(TempDbTestCase):
    def _claimed(self) -> str:
        task = store.create_task(self.conn, title="Закрыть", project="p", assignee="me")
        store.claim(self.conn, task["id"], holder="dsh")
        return task["id"]

    def events(self, tid: str, kind: str | None = None) -> list:
        rows = self.conn.execute(
            "SELECT kind, from_value, to_value, actor FROM events WHERE task_id=? ORDER BY id",
            (tid,)).fetchall()
        return [r for r in rows if kind is None or r["kind"] == kind]

    def assertReleasedOnce(self, tid: str, before: int = 0) -> None:
        task = store.get_task(self.conn, tid)
        self.assertFalse(task["holder"])
        self.assertFalse(task["holder_note"])
        new = self.events(tid)[before:]
        releases = [r for r in new if r["kind"] == "release"]
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]["from_value"], "dsh")
        self.assertFalse(releases[0]["to_value"])
        self.assertEqual([r for r in new if r["kind"] == "claim"], [])
        status = [r for r in new if r["kind"] == "status"]
        if status:
            self.assertEqual(releases[0]["actor"], status[0]["actor"])

    def test_a_done_releases(self) -> None:
        tid = self._claimed()
        before = len(self.events(tid))
        store.update_task(self.conn, tid, status="done", stage="done",
                          actor="agent:dsh", note="готово")
        self.assertReleasedOnce(tid, before)
        task = store.get_task(self.conn, tid)
        self.assertEqual(task["assignee"], "me")
        self.assertTrue(any("dsh" in k for k in task["worked_by"]))

    def test_b_local_call_releases(self) -> None:
        tid = self._claimed()
        before = len(self.events(tid))
        with mock.patch.object(paths, "DB_PATH", self.db_path), \
                mock.patch.object(db_mod, "init", return_value=self.conn):
            client.local_call("update", task_id=tid, status="done", stage="done",
                              actor="agent:dsh")
        self.assertReleasedOnce(tid, before)

    def test_c_cancelled_releases(self) -> None:
        tid = self._claimed()
        before = len(self.events(tid))
        store.update_task(self.conn, tid, status="cancelled", actor="agent:dsh")
        self.assertReleasedOnce(tid, before)

    def test_d_next_stage_done_single_release(self) -> None:
        tid = self._claimed()
        before = len(self.events(tid))
        store.next_stage(self.conn, tid, to_stage="done")
        self.assertReleasedOnce(tid, before)

    def test_e_explicit_empty_holder_single_release(self) -> None:
        tid = self._claimed()
        before = len(self.events(tid))
        store.update_task(self.conn, tid, status="done", holder="")
        self.assertReleasedOnce(tid, before)

    def test_f_explicit_other_holder_ignored(self) -> None:
        tid = self._claimed()
        before = len(self.events(tid))
        store.update_task(self.conn, tid, status="done", holder="other")
        self.assertReleasedOnce(tid, before)
        task = store.get_task(self.conn, tid)
        self.assertEqual(task["assignee"], "me")
        self.assertTrue(any("dsh" in k for k in task["worked_by"]))

    def test_g_no_holder_no_false_release(self) -> None:
        tid = store.create_task(self.conn, title="Без держателя", project="p")["id"]
        store.update_task(self.conn, tid, status="done")
        self.assertEqual([r["kind"] for r in self.events(tid)
                          if r["kind"] in ("release", "claim")], [])
        count = len(self.events(tid))
        out = store.update_task(self.conn, tid, status="done")
        self.assertTrue(out.get("unchanged"))
        self.assertEqual(len(self.events(tid)), count)

    def test_h_reopen_keeps_holder_untouched(self) -> None:
        tid = self._claimed()
        store.update_task(self.conn, tid, status="done")
        releases = len(self.events(tid, "release"))
        store.update_task(self.conn, tid, status="open")
        task = store.get_task(self.conn, tid)
        self.assertFalse(task["holder"])
        self.assertFalse(task["holder_note"])
        self.assertEqual(len(self.events(tid, "release")), releases)


if __name__ == "__main__":
    unittest.main()
