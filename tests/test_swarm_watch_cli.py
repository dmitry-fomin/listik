"""`listik watch` (listik-oltp, порция b): активное множество, пробы, журнал
расхождений. Команда только наблюдает и пишет журнал — реакций (заморозки) нет,
`decisions` в выводе всегда пуст (порция c).
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from importlib.machinery import SourceFileLoader
from unittest import mock

from listik import errors, paths, store
from listik import swarm_watch
from listik import worktree as worktree_mod
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN

HAS_GIT = shutil.which("git") is not None

MOD_CONTENT = (
    "def first():\n"
    "    a = 1\n"
    "    b = 2\n"
    "    return a + b\n"
)


class _StorePort:
    """Порт `swarm_watch.scan` напрямую на `store` — для сценариев, где нужно
    мокать `snapshot`/`probe` внутри одного процесса (subprocess CLI это не даёт)."""

    def __init__(self, conn) -> None:
        self.conn = conn

    def show(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)

    def comment(self, task_id: str, text: str) -> dict:
        return store.add_comment(self.conn, task_id, text, author="agent:listik-swarm",
                                 kind="journal")


@unittest.skipUnless(HAS_GIT, "нет git")
class WatchCliCase(TempDbTestCase):
    """Временный git-репозиторий «demo» + деревья задач + CLI в `--local`."""

    def setUp(self) -> None:
        super().setUp()
        self._env_patch = mock.patch.dict(
            os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        self.repo = self.tmp_path / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.name", "Тест")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "f.txt").write_text("base\n", encoding="utf-8")
        pkg = self.repo / "pkg"
        pkg.mkdir()
        (pkg / "mod.py").write_text(MOD_CONTENT, encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "первый")
        self.project_path = str(self.repo.resolve())
        store.add_project(self.conn, path=self.project_path, slug="demo")

    # -------------------------------------------------------------- git

    def git(self, *args: str, cwd=None) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def git_out(self, *args: str, cwd=None) -> str:
        return self.git(*args, cwd=cwd).stdout.strip()

    # -------------------------------------------------------------- задачи/деревья

    def make_task(self, title: str = "Проба", task_id: str | None = None, **fields) -> str:
        tid = store.create_task(self.conn, title=title, project="demo", task_id=task_id)["id"]
        if fields:
            store.update_task(self.conn, tid, **fields)
        return tid

    def make_tree(self, task_id: str) -> str:
        out = worktree_mod.ensure(self.project_path, task_id)
        store.update_task(self.conn, task_id, worktree=out["path"], branch=out["branch"])
        return out["path"]

    def edit(self, tree: str, name: str, text: str, *, commit: bool = True,
            message: str = "правка") -> None:
        with open(os.path.join(tree, name), "w", encoding="utf-8") as fh:
            fh.write(text)
        if commit:
            self.git("add", ".", cwd=tree)
            self.git("commit", "-qm", message, cwd=tree)

    def card(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)

    def comments(self, task_id: str) -> list[dict]:
        return self.card(task_id)["comments"]

    def marks(self, task_id: str, mark: str = swarm_watch.FIRST_CHANGE_MARK) -> list[dict]:
        return [c for c in self.comments(task_id)
                if c["author"] == "agent:listik-swarm" and c["kind"] == "journal"
                and c["text"].startswith(mark)]

    def scope_marks(self, task_id: str) -> list[dict]:
        return self.marks(task_id, mark=swarm_watch.SCOPE_MARK)

    def scan_inprocess(self, *, dry_run: bool = False, now: str | None = None) -> dict:
        """`scan()` напрямую с портом на `store` — для тестов, мокающих
        `swarm_watch.snapshot`/`swarm_watch.probe` в этом же процессе."""
        tasks = store.list_tasks(self.conn, project="demo", include_closed=True,
                                 limit=1000)["tasks"]
        return swarm_watch.scan(self.project_path, tasks, _StorePort(self.conn),
                                dry_run=dry_run, now=now)

    # -------------------------------------------------------------- CLI

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def watch(self, *args: str) -> dict:
        proc = self.run_cli("watch", "--project", "demo", "--json", *args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def cli_module(self):
        """`bin/listik` — файл без `.py`, загружается явным загрузчиком (как в
        `tests/test_stage_same.py`)."""
        loader = SourceFileLoader("listik_cli_watch_b", str(LISTIK_BIN))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module


# ---------------------------------------------------------------- 1


class NoCommonFilesTests(WatchCliCase):
    def test_no_common_files_no_probes_no_discrepancies(self) -> None:
        t1 = self.make_task(write_scope=["a.txt"])
        t2 = self.make_task(write_scope=["b.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "a.txt", "a\n")
        self.edit(tree2, "b.txt", "b\n")

        out = self.watch()
        self.assertTrue(out["tasks"][t1]["live"])
        self.assertTrue(out["tasks"][t2]["live"])
        self.assertEqual(out["probes"], [])
        self.assertEqual(out["decisions"], [])
        self.assertEqual(out["discrepancies"], [])
        self.assertFalse(out["truncated"])


# ---------------------------------------------------------------- 2


class ProbeCleanTests(WatchCliCase):
    def test_opposite_ends_of_shared_file_merge_clean(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["f.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n")

        out = self.watch()
        self.assertEqual(len(out["probes"]), 1)
        p = out["probes"][0]
        self.assertEqual({p["a"], p["b"]}, {t1, t2})
        self.assertTrue(p["clean"])
        self.assertEqual(p["files"], ["f.txt"])


# ---------------------------------------------------------------- 3


class ProbeConflictTests(WatchCliCase):
    def test_same_function_conflicts_without_side_effects(self) -> None:
        t1 = self.make_task(write_scope=["pkg"])
        t2 = self.make_task(write_scope=["pkg"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        # Длина строки должна отличаться от базовой: однобайтовая замена ("+"→"-")
        # даёт файл того же размера, что и база, и на неотслеженной правке рискует
        # racy-clean (git доверяет кэшу stat при том же mtime/размере — см.
        # докстринг `_diff_via_index_copy`), из-за чего правка иногда не видна.
        self.edit(tree1, "pkg/mod.py", MOD_CONTENT.replace("return a + b", "return a - b  # t1"))
        self.edit(tree2, "pkg/mod.py", MOD_CONTENT.replace("return a + b", "return a * b  # t2"),
                 commit=False)

        before1, before2 = self.card(t1), self.card(t2)

        out = self.watch()
        self.assertEqual(len(out["probes"]), 1)
        p = out["probes"][0]
        self.assertFalse(p["clean"])
        self.assertEqual(p["conflicts"], ["pkg/mod.py"])
        self.assertIsInstance(out["decisions"], list)

        after1, after2 = self.card(t1), self.card(t2)
        self.assertEqual(after1["holder"], before1["holder"])
        self.assertEqual(after1["generation"], before1["generation"])
        self.assertEqual(after2["holder"], before2["holder"])
        self.assertEqual(after2["generation"], before2["generation"])


# ---------------------------------------------------------------- 4


class FirstChangeOrderTests(WatchCliCase):
    def test_first_change_recorded_once_and_orders_by_it(self) -> None:
        early, late = "watch-a-early", "watch-b-late"
        self.assertLess(early, late)
        self.make_task(task_id=early, write_scope=["a.txt"])
        self.make_task(task_id=late, write_scope=["b.txt"])
        tree_early = self.make_tree(early)
        tree_late = self.make_tree(late)

        self.edit(tree_late, "b.txt", "b\n")
        out1 = self.watch()
        self.assertEqual(len(self.marks(late)), 1)
        self.assertEqual(out1["tasks"][late]["first_change"], self.marks(late)[0]["created_at"])
        # created_at хранится с точностью до секунды — без разнесения по времени
        # обе отметки, взятые в одном прогоне теста, могли бы совпасть, и порядок
        # решался бы уже вторым ключом (id), а не первой правкой (см. §2 шаг 5).
        self.conn.execute("UPDATE comments SET created_at = ? WHERE id = ?",
                          ("2000-01-01T00:00:00Z", self.marks(late)[0]["id"]))
        self.conn.commit()

        self.edit(tree_early, "a.txt", "a\n")
        out2 = self.watch()
        self.assertEqual(out2["order"], [late, early])
        self.assertEqual(len(self.marks(late)), 1)
        self.assertEqual(len(self.marks(early)), 1)

        out3 = self.watch()
        self.assertEqual(out3["order"], [late, early])
        self.assertEqual(len(self.marks(late)), 1)
        self.assertEqual(len(self.marks(early)), 1)
        self.assertEqual(out3["tasks"][late]["first_change"], out2["tasks"][late]["first_change"])


# ---------------------------------------------------------------- 5


class FirstChangeTieTests(WatchCliCase):
    """Тик in-process (не через CLI-подпроцесс): `created_at` — секундная точность,
    и два реальных вызова `store.add_comment` подряд не гарантируют тик в тик
    один и тот же таймстамп — `store.now_iso` фиксируется, чтобы тай-брейк
    (`launched_at`, затем `id`) проверялся, а не таймингом теста."""

    def _setup_pair(self, low: str, high: str) -> tuple[str, str]:
        self.assertLess(low, high)
        self.make_task(task_id=low, write_scope=["a.txt"])
        self.make_task(task_id=high, write_scope=["b.txt"])
        tree_low = self.make_tree(low)
        tree_high = self.make_tree(high)
        self.edit(tree_low, "a.txt", "a\n")
        self.edit(tree_high, "b.txt", "b\n")
        return tree_low, tree_high

    def test_tie_breaks_by_earlier_launched_at(self) -> None:
        low, high = "watch-tie1-a", "watch-tie1-b"
        self._setup_pair(low, high)
        self.conn.execute("UPDATE tasks SET launched_at = ? WHERE id = ?",
                          ("2020-01-01T00:00:00Z", high))
        self.conn.execute("UPDATE tasks SET launched_at = ? WHERE id = ?",
                          ("2020-01-02T00:00:00Z", low))
        self.conn.commit()

        with mock.patch.object(store, "now_iso", return_value="2024-01-01T00:00:00Z"):
            out1 = self.scan_inprocess()
            out2 = self.scan_inprocess()
        self.assertEqual(out1["order"], [high, low])
        self.assertEqual(out2["order"], [high, low])

    def test_tie_with_equal_launched_at_breaks_by_id(self) -> None:
        low, high = "watch-tie2-a", "watch-tie2-b"
        self._setup_pair(low, high)
        self.conn.execute("UPDATE tasks SET launched_at = ? WHERE id IN (?, ?)",
                          ("2020-01-01T00:00:00Z", low, high))
        self.conn.commit()

        with mock.patch.object(store, "now_iso", return_value="2024-01-01T00:00:00Z"):
            out1 = self.scan_inprocess()
            out2 = self.scan_inprocess()
        self.assertEqual(out1["order"], [low, high])
        self.assertEqual(out2["order"], [low, high])


# ---------------------------------------------------------------- 6


class DiscrepancyTests(WatchCliCase):
    def test_discrepancy_written_once_then_extended(self) -> None:
        t = self.make_task(write_scope=["pkg"])
        tree = self.make_tree(t)
        self.edit(tree, "f.txt", "x\n", commit=False)

        out1 = self.watch()
        self.assertEqual(out1["discrepancies"],
                         [{"task": t, "files": ["f.txt"], "declared": ["pkg"], "new": ["f.txt"]}])
        marks = self.scope_marks(t)
        self.assertEqual(len(marks), 1)
        payload = json.loads(marks[0]["text"][len(swarm_watch.SCOPE_MARK):].strip())
        self.assertEqual(payload["files"], ["f.txt"])

        out2 = self.watch()
        self.assertEqual(len(self.scope_marks(t)), 1)
        self.assertEqual(out2["discrepancies"][0]["new"], [])

        self.edit(tree, "README.md", "y\n", commit=False)
        out3 = self.watch()
        self.assertEqual(len(self.scope_marks(t)), 2)
        self.assertEqual(out3["discrepancies"][0]["new"], ["README.md"])
        self.assertEqual(out3["discrepancies"][0]["files"], ["README.md", "f.txt"])


# ---------------------------------------------------------------- 7


class EmptyWriteScopeTests(WatchCliCase):
    def test_empty_write_scope_marks_everything_outside(self) -> None:
        t = self.make_task()
        tree = self.make_tree(t)
        self.edit(tree, "f.txt", "x\n", commit=False)

        out = self.watch()
        self.assertEqual(out["discrepancies"][0]["declared"], [])
        self.assertEqual(out["discrepancies"][0]["files"], ["f.txt"])
        marks = self.scope_marks(t)
        self.assertEqual(len(marks), 1)
        payload = json.loads(marks[0]["text"][len(swarm_watch.SCOPE_MARK):].strip())
        self.assertEqual(payload["declared"], [])


# ---------------------------------------------------------------- 8


class DryRunTests(WatchCliCase):
    def test_dry_run_writes_nothing(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["f.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n")
        before1, before2 = len(self.comments(t1)), len(self.comments(t2))

        out = self.watch("--dry-run")
        self.assertEqual(len(self.comments(t1)), before1)
        self.assertEqual(len(self.comments(t2)), before2)
        self.assertTrue(out["dry_run"])
        self.assertIsNone(out["tasks"][t1]["first_change"])
        self.assertIsNone(out["tasks"][t2]["first_change"])
        self.assertEqual(len(out["probes"]), 1)


# ---------------------------------------------------------------- 9


class SkipReasonsTests(WatchCliCase):
    def test_no_worktree(self) -> None:
        t = self.make_task()
        out = self.watch()
        self.assertEqual(out["skipped"][t], "no_worktree")

    def test_main_marker(self) -> None:
        for marker in ("main", "MASTER"):
            with self.subTest(marker=marker):
                t = self.make_task()
                store.update_task(self.conn, t, worktree=marker)
                out = self.watch()
                self.assertEqual(out["skipped"][t], "main_worktree")

    def test_main_tree(self) -> None:
        t = self.make_task()
        store.update_task(self.conn, t, worktree=self.project_path)
        (self.repo / "human.txt").write_text("h\n", encoding="utf-8")
        out = self.watch()
        self.assertEqual(out["skipped"][t], "main_tree")
        self.assertNotIn(t, out["tasks"])

    def test_missing_dir(self) -> None:
        t = self.make_task()
        tree = self.make_tree(t)
        shutil.rmtree(tree)
        out = self.watch()
        self.assertEqual(out["skipped"][t], "missing_dir")

    def test_unregistered(self) -> None:
        t = self.make_task()
        plain = self.tmp_path / "plain"
        plain.mkdir()
        store.update_task(self.conn, t, worktree=str(plain))
        out = self.watch()
        self.assertEqual(out["skipped"][t], "unregistered")

    def test_broken_tree(self) -> None:
        t = self.make_task()
        tree = self.make_tree(t)
        os.remove(os.path.join(tree, ".git"))
        (self.repo / "human2.txt").write_text("h\n", encoding="utf-8")
        out = self.watch()
        self.assertEqual(out["skipped"][t], "broken_tree")
        self.assertNotIn(t, out["tasks"])

    def test_closed_task_clean_at_head_is_not_live(self) -> None:
        t = self.make_task(status="done")
        self.make_tree(t)
        out = self.watch()
        self.assertIn(t, out["tasks"])
        self.assertFalse(out["tasks"][t]["live"])
        self.assertNotIn(t, out["order"])

    def test_closed_task_with_unmerged_commit_is_live(self) -> None:
        t = self.make_task(status="done", write_scope=["f.txt"])
        partner = self.make_task(write_scope=["f.txt"])
        tree = self.make_tree(t)
        tree_partner = self.make_tree(partner)
        self.edit(tree, "f.txt", "0\nbase\n")
        self.edit(tree_partner, "f.txt", "base\n9\n")
        out = self.watch()
        self.assertTrue(out["tasks"][t]["live"])
        self.assertIn(t, out["order"])
        self.assertEqual(len(out["probes"]), 1)


# ---------------------------------------------------------------- 10


class FrozenTests(WatchCliCase):
    def test_frozen_task_excluded_from_order_and_probes(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["pkg"], labels=["frozen-by:t1"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n")

        out = self.watch()
        self.assertEqual(out["tasks"][t2]["frozen_by"], "t1")
        self.assertNotIn(t2, out["order"])
        for p in out["probes"]:
            self.assertNotIn(t2, (p["a"], p["b"]))
        self.assertTrue(any(d["task"] == t2 for d in out["discrepancies"]))


# ---------------------------------------------------------------- 11


class TaskFilterTests(WatchCliCase):
    def test_task_filter_limits_scope(self) -> None:
        t1 = self.make_task()
        t2 = self.make_task()
        t3 = self.make_task()
        for t in (t1, t2, t3):
            tree = self.make_tree(t)
            self.edit(tree, "x.txt", f"{t}\n")

        out = self.watch("--task", t1, "--task", t2)
        self.assertEqual(set(out["tasks"]), {t1, t2})
        self.assertNotIn(t3, out["tasks"])
        self.assertNotIn(t3, out["skipped"])

    def test_unknown_task_id_is_not_an_error(self) -> None:
        t1 = self.make_task()
        self.make_tree(t1)
        out = self.watch("--task", t1, "--task", "nope-0000")
        self.assertIn(t1, out["tasks"])
        self.assertEqual(out["skipped"]["nope-0000"], "unknown")

    def test_task_id_from_other_project_is_unknown(self) -> None:
        other_repo = self.tmp_path / "other"
        other_repo.mkdir()
        self.git("init", "-q", "-b", "main", ".", cwd=other_repo)
        self.git("config", "user.name", "Тест", cwd=other_repo)
        self.git("config", "user.email", "test@example.com", cwd=other_repo)
        self.git("config", "commit.gpgsign", "false", cwd=other_repo)
        (other_repo / "a.txt").write_text("a\n", encoding="utf-8")
        self.git("add", ".", cwd=other_repo)
        self.git("commit", "-qm", "init", cwd=other_repo)
        store.add_project(self.conn, path=str(other_repo.resolve()), slug="other")
        foreign = store.create_task(self.conn, title="foreign", project="other")["id"]

        out = self.watch("--task", foreign)
        self.assertEqual(out["skipped"][foreign], "unknown")


# ---------------------------------------------------------------- 12


class CommandErrorTests(WatchCliCase):
    def test_unknown_project_is_bad_argument(self) -> None:
        proc = self.run_cli("watch", "--project", "nope", "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = json.loads(proc.stdout)["error"]
        self.assertEqual(err["code"], "bad_argument")

    def test_project_without_path_is_bad_argument(self) -> None:
        store.create_task(self.conn, title="ничья", project="ghost")
        proc = self.run_cli("watch", "--project", "ghost", "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = json.loads(proc.stdout)["error"]
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("listik projects --add", err["hint"])


# ---------------------------------------------------------------- 13


class ActorTests(WatchCliCase):
    def test_actor_flag_does_not_change_journal_author(self) -> None:
        t = self.make_task(write_scope=["a.txt"])
        tree = self.make_tree(t)
        self.edit(tree, "a.txt", "a\n")

        proc = self.run_cli("--actor", "me", "watch", "--project", "demo", "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        marks = self.marks(t)
        self.assertEqual(len(marks), 1)
        self.assertEqual(marks[0]["author"], "agent:listik-swarm")


# ---------------------------------------------------------------- 14


class LaunchAliveTests(WatchCliCase):
    def test_launch_alive_states(self) -> None:
        t1, t2, t3 = self.make_task(), self.make_task(), self.make_task()
        for t in (t1, t2, t3):
            self.make_tree(t)
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'listik', launch_pid = 12345, "
            "launch_finished_at = NULL WHERE id = ?", (t1,))
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'listik', launch_pid = 12345, "
            "launch_finished_at = ? WHERE id = ?", ("2020-01-01T00:00:00Z", t2))
        self.conn.commit()

        out = self.watch()
        self.assertTrue(out["tasks"][t1]["launch_alive"])
        self.assertFalse(out["tasks"][t2]["launch_alive"])
        self.assertFalse(out["tasks"][t3]["launch_alive"])


# ---------------------------------------------------------------- 15


class GitErrorTests(WatchCliCase):
    def test_git_error_in_one_tree_does_not_crash_scan(self) -> None:
        t1 = self.make_task(write_scope=["a.txt"])
        t2 = self.make_task(write_scope=["b.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "a.txt", "a\n")
        self.edit(tree2, "b.txt", "b\n")
        with open(os.path.join(tree2, ".git"), "w", encoding="utf-8") as fh:
            fh.write("gitdir: /nonexistent\n")

        out = self.watch()
        self.assertTrue(out["skipped"][t2].startswith("git_error:"))
        self.assertNotIn(t2, out["tasks"])
        self.assertNotIn(t2, out["order"])
        self.assertIn(t1, out["tasks"])


# ---------------------------------------------------------------- 16 (in-process: мок snapshot)


class SnapshotFailureTests(WatchCliCase):
    def test_snapshot_failure_removes_only_that_task(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["f.txt"])
        t3 = self.make_task(write_scope=["f.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        tree3 = self.make_tree(t3)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n1\n")
        self.edit(tree3, "f.txt", "base\n2\n")

        real_snapshot = swarm_watch.snapshot

        def fake_snapshot(tree, **kwargs):
            if tree == tree2:
                raise errors.ListikError("boom", code=errors.CONFLICT)
            return real_snapshot(tree, **kwargs)

        with mock.patch.object(swarm_watch, "snapshot", side_effect=fake_snapshot):
            out = self.scan_inprocess()

        self.assertTrue(out["skipped"][t2].startswith("git_error:"))
        self.assertNotIn(t2, out["tasks"])
        self.assertNotIn(t2, out["order"])
        self.assertFalse(any(d["task"] == t2 for d in out["discrepancies"]))
        pair_ids = {frozenset((p["a"], p["b"])) for p in out["probes"]}
        self.assertIn(frozenset((t1, t3)), pair_ids)
        self.assertFalse(any(t2 in (p["a"], p["b"]) for p in out["probes"]))


# ---------------------------------------------------------------- 17 (in-process: мок probe)


class ProbeFailureTests(WatchCliCase):
    def test_probe_failure_keeps_the_pair_as_an_error(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["f.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n")

        def fake_probe(repo, a, b):
            raise errors.ListikError("merge exploded", code=errors.CONFLICT)

        with mock.patch.object(swarm_watch, "probe", side_effect=fake_probe):
            out = self.scan_inprocess()

        self.assertEqual(len(out["probes"]), 1)
        p = out["probes"][0]
        self.assertEqual({p["a"], p["b"]}, {t1, t2})
        self.assertEqual(p["error"], "merge exploded")
        self.assertNotIn("clean", p)
        self.assertNotIn("conflicts", p)
        self.assertIn(t1, out["tasks"])
        self.assertIn(t2, out["tasks"])
        self.assertEqual(out["skipped"], {})


# ---------------------------------------------------------------- 18 (in-process: мок snapshot-счётчик)


class LazySnapshotTests(WatchCliCase):
    def test_snapshot_called_lazily_with_dirty_hint(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["f.txt"])
        t3 = self.make_task(write_scope=["a.txt"])
        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        tree3 = self.make_tree(t3)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n", commit=False)
        self.edit(tree3, "a.txt", "other\n")

        real_snapshot = swarm_watch.snapshot
        calls: list[tuple[str, bool | None]] = []

        def spy(tree, **kwargs):
            calls.append((tree, kwargs.get("dirty")))
            return real_snapshot(tree, **kwargs)

        with mock.patch.object(swarm_watch, "snapshot", side_effect=spy):
            out = self.scan_inprocess()

        self.assertEqual(len(calls), 2)
        self.assertEqual({c[0] for c in calls}, {tree1, tree2})
        for tree, dirty_hint in calls:
            tid = t1 if tree == tree1 else t2
            self.assertEqual(dirty_hint, out["tasks"][tid]["dirty"])


# ---------------------------------------------------------------- 19


class TextOutputTests(WatchCliCase):
    def test_text_output_format(self) -> None:
        t1 = self.make_task(write_scope=["f.txt"])
        t2 = self.make_task(write_scope=["f.txt"])
        t3 = self.make_task()  # без дерева — будет пропущена

        tree1 = self.make_tree(t1)
        tree2 = self.make_tree(t2)
        self.edit(tree1, "f.txt", "0\nbase\n")
        self.edit(tree2, "f.txt", "base\n9\n")
        with open(os.path.join(tree2, "new.txt"), "w", encoding="utf-8") as fh:
            fh.write("new\n")  # не отслеживается — null в numstat

        proc = self.run_cli("watch", "--project", "demo")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        out = proc.stdout
        self.assertIn(t2, out)
        self.assertIn("(+1 без счёта)", out)
        self.assertIn("чисто", out)
        self.assertIn(f"пропущено: {t3} (no_worktree)", out)
        self.assertIn(f"вне write_scope {t2}: new.txt", out)


# ---------------------------------------------------------------- 20 (в процессе: подмена store.list_tasks)


class TruncatedTests(WatchCliCase):
    def test_truncated_flag_and_stderr_warning(self) -> None:
        t = self.make_task()
        self.make_tree(t)
        real_list_tasks = store.list_tasks

        def fake_list_tasks(conn, **kwargs):
            res = real_list_tasks(conn, **kwargs)
            res["total"] = res["total"] + 5
            return res

        cli = self.cli_module()
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(paths, "DB_PATH", self.db_path), \
             mock.patch.object(store, "list_tasks", side_effect=fake_list_tasks), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["--local", "watch", "--project", "demo", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["truncated"])
        self.assertIn("задач больше 1000", err.getvalue())

    def test_not_truncated_by_default(self) -> None:
        t = self.make_task()
        self.make_tree(t)
        out = self.watch()
        self.assertFalse(out["truncated"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
