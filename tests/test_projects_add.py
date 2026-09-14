"""add_project: путь приводится к корню git (listik-mo3a), slug без дублей по регистру (listik-7vhk)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from listik import store
from tests.helpers import TempDbTestCase


class AddProjectPathTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.repo = (self.tmp_path / "myrepo").resolve()
        (self.repo / "pkg" / "inner").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    def test_nested_dir_is_moved_to_git_root(self) -> None:
        project = store.add_project(self.conn, path=str(self.repo / "pkg" / "inner"))
        self.assertEqual(project["path"], str(self.repo))
        self.assertEqual(project["slug"], "myrepo")
        self.assertEqual(project["path_adjusted_from"], str(self.repo / "pkg" / "inner"))

    def test_root_is_not_adjusted(self) -> None:
        project = store.add_project(self.conn, path=str(self.repo))
        self.assertEqual(project["path"], str(self.repo))
        self.assertIsNone(project["path_adjusted_from"])

    def test_non_git_dir_kept(self) -> None:
        plain = (self.tmp_path / "plain").resolve()
        plain.mkdir()
        project = store.add_project(self.conn, path=str(plain))
        self.assertEqual(project["path"], str(plain))
        self.assertFalse(project["git"])


class ProjectSlugCaseTests(TempDbTestCase):
    def _slugs(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT slug FROM projects")]

    def test_add_with_other_case_reuses_existing(self) -> None:
        store.add_project(self.conn, slug="listik")
        project = store.add_project(self.conn, slug="Listik")
        self.assertEqual(project["slug"], "listik")
        self.assertFalse(project["created"])
        self.assertEqual(self._slugs(), ["listik"])

    def test_upsert_with_other_case_reuses_existing(self) -> None:
        store.upsert_project(self.conn, "Zoloto585/orders")
        store.upsert_project(self.conn, "zoloto585/ORDERS", title="x")
        self.assertEqual(self._slugs(), ["Zoloto585/orders"])

    def test_new_slug_keeps_case(self) -> None:
        project = store.add_project(self.conn, slug="Zoloto585/orders")
        self.assertEqual(project["slug"], "Zoloto585/orders")
        self.assertTrue(project["created"])
