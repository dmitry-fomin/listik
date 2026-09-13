"""Shared test scaffolding: an isolated, temporary sqlite database per test.

The real `listik.db` in the repository root is never touched by tests: the path is
always passed explicitly to `listik.db.init`/`listik.db.connect`, never through the
global `listik.paths.DB_PATH`.
"""
from __future__ import annotations

import pathlib
import tempfile
import unittest

from listik import db as db_mod

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "fixtures"


class TempDbTestCase(unittest.TestCase):
    """Base test case with a fresh, isolated sqlite database in a temp directory."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmpdir.name)
        self.db_path = self.tmp_path / "listik.db"
        self.conn = db_mod.init(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmpdir.cleanup()

    def fixture_copy(self, name: str) -> pathlib.Path:
        """Copy a fixture Markdown file into the temp dir and return its absolute path."""
        src = FIXTURES_DIR / name
        dst = self.tmp_path / name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return dst
