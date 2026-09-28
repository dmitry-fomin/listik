"""Пороги `[board]` параметром `row_to_task` (listik-i5i0, порция b).

Циклы по карточкам читают config.toml один раз, а не на каждую карточку:
число вызовов `config_mod.load` не растёт с числом карточек.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from listik import deps, store

from tests.helpers import TempDbTestCase


def _ago(hours: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class RowToTaskBoardParamTest(TempDbTestCase):
    def test_passed_board_skips_config_load(self) -> None:
        tid = store.create_task(self.conn, title="T", project="demo")["id"]
        self.conn.execute("UPDATE tasks SET status='in_progress', holder='agent:x', "
                          "holder_at=?, stage='s3-impl', stage_at=? WHERE id=?",
                          (_ago(0.1), _ago(2), tid))
        row = self.conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        with mock.patch.object(store.config_mod, "load") as load:
            task = store.row_to_task(self.conn, row,
                                     board={"stale_hours": 1, "wip_warn_hours": 1})
        self.assertEqual(load.call_count, 0)
        self.assertTrue(task["stage_warn"])


class LoopLoadsConfigOnceTest(TempDbTestCase):
    def _make(self, n: int) -> None:
        for i in range(n):
            tid = store.create_task(self.conn, title=f"T{i}", project="demo")["id"]
            self.conn.execute("UPDATE tasks SET status='in_progress' WHERE id=?", (tid,))
            blocker = store.create_task(self.conn, title=f"B{i}", project="other")["id"]
            store.add_dep(self.conn, tid, blocker, "blocks", confirm=True)
            store.create_task(self.conn, title=f"F{i}", project="free")
            # Ребёнок заблокированной карточки: `blocked_tasks` считает его в `children_open`.
            store.create_task(self.conn, title=f"C{i}", project="kids", parent=tid)
        self.conn.commit()

    def _calls(self, fn) -> int:
        real = store.config_mod.load
        with mock.patch.object(store.config_mod, "load", wraps=real) as load:
            fn()
        return load.call_count

    def _check(self, fn) -> None:
        self._make(1)
        one = self._calls(fn)
        self._make(4)
        five = self._calls(fn)
        self.assertEqual(one, five)

    def test_list_tasks(self) -> None:
        self._check(lambda: store.list_tasks(self.conn, project="demo"))

    def test_board(self) -> None:
        self._check(lambda: store.board(self.conn, project="demo"))

    def test_stats(self) -> None:
        self._check(lambda: store.stats(self.conn, project="demo"))

    def test_ready_tasks(self) -> None:
        self._check(lambda: self.assertTrue(deps.ready_tasks(self.conn, project="free")))

    def test_ready_tasks_with_waiting(self) -> None:
        # Блокеры из other свободны, и у каждого есть ждущая карточка (`waiting_for`).
        self._check(lambda: self.assertTrue(deps.ready_tasks(self.conn, project="other")))

    def test_blocked_tasks(self) -> None:
        self._check(lambda: self.assertTrue(deps.blocked_tasks(self.conn, project="demo")))


if __name__ == "__main__":
    unittest.main()
