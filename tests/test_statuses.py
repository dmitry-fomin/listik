"""Наборы статусов объявлены один раз, в `listik/statuses.py`, а SQL собирает их из констант
(listik-33cc): копии кортежей и литералы вида `('open','in_progress',…)` в запросах молча
расходятся, когда добавляют статус.
"""
from __future__ import annotations

import re
import sqlite3
import unittest
from pathlib import Path

from listik import deps, documents, mcp, statuses, store, swarm_watch
from tests.helpers import TempDbTestCase

ROOT = Path(__file__).resolve().parents[1]


class SingleDeclarationTests(unittest.TestCase):
    def test_modules_share_statuses_objects(self) -> None:
        for mod in (store, deps, swarm_watch):
            self.assertIs(mod.OPEN_STATUSES, statuses.OPEN_STATUSES, mod.__name__)
        for mod in (store, deps):
            self.assertIs(mod.FINAL_STATUSES, statuses.FINAL_STATUSES, mod.__name__)

    def test_claimable_is_open_without_blocked(self) -> None:
        self.assertEqual(deps.CLAIMABLE_STATUSES, ("open", "in_progress", "review"))

    def test_all_statuses_is_open_plus_final(self) -> None:
        self.assertEqual(statuses.ALL_STATUSES,
                         ("open", "in_progress", "blocked", "review", "done", "cancelled"))
        self.assertEqual(statuses.ALL_STATUSES,
                         statuses.OPEN_STATUSES + statuses.FINAL_STATUSES)

    def test_running_and_scalars_come_from_declared_sets(self) -> None:
        self.assertLessEqual(set(statuses.RUNNING_STATUSES), set(statuses.OPEN_STATUSES))
        self.assertIn(statuses.IN_PROGRESS, statuses.OPEN_STATUSES)
        self.assertIn(statuses.DONE, statuses.FINAL_STATUSES)

    def test_scalars_and_board_status_order(self) -> None:
        self.assertEqual(statuses.BOARD_STATUS_ORDER,
                         ("in_progress", "review", "open", "blocked"))
        self.assertEqual(sorted(statuses.BOARD_STATUS_ORDER),
                         sorted(statuses.OPEN_STATUSES))
        for scalar in (statuses.OPEN, statuses.BLOCKED, statuses.REVIEW):
            self.assertIn(scalar, statuses.OPEN_STATUSES)
        self.assertIn(statuses.REVIEW, statuses.RUNNING_STATUSES)
        self.assertIn(statuses.CANCELLED, statuses.FINAL_STATUSES)

    def test_epic_active_is_open_without_open(self) -> None:
        self.assertEqual(store._EPIC_ACTIVE, ("in_progress", "blocked", "review"))

    def test_mcp_status_enum_is_all_statuses(self) -> None:
        tool = next(t for t in mcp.TOOLS if t["name"] == "listik_list")
        enum = tool["inputSchema"]["properties"]["status"]["enum"]
        self.assertEqual(enum, list(statuses.ALL_STATUSES))


class SqlFragmentTests(unittest.TestCase):
    def _matching(self, fragment: str) -> tuple[str, ...]:
        conn = sqlite3.connect(":memory:")
        self.addCleanup(conn.close)
        return tuple(s for s in statuses.ALL_STATUSES
                     if conn.execute(f"SELECT ? IN ({fragment})", (s,)).fetchone()[0])

    def test_open_sql_matches_tuple(self) -> None:
        self.assertEqual(self._matching(statuses.OPEN_STATUSES_SQL), statuses.OPEN_STATUSES)

    def test_running_sql_matches_tuple(self) -> None:
        self.assertEqual(self._matching(statuses.RUNNING_STATUSES_SQL),
                         statuses.RUNNING_STATUSES)

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

    def test_no_running_statuses_literal(self) -> None:
        hits = self._hits(re.compile(r"'in_progress'\s*,\s*'review'"))
        self.assertEqual(hits, [], "литерал рабочего набора в SQL:\n" + self._fmt(hits))

    def test_sets_declared_only_in_statuses_module(self) -> None:
        expected = ROOT / "listik" / "statuses.py"
        for name in ("OPEN_STATUSES", "FINAL_STATUSES", "ALL_STATUSES", "RUNNING_STATUSES"):
            hits = self._hits(re.compile(rf"^{name}\s*=(?!=)"))
            self.assertEqual({p for p, _, _ in hits}, {expected},
                             f"объявления {name}:\n" + self._fmt(hits))


