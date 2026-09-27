"""Фильтры `store.list_tasks` по здоровью, зависимостям и дате (listik-0xy8, порция a).

`total` и страница считаются по отфильтрованному набору, а не по полученной странице;
`orchestrator=none` — задачи без оркестратора.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest import mock

from listik import deps, errors, server, store
from tests.helpers import TempDbTestCase
from tests.test_owner_store import OwnerStoreCase
from tests.test_resource_blocks import _insert_resource_block


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ids(res: dict) -> list[str]:
    return [t["id"] for t in res["tasks"]]


class Case(TempDbTestCase):
    def new(self, title: str, project: str = "demo", **kw) -> str:
        return store.create_task(self.conn, title=title, project=project, **kw)["id"]

    def sql(self, query: str, *params) -> None:
        self.conn.execute(query, params)
        self.conn.commit()

    def make_dead(self, tid: str) -> None:
        # «в работе» без держателя и без события release — брошена
        self.sql("UPDATE tasks SET status = 'in_progress', holder = NULL WHERE id = ?", tid)

    def make_healthy(self, tid: str) -> None:
        store.claim(self.conn, tid, holder="dsh", actor="agent:dsh", harness="dsh")

    def make_at_risk(self, tid: str) -> None:
        self.make_healthy(tid)
        self.sql("UPDATE tasks SET holder_at = ? WHERE id = ?", _ago(1), tid)

    def health_of(self, tid: str) -> str:
        return store.task_health(store.get_task(self.conn, tid, with_details=False))


class HealthPagingTests(Case):
    def setUp(self) -> None:
        super().setUp()
        # Порядок `updated`: s0 (свежее всех) ... s10. Брошенные и под угрозой стоят не первыми,
        # между ними — здоровые и без держателя.
        kinds = ["healthy", "unknown", "dead", "healthy", "at-risk", "dead", "unknown",
                 "at-risk", "healthy", "dead", "at-risk"]
        self.order: list[str] = []
        for i, kind in enumerate(kinds):
            tid = self.new(f"T{i}", stage="s1-spec")
            if kind == "dead":
                self.make_dead(tid)
            elif kind == "healthy":
                self.make_healthy(tid)
            elif kind == "at-risk":
                self.make_at_risk(tid)
            self.order.append(tid)
        for i, tid in enumerate(self.order):
            self.sql("UPDATE tasks SET updated_at = ? WHERE id = ?",
                     f"2026-09-{27 - i:02d}T10:00:00Z", tid)
        self.kinds = dict(zip(self.order, kinds))
        for tid, kind in self.kinds.items():
            self.assertEqual(self.health_of(tid), kind, tid)

    def check(self, health: str) -> None:
        expected = [t for t in ids(store.list_tasks(self.conn)) if self.kinds[t] == health]
        self.assertEqual(len(expected), 3)
        self.assertNotEqual(ids(store.list_tasks(self.conn))[0], expected[0])
        first = store.list_tasks(self.conn, health=health, limit=2)
        second = store.list_tasks(self.conn, health=health, limit=2, offset=2)
        self.assertEqual((first["total"], len(first["tasks"])), (3, 2))
        self.assertEqual((second["total"], len(second["tasks"])), (3, 1))
        self.assertEqual(ids(first) + ids(second), expected)
        for t in first["tasks"] + second["tasks"]:
            self.assertEqual(store.task_health(t), health)

    def test_dead(self) -> None:
        self.check("dead")

    def test_at_risk(self) -> None:
        self.check("at-risk")


class TaskHealthTests(Case):
    BASE = {"status": "in_progress", "stale": False, "abandoned": False, "holder": "dsh",
            "not_taken_warn": False, "stage_warn": False, "idle_hours": 0.0}

    def h(self, **kw) -> str:
        return store.task_health({**self.BASE, **kw})

    def test_branches(self) -> None:
        self.assertEqual(self.h(status="done", stale=True), "healthy")
        self.assertEqual(self.h(status="cancelled", abandoned=True, holder=None), "healthy")
        self.assertEqual(self.h(abandoned=True, holder=None), "dead")
        self.assertEqual(self.h(stale=True), "dead")
        self.assertEqual(self.h(holder=""), "unknown")
        self.assertEqual(self.h(holder=None), "unknown")
        self.assertEqual(self.h(holder=None, not_taken_warn=True), "unknown")
        self.assertEqual(self.h(not_taken_warn=True), "at-risk")
        self.assertEqual(self.h(stage_warn=True), "at-risk")
        self.assertEqual(self.h(idle_hours=0.25), "at-risk")
        self.assertEqual(self.h(idle_hours=0.2), "healthy")
        self.assertEqual(self.h(idle_hours=None), "healthy")
        self.assertEqual(store.AT_RISK_IDLE_HOURS, 0.25)


class DepsBlockedTests(Case):
    def test_blocked(self) -> None:
        conn = self.conn
        b, c, e = self.new("B"), self.new("C"), self.new("E")
        a1, a2, a4, a6, x = (self.new(n) for n in ("A1", "A2", "A4", "A6", "X"))
        store.add_dep(conn, a1, b, "blocks", created_by="me")
        store.add_dep(conn, a2, c, "blocks", created_by="me")
        store.update_task(conn, c, status="done")
        _insert_resource_block(conn, a4, e)
        store.add_dep(conn, a6, b, "blocks", created_by="agent:codex")
        store.add_dep(conn, x, b, "blocks", created_by="me")
        store.update_task(conn, x, status="cancelled")
        self.assertEqual(store.get_task(conn, x)["blocked_by"], [b])

        for kw in ({}, {"include_closed": True}):
            res = store.list_tasks(conn, deps="blocked", **kw)
            self.assertEqual(set(ids(res)), {a1, a4}, kw)
            self.assertEqual(res["total"], 2)
            paged = {t for off in (0, 1) for t in ids(
                store.list_tasks(conn, deps="blocked", limit=1, offset=off, **kw))}
            self.assertEqual(paged, {a1, a4})
        self.assertEqual(store.list_tasks(conn, deps="blocked", status="cancelled")["total"], 0)
        self.assertEqual(store.list_tasks(conn, deps="all")["total"],
                         store.list_tasks(conn)["total"])


class DepsReadyTests(Case):
    def test_matches_ready_tasks(self) -> None:
        conn = self.conn
        free = self.new("свободная")
        orphan = self.new("в работе без держателя")
        self.sql("UPDATE tasks SET status = 'in_progress', holder = NULL WHERE id = ?", orphan)
        held = self.new("с держателем", stage="s1-spec")
        self.make_healthy(held)
        blocker = self.new("блокер")
        waiting = self.new("ждёт блокер")
        store.add_dep(conn, waiting, blocker, "blocks", created_by="me")
        epic = self.new("эпик", issue_type="epic")
        child = self.new("ребёнок", parent=epic)
        status_blocked = self.new("статус blocked")
        self.sql("UPDATE tasks SET status = 'blocked' WHERE id = ?", status_blocked)
        closed = self.new("закрыта")
        store.update_task(conn, closed, status="done")

        res = store.list_tasks(conn, deps="ready", limit=1000)
        expected = {t["id"] for t in deps.ready_tasks(conn, limit=1000)}
        self.assertEqual(set(ids(res)), expected)
        self.assertEqual(res["total"], len(expected))
        self.assertEqual(expected, {free, orphan, blocker, child})
        self.assertFalse({held, waiting, epic, status_blocked, closed} & set(ids(res)))


class ExpireReturnWindowTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.p = self.new("P", stage="s3-impl")
        store.claim(self.conn, self.p, holder="dsh")
        store.next_stage(self.conn, self.p, to_stage="s4-judge")
        store.add_comment(self.conn, self.p, "VERDICT: FAIL\n1. test_x fails", kind="verdict",
                          author="agent:claude")
        self.assertEqual(store.get_task(self.conn, self.p)["stage"], "s3-impl")
        self.sql("UPDATE events SET ts = ? WHERE task_id = ? AND kind = 'stage' "
                 "AND from_value = 's4-judge' AND to_value = 's3-impl'", _ago(30), self.p)
        self.sql("UPDATE tasks SET holder_at = ? WHERE id = ?", _ago(31), self.p)

    def holder(self) -> str:
        return self.conn.execute("SELECT holder FROM tasks WHERE id = ?",
                                 (self.p,)).fetchone()["holder"]

    def test_only_ready_filter_expires(self) -> None:
        store.list_tasks(self.conn)
        self.assertEqual(self.holder(), "dsh")
        store.list_tasks(self.conn, health="dead")
        self.assertEqual(self.holder(), "dsh")
        res = store.list_tasks(self.conn, deps="ready")
        self.assertEqual(self.holder(), "")
        self.assertIn(self.p, ids(res))
        self.assertIn(self.p, {t["id"] for t in deps.ready_tasks(self.conn, limit=1000)})


class UpdatedDateTests(Case):
    def setUp(self) -> None:
        super().setUp()
        self.t = []
        for ts in ("2026-09-25T10:00:00Z", "2026-09-26T23:59:59Z", "2026-09-27T00:00:00Z"):
            tid = self.new(ts)
            self.sql("UPDATE tasks SET updated_at = ? WHERE id = ?", ts, tid)
            self.t.append(tid)

    def test_bounds_inclusive(self) -> None:
        a, b, c = self.t
        both = store.list_tasks(self.conn, updated_from="2026-09-26", updated_to="2026-09-26")
        self.assertEqual((ids(both), both["total"]), ([b], 1))
        self.assertEqual(set(ids(store.list_tasks(self.conn, updated_from="2026-09-26"))), {b, c})
        self.assertEqual(set(ids(store.list_tasks(self.conn, updated_to="2026-09-26"))), {a, b})
        self.assertEqual(store.list_tasks(self.conn, updated_from="2026-09-27",
                                          updated_to="2026-09-25")["total"], 0)
        self.assertEqual(store.list_tasks(self.conn, updated_from="", updated_to="")["total"], 3)


class OrchestratorNoneTests(Case):
    def test_none(self) -> None:
        null, empty, claude = self.new("null"), self.new("empty"), self.new("claude")
        for tid, value in ((null, None), (empty, ""), (claude, "claude")):
            self.sql("UPDATE tasks SET orchestrator = ? WHERE id = ?", value, tid)
        res = store.list_tasks(self.conn, orchestrator="none")
        self.assertEqual((set(ids(res)), res["total"]), ({null, empty}, 2))
        self.assertEqual(ids(store.list_tasks(self.conn, orchestrator="claude")), [claude])


class ValidationTests(Case):
    def test_bad_values(self) -> None:
        for kw in ({"health": "bogus"}, {"health": "unknown"}, {"deps": "bogus"},
                   {"updated_from": "2026-02-30"}, {"updated_to": "27.09.2026"},
                   {"updated_from": "2026-9-7"}, {"updated_from": "20260927"},
                   {"updated_to": "2026-09-27T00:00:00"}):
            with self.assertRaises(errors.BadArgument, msg=kw) as ctx:
                store.list_tasks(self.conn, **kw)
            self.assertIn(next(iter(kw)), str(ctx.exception))
        for kw in ({"health": ""}, {"deps": ""}, {"deps": "all"}, {"updated_from": ""}):
            store.list_tasks(self.conn, **kw)

    def test_http_400(self) -> None:
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            with self.assertRaises(errors.BadArgument) as ctx:
                server.handle("GET", "/api/tasks", {"health": "bogus"}, {}, authed=True)
        status, _, code, _hint = server.error_response(ctx.exception)
        self.assertEqual((status, code), (400, "bad_argument"))


class HttpPassesParamsTests(Case):
    def test_same_as_store(self) -> None:
        conn = self.conn
        dead = self.new("dead")
        self.make_dead(dead)
        b = self.new("B")
        a = self.new("A")
        store.add_dep(conn, a, b, "blocks", created_by="me")
        self.new("free")
        self.sql("UPDATE tasks SET orchestrator = 'claude' WHERE id = ?", b)
        self.sql("UPDATE tasks SET updated_at = '2026-09-20T10:00:00Z' WHERE id = ?", a)
        cases = ({"health": "dead"}, {"deps": "blocked"}, {"deps": "ready"},
                 {"updated_from": "2026-09-19", "updated_to": "2026-09-21"},
                 {"orchestrator": "none"}, {"orchestrator": "none", "deps": "ready"})
        for query in cases:
            direct = store.list_tasks(conn, **query)
            with mock.patch.object(server, "get_conn", return_value=conn):
                status, res = server.handle("GET", "/api/tasks", dict(query), {}, authed=True)
            self.assertEqual(status, 200)
            self.assertEqual((res["total"], ids(res)), (direct["total"], ids(direct)), query)
            self.assertLess(direct["total"], 4, query)


class OwnerFilterTests(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()

    def test_health_and_deps_keep_owner(self) -> None:
        conn = self.conn
        ann_dead = self.make("ann dead", project="demo", as_owner="ann")["id"]
        bob_dead = self.make("bob dead", project="demo", as_owner="bob")["id"]
        conn.execute("UPDATE tasks SET status = 'in_progress', holder = NULL WHERE id IN (?, ?)",
                     (ann_dead, bob_dead))
        conn.commit()
        res = store.list_tasks(conn, health="dead", as_owner="ann")
        self.assertEqual((ids(res), res["total"]), ([ann_dead], 1))

        b = self.make("B", project="demo", as_owner="ann")["id"]
        aa = self.make("Aa", project="demo", as_owner="ann")["id"]
        ab = self.make("Ab", project="demo", as_owner="bob")["id"]
        store.add_dep(conn, aa, b, "blocks", created_by="me")
        store.add_dep(conn, ab, b, "blocks", created_by="me")
        res = store.list_tasks(conn, deps="blocked", as_owner="ann")
        self.assertEqual((ids(res), res["total"]), ([aa], 1))
