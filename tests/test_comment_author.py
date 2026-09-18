"""Автор комментария с доски — человек, а не держатель карточки (listik-015y).

Воспроизведение: комментарий, отправленный с доски, подписывался `agent:<имя>`,
потому что клиент брал автором держателя карточки. Сервер без явного автора
теперь берёт человека из заголовка `X-Listik-Owner`; явный `author`/`actor`
(агентский `agent:<имя>`) остаётся сильнее, поэтому ограничения по фактическому
автору (`actor_kind == "agent"`) не путаются с держателем.
"""
from __future__ import annotations

from tests.test_owner_http import OwnerHttpCase


class TestCommentAuthor(OwnerHttpCase):
    def comment(self, task_id: str, *, owner=None, **body):
        status, payload = self.api("POST", f"/api/tasks/{task_id}/comment",
                                   owner=owner, body={"text": "текст", "kind": "comment", **body})
        return self.data(status, payload)

    def test_owner_becomes_author_without_explicit_author(self):
        task = self.make_task("ann")
        self.assertEqual(self.comment(task["id"], owner="ann")["author"], "ann")

    def test_explicit_agent_author_wins(self):
        task = self.make_task("ann")
        out = self.comment(task["id"], owner="ann", author="agent:dsh")
        self.assertEqual(out["author"], "agent:dsh")

    def test_agent_verdict_off_judge_stage_is_downgraded(self):
        task = self.make_task("ann")
        out = self.comment(task["id"], owner="ann", author="agent:dsh",
                           text="VERDICT: PASS", kind="verdict")
        self.assertEqual(out["kind"], "comment")
        self.assertFalse(out["verdict_accepted"])

    def test_human_verdict_off_judge_stage_is_accepted(self):
        task = self.make_task("ann")
        out = self.comment(task["id"], owner="ann", text="VERDICT: PASS", kind="verdict")
        self.assertTrue(out["verdict_accepted"])
