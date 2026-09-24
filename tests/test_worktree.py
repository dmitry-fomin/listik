"""`listik worktree <id>` — дерево задачи `<проект>/.worktrees/<id>` (listik-9ytm).

Проверяется поведение команды целиком через CLI (`--local`, временная база,
временный git-репозиторий) плюс чистые функции `listik.worktree` напрямую.
Главное, за чем следят тесты, — инварианты: незакоммиченная работа и коммиты
ветки задачи не теряются ни при каком вызове, а основное дерево проекта не
трогается.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from listik import migrate, store
from listik import worktree as worktree_mod
from tests.helpers import TempDbTestCase
from tests.test_claim import LISTIK_BIN


class WorktreeCase(TempDbTestCase):
    """Временный git-репозиторий «проекта» + карточка в нём + CLI в локальном режиме."""

    def setUp(self) -> None:
        super().setUp()
        self.repo = self.tmp_path / "repo"
        self.repo.mkdir()
        self.git("init", "-q", ".")
        self.git("config", "user.name", "Тест")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "a.txt").write_text("a\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "первый")
        # Путь проекта хранится так же, как его кладёт `listik projects --add`.
        self.project_path = str(self.repo.resolve())
        store.add_project(self.conn, path=self.project_path, slug="demo")
        self.task_id = store.create_task(self.conn, title="Проба", project="demo")["id"]

    # -------------------------------------------------------------- утилиты

    def git(self, *args: str, cwd=None, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args],
                              capture_output=True, text=True)
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def git_out(self, *args: str, cwd=None) -> str:
        return self.git(*args, cwd=cwd).stdout.strip()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def worktree(self, *args: str, task_id: str | None = None) -> dict:
        """Успешный вызов команды с `--json`; возвращает разобранный объект."""
        proc = self.run_cli("worktree", task_id or self.task_id, "--json", *args)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assert_clean_stderr(proc)
        return json.loads(proc.stdout)

    def failure(self, *args: str, task_id: str | None = None) -> dict:
        proc = self.run_cli("worktree", task_id or self.task_id, "--json", *args)
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assert_clean_stderr(proc)
        return json.loads(proc.stdout)["error"]

    def assert_clean_stderr(self, proc) -> None:
        self.assertNotIn("usage:", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)

    def card(self) -> dict:
        return store.get_task(self.conn, self.task_id)

    def events_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events WHERE task_id = ?",
                                 (self.task_id,)).fetchone()[0]

    def updated_at(self) -> str:
        return self.conn.execute("SELECT updated_at FROM tasks WHERE id = ?",
                                 (self.task_id,)).fetchone()[0]

    def wt_path(self, name: str | None = None) -> str:
        return os.path.join(self.project_path, ".worktrees", name or self.task_id)

    def main_state(self) -> tuple:
        status = [line for line in self.git_out("status", "--porcelain").splitlines()
                  if ".gitignore" not in line]
        return self.git_out("rev-parse", "HEAD"), tuple(status)

    def commit_in(self, path: str, text: str = "второй") -> str:
        with open(os.path.join(path, "b.txt"), "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        self.git("add", ".", cwd=path)
        self.git("commit", "-qm", text, cwd=path)
        return self.git_out("rev-parse", "HEAD", cwd=path)

    def add_elsewhere(self, name: str, branch: str) -> str:
        """Ещё одно дерево того же репозитория вне `.worktrees`."""
        path = str(self.tmp_path / name)
        self.git("worktree", "add", path, "-b", branch)
        return path


class CreateTests(WorktreeCase):
    """Пункты 1–8 чек-листа: первое заведение дерева."""

    def test_creates_worktree_on_task_branch(self) -> None:
        before = self.main_state()
        out = self.worktree()
        self.assertEqual(out["status"], "created")
        porcelain = self.git_out("worktree", "list", "--porcelain")
        self.assertIn(f"worktree {self.wt_path()}", porcelain)
        self.assertIn(f"branch refs/heads/task/{self.task_id}", porcelain)
        self.assertEqual(self.git_out("rev-parse", "HEAD", cwd=self.wt_path()),
                         self.git_out("rev-parse", "HEAD"))
        self.assertEqual(self.main_state(), before)

    def test_base_is_current_head_not_stale(self) -> None:
        second = self.commit_in(self.project_path, "второй в основном")
        out = self.worktree()
        self.assertEqual(out["base"]["sha"], second)

    def test_text_output_three_lines(self) -> None:
        proc = self.run_cli("worktree", self.task_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.strip().splitlines()
        sha7 = self.git_out("log", "-1", "--format=%h", cwd=self.wt_path())
        subject = self.git_out("log", "-1", "--format=%s", cwd=self.wt_path())
        self.assertEqual(lines, [
            f"дерево: {self.wt_path()} (создано)",
            f"ветка:  task/{self.task_id}",
            f"база:   {sha7} {subject}",
        ])

    def test_sha7_follows_core_abbrev(self) -> None:
        self.git("config", "core.abbrev", "12")
        out = self.worktree()
        sha7 = self.git_out("log", "-1", "--format=%h", cwd=self.wt_path())
        self.assertEqual(len(sha7), 12)
        self.assertEqual(out["base"]["sha7"], sha7)
        self.assertEqual(out["base"]["subject"],
                         self.git_out("log", "-1", "--format=%s", cwd=self.wt_path()))

    def test_json_keys(self) -> None:
        out = self.worktree()
        self.assertEqual(set(out), {"task_id", "name", "path", "branch", "base", "status",
                                    "dirty", "gitignore", "skill_link", "card_updated"})
        self.assertEqual(set(out["base"]), {"sha", "sha7", "subject"})
        self.assertEqual(out["task_id"], self.task_id)
        self.assertEqual(out["name"], self.task_id)
        self.assertEqual(out["path"], self.wt_path())
        self.assertEqual(out["branch"], f"task/{self.task_id}")
        self.assertTrue(out["card_updated"])
        self.assertFalse(out["dirty"])

    def test_card_gets_path_and_branch(self) -> None:
        self.worktree()
        card = self.card()
        self.assertEqual(card["worktree"], self.wt_path())
        self.assertEqual(card["branch"], f"task/{self.task_id}")

    def test_gitignore_added_updated_unchanged(self) -> None:
        self.assertEqual(self.worktree()["gitignore"], "added")
        self.assertIn(".worktrees/", (self.repo / ".gitignore").read_text(encoding="utf-8"))
        self.assertEqual(self.worktree()["gitignore"], "unchanged")

    def test_gitignore_updated_when_entry_missing(self) -> None:
        (self.repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
        out = self.worktree()
        self.assertEqual(out["gitignore"], "updated")
        text = (self.repo / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("*.log", text)
        self.assertIn(".worktrees/", text)

    def test_existing_branch_is_not_recreated(self) -> None:
        first = self.git_out("rev-parse", "HEAD")
        self.commit_in(self.project_path, "второй в основном")
        self.git("branch", f"task/{self.task_id}", first)
        out = self.worktree()
        self.assertEqual(out["status"], "created")
        self.assertEqual(out["base"]["sha"], first)
        self.assertEqual(self.git_out("rev-parse", "HEAD", cwd=self.wt_path()), first)
        branches = self.git_out("branch", "--list", "task/*").splitlines()
        self.assertEqual(len(branches), 1, branches)

    def test_actor_and_harness_flags_reach_update(self) -> None:
        # `updated_at` хранится с точностью до секунды, поэтому «до» отодвигаем явно:
        # иначе запись внутри той же секунды выглядит как отсутствие записи.
        self.conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?",
                          ("2020-01-01T00:00:00Z", self.task_id))
        self.conn.commit()
        before = self.updated_at()
        proc = self.run_cli("worktree", self.task_id, "--actor", "agent:codex",
                            "--harness", "codex")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        row = self.conn.execute("SELECT worktree, branch FROM tasks WHERE id = ?",
                                (self.task_id,)).fetchone()
        self.assertEqual(row[0], self.wt_path())
        self.assertEqual(row[1], f"task/{self.task_id}")
        self.assertNotEqual(self.updated_at(), before)


class CardFieldsTests(TempDbTestCase):
    """Правило «пишем в карточку только при отличии» — напрямую."""

    def test_same_values_give_empty_dict(self) -> None:
        self.assertEqual(worktree_mod.card_fields(
            {"worktree": "/tmp/wt", "branch": "task/x"}, "/tmp/wt", "task/x"), {})

    def test_missing_values_are_written(self) -> None:
        self.assertEqual(
            worktree_mod.card_fields({"worktree": "", "branch": None}, "/tmp/wt", "task/x"),
            {"worktree": "/tmp/wt", "branch": "task/x"})

    def test_only_different_field_is_written(self) -> None:
        self.assertEqual(
            worktree_mod.card_fields({"worktree": " /tmp/wt ", "branch": "old"},
                                     "/tmp/wt", "task/x"),
            {"branch": "task/x"})


class RepeatTests(WorktreeCase):
    """Пункты 9–18: повторный запуск, протухшие записи и чужие пути."""

    def test_second_call_is_reused_and_silent(self) -> None:
        self.worktree()
        updated_before, events_before = self.updated_at(), self.events_count()
        proc = self.run_cli("worktree", self.task_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("(переиспользовано)", proc.stdout)
        out = self.worktree()
        self.assertEqual(out["status"], "reused")
        self.assertFalse(out["card_updated"])
        self.assertEqual(self.updated_at(), updated_before)
        self.assertEqual(self.events_count(), events_before)
        paths = [line for line in self.git_out("worktree", "list", "--porcelain").splitlines()
                 if line == f"worktree {self.wt_path()}"]
        self.assertEqual(len(paths), 1)

    def test_reused_base_is_head_of_that_worktree(self) -> None:
        self.worktree()
        sha = self.commit_in(self.wt_path())
        out = self.worktree()
        self.assertEqual(out["status"], "reused")
        self.assertEqual(out["base"]["sha"], sha)
        self.assertNotEqual(out["base"]["sha"], self.git_out("rev-parse", "HEAD"))
        proc = self.run_cli("worktree", self.task_id)
        self.assertIn(self.git_out("log", "-1", "--format=%h", cwd=self.wt_path()), proc.stdout)
        self.assertIn("второй", proc.stdout)

    def test_dirty_worktree_is_kept(self) -> None:
        self.worktree()
        before = self.main_state()
        dirty_file = os.path.join(self.wt_path(), "черновик.txt")
        with open(dirty_file, "w", encoding="utf-8") as fh:
            fh.write("не коммичено\n")
        out = self.worktree()
        self.assertEqual(out["status"], "reused")
        self.assertTrue(out["dirty"])
        proc = self.run_cli("worktree", self.task_id)
        self.assertIn("в дереве незакоммиченные правки", proc.stdout)
        self.assertTrue(os.path.exists(dirty_file))
        self.assertEqual(self.main_state(), before)

    def test_stale_entry_is_recreated(self) -> None:
        self.worktree()
        shutil.rmtree(self.wt_path())
        out = self.worktree()
        self.assertEqual(out["status"], "recreated")
        self.assertTrue(os.path.isdir(self.wt_path()))
        porcelain = self.git_out("worktree", "list", "--porcelain")
        self.assertIn(f"branch refs/heads/task/{self.task_id}", porcelain)
        self.assertNotIn("prunable", porcelain)

    def test_stale_entry_keeps_branch_commits(self) -> None:
        self.worktree()
        sha = self.commit_in(self.wt_path())
        shutil.rmtree(self.wt_path())
        out = self.worktree()
        self.assertEqual(out["status"], "recreated")
        self.assertEqual(self.git_out("rev-parse", "HEAD", cwd=self.wt_path()), sha)
        self.assertEqual(out["base"]["sha"], sha)

    def test_busy_directory_is_a_conflict(self) -> None:
        os.makedirs(self.wt_path())
        keep = os.path.join(self.wt_path(), "чужое.txt")
        with open(keep, "w", encoding="utf-8") as fh:
            fh.write("чужое\n")
        err = self.failure()
        self.assertEqual(err["code"], "conflict")
        self.assertIn(self.wt_path(), err["message"])
        self.assertTrue(os.path.exists(keep))
        self.assertEqual(self.git_out("branch", "--list", f"task/{self.task_id}"), "")

    def test_card_path_of_registered_worktree_is_reused(self) -> None:
        elsewhere = self.add_elsewhere("elsewhere", f"task/{self.task_id}")
        store.update_task(self.conn, self.task_id, worktree=elsewhere,
                          branch=f"task/{self.task_id}")
        out = self.worktree()
        self.assertEqual(out["status"], "reused")
        self.assertEqual(out["path"], elsewhere)
        self.assertEqual(out["branch"], f"task/{self.task_id}")
        self.assertFalse(out["card_updated"])
        self.assertFalse(os.path.exists(self.wt_path()))

    def test_card_path_wins_over_busy_task_branch(self) -> None:
        elsewhere = self.add_elsewhere("elsewhere", f"other/{self.task_id}")
        third = self.add_elsewhere("third", f"task/{self.task_id}")
        store.update_task(self.conn, self.task_id, worktree=elsewhere)
        out = self.worktree()
        self.assertEqual(out["status"], "reused")
        self.assertEqual(out["path"], elsewhere)
        self.assertEqual(out["branch"], f"other/{self.task_id}")
        self.assertTrue(out["card_updated"])
        self.assertEqual(self.card()["branch"], f"other/{self.task_id}")
        self.assertFalse(os.path.exists(self.wt_path()))
        self.assertTrue(os.path.isdir(third))

    def test_unknown_card_path_falls_back_to_default(self) -> None:
        store.update_task(self.conn, self.task_id, worktree=str(self.tmp_path / "nowhere"))
        out = self.worktree()
        self.assertEqual(out["status"], "created")
        self.assertEqual(out["path"], self.wt_path())
        self.assertTrue(out["card_updated"])
        self.assertEqual(self.card()["worktree"], self.wt_path())

    def test_busy_branch_without_card_path_is_a_conflict(self) -> None:
        third = self.add_elsewhere("third", f"task/{self.task_id}")
        err = self.failure()
        self.assertEqual(err["code"], "conflict")
        self.assertIn(third, err["message"])
        self.assertFalse(os.path.exists(self.wt_path()))


class SkillLinkTests(WorktreeCase):
    """Симлинк скила в дереве задачи, закрытый через info/exclude (listik-5qzq)."""

    def exclude_lines(self) -> list[str]:
        exclude = self.git_out("rev-parse", "--git-path", "info/exclude", cwd=self.wt_path())
        if not os.path.isabs(exclude):
            exclude = os.path.join(self.wt_path(), exclude)
        with open(exclude, encoding="utf-8") as fh:
            return [line.strip() for line in fh if line.strip() == migrate.SKILL_REL]

    def assert_link_and_clean(self) -> None:
        link = os.path.join(self.wt_path(), migrate.SKILL_REL)
        self.assertTrue(os.path.islink(link))
        self.assertEqual(os.readlink(link), str(migrate.skill_source()))
        self.assertFalse(worktree_mod.is_dirty(self.wt_path()))
        self.assertEqual(self.git_out("status", "--porcelain", cwd=self.wt_path()), "")
        self.assertEqual(self.exclude_lines(), [migrate.SKILL_REL])

    def test_link_in_worktree_keeps_it_clean(self) -> None:
        out = self.worktree()
        self.assertEqual(out["skill_link"], "added")
        self.assertFalse(out["dirty"])
        self.assert_link_and_clean()

        again = self.worktree()
        self.assertEqual(again["status"], "reused")
        self.assertEqual(again["skill_link"], "unchanged")
        self.assert_link_and_clean()

        out = self.worktree("--recreate")
        self.assertEqual(out["status"], "recreated")
        self.assert_link_and_clean()

        # Проект через init-projects не проходил: ни симлинка в корне, ни строки в .gitignore.
        self.assertFalse(os.path.lexists(os.path.join(self.project_path, migrate.SKILL_REL)))
        with open(os.path.join(self.project_path, ".gitignore"), encoding="utf-8") as fh:
            self.assertNotIn(migrate.SKILL_REL, fh.read())


class RecreateTests(WorktreeCase):
    """Пункты 19–22: `--recreate` пересоздаёт только то, что нечего терять."""

    def test_clean_worktree_is_recreated_from_new_head(self) -> None:
        self.worktree()
        sha = self.commit_in(self.project_path, "новый в основном")
        out = self.worktree("--recreate")
        self.assertEqual(out["status"], "recreated")
        self.assertEqual(out["base"]["sha"], sha)
        entries = [line for line in self.git_out("worktree", "list", "--porcelain").splitlines()
                   if line == f"worktree {self.wt_path()}"]
        self.assertEqual(len(entries), 1)
        self.assertIn(f"branch refs/heads/task/{self.task_id}",
                      self.git_out("worktree", "list", "--porcelain"))

    def test_dirty_worktree_refuses_recreate(self) -> None:
        self.worktree()
        before = self.main_state()
        dirty_file = os.path.join(self.wt_path(), "черновик.txt")
        with open(dirty_file, "w", encoding="utf-8") as fh:
            fh.write("не коммичено\n")
        err = self.failure("--recreate")
        self.assertEqual(err["code"], "conflict")
        self.assertTrue(os.path.exists(dirty_file))
        self.assertIn(f"worktree {self.wt_path()}",
                      self.git_out("worktree", "list", "--porcelain"))
        self.assertEqual(self.main_state(), before)

    def test_branch_with_own_commits_refuses_recreate(self) -> None:
        self.worktree()
        sha = self.commit_in(self.wt_path())
        before = self.main_state()
        err = self.failure("--recreate")
        self.assertEqual(err["code"], "conflict")
        self.assertNotEqual(self.git_out("branch", "--list", f"task/{self.task_id}"), "")
        self.assertEqual(self.git_out("rev-parse", f"task/{self.task_id}"), sha)
        self.assertTrue(os.path.isdir(self.wt_path()))
        self.assertEqual(self.main_state(), before)

    def test_recreate_without_worktree_is_a_plain_create(self) -> None:
        out = self.worktree("--recreate")
        self.assertEqual(out["status"], "created")


class FailureTests(WorktreeCase):
    """Пункты 23–30: отказы и потоки вывода."""

    def test_main_marker_refuses(self) -> None:
        for marker in ("main", "master"):
            with self.subTest(marker=marker):
                store.update_task(self.conn, self.task_id, worktree=marker)
                err = self.failure()
                self.assertEqual(err["code"], "conflict")
                self.assertIn(marker, err["message"])
                self.assertIn(f"listik set {self.task_id} worktree= branch=", err["hint"])
                self.assertEqual(self.card()["worktree"], marker)
                self.assertFalse(os.path.exists(self.wt_path()))

    def test_hint_from_marker_refusal_works(self) -> None:
        store.update_task(self.conn, self.task_id, worktree="main", branch="task/x")
        proc = self.run_cli("set", self.task_id, "worktree=", "branch=")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        card = self.card()
        self.assertEqual(card["worktree"], "")
        self.assertEqual(card["branch"], "")

    def test_project_without_path(self) -> None:
        task_id = store.create_task(self.conn, title="Ничья", project="ghost")["id"]
        err = self.failure(task_id=task_id)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("ghost", err["message"])
        self.assertIn("listik projects --add", err["hint"])

    def test_project_dir_is_not_a_repo(self) -> None:
        plain = self.tmp_path / "plain"
        plain.mkdir()
        store.add_project(self.conn, path=str(plain), slug="plain")
        task_id = store.create_task(self.conn, title="Не репозиторий", project="plain")["id"]
        err = self.failure(task_id=task_id)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn(str(plain.resolve()), err["message"])

    def test_missing_project_dir(self) -> None:
        gone = self.tmp_path / "gone"
        gone.mkdir()
        store.add_project(self.conn, path=str(gone), slug="gone")
        task_id = store.create_task(self.conn, title="Нет каталога", project="gone")["id"]
        shutil.rmtree(gone)
        err = self.failure(task_id=task_id)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn(str(gone.resolve()), err["message"])

    def test_repo_without_commits(self) -> None:
        empty = self.tmp_path / "empty"
        empty.mkdir()
        subprocess.run(["git", "-C", str(empty), "init", "-q", "."], check=True)
        store.add_project(self.conn, path=str(empty), slug="empty")
        task_id = store.create_task(self.conn, title="Пустой", project="empty")["id"]
        err = self.failure(task_id=task_id)
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("нет коммитов", err["message"])
        self.assertFalse((empty / ".gitignore").exists())
        self.assertFalse((empty / ".worktrees").exists())

    def test_unknown_task(self) -> None:
        err = self.failure(task_id="listik-нет")
        self.assertEqual(err["code"], "not_found")

    def test_bad_track_values(self) -> None:
        for value in ("A B", ""):
            with self.subTest(value=value):
                err = self.failure("--track", value)
                self.assertEqual(err["code"], "bad_argument")
        self.assertFalse(os.path.exists(os.path.join(self.project_path, ".worktrees")))

    def test_git_failure_becomes_conflict(self) -> None:
        with open(os.path.join(self.project_path, ".worktrees"), "w", encoding="utf-8") as fh:
            fh.write("не каталог\n")
        before = self.main_state()
        err = self.failure()
        self.assertEqual(err["code"], "conflict")
        self.assertTrue(err["message"].strip())
        self.assertTrue("fatal" in err["message"].lower() or ".worktrees" in err["message"])
        self.assertEqual(self.main_state(), before)

    def test_error_streams(self) -> None:
        store.update_task(self.conn, self.task_id, worktree="main")
        proc = self.run_cli("worktree", self.task_id)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertTrue(proc.stderr.startswith("ошибка:"), proc.stderr)
        self.assert_clean_stderr(proc)
        for err in (self.failure(), self.failure(task_id="listik-нет")):
            self.assertIn("code", err)

    def test_busy_directory_error_streams(self) -> None:
        os.makedirs(self.wt_path())
        proc = self.run_cli("worktree", self.task_id)
        self.assertNotEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")
        self.assertTrue(proc.stderr.startswith("ошибка:"), proc.stderr)
        self.assert_clean_stderr(proc)


class TrackTests(WorktreeCase):
    """Пункты 31–33: `--track` заводит дерево трека и не трогает карточку."""

    def test_track_creates_named_worktree_without_touching_card(self) -> None:
        before_updated = self.updated_at()
        out = self.worktree("--track", "api")
        self.assertEqual(out["name"], f"{self.task_id}-api")
        self.assertEqual(out["path"], self.wt_path(f"{self.task_id}-api"))
        self.assertEqual(out["branch"], f"task/{self.task_id}-api")
        self.assertFalse(out["card_updated"])
        self.assertFalse(self.card()["worktree"])
        self.assertEqual(self.updated_at(), before_updated)

    def test_two_tracks_from_the_same_head(self) -> None:
        head = self.git_out("rev-parse", "HEAD")
        api = self.worktree("--track", "api")
        web = self.worktree("--track", "web")
        self.assertEqual(api["base"]["sha"], head)
        self.assertEqual(web["base"]["sha"], head)
        porcelain = self.git_out("worktree", "list", "--porcelain")
        self.assertIn(f"branch refs/heads/task/{self.task_id}-api", porcelain)
        self.assertIn(f"branch refs/heads/task/{self.task_id}-web", porcelain)

    def test_track_ignores_card_path(self) -> None:
        elsewhere = self.add_elsewhere("elsewhere", f"other/{self.task_id}")
        store.update_task(self.conn, self.task_id, worktree=elsewhere)
        out = self.worktree("--track", "api")
        self.assertEqual(out["path"], self.wt_path(f"{self.task_id}-api"))
        self.assertEqual(out["status"], "created")
        self.assertEqual(self.card()["worktree"], elsewhere)


class HelpTests(WorktreeCase):
    """Пункт 35: справка знает про команду и её флаги."""

    def test_root_help_lists_worktree(self) -> None:
        proc = self.run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("worktree", proc.stdout)

    def test_subcommand_help(self) -> None:
        proc = self.run_cli("worktree", "--help")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for fragment in ("--track", "--recreate", ".worktrees/", "task/<id>"):
            self.assertIn(fragment, proc.stdout)


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()
