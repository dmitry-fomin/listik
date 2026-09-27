"""`blocked_count` доски — то же правило, что `/api/blocked`; `LINK_TYPES` знает все типы связей (listik-nghw, порция a)."""
from __future__ import annotations

import pathlib
import re
import unittest

from listik import deps, store
from tests.helpers import TempDbTestCase
from tests.test_owner_store import OwnerStoreCase
from tests.test_resource_blocks import _insert_resource_block
from tests.test_routes_config import board_icon_names

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
DICTIONARIES_TS = REPO_DIR / "web" / "src" / "lib" / "dictionaries.ts"

LINK_TYPE_RE = re.compile(r"value: '([^']+)', label: '([^']+)', icon: '([^']+)'")


class BoardBlockedCountTests(TempDbTestCase):
    def new(self, title: str, project: str = "demo") -> str:
        return store.create_task(self.conn, title=title, project=project)["id"]

    def test_counts_like_api_blocked(self) -> None:
        conn = self.conn
        b, c, e = self.new("B"), self.new("C"), self.new("E")
        d, f = self.new("D", "other"), self.new("F", "other")
        a1, a2, a3, a4, a5, a6 = (self.new(f"A{i}") for i in range(1, 7))

        store.add_dep(conn, a1, b, "blocks", created_by="me")
        store.add_dep(conn, a2, c, "blocks", created_by="me")
        store.update_task(conn, c, status="done")
        store.add_dep(conn, a3, d, "blocks", created_by="me")
        store.update_task(conn, d, status="done")
        _insert_resource_block(conn, a4, e)
        store.add_dep(conn, a5, f, "blocks", created_by="me")
        store.add_dep(conn, a6, b, "blocks", created_by="agent:codex")

        self.assertEqual(store.board(conn, project="demo")["blocked_count"], 3)
        self.assertEqual(len(deps.blocked_tasks(conn, project="demo")), 3)
        self.assertEqual({t["id"] for t in deps.blocked_tasks(conn, project="demo")},
                         {a1, a4, a5})

    def test_include_closed_skips_cancelled(self) -> None:
        conn = self.conn
        b, x = self.new("B"), self.new("X")
        store.add_dep(conn, x, b, "blocks", created_by="me")
        store.update_task(conn, x, status="cancelled")
        self.assertEqual(store.get_task(conn, x)["blocked_by"], [b])

        self.assertEqual(
            store.board(conn, project="demo", include_closed=True)["blocked_count"], 0)
        self.assertEqual(len(deps.blocked_tasks(conn, project="demo")), 0)


class BoardBlockedCountOwnerTests(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()

    def test_owner_filter_applies(self) -> None:
        conn = self.conn
        b = self.make("B", project="demo", as_owner="ann")["id"]
        aa = self.make("Aa", project="demo", as_owner="ann")["id"]
        ab = self.make("Ab", project="demo", as_owner="bob")["id"]
        store.add_dep(conn, aa, b, "blocks", created_by="me")
        store.add_dep(conn, ab, b, "blocks", created_by="me")

        self.assertEqual(store.board(conn, project="demo", as_owner="ann")["blocked_count"], 1)
        self.assertEqual(store.board(conn, project="demo", as_owner="bob")["blocked_count"], 1)
        self.assertEqual(len(deps.blocked_tasks(conn, project="demo")), 2)


class LinkTypesDictionaryTests(unittest.TestCase):
    """Читает `web/src/lib/dictionaries.ts` как текст, без сборки доски."""

    def setUp(self) -> None:
        text = DICTIONARIES_TS.read_text(encoding="utf-8")
        self.assertIn("export const LINK_TYPES", text)
        block = text.split("export const LINK_TYPES", 1)[1].split("\n]", 1)[0]
        self.items = LINK_TYPE_RE.findall(block)

    def test_values_unique(self) -> None:
        values = [value for value, _, _ in self.items]
        self.assertEqual(len(values), len(set(values)))

    def test_labels_match_dep_titles(self) -> None:
        self.assertEqual({value: label for value, label, _ in self.items}, deps.DEP_TITLES)

    def test_icons_known_to_board(self) -> None:
        known = board_icon_names()
        self.assertTrue(known)
        for _, _, icon in self.items:
            self.assertIn(icon, known)


if __name__ == "__main__":
    unittest.main()
