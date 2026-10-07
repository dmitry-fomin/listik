"""`stage_launch.in_swarm` — правило «карточка роя» (зеркало `isSwarmCard`, listik-w7ge)."""
from __future__ import annotations

import sqlite3
import unittest

from listik.stage_launch import in_swarm

ROUTES = {
    "roy": {"key": "roy", "kind": "swarm"},
    # Лишнее поле старой схемы: рой решается только по `kind` (listik-ujra).
    "pipe-swarm": {"key": "pipe-swarm", "kind": "pipeline", "driver": "swarm"},
    "pipe": {"key": "pipe", "kind": "pipeline"},
    "direct": {"key": "direct", "kind": "direct"},
}


def card(route=None, driver=None) -> dict:
    return {"launch_route": route, "launch_driver": driver}


class InSwarmTests(unittest.TestCase):
    def test_rule(self) -> None:
        cases = [
            ("пустой маршрут, снимок swarm", card("", "swarm"), False),
            ("пробельный маршрут, снимок swarm", card("   ", "swarm"), False),
            ("снимок swarm, маршрута нет в словаре", card("gone", "swarm"), True),
            ("без снимка, маршрута нет в словаре", card("gone"), False),
            ("без снимка, kind=swarm", card("roy"), True),
            ("без снимка, pipeline с лишним полем роя", card("pipe-swarm"), False),
            ("без снимка, pipeline", card("pipe"), False),
            ("снимок swarm, маршрут-конвейер", card("pipe", "swarm"), True),
            ("без снимка, direct", card("direct"), False),
            ("снимок skill, роевой маршрут", card("roy", "skill"), False),
            ("словарь без полей", {}, False),
        ]
        for name, task, expected in cases:
            with self.subTest(name):
                self.assertIs(in_swarm(task, ROUTES), expected)

    def test_sqlite_row(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE t (launch_route TEXT, launch_driver TEXT)")
        conn.executemany("INSERT INTO t VALUES (?, ?)",
                         [("roy", None), ("roy", "skill"), (" gone ", "swarm")])
        rows = conn.execute("SELECT * FROM t ORDER BY rowid").fetchall()
        self.assertEqual([in_swarm(r, ROUTES) for r in rows], [True, False, True])
        conn.close()


if __name__ == "__main__":
    unittest.main()
