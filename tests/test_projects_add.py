"""add_project: путь приводится к корню git (listik-mo3a), slug без дублей по регистру (listik-7vhk)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from listik import server, store
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN


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


class RelativePathTests(TempDbTestCase):
    """Относительный путь отклоняется, а не разрешается от чьего-то cwd (listik-mo3a)."""

    def _count_projects(self) -> int:
        return self.conn.execute("SELECT count(*) FROM projects").fetchone()[0]

    def test_store_refuses_relative_path(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            store.add_project(self.conn, path="Listik")
        self.assertIn("абсолютн", str(ctx.exception))
        self.assertIn("Listik", str(ctx.exception))
        self.assertEqual(self._count_projects(), 0)

    def test_cli_resolves_relative_path_in_its_own_cwd(self) -> None:
        """CLI по-прежнему принимает относительный путь: разрешает его сам до запроса."""
        repo = (self.tmp_path / "repo").resolve()
        (repo / "pkg").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        proc = subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", "projects", "--add", "repo/pkg"],
            capture_output=True, text=True, env=env, cwd=str(self.tmp_path))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn(str(repo), proc.stdout, "CLI не напечатал итоговый путь")
        row = self.conn.execute("SELECT path FROM projects").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(Path(row[0]).resolve(), repo)


class ProjectsApiPathTests(TempDbTestCase):
    """POST /api/projects: относительный path — 400 bad_argument, абсолютный — к корню git."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)

    def post(self, body: dict) -> tuple[int, dict]:
        return server.handle("POST", "/api/projects", {}, body, authed=True)

    def test_relative_path_is_rejected_even_if_it_exists(self) -> None:
        # Точная ловушка из карточки: «Listik» при cwd=~/Projects/Listik разрешался
        # в существующий ~/Projects/Listik/Listik (регистронезависимая APFS).
        old_cwd = os.getcwd()
        with self.assertRaises(server.ApiError) as ctx:
            try:
                os.chdir(self.tmp_path)
                (self.tmp_path / "Listik" / "Listik").mkdir(parents=True)
                self.post({"path": "Listik"})
            finally:
                os.chdir(old_cwd)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("абсолютн", ctx.exception.message)
        self.assertIn("Listik", ctx.exception.message)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM projects").fetchone()[0], 0)

    def test_absolute_nested_path_is_adjusted_to_git_root(self) -> None:
        repo = (self.tmp_path / "repo").resolve()
        (repo / "pkg" / "inner").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        status, project = self.post({"path": str(repo / "pkg" / "inner")})
        self.assertEqual(status, 201)
        self.assertEqual(project["path"], str(repo))
        self.assertEqual(project["path_adjusted_from"], str(repo / "pkg" / "inner"))
