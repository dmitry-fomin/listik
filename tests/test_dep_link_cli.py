import os
import subprocess
import sys

from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN
from listik import store


class DepLinkCliTests(TempDbTestCase):
    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def test_link_with_second_arg_refuses_with_hint(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        b = store.create_task(self.conn, title="B", project="demo")["id"]
        p = self._run("dep", "link", a, b, "--dep-type", "parent-child")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(f"dep add {a} {b} --dep-type parent-child", p.stderr)
        self.assertNotIn("Traceback", p.stderr)
        n = self.conn.execute("SELECT COUNT(*) FROM deps WHERE issue_id=?", (a,)).fetchone()[0]
        self.assertEqual(n, 0)

    def test_suggest_with_second_arg_refuses(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo")["id"]
        p = self._run("dep", "suggest", a, "x-1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("dep add", p.stderr)
