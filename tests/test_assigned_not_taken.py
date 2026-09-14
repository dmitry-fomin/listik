"""«Выдана, но не взята» — состояние, отдельное от «взята».

Оркестратор только выдаёт задачу (`stage --holder <кому>`) и не пишет за
исполнителя. Пока сам агент не сделал `claim` (или `heartbeat`) от своего имени,
карточка «выдана, но не взята»; после `board.assign_warn_minutes` она попадает в
«нужен ты», и брошенный прогон видно, не дожидаясь `board.stale_hours`.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events(conn, task_id: str, kind: str | None = None) -> list[dict]:
    sql = "SELECT id, kind, ts, actor, harness, to_value, note FROM events WHERE task_id = ?"
    params: list = [task_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]


class AssignedNotTakenTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="Порция", project="demo")["id"]

    def _issue(self, **kwargs) -> dict:
        """Оркестратор выдаёт задачу исполнителю: `stage --holder dsh`."""
        return store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                                actor=kwargs.pop("actor", "agent:claude"),
                                harness=kwargs.pop("harness", "claude"), **kwargs)

    def _backdate_assignment(self, minutes: int) -> None:
        ts = _ago(minutes)
        self.conn.execute("UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'claim'",
                          (ts, self.task))
        self.conn.execute("UPDATE tasks SET holder_at = ?, updated_at = ? WHERE id = ?",
                          (ts, ts, self.task))
        self.conn.commit()

    def test_stage_holder_is_assigned_not_taken(self) -> None:
        out = self._issue()
        self.assertEqual(out["holder"], "dsh")
        self.assertFalse(out["holder_taken"])
        self.assertTrue(out["not_taken"])
        self.assertFalse(out["not_taken_warn"])
        self.assertEqual(out["holder_assigned_by"], "agent:claude")
        self.assertEqual(out["holder_assigned_by_title"], "Claude")
        self.assertIsNotNone(out["assigned_at"])
        self.assertIsNotNone(out["assigned_hours"])

    def test_reissue_at_same_stage_keeps_holder_assigned(self) -> None:
        """Повторная выдача на том же этапе (перезапуск после красного) не снимает
        держателя: карточка остаётся «выдана, но не взята», пока агент не сделает claim."""
        self._issue()
        out = store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                               actor="agent:claude", harness="claude")
        self.assertEqual(out["holder"], "dsh")
        self.assertTrue(out["not_taken"])
        out = store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        self.assertTrue(out["holder_taken"])

    def test_self_claim_marks_taken(self) -> None:
        self._issue()
        out = store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])
        self.assertFalse(out["not_taken_warn"])
        claims = _events(self.conn, self.task, "claim")
        self.assertEqual(claims[-1]["actor"], "agent:dsh")
        self.assertEqual(claims[-1]["harness"], "dsh")

    def test_claim_without_actor_keeps_old_behaviour(self) -> None:
        """Старый клиент без `--actor` берёт задачу сам: идентичность — держатель."""
        out = store.claim(self.conn, self.task, holder="dsh")
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])

    def test_orchestrator_claiming_for_itself_is_taken(self) -> None:
        out = store.claim(self.conn, self.task, holder="claude",
                          actor="agent:claude", harness="claude")
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])

    def test_orchestrator_claiming_for_other_is_assigned(self) -> None:
        """claim чужой рукой (`--holder dsh` от оркестратора) — это выдача, не взятие."""
        out = store.claim(self.conn, self.task, holder="dsh",
                          actor="agent:claude", harness="claude")
        self.assertFalse(out["holder_taken"])
        self.assertTrue(out["not_taken"])
        self.assertEqual(out["holder_assigned_by"], "agent:claude")

    def test_repeated_claim_is_idempotent_after_self_claim(self) -> None:
        self._issue()
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        before = [e["id"] for e in _events(self.conn, self.task, "claim")]
        out = store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        self.assertTrue(out["holder_taken"])
        self.assertEqual([e["id"] for e in _events(self.conn, self.task, "claim")], before)

    def test_foreign_heartbeat_does_not_confirm(self) -> None:
        """Heartbeat за исполнителя (рукой оркестратора) не делает задачу взятой."""
        self._issue()
        self._backdate_assignment(30)
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:claude",
                        harness="claude", note="слежу за прогоном")
        out = store.get_task(self.conn, self.task)
        self.assertFalse(out["holder_taken"])
        self.assertTrue(out["not_taken"])

    def test_heartbeat_by_holder_confirms(self) -> None:
        self._issue()
        self._backdate_assignment(30)
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:dsh",
                        harness="dsh", note="пишу порцию")
        out = store.get_task(self.conn, self.task)
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])
        heartbeats = _events(self.conn, self.task, "heartbeat")
        self.assertEqual(heartbeats[-1]["actor"], "agent:dsh")

    def test_release_clears_state(self) -> None:
        self._issue()
        out = store.update_task(self.conn, self.task, holder="", actor="agent:dsh")
        self.assertEqual(out["holder"], "")
        self.assertFalse(out["not_taken"])
        self.assertFalse(out["holder_taken"])
        self.assertIsNone(out["assigned_hours"])

    def test_task_without_holder_has_no_state(self) -> None:
        out = store.get_task(self.conn, self.task)
        self.assertFalse(out["not_taken"])
        self.assertFalse(out["holder_taken"])
        self.assertIsNone(out["assigned_at"])

    def test_warn_after_threshold_reaches_needs_you(self) -> None:
        self._issue()
        self._backdate_assignment(40)
        out = store.get_task(self.conn, self.task)
        self.assertTrue(out["not_taken"])
        self.assertTrue(out["not_taken_warn"])
        board = store.board(self.conn, project="demo")
        ids = [t["id"] for t in board["needs_you"]]
        self.assertIn(self.task, ids)
        # Задача без `in_progress` (её никто не брал) лежит в колонке «открыта»;
        # счётчик «выдана, не взята» должен стоять в её же колонке.
        col = next(c for c in board["columns"]
                   if any(t["id"] == self.task for t in c["tasks"]))
        self.assertEqual(col["not_taken"], 1)

    def test_taken_task_does_not_reach_needs_you(self) -> None:
        self._issue()
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        self._backdate_assignment(40)
        board = store.board(self.conn, project="demo")
        self.assertNotIn(self.task, [t["id"] for t in board["needs_you"]])


class AssignedNotTakenCliTests(TempDbTestCase):
    """Видно в текстовом выводе CLI: `show`/`list` зовут это «выдана, не взята»."""

    def setUp(self) -> None:
        super().setUp()
        self.cfg = self.tmp_path / "config.toml"
        self.cfg.write_text("", encoding="utf-8")
        self.task = store.create_task(self.conn, title="Порция", project="demo")["id"]
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")

    def run_cli(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_CONFIG": str(self.cfg),
               "LISTIK_LOG": str(self.tmp_path / "cli.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_show_says_assigned_not_taken(self) -> None:
        p = self.run_cli("show", self.task, "--actor", "agent:claude", "--harness", "claude")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("выдана", p.stdout)
        self.assertIn("не взята", p.stdout)
        self.assertNotIn("держит", p.stdout.split("исполнитель:")[1].split("\n")[0])

    def test_list_shows_flag_in_json(self) -> None:
        p = self.run_cli("list", "--project", "demo", "--json",
                         "--actor", "agent:claude", "--harness", "claude")
        self.assertEqual(p.returncode, 0, p.stderr)
        task = json.loads(p.stdout)["tasks"][0]
        self.assertTrue(task["not_taken"])
        self.assertFalse(task["holder_taken"])


if __name__ == "__main__":
    unittest.main()
