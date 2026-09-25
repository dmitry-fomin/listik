"""Эпик: тип и статус карточки выводятся из подзадач, в работу эпик не берётся
(listik-zr05, порция a; docs/API.md, «Эпик»)."""
from __future__ import annotations

import threading
from unittest import mock

from listik import actors, deps, errors, paths, server, store
from tests.helpers import TempDbTestCase
from tests.test_waves import _task

LISTIK = actors.resolve(store.EPIC_ACTOR)[0]


class EpicCase(TempDbTestCase):
    def new(self, title="T", **kw) -> str:
        kw.setdefault("project", "demo")
        return store.create_task(self.conn, title=title, **kw)["id"]

    def task(self, tid: str) -> dict:
        return store.get_task(self.conn, tid)

    def type_events(self, tid: str) -> list:
        return self.conn.execute(
            "SELECT from_value, to_value, note, actor FROM events "
            "WHERE task_id = ? AND kind = 'type_change' ORDER BY rowid", (tid,)).fetchall()

    def journal(self, tid: str) -> list:
        return self.conn.execute(
            "SELECT text, author FROM comments WHERE task_id = ? AND kind = 'journal' "
            "ORDER BY rowid", (tid,)).fetchall()

    def status_events(self, tid: str) -> list:
        return self.conn.execute(
            "SELECT to_value, actor FROM events WHERE task_id = ? AND kind = 'status' "
            "ORDER BY rowid", (tid,)).fetchall()

    def counts(self, tid: str) -> tuple[int, int]:
        ev = self.conn.execute("SELECT count(*) FROM events WHERE task_id = ?", (tid,)).fetchone()[0]
        cm = self.conn.execute("SELECT count(*) FROM comments WHERE task_id = ?", (tid,)).fetchone()[0]
        return ev, cm


class TypeTests(EpicCase):
    def test_child_makes_epic_and_removal_reverts(self) -> None:  # 1, 2, 10
        x = self.new()
        child = self.new(parent=x)
        self.assertEqual(self.task(x)["issue_type"], "epic")
        ev = self.type_events(x)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["from_value"], ev[0]["to_value"], ev[0]["note"]),
                         ("task", "epic", "подзадачи"))
        self.assertEqual(ev[0]["actor"], LISTIK)
        jr = self.journal(x)
        self.assertTrue(any("тип: task → epic" in r["text"] for r in jr))
        self.assertTrue(all(r["author"] == LISTIK for r in jr))

        store.remove_dep(self.conn, child, x, "parent-child")
        self.assertEqual(self.task(x)["issue_type"], "task")
        ev = self.type_events(x)
        self.assertEqual((ev[-1]["from_value"], ev[-1]["to_value"], ev[-1]["note"]),
                         ("epic", "task", "подзадач не осталось"))
        self.assertEqual(ev[-1]["actor"], LISTIK)
        jr = self.journal(x)
        self.assertIn("подзадач не осталось", jr[-1]["text"])
        self.assertEqual(jr[-1]["author"], LISTIK)

    def test_created_as_epic_stays_epic(self) -> None:  # 3
        x = self.new(issue_type="epic")
        child = self.new(parent=x)
        store.remove_dep(self.conn, child, x, "parent-child")
        self.assertEqual(self.task(x)["issue_type"], "epic")

    def test_manual_type_change_wins(self) -> None:  # 4
        x = self.new()
        child = self.new(parent=x)
        store.update_task(self.conn, x, issue_type="bug")
        store.update_task(self.conn, x, issue_type="epic")
        store.remove_dep(self.conn, child, x, "parent-child")
        self.assertEqual(self.task(x)["issue_type"], "epic")

    def test_delete_last_child_reverts_type(self) -> None:  # 9
        x = self.new(issue_type="feature")
        child = self.new(parent=x)
        self.assertEqual(self.task(x)["issue_type"], "epic")
        store.delete_task(self.conn, child)
        self.assertEqual(self.task(x)["issue_type"], "feature")

    def test_idempotent(self) -> None:  # 11
        x = self.new()
        child = self.new(parent=x)
        before = self.counts(x)
        store.add_dep(self.conn, child, x, "parent-child")
        store.sync_epic(self.conn, x)
        self.assertEqual(self.counts(x), before)

    def test_manual_type_event(self) -> None:  # 12
        x = self.new()
        store.update_task(self.conn, x, issue_type="task")
        self.assertEqual(self.type_events(x), [])
        store.update_task(self.conn, x, issue_type="bug", note="руками")
        ev = self.type_events(x)
        self.assertEqual(len(ev), 1)
        self.assertEqual((ev[0]["from_value"], ev[0]["to_value"], ev[0]["note"]),
                         ("task", "bug", "руками"))

    def test_missing_task_is_noop(self) -> None:
        store.sync_epic(self.conn, "нет-такой")


