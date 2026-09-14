"""Простой карточки с учётом открытых прямых детей (`row_to_task`).

Эпик с открытыми порциями не должен выглядеть «молчащим»: пока двигается
ребёнок, `idle_hours`/`idle_age` считаются от его свежей метки, а `stale`
не срабатывает. Внуки и другие типы связей не учитываются, а карточка без
собственной метки простоя детей не слушает вовсе.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from listik import store

from tests.helpers import TempDbTestCase


def _ago(hours: float) -> str:
    """Метка времени в прошлом в том же формате, что пишет `store.now_iso`."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class IdleChildrenTestCase(TempDbTestCase):
    """Общая обвязка: карточка E (`in_progress`, heartbeat 2 ч назад)."""

    def setUp(self) -> None:
        super().setUp()
        self._cfg = mock.patch.object(
            store.config_mod, "load", return_value={"board": {"stale_hours": 24}})
        self._cfg.start()
        self.addCleanup(self._cfg.stop)
        self.epic = store.create_task(self.conn, title="E", project="demo")["id"]
        self._set_task(self.epic, status="in_progress", holder="agent:dsh", holder_at=_ago(2),
                       started_at=_ago(2))

    def _set_task(self, task_id: str, **fields) -> None:
        sets = ", ".join(f"{name} = ?" for name in fields)
        self.conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?",
                          (*fields.values(), task_id))
        self.conn.commit()

    def _child(self, *, status: str = "in_progress", holder_at: str | None = None,
               started_at: str | None = None, parent: str | None = None) -> str:
        """Ребёнок карточки `parent` (по умолчанию — E) со своими метками."""
        child = store.create_task(self.conn, title="C", project="demo")["id"]
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type) VALUES(?,?,?)",
            (child, parent or self.epic, "parent-child"))
        self._set_task(child, status=status, holder=(holder_at and "agent:dsh"),
                       holder_at=holder_at, started_at=started_at)
        return child

    def _link(self, task_id: str, other: str, dep_type: str) -> None:
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type) VALUES(?,?,?)",
            (task_id, other, dep_type))
        self.conn.commit()

    def task(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)


class ChildActivityTests(IdleChildrenTestCase):
    def test_s1_open_child_with_fresh_holder_moves_idle(self) -> None:
        before = self.task(self.epic)
        self._child(status="in_progress", holder_at=_ago(1 / 60.0))
        after = self.task(self.epic)

        self.assertLess(after["idle_hours"], 0.25)
        self.assertTrue(after["idle_age"].endswith(" мин"), after["idle_age"])
        self.assertAlmostEqual(after["holder_hours"], 2, delta=0.05)
        self.assertEqual(after["holder_age"], "2 ч")
        for field in ("abandoned", "not_taken", "not_taken_warn", "holder_taken"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["holder_at"], before["holder_at"])

    def test_s2_done_child_ignored(self) -> None:
        self._child(status="done", holder_at=_ago(1 / 60.0))
        self.assertAlmostEqual(self.task(self.epic)["idle_hours"], 2, delta=0.05)

    def test_s3_open_child_without_labels_ignored(self) -> None:
        self._child(status="open")
        self.assertAlmostEqual(self.task(self.epic)["idle_hours"], 2, delta=0.05)

    def test_s4_open_child_started_at_counts(self) -> None:
        self._child(status="open", started_at=_ago(1 / 60.0))
        self.assertLess(self.task(self.epic)["idle_hours"], 0.25)

    def test_s5_other_link_type_ignored(self) -> None:
        other = store.create_task(self.conn, title="R", project="demo")["id"]
        self._set_task(other, status="in_progress", holder="agent:dsh", holder_at=_ago(1 / 60.0))
        self._link(other, self.epic, "relates-to")
        self.assertAlmostEqual(self.task(self.epic)["idle_hours"], 2, delta=0.05)

    def test_s6_fresh_child_unstales_card(self) -> None:
        self._set_task(self.epic, holder_at=_ago(30))
        self.assertIs(self.task(self.epic)["stale"], True)
        self._child(status="in_progress", holder_at=_ago(1 / 60.0))
        self.assertIs(self.task(self.epic)["stale"], False)

    def test_s7_open_card_without_own_label_ignores_children(self) -> None:
        card = store.create_task(self.conn, title="O", project="demo")["id"]
        self._child(status="in_progress", holder_at=_ago(1 / 60.0), parent=card)
        out = self.task(card)
        self.assertIsNone(out["idle_hours"])
        self.assertEqual(out["idle_age"], store.human_age(out["updated_at"]))

    def test_s8_stale_child_label_ignored(self) -> None:
        self._child(status="in_progress", holder_at=_ago(5))
        self.assertAlmostEqual(self.task(self.epic)["idle_hours"], 2, delta=0.05)

    def test_s9_grandchildren_ignored(self) -> None:
        child = self._child(status="done", holder_at=_ago(5))
        grandchild = store.create_task(self.conn, title="G", project="demo")["id"]
        self._set_task(grandchild, status="in_progress", holder="agent:dsh", holder_at=_ago(1 / 60.0))
        self._link(grandchild, child, "parent-child")
        self.assertAlmostEqual(self.task(self.epic)["idle_hours"], 2, delta=0.05)

    def test_s10_closed_card_ignores_children(self) -> None:
        self._set_task(self.epic, status="done")
        self._child(status="in_progress", holder_at=_ago(1 / 60.0))
        self.assertAlmostEqual(self.task(self.epic)["idle_hours"], 2, delta=0.05)

    def test_s11_without_children_unchanged(self) -> None:
        out = self.task(self.epic)
        self.assertAlmostEqual(out["idle_hours"], out["holder_hours"], delta=0.05)
        self.assertEqual(out["idle_age"], store.human_age(out["holder_at"]))


if __name__ == "__main__":
    unittest.main()
