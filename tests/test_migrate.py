"""Tests for listik.migrate — the AGENTS.md/CLAUDE.md protocol block."""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from listik import migrate, paths

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
        self.assertIn("### Роли по этапам", rendered)
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


if __name__ == "__main__":
    unittest.main()
