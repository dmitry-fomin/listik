"""Лестница `listik watch` пишет в чужую карточку от имени её владельца (listik-dfjc).

Серверный режим (`users = ["ann", "bob"]`), владелец машины — `bob`, опоздавший —
карточка `ann`. `release` и `PATCH labels` порта `_SwarmCards` проверяют владельца,
поэтому порт шлёт владельца карточки; проверка владельца на сервере остаётся (T4).
"""
from __future__ import annotations

import argparse
import os
from unittest import mock

from listik import errors, store, swarm_watch
from tests.test_autostart import _load_cli
from tests.test_owner_http import OwnerHttpCase

RELEASE_NOTE = "рой: держатель снят при заморозке"
SWARM = "agent:listik-swarm"


class TestSwarmCardsOwner(OwnerHttpCase):

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.cli = _load_cli()

    def setUp(self) -> None:
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("LISTIK_OWNER", None)
        super().setUp()
        self.late = self.make_task("ann", title="late")["id"]
        self.data(*self.api("POST", f"/api/tasks/{self.late}/claim", owner="ann",
                            body={"holder": "agent:dsh"}))
        self.data(*self.api("PATCH", f"/api/tasks/{self.late}", owner="ann",
                            body={"labels": ["port:5173"]}))

    def cards(self, *, local: bool = False):
        self.args = argparse.Namespace(host="127.0.0.1", port=self.port, owner="bob",
                                       local=local)
        return self.cli._SwarmCards(self.args)

    def freeze_by_port(self, cards) -> list[str]:
        labels = ["port:5173", "frozen-by:demo-0001"]
        cards.release(self.late, RELEASE_NOTE)
        cards.set_labels(self.late, labels)
        return labels

    def assert_frozen(self, labels: list[str], owner: str = "ann") -> dict:
        task = self.get_task(self.late)
        self.assertFalse(task["holder"])
        self.assertEqual(task["labels"], labels)
        self.assertEqual(task["owner"] or "", owner)
        release = next(e for e in task["events"] if e["kind"] == "release")
        self.assertEqual(release["actor"], SWARM)
        self.assertEqual(release["note"], RELEASE_NOTE)
        return task

    def assert_journal(self, task: dict, mark: str) -> None:
        self.assertTrue(any(c["author"] == SWARM and c["kind"] == "journal"
                            and c["text"].startswith(mark) for c in task["comments"]),
                        task["comments"])

    def test_http_foreign_card(self):  # T1
        labels = self.freeze_by_port(self.cards())
        self.assert_frozen(labels)
        self.assertEqual(self.args.owner, "bob")

    def test_local_foreign_card(self):  # T2
        labels = self.freeze_by_port(self.cards(local=True))
        self.assert_frozen(labels)
        self.assertEqual(self.args.owner, "bob")

    def test_http_ladder(self):  # T3
        t1 = self.make_task("bob", title="owner")["id"]
        cards = self.cards()
        decision = swarm_watch._freeze(cards, t1, self.late, ["pkg/mod.py"],
                                       cards.show(self.late), resumed=True, dry_run=False)
        self.assertIs(decision["ok"], True, decision)
        self.assertNotIn("error", decision)
        late = self.assert_frozen(["port:5173", f"frozen-by:{t1}"])
        self.assert_journal(late, swarm_watch.FREEZE_MARK)
        self.assert_journal(self.get_task(t1), swarm_watch.OWN_MARK)

    def test_raw_foreign_writes_still_forbidden(self):  # T4
        for method, path, body in (
                ("POST", f"/api/tasks/{self.late}/release", {"actor": SWARM, "note": RELEASE_NOTE}),
                ("PATCH", f"/api/tasks/{self.late}", {"actor": SWARM, "labels": ["frozen-by:x"]})):
            status, payload = self.api(method, path, owner="bob", body=body)
            self.assertEqual(status, 403, payload)
            self.assertEqual(payload["code"], "forbidden")
        task = self.get_task(self.late)
        self.assertEqual(task["holder"], "agent:dsh")
        self.assertEqual(task["labels"], ["port:5173"])

    def test_unowned_card_goes_as_machine(self):  # T5
        self.data(*self.api("PATCH", f"/api/tasks/{self.late}", owner="ann",
                            body={"owner": ""}))
        labels = self.freeze_by_port(self.cards())
        self.assert_frozen(labels, owner="")

    def test_local_forbidden_is_port_error(self):  # T6
        cards = self.cards(local=True)
        with mock.patch.object(store, "release_task", side_effect=errors.Forbidden("чужая")):
            with self.assertRaises(errors.ListikError) as ctx:
                cards.release(self.late, RELEASE_NOTE)
        self.assertEqual(ctx.exception.code, "forbidden")

