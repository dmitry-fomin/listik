"""Агентский автор по умолчанию у `listik_comment`/`listik_deps` по MCP (listik-ou3i).

Воспроизведение: `listik_comment` передавал в store `author=args.get("author")` без
фолбэка. Без автора `store.add_comment` считал автора человеком, и агентский
`kind=verdict` принимался на любом этапе в обход правила s4-judge, а `comments.author`
оставался NULL. Владелец транспорта (`X-Listik-Owner`/`LISTIK_OWNER`) автором у
`listik_comment`/`listik_deps` не становится; у `listik_needs_owner` — становится, а
присланный туда `author` не участвует.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from listik import mcp
from listik import store
from tests.helpers import TempDbTestCase


def _events(conn, task_id, kind):
    return conn.execute(
        "SELECT * FROM events WHERE task_id = ? AND kind = ? ORDER BY id", (task_id, kind)
    ).fetchall()


def _dep_rows(conn, issue_id, depends_on):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM deps WHERE issue_id = ? AND depends_on = ?", (issue_id, depends_on))]


class McpCommentAuthorTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        # У агентов, которые гоняют тесты, LISTIK_ACTOR выставлен: убираем его и
        # LISTIK_OWNER на время теста, patch.dict вернёт окружение после.
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LISTIK_ACTOR", None)
        os.environ.pop("LISTIK_OWNER", None)

    def _task(self, stage="s3-impl", title="Задача"):
        return store.create_task(self.conn, title=title, project="demo", stage=stage)["id"]

    def _call(self, name, **args):
        return mcp.call_tool(name, args, conn=self.conn, fence=None)

    def _handle(self, name, args, owner):
        return mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": name, "arguments": args}},
            conn=self.conn, owner=owner, fence=None)

    def _comment(self, task_id, **args):
        return self._call("listik_comment", id=task_id, text=args.pop("text", "заметка"),
                          **args)

    def _assert_author(self, task_id, expected, kind="comment", event_kind="comment"):
        comments = [c for c in store.get_task(self.conn, task_id)["comments"]
                    if c["kind"] == kind]
        self.assertEqual(len(comments), 1, comments)
        self.assertEqual(comments[0]["author"], expected)
        events = _events(self.conn, task_id, event_kind)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], expected)

    def _assert_verdict_rejected(self, task_id, out):
        self.assertEqual(out["kind"], "comment")
        self.assertIs(out["verdict_accepted"], False)
        rows = self.conn.execute(
            "SELECT kind FROM comments WHERE task_id = ?", (task_id,)).fetchall()
        self.assertEqual([r["kind"] for r in rows], ["comment"])
        self.assertEqual(store.get_task(self.conn, task_id)["stage"], "s3-impl")

    # T1
    def test_comment_without_author_is_agent_mcp(self) -> None:
        task_id = self._task()
        self._comment(task_id)
        self._assert_author(task_id, "agent:mcp")

    # T2
    def test_comment_without_author_uses_env_actor(self) -> None:
        task_id = self._task()
        os.environ["LISTIK_ACTOR"] = "agent:codex"
        self._comment(task_id)
        self._assert_author(task_id, "agent:codex")

    # T3
    def test_explicit_author_is_kept_and_normalized(self) -> None:
        dsh = self._task(title="dsh")
        self._comment(dsh, author="agent:dsh")
        self._assert_author(dsh, "agent:dsh")

        claude = self._task(title="claude")
        self._comment(claude, author="claude")
        self._assert_author(claude, "agent:claude")

    # T4
    def test_actor_is_used_without_author(self) -> None:
        task_id = self._task()
        self._comment(task_id, actor="agent:grok")
        self._assert_author(task_id, "agent:grok")

    # T5
    def test_verdict_without_author_is_rejected_outside_judge_stage(self) -> None:
        task_id = self._task("s3-impl")
        out = self._comment(task_id, text="VERDICT: PASS", kind="verdict")
        self._assert_verdict_rejected(task_id, out)

    # T6
    def test_http_owner_does_not_make_verdict_human(self) -> None:
        task_id = self._task("s3-impl")
        response = self._handle("listik_comment",
                                {"id": task_id, "text": "VERDICT: PASS", "kind": "verdict"},
                                owner="ann")
        self.assertFalse(response["result"].get("isError"), response)
        rows = self.conn.execute(
            "SELECT kind FROM comments WHERE task_id = ?", (task_id,)).fetchall()
        self.assertEqual([r["kind"] for r in rows], ["comment"])
        self._assert_author(task_id, "agent:mcp")

    # T7
    def test_blank_author_is_treated_as_missing(self) -> None:
        task_id = self._task("s3-impl")
        out = self._comment(task_id, text="VERDICT: PASS", kind="verdict", author="   ")
        self._assert_verdict_rejected(task_id, out)
        self._assert_author(task_id, "agent:mcp")

    # T8
    def test_verdict_without_author_is_accepted_on_judge_stage(self) -> None:
        task_id = self._task("s4-judge")
        out = self._comment(task_id, text="VERDICT: PASS", kind="verdict")
        self.assertEqual(out["kind"], "verdict")
        self.assertIs(out["verdict_accepted"], True)

    # T9
    def test_deps_with_http_owner_stays_agent_suggestion(self) -> None:
        a = self._task(title="A")
        b = self._task(title="B")
        response = self._handle("listik_deps", {"id": b, "depends_on": a}, owner="ann")
        self.assertFalse(response["result"].get("isError"), response)
        rows = _dep_rows(self.conn, b, a)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["dep_type"], "suggested-blocks")
        self.assertEqual(rows[0]["created_by"], "agent:mcp")

    # T10
    def test_author_wins_over_actor(self) -> None:
        task_id = self._task()
        self._comment(task_id, author="agent:dsh", actor="agent:grok")
        self._assert_author(task_id, "agent:dsh")

    # T11
    def test_needs_owner_ignores_author(self) -> None:
        both = self._task(title="both")
        self._call("listik_needs_owner", id=both, value=True, text="Q",
                   author="agent:dsh", actor="agent:grok")
        self._assert_author(both, "agent:grok", kind="question", event_kind="question")

        only_author = self._task(title="only author")
        mcp.call_tool("listik_needs_owner",
                      {"id": only_author, "value": True, "text": "Q", "author": "agent:dsh"},
                      conn=self.conn, owner=None, fence=None)
        self._assert_author(only_author, "agent:mcp", kind="question", event_kind="question")

    # T12
    def test_deps_uses_author(self) -> None:
        a = self._task(title="A")
        b = self._task(title="B")
        out = self._call("listik_deps", id=b, depends_on=a, author="agent:codex")
        self.assertTrue(out["suggested"])
        rows = _dep_rows(self.conn, b, a)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["created_by"], "agent:codex")


if __name__ == "__main__":
    unittest.main()
