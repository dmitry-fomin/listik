"""Tests for `store.claim` (шаг 04, порция c).

Лок рабочего дерева распространяется только на пишущие задачи (`s3-impl`,
`s4-judge`, или без этапа вовсе — прямая задача «claim -> код -> done»);
держатели на `s1-spec`/`s2-review` дерево не занимают. Красный вердикт на
`s4-judge` возвращает задачу на `s3-impl` (sticky-return) через `next_stage`
(одно событие `stage` с `duration_s`), а окно возврата истекает лениво, только
если держатель молчал после возврата.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from listik import deps as deps_mod
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _events(conn, task_id, kind=None):
    sql = "SELECT * FROM events WHERE task_id = ?"
    params = [task_id]
    if kind is not None:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " ORDER BY id"
    return conn.execute(sql, params).fetchall()


class ClaimBlockerTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="Блокер A", project="demo")["id"]
        self.b = store.create_task(self.conn, title="Задача B", project="demo")["id"]
        store.add_dep(self.conn, self.b, self.a, dep_type="blocks", created_by="автор")
        store.claim(self.conn, self.a, holder="dsh")

    def test_claim_rejects_with_blocker_reason(self) -> None:
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.b, holder="dsh")
        text = str(cm.exception)
        self.assertIn(self.a, text)
        self.assertIn("Блокер A", text)
        self.assertIn("держит", text)
        self.assertIn("Варианты", text)
        self.assertIn("--force", text)

    def test_claim_force_bypasses_blocker_and_logs_it(self) -> None:
        out = store.claim(self.conn, self.b, holder="dsh", force=True)
        self.assertEqual(out["holder"], "dsh")
        notes = [e["note"] or "" for e in _events(self.conn, self.b, "note")]
        self.assertTrue(any("ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ" in n for n in notes))


class ClaimIdempotencyTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="Задача A", project="demo")["id"]

    def test_repeated_claim_refreshes_holder_at_without_new_event(self) -> None:
        store.claim(self.conn, self.a, holder="dsh")
        old_ts = _ago(2)
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (old_ts, self.a))
        self.conn.commit()
        out = store.claim(self.conn, self.a, holder="dsh")
        self.assertEqual(out["holder"], "dsh")
        self.assertLess(store.hours_since(out["holder_at"]), 1 / 60)
        claim_events = _events(self.conn, self.a, "claim")
        self.assertEqual(len(claim_events), 1)
        self.assertEqual(out["status"], "in_progress")
        self.assertEqual(out["assignee"], "dsh")
        self.assertFalse(out["holder_note"])


class ClaimOtherHolderTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="Задача A", project="demo")["id"]
        store.claim(self.conn, self.a, holder="dsh")

    def test_other_holder_gets_release_hint(self) -> None:
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.a, holder="codex")
        text = str(cm.exception)
        self.assertIn("dsh", text)
        self.assertIn("release", text)
        self.assertIn("Варианты", text)

    def test_stale_holder_mentions_abandoned(self) -> None:
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(30), self.a))
        self.conn.commit()
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.a, holder="codex")
        self.assertIn("брошена", str(cm.exception))


class WorktreeLockTests(TempDbTestCase):
    def test_lock_conflict_same_worktree_same_project(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        c = store.create_task(self.conn, title="C", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, a, holder="dsh")
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, c, holder="codex")
        text = str(cm.exception)
        self.assertIn("рабочее дерево", text)
        self.assertIn(a, text)
        self.assertIn("dsh", text)
        self.assertIn("Варианты", text)

    def test_lock_conflict_resolved_by_different_worktree(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        c = store.create_task(self.conn, title="C", project="demo", stage="s3-impl")["id"]
        store.update_task(self.conn, c, worktree="/tmp/wt2")
        store.claim(self.conn, a, holder="dsh")
        out = store.claim(self.conn, c, holder="codex")
        self.assertEqual(out["holder"], "codex")

    def test_lock_conflict_resolved_by_different_project(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        c = store.create_task(self.conn, title="C", project="other", stage="s3-impl")["id"]
        store.claim(self.conn, a, holder="dsh")
        out = store.claim(self.conn, c, holder="codex")
        self.assertEqual(out["holder"], "codex")


class NonWritingStageDoesNotLockTests(TempDbTestCase):
    def test_holder_on_s1_spec_does_not_lock_worktree(self) -> None:
        s = store.create_task(self.conn, title="S", project="demo", stage="s1-spec")["id"]
        p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, s, holder="claude")
        out = store.claim(self.conn, p, holder="dsh")
        self.assertEqual(out["holder"], "dsh")

    def test_holder_on_s2_review_does_not_lock_worktree(self) -> None:
        p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        s2 = store.create_task(self.conn, title="S2", project="demo", stage="s2-review")["id"]
        store.claim(self.conn, p, holder="dsh")
        out = store.claim(self.conn, s2, holder="grok")
        self.assertEqual(out["holder"], "grok")


class DirectTaskLocksTests(TempDbTestCase):
    def test_task_without_stage_locks_and_is_locked(self) -> None:
        d = store.create_task(self.conn, title="D", project="demo")["id"]
        p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        d2 = store.create_task(self.conn, title="D2", project="demo")["id"]
        store.claim(self.conn, d, holder="dsh")
        with self.assertRaises(ValueError):
            store.claim(self.conn, p, holder="codex")
        with self.assertRaises(ValueError):
            store.claim(self.conn, d2, holder="codex")


class SameHolderTwoWritingTasksTests(TempDbTestCase):
    def test_same_holder_can_hold_two_writing_tasks_in_same_worktree(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        b = store.create_task(self.conn, title="B", project="demo", stage="s4-judge")["id"]
        store.claim(self.conn, a, holder="dsh")
        out = store.claim(self.conn, b, holder="dsh")
        self.assertEqual(out["holder"], "dsh")


class RedVerdictTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, self.p, holder="dsh")
        store.next_stage(self.conn, self.p, to_stage="s4-judge")
        self.conn.execute("UPDATE tasks SET stage_at = ? WHERE id = ?", (_ago(2), self.p))
        self.conn.commit()

    def test_red_verdict_returns_to_s3_with_duration(self) -> None:
        store.add_comment(self.conn, self.p, "VERDICT: FAIL\n1. test_x fails", kind="verdict",
                          author="agent:claude")
        task = store.get_task(self.conn, self.p)
        self.assertEqual(task["stage"], "s3-impl")
        self.assertEqual(task["holder"], "dsh")
        stage_events = _events(self.conn, self.p, "stage")
        last = stage_events[-1]
        self.assertEqual(last["from_value"], "s4-judge")
        self.assertEqual(last["to_value"], "s3-impl")
        self.assertIsNotNone(last["duration_s"])
        self.assertGreaterEqual(last["duration_s"], 7000)
        self.assertIn("красного verdict", last["note"] or "")

    def test_parse_verdict_first_line_only(self) -> None:
        self.assertFalse(store.parse_verdict("VERDICT: PASS\nall required checks covered, no fail"))
        self.assertTrue(store.parse_verdict("VERDICT: FAIL\n1. fix x"))
        for bad in ("зелёный: ok", "verdict: pass", "PASS", "", "VERDICT: FAIL", "VERDICT: FAIL\n  "):
            with self.assertRaises(ValueError, msg=bad):
                store.parse_verdict(bad)

    def test_bad_verdict_rejected_and_not_stored(self) -> None:
        with self.assertRaises(ValueError):
            store.add_comment(self.conn, self.p, "красный: тест падает", kind="verdict",
                              author="agent:claude")
        task = store.get_task(self.conn, self.p)
        self.assertEqual(task["stage"], "s4-judge")
        n = self.conn.execute("SELECT COUNT(*) FROM comments WHERE task_id = ? AND kind = 'verdict'",
                              (self.p,)).fetchone()[0]
        self.assertEqual(n, 0)

    def test_green_verdict_does_not_change_stage(self) -> None:
        store.add_comment(self.conn, self.p, "VERDICT: PASS", kind="verdict",
                          author="agent:claude")
        task = store.get_task(self.conn, self.p)
        self.assertEqual(task["stage"], "s4-judge")


class ExpireReturnWindowTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, self.p, holder="dsh")
        store.next_stage(self.conn, self.p, to_stage="s4-judge")
        store.add_comment(self.conn, self.p, "VERDICT: FAIL\n1. test_x fails", kind="verdict",
                          author="agent:claude")
        self.assertEqual(store.get_task(self.conn, self.p)["stage"], "s3-impl")

    def _age_return_event(self, hours: float) -> None:
        self.conn.execute(
            "UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'stage' "
            "AND from_value = 's4-judge' AND to_value = 's3-impl'",
            (_ago(hours), self.p))
        self.conn.commit()

    def test_expires_without_activity(self) -> None:
        self._age_return_event(30)
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(31), self.p))
        self.conn.commit()
        released = deps_mod.expire_return_handoffs(self.conn)
        self.assertEqual(released, 1)
        task = store.get_task(self.conn, self.p)
        self.assertEqual(task["holder"], "")
        release_events = _events(self.conn, self.p, "release")
        self.assertEqual(release_events[-1]["from_value"], "dsh")
        self.assertIn("истёк срок возврата", release_events[-1]["note"] or "")
        ready_ids = [t["id"] for t in deps_mod.ready_tasks(self.conn, project="demo")]
        self.assertIn(self.p, ready_ids)

    def test_activity_after_return_keeps_holder(self) -> None:
        self._age_return_event(30)
        store.heartbeat(self.conn, self.p, holder="dsh")
        released = deps_mod.expire_return_handoffs(self.conn)
        self.assertEqual(released, 0)
        task = store.get_task(self.conn, self.p)
        self.assertEqual(task["holder"], "dsh")

    def test_repeated_claim_extends_window(self) -> None:
        self._age_return_event(30)
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(1), self.p))
        self.conn.commit()
        before_events = _events(self.conn, self.p)
        before_ids = {e["id"] for e in before_events}
        store.claim(self.conn, self.p, holder="dsh")
        after_events = _events(self.conn, self.p)
        new_events = [e for e in after_events if e["id"] not in before_ids]
        self.assertEqual(new_events, [])
        task = store.get_task(self.conn, self.p)
        self.assertLess(store.hours_since(task["holder_at"]), 1 / 60)
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(25), self.p))
        self.conn.commit()
        released = deps_mod.expire_return_handoffs(self.conn)
        self.assertEqual(released, 0)

    def test_stale_holder_at_older_than_return_event_expires_then_claim_takes_it(self) -> None:
        self._age_return_event(30)
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(31), self.p))
        self.conn.commit()
        store.claim(self.conn, self.p, holder="codex")
        events = _events(self.conn, self.p)
        kinds = [e["kind"] for e in events]
        release_idx = len(kinds) - 1 - kinds[::-1].index("release")
        claim_idx = len(kinds) - 1 - kinds[::-1].index("claim")
        self.assertLess(release_idx, claim_idx)


class ProjectReturnWindowTests(TempDbTestCase):
    def test_project_override_shortens_window(self) -> None:
        store.upsert_project(self.conn, "demo")
        store.update_project(self.conn, "demo", routing={"return_window_hours": 1})
        p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, p, holder="dsh")
        store.next_stage(self.conn, p, to_stage="s4-judge")
        store.add_comment(self.conn, p, "VERDICT: FAIL\n1. test_x fails", kind="verdict", author="agent:claude")
        self.conn.execute(
            "UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'stage' "
            "AND from_value = 's4-judge' AND to_value = 's3-impl'",
            (_ago(2), p))
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(3), p))
        self.conn.commit()
        released = deps_mod.expire_return_handoffs(self.conn)
        self.assertEqual(released, 1)

    def test_default_window_does_not_expire_at_two_hours(self) -> None:
        p = store.create_task(self.conn, title="P2", project="demo2", stage="s3-impl")["id"]
        store.claim(self.conn, p, holder="dsh")
        store.next_stage(self.conn, p, to_stage="s4-judge")
        store.add_comment(self.conn, p, "VERDICT: FAIL\n1. test_x fails", kind="verdict", author="agent:claude")
        self.conn.execute(
            "UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'stage' "
            "AND from_value = 's4-judge' AND to_value = 's3-impl'",
            (_ago(2), p))
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(3), p))
        self.conn.commit()
        released = deps_mod.expire_return_handoffs(self.conn)
        self.assertEqual(released, 0)


class ClaimAfterExpiredWindowTests(TempDbTestCase):
    def test_claim_takes_over_after_expired_window(self) -> None:
        p = store.create_task(self.conn, title="P", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, p, holder="dsh")
        store.next_stage(self.conn, p, to_stage="s4-judge")
        store.add_comment(self.conn, p, "VERDICT: FAIL\n1. test_x fails", kind="verdict", author="agent:claude")
        self.conn.execute(
            "UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'stage' "
            "AND from_value = 's4-judge' AND to_value = 's3-impl'",
            (_ago(30), p))
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(31), p))
        self.conn.commit()
        out = store.claim(self.conn, p, holder="codex")
        self.assertEqual(out["holder"], "codex")
        kinds = [e["kind"] for e in _events(self.conn, p)]
        self.assertIn("release", kinds)
        self.assertIn("claim", kinds)


class ReadyWorktreeBusyTests(TempDbTestCase):
    def test_ready_reports_worktree_busy(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        c = store.create_task(self.conn, title="C", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, a, holder="dsh")
        state = deps_mod.ready(self.conn, c)
        self.assertEqual(state["worktree_busy"]["id"], a)
        self.assertTrue(any("дерево занято" in r for r in state["reasons"]))
        self.assertTrue(state["ready"])

    def test_ready_worktree_free(self) -> None:
        c = store.create_task(self.conn, title="C", project="demo", stage="s3-impl")["id"]
        state = deps_mod.ready(self.conn, c)
        self.assertIsNone(state["worktree_busy"])

    def test_ready_worktree_busy_none_for_non_writing_stage(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        s = store.create_task(self.conn, title="S", project="demo", stage="s1-spec")["id"]
        store.claim(self.conn, a, holder="dsh")
        state = deps_mod.ready(self.conn, s)
        self.assertIsNone(state["worktree_busy"])


class ClaimCliTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        cwd = str(LISTIK_BIN.parent.parent)
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=cwd,
        )

    def test_cli_claim_conflict_and_recovery(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        c = store.create_task(self.conn, title="C", project="demo", stage="s3-impl")["id"]
        store.claim(self.conn, a, holder="dsh")

        p = self._run("claim", c, "--holder", "codex")
        self.assertEqual(p.returncode, 1)
        self.assertIn("ошибка:", p.stderr)
        self.assertIn("рабочее дерево", p.stderr)
        self.assertNotIn("Traceback", p.stderr)

        p2 = self._run("claim", a, "--holder", "dsh")
        self.assertEqual(p2.returncode, 0)

        p3 = self._run("show", c, "--json")
        self.assertEqual(p3.returncode, 0)
        task = json.loads(p3.stdout)
        self.assertIn("worktree_busy", task["deps_state"])


if __name__ == "__main__":
    unittest.main()
