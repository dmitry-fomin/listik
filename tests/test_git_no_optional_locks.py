"""Опрос чужого дерева не должен брать `index.lock` и срывать коммит воркера (listik-8q6c).

`worktree.git` и `store._git_value` — единственные места, где Listik собирает argv `git`;
оба обязаны звать git с глобальным флагом `--no-optional-locks`, который запрещает git
переписывать индекс при опросе (`status`/`diff`) в дереве, где в это время может идти
`git add -A && git commit` воркера.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from listik import store
from listik import worktree as worktree_mod

ROOT = Path(__file__).resolve().parents[1]
HAS_GIT = shutil.which("git") is not None


def _ok_result() -> mock.Mock:
    """Результат `subprocess.run`, достаточный для `git(..., check=True)`."""
    result = mock.Mock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


# --------------------------------------------------------------- моки argv


class WorktreeArgvTests(unittest.TestCase):
    """`worktree.git`/`git_out`/`git_ok` собирают argv с `--no-optional-locks`."""

    def test_git_prepends_flag(self) -> None:
        with mock.patch.object(worktree_mod.subprocess, "run", return_value=_ok_result()) as run:
            worktree_mod.git("/repo", "status", "--porcelain")
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "git")
        self.assertEqual(argv[1], "--no-optional-locks")
        self.assertEqual(argv[2:4], ["-C", "/repo"])
        self.assertEqual(argv[4:], ["status", "--porcelain"])

    def test_git_out_prepends_flag(self) -> None:
        with mock.patch.object(worktree_mod.subprocess, "run", return_value=_ok_result()) as run:
            worktree_mod.git_out("/repo", "status", "--porcelain")
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "git")
        self.assertEqual(argv[1], "--no-optional-locks")
        self.assertEqual(argv[2:4], ["-C", "/repo"])
        self.assertEqual(argv[4:], ["status", "--porcelain"])

    def test_git_ok_prepends_flag(self) -> None:
        with mock.patch.object(worktree_mod.subprocess, "run", return_value=_ok_result()) as run:
            worktree_mod.git_ok("/repo", "status", "--porcelain")
        argv = run.call_args[0][0]
        self.assertEqual(argv[0], "git")
        self.assertEqual(argv[1], "--no-optional-locks")
        self.assertEqual(argv[2:4], ["-C", "/repo"])
        self.assertEqual(argv[4:], ["status", "--porcelain"])


class StoreArgvTests(unittest.TestCase):
    """`store._git_value` собирает argv с `--no-optional-locks`, `timeout=5` сохранён."""

    def test_git_value_prepends_flag(self) -> None:
        with mock.patch.object(store.subprocess, "run", return_value=_ok_result()) as run:
            store._git_value(Path("/repo"), "status", "--porcelain")
        args, kwargs = run.call_args
        argv = args[0]
        self.assertEqual(argv[0], "git")
        self.assertEqual(argv[1], "--no-optional-locks")
        self.assertEqual(argv[2:4], ["-C", "/repo"])
        self.assertEqual(argv[4:], ["status", "--porcelain"])
        self.assertEqual(kwargs.get("timeout"), 5)


# --------------------------------------------------------------- настоящий git


@unittest.skipUnless(HAS_GIT, "нет git")
class RealGitCase(unittest.TestCase):
    """Временный git-репозиторий с одним закоммиченным файлом."""

    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="listik-no-optional-locks-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = Path(self.tmp) / "repo"
        self.repo.mkdir()
        self._git("init", "-q", ".")
        self._git("config", "user.name", "Тест")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "commit.gpgsign", "false")
        self.tracked = self.repo / "a.txt"
        self.tracked.write_text("a\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-qm", "первый")

    def _git(self, *args: str) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc


class RealGitSubcommandsTests(RealGitCase):
    """Флаг не ломает подкоманды, которые обёртка использует."""

    def test_is_dirty_clean_and_dirty(self) -> None:
        self.assertFalse(worktree_mod.is_dirty(str(self.repo)))
        (self.repo / "b.txt").write_text("b\n", encoding="utf-8")
        self.assertTrue(worktree_mod.is_dirty(str(self.repo)))

    def test_head_sha_base_info_ensure(self) -> None:
        sha = worktree_mod.head_sha(str(self.repo))
        self.assertEqual(len(sha), 40)
        base = worktree_mod.base_info(str(self.repo))
        self.assertEqual(base["sha"], sha)
        self.assertTrue(base["sha7"])
        self.assertEqual(base["subject"], "первый")

        out = worktree_mod.ensure(str(self.repo), "listik-test")
        self.assertEqual(out["status"], "created")
        self.assertTrue(os.path.isdir(out["path"]))


class RacyIndexTests(RealGitCase):
    """Различающая проверка: с флагом `status` не переписывает `.git/index`."""

    def _make_stat_dirty(self) -> None:
        """Перезаписать файл тем же содержимым и сдвинуть mtime — «racily clean»."""
        content = self.tracked.read_bytes()
        self.tracked.write_bytes(content)
        future = time.time() + 5
        os.utime(self.tracked, (future, future))

    def test_worktree_is_dirty_does_not_rewrite_index(self) -> None:
        self._make_stat_dirty()
        index_path = self.repo / ".git" / "index"
        before = index_path.read_bytes()
        self.assertFalse(worktree_mod.is_dirty(str(self.repo)))
        after = index_path.read_bytes()
        self.assertEqual(before, after)

    def test_store_git_value_does_not_rewrite_index(self) -> None:
        self._make_stat_dirty()
        index_path = self.repo / ".git" / "index"
        before = index_path.read_bytes()
        status = store._git_value(self.repo, "status", "--porcelain")
        self.assertFalse(status)
        after = index_path.read_bytes()
        self.assertEqual(before, after)


class IndexLockSmokeTests(RealGitCase):
    """Дымовая проверка: заранее созданный `index.lock` не роняет вызов исключением."""

    def test_is_dirty_survives_existing_lock(self) -> None:
        lock = self.repo / ".git" / "index.lock"
        lock.write_text("чужой коммит в процессе\n", encoding="utf-8")
        lock_mtime = lock.stat().st_mtime
        content_before = self.tracked.read_bytes()

        result = worktree_mod.is_dirty(str(self.repo))

        self.assertIsInstance(result, bool)
        self.assertTrue(lock.exists())
        self.assertEqual(lock.stat().st_mtime, lock_mtime)
        self.assertEqual(self.tracked.read_bytes(), content_before)


# --------------------------------------------------------------- grep по исходникам


class GitArgvGrepTests(unittest.TestCase):
    """Каждое собранное argv `git` в исходниках несёт `--no-optional-locks`."""

    def _paths(self) -> list[Path]:
        return sorted((ROOT / "listik").glob("*.py")) + [ROOT / "bin" / "listik"]

    def test_every_git_argv_has_the_flag(self) -> None:
        pattern = re.compile(r'\["git"\s*,\s*([^,\]]+)')
        offenders = []
        for path in self._paths():
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                following = match.group(1).strip()
                if following != '"--no-optional-locks"':
                    offenders.append(f"{path}: {match.group(0)}")
        self.assertEqual(offenders, [],
                         "argv git без --no-optional-locks:\n" + "\n".join(offenders))

    def test_documents_calls_git_only_through_store(self) -> None:
        text = (ROOT / "listik" / "documents.py").read_text(encoding="utf-8")
        self.assertNotIn('subprocess.run(["git"', text)
        self.assertNotIn("subprocess.run([\"git\"", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
