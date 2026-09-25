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

        other_body = "<!-- listik-protocol: 3 -->\n\nдругой текст\n"
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


class ProjectProtocolSourceTests(unittest.TestCase):
    """Тело блока AGENTS.md берётся из протокола проекта, если он есть (listik-e8za):
    установленная копия со старым шаблоном не должна откатывать свежий блок."""

    PROJECT_TEXT = "локальный протокол\n"
    TEMPLATE_TEXT = "шаблон установки\n"

    def setUp(self) -> None:
        self.project_tmp = tempfile.TemporaryDirectory()
        self.root_tmp = tempfile.TemporaryDirectory()
        self.project = Path(self.project_tmp.name)
        self.fake_root = Path(self.root_tmp.name)
        (self.fake_root / "docs").mkdir()
        self.template = self.fake_root / "docs" / "harness-protocol.md"
        self.template.write_text(self.TEMPLATE_TEXT, encoding="utf-8")
        patcher = patch.object(paths, "ROOT_DIR", self.fake_root)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.agents = self.project / "AGENTS.md"
        self.claude = self.project / "CLAUDE.md"

    def tearDown(self) -> None:
        self.project_tmp.cleanup()
        self.root_tmp.cleanup()

    def _write_project_protocol(self, text: str) -> None:
        (self.project / "docs").mkdir()
        (self.project / "docs" / "harness-protocol.md").write_text(text, encoding="utf-8")

    def test_project_protocol_wins_over_install_template(self) -> None:
        self._write_project_protocol(self.PROJECT_TEXT)
        self.agents.write_text("# Project\n", encoding="utf-8")
        self.claude.write_text("# Project\n", encoding="utf-8")
        migrate.migrate_all([self.project], verbose=False)
        agents = self.agents.read_text(encoding="utf-8")
        self.assertIn(migrate.block(self.PROJECT_TEXT), agents)
        self.assertNotIn("шаблон установки", agents)
        claude = self.claude.read_text(encoding="utf-8")
        self.assertIn(migrate.block(migrate.CLAUDE_BODY), claude)

    def test_fresh_project_block_is_not_rolled_back_by_old_template(self) -> None:
        self._write_project_protocol(self.PROJECT_TEXT)
        before = "# Project\n\n" + migrate.block(self.PROJECT_TEXT)
        self.agents.write_text(before, encoding="utf-8")
        self.assertEqual(migrate.upsert(self.agents), "unchanged")
        self.assertEqual(self.agents.read_bytes(), before.encode("utf-8"))

    def test_project_without_protocol_gets_install_template(self) -> None:
        self.agents.write_text("# Project\n", encoding="utf-8")
        report = migrate.migrate_all([self.project], verbose=False)
        self.assertIn(str(self.agents), report["added"])
        self.assertIn(migrate.block(self.TEMPLATE_TEXT), self.agents.read_text(encoding="utf-8"))

    def test_empty_project_protocol_is_a_valid_source(self) -> None:
        self._write_project_protocol("")
        self.agents.write_text("# Project\n", encoding="utf-8")
        migrate.upsert(self.agents)
        agents = self.agents.read_text(encoding="utf-8")
        self.assertIn(f"{migrate.BEGIN}\n{migrate.END}\n", agents)
        self.assertNotIn("шаблон установки", agents)

    def test_no_protocol_anywhere_raises(self) -> None:
        self.template.unlink()
        self.agents.write_text("# Project\n", encoding="utf-8")
        with self.assertRaises(FileNotFoundError):
            migrate.upsert(self.agents)


class RealTreeProtocolRegressionTests(unittest.TestCase):
    """Приёмка 4 listik-e8za на реальном дереве, без подмены ROOT_DIR."""

    def test_repo_agents_md_keeps_revoked_authority(self) -> None:
        agents = paths.ROOT_DIR / "AGENTS.md"
        self.assertEqual(migrate.upsert(agents, dry_run=True), "unchanged")
        lines = agents.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(line.startswith("**Revoked authority.**") for line in lines))


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


