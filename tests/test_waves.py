"""`deps.waves` — расчёт волн планировщика роя (listik-aoid, порция a)."""
from __future__ import annotations

import json
import sqlite3
import sys
from unittest import mock

from listik import deps, errors, harnesses_store, routes_store, stage_launch, store
from tests.helpers import TempDbTestCase
from tests.test_resource_blocks import _insert_resource_block


def _task(conn, key, *, project="demo", scope=None, route="nano", priority=2,
          worktree=None, stage=None, created_at=None):
    if scope is None:
        scope = (f"pkg/{key}.py",)
    task = store.create_task(conn, title=f"task {key}", project=project, priority=priority)
    tid = task["id"]
    # Карточка роя: маршрут со снимком `launch_driver='swarm'` (маршрута `nano` в базе нет).
    conn.execute("UPDATE tasks SET launch_route = ?, launch_driver = ? WHERE id = ?",
                 (route, "swarm" if route else None, tid))
    if worktree is not None:
        conn.execute("UPDATE tasks SET worktree = ? WHERE id = ?", (worktree, tid))
    if stage is not None:
        conn.execute("UPDATE tasks SET stage = ? WHERE id = ?", (stage, tid))
    if created_at is not None:
        conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (created_at, tid))
    if route is not None:
        store.update_task(conn, tid, write_scope=list(scope))
    else:
        conn.execute("UPDATE tasks SET write_scope = ? WHERE id = ?", (json.dumps(list(scope)), tid))
    conn.commit()
    return tid


def _no_route(conn, key, **kwargs):
    kwargs.setdefault("scope", ("pkg/unused.py",))
    task = store.create_task(conn, title=f"task {key}", project=kwargs.get("project", "demo"),
                              priority=kwargs.get("priority", 2))
    tid = task["id"]
    if kwargs.get("created_at") is not None:
        conn.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (kwargs["created_at"], tid))
    conn.commit()
    return tid


def _skill_task(conn, key, **kwargs):
    """Не-роевая карточка: маршрут режима скила (`skillish`, pipeline), без снимка."""
    if not conn.execute("SELECT 1 FROM routes WHERE key = 'skillish'").fetchone():
        routes_store.create_route(conn, key="skillish", kind="pipeline", title="Скил")
    tid = _task(conn, key, **kwargs)
    conn.execute("UPDATE tasks SET launch_route = 'skillish', launch_driver = NULL WHERE id = ?",
                 (tid,))
    conn.commit()
    return tid


def _mentioned(result: dict) -> set[str]:
    """Все id в ответе `waves`, кроме значений `blocked`."""
    ids = {tid for wave in result["waves"] for tid in wave}
    ids |= {tid for cycle in result["cycles"] for tid in cycle}
    ids |= {tid for pair in result["resource_blocks"] for tid in pair}
    ids |= set(result["unroutable"]) | set(result["unscoped"])
    ids |= set(result["blocked"]) | set(result["tasks"])
    return ids


