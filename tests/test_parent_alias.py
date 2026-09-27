"""Старое имя связи `parent` читается как `parent-child` во всех выборках детей
и родителя (listik-p4lt): `children_open`/`can_finish`, `children[]`, `lint`,
`idle_hours`, `sync_portions`.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from listik import deps, store

from tests.helpers import TempDbTestCase


def _ago(hours: float) -> str:
    """Метка времени в прошлом в том же формате, что пишет `store.now_iso`."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


class ParentAliasTests(TempDbTestCase):
    def new(self, title: str = "T", project: str = "demo", **kw) -> str:
        return store.create_task(self.conn, title=title, project=project, **kw)["id"]

    def pair(self) -> tuple[str, str]:
        """Родитель P и ребёнок C по старому имени `parent`."""
        p, c = self.new("P"), self.new("C")
        store.add_dep(self.conn, c, p, "parent")
        return p, c

    def test_s1_children_open_and_can_finish(self) -> None:
        p, c = self.pair()
        state = deps.ready(self.conn, p)
        self.assertEqual([x["id"] for x in state["children_open"]], [c])
        self.assertEqual(state["children_open"][0]["dep_type"], "parent-child")
        self.assertIs(state["can_finish"], False)
        self.assertNotIn(p, [t["id"] for t in deps.ready_tasks(self.conn, project="demo")])
        self.assertEqual(deps.parent(self.conn, c)["id"], p)
        self.assertEqual(store.parent_card(self.conn, c)["id"], p)
        self.assertIs(store.get_task(self.conn, p)["has_portions"], True)

    def test_s2_children_in_card(self) -> None:
        p, c = self.pair()
        self.assertEqual([x["id"] for x in store.get_task(self.conn, p)["children"]], [c])

    def test_s3_both_types_listed_once(self) -> None:
        p, c = self.new("P"), self.new("C")
        store.add_dep(self.conn, c, p, "parent-child")
        store.add_dep(self.conn, c, p, "parent")
        rows = self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?", (c, p)).fetchall()
        self.assertEqual(sorted(r["dep_type"] for r in rows), ["parent", "parent-child"])
        self.assertEqual([x["id"] for x in deps.ready(self.conn, p)["children_open"]], [c])
        self.assertEqual([x["id"] for x in store.get_task(self.conn, p)["children"]], [c])

    def test_s4_relates_to_is_not_a_child(self) -> None:
        p, r = self.new("P"), self.new("R")
        store.add_dep(self.conn, r, p, "relates-to")
        state = deps.ready(self.conn, p)
        self.assertNotIn(r, [x["id"] for x in state["children_open"]])
        self.assertNotIn(r, [x["id"] for x in store.get_task(self.conn, p)["children"]])
        self.assertIs(state["can_finish"], True)

    def test_s5_lint_sees_parent_child(self) -> None:
        p = self.new("P", project="p", spec_path=str(self.tmp_path / "s.md"))
        (self.tmp_path / f"{p}.a.md").write_text("x", encoding="utf-8")

        def rules() -> list[str]:
            return [i["rule"] for i in store.lint(self.conn, "p")["items"] if i["id"] == p]

        self.assertIn("portion_files_without_cards", rules())
        store.add_dep(self.conn, self.new("C", project="p"), p, "parent")
        self.assertNotIn("portion_files_without_cards", rules())

    def test_s6_idle_follows_parent_child(self) -> None:
        p, c = self.new("P"), self.new("C")
        self.conn.execute("INSERT INTO deps(issue_id, depends_on, dep_type) VALUES(?,?,?)",
                          (c, p, "parent"))
        self.conn.execute(
            "UPDATE tasks SET status = 'in_progress', holder = 'agent:dsh', holder_at = ?, "
            "started_at = ? WHERE id = ?", (_ago(2), _ago(2), p))
        self.conn.execute(
            "UPDATE tasks SET status = 'in_progress', holder = 'agent:dsh', holder_at = ? "
            "WHERE id = ?", (_ago(1 / 60.0), c))
        self.conn.commit()
        self.assertLess(store.get_task(self.conn, p)["idle_hours"], 0.25)

    def test_s7_sync_portions_reuses_parent_child(self) -> None:
        p = self.new("P")
        store.update_task(self.conn, p, spec_path=str(self.tmp_path / f"{p}.md"))
        for letter in "ab":
            (self.tmp_path / f"{p}.{letter}.md").write_text("x", encoding="utf-8")
        c = self.new("C", spec_path=str(self.tmp_path / f"{p}.a.md"))
        store.add_dep(self.conn, c, p, "parent")
        out = store.sync_portions(self.conn, p, actor="agent:test")
        self.assertEqual(len(out["created"]), 1)
        self.assertNotIn(c, out["created"])
        self.assertEqual({x["letter"]: x["id"] for x in out["portions"]}["a"], c)


if __name__ == "__main__":
    unittest.main()
