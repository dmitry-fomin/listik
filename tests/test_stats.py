"""Tests for `store.stats`: темп закрытия для страницы «Метрики»."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from listik import paths, store
from tests.helpers import TempDbTestCase


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hours_ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


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


class StatsThresholdTests(TempDbTestCase):
    """`stale`/`long_stage` в stats — из флагов карточек и порогов `[board]` config."""

    def _config(self, board: str | None) -> None:
        cfg = self.tmp_path / "config.toml"
        cfg.write_text(f"[board]\n{board}\n" if board is not None else "", encoding="utf-8")
        patch = mock.patch.object(paths, "CONFIG_PATH", cfg)
        patch.start()
        self.addCleanup(patch.stop)

    def _task(self, *, status: str, holder: str | None = None, holder_at: str | None = None,
              started_at: str | None = None, stage_at: str | None = None) -> str:
        task_id = store.create_task(self.conn, title=f"{status} {holder}", project="demo")["id"]
        self.conn.execute(
            "UPDATE tasks SET holder=?, holder_at=?, started_at=?, stage_at=?, status=? WHERE id=?",
            (holder, holder_at, started_at, stage_at, status, task_id))
        return task_id

    def _cards(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE archived = 0 AND status IN ('in_progress','review')")
        return [store.row_to_task(self.conn, r) for r in rows]

    def test_orphan_is_not_stale(self) -> None:
        self._config("stale_hours = 24")
        self._task(status="in_progress", started_at=_days_ago(3))
        data = store.stats(self.conn)
        self.assertEqual(data["stale"], 0)
        [card] = self._cards()
        self.assertTrue(card["abandoned"])
        self.assertFalse(card["stale"])

    def test_stale_follows_stale_hours(self) -> None:
        self._task(status="in_progress", holder="agent:x", holder_at=_hours_ago(5),
                   started_at=_hours_ago(5))
        for hours, expected in ((24, 0), (4, 1)):
            with self.subTest(stale_hours=hours):
                self._config(f"stale_hours = {hours}")
                data = store.stats(self.conn)
                self.assertEqual(data["stale"], expected)
                self.assertEqual(data["stale"], sum(1 for c in self._cards() if c["stale"]))

    def test_long_stage_follows_wip_warn_hours(self) -> None:
        self._task(status="review", holder="agent:x", holder_at=_hours_ago(1),
                   stage_at=_hours_ago(3))
        for hours, expected in ((8, 0), (2, 1)):
            with self.subTest(wip_warn_hours=hours):
                self._config(f"wip_warn_hours = {hours}")
                data = store.stats(self.conn)
                self.assertEqual(data["long_stage"], expected)
                self.assertEqual(data["long_stage"],
                                 sum(1 for c in self._cards() if c["stage_warn"]))

    def test_thresholds_in_response(self) -> None:
        self._config("stale_hours = 12\nwip_warn_hours = 3")
        data = store.stats(self.conn)
        self.assertEqual((data["stale_hours"], data["wip_warn_hours"]), (12.0, 3.0))
        self.assertIsInstance(data["stale_hours"], float)
        self._config(None)
        data = store.stats(self.conn)
        self.assertEqual((data["stale_hours"], data["wip_warn_hours"]), (24.0, 8.0))
        self.assertIsInstance(data["wip_warn_hours"], float)

    def test_running_only_in_progress(self) -> None:
        self._config(None)
        wip = self._task(status="in_progress", holder="agent:x", holder_at=_hours_ago(1),
                         stage_at=_hours_ago(1))
        self._task(status="review", holder="agent:y", holder_at=_hours_ago(1),
                   stage_at=_hours_ago(2))
        data = store.stats(self.conn)
        self.assertEqual([t["id"] for t in data["running"]], [wip])


if __name__ == "__main__":
    unittest.main()
