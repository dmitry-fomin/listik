"""Tests for listik.migrate — the AGENTS.md/CLAUDE.md protocol block and the .worktrees/
line in the project's .gitignore."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from listik import migrate, paths, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"

_HARNESS_NAMES = re.compile(
    r"claude|codex|dsh|deepseek|grok|gemini|writerllm|opus|sonnet",
    re.IGNORECASE,
)

# Ten canonical rules of the harness protocol, frozen from the step-02 spec (the working
# specs are not committed, so the reference copy lives next to the tests).
_RULES_PATH = Path(__file__).parent / "fixtures" / "harness-protocol-rules.txt"


def _spec_rules() -> list[str]:
    """Read the numbered rules from the fixture and normalize whitespace."""
    block = _RULES_PATH.read_text(encoding="utf-8")
    rules: list[str] = []
    current: list[str] = []
    for ln in block.splitlines():
        if re.match(r"^\s*\d+\.\s", ln):
            if current:
                rules.append(" ".join(current))
            current = [ln.strip()]
        elif ln.strip() and current:
            current.append(ln.strip())
    if current:
        rules.append(" ".join(current))
    assert len(rules) == 10, f"expected 10 numbered rules in spec, found {len(rules)}"
    return rules


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class BlockContentTests(unittest.TestCase):
    def test_block_has_ten_numbered_rules_and_roles_section_no_harness_names(self) -> None:
        rendered = migrate.block()
        for n in range(1, 11):
            self.assertRegex(
                rendered,
                rf"(^|\n){n}\.\s",
                f"missing numbered rule {n}",
            )
        self.assertIn("### Stages", rendered)
        for stage in ("s1-spec", "s2-review", "s3-impl", "s4-judge"):
            self.assertIn(stage, rendered)
        self.assertNotRegex(rendered, _HARNESS_NAMES)

    def test_ten_rules_match_spec_verbatim(self) -> None:
        spec_lines = _spec_rules()
        protocol_text = _normalize(
            (paths.ROOT_DIR / "docs" / "harness-protocol.md").read_text(encoding="utf-8")
        )
        for line in spec_lines:
            self.assertIn(
                _normalize(line),
                protocol_text,
                f"rule not found verbatim in docs/harness-protocol.md: {line!r}",
            )


class SkillFileTests(unittest.TestCase):
    """.agents/skills/listik/SKILL.md — the same protocol as docs/harness-protocol.md."""

    def setUp(self) -> None:
        self.skill_dir = paths.ROOT_DIR / migrate.SKILL_REL
        self.text = (self.skill_dir / "SKILL.md").read_text(encoding="utf-8")

    def _split(self) -> tuple[list[str], str]:
        self.assertTrue(self.text.startswith("---\n"), "SKILL.md must start with ---")
        head, sep, body = self.text[4:].partition("\n---\n")
        self.assertTrue(sep, "frontmatter is not closed with ---")
        return head.split("\n"), body

    def test_frontmatter_is_name_and_description(self) -> None:
        lines, _ = self._split()
        self.assertEqual(2, len(lines), lines)
        self.assertEqual(f"name: {self.skill_dir.name}", lines[0])
        self.assertRegex(lines[1], r"^description: \S")

    def test_body_equals_harness_protocol(self) -> None:
        _, body = self._split()
        protocol = (paths.ROOT_DIR / "docs" / "harness-protocol.md").read_text(encoding="utf-8")
        self.assertEqual(protocol, body)

    def test_no_harness_names(self) -> None:
        self.assertNotRegex(self.text, _HARNESS_NAMES)


class UpsertTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_upsert_add_unchanged_update_remove_cycle(self) -> None:
        target = self.tmp_path / "AGENTS.md"
        original = "# Some project\n\nExisting instructions.\n"
        target.write_text(original, encoding="utf-8")

        result = migrate.upsert(target)
        self.assertEqual(result, "added")
        after_add = target.read_text(encoding="utf-8")
        self.assertTrue(after_add.rstrip("\n").endswith(migrate.END))
        self.assertTrue(after_add.startswith(original))

        result = migrate.upsert(target)
        self.assertEqual(result, "unchanged")

        other_body = "другой текст\n"
        result = migrate.upsert(target, body=other_body)
        self.assertEqual(result, "updated")
        after_update = target.read_text(encoding="utf-8")
        self.assertIn(other_body, after_update)
        outside_marker_text = (
            after_update[: after_update.index(migrate.BEGIN)]
            + after_update[after_update.index(migrate.END) + len(migrate.END):]
        )
        outside_marker_original = (
            original if migrate.BEGIN not in original else original
        )
        self.assertEqual(outside_marker_text.strip("\n"), original.strip("\n"))

        result = migrate.remove(target)
        self.assertEqual(result, "removed")
        self.assertEqual(target.read_text(encoding="utf-8"), original)


class AgentsMdTests(unittest.TestCase):
    def test_agents_md_carries_current_block(self) -> None:
        result = migrate.upsert(paths.ROOT_DIR / "AGENTS.md", dry_run=True)
        self.assertEqual(result, "unchanged")

    def test_claude_md_carries_skill_pointer(self) -> None:
        result = migrate.upsert(paths.ROOT_DIR / "CLAUDE.md", dry_run=True)
        self.assertEqual(result, "unchanged")


class PerFileBodyTests(unittest.TestCase):
    """AGENTS.md получает протокол целиком, CLAUDE.md — только указание на скил listik:listik."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_migrate_all_writes_protocol_to_agents_and_skill_pointer_to_claude(self) -> None:
        for name in migrate.TARGETS:
            (self.project / name).write_text("# Project\n", encoding="utf-8")
        migrate.migrate_all([self.project], verbose=False)
        agents = (self.project / "AGENTS.md").read_text(encoding="utf-8")
        claude = (self.project / "CLAUDE.md").read_text(encoding="utf-8")
        self.assertIn(migrate.block(), agents)
        self.assertIn(migrate.block(migrate.CLAUDE_BODY), claude)
        self.assertIn("listik:listik", claude)
        self.assertNotIn("### Stages", claude)


