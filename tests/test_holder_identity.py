"""Тот же держатель — это тот же актор, а не та же строка.

`claude`, `agent:claude`, `sonnet-judge` — один держатель; `dsh`, `agent:dsh`,
`dsh/deepseek-flash` — один. До этого шага Listik сравнивал держателя сырой
строкой, поэтому один агент под двумя написаниями выглядел как два разных: claim
одним написанием и heartbeat другим давали «выдана, но не взята», повторный claim
падал с «уже удерживается», лок рабочего дерева не пускал судью к своей же задаче,
а повторная выдача стирала заметку.

Тождество берётся ровно из `actors.resolve` (`actors.same_actor`) — ничего
нечёткого: `alice` и `alicia` по-прежнему разные акторы, пустой держатель не
тождествен ничему.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from listik import actors, deps, store
from tests.helpers import TempDbTestCase


def _events(conn, task_id: str, kind: str | None = None) -> list[dict]:
    sql = ("SELECT id, kind, ts, actor, harness, from_value, to_value, note FROM events "
           "WHERE task_id = ?")
    params: list = [task_id]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]


def _holder(conn, task_id: str) -> str | None:
    return conn.execute("SELECT holder FROM tasks WHERE id = ?", (task_id,)).fetchone()["holder"]


def _holder_at(conn, task_id: str) -> str | None:
    return conn.execute("SELECT holder_at FROM tasks WHERE id = ?", (task_id,)).fetchone()["holder_at"]


def _ago(minutes: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class SameActorFunctionTests(TempDbTestCase):
    """R1: правило тождества — одно, и оно не шире `actors.resolve`."""

    def test_empty_holder_is_not_the_same_as_anything(self) -> None:
        self.assertFalse(actors.same_actor("", ""))
        self.assertFalse(actors.same_actor(None, ""))
        self.assertFalse(actors.same_actor("dsh", None))
        self.assertFalse(actors.same_actor(" ", ""))

    def test_spellings_of_one_actor_are_the_same(self) -> None:
        self.assertTrue(actors.same_actor("claude", "agent:claude"))
        self.assertTrue(actors.same_actor("Bob ", "bob"))
        self.assertTrue(actors.same_actor("sonnet-judge", "claude"))
        self.assertTrue(actors.same_actor("dsh/deepseek-flash", "agent:dsh"))

    def test_different_names_are_not_glued(self) -> None:
        self.assertFalse(actors.same_actor("alice", "alicia"))
        self.assertFalse(actors.same_actor("dsh", "grok"))
        self.assertFalse(actors.same_actor("claude", "codex"))


class HolderIdentityTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task = store.create_task(self.conn, title="Порция", project="demo")["id"]

    # ---------------------------------------------------------------- heartbeat

    def test_heartbeat_in_other_spelling_keeps_card_taken(self) -> None:
        """Случай listik-yf5z: claim `claude`, heartbeat `agent:claude`."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.heartbeat(self.conn, self.task, holder="agent:claude", actor="agent:claude",
                        harness="claude")
        out = store.get_task(self.conn, self.task)
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])
        self.assertEqual(out["holder_assigned_by"], "agent:claude")
        # Хранимое написание держателя не меняется от тождественного heartbeat.
        self.assertEqual(_holder(self.conn, self.task), "claude")
        beats = _events(self.conn, self.task, "heartbeat")
        for beat in beats:  # событие могло быть затроттлено — карточка уже взята
            self.assertIsNone(beat["from_value"])
            self.assertEqual(beat["to_value"], "claude")

    def test_same_actor_heartbeat_keeps_note_and_spelling(self) -> None:
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh")
        store.heartbeat(self.conn, self.task, holder="dsh", note="пишу порцию",
                        actor="agent:dsh")
        store.heartbeat(self.conn, self.task, holder="agent:dsh", actor="agent:dsh")
        out = store.get_task(self.conn, self.task)
        self.assertEqual(out["holder_note"], "пишу порцию")
        self.assertEqual(_holder(self.conn, self.task), "dsh")

    def test_holder_own_heartbeat_takeover_is_assignment(self) -> None:
        """Держатель забрал карточку у другого актора своей рукой — это назначение."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:dsh", harness="dsh")
        out = store.get_task(self.conn, self.task)
        self.assertEqual(_holder(self.conn, self.task), "dsh")
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])
        self.assertEqual(out["holder_assigned_by"], "agent:dsh")
        beats = [b for b in _events(self.conn, self.task, "heartbeat")
                 if b["from_value"] == "claude"]
        self.assertEqual(len(beats), 1)
        self.assertEqual(out["assigned_at"], beats[0]["ts"])

    def test_foreign_hand_heartbeat_neither_assigns_nor_confirms(self) -> None:
        """Регрессионный: оркестратор переставил держателя за харнесс."""
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:claude",
                        harness="claude")
        out = store.get_task(self.conn, self.task)
        self.assertEqual(_holder(self.conn, self.task), "dsh")
        self.assertFalse(out["holder_taken"])
        self.assertTrue(out["not_taken"])
        self.assertIsNone(out["holder_assigned_by"])

    def test_heartbeat_on_unheld_card_is_not_assignment(self) -> None:
        """Регрессионный: heartbeat на карточку без держателя — не назначение."""
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:dsh")
        out = store.get_task(self.conn, self.task)
        self.assertEqual(_holder(self.conn, self.task), "dsh")
        self.assertFalse(out["holder_taken"])
        self.assertTrue(out["not_taken"])
        self.assertIsNone(out["holder_assigned_by"])

    def test_plain_heartbeat_does_not_reanchor_assignment(self) -> None:
        """Регрессионный: обычный heartbeat держателя «кто выдал» не переставляет."""
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh")
        before = store.get_task(self.conn, self.task)
        store.heartbeat(self.conn, self.task, holder="dsh", actor="agent:dsh", note="работаю")
        after = store.get_task(self.conn, self.task)
        self.assertTrue(after["holder_taken"])
        self.assertEqual(after["holder_assigned_by"], before["holder_assigned_by"])
        self.assertEqual(after["assigned_at"], before["assigned_at"])

    def test_heartbeat_in_other_spelling_does_not_reanchor_assignment(self) -> None:
        """То же, но heartbeat под другим написанием: «взята» не теряется."""
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh")
        before = store.get_task(self.conn, self.task)
        store.heartbeat(self.conn, self.task, holder="agent:dsh", actor="agent:dsh")
        after = store.get_task(self.conn, self.task)
        self.assertTrue(after["holder_taken"])
        self.assertFalse(after["not_taken"])
        self.assertEqual(after["holder_assigned_by"], before["holder_assigned_by"])
        self.assertEqual(after["assigned_at"], before["assigned_at"])

    def test_latest_assignment_is_chosen_by_ts_not_id(self) -> None:
        """Регрессионный: «последнее назначение» — по `ts`, потом по `id`."""
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:grok")
        claims = _events(self.conn, self.task, "claim")
        self.assertEqual(len(claims), 2)
        later = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.conn.execute("UPDATE events SET ts = ? WHERE id = ?", (later, claims[0]["id"]))
        self.conn.commit()
        out = store.get_task(self.conn, self.task)
        self.assertEqual(out["holder_assigned_by"], "agent:claude")
        self.assertEqual(out["assigned_at"], later)

    # -------------------------------------------------------------------- claim

    def test_claim_in_other_spelling_is_idempotent(self) -> None:
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        before = _events(self.conn, self.task, "claim")
        store.claim(self.conn, self.task, holder="agent:dsh", actor="agent:dsh", harness="dsh")
        out = store.get_task(self.conn, self.task)
        self.assertEqual(_holder(self.conn, self.task), "dsh")
        self.assertTrue(out["holder_taken"])
        self.assertFalse(out["not_taken"])
        claims = _events(self.conn, self.task, "claim")
        self.assertEqual(len(claims), len(before) + 1)
        self.assertEqual(claims[-1]["actor"], "agent:dsh")
        self.assertEqual(claims[-1]["to_value"], "dsh")

    def test_repeated_same_actor_claim_adds_no_events(self) -> None:
        store.next_stage(self.conn, self.task, to_stage="s3-impl", holder="dsh",
                         actor="agent:claude", harness="claude")
        store.claim(self.conn, self.task, holder="agent:dsh", actor="agent:dsh", harness="dsh")
        claims = _events(self.conn, self.task, "claim")
        stale = _ago(30)
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (stale, self.task))
        self.conn.commit()
        store.claim(self.conn, self.task, holder="agent:dsh", actor="agent:dsh", harness="dsh")
        self.assertEqual(_events(self.conn, self.task, "claim"), claims)
        self.assertNotEqual(_holder_at(self.conn, self.task), stale)
        self.assertEqual(_holder(self.conn, self.task), "dsh")

    def test_substring_alias_claim_is_idempotent(self) -> None:
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        store.claim(self.conn, self.task, holder="sonnet-judge", actor="agent:claude")
        self.assertEqual(_holder(self.conn, self.task), "claude")

    def test_other_actor_claim_is_refused(self) -> None:
        """Регрессионный: имена не склеиваются, чужой claim не проходит."""
        store.claim(self.conn, self.task, holder="alice")
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.task, holder="alicia")
        self.assertIn("уже удерживается", str(cm.exception))
        self.assertEqual(_holder(self.conn, self.task), "alice")

    def test_other_agent_claim_is_refused(self) -> None:
        store.claim(self.conn, self.task, holder="dsh", actor="agent:dsh")
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.task, holder="grok", actor="agent:grok")
        self.assertIn("уже удерживается", str(cm.exception))
        self.assertEqual(_holder(self.conn, self.task), "dsh")

    def test_codex_claim_on_claude_card_is_refused(self) -> None:
        store.claim(self.conn, self.task, holder="claude", actor="agent:claude",
                    harness="claude")
        claims = _events(self.conn, self.task, "claim")
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.task, holder="codex", actor="agent:codex")
        self.assertIn("уже удерживается", str(cm.exception))
        self.assertEqual(_holder(self.conn, self.task), "claude")
        self.assertEqual(_events(self.conn, self.task, "claim"), claims)

    # ----------------------------------------------------------- stage_unchanged

    def test_reissue_to_same_actor_keeps_note(self) -> None:
        task = store.create_task(self.conn, title="Порция", project="demo",
                                 stage="s3-impl")["id"]
        store.claim(self.conn, task, holder="dsh", actor="agent:dsh")
        store.heartbeat(self.conn, task, holder="dsh", note="пишу порцию", actor="agent:dsh")
        store.next_stage(self.conn, task, to_stage="s3-impl", holder="agent:dsh",
                         actor="agent:claude", harness="claude")
        out = store.get_task(self.conn, task)
        self.assertEqual(out["holder_note"], "пишу порцию")
        self.assertEqual(_holder(self.conn, task), "agent:dsh")
        # listik-udop: новая выдача переанкоривает «взята» — карточка снова «выдана».
        self.assertTrue(out["not_taken"])
        claims = _events(self.conn, task, "claim")
        self.assertEqual(claims[-1]["actor"], "agent:claude")


class WorktreeLockIdentityTests(TempDbTestCase):
    """Лок рабочего дерева: тот же актор — не конфликт, другой — конфликт."""

    def setUp(self) -> None:
        super().setUp()
        self.a = store.create_task(self.conn, title="Реализация", project="demo",
                                   stage="s3-impl")["id"]
        self.b = store.create_task(self.conn, title="Суд", project="demo",
                                   stage="s4-judge")["id"]

    def test_same_actor_holds_two_writing_tasks_in_one_worktree(self) -> None:
        store.claim(self.conn, self.a, holder="grok", actor="agent:grok")
        store.claim(self.conn, self.b, holder="agent:grok", actor="agent:grok")
        self.assertEqual(_holder(self.conn, self.b), "agent:grok")

    def test_lock_holds_for_other_actor_and_for_no_holder(self) -> None:
        """Регрессионный: лок не ослаблен ни для чужого актора, ни при `holder=None`."""
        store.claim(self.conn, self.a, holder="dsh", actor="agent:dsh")
        row_b = self.conn.execute("SELECT * FROM tasks WHERE id = ?", (self.b,)).fetchone()
        conflict = deps.worktree_conflict(self.conn, row_b, holder=None)
        self.assertIsNotNone(conflict)
        self.assertEqual(conflict["id"], self.a)
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, self.b, holder="grok", actor="agent:grok")
        self.assertIn("занято задачей", str(cm.exception))
        # `ready` про держателя и блокеры, занятое дерево оно отдаёт отдельным
        # полем `worktree_busy` (и строкой в `reasons`) — его и проверяем.
        state = deps.ready(self.conn, self.b)
        self.assertIsNotNone(state["worktree_busy"])
        self.assertEqual(state["worktree_busy"]["id"], self.a)


class NeedsYouNoFalseAlarmTests(TempDbTestCase):
    """Полоса «нужен ты»: тождественный heartbeat не оставляет ложный сигнал."""

    def test_card_is_not_in_needs_you_after_same_actor_heartbeat(self) -> None:
        task = store.create_task(self.conn, title="Порция", project="demo")["id"]
        store.claim(self.conn, task, holder="claude", actor="agent:claude", harness="claude")
        store.heartbeat(self.conn, task, holder="agent:claude", actor="agent:claude",
                        harness="claude")
        ts = _ago(40)
        self.conn.execute("UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'claim'",
                          (ts, task))
        self.conn.execute("UPDATE tasks SET holder_at = ?, updated_at = ? WHERE id = ?",
                          (ts, ts, task))
        self.conn.commit()
        out = store.get_task(self.conn, task)
        self.assertFalse(out["not_taken_warn"])
        board = store.board(self.conn, project="demo")
        self.assertNotIn(task, [c["id"] for c in board.get("needs_you", [])])
