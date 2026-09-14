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
    """Относительный путь — от корня проектов, не от чьего-то cwd (listik-mo3a, listik-i23u)."""

    def _count_projects(self) -> int:
        return self.conn.execute("SELECT count(*) FROM projects").fetchone()[0]

    def test_store_resolves_relative_path_from_projects_root(self) -> None:
        """listik-i23u: относительный путь — от корня проектов, не от cwd процесса."""
        root = (self.tmp_path / "Projects").resolve()
        (root / "Zoloto585" / "Parser").mkdir(parents=True)
        (self.tmp_path / "elsewhere").mkdir()
        old_cwd = os.getcwd()
        try:
            os.chdir(self.tmp_path / "elsewhere")
            with mock.patch.object(store.paths, "PROJECTS_ROOT", root):
                project = store.add_project(self.conn, path="Zoloto585/Parser",
                                            slug="Zoloto585Parser")
        finally:
            os.chdir(old_cwd)
        self.assertEqual(Path(project["path"]), root / "Zoloto585" / "Parser")

    def test_missing_relative_dir_names_full_path(self) -> None:
        root = (self.tmp_path / "Projects").resolve()
        root.mkdir()
        with mock.patch.object(store.paths, "PROJECTS_ROOT", root):
            with self.assertRaises(ValueError) as ctx:
                store.add_project(self.conn, path="Nope/Repo")
        self.assertIn(str(root / "Nope" / "Repo"), str(ctx.exception))
        self.assertEqual(self._count_projects(), 0)

    def test_update_project_relative_path_from_root(self) -> None:
        root = (self.tmp_path / "Projects").resolve()
        store.add_project(self.conn, slug="p1")
        with mock.patch.object(store.paths, "PROJECTS_ROOT", root):
            out = store.update_project(self.conn, "p1", path="a/b")
        self.assertEqual(out["path"], str(root / "a" / "b"))

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

    def test_relative_path_from_projects_root_despite_foreign_cwd(self) -> None:
        # Ловушки mo3a/i23u: cwd сервера (чужой worktree с таким же подкаталогом)
        # не влияет — путь берётся от корня проектов.
        root = (self.tmp_path / "Projects").resolve()
        (root / "Zoloto585" / "Parser").mkdir(parents=True)
        wt = self.tmp_path / "wt"
        (wt / "Zoloto585" / "Parser").mkdir(parents=True)
        old_cwd = os.getcwd()
        try:
            os.chdir(wt)
            with mock.patch.object(store.paths, "PROJECTS_ROOT", root):
                status, project = self.post({"path": "Zoloto585/Parser", "slug": "Zoloto585Parser"})
        finally:
            os.chdir(old_cwd)
        self.assertEqual(status, 201)
        self.assertEqual(Path(project["path"]), root / "Zoloto585" / "Parser")

    def test_health_reports_runtime(self) -> None:
        status, data = server.handle("GET", "/api/health", {}, {}, authed=True)
        self.assertEqual(data["runtime"]["cwd"], os.getcwd())
        self.assertEqual(data["runtime"]["code_dir"], str(server.paths.ROOT_DIR))

    def test_runtime_warns_for_linked_worktree(self) -> None:
        main = self.tmp_path / "main"
        subprocess.run(["git", "init", "-q", str(main)], check=True)
        subprocess.run(["git", "-C", str(main), "-c", "user.name=t", "-c", "user.email=t@t",
                        "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        wt = self.tmp_path / "wt2"
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(wt)], check=True)
        self.assertFalse(server.runtime_info(main)["worktree"])
        info = server.runtime_info(wt)
        self.assertTrue(info["worktree"])
        self.assertEqual(Path(info["main_repo"]), main.resolve())
        self.assertIn("worktree", info["warning"])

    def test_absolute_nested_path_is_adjusted_to_git_root(self) -> None:
        repo = (self.tmp_path / "repo").resolve()
        (repo / "pkg" / "inner").mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        status, project = self.post({"path": str(repo / "pkg" / "inner")})
        self.assertEqual(status, 201)
        self.assertEqual(project["path"], str(repo))
        self.assertEqual(project["path_adjusted_from"], str(repo / "pkg" / "inner"))
