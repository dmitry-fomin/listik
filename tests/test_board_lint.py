"""Находки `lint` на доске: поле карточки, корень ответа, лента `needs_you` (listik-ugw8, порция d)."""
from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from unittest import mock

from listik import db as db_mod
from listik import paths, server, store
from tests.helpers import TempDbTestCase


def _ago(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _released_in_progress(conn, title: str, project: str = "p") -> str:
    """`in_progress` без держателя, но в льготном окне после release — не `abandoned`."""
    tid = store.create_task(conn, title=title, project=project, stage="s1-spec")["id"]
    store.claim(conn, tid, holder="agent:a")
    store.update_task(conn, tid, holder="")
    return tid


def _cards(board: dict) -> dict[str, dict]:
    return {t["id"]: t for col in board["columns"] for t in col["tasks"]}


class BoardLintTests(TempDbTestCase):
    def new(self, title: str = "T", project: str = "p", **kw) -> str:
        return store.create_task(self.conn, title=title, project=project, **kw)["id"]

    def test_card_field_root_and_needs_you(self) -> None:
        a = _released_in_progress(self.conn, "A")
        b = self.new("B")
        board = store.board(self.conn, project="p")
        cards = _cards(board)
        self.assertFalse(cards[a]["abandoned"])
        self.assertEqual(cards[a]["lint"], ["in_progress_no_holder"])
        self.assertEqual(cards[b]["lint"], [])
        ids = [t["id"] for t in board["needs_you"]]
        self.assertIn(a, ids)
        self.assertNotIn(b, ids)
        self.assertEqual(board["lint"]["count"], 1)
        self.assertEqual(board["lint"]["items"], store.lint(self.conn, "p")["items"])
        self.assertEqual(set(board["lint"]), {"count", "items"})

    def test_order_lint_only_last(self) -> None:
        a = _released_in_progress(self.conn, "A")
        c = self.new("C")
        store.set_needs_owner(self.conn, c, value=True, text="вопрос")
        d = self.new("D", stage="s1-spec")
        store.claim(self.conn, d, holder="agent:d")
        self.conn.execute("UPDATE tasks SET holder_at = ? WHERE id = ?", (_ago(30), d))
        self.conn.commit()
        board = store.board(self.conn, project="p")
        self.assertTrue(_cards(board)[d]["stale"])
        self.assertEqual([t["id"] for t in board["needs_you"]], [c, d, a])

    def test_two_stale_suggested_deps(self) -> None:
        e, f, g = self.new("E"), self.new("F"), self.new("G")
        for other in (f, g):
            store.add_dep(self.conn, e, other, "blocks", created_by="agent:x")
        self.conn.execute("UPDATE deps SET created_at = ?", (_ago(30),))
        self.conn.commit()
        board = store.board(self.conn, project="p")
        self.assertEqual(_cards(board)[e]["lint"],
                         ["suggested_dep_stale", "suggested_dep_stale"])
        self.assertIn(e, [t["id"] for t in board["needs_you"]])

    def test_without_project_no_lint(self) -> None:
        a = _released_in_progress(self.conn, "A")
        self.new("B")
        board = store.board(self.conn)
        self.assertTrue(all(t["lint"] == [] for t in _cards(board).values()))
        self.assertEqual(board["lint"], {"count": 0, "items": []})
        self.assertNotIn(a, [t["id"] for t in board["needs_you"]])

    def test_lint_error_does_not_break_board(self) -> None:
        _released_in_progress(self.conn, "A")
        with mock.patch("listik.store.lint", side_effect=RuntimeError):
            board = store.board(self.conn, project="p")
        self.assertEqual(board["lint"], {"count": 0, "items": []})
        self.assertTrue(all(t["lint"] == [] for t in _cards(board).values()))

    def test_show_and_list_have_no_lint(self) -> None:
        a = _released_in_progress(self.conn, "A")
        self.assertNotIn("lint", store.get_task(self.conn, a))
        listed = store.list_tasks(self.conn, project="p")
        rows = listed["tasks"] if isinstance(listed, dict) else listed
        self.assertTrue(rows)
        self.assertTrue(all("lint" not in t for t in rows))


class BoardLintHttpTests(unittest.TestCase):
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

    def test_board_http(self) -> None:
        a = _released_in_progress(self.conn, "A")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/board?project=p",
                                     headers={"Authorization": "Bearer t"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read())
        body = payload.get("data", payload)
        self.assertEqual(body["lint"]["count"], 1)
        self.assertEqual(body["lint"]["items"][0]["id"], a)
        self.assertEqual(_cards(body)[a]["lint"], ["in_progress_no_holder"])


if __name__ == "__main__":
    unittest.main()