class StatusScalarGrepTests(unittest.TestCase):
    """Гард порции listik-9ate.a: литералы статусов в логике заменены константами
    `listik/statuses.py` (остатки белого списка — словари отображения и ключи ответов —
    проверяются инвентарём чек-листа, а не этим гардом)."""

    _SOURCES = ("listik/store.py", "listik/deps.py", "listik/stage_launch.py",
                "bin/listik")

    @staticmethod
    def _hits(pattern: re.Pattern, files: tuple[str, ...]) -> list[str]:
        out = []
        for rel in files:
            for n, line in enumerate(
                    (ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    out.append(f"{rel}:{n}: {line.strip()}")
        return out

    def test_no_running_tuple_literal(self) -> None:
        hits = self._hits(re.compile(r"[\"']in_progress[\"']\s*,\s*[\"']review[\"']"),
                          self._SOURCES)
        self.assertEqual(hits, [],
                         "кортеж ('in_progress', 'review') литералом:\n" + "\n".join(hits))

    def test_no_single_quoted_cancelled_in_stage_launch(self) -> None:
        hits = self._hits(re.compile(r"'cancelled'"), ("listik/stage_launch.py",))
        self.assertEqual(hits, [],
                         "'cancelled' одинарными кавычками:\n" + "\n".join(hits))

    def test_no_coalesce_open_default_in_deps(self) -> None:
        hits = self._hits(re.compile(r"coalesce\([^)]*'open'"), ("listik/deps.py",))
        self.assertEqual(hits, [],
                         "coalesce(…, 'open') в deps.py:\n" + "\n".join(hits))


def _declared_statuses(text: str, declaration: re.Pattern) -> set[str]:
    """Строковые литералы тела объявления (группа 1 паттерна); объявление обязано найтись."""
    m = declaration.search(text)
    assert m, f"объявление не найдено: {declaration.pattern}"
    return set(re.findall(r"[\"']([^\"']+)[\"']", m.group(1)))


def _swarm_open_statuses(text: str) -> set[str]:
    """Набор из `export const OPEN_STATUSES = new Set([...])` в тексте decide.mjs."""
    return _declared_statuses(text, re.compile(
        r"export const OPEN_STATUSES\s*=\s*new Set\(\[([^\]]*)\]"))


def _task_status_union(text: str) -> set[str]:
    """Набор из union `export type TaskStatus = ...` в тексте types.ts."""
    return _declared_statuses(text, re.compile(r"export type TaskStatus\s*=\s*([^\n;]+)"))


def _web_final_statuses(text: str) -> set[str]:
    """Набор из `export const FINAL_STATUSES ... = [...]` в тексте dictionaries.ts."""
    return _declared_statuses(text, re.compile(
        r"export const FINAL_STATUSES\b[^=\n]*=\s*\[([^\]]*)\]"))


class JsStatusCopyTests(unittest.TestCase):
    """Порция listik-9ate.b: JS-копии наборов статусов (общего объявления с Python у них
    нет) сверяются с `listik/statuses.py` regex-разбором текста файла. Мутационные
    прогоны подменяют строку в памяти — сами файлы не трогаются."""

    def _text(self, rel: str) -> str:
        return (ROOT / rel).read_text(encoding="utf-8")

    def test_swarm_open_statuses_match(self) -> None:
        declared = _swarm_open_statuses(self._text("swarm/decide.mjs"))
        self.assertEqual(declared, set(statuses.OPEN_STATUSES))

    def test_task_status_union_matches_all(self) -> None:
        declared = _task_status_union(self._text("web/src/api/types.ts"))
        self.assertEqual(declared, set(statuses.ALL_STATUSES))

    def test_web_final_statuses_match(self) -> None:
        declared = _web_final_statuses(self._text("web/src/lib/dictionaries.ts"))
        self.assertEqual(declared, set(statuses.FINAL_STATUSES))

    def test_mutation_decide_without_review(self) -> None:
        declared = _swarm_open_statuses(self._text("swarm/decide.mjs").replace('"review"', ""))
        self.assertNotEqual(declared, set(statuses.OPEN_STATUSES))

    def test_mutation_types_without_cancelled(self) -> None:
        declared = _task_status_union(
            self._text("web/src/api/types.ts").replace("'cancelled'", ""))
        self.assertNotEqual(declared, set(statuses.ALL_STATUSES))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
