"""Маркеры журнала и автор роя: Python-константы и их Node-копии в `swarm/*.mjs`
совпадают (listik-11gx). Общего объявления у Python и Node нет, поэтому значения из `.mjs`
достаются regex-разбором текста файла, как в `tests/test_statuses.py`; без `node`."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from listik import deps, swarm_llm, swarm_watch

ROOT = Path(__file__).resolve().parents[1]

_CONFIG_ACTOR = re.compile(r'^[ \t]+actor:\s*"([^"]*)",', re.MULTILINE)


def _text(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _export_const(text: str, name: str) -> str:
    """Значение `export const <name> = "…";`; объявление обязано найтись."""
    m = re.search(rf'^export const {name}\s*=\s*"([^"]*)";', text, re.MULTILINE)
    assert m, f"объявление export const {name} не найдено"
    return m.group(1)


def _config_actor(text: str) -> str:
    """Дефолт `actor: "…",` в swarm/config.mjs; строка обязана быть ровно одна."""
    found = _CONFIG_ACTOR.findall(text)
    assert len(found) == 1, f"строк `actor: \"…\",` в config.mjs: {len(found)}, нужна одна"
    return found[0]


class SwarmMarkerCopyTests(unittest.TestCase):
    def test_marks_match(self) -> None:
        pairs = (
            (swarm_watch.FIRST_CHANGE_MARK, "swarm/barrier.mjs", "FIRST_CHANGE_MARK"),
            (swarm_watch.SCOPE_MARK, "swarm/rollback.mjs", "SCOPE_MARK"),
            (swarm_watch.FREEZE_MARK, "swarm/rollback.mjs", "FREEZE_MARK"),
            (swarm_llm.MERGED_MARK, "swarm/barrier.mjs", "MERGED_MARK"),
            (swarm_watch.FROZEN_LABEL, "swarm/decide.mjs", "FROZEN_LABEL"),
            (deps.RESOURCE_BLOCK_AUTHOR, "swarm/barrier.mjs", "SWARM_AUTHOR"),
        )
        for py, rel, name in pairs:
            with self.subTest(name=name, file=rel):
                self.assertEqual(_export_const(_text(rel), name), py)

    def test_config_actor_default(self) -> None:
        self.assertEqual(_config_actor(_text("swarm/config.mjs")), deps.RESOURCE_BLOCK_AUTHOR)

    def test_python_actor_is_single_object(self) -> None:
        self.assertIs(swarm_watch.SWARM_ACTOR, deps.RESOURCE_BLOCK_AUTHOR)
        self.assertIs(swarm_llm.SWARM_AUTHOR, deps.RESOURCE_BLOCK_AUTHOR)

    def test_mutation_merged_mark(self) -> None:
        text = _text("swarm/barrier.mjs").replace('"рой: влито:"', '"рой: слито:"')
        self.assertNotEqual(_export_const(text, "MERGED_MARK"), swarm_llm.MERGED_MARK)


class SwarmLiteralGrepTests(unittest.TestCase):
    """Литералы не возвращаются вместо констант (обратные кавычки комментариев — не в счёт)."""

    @staticmethod
    def _hits(rel: str, literal: str) -> list[str]:
        pattern = re.compile(rf"[\"']{re.escape(literal)}[\"']")
        return [f"{rel}:{n}: {line.strip()}"
                for n, line in enumerate(_text(rel).splitlines(), 1)
                for _ in pattern.finditer(line)]

    def test_no_swarm_actor_literal(self) -> None:
        for rel in ("listik/swarm_watch.py", "bin/listik"):
            hits = self._hits(rel, "agent:listik-swarm")
            self.assertEqual(hits, [], "литерал автора роя:\n" + "\n".join(hits))

    def test_frozen_label_declared_once_in_swarm_watch(self) -> None:
        hits = self._hits("listik/swarm_watch.py", "frozen-by:")
        self.assertEqual(len(hits), 1, "литералы frozen-by::\n" + "\n".join(hits))

    def test_mjs_literals_only_in_decide(self) -> None:
        files = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "swarm").glob("*.mjs"))
        for literal in ("frozen-by:", "рой:"):
            hits = [h for rel in files for h in self._hits(rel, literal)]
            with self.subTest(literal=literal):
                self.assertEqual(len(hits), 1, "\n".join(hits))
                self.assertTrue(hits[0].startswith("swarm/decide.mjs:"), hits[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
