"""`listik portions sync` — карточки порций по файлам шага (listik-ugw8, порция e)."""
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

from listik import db as db_mod
from listik import deps as deps_mod
from listik import errors, paths, server, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


def make_step(conn, root: pathlib.Path, *, portions: bool = True) -> str:
    sid = store.create_task(conn, title="шаг", project="p")["id"]
    (root / f"{sid}.md").write_text("# шаг\n", encoding="utf-8")
    (root / f"{sid}.journal.md").write_text("# журнал\n", encoding="utf-8")
    if portions:
        (root / f"{sid}.a.md").write_text("текст\n# Порция a: первая\n", encoding="utf-8")
        (root / f"{sid}.b.md").write_text("без заголовка\n", encoding="utf-8")
        (root / f"{sid}.check-a.md").write_text("# чек-лист a\n", encoding="utf-8")
    store.update_task(conn, sid, spec_path=str(root / f"{sid}.md"),
                      journal_path=str(root / f"{sid}.journal.md"))
    return sid


class SyncTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sid = make_step(self.conn, self.tmp_path)

    def counts(self) -> tuple[int, int, int]:
        return tuple(self.conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                     for t in ("tasks", "deps", "events"))

    def test_create_then_idempotent_then_new_file(self) -> None:
        res = store.sync_portions(self.conn, self.sid, actor="agent:t")
        self.assertEqual(len(res["created"]), 2)
        a, b = res["created"]
        self.assertEqual([p["id"] for p in res["portions"]], [a, b])
        ta, tb = store.get_task(self.conn, a), store.get_task(self.conn, b)
        self.assertEqual(ta["checklist_path"], str(self.tmp_path / f"{self.sid}.check-a.md"))
        self.assertFalse(tb["checklist_path"])
        journal = str(self.tmp_path / f"{self.sid}.journal.md")
        self.assertEqual((ta["journal_path"], tb["journal_path"]), (journal, journal))
        self.assertEqual(ta["title"], "Порция a: первая")
        self.assertEqual(tb["title"], "порция b")
        self.assertEqual(res["linked"], [[a, b]])
        dep = self.conn.execute("SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?",
                                (b, a)).fetchall()
        self.assertEqual([r[0] for r in dep], ["blocks"])
        self.assertTrue(deps_mod.ready(self.conn, a)["ready"])
        self.assertFalse(deps_mod.ready(self.conn, b)["ready"])

        before = self.counts()
        again = store.sync_portions(self.conn, self.sid, actor="agent:t")
        self.assertEqual((again["created"], again["updated"], again["linked"]), ([], [], []))
        self.assertEqual(again["unchanged"], [a, b])
        self.assertEqual(self.counts(), before)

        (self.tmp_path / f"{self.sid}.c.md").write_text("# c\n", encoding="utf-8")
        third = store.sync_portions(self.conn, self.sid, actor="agent:t")
        self.assertEqual(len(third["created"]), 1)
        c = third["created"][0]
        self.assertEqual(third["linked"], [[b, c]])
        self.assertFalse(deps_mod.ready(self.conn, c)["ready"])

    def test_suggested_link_becomes_hard(self) -> None:
        a, b = store.sync_portions(self.conn, self.sid)["created"]
        store.remove_dep(self.conn, b, a)
        store.add_dep(self.conn, b, a, "blocks", created_by="agent:x")  # suggested
        res = store.sync_portions(self.conn, self.sid)
        self.assertEqual(res["linked"], [[a, b]])
        types = [r[0] for r in self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=?", (b, a))]
        self.assertEqual(types, ["blocks"])

    def test_no_spec_path(self) -> None:
        bare = store.create_task(self.conn, title="x", project="p")["id"]
        with self.assertRaises(errors.BadArgument):
            store.sync_portions(self.conn, bare)
        with self.assertRaises(errors.NotFound):
            store.sync_portions(self.conn, "p-nope")

    def test_manual_child_gets_paths(self) -> None:
        manual = store.create_task(self.conn, title="порция a", parent=self.sid)["id"]
        res = store.sync_portions(self.conn, self.sid)
        self.assertEqual(res["updated"], [manual])
        self.assertEqual(len(res["created"]), 1)
        t = store.get_task(self.conn, manual)
        self.assertEqual(t["spec_path"], str(self.tmp_path / f"{self.sid}.a.md"))
        self.assertEqual(t["checklist_path"], str(self.tmp_path / f"{self.sid}.check-a.md"))
        self.assertEqual(t["title"], "порция a")

    def test_lint_clean_after_sync(self) -> None:
        rule = "portion_files_without_cards"
        rules = lambda: [i["rule"] for i in store.lint(self.conn, "p")["items"]]  # noqa: E731
        self.assertIn(rule, rules())
        store.sync_portions(self.conn, self.sid)
        self.assertNotIn(rule, rules())

    def _cli(self, tid: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "portions", "sync", tid, "--local", "--json"],
            capture_output=True, text=True, cwd=str(self.tmp_path),
            env={**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_PROJECT": ""})

    def test_cli_no_spec_path_exit_2(self) -> None:
        bare = store.create_task(self.conn, title="x", project="p")["id"]
        self.conn.commit()
        run = self._cli(bare)
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("bad_argument", run.stdout + run.stderr)
        self.assertNotIn("Traceback", run.stderr)

    def test_cli_local(self) -> None:
        self.conn.commit()
        run = self._cli(self.sid)
        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout)
        self.assertEqual(len(data["created"]), 2)
        self.assertEqual(store.sync_portions(self.conn, self.sid)["unchanged"], data["created"])


class SyncHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_paths = (paths.DB_PATH, paths.CONFIG_PATH)
        self._saved_conn = (server._conn_made, server._conn_local)
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = pathlib.Path(self._tmp.name)
        paths.DB_PATH = self.tmp / "listik.db"
        paths.CONFIG_PATH = self.tmp / "config.toml"
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

    def post(self, tid: str):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/tasks/{tid}/portions/sync", data=b"{}",
            method="POST", headers={"Authorization": "Bearer t",
                                    "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"null")

    def test_http(self) -> None:
        sid = make_step(self.conn, self.tmp)
        self.conn.commit()
        status, payload = self.post(sid)
        self.assertEqual(status, 200, payload)
        self.assertEqual(len(payload.get("data", payload)["created"]), 2)
        bare = store.create_task(self.conn, title="x", project="p")["id"]
        self.conn.commit()
        status, payload = self.post(bare)
        self.assertEqual(status, 400, payload)
        self.assertEqual(payload["code"], errors.BAD_ARGUMENT)
        status, _ = self.post("p-nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