class StatusTests(EpicCase):
    def test_active_child_statuses(self) -> None:  # 6
        for status in ("in_progress", "blocked", "review"):
            with self.subTest(status=status):
                x = self.new()
                child = self.new(parent=x)
                store.update_task(self.conn, child, status=status)
                self.assertEqual(self.task(x)["status"], "in_progress")

    def test_open_child_with_stage_and_holder(self) -> None:  # 6
        x = self.new()
        a = self.new(parent=x)
        b = self.new(parent=x)
        store.update_task(self.conn, a, status="in_progress")
        self.assertEqual(self.task(x)["status"], "in_progress")
        store.update_task(self.conn, b, stage="s3-impl", holder="dsh")
        store.update_task(self.conn, a, status="done")
        self.assertEqual(self.task(b)["status"], "open")
        self.assertEqual(self.task(x)["status"], "open")

    def test_all_done(self) -> None:  # 7, 10
        x = self.new()
        a = self.new(parent=x)
        b = self.new(parent=x)
        store.update_task(self.conn, a, status="done")
        store.update_task(self.conn, b, status="done")
        task = self.task(x)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["stage"], "done")
        self.assertTrue(task["close_reason"].startswith("подзадачи закрыты:"))
        self.assertIn(a, task["close_reason"])
        self.assertIn(b, task["close_reason"])
        self.assertEqual(self.status_events(x)[-1]["actor"], LISTIK)
        jr = self.journal(x)[-1]
        self.assertEqual(jr["text"], task["close_reason"])
        self.assertEqual(jr["author"], LISTIK)

    def test_all_cancelled(self) -> None:  # 8, 10
        x = self.new()
        a = self.new(parent=x)
        b = self.new(parent=x)
        store.update_task(self.conn, a, status="cancelled")
        store.update_task(self.conn, b, status="cancelled")
        task = self.task(x)
        self.assertEqual(task["status"], "cancelled")
        self.assertTrue(task["close_reason"].startswith("подзадачи отменены:"))
        self.assertFalse(task["needs_owner"])
        self.assertEqual(self.status_events(x)[-1]["actor"], LISTIK)
        jr = self.journal(x)[-1]
        self.assertEqual(jr["text"], task["close_reason"])
        self.assertEqual(jr["author"], LISTIK)

    def test_resurrection(self) -> None:  # 14
        x = self.new()
        a = self.new(parent=x)
        store.update_task(self.conn, a, status="done")
        self.assertEqual((self.task(x)["status"], self.task(x)["stage"]), ("done", "done"))
        self.new(parent=x)
        task = self.task(x)
        self.assertEqual(task["status"], "open")
        self.assertFalse(task["stage"])
        self.assertFalse(task["close_reason"])

    def test_hook_error_is_swallowed(self) -> None:  # 15
        x = self.new()
        child = self.new(parent=x)
        with mock.patch("listik.store.sync_epic", side_effect=RuntimeError("x")):
            store.update_task(self.conn, child, status="in_progress")
        kinds = [r["kind"] for r in self.conn.execute(
            "SELECT kind FROM events WHERE task_id = ?", (child,))]
        self.assertIn("swarm_parent_error", kinds)


class NotTakenTests(EpicCase):
    def test_ready_waves_claim(self) -> None:  # 5
        x = _task(self.conn, "x")
        child = self.new(parent=x)
        ready_ids = [t["id"] for t in deps.ready_tasks(self.conn, project="demo")]
        self.assertNotIn(x, ready_ids)
        self.assertIn(child, ready_ids)
        waves = deps.waves(self.conn, project="demo")
        self.assertFalse(any(x in wave for wave in waves["waves"]))
        with self.assertRaises(errors.ListikError) as cm:
            store.claim(self.conn, x, holder="dsh")
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertIn(child, cm.exception.message)

    def test_force_and_only_open_children_listed(self) -> None:  # 13
        x = self.new()
        done = self.new(parent=x)
        live = self.new(parent=x)
        store.update_task(self.conn, done, status="done")
        with self.assertRaises(errors.ListikError) as cm:
            store.claim(self.conn, x, holder="dsh", force=True)
        self.assertEqual(cm.exception.code, errors.CONFLICT)
        self.assertIn(live, cm.exception.message)
        self.assertNotIn(done, cm.exception.message)

    def test_resource_edge_to_parent_removed(self) -> None:  # сценарий digest-cm1v
        p = _task(self.conn, "p", scope=("pkg/shared.py",))
        c = _task(self.conn, "c", scope=("pkg/shared.py",))
        store.add_dep(self.conn, c, p, "parent-child")
        self.conn.execute(
            "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
            "VALUES(?, ?, 'resource-blocks', 'old')", (c, p))
        self.conn.commit()
        deps.apply_resource_blocks(self.conn, project="demo")
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id = ? AND depends_on = ? "
            "AND dep_type = 'resource-blocks'", (c, p)).fetchone())
        waves = deps.waves(self.conn, project="demo")
        self.assertIn(c, waves["waves"][0])
        self.assertFalse(any(p in wave for wave in waves["waves"]))


class ClaimHttpTests(EpicCase):
    def setUp(self) -> None:
        super().setUp()
        self._saved = (paths.DB_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        server._conn_made = False
        server._conn_local = threading.local()

    def tearDown(self) -> None:
        conn = getattr(server._conn_local, "conn", None)
        if conn is not None:
            conn.close()
        paths.DB_PATH, server._conn_made, server._conn_local = self._saved
        super().tearDown()

    def test_claim_epic_is_409(self) -> None:  # 5
        x = self.new()
        child = self.new(parent=x)
        with self.assertRaises(server.ApiError) as cm:
            server.handle("POST", f"/api/tasks/{x}/claim", {}, {"holder": "dsh"}, authed=True)
        status, message, code = server.error_response(cm.exception)
        self.assertEqual(status, 409)
        self.assertEqual(code, errors.CONFLICT)
        self.assertIn(child, message)