class WavesTests(TempDbTestCase):
    def test_chain(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",))
        b = _task(self.conn, "b", scope=("pkg/b.py",))
        c = _task(self.conn, "c", scope=("pkg/c.py",))
        store.add_dep(self.conn, b, a, "blocks")
        store.add_dep(self.conn, c, b, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a], [b], [c]])
        self.assertEqual(result["cycles"], [])
        self.assertEqual(result["unroutable"], [])
        self.assertEqual(result["unscoped"], [])
        self.assertEqual(result["resource_blocks"], [])
        self.assertEqual(result["blocked"], {})

    def test_diamond(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        d = _task(self.conn, "d", scope=("pkg/d.py",), priority=3)
        store.add_dep(self.conn, b, a, "blocks")
        store.add_dep(self.conn, c, a, "blocks")
        store.add_dep(self.conn, d, b, "blocks")
        store.add_dep(self.conn, d, c, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a], [b, c], [d]])

    def test_independent(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, b, c]])

        self.conn.execute("UPDATE tasks SET priority = 2 WHERE id = ?", (a,))
        self.conn.execute("UPDATE tasks SET priority = 0 WHERE id = ?", (c,))
        self.conn.commit()
        result2 = deps.waves(self.conn, project="demo")
        self.assertEqual(result2["waves"], [[c, b, a]])

    def test_cycle(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        d = _task(self.conn, "d", scope=("pkg/d.py",), priority=3)
        for issue, dep in ((a, c), (b, a), (c, b)):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', 'human')", (issue, dep))
        self.conn.commit()
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["cycles"], [[a, b, c]])
        self.assertEqual(result["waves"], [])
        self.assertEqual(result["resource_blocks"], [])

    def test_scope_intersection(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, b], [c]])
        self.assertEqual(result["resource_blocks"], [[a, c]])

    def test_directory_covers_file(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py", "pkg"), priority=0)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a], [b]])
        self.assertEqual(result["resource_blocks"], [[a, b]])

    def test_similar_prefix_no_intersection(self) -> None:
        a = _task(self.conn, "a", scope=("docs",), priority=0)
        b = _task(self.conn, "b", scope=("docs-old/x.md",), priority=1)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, b]])
        self.assertEqual(result["resource_blocks"], [])

    def test_shift_pulls_dependents(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/alpha.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/gamma.py",), priority=2)
        store.add_dep(self.conn, c, b, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a], [b], [c]])

    def test_three_on_one_file(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/alpha.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a], [b], [c]])
        self.assertEqual(result["resource_blocks"], [[a, b], [a, c], [b, c]])

    def test_worktree_key(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0, worktree="main")
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1, worktree="main")
        c = _task(self.conn, "c", scope=("pkg/gamma.py",), priority=2)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, c], [b]])
        self.assertEqual(result["resource_blocks"], [[a, b]])

    def test_empty_worktree_no_conflict(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0, stage="s3-impl")
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1, stage="s3-impl")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, b]])

    def test_worktree_outside_writing_stage(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0, worktree="main",
                   stage="s1-spec")
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1, worktree="main",
                   stage="s1-spec")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, b]])

    def test_worktree_path_is_canonical(self) -> None:
        wt = str(self.tmp_path / "wt")
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0, worktree=wt)
        b = _task(self.conn, "b", scope=("pkg/beta.py",), priority=1, worktree=wt + "/")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a], [b]])

    def test_unroutable(self) -> None:
        a = _no_route(self.conn, "a", priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        store.add_dep(self.conn, b, a, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[c]])
        self.assertEqual(result["unroutable"], [])
        self.assertEqual(result["blocked"], {b: a})

    def test_unscoped(self) -> None:
        a = _task(self.conn, "a", scope=(), priority=0, stage="s3-impl")
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        store.add_dep(self.conn, b, a, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[c]])
        self.assertEqual(result["unscoped"], [a])
        self.assertEqual(result["blocked"], {b: a})
        self.assertNotIn(a, result["unroutable"])

    def test_spec_and_review_need_no_write_scope(self) -> None:
        spec = _task(self.conn, "spec", scope=(), priority=0, stage="s1-spec")
        fresh = _task(self.conn, "fresh", scope=(), priority=1)
        review = _task(self.conn, "review", scope=(), priority=2, stage="s2-review")
        impl = _task(self.conn, "impl", scope=(), priority=3, stage="s3-impl")
        judge = _task(self.conn, "judge", scope=(), priority=4, stage="s4-judge")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[spec, fresh, review]])
        self.assertEqual(result["unscoped"], [impl, judge])

    def test_unroutable_and_unscoped(self) -> None:
        a = _no_route(self.conn, "a", priority=0, scope=())
        self.conn.execute("UPDATE tasks SET write_scope = '[]' WHERE id = ?", (a,))
        self.conn.commit()
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["unroutable"], [])
        self.assertEqual(result["unscoped"], [])

    def test_blocked_two_levels(self) -> None:
        a = _no_route(self.conn, "a", priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        d = _task(self.conn, "d", scope=("pkg/d.py",), priority=3)
        store.add_dep(self.conn, b, a, "blocks")
        store.add_dep(self.conn, c, b, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[d]])
        self.assertEqual(result["unroutable"], [])
        self.assertEqual(result["blocked"], {b: a, c: b})

    def test_blocked_visible_immediately(self) -> None:
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=0)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=1)
        a = _no_route(self.conn, "a", priority=2)
        x = _no_route(self.conn, "x", priority=3)
        store.add_dep(self.conn, b, x, "blocks")
        store.add_dep(self.conn, c, a, "blocks")
        store.add_dep(self.conn, c, b, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["blocked"], {b: x, c: b})

    def test_blocked_reason_not_recomputed(self) -> None:
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        a = _no_route(self.conn, "a", priority=2)
        x = _no_route(self.conn, "x", priority=3)
        store.add_dep(self.conn, b, x, "blocks")
        store.add_dep(self.conn, c, a, "blocks")
        store.add_dep(self.conn, c, b, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["blocked"], {c: a, b: x})

    def test_blocked_keys_ordered_by_o_not_by_pass(self) -> None:
        # O = [c, b, a] (по приоритету): c ждёт b, b ждёт a (a без маршрута).
        # На первом проходе блокируется b (кандидат a), на втором — c (кандидат b):
        # порядок вставки в словарь — [b, c], а обязан быть порядок O — [c, b].
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        a = _no_route(self.conn, "a", priority=2)
        store.add_dep(self.conn, c, b, "blocks")
        store.add_dep(self.conn, b, a, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["blocked"], {c: b, b: a})
        self.assertEqual(list(result["blocked"].keys()), [c, b],
                          "ключи blocked должны идти по O (c раньше b), а не по "
                          "порядку проходов цикла «до устойчивости»")

    def test_closed_dependency(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        store.add_dep(self.conn, b, a, "blocks")
        store.add_dep(self.conn, c, b, "blocks")
        self.conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (a,))
        self.conn.commit()
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[b], [c]])
        self.assertNotIn(a, result["tasks"])
        for key in ("waves", "unroutable", "unscoped", "blocked", "resource_blocks"):
            flat = result[key]
            if isinstance(flat, dict):
                self.assertNotIn(a, flat)
                self.assertNotIn(a, flat.values())
            else:
                self.assertNotIn(a, [x for group in flat for x in
                                     (group if isinstance(group, list) else [group])])

    def test_closing_removes_conflict(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/alpha.py",), priority=1)
        self.conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (a,))
        self.conn.commit()
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[b]])
        self.assertEqual(result["resource_blocks"], [])

    def test_external_blocker(self) -> None:
        b = _task(self.conn, "b", project="demo", scope=("pkg/b.py",), priority=0)
        x = _task(self.conn, "x", project="other", scope=("pkg/x.py",), priority=0)
        store.add_dep(self.conn, b, x, "blocks")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [])
        self.assertEqual(result["blocked"], {b: x})
        self.conn.execute("UPDATE tasks SET status = 'done' WHERE id = ?", (x,))
        self.conn.commit()
        result2 = deps.waves(self.conn, project="demo")
        self.assertEqual(result2["waves"], [[b]])

    def test_stage_filter(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0, stage="s3-impl")
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1, stage="s3-impl")
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2, stage="s1-spec")
        d = _task(self.conn, "d", scope=("pkg/d.py",), priority=3, stage="s3-impl")
        store.add_dep(self.conn, b, a, "blocks")
        store.add_dep(self.conn, d, c, "blocks")
        result = deps.waves(self.conn, project="demo", stage="s3-impl")
        self.assertEqual(result["waves"], [[a], [b]])
        self.assertEqual(result["blocked"], {d: c})
        self.assertNotIn(c, result["tasks"])

        result_all = deps.waves(self.conn, project="demo")
        self.assertEqual(result_all["waves"], [[a, c], [b, d]])

    def test_old_resource_block_no_effect(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        _insert_resource_block(self.conn, b, a)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[a, b]])
        self.assertEqual(result["resource_blocks"], [])
        rows = self.conn.execute(
            "SELECT issue_id, depends_on, dep_type FROM deps WHERE dep_type = 'resource-blocks'"
        ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_does_not_write(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/alpha.py",), priority=1)
        before = self.conn.execute(
            "SELECT issue_id, depends_on, dep_type FROM deps ORDER BY 1,2,3").fetchall()
        deps.waves(self.conn, project="demo")
        after = self.conn.execute(
            "SELECT issue_id, depends_on, dep_type FROM deps ORDER BY 1,2,3").fetchall()
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])
        self.assertFalse(self.conn.in_transaction)

    def test_determinism(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/alpha.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/alpha.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/alpha.py",), priority=2)
        results = [deps.waves(self.conn, project="demo") for _ in range(10)]
        for r in results[1:]:
            self.assertEqual(r, results[0])

    def test_archived_and_cancelled(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        self.conn.execute("UPDATE tasks SET archived = 1 WHERE id = ?", (a,))
        self.conn.execute("UPDATE tasks SET status = 'cancelled' WHERE id = ?", (b,))
        self.conn.commit()
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[c]])
        self.assertNotIn(a, result["tasks"])
        self.assertNotIn(b, result["tasks"])

    def test_empty_project(self) -> None:
        result = deps.waves(self.conn, project="nope")
        self.assertEqual(result["waves"], [])
        self.assertEqual(result["cycles"], [])
        self.assertEqual(result["unroutable"], [])
        self.assertEqual(result["unscoped"], [])
        self.assertEqual(result["blocked"], {})
        self.assertEqual(result["resource_blocks"], [])
        self.assertEqual(result["tasks"], {})

    def test_no_project(self) -> None:
        with self.assertRaises(errors.BadArgument):
            deps.waves(self.conn, project="")

    def test_tasks_view(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=2)
        store.add_dep(self.conn, b, a, "blocks")
        store.add_dep(self.conn, c, a, "blocks")
        result = deps.waves(self.conn, project="demo")
        for tid in (a, b, c):
            self.assertIn(tid, result["tasks"])
            info = result["tasks"][tid]
            self.assertIn("title", info)
            self.assertIsInstance(info["write_scope"], list)
            self.assertEqual(info["launch_route"], "nano")


