"""`deps.open_hard_edges` — единственное определение незакрытого жёсткого ребра (listik-nghw.b).

Ребро незакрыто, если его тип из `HARD_BLOCKERS`, а задача-блокер не `done`/`cancelled`;
отсутствующая задача-блокер считается открытой. На этом правиле стоят гейт `claim`/`ready`,
денормализованный `blocked_by` и выборка `ready_tasks` — тесты ниже держат их вместе.
"""
from __future__ import annotations

import unittest

from listik import deps
from listik import store
from tests.helpers import TempDbTestCase
from tests.test_resource_blocks import _insert_resource_block


def _edges(rows) -> list[dict]:
    return [dict(r) for r in rows]


def _edge(issue_id: str, depends_on: str, dep_type: str = "blocks") -> dict:
    return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": dep_type}


class OpenHardEdgesTests(TempDbTestCase):
    def _new(self, title: str, project: str = "demo") -> str:
        return store.create_task(self.conn, title=title, project=project)["id"]

    def _hard(self, issue_id: str, depends_on: str, dep_type: str = "blocks") -> None:
        if dep_type == deps.RESOURCE_BLOCK:
            _insert_resource_block(self.conn, issue_id, depends_on)
        else:
            store.add_dep(self.conn, issue_id, depends_on, dep_type, created_by="me")

    def _blocked_by(self, task_id: str) -> list[str]:
        return store.get_task(self.conn, task_id)["blocked_by"]

    def test_every_hard_type_blocks_until_blocker_closes(self) -> None:
        for dep_type in deps.HARD_BLOCKERS:
            with self.subTest(dep_type=dep_type):
                a = self._new(f"A {dep_type}")
                b = self._new(f"B {dep_type}")
                self._hard(a, b, dep_type)

                self.assertEqual(_edges(deps.open_hard_edges(self.conn, issue_ids=[a])),
                                 [_edge(a, b, dep_type)])
                self.assertFalse(deps.ready(self.conn, a)["claimable"])
                with self.assertRaises(ValueError):
                    store.claim(self.conn, a, holder="dsh")
                self.assertEqual(self._blocked_by(a), [b])

                final = "cancelled" if dep_type == "waits-for" else "done"
                store.update_task(self.conn, b, status=final)
                self.assertEqual(deps.open_hard_edges(self.conn, issue_ids=[a]), [])
                self.assertTrue(deps.ready(self.conn, a)["claimable"])
                self.assertEqual(self._blocked_by(a), [])

    def test_soft_links_never_block(self) -> None:
        a = self._new("A")
        b = self._new("B")
        store.add_dep(self.conn, a, b, "relates-to", created_by="me")
        store.add_dep(self.conn, a, b, "parent-child", created_by="me")
        store.add_dep(self.conn, a, b, "blocks", created_by="agent:codex")
        types = {r["dep_type"] for r in self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ?", (a,))}
        self.assertEqual(types, {"relates-to", "parent-child", "suggested-blocks"})

        self.assertEqual(deps.open_hard_edges(self.conn, issue_ids=[a]), [])
        self.assertEqual(deps.open_hard_edges(self.conn), [])
        state = deps.ready(self.conn, a)
        self.assertTrue(state["ready"])
        self.assertTrue(state["claimable"])

    def test_missing_blocker_still_blocks(self) -> None:
        a = self._new("A")
        b = self._new("B")
        self._hard(a, b)
        store.delete_task(self.conn, b)

        self.assertEqual(_edges(deps.open_hard_edges(self.conn, issue_ids=[a])), [_edge(a, b)])
        deps.refresh_task(self.conn, a)
        self.assertEqual(self._blocked_by(a), [b])
        self.assertEqual([(x["id"], x["missing"]) for x in deps.blockers(self.conn, a)],
                         [(b, True)])

    def test_filters(self) -> None:
        a1, a2, a3 = self._new("A1"), self._new("A2"), self._new("A3")
        b1, b2 = self._new("B1"), self._new("B2")
        self._hard(a1, b1)
        self._hard(a1, b2)
        self._hard(a2, b1)
        self._hard(a3, b2)
        every = sorted([(a1, b1), (a1, b2), (a2, b1), (a3, b2)])

        self.assertEqual(_edges(deps.open_hard_edges(self.conn)),
                         [_edge(i, d) for i, d in every])
        self.assertEqual(_edges(deps.open_hard_edges(self.conn, issue_ids=[a1])),
                         [_edge(a1, d) for d in sorted([b1, b2])])
        self.assertEqual(_edges(deps.open_hard_edges(self.conn, depends_on=b1)),
                         [_edge(i, b1) for i in sorted([a1, a2])])
        self.assertEqual(_edges(deps.open_hard_edges(self.conn, issue_ids=[a1, a3], depends_on=b2)),
                         [_edge(i, b2) for i in sorted([a1, a3])])
        self.assertEqual(_edges(deps.open_hard_edges(self.conn, issue_ids=[a2], depends_on=b2)), [])
        self.assertEqual(_edges(deps.open_hard_edges(self.conn, issue_ids={a1, a2})),
                         _edges(deps.open_hard_edges(self.conn, issue_ids=[a1, a2])))

        statements: list[str] = []
        self.conn.set_trace_callback(statements.append)
        try:
            self.assertEqual(deps.open_hard_edges(self.conn, issue_ids=[]), [])
            self.assertEqual(deps.open_hard_edges(self.conn, issue_ids=set(), depends_on=b1), [])
        finally:
            self.conn.set_trace_callback(None)
        self.assertEqual(statements, [])

    def test_ready_tasks_groups_by_issue_id(self) -> None:
        a = self._new("A")
        b = self._new("B")
        self._hard(a, b)
        ready_ids = [t["id"] for t in deps.ready_tasks(self.conn, project="demo")]
        self.assertNotIn(a, ready_ids)
        self.assertIn(b, ready_ids)

    def test_closed_blocker_from_other_project(self) -> None:
        a = self._new("A")
        d = self._new("D", project="other")
        self._hard(a, d)
        store.update_task(self.conn, d, status="done")

        self.assertEqual(deps.blockers(self.conn, a), [])
        self.assertEqual(self._blocked_by(a), [])
        self.assertIn(a, [t["id"] for t in deps.ready_tasks(self.conn, project="demo")])

    def test_blockers_order(self) -> None:
        a = self._new("A")
        b1, b2, b3, b4 = (self._new(f"B{i}") for i in range(1, 5))
        for b in (b4, b3, b2, b1):
            self._hard(a, b)
        store.update_task(self.conn, b2, status="in_progress")
        store.delete_task(self.conn, b3)

        self.assertEqual([x["id"] for x in deps.blockers(self.conn, a)],
                         [b2] + sorted([b1, b4]) + [b3])

    def test_waiting_for_order_missing_and_closed_root(self) -> None:
        r = self._new("R")
        w1, w2, w3, w4, w5 = (self._new(f"W{i}") for i in range(1, 6))
        for w in (w5, w4, w3, w2, w1):
            self._hard(w, r)
        store.update_task(self.conn, w2, status="in_progress")
        store.delete_task(self.conn, w3)
        store.update_task(self.conn, w5, status="cancelled")

        self.assertEqual([x["id"] for x in deps.waiting_for(self.conn, r)],
                         [w2] + sorted([w1, w3, w4]))

        store.update_task(self.conn, r, status="done")
        self.assertEqual(deps.waiting_for(self.conn, r), [])
        self.assertEqual(store.get_task(self.conn, r)["deps_state"]["waiting_for"], [])
        self.assertEqual(deps.graph(self.conn, r)["waited_by"], [])

    def test_refreshes_agree(self) -> None:
        a, b, c = self._new("A"), self._new("B"), self._new("C")
        x = self._new("X", project="other")
        self._hard(a, b)
        self._hard(a, c)
        self._hard(a, x, "waits-for")
        store.update_task(self.conn, c, status="done")

        e, f = self._new("E"), self._new("F")
        g, h = self._new("G"), self._new("H")
        self._hard(e, f, deps.RESOURCE_BLOCK)
        self._hard(g, h, deps.RESOURCE_BLOCK)
        store.update_task(self.conn, h, status="done")

        k, m = self._new("K"), self._new("M")
        self._hard(k, m)
        store.delete_task(self.conn, m)

        n = self._new("N")
        p = self._new("P", project="other")
        self._hard(n, p, "conditional-blocks")
        store.update_task(self.conn, p, status="cancelled")

        self.assertEqual(deps.refresh_blocked_column(self.conn), 0)
        expected = {a: sorted([b, x]), b: [], c: [], x: [], e: [f], f: [], g: [], h: [],
                    k: [m], n: [], p: []}
        for tid, want in expected.items():
            with self.subTest(task=tid):
                got = self._blocked_by(tid)
                self.assertEqual(got, want)
                self.assertEqual(got, sorted({bl["id"] for bl in deps.blockers(self.conn, tid)}))


if __name__ == "__main__":
    unittest.main()
