"""Автор вопроса/ответа у `listik_needs_owner` по MCP (listik-ddjd).

Воспроизведение: ветка `listik_needs_owner` в `listik/mcp.py` передавала в store
только `actor` из аргументов. Без него `store.set_needs_owner` писал комментарий и
событие `question`/`answer` без автора, тогда как CLI и доска (HTTP) подписываются.

Проверяются оба случая приёмки — без `actor` (внятный автор, а не пусто) и с явным
`agent:<имя>` — и совпадение актора у комментария и события. Заодно проверено, что
транспортное имя (`X-Listik-Owner`/`LISTIK_OWNER`/`LISTIK_ACTOR`) не теряется.
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from listik import mcp
from listik import store
from tests.helpers import TempDbTestCase


def _comments(conn, task_id, kind):
    return [c for c in store.get_task(conn, task_id)["comments"] if c["kind"] == kind]


def _events(conn, task_id, kind):
    return conn.execute(
        "SELECT * FROM events WHERE task_id = ? AND kind = ? ORDER BY id", (task_id, kind)
    ).fetchall()


class McpNeedsOwnerAuthorTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Задача", project="demo")["id"]

    def _call(self, **args):
        return mcp.call_tool("listik_needs_owner", {"id": self.task_id, **args}, conn=self.conn)

    def _clear_transport_env(self):
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LISTIK_ACTOR", None)
        os.environ.pop("LISTIK_OWNER", None)

    def test_without_actor_question_and_answer_have_sensible_author(self) -> None:
        self._clear_transport_env()
        self._call(value=True, text="Q")
        self._call(value=False, text="A")

        for kind in ("question", "answer"):
            comments = _comments(self.conn, self.task_id, kind)
            self.assertEqual(len(comments), 1, kind)
            self.assertEqual(comments[0]["author"], "agent:mcp", kind)
            events = _events(self.conn, self.task_id, kind)
            self.assertEqual(len(events), 1, kind)
            self.assertEqual(events[0]["actor"], comments[0]["author"], kind)

    def test_explicit_agent_actor_is_kept(self) -> None:
        self._clear_transport_env()
        self._call(value=True, text="Q", actor="agent:dsh")

        comments = _comments(self.conn, self.task_id, "question")
        self.assertEqual(comments[0]["author"], "agent:dsh")
        events = _events(self.conn, self.task_id, "question")
        self.assertEqual(events[0]["actor"], "agent:dsh")

    def test_env_actor_is_used_without_explicit_actor(self) -> None:
        self._clear_transport_env()
        with mock.patch.dict(os.environ, {"LISTIK_ACTOR": "agent:codex"}):
            self._call(value=True, text="Q")

        comments = _comments(self.conn, self.task_id, "question")
        self.assertEqual(comments[0]["author"], "agent:codex")
        events = _events(self.conn, self.task_id, "question")
        self.assertEqual(events[0]["actor"], "agent:codex")

    def test_http_owner_header_signs_without_actor(self) -> None:
        """Путь HTTP-MCP: имя приходит заголовком `X-Listik-Owner` (owner)."""
        self._clear_transport_env()
        response = mcp.handle(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "listik_needs_owner",
                        "arguments": {"id": self.task_id, "text": "Q"}}},
            conn=self.conn, owner="ann")

        self.assertFalse(response["result"].get("isError"), response)
        comments = _comments(self.conn, self.task_id, "question")
        self.assertEqual(comments[0]["author"], "ann")
        events = _events(self.conn, self.task_id, "question")
        self.assertEqual(events[0]["actor"], "ann")


if __name__ == "__main__":
    unittest.main()