class MissingProtocolFileTests(unittest.TestCase):
    def test_body_raises_when_protocol_file_missing(self) -> None:
        missing_root = Path(tempfile.mkdtemp())
        try:
            with patch.object(paths, "ROOT_DIR", missing_root):
                with self.assertRaises(FileNotFoundError):
                    migrate.body()
        finally:
            missing_root.rmdir()


class MarkerSubstringRegressionTests(unittest.TestCase):
    """`upsert`/`remove` used to look for BEGIN/END as a bare substring anywhere in the
    text, so a file that merely *mentions* the markers in prose (no real generated block)
    was mistaken for one and had everything between the two mentions overwritten/dropped."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "PROSE.md"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_prose_mentioning_markers_is_not_treated_as_a_block(self) -> None:
        prose = (
            "# Docs\n\nThe migrate script inserts a block between "
            f"`{migrate.BEGIN}` and `{migrate.END}` markers.\n\nMore unrelated text.\n"
        )
        self.path.write_text(prose, encoding="utf-8")
        self.assertEqual(migrate.upsert(self.path, dry_run=True), "added")
        migrate.upsert(self.path)
        after = self.path.read_text(encoding="utf-8")
        self.assertIn(prose, after)
        self.assertEqual(migrate.remove(self.path, dry_run=True), "removed")

    def test_real_block_on_standalone_lines_round_trips(self) -> None:
        text = "# Title\n\nIntro.\n\n" + migrate.block() + "\nTrailing.\n"
        self.path.write_text(text, encoding="utf-8")
        self.assertEqual(migrate.upsert(self.path, dry_run=True), "unchanged")
        self.assertEqual(migrate.remove(self.path, dry_run=True), "removed")
        migrate.remove(self.path)
        after = self.path.read_text(encoding="utf-8")
        self.assertNotIn(migrate.BEGIN, after)
        self.assertIn("Intro.", after)
        self.assertIn("Trailing.", after)


class GitignoreTests(unittest.TestCase):
    """init-projects заводит строку .worktrees/ в .gitignore проекта (listik-yaz9)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self._tmpdir.name)
        self.gitignore = self.project / ".gitignore"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _entries(self) -> list[str]:
        """Строки .gitignore, закрывающие .worktrees/ (комментарии и пустые не в счёт)."""
        return [ln.strip() for ln in self.gitignore.read_text(encoding="utf-8").splitlines()
                if ln.strip().lstrip("/").rstrip("/") == migrate.GITIGNORE_ENTRY.rstrip("/")]

    def test_missing_gitignore_is_created_with_worktrees_line(self) -> None:
        self.assertEqual(migrate.ensure_gitignore(self.project), "added")
        self.assertEqual(self.gitignore.read_text(encoding="utf-8"), migrate.GITIGNORE_ENTRY + "\n")
        self.assertEqual(migrate.ensure_gitignore(self.project), "unchanged")

    def test_existing_gitignore_keeps_its_text_and_gets_the_line_once(self) -> None:
        original = "# Build\nweb/dist/\n"
        self.gitignore.write_text(original, encoding="utf-8")
        self.assertEqual(migrate.ensure_gitignore(self.project), "updated")
        after = self.gitignore.read_text(encoding="utf-8")
        self.assertTrue(after.startswith(original))
        self.assertEqual(self._entries(), [migrate.GITIGNORE_ENTRY])
        self.assertEqual(migrate.ensure_gitignore(self.project), "unchanged")
        self.assertEqual(self.gitignore.read_text(encoding="utf-8"), after)

    def test_file_without_trailing_newline_gets_the_line_on_its_own_line(self) -> None:
        self.gitignore.write_text("web/dist/", encoding="utf-8")
        self.assertEqual(migrate.ensure_gitignore(self.project), "updated")
        self.assertEqual(self.gitignore.read_text(encoding="utf-8"),
                         "web/dist/\n" + migrate.GITIGNORE_ENTRY + "\n")

    def test_equivalent_existing_entry_is_not_duplicated(self) -> None:
        for entry in (".worktrees/", ".worktrees", "/.worktrees/", "/.worktrees"):
            with self.subTest(entry=entry):
                self.gitignore.write_text(f"# Listik\n{entry}\n", encoding="utf-8")
                self.assertEqual(migrate.ensure_gitignore(self.project), "unchanged")
                self.assertEqual(self.gitignore.read_text(encoding="utf-8"), f"# Listik\n{entry}\n")

    def test_comment_mentioning_worktrees_does_not_count_as_the_line(self) -> None:
        self.gitignore.write_text("# .worktrees/ живут в проекте\n", encoding="utf-8")
        self.assertEqual(migrate.ensure_gitignore(self.project), "updated")
        self.assertIn("\n" + migrate.GITIGNORE_ENTRY + "\n",
                      self.gitignore.read_text(encoding="utf-8"))

    def test_dry_run_reports_change_but_writes_nothing(self) -> None:
        self.assertEqual(migrate.ensure_gitignore(self.project, dry_run=True), "added")
        self.assertFalse(self.gitignore.exists())
        self.gitignore.write_text("web/dist/\n", encoding="utf-8")
        self.assertEqual(migrate.ensure_gitignore(self.project, dry_run=True), "updated")
        self.assertEqual(self.gitignore.read_text(encoding="utf-8"), "web/dist/\n")

    def test_missing_project_dir_is_skipped(self) -> None:
        self.assertEqual(migrate.ensure_gitignore(self.project / "nope"), "skipped")

    def test_migrate_all_reports_gitignore_and_remove_leaves_it_alone(self) -> None:
        (self.project / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
        report = migrate.migrate_all([self.project], verbose=False)
        self.assertEqual(report["gitignore"], [str(self.gitignore)])
        self.assertIn(migrate.GITIGNORE_ENTRY,
                      self.gitignore.read_text(encoding="utf-8"))

        again = migrate.migrate_all([self.project], verbose=False)
        self.assertEqual(again["gitignore"], [], "повторный прогон ничего не дописывает")

        removed = migrate.migrate_all([self.project], remove_block=True, verbose=False)
        self.assertEqual(removed["gitignore"], [])
        self.assertIn(migrate.GITIGNORE_ENTRY, self.gitignore.read_text(encoding="utf-8"))


class InitProjectsCliTests(TempDbTestCase):
    """`listik init-projects` доводит строку .worktrees/ до .gitignore проекта."""

    def test_command_adds_gitignore_line_to_registered_project(self) -> None:
        project = (self.tmp_path / "proj").resolve()
        project.mkdir()
        (project / "AGENTS.md").write_text("# Project\n", encoding="utf-8")
        store.add_project(self.conn, path=str(project))

        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        proc = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "init-projects"],
            capture_output=True, text=True, env=env, cwd=str(self.tmp_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("поправлен .gitignore: 1", proc.stdout)
        self.assertEqual((project / ".gitignore").read_text(encoding="utf-8"),
                         migrate.GITIGNORE_ENTRY + "\n")


if __name__ == "__main__":
    unittest.main()
