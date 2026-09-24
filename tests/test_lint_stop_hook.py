"""`listik lint --stop-hook` — Stop-хук Claude Code (listik-ugw8, порция f)."""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
LISTIK_BIN = REPO_DIR / "bin" / "listik"


class StopHookTests(TempDbTestCase):
    def hook(self, stdin: str = "{}", db=None, *extra, project: str | None = "p"):
        env = {k: v for k, v in os.environ.items() if k != "LISTIK_PROJECT"}
        env.update(LISTIK_DB=str(db or self.db_path), LISTIK_LOG=str(self.tmp_path / "listik.log"))
        proj = ["--project", project] if project else []
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "lint", "--stop-hook", "--local", *proj, *extra],
            input=stdin, capture_output=True, text=True, env=env, cwd=str(self.tmp_path))

    def stray(self, n: int = 1) -> list[str]:
        """Карточки `in_progress` без держателя — по находке на каждую."""
        return [store.create_task(self.conn, title=f"T{i}", project="p",
                                  status="in_progress")["id"] for i in range(n)]

    def block_reason(self, proc) -> str:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        out = json.loads(proc.stdout)
        self.assertEqual(out["decision"], "block")
        return out["reason"]

    def assert_silent(self, proc) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_findings_block(self) -> None:  # (а) + (з)
        tid = self.stray()[0]
        reason = self.block_reason(self.hook("{}"))
        self.assertIn(tid, reason)
        self.assertTrue(reason.splitlines()[0].startswith("listik lint: 1 несостыковок в проекте p"))
        self.assertIn("in_progress_no_holder", reason)

    def test_stop_hook_active_passes(self) -> None:  # (б)
        self.stray()
        self.assert_silent(self.hook('{"stop_hook_active": true}'))

    def test_no_findings_silent(self) -> None:  # (в)
        self.assert_silent(self.hook("{}"))

    def test_reason_capped_at_20(self) -> None:  # (г)
        self.stray(25)
        lines = self.block_reason(self.hook("{}")).splitlines()
        self.assertTrue(lines[0].startswith("listik lint: 25 несостыковок"))
        self.assertEqual(len(lines), 1 + 20 + 1)
        self.assertEqual(lines[-1], "… и ещё 5")

    def test_missing_db_dir_silent_and_not_created(self) -> None:  # (д)
        db = self.tmp_path / "нет" / "listik.db"
        self.assert_silent(self.hook("{}", db))
        self.assertFalse(db.parent.exists())

    def test_broken_db_silent(self) -> None:  # (д)
        db = self.tmp_path / "broken.db"
        db.write_bytes(b"not a sqlite database at all" * 100)
        proc = self.hook("{}", db)
        self.assert_silent(proc)
        self.assertEqual(proc.stderr, "")

    def test_multiline_active_passes(self) -> None:  # (е)
        self.stray()
        stdin = json.dumps({"session_id": "x", "stop_hook_active": True}, indent=2)
        self.assert_silent(self.hook(stdin))

    def test_empty_and_invalid_stdin_block(self) -> None:  # (ж)
        tid = self.stray()[0]
        self.assertIn(tid, self.block_reason(self.hook("")))
        self.assertIn(tid, self.block_reason(self.hook("не json")))

    def test_no_project_silent(self) -> None:
        self.stray()
        self.assert_silent(self.hook("{}", project=None))

    def test_does_not_touch_data(self) -> None:
        self.stray(3)
        tables = ("tasks", "comments", "events")

        def counts():
            return [self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables]

        before = counts()
        self.block_reason(self.hook("{}"))
        self.assertEqual(counts(), before)

    def test_json_conflicts(self) -> None:
        proc = self.hook("{}", None, "--json")
        self.assertEqual(proc.returncode, 2)

    def test_plain_lint_unchanged(self) -> None:
        self.stray()
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), "lint", "--local", "--project", "p"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main()
