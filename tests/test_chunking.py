"""Tests for listik.documents.split_markdown — no database, no network."""
from __future__ import annotations

import re
import unittest

from listik import documents
from tests.helpers import FIXTURES_DIR

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
UNIQUE_PHRASE = "ксилофонный-градиент-42"


def _read(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def _heading_ranges(content: str) -> list[tuple[str, int, int]]:
    """Independent reference: (heading, first_line, last_line) for every heading section,
    ignoring fenced code blocks — used only to check start_line placement."""
    lines = content.splitlines()
    ranges: list[tuple[str, int, int]] = []
    current_heading = None
    current_start = 1
    in_fence = False
    for line_no, line in enumerate(lines, 1):
        if re.match(r"^\s*```", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        if current_heading is not None or current_start == 1:
            ranges.append((current_heading, current_start, line_no - 1))
        current_heading = match.group(2).strip()
        current_start = line_no
    ranges.append((current_heading, current_start, len(lines)))
    return [r for r in ranges if r[0] is not None]


class LongSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = _read("long-spec.md")
        self.lines = self.content.splitlines()
        self.chunks = documents.split_markdown(self.content)

    def test_every_heading_has_a_chunk(self) -> None:
        headings_in_file = {m.group(2).strip() for line in self.lines
                             if (m := _HEADING.match(line))}
        headings_in_chunks = {c["heading"] for c in self.chunks}
        self.assertTrue(headings_in_file.issubset(headings_in_chunks))

    def test_h3_breadcrumb_contains_h1_and_h2(self) -> None:
        h3_chunks = [c for c in self.chunks if c["heading"] in
                     ("Первый подраздел", "Второй подраздел", "Третий подраздел")]
        self.assertTrue(h3_chunks)
        for c in h3_chunks:
            parts = c["breadcrumb"].split(" / ")
            self.assertEqual(len(parts), 3)
            self.assertEqual(parts[-1], c["heading"])

    def test_text_starts_with_breadcrumb(self) -> None:
        for c in self.chunks:
            if c["breadcrumb"]:
                self.assertTrue(c["text"].startswith(c["breadcrumb"]))

    def test_line_ranges_within_file_and_section(self) -> None:
        ranges = _heading_ranges(self.content)
        by_heading = {}
        for heading, start, end in ranges:
            by_heading.setdefault(heading, []).append((start, end))
        for c in self.chunks:
            self.assertGreaterEqual(c["start_line"], 1)
            self.assertLessEqual(c["start_line"], c["end_line"])
            self.assertLessEqual(c["end_line"], len(self.lines))
            spans = by_heading.get(c["heading"], [])
            self.assertTrue(
                any(start <= c["start_line"] <= end for start, end in spans),
                f"{c['heading']!r} chunk start_line {c['start_line']} not in {spans}",
            )

    def test_big_section_split_and_unique_phrase(self) -> None:
        big = [c for c in self.chunks if c["heading"] == "Большой раздел"]
        self.assertGreaterEqual(len(big), 3)
        hits = [c for c in big if UNIQUE_PHRASE in c["text"]]
        self.assertEqual(len(hits), 1)
        # Phrase must not leak into chunks of any other heading.
        others = [c for c in self.chunks if c["heading"] != "Большой раздел"]
        self.assertFalse(any(UNIQUE_PHRASE in c["text"] for c in others))

    def test_overlap_between_plain_paragraph_chunks(self) -> None:
        big = [c for c in self.chunks if c["heading"] == "Большой раздел"]
        found = False
        for a, b in zip(big, big[1:]):
            body_a = a["text"][len(a["breadcrumb"] + "\n\n"):]
            body_b = b["text"][len(b["breadcrumb"] + "\n\n"):]
            tail = body_a[-documents.OVERLAP:]
            if tail and body_b.startswith(tail):
                found = True
                break
        self.assertTrue(found, "expected at least one overlapping pair in the long section")

    def test_determinism(self) -> None:
        self.assertEqual(self.chunks, documents.split_markdown(self.content))

    def test_max_chars_bound(self) -> None:
        for c in self.chunks:
            self.assertLessEqual(len(c["text"]), documents.MAX_CHARS)


class FencedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = _read("fenced.md")
        self.chunks = documents.split_markdown(self.content)

    def test_fake_heading_inside_fence_is_not_a_heading(self) -> None:
        headings = {c["heading"] for c in self.chunks}
        self.assertNotIn("not heading", headings)

    def test_real_heading_after_fence_is_detected(self) -> None:
        headings = {c["heading"] for c in self.chunks}
        self.assertIn("После кода", headings)

    def test_fenced_code_stays_in_its_section(self) -> None:
        code_chunks = [c for c in self.chunks if "# not heading" in c["text"]]
        self.assertTrue(code_chunks)
        for c in code_chunks:
            self.assertEqual(c["heading"], "Раздел с кодом")

    def test_max_chars_bound(self) -> None:
        for c in self.chunks:
            self.assertLessEqual(len(c["text"]), documents.MAX_CHARS)

    def test_determinism(self) -> None:
        self.assertEqual(self.chunks, documents.split_markdown(self.content))


class ListsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.content = _read("lists.md")
        self.chunks = documents.split_markdown(self.content)

    def test_every_chunk_starts_with_a_list_marker(self) -> None:
        self.assertTrue(self.chunks)
        marker_re = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+")
        for c in self.chunks:
            body = c["text"][len(c["breadcrumb"] + "\n\n"):] if c["breadcrumb"] else c["text"]
            self.assertRegex(body, marker_re)

    def test_no_item_split_mid_way(self) -> None:
        for c in self.chunks:
            body = c["text"][len(c["breadcrumb"] + "\n\n"):] if c["breadcrumb"] else c["text"]
            for line in body.splitlines():
                self.assertTrue(line.startswith("- "), f"unexpected continuation line: {line!r}")

    def test_no_overlap_between_item_groups(self) -> None:
        for a, b in zip(self.chunks, self.chunks[1:]):
            body_a_lines = (a["text"][len(a["breadcrumb"] + "\n\n"):]).splitlines()
            body_b_lines = (b["text"][len(b["breadcrumb"] + "\n\n"):]).splitlines()
            self.assertNotIn(body_b_lines[0], body_a_lines)

    def test_max_chars_bound(self) -> None:
        for c in self.chunks:
            self.assertLessEqual(len(c["text"]), documents.MAX_CHARS)

    def test_determinism(self) -> None:
        self.assertEqual(self.chunks, documents.split_markdown(self.content))


class OverlapTailBudgetTests(unittest.TestCase):
    """Regression test for the accumulation-budget defect (§2): a chunk that starts with
    an overlap tail must still respect the char limit, even when tail + paragraph would
    otherwise exceed it."""

    def test_overflow_on_overlap_tail(self) -> None:
        limit = 200
        # First paragraph fills the accumulator close to the limit so its tail is exactly
        # OVERLAP-ish long; the next paragraph is deliberately sized so that
        # tail + "\n\n" + paragraph would exceed `limit` if the tail were kept whole.
        para1 = "A" * 190
        para2 = "B" * 190
        content = "# h\n\n" + para1 + "\n\n" + para2 + "\n\n" + ("C" * 190) + "\n"
        chunks = documents.split_markdown(content, limit=limit)
        for c in chunks:
            self.assertLessEqual(len(c["text"]), limit)

    def test_determinism(self) -> None:
        limit = 200
        para1 = "A" * 190
        para2 = "B" * 190
        content = "# h\n\n" + para1 + "\n\n" + para2 + "\n\n" + ("C" * 190) + "\n"
        self.assertEqual(documents.split_markdown(content, limit=limit),
                          documents.split_markdown(content, limit=limit))


class ChecklistTests(unittest.TestCase):
    def test_fixture_is_a_short_checklist(self) -> None:
        content = _read("checklist.md")
        chunks = documents.split_markdown(content)
        checklist_items = [line for line in content.splitlines() if line.startswith("- [ ]")]
        self.assertGreaterEqual(len(checklist_items), 2)
        joined = "\n".join(c["text"] for c in chunks)
        for item in checklist_items:
            self.assertIn(item, joined)


if __name__ == "__main__":
    unittest.main()
