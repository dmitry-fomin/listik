"""`get_task` отдаёт все события задачи, без `LIMIT 100` (listik-pzpd).

Доска строит из `events` ленту и шаги конвейера (событие `created`, первые этапы), рой ищет
в них старый `revoke`, поэтому у длинной истории не должно теряться ничего старого. События
пишутся с явными разными `ts` в прошлом: в одной секунде порядок не задан, и `created` мог бы
случайно попасть в первые 100.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from listik import fence, store
from tests.helpers import TempDbTestCase

HEARTBEATS = 150
OLDEST_TS = "2000-01-01T00:00:00Z"
START = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _ts(seconds: int) -> str:
    return (START + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TaskEventsHistoryTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="Длинная история", project="demo")["id"]
        for i in range(HEARTBEATS):
            store.event(self.conn, self.task, "heartbeat", ts=_ts(i))
        # Карантин посреди истории: строго между первым и последним `heartbeat`.
        store.event(self.conn, self.task, fence.REJECTED_KIND, from_value="0", to_value="1",
                    ts=_ts(HEARTBEATS // 2))
        store.close_task(self.conn, self.task, reason="проверка длинной истории")

    def _events(self) -> list[dict]:
        return store.get_task(self.conn, self.task)["events"]

    def test_s1_long_history_returns_every_event(self) -> None:
        events = self._events()
        expected = self.conn.execute(
            "SELECT count(*) FROM events WHERE task_id = ? AND kind != 'rejected'",
            (self.task,)).fetchone()[0]
        self.assertGreater(expected, 100)
        self.assertEqual(len(events), expected)
        self.assertIn(OLDEST_TS, [e["ts"] for e in events if e["kind"] == "heartbeat"])
        self.assertEqual(sum(1 for e in events if e["kind"] == "created"), 1)

    def test_s2_newest_first(self) -> None:
        stamps = [e["ts"] for e in self._events()]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    def test_s3_close_visible_in_long_history(self) -> None:
        events = self._events()
        done = [e for e in events if e["kind"] == "status" and e["to_value"] == "done"]
        self.assertTrue(done)
        self.assertEqual(done[0]["ts"], max(e["ts"] for e in events))

    def test_s4_quarantine_hidden_in_long_history(self) -> None:
        plain = store.get_task(self.conn, self.task)
        self.assertFalse([e for e in plain["events"] if e["kind"] == "rejected"])
        full = store.get_task(self.conn, self.task, with_rejected=True)
        self.assertFalse([e for e in full["events"] if e["kind"] == "rejected"])
        self.assertIsInstance(full["rejected"], list)
        self.assertEqual(len(full["rejected"]), 1)
