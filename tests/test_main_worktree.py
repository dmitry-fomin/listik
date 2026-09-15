"""Маркер основной ветки в `worktree` (listik-laq8).

`worktree=main`/`master` — явное «работа идёт в основной ветке репозитория,
отдельного дерева нет» (`docs/API.md`, «Работа в основной ветке»). Проверяем пять
мест, где значение поля трактуется не как путь:

* `store.main_worktree`/`is_main_worktree`/`main_worktree_title` — разбор маркера;
* `store.update_task` — канонизация (`MAIN` → `main`, путь — только trim);
* `launcher._workdir` — каталог запуска при маркере = `path` проекта;
* `documents._worktree` — контекст судьи проверяет репозиторий проекта и отдаёт
  `mode: "main"`/`title`;
* `store.claim`/`deps.worktree_conflict` — ключ блокировки дерева канонический
  (`store.worktree_lock_key`): пустое дерево, маркеры и путь проекта — одно
  «основное дерево проекта», в тексте отказа оно названо словами («работа в main»).
"""
from __future__ import annotations

import pathlib
import subprocess
import unittest

from listik import deps as deps_mod
from listik import documents, launcher, store
from tests.helpers import TempDbTestCase


class MainWorktreeValueTest(unittest.TestCase):
    """Разбор значения поля: маркер — ровно main/master, регистр не важен."""

    def test_marker_is_recognized(self) -> None:
        for value in ("main", "MAIN", " master ", "Master"):
            with self.subTest(value=value):
                self.assertTrue(store.is_main_worktree(value))

    def test_paths_and_empty_are_not_markers(self) -> None:
        for value in ("", "   ", None, "/tmp/main", "main-wt", "/repo/master", "matrix"):
            with self.subTest(value=value):
                self.assertFalse(store.is_main_worktree(value))
                self.assertEqual(store.main_worktree(value), "")

    def test_canonical_marker_and_title(self) -> None:
        self.assertEqual(store.main_worktree(" MASTER "), "master")
        self.assertEqual(store.main_worktree_title("main"), "работа в main")
        self.assertEqual(store.main_worktree_title("Master"), "работа в master")
        self.assertEqual(store.main_worktree_title("/tmp/wt"), "")


class UpdateTaskNormalizationTest(TempDbTestCase):
    """`set worktree=…`: маркер хранится строчными, путь — как дали, без пробелов."""

    def make_task(self) -> str:
        return store.create_task(self.conn, title="Задача", project="demo")["id"]

    def test_marker_is_lowercased(self) -> None:
        task_id = self.make_task()
        task = store.update_task(self.conn, task_id, worktree="MAIN")
        self.assertEqual(task["worktree"], "main")
        self.assertEqual(store.get_task(self.conn, task_id)["worktree"], "main")

    def test_path_is_only_trimmed(self) -> None:
        task_id = self.make_task()
        task = store.update_task(self.conn, task_id, worktree="  /tmp/MyWorktree  ")
        self.assertEqual(task["worktree"], "/tmp/MyWorktree")

    def test_directory_named_main_is_still_a_path(self) -> None:
        task_id = self.make_task()
        task = store.update_task(self.conn, task_id, worktree="/tmp/main")
        self.assertEqual(task["worktree"], "/tmp/main")
        self.assertFalse(store.is_main_worktree(task["worktree"]))


class LauncherWorkdirTest(TempDbTestCase):
    """Автозапуск: маркер — не каталог, cwd берётся у проекта."""

    def setUp(self) -> None:
        super().setUp()
        self.project_dir = self.tmp_path / "proj"
        self.project_dir.mkdir()
        store.upsert_project(self.conn, "proj", title="proj", path=str(self.project_dir))

    def row(self, task_id: str):
        return self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def make_task(self, worktree: str) -> str:
        task_id = store.create_task(self.conn, title="Задача", project="proj")["id"]
        store.update_task(self.conn, task_id, worktree=worktree)
        return task_id

    def test_main_marker_uses_project_path(self) -> None:
        task_id = self.make_task("main")
        self.assertEqual(launcher._workdir(self.conn, self.row(task_id)), self.project_dir)

    def test_worktree_path_still_wins(self) -> None:
        worktree = self.tmp_path / "tree"
        worktree.mkdir()
        task_id = self.make_task(str(worktree))
        self.assertEqual(launcher._workdir(self.conn, self.row(task_id)), worktree)


