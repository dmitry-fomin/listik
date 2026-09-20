"""`scope.covers`/`scope.scopes_intersect` — пересечение областей записи (listik-aoid, порция a)."""
from __future__ import annotations

import unittest

from listik import scope


class CoversTests(unittest.TestCase):
    def test_equal_path(self) -> None:
        self.assertTrue(scope.covers("docs/API.md", "docs/API.md"))

    def test_directory_covers_file(self) -> None:
        self.assertTrue(scope.covers("docs", "docs/API.md"))

    def test_directory_does_not_cover_similar_prefix(self) -> None:
        self.assertFalse(scope.covers("docs", "docs-old/x"))

    def test_file_does_not_cover_parent_directory(self) -> None:
        self.assertFalse(scope.covers("docs/API.md", "docs"))

    def test_trailing_slash_on_entry_does_not_matter(self) -> None:
        self.assertTrue(scope.covers("docs/", "docs/API.md"))

    def test_empty_string_covers_nothing(self) -> None:
        self.assertFalse(scope.covers("", "docs/API.md"))
        self.assertFalse(scope.covers("docs", ""))
        self.assertFalse(scope.covers("", ""))


class ScopesIntersectTests(unittest.TestCase):
    def test_equal_path_intersects(self) -> None:
        self.assertTrue(scope.scopes_intersect(["pkg/a.py"], ["pkg/a.py"]))

    def test_directory_intersects_either_side(self) -> None:
        self.assertTrue(scope.scopes_intersect(["docs"], ["docs/API.md"]))
        self.assertTrue(scope.scopes_intersect(["docs/API.md"], ["docs"]))

    def test_no_intersection(self) -> None:
        self.assertFalse(scope.scopes_intersect(["pkg/a.py"], ["pkg/b.py"]))
        self.assertFalse(scope.scopes_intersect(["docs"], ["docs-old/x"]))

    def test_empty_list_intersects_nothing(self) -> None:
        self.assertFalse(scope.scopes_intersect([], ["pkg/a.py"]))
        self.assertFalse(scope.scopes_intersect(["pkg/a.py"], []))
        self.assertFalse(scope.scopes_intersect([], []))


if __name__ == "__main__":
    unittest.main()
