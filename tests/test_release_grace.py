"""Льготное окно «в работе без держателя» после штатного release.

Держателя снимают не только брошенные прогоны: handoff без `--holder`, `listik
release` и истечение окна возврата после красного вердикта делают это штатно.
Такой карточке даётся та же фора `board.assign_warn_minutes` (15 мин), что и
выданной, но не взятой: пока фора идёт, карточка не `abandoned` и не попадает в
«нужен ты». Карточка без единого события `release` (импорт, ручной `set status`)
брошена сразу, как и раньше.

Конфиг тесты не подменяют — работают на умолчании `assign_warn_minutes = 15`.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from listik import deps as deps_mod
from listik import store
from tests.helpers import TempDbTestCase


def _ago(minutes: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReleaseGraceTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="Порция", project="demo")["id"]

    # ------------------------------------------------------------ помощники

    def _handoff(self, task_id: str | None = None) -> None:
        """Штатный handoff s2→s3 без `--holder`: держатель снят, событие release."""
        tid = task_id or self.task
        store.claim(self.conn, tid, holder="claude", actor="agent:claude", harness="claude")
        store.next_stage(self.conn, tid, to_stage="s2-review",
                         actor="agent:claude", harness="claude")
        store.next_stage(self.conn, tid, to_stage="s3-impl",
                         actor="agent:claude", harness="claude")

    def _backdate_release(self, minutes: float, task_id: str | None = None) -> str:
        ts = _ago(minutes)
        self.conn.execute("UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'release'",
                          (ts, task_id or self.task))
        self.conn.commit()
        return ts

    def _release_ts(self, task_id: str | None = None) -> str:
        row = self.conn.execute(
            "SELECT ts FROM events WHERE task_id = ? AND kind = 'release' "
            "ORDER BY ts DESC, id DESC LIMIT 1", (task_id or self.task,)).fetchone()
        self.assertIsNotNone(row, "событие release не записано")
        return row["ts"]

    def _needs_you_ids(self) -> list[str]:
        return [t["id"] for t in store.board(self.conn, project="demo")["needs_you"]]

    # ------------------------------------------------------------ сценарии

    def test_handoff_without_holder_is_not_abandoned_yet(self) -> None:
        """B1: handoff без `--holder` — карточка не брошена сразу."""
        self._handoff()
        out = store.get_task(self.conn, self.task)
        self.assertEqual(out["status"], "in_progress")
        self.assertEqual(out["holder"], "")
        self.assertFalse(out["abandoned"])
        self.assertEqual(out["released_at"], self._release_ts())
        self.assertTrue(out["released_at"])
        # В ленту карточку ведёт только lint (`in_progress_no_holder`), не «брошена».
        card = next((t for t in store.board(self.conn, project="demo")["needs_you"]
                     if t["id"] == self.task), None)
        self.assertIsNotNone(card)
        self.assertFalse(card["abandoned"] or card["stale"] or card["needs_owner"]
                         or card["not_taken_warn"])
        self.assertIn("in_progress_no_holder", card["lint"])

    def test_release_command_is_not_abandoned_yet(self) -> None:
        """B2: `release` командой — карточка не брошена сразу."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.update_task(self.conn, self.task, holder="", actor="agent:claude")
        out = store.get_task(self.conn, self.task)
        self.assertFalse(out["abandoned"])
        self.assertTrue(out["released_at"])
        self.assertFalse(out["stale"])
        self.assertFalse(out["not_taken"])

    def test_window_passed_is_abandoned(self) -> None:
        """B3: окно прошло — карточка брошена, как раньше."""
        self._handoff()
        ts = self._backdate_release(16)
        out = store.get_task(self.conn, self.task)
        self.assertTrue(out["abandoned"])
        self.assertEqual(out["released_at"], ts)
        self.assertIn(self.task, self._needs_you_ids())

    def test_threshold_14_in_window_16_out(self) -> None:
        """B4: 14 минут — ещё в окне, 16 — уже нет."""
        self._handoff()
        self._backdate_release(14)
        self.assertFalse(store.get_task(self.conn, self.task)["abandoned"])
        self._backdate_release(16)
        self.assertTrue(store.get_task(self.conn, self.task)["abandoned"])

    def test_last_release_wins(self) -> None:
        """B5: окно считается от последнего release, а не от первого."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.update_task(self.conn, self.task, holder="", actor="agent:claude")
        old_ts = self._backdate_release(60)
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.update_task(self.conn, self.task, holder="", actor="agent:claude")
        fresh_ts = self._release_ts()
        self.assertNotEqual(fresh_ts, old_ts)
        out = store.get_task(self.conn, self.task)
        self.assertFalse(out["abandoned"])
        self.assertEqual(out["released_at"], fresh_ts)

    def test_in_progress_without_any_release_is_abandoned(self) -> None:
        """B6 (регрессионный): статус руками, держателя не было — брошена сразу."""
        store.update_task(self.conn, self.task, status="in_progress", actor="agent:claude")
        out = store.get_task(self.conn, self.task)
        self.assertFalse(out["holder"])
        self.assertTrue(out["abandoned"])
        self.assertIsNone(out.get("released_at"))

    def test_window_does_not_leak_between_tasks(self) -> None:
        """B7: чужой release не гасит `abandoned` — запрос фильтрует по task_id."""
        other = store.create_task(self.conn, title="Вторая", project="demo")["id"]
        self._handoff()
        store.update_task(self.conn, other, status="in_progress", actor="agent:claude")
        a = store.get_task(self.conn, self.task)
        b = store.get_task(self.conn, other)
        self.assertFalse(a["abandoned"])
        self.assertTrue(b["abandoned"])
        self.assertIsNone(b.get("released_at"))

    def test_window_does_not_cover_missing_heartbeat(self) -> None:
        """B8 (регрессионный): держатель без heartbeat брошен независимо от окна."""
        store.update_task(self.conn, self.task, status="in_progress", actor="agent:claude")
        self.conn.execute("UPDATE tasks SET holder = 'dsh', holder_at = NULL WHERE id = ?",
                          (self.task,))
        self.conn.commit()
        store.event(self.conn, self.task, "release", from_value="x", to_value="")
        self.conn.commit()
        out = store.get_task(self.conn, self.task)
        self.assertTrue(out["abandoned"])
        self.assertIsNone(out.get("released_at"))

    def test_released_at_empty_for_held_and_closed(self) -> None:
        """B9: у карточки с держателем и у закрытой `released_at` пуст."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        out = store.get_task(self.conn, self.task)
        self.assertIsNone(out["released_at"])
        store.update_task(self.conn, self.task, status="done", stage="done", holder="",
                          actor="agent:claude")
        out = store.get_task(self.conn, self.task)
        self.assertFalse(out["abandoned"])
        self.assertIsNone(out["released_at"])

    def test_expired_return_window_gets_grace(self) -> None:
        """B10: истечение окна возврата — тоже release, и у него есть фора."""
        p = store.create_task(self.conn, title="Возврат", project="demo",
                              stage="s3-impl")["id"]
        store.claim(self.conn, p, holder="dsh")
        store.next_stage(self.conn, p, to_stage="s4-judge")
        store.add_comment(self.conn, p, "VERDICT: FAIL\n1. test_x fails", kind="verdict",
                          author="agent:claude")
        self.assertEqual(store.get_task(self.conn, p)["stage"], "s3-impl")
        self.conn.execute(
            "UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'stage' "
            "AND from_value = 's4-judge' AND to_value = 's3-impl'",
            (_ago(30 * 60), p))
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(31 * 60), p))
        self.conn.commit()
        self.assertEqual(deps_mod.expire_return_handoffs(self.conn, task_id=p), 1)
        out = store.get_task(self.conn, p)
        self.assertEqual(out["holder"], "")
        self.assertFalse(out["abandoned"])
        self.assertEqual(out["released_at"], self._release_ts(p))

    def test_unparsable_release_ts_expires_window(self) -> None:
        """B11: метку release не разобрать — окно истекло, карточка не падает."""
        self._handoff()
        self.conn.execute("UPDATE events SET ts = 'мусор' WHERE task_id = ? AND kind = 'release'",
                          (self.task,))
        self.conn.commit()
        out = store.get_task(self.conn, self.task)
        self.assertTrue(out["abandoned"])
        self.assertEqual(out["released_at"], "мусор")

    def test_released_at_in_list_and_board(self) -> None:
        """B12: `released_at` виден в списке и на доске, но не в `card_link`."""
        self._handoff()
        expected = store.get_task(self.conn, self.task)["released_at"]
        listed = [t for t in store.list_tasks(self.conn, project="demo")["tasks"]
                  if t["id"] == self.task]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["released_at"], expected)
        on_board = [t for col in store.board(self.conn, project="demo")["columns"]
                    for t in col["tasks"] if t["id"] == self.task]
        self.assertEqual(len(on_board), 1)
        self.assertEqual(on_board[0]["released_at"], expected)
        self.assertNotIn("released_at", store.card_link(self.conn, self.task))


if __name__ == "__main__":
    unittest.main()
