"""Валидация и запись `read_scope`/`write_scope`: модуль `scope.py`, `update_task`,
CLI (`listik set`/`show`), HTTP (`PATCH /api/tasks/{id}`) и MCP (`listik_update`)."""
from __future__ import annotations

import json
import os
import subprocess
import sys

from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from tests.test_owner_http import OwnerHttpCase, LOCAL_CONFIG

from listik import errors, mcp, scope, store


class NormalizeAcceptsTests(TempDbTestCase):
    def test_example_list(self) -> None:
        value = ["listik/store.py", "docs/", "./web/src", "a//b", "a/./b", "docs"]
        self.assertEqual(
            scope.normalize_scope(value, field="write_scope"),
            ["listik/store.py", "docs", "web/src", "a/b"],
        )

    def test_empty_list(self) -> None:
        self.assertEqual(scope.normalize_scope([], field="write_scope"), [])

    def test_strips_edges(self) -> None:
        self.assertEqual(scope.normalize_scope([" a/b "], field="write_scope"), ["a/b"])

    def test_collapses_dotdot(self) -> None:
        self.assertEqual(scope.normalize_scope(["a/b/../c"], field="write_scope"), ["a/c"])


class NormalizeRejectsTests(TempDbTestCase):
    def _assert_bad(self, value, field="write_scope"):
        with self.assertRaises(errors.BadArgument) as cm:
            scope.normalize_scope(value, field=field)
        self.assertIn(field, str(cm.exception))
        self.assertEqual(errors.code_of(cm.exception), "bad_argument")

    def test_rejected_elements(self) -> None:
        cases = [
            "/etc/hosts", "~/x", "C:x", "C:\\x", "a\\b", "../x", "a/../../x",
            "..", ".", "", "   ", "*.py", "web/**", "src/?.py", "a[1]",
            "a\x00b", "a\nb", 42, None,
        ]
        for case in cases:
            with self.subTest(case=case):
                self._assert_bad([case])

    def test_rejected_non_lists(self) -> None:
        for case in ("a,b", {"a": 1}, 7, None):
            with self.subTest(case=case):
                self._assert_bad(case)


class UpdateTaskScopeTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="T", project="demo")["id"]

    def test_normalizes_and_dedupes(self) -> None:
        store.update_task(self.conn, self.task_id,
                          write_scope=["docs/", "docs", "./listik/store.py"], actor="автор")
        task = store.get_task(self.conn, self.task_id)
        self.assertEqual(task["write_scope"], ["docs", "listik/store.py"])

    def test_unchanged_on_same_value(self) -> None:
        store.update_task(self.conn, self.task_id, write_scope=["docs"], actor="автор")
        out = store.update_task(self.conn, self.task_id, write_scope=["docs"], actor="автор")
        self.assertTrue(out["unchanged"])

    def test_empty_clears_scope(self) -> None:
        store.update_task(self.conn, self.task_id, read_scope=["docs"], actor="автор")
        store.update_task(self.conn, self.task_id, read_scope=[], actor="автор")
        task = store.get_task(self.conn, self.task_id)
        self.assertEqual(task["read_scope"], [])

    def test_other_fields_untouched(self) -> None:
        store.update_task(self.conn, self.task_id, labels=["a"], actor="автор")
        store.update_task(self.conn, self.task_id, write_scope=["docs"], actor="автор")
        task = store.get_task(self.conn, self.task_id)
        self.assertEqual(task["labels"], ["a"])

    def test_string_instead_of_list_rejected(self) -> None:
        with self.assertRaises(errors.BadArgument):
            store.update_task(self.conn, self.task_id, write_scope="a,b", actor="автор")

    def test_dotdot_rejected_and_db_unchanged(self) -> None:
        before = self.conn.execute(
            "SELECT write_scope FROM tasks WHERE id=?", (self.task_id,)).fetchone()[0]
        with self.assertRaises(errors.BadArgument):
            store.update_task(self.conn, self.task_id, write_scope=["../x"], actor="автор")
        after = self.conn.execute(
            "SELECT write_scope FROM tasks WHERE id=?", (self.task_id,)).fetchone()[0]
        self.assertEqual(before, after)

    def test_no_event_on_scope_change(self) -> None:
        before = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=?", (self.task_id,)).fetchone()[0]
        store.update_task(self.conn, self.task_id, write_scope=["docs"], actor="автор")
        after = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id=?", (self.task_id,)).fetchone()[0]
        self.assertEqual(before, after)


