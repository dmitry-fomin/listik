"""Автор вопроса/ответа с доски — человек, а не «никто» (listik-z0sd).

Воспроизведение: путь `needs-owner` (форма вопроса и форма ответа на доске)
отправлял текст без автора, поэтому комментарий `question`/`answer` и событие
оставались без автора и могли достаться не тому актору. Сервер без явного
`actor` теперь подписывает запись человеком из заголовка `X-Listik-Owner`;
явный агентский `actor` (`agent:<имя>`) остаётся сильнее.
"""
from __future__ import annotations

from tests.test_owner_http import OwnerHttpCase


class TestNeedsOwnerAuthor(OwnerHttpCase):
    def needs_owner(self, task_id: str, *, owner=None, **body):
        status, payload = self.api("POST", f"/api/tasks/{task_id}/needs-owner",
                                   owner=owner, body=body)
        return self.data(status, payload)

    def comments(self, task_id: str, kind: str):
        return [c for c in self.get_task(task_id)["comments"] if c["kind"] == kind]

    def events(self, task_id: str, kind: str):
        return [e for e in self.get_task(task_id)["events"] if e["kind"] == kind]

    def test_owner_becomes_question_author(self):
        task = self.make_task("ann")
        self.needs_owner(task["id"], owner="ann", value=True, note="вопрос автору")
        questions = self.comments(task["id"], "question")
        self.assertEqual([c["author"] for c in questions], ["ann"])

    def test_owner_becomes_answer_author(self):
        task = self.make_task("ann")
        self.needs_owner(task["id"], owner="ann", value=True, note="вопрос автору")
        self.needs_owner(task["id"], owner="ann", value=False, note="ответ автора")
        answers = self.comments(task["id"], "answer")
        self.assertEqual([c["author"] for c in answers], ["ann"])

    def test_explicit_agent_actor_wins(self):
        task = self.make_task("ann")
        self.needs_owner(task["id"], owner="ann", value=True, note="вопрос агента",
                         actor="agent:dsh")
        questions = self.comments(task["id"], "question")
        self.assertEqual([c["author"] for c in questions], ["agent:dsh"])
        self.assertEqual([e["actor"] for e in self.events(task["id"], "question")],
                         ["agent:dsh"])

    def test_question_event_gets_actor(self):
        task = self.make_task("ann")
        self.needs_owner(task["id"], owner="ann", value=True, note="вопрос автору")
        events = self.events(task["id"], "question")
        self.assertEqual(len(events), 1, events)
        self.assertTrue(events[0]["actor"])