class SkillLinkTests(unittest.TestCase):
    """Симлинк `.agents/skills/listik` на скил из установки (listik-5qzq)."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        self.link = self.project / migrate.SKILL_REL
        self.gitignore = self.project / ".gitignore"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _install(self, version: str = "1.2.3") -> Path:
        """Каталог данных установленного Listik: app/<версия> + ссылка app/current."""
        home = self.tmp / "home"
        skill = home / "app" / version / migrate.SKILL_REL
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# listik\n", encoding="utf-8")
        (home / "app" / "current").symlink_to(home / "app" / version)
        return home

    def test_skill_source_goes_through_app_current(self) -> None:
        home = self._install("9.9.9")
        with patch.object(paths, "DATA_DIR", home):
            source = migrate.skill_source()
        self.assertEqual(source, home / "app" / "current" / migrate.SKILL_REL)
        self.assertIn(os.path.join("app", "current"), str(source))
        self.assertNotIn("9.9.9", str(source.relative_to(home)))
        self.assertTrue((source / "SKILL.md").exists())

    def test_skill_source_without_install_is_the_repo(self) -> None:
        with patch.object(paths, "DATA_DIR", self.tmp / "empty-home"):
            self.assertEqual(migrate.skill_source(), paths.ROOT_DIR / migrate.SKILL_REL)

    def test_link_cycle_added_unchanged_updated_then_real_dir_skipped(self) -> None:
        self.assertEqual(migrate.ensure_skill_link(self.project, dry_run=True), "added")
        self.assertFalse(os.path.lexists(self.link))

        self.assertEqual(migrate.ensure_skill_link(self.project), "added")
        self.assertEqual(os.readlink(self.link), str(migrate.skill_source()))
        self.assertEqual(migrate.ensure_skill_link(self.project), "unchanged")

        home = self._install()
        with patch.object(paths, "DATA_DIR", home):
            self.assertEqual(migrate.ensure_skill_link(self.project), "updated")
            self.assertEqual(os.readlink(self.link), str(migrate.skill_source()))

        self.link.unlink()
        self.link.mkdir()
        (self.link / "SKILL.md").write_text("настоящий\n", encoding="utf-8")
        self.assertEqual(migrate.ensure_skill_link(self.project), "skipped")
        self.assertFalse(os.path.islink(self.link))
        self.assertEqual((self.link / "SKILL.md").read_text(encoding="utf-8"), "настоящий\n")

    def test_install_without_skill_is_skipped(self) -> None:
        home = self.tmp / "old-home"
        (home / "app" / "1.0.0").mkdir(parents=True)
        (home / "app" / "current").symlink_to(home / "app" / "1.0.0")
        with patch.object(paths, "DATA_DIR", home):
            self.assertEqual(migrate.ensure_skill_link(self.project), "skipped")
        self.assertFalse(os.path.lexists(self.link))

    def test_gitignore_gets_both_lines_only_for_a_symlink(self) -> None:
        migrate.ensure_skill_link(self.project)
        self.assertEqual(migrate.ensure_gitignore(self.project), "added")
        self.assertEqual(self.gitignore.read_text(encoding="utf-8").splitlines(),
                         [migrate.GITIGNORE_ENTRY, migrate.SKILL_REL])
        self.assertEqual(migrate.ensure_gitignore(self.project), "unchanged")

    def test_real_dir_gets_no_gitignore_line(self) -> None:
        self.link.mkdir(parents=True)
        migrate.ensure_gitignore(self.project)
        self.assertNotIn(migrate.SKILL_REL, self.gitignore.read_text(encoding="utf-8"))

    def test_migrate_all_reports_link_and_remove_takes_only_symlink(self) -> None:
        report = migrate.migrate_all([self.project], verbose=False)
        self.assertEqual(report["skill_link"], [str(self.link)])
        self.assertIn(migrate.SKILL_REL, self.gitignore.read_text(encoding="utf-8"))
        self.assertEqual(migrate.migrate_all([self.project], verbose=False)["skill_link"], [])

        removed = migrate.migrate_all([self.project], remove_block=True, verbose=False)
        self.assertEqual(removed["skill_link"], [str(self.link)])
        self.assertFalse(os.path.lexists(self.link))

        self.link.mkdir(parents=True)
        removed = migrate.migrate_all([self.project], remove_block=True, verbose=False)
        self.assertEqual(removed["skill_link"], [])
        self.assertTrue(self.link.is_dir())


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
        self.assertIn("скил: 1", proc.stdout)
        link = project / migrate.SKILL_REL
        self.assertTrue(os.path.islink(link))
        self.assertEqual((project / ".gitignore").read_text(encoding="utf-8").splitlines(),
                         [migrate.GITIGNORE_ENTRY, migrate.SKILL_REL])


def _versioned(n: int | None, text: str = "## Listik\n\nprotocol text\n") -> str:
    return (f"<!-- listik-protocol: {n} -->\n\n" if n is not None else "") + text


class ProtocolVersionTests(unittest.TestCase):
    """Метка версии протокола: блок новее тела не откатывается без force (listik-e8za)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "AGENTS.md"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, body: str) -> bytes:
        self.path.write_text("# Proj\n\n" + migrate.block(body) + "\ntail\n", encoding="utf-8")
        return self.path.read_bytes()

    def test_protocol_version_parsing(self) -> None:
        self.assertEqual(migrate.protocol_version(_versioned(7)), 7)
        self.assertEqual(migrate.protocol_version("## Listik\n"), 0)
        self.assertEqual(migrate.protocol_version("<!-- listik-protocol: abc -->\n"), 0)
        self.assertEqual(
            migrate.protocol_version("see <!-- listik-protocol: 3 --> in prose\n"), 0)

    def test_newer_block_is_skipped(self) -> None:
        before = self._seed(_versioned(5))
        for dry in (False, True):
            self.assertEqual(
                migrate.upsert(self.path, body=_versioned(1, "new\n"), dry_run=dry),
                "skipped-newer")
            self.assertEqual(self.path.read_bytes(), before)

    def test_force_overwrites_newer_block(self) -> None:
        self._seed(_versioned(5))
        self.assertEqual(
            migrate.upsert(self.path, body=_versioned(1, "new\n"), force=True), "updated")
        self.assertIn(migrate.block(_versioned(1, "new\n")),
                      self.path.read_text(encoding="utf-8"))

    def test_older_block_is_updated(self) -> None:
        self._seed(_versioned(1))
        self.assertEqual(migrate.upsert(self.path, body=_versioned(5)), "updated")

    def test_unmarked_block_is_updated(self) -> None:
        self._seed("## Listik\nold\n")
        self.assertEqual(migrate.upsert(self.path, body=_versioned(1)), "updated")

    def test_unmarked_body_skips_marked_block(self) -> None:
        self._seed(_versioned(1))
        self.assertEqual(migrate.upsert(self.path, body="## Listik\nold\n"), "skipped-newer")

    def test_equal_marks_different_text_updated(self) -> None:
        self._seed(_versioned(2, "a\n"))
        self.assertEqual(migrate.upsert(self.path, body=_versioned(2, "b\n")), "updated")

    def test_force_identical_is_unchanged_and_dry_run_keeps_file(self) -> None:
        self._seed(_versioned(2))
        self.assertEqual(migrate.upsert(self.path, body=_versioned(2), force=True), "unchanged")
        before = self._seed(_versioned(1))
        self.assertEqual(
            migrate.upsert(self.path, body=_versioned(3), force=True, dry_run=True), "updated")
        self.assertEqual(self.path.read_bytes(), before)

    def test_migrate_all_report_and_remove(self) -> None:
        self._seed(_versioned(999))
        report = migrate.migrate_all([self.dir], verbose=False)
        self.assertEqual(report["skipped-newer"], [str(self.path)])
        forced = migrate.migrate_all([self.dir], verbose=False, force=True)
        self.assertEqual(forced["updated"], [str(self.path)])
        self.assertEqual(forced["skipped-newer"], [])
        self._seed(_versioned(999))
        removed = migrate.migrate_all([self.dir], remove_block=True, verbose=False)
        self.assertEqual(removed["removed"], [str(self.path)])
        self.assertNotIn(migrate.BEGIN, self.path.read_text(encoding="utf-8"))

    def test_repo_protocol_is_marked(self) -> None:
        protocol = (paths.ROOT_DIR / "docs" / "harness-protocol.md").read_text(encoding="utf-8")
        self.assertTrue(protocol.startswith("<!-- listik-protocol: "))
        self.assertGreaterEqual(migrate.protocol_version(migrate.body()), 1)


class InitProjectsForceCliTests(TempDbTestCase):
    """`init-projects` пропускает блок новее шаблона, `--force` его перезаписывает."""

    def test_skip_then_force(self) -> None:
        project = (self.tmp_path / "proj").resolve()
        project.mkdir()
        agents = project / "AGENTS.md"
        agents.write_text("# Project\n\n" + migrate.block(_versioned(999)), encoding="utf-8")
        before = agents.read_bytes()
        store.add_project(self.conn, path=str(project))
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}

        def run(*extra: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(LISTIK_BIN), "--local", "init-projects", *extra],
                capture_output=True, text=True, env=env, cwd=str(self.tmp_path))

        proc = run()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("! пропущен", proc.stdout)
        self.assertIn("новее шаблона: 1", proc.stdout)
        self.assertEqual(agents.read_bytes(), before)

        proc = run("--force")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("обновлено: 1", proc.stdout)
        text = agents.read_text(encoding="utf-8")
        self.assertIn(migrate.BEGIN + "\n<!-- listik-protocol: 3 -->\n", text)
        self.assertIn("## Listik — harness protocol", text)

if __name__ == "__main__":
    unittest.main()
