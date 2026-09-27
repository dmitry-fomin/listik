"""Сортировка `store.list_tasks` по полю и направлению до пагинации и `waiting_for_count`
(2-p1l8, порция a).

Порядок задаёт весь отфильтрованный набор, а не загруженная страница: первые страницы
`asc` и `desc` различаются. Пустые значения в конце в обоих направлениях, при равном ключе —
`updated_at DESC`, затем `id`.
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


# заголовок: status, holder, holder_at (ч назад), stage, stage_at (ч назад), updated_at (ч назад),
# priority
FIXTURE = {
    "echo": ("in_progress", "agent:dsh", 0.15, "s3-impl", 0.2, 0.2, 3),
    "golf": ("in_progress", "agent:dsh", 0.2, "s4-judge", 50, 3, 0),
    "alfa": ("in_progress", "agent:dsh", 3, "s3-impl", 40, 0.15, 2),
    "Delta": ("in_progress", "agent:dsh", 40, "s2-review", 3, 50, 4),
    "Bravo": ("in_progress", "agent:dsh", 50, "s3-impl", 0.15, 40, 2),
    "fox": ("open", None, None, None, None, 1, 1),
    "charlie": ("open", None, None, "s1-spec", 30, 5, 1),
}

EXPECTED = {
    "title": ("alfa bravo charlie delta echo fox golf",
              "golf fox echo delta charlie bravo alfa"),
    "status_title": ("alfa echo golf bravo delta fox charlie",
                     "fox charlie alfa echo golf bravo delta"),
    "priority": ("golf fox charlie alfa bravo echo delta",
                 "delta echo alfa bravo fox charlie golf"),
    "holder_hours": ("echo golf alfa delta bravo fox charlie",
                     "bravo delta alfa golf echo fox charlie"),
    "stage_hours": ("bravo echo delta charlie alfa golf fox",
                    "golf alfa charlie delta echo bravo fox"),
    "updated_at": ("delta bravo charlie golf fox echo alfa",
                   "alfa echo fox golf charlie bravo delta"),
    "blocked_count": ("echo fox golf charlie bravo alfa delta",
                      "delta alfa echo fox golf charlie bravo"),
    "waiting_for_count": ("alfa echo golf bravo delta charlie fox",
                          "fox charlie alfa echo golf bravo delta"),
}

INJECTIONS = (
    {"sort": "id; DROP TABLE tasks"},
    {"sort": "priority DESC, (SELECT 1)"},
    {"sort_dir": "desc; --"},
    {"sort_dir": "asc, id"},
)


class Case(TempDbTestCase):
    def new(self, title: str, project: str | None = "demo") -> str:
        return store.create_task(self.conn, title=title, project=project)["id"]

    def sql(self, query: str, *params) -> None:
        self.conn.execute(query, params)
        self.conn.commit()

    def count_tasks(self) -> int:
        return self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]


class MainFixtureCase(Case):
    def setUp(self) -> None:
        super().setUp()
        self.by_name = {title.lower(): self.new(title) for title in FIXTURE}
        n = self.by_name
        store.add_dep(self.conn, n["alfa"], n["fox"], "blocks", created_by="me")
        store.add_dep(self.conn, n["delta"], n["fox"], "blocks", created_by="me")
        store.add_dep(self.conn, n["delta"], n["charlie"], "blocks", created_by="me")
        for title, (status, holder, holder_h, stage, stage_h, upd_h, prio) in FIXTURE.items():
            self.sql("UPDATE tasks SET status = ?, holder = ?, holder_at = ?, stage = ?, "
                     "stage_at = ?, updated_at = ?, priority = ? WHERE id = ?",
                     status, holder, None if holder_h is None else _ago(holder_h), stage,
                     None if stage_h is None else _ago(stage_h), _ago(upd_h), prio,
                     n[title.lower()])
        self.all_ids = list(n.values())

    def names(self, words: str) -> list[str]:
        return [self.by_name[w] for w in words.split()]

    def expected(self) -> dict[tuple[str, str], list[str]]:
        out = {}
        for sort, (asc, desc) in EXPECTED.items():
            out[(sort, "asc")] = self.names(asc)
            out[(sort, "desc")] = self.names(desc)
        out[("id", "asc")] = sorted(self.all_ids)
        out[("id", "desc")] = sorted(self.all_ids, reverse=True)
        return out

    def listed(self, **kw) -> dict:
        return store.list_tasks(self.conn, **kw)


class OrderTests(MainFixtureCase):
    def test_full_order(self) -> None:
        cases = self.expected()
        self.assertEqual(len(cases), 18)
        for (sort, direction), want in cases.items():
            res = self.listed(sort=sort, sort_dir=direction, limit=1000)
            self.assertEqual(ids(res), want, (sort, direction))
            self.assertEqual(res["total"], 7)

    def test_paging_through(self) -> None:
        for (sort, direction), want in self.expected().items():
            got = []
            for offset in (0, 2, 4, 6):
                page = self.listed(sort=sort, sort_dir=direction, limit=2, offset=offset)
                self.assertEqual(page["total"], 7, (sort, direction, offset))
                got += ids(page)
            self.assertEqual(got, want, (sort, direction))

    def test_first_pages_differ(self) -> None:
        # Ошибка карточки: при сортировке страницы asc и desc давали один набор.
        sorts = ("id", *EXPECTED)
        self.assertEqual(len(sorts), 9)
        for sort in sorts:
            asc = set(ids(self.listed(sort=sort, sort_dir="asc", limit=2)))
            desc = set(ids(self.listed(sort=sort, sort_dir="desc", limit=2)))
            self.assertNotEqual(asc, desc, sort)

    def test_default_direction_is_asc(self) -> None:
        self.assertEqual(ids(self.listed(sort="priority", limit=1000)),
                         self.names(EXPECTED["priority"][0]))

    def test_waiting_for_count(self) -> None:
        want = {tid: 0 for tid in self.all_ids}
        want[self.by_name["fox"]] = 2
        want[self.by_name["charlie"]] = 1
        for kw in ({}, {"sort": "waiting_for_count"}, {"sort": "title", "sort_dir": "desc"}):
            res = self.listed(limit=1000, **kw)
            self.assertEqual({t["id"]: t["waiting_for_count"] for t in res["tasks"]}, want, kw)

    def test_with_health(self) -> None:
        delta, bravo = self.by_name["delta"], self.by_name["bravo"]
        for tid in (delta, bravo):
            self.assertEqual(store.task_health(store.get_task(self.conn, tid,
                                                              with_details=False)), "dead")
        for direction, want in (("asc", [delta, bravo]), ("desc", [bravo, delta])):
            res = self.listed(health="dead", sort="holder_hours", sort_dir=direction)
            self.assertEqual((ids(res), res["total"]), (want, 2), direction)
            self.assertEqual([t["waiting_for_count"] for t in res["tasks"]], [0, 0])
            pages = []
            for offset in (0, 1):
                page = self.listed(health="dead", sort="holder_hours", sort_dir=direction,
                                   limit=1, offset=offset)
                self.assertEqual(page["total"], 2)
                pages += ids(page)
            self.assertEqual(pages, want, direction)

    def test_order_ignored_with_sort(self) -> None:
        self.assertEqual(
            ids(self.listed(sort="priority", sort_dir="desc", order="created", limit=1000)),
            ids(self.listed(sort="priority", sort_dir="desc", limit=1000)))

    def test_without_sort_unchanged(self) -> None:
        base = ids(self.listed())
        self.assertEqual(ids(self.listed(sort_dir="desc")), base)
        self.assertEqual(ids(self.listed(sort="")), base)


class WaitingForEqualsDepsTests(Case):
    def test_same_as_deps_waiting_for(self) -> None:
        conn = self.conn
        x, y, z = self.new("X"), self.new("Y"), self.new("Z")
        x_hard, x_res, x_closed = self.new("ждёт X"), self.new("ресурс X"), self.new("закрыта")
        y_waiter, z_agent = self.new("ждёт Y"), self.new("предлагает Z")
        store.add_dep(conn, x_hard, x, "blocks", created_by="me")
        _insert_resource_block(conn, x_res, x)
        store.add_dep(conn, x_closed, x, "blocks", created_by="me")
        store.add_dep(conn, y_waiter, y, "blocks", created_by="me")
        store.add_dep(conn, z_agent, z, "blocks", created_by="agent:dsh")
        self.assertEqual(conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?",
            (z_agent, z)).fetchone()[0], "suggested-blocks")
        self.sql("UPDATE tasks SET status = 'done' WHERE id IN (?, ?)", x_closed, y)
        res = store.list_tasks(conn, include_closed=True, limit=1000)
        got = {t["id"]: t["waiting_for_count"] for t in res["tasks"]}
        self.assertEqual(len(got), 8)
        for tid, count in got.items():
            self.assertEqual(count, len(deps.waiting_for(conn, tid)), tid)
        self.assertEqual((got[x], got[y], got[z]), (2, 0, 0))


class StatusTitleTests(Case):
    def test_by_title_not_code(self) -> None:
        by_status = {s: self.new(s) for s in ("open", "review", "blocked", "in_progress")}
        for status, tid in by_status.items():
            self.sql("UPDATE tasks SET status = ? WHERE id = ?", status, tid)
        res = store.list_tasks(self.conn, sort="status_title", sort_dir="asc")
        self.assertEqual(ids(res), [by_status[s] for s in
                                    ("in_progress", "blocked", "review", "open")])


class ProjectEmptyLastTests(Case):
    def test_empty_last_both_directions(self) -> None:
        beta, alpha = self.new("b", project="beta"), self.new("a", project="alpha")
        null, blank = self.new("n", project="demo"), self.new("e", project="demo")
        self.sql("UPDATE tasks SET project = NULL, updated_at = ? WHERE id = ?", _ago(1), null)
        self.sql("UPDATE tasks SET project = '', updated_at = ? WHERE id = ?", _ago(2), blank)
        cases = {"asc": [alpha, beta, null, blank], "desc": [beta, alpha, null, blank]}
        for direction, want in cases.items():
            res = store.list_tasks(self.conn, sort="project", sort_dir=direction)
            self.assertEqual((ids(res), res["total"]), (want, 4), direction)
            pages = []
            for offset in (0, 2):
                page = store.list_tasks(self.conn, sort="project", sort_dir=direction,
                                        limit=2, offset=offset)
                self.assertEqual(page["total"], 4)
                pages += ids(page)
            self.assertEqual(pages, want, direction)


class EqualKeysTests(Case):
    def test_equal_priority_and_updated(self) -> None:
        same = [self.new(f"t{i}") for i in range(3)]
        for tid in same:
            self.sql("UPDATE tasks SET priority = 2, updated_at = '2026-09-20T10:00:00Z' "
                     "WHERE id = ?", tid)
        for direction in ("asc", "desc"):
            self.assertEqual(ids(store.list_tasks(self.conn, sort="priority",
                                                  sort_dir=direction)), sorted(same), direction)

    def test_equal_priority_fresher_first(self) -> None:
        old, fresh = self.new("old"), self.new("fresh")
        self.sql("UPDATE tasks SET priority = 2, updated_at = ? WHERE id = ?", _ago(5), old)
        self.sql("UPDATE tasks SET priority = 2, updated_at = ? WHERE id = ?", _ago(1), fresh)
        for direction in ("asc", "desc"):
            self.assertEqual(ids(store.list_tasks(self.conn, sort="priority",
                                                  sort_dir=direction)), [fresh, old], direction)


class ValidationTests(Case):
    def test_bad_values(self) -> None:
        self.new("одна")
        before = self.count_tasks()
        for sort in ("bogus", "holder_at", "status", "id; DROP TABLE tasks",
                     "priority DESC, (SELECT 1)"):
            with self.assertRaises(errors.BadArgument, msg=sort) as ctx:
                store.list_tasks(self.conn, sort=sort)
            self.assertTrue(str(ctx.exception).startswith("sort:"), str(ctx.exception))
        for sort_dir in ("up", "DESC", "desc; --", "asc, id"):
            with self.assertRaises(errors.BadArgument, msg=sort_dir) as ctx:
                store.list_tasks(self.conn, sort="id", sort_dir=sort_dir)
            self.assertTrue(str(ctx.exception).startswith("dir:"), str(ctx.exception))
            self.assertNotIn("sort_dir", str(ctx.exception))
        for kw in INJECTIONS:
            with self.assertRaises(errors.BadArgument, msg=kw):
                store.list_tasks(self.conn, **kw)
        self.assertEqual(self.count_tasks(), before)


class HttpTests(MainFixtureCase):
    def get(self, query: dict):
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            return server.handle("GET", "/api/tasks", query, {}, authed=True)

    def test_same_as_store(self) -> None:
        status, res = self.get({"sort": "holder_hours", "dir": "desc", "limit": "2",
                                "offset": "2"})
        direct = self.listed(sort="holder_hours", sort_dir="desc", limit=2, offset=2)
        self.assertEqual(status, 200)
        self.assertEqual((res["total"], ids(res)), (direct["total"], ids(direct)))
        self.assertEqual(ids(res), self.names("alfa golf"))

    def test_bad_dir_is_400(self) -> None:
        with self.assertRaises(errors.BadArgument) as ctx:
            self.get({"dir": "up"})
        status, _message, code, _hint = server.error_response(ctx.exception)
        self.assertEqual((status, code), (400, "bad_argument"))

    def test_injection_rejected(self) -> None:
        before = self.count_tasks()
        with self.assertRaises(errors.BadArgument):
            self.get({"sort": "id; DROP TABLE tasks"})
        self.assertEqual(self.count_tasks(), before)


class OwnerSortTests(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()

    def test_owner_filter_kept(self) -> None:
        conn = self.conn
        a = self.make("A", project="demo", as_owner="ann")["id"]
        b = self.make("B", project="demo", as_owner="bob")["id"]
        for i in range(3):
            w = self.make(f"ждёт B {i}", project="demo", as_owner="bob")["id"]
            store.add_dep(conn, w, b, "blocks", created_by="me")
        for sort in ("waiting_for_count", "blocked_count"):
            res = store.list_tasks(conn, sort=sort, sort_dir="desc", as_owner="ann")
            self.assertEqual((ids(res), res["total"]), ([a], 1), sort)