class SwarmOnlyWavesTests(TempDbTestCase):
    """Рабочее множество `waves` — только карточки роя (listik-w7ge)."""

    def _blocks(self, issue: str, dep: str) -> None:
        self.conn.execute("INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                          "VALUES(?, ?, 'blocks', 'human')", (issue, dep))
        self.conn.commit()

    def test_skill_cards_absent_everywhere(self) -> None:
        c = _task(self.conn, "c", scope=("pkg/shared.py",), priority=0)
        scoped = _skill_task(self.conn, "s1", scope=("pkg/shared.py",), priority=1)
        unscoped = _skill_task(self.conn, "s2", scope=(), priority=2, stage="s3-impl")
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [[c]])
        self.assertEqual(_mentioned(result) & {scoped, unscoped}, set())
        self.assertEqual(result["unscoped"], [])
        self.assertEqual(result["resource_blocks"], [])

    def test_foreign_cycle_does_not_stop_swarm(self) -> None:
        c = _task(self.conn, "c", scope=("pkg/c.py",), priority=0)
        n1 = _skill_task(self.conn, "n1", priority=1)
        n2 = _skill_task(self.conn, "n2", priority=2)
        self._blocks(n1, n2)
        self._blocks(n2, n1)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["cycles"], [])
        self.assertEqual(result["waves"], [[c]])

    def test_mixed_cycle_blocks_swarm_card(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        n = _skill_task(self.conn, "n", priority=1)
        self._blocks(a, n)
        self._blocks(n, a)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["cycles"], [])
        self.assertEqual(result["blocked"], {a: n})
        self.assertEqual(result["waves"], [])

    def test_swarm_cycle_still_stops(self) -> None:
        a = _task(self.conn, "a", scope=("pkg/a.py",), priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        self._blocks(a, b)
        self._blocks(b, a)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["waves"], [])
        self.assertEqual(result["cycles"], [[a, b]])

    def test_unroutable_always_empty(self) -> None:
        a = _no_route(self.conn, "a", priority=0)
        b = _task(self.conn, "b", scope=("pkg/b.py",), priority=1)
        result = deps.waves(self.conn, project="demo")
        self.assertEqual(result["unroutable"], [])
        self.assertNotIn(a, _mentioned(result))
        self.assertEqual(result["waves"], [[b]])

    def test_routes_unavailable(self) -> None:
        harnesses_store.create(self.conn, {"key": "probe", "label": "probe",
                                           "argv": [sys.executable, "-c", "print(1)"]})
        routes_store.create_route(
            self.conn, key="roy", kind="swarm", title="Рой",
            roles={role: {"harness": "probe"} for _, role in stage_launch.STAGE_ROLES})
        snap = _task(self.conn, "snap", scope=("pkg/snap.py",), priority=0)
        live = _task(self.conn, "live", scope=("pkg/live.py",), priority=1, route="roy")
        self.conn.execute("UPDATE tasks SET launch_driver = NULL WHERE id = ?", (live,))
        self.conn.commit()
        self.assertIn(live, deps.waves(self.conn, project="demo")["tasks"])

        with mock.patch.object(routes_store, "list_routes",
                               side_effect=sqlite3.DatabaseError("база недоступна")):
            result = deps.waves(self.conn, project="demo")
        self.assertIn(snap, result["tasks"])
        self.assertNotIn(live, result["tasks"])


if __name__ == "__main__":
    import unittest as _u
    _u.main()
