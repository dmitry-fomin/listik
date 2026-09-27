"""Наборы статусов объявлены один раз, в `listik/statuses.py`, а SQL собирает их из констант
(listik-33cc): копии кортежей и литералы вида `('open','in_progress',…)` в запросах молча
расходятся, когда добавляют статус.
"""
from __future__ import annotations

import re
import sqlite3
import unittest
from pathlib import Path

from listik import deps, documents, statuses, store, swarm_watch
from tests.helpers import TempDbTestCase

ROOT = Path(__file__).resolve().parents[1]
ALL_STATUSES = ("open", "in_progress", "blocked", "review", "done", "cancelled")


class SingleDeclarationTests(unittest.TestCase):
    def test_modules_share_statuses_objects(self) -> None:
        for mod in (store, deps, swarm_watch):
            self.assertIs(mod.OPEN_STATUSES, statuses.OPEN_STATUSES, mod.__name__)
        for mod in (store, deps):
            self.assertIs(mod.FINAL_STATUSES, statuses.FINAL_STATUSES, mod.__name__)

    def test_claimable_is_open_without_blocked(self) -> None:
        self.assertEqual(deps.CLAIMABLE_STATUSES, ("open", "in_progress", "review"))


class SqlFragmentTests(unittest.TestCase):
    def _matching(self, fragment: str) -> tuple[str, ...]:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        return tuple(s for s in ALL_STATUSES
                     if conn.execute(f"SELECT ? IN ({fragment})", (s,)).fetchone()[0])

    def test_open_sql_matches_tuple(self) -> None:
        self.assertEqual(self._matching(statuses.OPEN_STATUSES_SQL), statuses.OPEN_STATUSES)

    def test_claimable_sql_matches_tuple(self) -> None:
        self.assertEqual(self._matching(deps.CLAIMABLE_STATUSES_SQL), deps.CLAIMABLE_STATUSES)


class RefreshAllSkipsFinalTests(TempDbTestCase):
    def test_only_open_task_is_checked(self) -> None:
        spec = self.tmp_path / "spec.md"
        spec.write_text("# ТЗ\n\nТекст.\n", encoding="utf-8")
        ids = [store.create_task(self.conn, title=f"задача {n}", spec_path=str(spec))["id"]
               for n in range(3)]
        store.update_task(self.conn, ids[1], status="done")
        store.update_task(self.conn, ids[2], status="cancelled")
        self.assertEqual(documents.refresh_all(self.conn)["checked"], 1)


class StatusLiteralGrepTests(unittest.TestCase):
    """Гард по исходникам, как `GitArgvGrepTests`: наборы статусов не возвращаются литералами."""

    def _paths(self) -> list[Path]:
        return sorted((ROOT / "listik").glob("*.py")) + [ROOT / "bin" / "listik"]

    def _hits(self, pattern: re.Pattern) -> list[tuple[Path, int, str]]:
        out = []
        for path in self._paths():
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    out.append((path, n, line.strip()))
        return out

    @staticmethod
    def _fmt(hits: list[tuple[Path, int, str]]) -> str:
        return "\n".join(f"{p.relative_to(ROOT)}:{n}: {line}" for p, n, line in hits)

    def test_no_open_statuses_literal(self) -> None:
        hits = self._hits(re.compile(r"'open'\s*,\s*'in_progress'"))
        self.assertEqual(hits, [], "литерал открытого набора в SQL:\n" + self._fmt(hits))

    def test_no_final_statuses_literal(self) -> None:
        hits = self._hits(re.compile(r"'done'\s*,\s*'cancelled'"))
        self.assertEqual(hits, [], "литерал финального набора в SQL:\n" + self._fmt(hits))

    def test_sets_declared_only_in_statuses_module(self) -> None:
        expected = ROOT / "listik" / "statuses.py"
        for name in ("OPEN_STATUSES", "FINAL_STATUSES"):
            hits = self._hits(re.compile(rf"^{name}\s*=(?!=)"))
            self.assertEqual({p for p, _, _ in hits}, {expected},
                             f"объявления {name}:\n" + self._fmt(hits))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