class GuardStillClosedTests(TempDbTestCase):
    def test_generation_and_dispatch_id_not_updatable(self) -> None:
        task_id = store.create_task(self.conn, title="T", project="demo")["id"]
        before = self.conn.execute(
            "SELECT dispatch_id, generation FROM tasks WHERE id=?", (task_id,)).fetchone()
        out = store.update_task(self.conn, task_id, generation=3, dispatch_id="d", actor="автор")
        self.assertTrue(out["unchanged"])
        after = self.conn.execute(
            "SELECT dispatch_id, generation FROM tasks WHERE id=?", (task_id,)).fetchone()
        self.assertEqual(before, after)


class CliScopeTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="T", project="demo")["id"]

    def test_set_and_show(self) -> None:
        p = self._run("set", self.task_id, "write_scope=listik/store.py,docs/")
        self.assertEqual(p.returncode, 0, p.stderr)

        p = self._run("show", self.task_id, "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        data = json.loads(p.stdout)
        self.assertEqual(data["write_scope"], ["listik/store.py", "docs"])

        p = self._run("show", self.task_id)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("правит:", p.stdout)
        self.assertIn("listik/store.py", p.stdout)
        self.assertIn("docs", p.stdout)

    def test_set_empty_clears(self) -> None:
        self._run("set", self.task_id, "write_scope=listik/store.py")
        p = self._run("set", self.task_id, "write_scope=")
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self._run("show", self.task_id)
        self.assertNotIn("правит:", p.stdout)

    def test_set_absolute_path_rejected(self) -> None:
        p = self._run("set", self.task_id, "read_scope=/abs", "--json")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('"code": "bad_argument"', p.stdout + p.stderr)
        self.assertNotIn("Traceback", p.stderr)

    def test_set_glob_rejected(self) -> None:
        p = self._run("set", self.task_id, "write_scope=a/*.py")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("Traceback", p.stderr)


class HttpScopeTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def test_patch_normalizes(self) -> None:
        task = self.make_task(title="T")
        status, payload = self.api("PATCH", f"/api/tasks/{task['id']}",
                                   body={"write_scope": ["docs/", "docs"]})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["write_scope"], ["docs"])

    def test_patch_string_rejected(self) -> None:
        task = self.make_task(title="T")
        status, payload = self.api("PATCH", f"/api/tasks/{task['id']}",
                                   body={"write_scope": "docs"})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")

    def test_patch_dotdot_rejected_and_unchanged(self) -> None:
        task = self.make_task(title="T")
        status, payload = self.api("PATCH", f"/api/tasks/{task['id']}",
                                   body={"read_scope": ["../x"]})
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], "bad_argument")
        got = self.get_task(task["id"])
        self.assertEqual(got["read_scope"], [])

    def test_generation_not_updatable_over_http(self) -> None:
        task = self.make_task(title="T")
        status, payload = self.api("PATCH", f"/api/tasks/{task['id']}",
                                   body={"generation": 9})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["data"]["generation"], 0)


class McpScopeTests(TempDbTestCase):
    def test_update_normalizes(self) -> None:
        task_id = store.create_task(self.conn, title="T", project="demo")["id"]
        out = mcp.call_tool("listik_update",
                            {"id": task_id, "fields": {"read_scope": ["web/src/", "web/src"]}},
                            conn=self.conn)
        self.assertEqual(out["read_scope"], ["web/src"])

    def test_update_rejects_absolute(self) -> None:
        task_id = store.create_task(self.conn, title="T", project="demo")["id"]
        with self.assertRaises(errors.BadArgument):
            mcp.call_tool("listik_update",
                          {"id": task_id, "fields": {"write_scope": ["/abs"]}},
                          conn=self.conn)
