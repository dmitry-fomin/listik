"""`.listik.toml`: поиск вверх до корня git-репозитория и проверка содержимого (listik-r69k, a)."""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest

from listik import errors, project_file


class ProjectFileCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, text: str, *, raw: bytes | None = None) -> pathlib.Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if raw is not None:
            path.write_bytes(raw)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def git(self, rel: str = "", *, as_file: bool = False) -> None:
        target = self.root / rel / ".git"
        target.parent.mkdir(parents=True, exist_ok=True)
        if as_file:
            target.write_text("gitdir: elsewhere\n", encoding="utf-8")
        else:
            target.mkdir(exist_ok=True)

    def error(self, text: str, *, raw: bytes | None = None) -> str:
        path = self.write("repo/.listik.toml", text, raw=raw)
        self.git("repo")
        with self.assertRaises(errors.ListikError) as ctx:
            project_file.find(self.root / "repo")
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertIn(str(path), ctx.exception.message)
        return ctx.exception.message


class FindTests(ProjectFileCase):
    def test_found_in_parent(self) -> None:
        path = self.write("repo/.listik.toml", 'server = "local"\nproject = "demo"\n')
        self.git("repo")
        (self.root / "repo/a/b").mkdir(parents=True)
        found = project_file.find(self.root / "repo/a/b")
        self.assertEqual(found, {"path": str(path), "root": str(path.parent),
                                 "server": "local", "project": "demo"})

    def test_no_file_is_none(self) -> None:
        self.git("repo")
        (self.root / "repo/sub").mkdir(parents=True)
        self.assertIsNone(project_file.find(self.root / "repo/sub"))

    def test_git_dir_is_the_boundary(self) -> None:
        self.write(".listik.toml", 'server = "local"\nproject = "outer"\n')
        self.git("repo")
        (self.root / "repo/sub").mkdir(parents=True)
        self.assertIsNone(project_file.find(self.root / "repo/sub"))

    def test_git_file_is_the_boundary_too(self) -> None:
        self.write(".listik.toml", 'server = "local"\nproject = "outer"\n')
        self.git("wt", as_file=True)
        self.assertIsNone(project_file.find(self.root / "wt"))

    def test_without_git_walks_up(self) -> None:
        self.write(".listik.toml", 'server = "local"\nproject = "outer"\n')
        (self.root / "a/b").mkdir(parents=True)
        self.assertEqual(project_file.find(self.root / "a/b")["project"], "outer")

    def test_directory_with_the_name_is_skipped(self) -> None:
        self.write("repo/.listik.toml", 'server = "local"\nproject = "demo"\n')
        self.git("repo")
        (self.root / "repo/sub/.listik.toml").mkdir(parents=True)
        self.assertEqual(project_file.find(self.root / "repo/sub")["project"], "demo")

    def test_path_is_abspath_not_realpath(self) -> None:
        self.write("real/.listik.toml", 'server = "local"\nproject = "demo"\n')
        self.git("real")
        link = self.root / "link"
        link.symlink_to(self.root / "real")
        found = project_file.find(str(link))
        self.assertEqual(found["path"], os.path.join(str(link), ".listik.toml"))

    def test_server_url_is_normalized(self) -> None:
        self.write("repo/.listik.toml", 'server = "HTTPS://Hub.Example:443/listik/"\n'
                                        'project = "Zoloto585/repo"\n')
        self.git("repo")
        found = project_file.find(self.root / "repo")
        self.assertEqual(found["server"], "https://hub.example/listik")
        self.assertEqual(found["project"], "Zoloto585/repo")

    def test_local_any_case_and_bom(self) -> None:
        self.write("repo/.listik.toml", "", raw='\ufeffserver = "LOCAL"\nproject = "p"\n'.encode())
        self.git("repo")
        self.assertEqual(project_file.find(self.root / "repo")["server"], "local")


class ValidationTests(ProjectFileCase):
    def test_extra_key_refused_by_name(self) -> None:
        msg = self.error('server = "local"\nproject = "p"\ntoken = "tk-9f3a7c1e"\n')
        self.assertIn("token", msg)
        self.assertNotIn("tk-9f3a7c1e", msg)

    def test_not_toml(self) -> None:
        self.error("server = \n")

    def test_not_utf8(self) -> None:
        self.error("", raw=b'server = "\xff"\nproject = "p"\n')

    def test_unreadable(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root читает всё")
        path = self.write("repo/.listik.toml", 'server = "local"\nproject = "p"\n')
        path.chmod(0)
        self.git("repo")
        try:
            with self.assertRaises(errors.ListikError) as ctx:
                project_file.find(self.root / "repo")
            self.assertIn(str(path), ctx.exception.message)
        finally:
            path.chmod(0o600)

    def test_server_rules(self) -> None:
        self.error('project = "p"\n')
        self.error('server = 1\nproject = "p"\n')
        self.error('server = ["local"]\nproject = "p"\n')
        self.error('server = "ftp://h"\nproject = "p"\n')
        self.error('server = "http://u:p@h"\nproject = "p"\n')

    def test_project_rules(self) -> None:
        for bad in ('', 'project = ""', 'project = " p"', 'project = "p "',
                    'project = "/abs/path"', 'project = "~/repo"', "project = 5",
                    "[project]\nx = 1"):
            with self.subTest(bad=bad):
                self.error(f'server = "local"\n{bad}\n')


if __name__ == "__main__":
    unittest.main()