class DocumentsWorktreeModeTest(TempDbTestCase):
    """Контекст судьи (`s4-judge`): блок `worktree` знает про маркер основной ветки."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp_path / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def make_task(self, worktree: str, *, project_path: bool = True) -> dict:
        if project_path:
            store.upsert_project(self.conn, "proj", title="proj", path=str(self.repo))
        task_id = store.create_task(self.conn, title="Задача", project="proj")["id"]
        store.update_task(self.conn, task_id, worktree=worktree, branch=worktree)
        return store.get_task(self.conn, task_id)

    def test_main_marker_checks_project_repo(self) -> None:
        card = self.make_task("main")
        wt = documents._worktree(card, self.conn)
        self.assertEqual(wt["mode"], "main")
        self.assertEqual(wt["value"], "main")
        self.assertEqual(wt["title"], "работа в main")
        self.assertTrue(wt["exists"])
        self.assertTrue(wt["git"])
        self.assertEqual(pathlib.Path(wt["path"]), self.repo.resolve())

    def test_master_marker_keeps_its_name_in_title(self) -> None:
        card = self.make_task("master")
        wt = documents._worktree(card, self.conn)
        self.assertEqual(wt["mode"], "main")
        self.assertEqual(wt["title"], "работа в master")

    def test_main_marker_without_project_path_is_unavailable(self) -> None:
        card = self.make_task("main", project_path=False)
        wt = documents._worktree(card, self.conn)
        self.assertEqual(wt["mode"], "main")
        self.assertFalse(wt["exists"])
        self.assertIn("работа в main", wt["reason"])
        self.assertIn("не указан путь", wt["reason"])

    def test_worktree_path_keeps_mode(self) -> None:
        card = self.make_task(str(self.repo))
        wt = documents._worktree(card, self.conn)
        self.assertEqual(wt["mode"], "worktree")
        self.assertTrue(wt["exists"])


class ClaimLockLabelTest(TempDbTestCase):
    """Маркер — по-прежнему ключ блокировки дерева, но в отказе назван словами."""

    def test_two_tasks_in_main_conflict_and_message_says_main(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        b = store.create_task(self.conn, title="B", project="demo", stage="s3-impl")["id"]
        store.update_task(self.conn, a, worktree="main")
        store.update_task(self.conn, b, worktree="MAIN")
        store.claim(self.conn, a, holder="dsh")
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, b, holder="codex")
        text = str(cm.exception)
        self.assertIn("работа в main", text)
        self.assertIn(a, text)

    def test_worktree_task_does_not_conflict_with_main(self) -> None:
        a = store.create_task(self.conn, title="A", project="demo", stage="s3-impl")["id"]
        b = store.create_task(self.conn, title="B", project="demo", stage="s3-impl")["id"]
        store.update_task(self.conn, a, worktree="main")
        store.update_task(self.conn, b, worktree="/tmp/wt-main")
        store.claim(self.conn, a, holder="dsh")
        out = store.claim(self.conn, b, holder="codex")
        self.assertEqual(out["holder"], "codex")


class WorktreeLockKeyTest(unittest.TestCase):
    """Ключ блокировки дерева: `store.worktree_lock_key` (правка после FAIL).

    Ключ — это дерево, в которое задача пишет, а не сырое значение поля:
    пустое `worktree`, маркеры `main`/`master` и явный путь, совпавший с
    каталогом проекта, дают один ключ «основное дерево проекта».
    """

    def test_empty_and_markers_share_the_project_key(self) -> None:
        main = store.worktree_lock_key("main", "/repo")
        self.assertEqual(main, store.worktree_lock_key("", "/repo"))
        self.assertEqual(main, store.worktree_lock_key("  MASTER ", "/repo"))
        self.assertEqual(main, store.worktree_lock_key("/repo", "/repo"))

    def test_paths_are_canonicalized(self) -> None:
        self.assertEqual(store.worktree_lock_key("/repo/", ""),
                         store.worktree_lock_key("/repo", ""))
        self.assertNotEqual(store.worktree_lock_key("/repo", ""),
                            store.worktree_lock_key("/repo/other", ""))

    def test_without_project_path_the_main_tree_is_one_sentinel(self) -> None:
        self.assertEqual(store.worktree_lock_key("main", ""), store.MAIN_TREE_LOCK_KEY)
        self.assertEqual(store.worktree_lock_key("", ""), store.MAIN_TREE_LOCK_KEY)
        self.assertNotEqual(store.worktree_lock_key("/repo", ""), store.MAIN_TREE_LOCK_KEY)

    def test_directory_named_main_is_still_a_distinct_path(self) -> None:
        self.assertNotEqual(store.worktree_lock_key("/tmp/main", ""),
                            store.worktree_lock_key("main", ""))


class ClaimLockCanonicalTreeTest(TempDbTestCase):
    """Одно дерево проекта — одна блокировка, чем бы дерево ни было задано.

    До правки `worktree=main` и `worktree=master` не конфликтовали (launcher
    берёт для обоих `path` проекта), и пустое дерево не конфликтовало с
    маркером — две пишущие задачи могли писать в один каталог.
    """

    def task(self, title: str, worktree: str = "", *, project: str = "demo",
             stage: str = "s3-impl") -> str:
        task_id = store.create_task(self.conn, title=title, project=project, stage=stage)["id"]
        if worktree:
            store.update_task(self.conn, task_id, worktree=worktree)
        return task_id

    def assert_busy(self, task_id: str, holder: str, blocked_by: str) -> str:
        with self.assertRaises(ValueError) as cm:
            store.claim(self.conn, task_id, holder=holder)
        text = str(cm.exception)
        self.assertIn("занято задачей", text)
        self.assertIn(blocked_by, text)
        return text

    def test_main_and_master_are_one_tree(self) -> None:
        a = self.task("A", "main")
        b = self.task("B", "master")
        store.claim(self.conn, a, holder="dsh")
        text = self.assert_busy(b, "codex", a)
        self.assertIn("работа в master", text)

    def test_marker_and_empty_worktree_are_one_tree(self) -> None:
        tree = self.tmp_path / "proj"
        tree.mkdir()
        store.upsert_project(self.conn, "proj", title="proj", path=str(tree))
        a = self.task("A", "main", project="proj")
        b = self.task("B", project="proj")  # без worktree: то же основное дерево
        store.claim(self.conn, a, holder="dsh")
        # Отказ называет дерево словом занявшей задачи, а не пустым полем заявителя.
        self.assertIn("работа в main", self.assert_busy(b, "codex", a))

    def test_empty_worktree_blocks_the_marker(self) -> None:
        tree = self.tmp_path / "proj"
        tree.mkdir()
        store.upsert_project(self.conn, "proj", title="proj", path=str(tree))
        a = self.task("A", project="proj")
        b = self.task("B", "MASTER", project="proj")
        store.claim(self.conn, a, holder="dsh")
        self.assert_busy(b, "codex", a)

    def test_two_tasks_without_worktree_still_conflict(self) -> None:
        a = self.task("A")
        b = self.task("B")
        store.claim(self.conn, a, holder="dsh")
        self.assert_busy(b, "codex", a)

    def test_project_path_spelled_differently_is_the_same_tree(self) -> None:
        tree = self.tmp_path / "proj"
        tree.mkdir()
        link = self.tmp_path / "proj-link"
        link.symlink_to(tree)
        store.upsert_project(self.conn, "proj", title="proj", path=str(tree))
        a = self.task("A", "main", project="proj")
        b = self.task("B", f"{tree}/", project="proj")      # хвостовой слэш
        c = self.task("C", str(link), project="proj")       # симлинк на тот же каталог
        store.claim(self.conn, a, holder="dsh")
        self.assert_busy(b, "codex", a)
        self.assert_busy(c, "codex", a)

    def test_other_worktrees_do_not_conflict(self) -> None:
        a = self.task("A", str(self.tmp_path / "tree-a"))
        b = self.task("B", str(self.tmp_path / "tree-b"))
        store.claim(self.conn, a, holder="dsh")
        self.assertEqual(store.claim(self.conn, b, holder="codex")["holder"], "codex")

    def test_spec_stage_takes_no_lock_on_main_tree(self) -> None:
        a = self.task("A", "main")
        s = self.task("S", stage="s1-spec")
        store.claim(self.conn, a, holder="dsh")
        self.assertEqual(store.claim(self.conn, s, holder="codex")["holder"], "codex")

    def test_same_holder_is_exempt_in_the_shared_tree(self) -> None:
        # sticky: исполнитель и судья одной сессии держат две задачи в одном дереве
        a = self.task("A", "main")
        b = self.task("B")
        store.claim(self.conn, a, holder="dsh")
        self.assertEqual(store.claim(self.conn, b, holder="dsh")["holder"], "dsh")

    def test_ready_reports_busy_main_tree_for_task_without_worktree(self) -> None:
        a = self.task("A", "main")
        b = self.task("B")
        store.claim(self.conn, a, holder="dsh")
        state = deps_mod.ready(self.conn, b)
        self.assertEqual(state["worktree_busy"]["id"], a)
        self.assertEqual(state["worktree_busy"]["worktree"], "main")
        self.assertTrue(any("дерево занято" in r for r in state["reasons"]))


if __name__ == "__main__":
    unittest.main()
