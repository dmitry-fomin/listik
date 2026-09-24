"""`listik lint` — несостыковки карточек проекта (listik-ugw8, порция c)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from listik import db as db_mod
from listik import errors, paths, server, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


class LintCase(TempDbTestCase):
    def new(self, title: str = "T", project: str = "p", **kw) -> str:
        return store.create_task(self.conn, title=title, project=project, **kw)["id"]

    def archive(self, tid: str) -> None:
        self.conn.execute("UPDATE tasks SET archived = 1 WHERE id = ?", (tid,))
        self.conn.commit()

    def found(self, rule: str, project: str = "p", **kw) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for item in store.lint(self.conn, project, **kw)["items"]:
            if item["rule"] == rule:
                out.setdefault(item["id"], []).append(item)
        return out


class InProgressNoHolderTests(LintCase):
    def test_rule(self) -> None:
        # s1-spec: лок рабочего дерева не мешает нескольким claim в одном проекте
        hit = self.new(status="in_progress", stage="s1-spec")
        claimed = self.new(status="in_progress", stage="s1-spec")
        store.claim(self.conn, claimed, holder="agent:a")
        opened = self.new()
        closed = self.new(status="in_progress")
        store.update_task(self.conn, closed, status="done")
        other = self.new(project="q", status="in_progress")
        arch = self.new(status="in_progress")
        self.archive(arch)
        released = self.new(stage="s1-spec")
        store.claim(self.conn, released, holder="agent:a")
        store.update_task(self.conn, released, holder="")

        found = self.found("in_progress_no_holder")
        self.assertEqual(set(found), {hit, released})
        self.assertIsNone(found[hit][0]["details"]["released_at"])
        self.assertTrue(found[released][0]["details"]["released_at"])
        self.assertEqual(found[hit][0]["message"], "в работе без держателя")
        for tid in (claimed, opened, closed, other, arch):
            self.assertNotIn(tid, found)

        store.claim(self.conn, hit, holder="agent:b")
        self.assertNotIn(hit, self.found("in_progress_no_holder"))


class DoneWithHolderTests(LintCase):
    def test_rule(self) -> None:
        hit = self.new()
        store.update_task(self.conn, hit, status="done")
        self.conn.execute("UPDATE tasks SET holder = 'x' WHERE id = ?", (hit,))
        clean = self.new()
        store.update_task(self.conn, clean, status="done")
        arch = self.new()
        store.update_task(self.conn, arch, status="cancelled")
        self.conn.execute("UPDATE tasks SET holder = 'x' WHERE id = ?", (arch,))
        self.archive(arch)
        found = self.found("done_with_holder")
        self.assertEqual(set(found), {hit})
        self.assertEqual(found[hit][0]["details"], {"holder": "x"})
        self.assertEqual(found[hit][0]["message"], "закрыта, но держатель не снят: x")


class StageWithoutDocsTests(LintCase):
    def test_rule(self) -> None:
        both = self.new(stage="s3-impl")
        no_journal = self.new(stage="s3-impl", spec_path="/x/spec.md")
        s1 = self.new(stage="s1-spec")
        full = self.new(stage="s2-review", spec_path="/x/s.md", journal_path="/x/j.md")
        closed = self.new(stage="s3-impl")
        store.update_task(self.conn, closed, status="done")
        other = self.new(project="q", stage="s3-impl")
        arch = self.new(stage="s4-judge")
        self.archive(arch)
        found = self.found("stage_without_docs")
        self.assertEqual(set(found), {both, no_journal})
        self.assertEqual(found[both][0]["details"]["missing"], ["spec_path", "journal_path"])
        self.assertEqual(found[both][0]["message"], "этап s3-impl без spec_path, journal_path")
        self.assertEqual(found[no_journal][0]["details"]["missing"], ["journal_path"])
        for tid in (s1, full, closed, other, arch):
            self.assertNotIn(tid, found)


class PortionFilesTests(LintCase):
    def _files(self, where: pathlib.Path, tid: str) -> None:
        where.mkdir(parents=True, exist_ok=True)
        for name in (f"{tid}.md", f"{tid}.a.md", f"{tid}.b.md", f"{tid}.check-a.md",
                     f"{tid}.journal.md"):
            (where / name).write_text("x", encoding="utf-8")

    def test_by_spec_path(self) -> None:
        tid = self.new(spec_path=str(self.tmp_path / "s.md"))
        self._files(self.tmp_path, tid)
        found = self.found("portion_files_without_cards")
        self.assertEqual(found[tid][0]["details"],
                         {"files": [f"{tid}.a.md", f"{tid}.b.md"],
                          "steps_dir": str(self.tmp_path)})
        child = self.new(parent=tid)
        self.assertNotIn(tid, self.found("portion_files_without_cards"))
        store.update_task(self.conn, child, status="done")
        self.assertNotIn(tid, self.found("portion_files_without_cards"))
        self.archive(child)
        self.assertIn(tid, self.found("portion_files_without_cards"))

    def test_closed_and_other_project_silent(self) -> None:
        closed = self.new(spec_path=str(self.tmp_path / "s.md"))
        self._files(self.tmp_path, closed)
        store.update_task(self.conn, closed, status="done")
        other = self.new(project="q", spec_path=str(self.tmp_path / "s.md"))
        self._files(self.tmp_path, other)
        found = self.found("portion_files_without_cards")
        self.assertNotIn(closed, found)
        self.assertNotIn(other, found)

    def test_by_project_path(self) -> None:
        root = self.tmp_path / "proj"
        root.mkdir()
        store.add_project(self.conn, slug="p", path=str(root))
        tid = self.new()
        steps = root / "docs" / "specs" / "steps"
        self.assertEqual(self.found("portion_files_without_cards"), {})  # каталога нет
        self._files(steps, tid)
        found = self.found("portion_files_without_cards")
        self.assertEqual(found[tid][0]["details"]["steps_dir"], str(steps.resolve()))

    def test_missing_dir_is_silent(self) -> None:
        tid = self.new(spec_path=str(self.tmp_path / "nope" / "s.md"))
        self.assertNotIn(tid, self.found("portion_files_without_cards"))


class StageBehindPortionsTests(LintCase):
    def test_child_ahead(self) -> None:
        parent = self.new(stage="s2-review")
        self.new(parent=parent, stage="s3-impl")
        found = self.found("stage_behind_portions")
        self.assertEqual(found[parent][0]["details"],
                         {"parent_stage": "s2-review", "max_child_stage": "s3-impl"})

    def test_parent_without_stage(self) -> None:
        parent = self.new()
        self.new(parent=parent, stage="s1-spec")
        found = self.found("stage_behind_portions")
        self.assertIsNone(found[parent][0]["details"]["parent_stage"])
        self.assertEqual(found[parent][0]["message"], "этап шага — позади порций (s1-spec)")

    def test_same_stage_silent(self) -> None:
        parent = self.new(stage="s3-impl")
        self.new(parent=parent, stage="s3-impl")
        self.assertNotIn(parent, self.found("stage_behind_portions"))

    def test_all_children_done(self) -> None:
        late = self.new(stage="s4-judge")
        early = self.new(stage="s2-review")
        for parent in (late, early):
            for _ in range(2):
                store.update_task(self.conn, self.new(parent=parent, stage="s3-impl"),
                                  status="done")
        found = self.found("stage_behind_portions")
        self.assertEqual(found[late][0]["details"],
                         {"parent_stage": "s4-judge", "children_done": True})
        self.assertNotIn(early, found)

    def test_no_children_and_archived_child(self) -> None:
        lonely = self.new(stage="s2-review")
        parent = self.new(stage="s2-review")
        self.archive(self.new(parent=parent, stage="s3-impl"))
        found = self.found("stage_behind_portions")
        self.assertNotIn(lonely, found)
        self.assertNotIn(parent, found)


class SuggestedDepStaleTests(LintCase):
    def _suggest(self, a: str, b: str, hours: float) -> None:
        dep = store.add_dep(self.conn, a, b, "blocks", created_by="agent:x")
        self.assertEqual(dep.get("dep_type", "suggested-blocks"), "suggested-blocks")
        self.conn.execute("UPDATE deps SET created_at = ? WHERE issue_id = ? AND depends_on = ?",
                          (_ago(hours), a, b))
        self.conn.commit()

    def test_rule(self) -> None:
        a, b, c = self.new(), self.new(), self.new()
        self._suggest(a, b, 30)
        found = self.found("suggested_dep_stale")
        self.assertEqual(len(found[a]), 1)
        self.assertEqual(found[a][0]["details"]["depends_on"], b)
        self.assertTrue(29.9 <= found[a][0]["details"]["hours"] <= 30.1)
        self.assertEqual(self.found("suggested_dep_stale", suggested_hours=48), {})
        self._suggest(a, c, 30)
        self.assertEqual(len(self.found("suggested_dep_stale")[a]), 2)

    def test_closed_issue_silent(self) -> None:
        a, b = self.new(), self.new()
        self._suggest(a, b, 30)
        store.update_task(self.conn, a, status="done")
        self.assertEqual(self.found("suggested_dep_stale"), {})

    def test_bad_hours(self) -> None:
        for bad in (0, -1, "abc"):
            with self.assertRaises(errors.BadArgument):
                store.lint(self.conn, "p", suggested_hours=bad)


class GeneralTests(LintCase):
    def test_sorted_count_and_arguments(self) -> None:
        for _ in range(3):
            self.new(status="in_progress", stage="s3-impl")
        res = store.lint(self.conn, "p")
        keys = [(i["rule"], i["id"]) for i in res["items"]]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(res["count"], len(res["items"]))
        self.assertEqual(res["count"], 6)
        self.assertEqual(res["project"], "p")
        for bad in (None, "", "  "):
            with self.assertRaises(errors.BadArgument):
                store.lint(self.conn, bad)
        empty = store.lint(self.conn, "нет-такого")
        self.assertEqual((empty["items"], empty["count"]), ([], 0))

    def test_read_only(self) -> None:
        a, b = self.new(status="in_progress", stage="s3-impl"), self.new()
        store.add_dep(self.conn, a, b, "blocks", created_by="agent:x")
        self.conn.execute("UPDATE deps SET created_at = ?", (_ago(30),))
        self.conn.commit()

        def snap():
            return (self.conn.execute("SELECT count(*) FROM events").fetchone()[0],
                    self.conn.execute("SELECT max(updated_at) FROM tasks").fetchone()[0],
                    self.conn.execute("SELECT count(*) FROM deps").fetchone()[0])
        before = snap()
        self.assertGreater(store.lint(self.conn, "p")["count"], 0)
        self.assertEqual(snap(), before)


class LintCliTests(LintCase):
    def _run(self, *args: str, env: dict | None = None,
             cwd: str | None = None) -> subprocess.CompletedProcess:
        full_env = {**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_PROJECT": ""}
        full_env.update(env or {})
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=full_env,
                              cwd=cwd or str(self.tmp_path))

    def test_cli(self) -> None:
        empty = self._run("lint", "--project", "p")
        self.assertEqual(empty.returncode, 0, empty.stderr)
        self.assertIn("несостыковок нет", empty.stdout)

        a = self.new(status="in_progress")
        b = self.new(status="in_progress")
        self.conn.commit()
        res = self._run("lint", "--project", "p")
        self.assertEqual(res.returncode, 1, res.stderr)
        self.assertIn("in_progress_no_holder:", res.stdout)
        for tid in (a, b):
            self.assertIn(f"  {tid}  в работе без держателя", res.stdout)

        js = self._run("lint", "--project", "p", "--json")
        self.assertEqual(js.returncode, 1, js.stderr)
        data = json.loads(js.stdout)
        self.assertLessEqual({"project", "generated_at", "count", "items"}, set(data))
        self.assertEqual(data["count"], 2)

        by_env = self._run("lint", env={"LISTIK_PROJECT": "p"})
        self.assertEqual((by_env.returncode, by_env.stdout), (res.returncode, res.stdout))

        none = self._run("lint")
        self.assertEqual(none.returncode, 2, none.stdout + none.stderr)
        self.assertIn("--project", none.stdout + none.stderr)

        zero = self._run("lint", "--project", "p", "--suggested-hours", "0")
        self.assertEqual(zero.returncode, 2, zero.stdout + zero.stderr)


class LintHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._saved_conn = (server._conn_made, server._conn_local)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = pathlib.Path(self._tmp.name)
        paths.DB_PATH = tmp / "listik.db"
        paths.CONFIG_PATH = tmp / "config.toml"
        paths.CONFIG_PATH.write_text('[auth]\ntoken = "t"\n', encoding="utf-8")
        self.conn = db_mod.init(paths.DB_PATH)
        server._conn_made = False
        server._conn_local = threading.local()
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()
        self.conn.close()
        paths.DB_PATH, paths.CONFIG_PATH = self._saved_paths
        server._conn_made, server._conn_local = self._saved_conn
        self._tmp.cleanup()

    def get(self, query: str):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/lint{query}",
                                     headers={"Authorization": "Bearer t"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_http(self) -> None:
        a = store.create_task(self.conn, title="a", project="p", status="in_progress")["id"]
        b = store.create_task(self.conn, title="b", project="p")["id"]
        store.add_dep(self.conn, a, b, "blocks", created_by="agent:x")
        self.conn.execute("UPDATE deps SET created_at = ?", (_ago(30),))
        self.conn.commit()
        status, payload = self.get("?project=p&suggested_hours=48")
        self.assertEqual(status, 200, payload)
        body = payload.get("data", payload)
        self.assertEqual(body["items"],
                         store.lint(self.conn, "p", suggested_hours=48)["items"])
        status, default = self.get("?project=p")
        self.assertIn("suggested_dep_stale",
                      [i["rule"] for i in default.get("data", default)["items"]])
        for query in ("", "?project=p&suggested_hours=abc", "?project=p&suggested_hours=0"):
            status, payload = self.get(query)
            self.assertEqual(status, 400, (query, payload))
            self.assertEqual(payload["code"], errors.BAD_ARGUMENT, (query, payload))


if __name__ == "__main__":
    unittest.main()
