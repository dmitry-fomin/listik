"""Tests for `store.stats`: темп закрытия для страницы «Метрики»."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from listik import store
from tests.helpers import TempDbTestCase


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatsClosedTests(TempDbTestCase):
    def _closed(self, title: str, days: int) -> None:
        task_id = store.create_task(self.conn, title=title, project="demo")["id"]
        self.conn.execute(
            "UPDATE tasks SET status = 'done', closed_at = ? WHERE id = ?", (_days_ago(days), task_id))

    def test_closed_by_day_covers_14_days_oldest_first(self) -> None:
        self._closed("сегодня", 0)
        self._closed("сегодня тоже", 0)
        self._closed("три дня назад", 3)
        self._closed("десять дней назад", 10)
        self._closed("месяц назад", 30)

        data = store.stats(self.conn)
        days = data["closed_by_day"]

        self.assertEqual(len(days), 14)
        today = datetime.now(timezone.utc).date()
        self.assertEqual(days[-1]["date"], today.isoformat())
        self.assertEqual(days[0]["date"], (today - timedelta(days=13)).isoformat())
        counts = {day["date"]: day["count"] for day in days}
        self.assertEqual(counts[today.isoformat()], 2)
        self.assertEqual(counts[(today - timedelta(days=3)).isoformat()], 1)
        self.assertEqual(counts[(today - timedelta(days=10)).isoformat()], 1)
        self.assertEqual(sum(counts.values()), 4)
        self.assertEqual(data["closed_7d"], 3)
        self.assertEqual(data["closed_prev_7d"], 1)


if __name__ == "__main__":
    unittest.main()
