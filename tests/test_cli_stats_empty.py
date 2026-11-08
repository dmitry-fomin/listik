"""`listik stats` без живых задач: одна строка «живых задач нет» вместо пустых
секций «проекты:» и «держат прямо сейчас:» (listik-72il, порция a).

CLI запускается подпроцессом в локальном режиме на временной базе: сети и Ollama нет.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from listik import store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN

NO_LIVE = "живых задач нет"


class StatsEmptyCase(TempDbTestCase):
    """CLI `stats --local` на временной базе без конфига (локальный режим без него работает)."""

    def run_stats(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log"),
               "LISTIK_CONFIG": str(self.tmp_path / "nope.toml")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", "stats", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))


class StatsEmptyTests(StatsEmptyCase):
    def test_clean_db_text_says_no_live_tasks(self) -> None:
        p = self.run_stats()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn(NO_LIVE, p.stdout)
        self.assertNotIn("проекты:", p.stdout)
        self.assertNotIn("держат прямо сейчас:", p.stdout)

    def test_live_task_keeps_sections(self) -> None:
        store.create_task(self.conn, title="живая задача", project="demo")
        p = self.run_stats()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("проекты:", p.stdout)
        self.assertIn("держат прямо сейчас:", p.stdout)
        self.assertNotIn(NO_LIVE, p.stdout)

    def test_json_unchanged(self) -> None:
        p = self.run_stats("--json")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        res = json.loads(p.stdout)
        self.assertEqual(res["by_project"], [])
        self.assertEqual(res["by_holder"], [])
        self.assertNotIn(NO_LIVE, p.stdout)


if __name__ == "__main__":
    unittest.main()
