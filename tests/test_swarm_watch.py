"""Git-слой наблюдателя роя (listik-oltp, порция a): `changes`/`snapshot`/`probe`/
`outside_scope`/`common_files` и необязательный `env` у `worktree.git`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from listik import errors
from listik import swarm_watch
from listik import worktree as worktree_mod

HAS_GIT = shutil.which("git") is not None

F_LINES = [f"l{i}" for i in range(1, 9)]
F_CONTENT = "\n".join(F_LINES) + "\n"

MOD_CONTENT = (
    "def first():\n"
    "    a = 1\n"
    "    b = 2\n"
    "    return a + b\n"
    "\n"
    "\n"
    "def second():\n"
    "    x = 1\n"
    "    return x\n"
)


@unittest.skipUnless(HAS_GIT, "нет git")
class SwarmWatchCase(unittest.TestCase):
    """Временный git-репозиторий: `f.txt` (l1..l8) и `pkg/mod.py` (first/second)."""

    def setUp(self) -> None:
        self._env_patch = mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        self.tmp = tempfile.mkdtemp(prefix="listik-swarm-watch-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", "-b", "main", ".")
        self._git("config", "user.name", "Тест")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text(F_CONTENT, encoding="utf-8")
        pkg = self.repo / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text(MOD_CONTENT, encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "первый")
        self.main_head = self._git_out("rev-parse", "HEAD")

    # -------------------------------------------------------------- утилиты

    def _git(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def _git_out(self, *args: str, cwd=None) -> str:
        return self._git(*args, cwd=cwd).stdout.strip()

    def add_worktree(self, name: str) -> Path:
        path = self.repo / ".worktrees" / name
        self._git("worktree", "add", "-b", f"task/{name}", str(path), "HEAD")
        return path

    def _count_objects(self, tree) -> dict:
        out = self._git_out("count-objects", "-v", cwd=tree)
        result = {}
        for line in out.splitlines():
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
        return result

    # -------------------------------------------------------------- 1

    def test_probe_opposite_ends_of_file_merges_clean(self) -> None:
        tree_a = self.add_worktree("a")
        (tree_a / "f.txt").write_text("l0\n" + F_CONTENT, encoding="utf-8")
        self._git("commit", "-aqm", "a: l0", cwd=tree_a)

        tree_b = self.add_worktree("b")
        (tree_b / "f.txt").write_text(F_CONTENT + "l9\n", encoding="utf-8")
        self._git("commit", "-aqm", "b: l9", cwd=tree_b)

        result = swarm_watch.probe(str(self.repo), "task/a", "task/b")
        self.assertEqual(result, {"clean": True, "files": []})

    # -------------------------------------------------------------- 2

    def test_probe_same_function_conflicts(self) -> None:
        tree_a = self.add_worktree("a")
        (tree_a / "pkg" / "mod.py").write_text(
            MOD_CONTENT.replace("return a + b", "return a - b"), encoding="utf-8")
        self._git("commit", "-aqm", "a: minus", cwd=tree_a)

        tree_b = self.add_worktree("b")
        (tree_b / "pkg" / "mod.py").write_text(
            MOD_CONTENT.replace("return a + b", "return a * b"), encoding="utf-8")
        self._git("commit", "-aqm", "b: times", cwd=tree_b)

        result = swarm_watch.probe(str(self.repo), "task/a", "task/b")
        self.assertEqual(result, {"clean": False, "files": ["pkg/mod.py"]})

    # -------------------------------------------------------------- 3

    def test_snapshot_of_uncommitted_probes_cleanly(self) -> None:
        tree_a = self.add_worktree("a")
        (tree_a / "f.txt").write_text("l0\n" + F_CONTENT, encoding="utf-8")
        self._git("commit", "-aqm", "a: l0", cwd=tree_a)

        tree_b = self.add_worktree("b")
        (tree_b / "f.txt").write_text(F_CONTENT.replace("l4", "L4"), encoding="utf-8")

        tree_c = self.add_worktree("c")
        (tree_c / "f.txt").write_text(F_CONTENT.replace("l4", "X4"), encoding="utf-8")
        self._git("commit", "-aqm", "c: X4", cwd=tree_c)

        status_before = self._git_out("status", "--porcelain", cwd=tree_b)
        index_path = Path(self._git_out("rev-parse", "--git-path", "index", cwd=tree_b))
        if not index_path.is_absolute():
            index_path = tree_b / index_path
        index_before = index_path.read_bytes()
        refs_before = self._git_out("for-each-ref", cwd=self.repo)

        s = swarm_watch.snapshot(str(tree_b))
        self.assertNotEqual(s, worktree_mod.head_sha(str(tree_b)))

        status_after = self._git_out("status", "--porcelain", cwd=tree_b)
        index_after = index_path.read_bytes()
        refs_after = self._git_out("for-each-ref", cwd=self.repo)
        self.assertEqual(status_before, status_after)
        self.assertEqual(index_before, index_after)
        self.assertEqual(refs_before, refs_after)

        clean_result = swarm_watch.probe(str(self.repo), "task/a", s)
        self.assertTrue(clean_result["clean"])

        conflict_result = swarm_watch.probe(str(self.repo), "task/c", s)
        self.assertEqual(conflict_result, {"clean": False, "files": ["f.txt"]})

    # -------------------------------------------------------------- 4

    def test_new_file_both_sides_conflicts(self) -> None:
        tree_a = self.add_worktree("a")
        (tree_a / "new.txt").write_text("A\n", encoding="utf-8")

        tree_b = self.add_worktree("b")
        (tree_b / "new.txt").write_text("B\n", encoding="utf-8")

        result = swarm_watch.probe(str(self.repo), swarm_watch.snapshot(str(tree_a)),
                                   swarm_watch.snapshot(str(tree_b)))
        self.assertFalse(result["clean"])
        self.assertIn("new.txt", result["files"])

    # -------------------------------------------------------------- 5

    def test_snapshot_clean_and_dirty_determinism(self) -> None:
        tree = self.add_worktree("t")

        before = self._count_objects(tree)
        s_clean = swarm_watch.snapshot(str(tree))
        self.assertEqual(s_clean, worktree_mod.head_sha(str(tree)))
        after = self._count_objects(tree)
        self.assertEqual(before.get("count"), after.get("count"))
        self.assertEqual(before.get("in-pack"), after.get("in-pack"))

        (tree / "f.txt").write_text(F_CONTENT.replace("l4", "L4"), encoding="utf-8")

        s1 = swarm_watch.snapshot(str(tree))
        before2 = self._count_objects(tree)
        s2 = swarm_watch.snapshot(str(tree))
        after2 = self._count_objects(tree)
        self.assertEqual(s1, s2)
        self.assertEqual(before2.get("count"), after2.get("count"))
        self.assertEqual(before2.get("in-pack"), after2.get("in-pack"))

        s_hint_true = swarm_watch.snapshot(str(tree), dirty=True)
        self.assertEqual(s_hint_true, s1)

        s_hint_false = swarm_watch.snapshot(str(tree), dirty=False)
        self.assertEqual(s_hint_false, worktree_mod.head_sha(str(tree)))

    # -------------------------------------------------------------- 6

    def test_changes_composition(self) -> None:
        tree_b = self.add_worktree("b")
        (tree_b / "pkg" / "mod.py").write_text(
            MOD_CONTENT.replace("x = 1", "x = 2"), encoding="utf-8")
        self._git("commit", "-aqm", "b: second", cwd=tree_b)

        (tree_b / "f.txt").write_text(F_CONTENT.replace("l3", "L3"), encoding="utf-8")
        (tree_b / "new.txt").write_text("new\n", encoding="utf-8")
        (tree_b / "staged.txt").write_text("staged\n", encoding="utf-8")
        self._git("add", "staged.txt", cwd=tree_b)

        result = swarm_watch.changes(str(tree_b), self.main_head)
        self.assertEqual(result["files"], ["f.txt", "new.txt", "pkg/mod.py", "staged.txt"])
        self.assertTrue(result["dirty"])
        self.assertEqual(result["ahead"], 1)
        self.assertIsInstance(result["numstat"]["f.txt"]["added"], int)
        self.assertIsInstance(result["numstat"]["f.txt"]["deleted"], int)
        self.assertEqual(result["numstat"]["new.txt"], {"added": None, "deleted": None})
        self.assertEqual(set(result["numstat"].keys()), set(result["files"]))

    # -------------------------------------------------------------- 7

    def test_changes_base_when_main_moved(self) -> None:
        tree_b = self.add_worktree("b")

        (self.repo / "pkg" / "mod.py").write_text(
            MOD_CONTENT.replace("x = 1", "x = 3"), encoding="utf-8")
        self._git("commit", "-aqm", "main: second", cwd=self.repo)
        new_main_head = self._git_out("rev-parse", "HEAD", cwd=self.repo)

        result = swarm_watch.changes(str(tree_b), new_main_head)
        self.assertNotIn("pkg/mod.py", result["files"])
        self.assertEqual(result["merge_base"], self.main_head)

    # -------------------------------------------------------------- 8

    def test_numstat_deletions_and_binary(self) -> None:
        tree_append = self.add_worktree("append")
        (tree_append / "f.txt").write_text(F_CONTENT + "l9\nl10\n", encoding="utf-8")
        self._git("commit", "-aqm", "append", cwd=tree_append)
        result = swarm_watch.changes(str(tree_append), self.main_head)
        self.assertEqual(result["numstat"]["f.txt"], {"added": 2, "deleted": 0})

        tree_replace = self.add_worktree("replace")
        (tree_replace / "f.txt").write_text(F_CONTENT.replace("l4", "L4"), encoding="utf-8")
        self._git("commit", "-aqm", "replace", cwd=tree_replace)
        result = swarm_watch.changes(str(tree_replace), self.main_head)
        self.assertEqual(result["numstat"]["f.txt"], {"added": 1, "deleted": 1})

        tree_bin = self.add_worktree("bin")
        (tree_bin / "bin.dat").write_bytes(b"\x00\x01\x02")
        self._git("add", "bin.dat", cwd=tree_bin)
        self._git("commit", "-qm", "bin", cwd=tree_bin)
        result = swarm_watch.changes(str(tree_bin), self.main_head)
        self.assertEqual(result["numstat"]["bin.dat"], {"added": None, "deleted": None})
        self.assertIn("bin.dat", result["files"])

    # -------------------------------------------------------------- 9

    def test_rename_shows_both_sides(self) -> None:
        tree_b = self.add_worktree("b")
        self._git("mv", "f.txt", "g.txt", cwd=tree_b)
        self._git("commit", "-qm", "rename", cwd=tree_b)

        result = swarm_watch.changes(str(tree_b), self.main_head)
        self.assertIn("f.txt", result["files"])
        self.assertIn("g.txt", result["files"])

    # -------------------------------------------------------------- 10

    def test_outside_scope(self) -> None:
        self.assertEqual(
            swarm_watch.outside_scope(["pkg/mod.py", "f.txt"], ["pkg"]), ["f.txt"])
        self.assertEqual(
            swarm_watch.outside_scope(["pkg/mod.py", "f.txt"], []), ["pkg/mod.py", "f.txt"])
        self.assertEqual(
            swarm_watch.outside_scope(["pkg/mod.py", "f.txt"], ["pkg/mod.py"]), ["f.txt"])

    # -------------------------------------------------------------- 11

    def test_probe_raises_on_non_merge_errors(self) -> None:
        self.add_worktree("a")

        with self.assertRaises(errors.ListikError) as ctx:
            swarm_watch.probe(str(self.repo), "task/a", "no-such-ref")
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("not something we can merge", ctx.exception.message)

        mktree = subprocess.run(["git", "-C", str(self.repo), "mktree"],
                                input="", capture_output=True, text=True)
        self.assertEqual(mktree.returncode, 0, mktree.stderr)
        empty_tree = mktree.stdout.strip()
        commit = subprocess.run(
            ["git", "-C", str(self.repo), "commit-tree", empty_tree, "-m", "orphan"],
            capture_output=True, text=True)
        self.assertEqual(commit.returncode, 0, commit.stderr)
        orphan_sha = commit.stdout.strip()

        with self.assertRaises(errors.ListikError) as ctx:
            swarm_watch.probe(str(self.repo), "task/a", orphan_sha)
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("128", ctx.exception.message)

    # -------------------------------------------------------------- 12

    def test_mocked_argv_no_optional_locks_and_env(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        fd, fake_index = tempfile.mkstemp(prefix="listik-swarm-watch-mock-index-")
        os.close(fd)
        Path(fake_index).write_bytes(b"mock-index-bytes")
        self.addCleanup(lambda: os.path.exists(fake_index) and os.remove(fake_index))

        def fake_run(argv, **kwargs):
            calls.append((list(argv), kwargs))
            result = mock.Mock()
            result.returncode = 0
            result.stderr = ""
            if "rev-list" in argv and "--count" in argv:
                result.stdout = "1"
            elif "status" in argv and "--porcelain" in argv:
                result.stdout = " M f.txt\n"
            elif "rev-parse" in argv and "--git-path" in argv and "index" in argv:
                result.stdout = fake_index
            elif "diff" in argv and "--numstat" in argv:
                result.stdout = "1\t0\tf.txt\n"
            elif "diff" in argv and "--name-only" in argv:
                result.stdout = "f.txt\n"
            elif "write-tree" in argv or "commit-tree" in argv:
                result.stdout = "a" * 40
            elif "merge-tree" in argv:
                result.stdout = "b" * 40
            else:
                result.stdout = ""
            return result

        with mock.patch.object(worktree_mod.subprocess, "run", side_effect=fake_run):
            swarm_watch.changes("/fake/tree", "0" * 40)
            swarm_watch.snapshot("/fake/tree", dirty=True)
            swarm_watch.probe("/fake/repo", "a", "b")

        self.assertTrue(calls)
        # Настоящая («реальная») копия индекса, из которой diff читал, ни разу не
        # тронута: наш код только читает её через shutil.copyfile, никогда не
        # открывает на запись.
        self.assertEqual(Path(fake_index).read_bytes(), b"mock-index-bytes")

        for argv, kwargs in calls:
            self.assertEqual(argv[0], "git")
            self.assertEqual(argv[1], "--no-optional-locks")

            is_index_write = (
                "read-tree" in argv or ("add" in argv and "-A" in argv)
                or "write-tree" in argv
                or (("diff" in argv) and ("--name-only" in argv or "--numstat" in argv)))
            if is_index_write:
                env = kwargs.get("env")
                self.assertIsNotNone(env)
                self.assertIn("GIT_INDEX_FILE", env)
            elif "commit-tree" in argv:
                env = kwargs.get("env")
                self.assertIsNotNone(env)
                self.assertNotIn("GIT_INDEX_FILE", env)
                for key in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                            "GIT_COMMITTER_EMAIL", "GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
                    self.assertIn(key, env)
            else:
                self.assertIsNone(kwargs.get("env"))

            if (("diff" in argv and ("--name-only" in argv or "--numstat" in argv))
                    or "merge-tree" in argv
                    or ("ls-files" in argv and "--others" in argv)):
                self.assertEqual(argv[4:6], ["-c", "core.quotepath=false"])

    # -------------------------------------------------------------- 13

    def test_worktree_git_env_parameter(self) -> None:
        def fake_run(argv, **kwargs):
            result = mock.Mock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result

        with mock.patch.object(worktree_mod.subprocess, "run", side_effect=fake_run) as run:
            worktree_mod.git("/repo", "status", env={"X": "1"})
        _, kwargs = run.call_args
        self.assertIn("env", kwargs)
        self.assertEqual(kwargs["env"]["X"], "1")
        self.assertIn("PATH", kwargs["env"])  # унаследованный os.environ тоже там

        with mock.patch.object(worktree_mod.subprocess, "run", side_effect=fake_run) as run:
            worktree_mod.git("/repo", "status")
        _, kwargs = run.call_args
        self.assertIsNone(kwargs.get("env"))

    # -------------------------------------------------------------- 14

    def test_changes_does_not_rewrite_worktree_index(self) -> None:
        tree = self.add_worktree("t")

        def make_racy() -> None:
            content = (tree / "f.txt").read_bytes()
            (tree / "f.txt").write_bytes(content)
            future = time.time() + 5
            os.utime(tree / "f.txt", (future, future))

        index_path = Path(self._git_out("rev-parse", "--git-path", "index", cwd=tree))
        if not index_path.is_absolute():
            index_path = tree / index_path

        make_racy()
        before = index_path.read_bytes()
        result = swarm_watch.changes(str(tree), self.main_head)
        after = index_path.read_bytes()
        self.assertEqual(before, after)

        # racy-clean f.txt (stat разошёлся, содержимое то же) не должен попасть
        # в files/numstat как «тронутый» — иначе ключи numstat и files
        # разъезжаются и outside_scope врёт про несуществующую правку.
        self.assertEqual(result["files"], [])
        self.assertEqual(result["numstat"], {})
        self.assertFalse(result["dirty"])
        self.assertEqual(swarm_watch.outside_scope(result["files"], ["pkg"]), [])

        # Контроль: тот же сценарий без --no-optional-locks индекс переписывает.
        make_racy()
        before2 = index_path.read_bytes()
        subprocess.run(["git", "-C", str(tree), "diff", self.main_head],
                       capture_output=True, text=True)
        after2 = index_path.read_bytes()
        self.assertNotEqual(before2, after2)

    # -------------------------------------------------------------- 15

    def test_non_ascii_paths(self) -> None:
        docs = self.repo / "док"
        docs.mkdir()
        (docs / "файл.txt").write_text("base\n", encoding="utf-8")
        self._git("add", ".", cwd=self.repo)
        self._git("commit", "-qm", "не-ascii база", cwd=self.repo)
        base_with_docs = self._git_out("rev-parse", "HEAD", cwd=self.repo)

        tree_b = self.add_worktree("b")
        (tree_b / "док" / "файл.txt").write_text("b version\n", encoding="utf-8")
        self._git("commit", "-aqm", "b: правка", cwd=tree_b)
        (tree_b / "новый.txt").write_text("new\n", encoding="utf-8")

        tree_c = self.add_worktree("c")
        (tree_c / "док" / "файл.txt").write_text("c version\n", encoding="utf-8")
        self._git("commit", "-aqm", "c: правка", cwd=tree_c)

        result = swarm_watch.changes(str(tree_b), base_with_docs)
        self.assertIn("док/файл.txt", result["files"])
        self.assertIn("новый.txt", result["files"])
        self.assertEqual(set(result["numstat"].keys()), set(result["files"]))

        probe_result = swarm_watch.probe(str(self.repo), "task/c", swarm_watch.snapshot(str(tree_b)))
        self.assertEqual(probe_result, {"clean": False, "files": ["док/файл.txt"]})

        self.assertEqual(swarm_watch.outside_scope(["док/файл.txt"], ["док"]), [])

    # -------------------------------------------------------------- 16

    def test_common_files(self) -> None:
        self.assertEqual(swarm_watch.common_files(["b", "a", "a"], ["a", "c"]), ["a"])
        self.assertEqual(swarm_watch.common_files(["x"], ["y"]), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
