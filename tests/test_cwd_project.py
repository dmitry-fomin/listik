"""Автоопределение проекта по рабочему каталогу (listik-v5se, порция a).

Три уровня: чистая функция `listik.cwd_project.detect` (юнит-тесты, без базы и
процессов), ленивость/гашение ошибок в `bin/listik` (в том же процессе, подмена
`client.list_projects`) и поведение CLI подпроцессом с временной базой.
"""
from __future__ import annotations

import argparse
import importlib.machinery
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from listik import cwd_project, store
from tests.helpers import TempDbTestCase
from tests.test_local_bypass_warning import LocalBypassWarningCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
REPO_DIR = LISTIK_BIN.parent.parent

DETECTED = "# проект определён по каталогу"


def _load_cli():
    """Загрузить `bin/listik` как модуль — у файла нет расширения `.py`."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_cwd_test", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


CLI = _load_cli()


def _projects(*slugs_and_paths, archived=()):
    out = [{"slug": slug, "path": path} for slug, path in slugs_and_paths]
    for slug in archived:
        out.append({"slug": slug, "path": f"/archived/{slug}", "archived": 1})
    return out


class DetectMatchTests(unittest.TestCase):
    """Правила сопоставления по `projects.path` (пп. 1–6 требований)."""

    def test_exact_match(self) -> None:
        projects = _projects(("alpha", "/home/u/Projects/alpha"))
        self.assertEqual(cwd_project.detect(projects, "/home/u/Projects/alpha"), "alpha")

    def test_nested_directory(self) -> None:
        projects = _projects(("alpha", "/home/u/Projects/alpha"))
        self.assertEqual(cwd_project.detect(projects, "/home/u/Projects/alpha/pkg/inner"), "alpha")

    def test_longest_path_wins_for_nested_projects(self) -> None:
        projects = _projects(("outer", "/home/u/Projects/outer"),
                             ("inner", "/home/u/Projects/outer/inner"))
        self.assertEqual(cwd_project.detect(projects, "/home/u/Projects/outer/inner/src"), "inner")

    def test_worktree_and_deeper(self) -> None:
        projects = _projects(("alpha", "/home/u/Projects/alpha"))
        root = "/home/u/Projects/alpha/.worktrees/listik-v5se"
        self.assertEqual(cwd_project.detect(projects, root), "alpha")
        self.assertEqual(cwd_project.detect(projects, root + "/listik"), "alpha")

    def test_empty_or_missing_path_is_skipped(self) -> None:
        projects = [{"slug": "empty", "path": ""}, {"slug": "none", "path": None},
                    {"slug": "alpha", "path": "/home/u/Projects/alpha"}]
        self.assertEqual(cwd_project.detect(projects, "/home/u/Projects/alpha"), "alpha")
        self.assertIsNone(cwd_project.detect([{"slug": "none", "path": None}],
                                             "/home/u/Projects/alpha"))

    def test_sibling_with_shared_string_prefix_is_not_a_match(self) -> None:
        projects = _projects(("bc", "/a/bc"))
        self.assertIsNone(cwd_project.detect(projects, "/a/bcd"))
        self.assertEqual(cwd_project.detect(projects, "/a/bc/deep"), "bc")

    def test_archived_project_matches_like_a_normal_one(self) -> None:
        projects = _projects(archived=("old",))
        self.assertEqual(cwd_project.detect(projects, "/archived/old/sub"), "old")

    def test_directory_outside_everything_is_none(self) -> None:
        projects = _projects(("alpha", "/home/u/Projects/alpha"))
        self.assertIsNone(cwd_project.detect(projects, "/var/tmp/nowhere"))


class DetectFallbackTests(unittest.TestCase):
    """Запасной вариант через `projects_root` (правило 7)."""

    def test_first_segment_used_when_slug_exists(self) -> None:
        projects = _projects(("alpha", None))
        self.assertEqual(cwd_project.detect(projects, "/home/u/Projects/alpha/src",
                                            projects_root="/home/u/Projects"), "alpha")

    def test_slug_matched_case_insensitively(self) -> None:
        projects = _projects(("Alpha", None))
        self.assertEqual(cwd_project.detect(projects, "/home/u/Projects/alpha",
                                            projects_root="/home/u/Projects"), "Alpha")

    def test_unknown_slug_is_none(self) -> None:
        projects = _projects(("alpha", None))
        self.assertIsNone(cwd_project.detect(projects, "/home/u/Projects/unknown",
                                             projects_root="/home/u/Projects"))

    def test_root_itself_is_none(self) -> None:
        projects = _projects(("alpha", None))
        self.assertIsNone(cwd_project.detect(projects, "/home/u/Projects",
                                             projects_root="/home/u/Projects"))


class DetectNormalizationTests(unittest.TestCase):
    """Нормализация путей (правило 1): `~`, относительный путь, симлинк."""

    def _home(self):
        tmp = tempfile.TemporaryDirectory()
        home = Path(tmp.name).resolve()
        (home / "Projects" / "Foo").mkdir(parents=True)
        return tmp, home

    def test_tilde_path_matches_absolute_cwd(self) -> None:
        tmp, home = self._home()
        try:
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                projects = _projects(("foo", "~/Projects/Foo"))
                cwd = str(home / "Projects" / "Foo")
                self.assertEqual(cwd_project.detect(projects, cwd), "foo")
        finally:
            tmp.cleanup()

    def test_relative_project_path_is_made_absolute(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        base = Path(tmp.name).resolve()
        (base / "sub").mkdir()
        old = os.getcwd()
        try:
            os.chdir(base)
            projects = _projects(("sub", "sub"))
            self.assertEqual(cwd_project.detect(projects, str(base / "sub")), "sub")
        finally:
            os.chdir(old)
            tmp.cleanup()

    def test_cwd_through_symlink_matches_real_path(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        base = Path(tmp.name).resolve()
        (base / "real" / "proj").mkdir(parents=True)
        link = base / "link"
        link.symlink_to(base / "real" / "proj")
        try:
            projects = _projects(("proj", str(base / "real" / "proj")))
            self.assertEqual(cwd_project.detect(projects, str(link)), "proj")
        finally:
            tmp.cleanup()

    def test_tilde_projects_root_with_absolute_cwd(self) -> None:
        tmp, home = self._home()
        try:
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                projects = _projects(("Foo", None))
                cwd = str(home / "Projects" / "Foo")
                self.assertEqual(cwd_project.detect(projects, cwd,
                                                    projects_root="~/Projects"), "Foo")
        finally:
            tmp.cleanup()


class ProjectDefaultLazinessTests(unittest.TestCase):
    """Список проектов не запрашивается, если проект дали флаг/env (п. 4)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = (Path(self.tmp.name) / "alpha").resolve()
        self.proj.mkdir()
        self.projects = [{"slug": "alpha", "path": str(self.proj)}]
        self.calls = 0

        def fake_list_projects(**kwargs):
            self.calls += 1
            return {"projects": self.projects, "root": str(self.proj)}

        self._patch = mock.patch.object(CLI.client, "list_projects", new=fake_list_projects)
        self._patch.start()
        self._old_cwd = os.getcwd()
        self._env = os.environ.pop("LISTIK_PROJECT", None)

    def tearDown(self) -> None:
        os.chdir(self._old_cwd)
        if self._env is not None:
            os.environ["LISTIK_PROJECT"] = self._env
        self._patch.stop()
        self.tmp.cleanup()

    @staticmethod
    def _args(project=None):
        return argparse.Namespace(project=project, json=True, local=True, host=None, port=None)

    def test_explicit_flag_needs_no_lookup(self) -> None:
        self.assertEqual(CLI.project_default(self._args("alpha")), "alpha")
        self.assertIsNone(CLI.project_default(self._args("all")))
        self.assertIsNone(CLI.project_default(self._args("ALL")))
        self.assertEqual(self.calls, 0)

    def test_env_needs_no_lookup(self) -> None:
        with mock.patch.dict(os.environ, {"LISTIK_PROJECT": "alpha"}):
            self.assertEqual(CLI.project_default(self._args(None)), "alpha")
        with mock.patch.dict(os.environ, {"LISTIK_PROJECT": "ALL"}):
            self.assertIsNone(CLI.project_default(self._args(None)))
        self.assertEqual(self.calls, 0)

    def test_cwd_detection_looks_up_at_most_once(self) -> None:
        os.chdir(self.proj)
        self.assertEqual(CLI.project_default(self._args(None)), "alpha")
        self.assertEqual(self.calls, 1)


class ProjectDefaultErrorTests(TempDbTestCase):
    """Ошибка получения списка гасится: команда работает без фильтра (п. 4)."""

    def test_list_projects_error_is_suppressed(self) -> None:
        def boom(**kwargs):
            raise RuntimeError("сервер отдал мусор")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(CLI.client, "list_projects", new=boom), \
                mock.patch.object(CLI.paths, "DB_PATH", self.db_path), \
                redirect_stdout(out), redirect_stderr(err):
            code = CLI.main(["ready", "--local", "--json"])
        self.assertEqual(code, 0)
        text = out.getvalue() + err.getvalue()
        self.assertNotIn("Traceback", text)
        self.assertNotIn(DETECTED, err.getvalue())
        json.loads(out.getvalue())


class CliProjectDetectTests(TempDbTestCase):
    """`ready` подпроцессом: фильтр по каталогу, env и флагу (пп. 2, 3, 5)."""

    def setUp(self) -> None:
        super().setUp()
        self.root = (self.tmp_path / "projects").resolve()
        self.alpha = self.root / "alpha"
        self.beta = self.root / "beta"
        self.outside = (self.tmp_path / "elsewhere").resolve()
        for path in (self.alpha, self.beta, self.outside):
            path.mkdir(parents=True)
        store.add_project(self.conn, path=str(self.alpha), slug="alpha")
        store.add_project(self.conn, path=str(self.beta), slug="beta")
        store.create_task(self.conn, title="задача alpha", project="alpha")
        store.create_task(self.conn, title="задача beta", project="beta")
        self.config = self.tmp_path / "config.toml"
        self.config.write_text(f'[import]\nprojects_root = "{self.root}"\n', encoding="utf-8")

    def run_cli(self, *args, cwd=None, env_extra=None):
        env = {**os.environ,
               "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(self.config),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        env.pop("LISTIK_PROJECT", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(cwd or REPO_DIR))

    @staticmethod
    def projects_in(proc):
        return sorted(t["project"] for t in json.loads(proc.stdout)["tasks"])

    def test_ready_from_project_dir_returns_only_it(self) -> None:
        proc = self.run_cli("ready", "--json", cwd=self.alpha)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.projects_in(proc), ["alpha"])

    def test_ready_from_another_project_dir(self) -> None:
        proc = self.run_cli("ready", "--json", cwd=self.beta)
        self.assertEqual(self.projects_in(proc), ["beta"])

    def test_ready_from_worktree_of_project(self) -> None:
        worktree = self.alpha / ".worktrees" / "listik-v5se"
        worktree.mkdir(parents=True)
        proc = self.run_cli("ready", "--json", cwd=worktree)
        self.assertEqual(self.projects_in(proc), ["alpha"])

    def test_explicit_flag_overrides_detection(self) -> None:
        proc = self.run_cli("ready", "--json", "-p", "beta", cwd=self.alpha)
        self.assertEqual(self.projects_in(proc), ["beta"])

    def test_all_with_flag_returns_everything(self) -> None:
        for args in (("--project", "all"), ("-p", "ALL")):
            with self.subTest(args=args):
                proc = self.run_cli("ready", "--json", *args, cwd=self.alpha)
                self.assertEqual(self.projects_in(proc), ["alpha", "beta"])

    def test_env_project_substitutes(self) -> None:
        proc = self.run_cli("ready", "--json", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": "beta"})
        self.assertEqual(self.projects_in(proc), ["beta"])
        self.assertNotIn(DETECTED, proc.stderr)

    def test_env_all_suspends_filter(self) -> None:
        proc = self.run_cli("ready", "--json", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": "ALL"})
        self.assertEqual(self.projects_in(proc), ["alpha", "beta"])

    def test_flag_beats_env(self) -> None:
        proc = self.run_cli("ready", "--json", "-p", "alpha", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": "beta"})
        self.assertEqual(self.projects_in(proc), ["alpha"])

    def test_empty_env_is_not_set(self) -> None:
        proc = self.run_cli("ready", "--json", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": ""})
        self.assertEqual(self.projects_in(proc), ["alpha"])

    def test_directory_outside_projects_keeps_all_tasks(self) -> None:
        proc = self.run_cli("ready", "--json", cwd=self.outside)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.projects_in(proc), ["alpha", "beta"])
        self.assertNotIn(DETECTED, proc.stderr)

    def test_detected_line_only_in_human_mode(self) -> None:
        human = self.run_cli("ready", cwd=self.alpha)
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn(f"{DETECTED}: alpha", human.stderr)

        machine = self.run_cli("ready", "--json", cwd=self.alpha)
        self.assertNotIn(DETECTED, machine.stderr)
        json.loads(machine.stdout)


class TaskTimelineProjectFilterTests(TempDbTestCase):
    """Юнит-тест фильтра по проекту у `store.task_timeline` (п. 1)."""

    def setUp(self) -> None:
        super().setUp()
        self.alpha_task = store.create_task(self.conn, title="alpha", project="alpha",
                                            created_at="2024-01-01T00:00:00")["id"]
        self.beta_task = store.create_task(self.conn, title="beta", project="beta",
                                           created_at="2024-01-01T00:00:00")["id"]
        store.event(self.conn, self.alpha_task, "note", note="alpha", ts="2024-01-01T00:00:01")
        store.event(self.conn, self.beta_task, "note", note="beta", ts="2024-01-01T00:00:02")

    def test_without_project_keeps_all_events(self) -> None:
        items = store.task_timeline(self.conn)
        self.assertEqual({i["task_id"] for i in items}, {self.alpha_task, self.beta_task})

    def test_project_keeps_only_its_events(self) -> None:
        items = store.task_timeline(self.conn, project="alpha")
        self.assertTrue(items)
        self.assertEqual({i["task_id"] for i in items}, {self.alpha_task})

    def test_event_without_task_is_hidden_by_filter(self) -> None:
        store.event(self.conn, "gone-task", "note", note="orphan", ts="2024-01-01T00:00:03")
        self.assertIn("gone-task", [i["task_id"] for i in store.task_timeline(self.conn)])
        filtered = store.task_timeline(self.conn, project="alpha")
        self.assertNotIn("gone-task", [i["task_id"] for i in filtered])

    def test_limit_applies_after_filter(self) -> None:
        store.event(self.conn, self.alpha_task, "note", note="alpha2", ts="2024-01-01T00:00:04")
        store.event(self.conn, self.beta_task, "note", note="beta2", ts="2024-01-01T00:00:05")
        items = store.task_timeline(self.conn, limit=1, project="alpha")
        self.assertEqual([i["task_id"] for i in items], [self.alpha_task])
        self.assertEqual(items[0]["note"], "alpha2")


class CliInboxTimelineProjectTests(TempDbTestCase):
    """`inbox`/`timeline` подпроцессом: фильтр по проекту и автоопределение (пп. 2, 3, 5)."""

    def setUp(self) -> None:
        super().setUp()
        self.root = (self.tmp_path / "projects").resolve()
        self.alpha = self.root / "alpha"
        self.beta = self.root / "beta"
        self.outside = (self.tmp_path / "elsewhere").resolve()
        for path in (self.alpha, self.beta, self.outside):
            path.mkdir(parents=True)
        store.add_project(self.conn, path=str(self.alpha), slug="alpha")
        store.add_project(self.conn, path=str(self.beta), slug="beta")
        # created_at фиксирован: возраст в JSON не зависит от момента прогона.
        self.alpha_q = store.create_task(
            self.conn, title="вопрос alpha", project="alpha", needs_owner=True,
            created_at="2024-01-01T00:00:01")["id"]
        self.beta_q = store.create_task(
            self.conn, title="вопрос beta", project="beta", needs_owner=True,
            created_at="2024-01-01T00:00:02")["id"]
        self.alpha_d = store.create_task(
            self.conn, title="брошена alpha", project="alpha", status="in_progress",
            created_at="2024-01-01T00:00:03")["id"]
        self.beta_d = store.create_task(
            self.conn, title="брошена beta", project="beta", status="in_progress",
            created_at="2024-01-01T00:00:04")["id"]
        store.event(self.conn, self.alpha_q, "note", note="alpha-event",
                    ts="2024-01-01T00:00:10")
        store.event(self.conn, self.beta_q, "note", note="beta-event",
                    ts="2024-01-01T00:00:11")
        self.conn.commit()  # `store.event` не коммитит: отпускаем блокировку базы
        self.config = self.tmp_path / "config.toml"
        self.config.write_text(f'[import]\nprojects_root = "{self.root}"\n', encoding="utf-8")

    def run_cli(self, *args, cwd=None, env_extra=None):
        env = {**os.environ,
               "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(self.config),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        env.pop("LISTIK_PROJECT", None)
        env.update(env_extra or {})
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(cwd or REPO_DIR))

    @staticmethod
    def event_projects(proc):
        return {i["project"] for i in json.loads(proc.stdout)["items"]}

    @staticmethod
    def inbox_tasks(proc):
        data = json.loads(proc.stdout)
        return (sorted(t["id"] for t in data["questions"]),
                sorted(t["id"] for t in data["dropped"]))

    def test_timeline_from_project_dir_only_its_events(self) -> None:
        proc = self.run_cli("timeline", "--json", cwd=self.alpha)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.event_projects(proc), {"alpha"})

    def test_timeline_all_shows_every_project(self) -> None:
        proc = self.run_cli("timeline", "--json", "--project", "all", cwd=self.alpha)
        self.assertEqual(self.event_projects(proc), {"alpha", "beta"})
        self.assertNotIn(DETECTED, proc.stderr)

    def test_timeline_flag_overrides_cwd(self) -> None:
        proc = self.run_cli("timeline", "--json", "--project", "beta", cwd=self.alpha)
        self.assertEqual(self.event_projects(proc), {"beta"})

    def test_timeline_env_project_overrides_cwd(self) -> None:
        proc = self.run_cli("timeline", "--json", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": "beta"})
        self.assertEqual(self.event_projects(proc), {"beta"})
        self.assertNotIn(DETECTED, proc.stderr)

    def test_timeline_flag_beats_env(self) -> None:
        proc = self.run_cli("timeline", "--json", "--project", "alpha", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": "beta"})
        self.assertEqual(self.event_projects(proc), {"alpha"})

    def test_detected_line_only_in_human_mode(self) -> None:
        human = self.run_cli("timeline", cwd=self.alpha)
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertIn(f"{DETECTED}: alpha", human.stderr)

        machine = self.run_cli("timeline", "--json", cwd=self.alpha)
        self.assertNotIn(DETECTED, machine.stderr)
        json.loads(machine.stdout)

    def test_inbox_from_project_dir_only_its_tasks(self) -> None:
        proc = self.run_cli("inbox", "--json", cwd=self.alpha)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.inbox_tasks(proc), ([self.alpha_q], [self.alpha_d]))

    def test_inbox_env_project_overrides_cwd(self) -> None:
        proc = self.run_cli("inbox", "--json", cwd=self.alpha,
                            env_extra={"LISTIK_PROJECT": "beta"})
        self.assertEqual(self.inbox_tasks(proc), ([self.beta_q], [self.beta_d]))

    def test_unfiltered_output_matches_all(self) -> None:
        # Эталон «как до правки»: CLI без фильтра (каталог вне проектов) и раньше
        # звал store без project. `--project all` обязан дать тот же JSON, сохранив
        # и порядок элементов.
        base = self.run_cli("timeline", "--json", cwd=self.outside)
        allp = self.run_cli("timeline", "--json", "--project", "all", cwd=self.alpha)
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertEqual(allp.returncode, 0, allp.stderr)
        self.assertEqual(json.loads(base.stdout), json.loads(allp.stdout))

        base_in = self.run_cli("inbox", "--json", cwd=self.outside)
        all_in = self.run_cli("inbox", "--json", "--project", "all", cwd=self.alpha)
        self.assertEqual(base_in.returncode, 0, base_in.stderr)
        self.assertEqual(all_in.returncode, 0, all_in.stderr)
        self.assertEqual(json.loads(base_in.stdout), json.loads(all_in.stdout))


class ServerLocalParityTests(LocalBypassWarningCase):
    """HTTP и `--local` для `timeline`/`inbox` дают один состав (п. 5)."""

    def setUp(self) -> None:
        super().setUp()
        self.root = (self.tmp_path / "projects").resolve()
        self.alpha = self.root / "alpha"
        self.beta = self.root / "beta"
        for path in (self.alpha, self.beta):
            path.mkdir(parents=True)
        store.add_project(self.conn, path=str(self.alpha), slug="alpha")
        store.add_project(self.conn, path=str(self.beta), slug="beta")
        self.alpha_t = store.create_task(self.conn, title="alpha", project="alpha",
                                         needs_owner=True,
                                         created_at="2024-01-01T00:00:01")["id"]
        self.beta_t = store.create_task(self.conn, title="beta", project="beta",
                                        created_at="2024-01-01T00:00:02")["id"]
        store.event(self.conn, self.alpha_t, "note", note="a", ts="2024-01-01T00:00:10")
        store.event(self.conn, self.beta_t, "note", note="b", ts="2024-01-01T00:00:11")
        self.conn.commit()  # `store.event` не коммитит: иначе база заперта для CLI
        self.config_path.write_text(
            f'[auth]\ntoken = "test-token"\n[import]\nprojects_root = "{self.root}"\n',
            encoding="utf-8")

    def run_cli_at(self, *args, cwd, local):
        cmd = [sys.executable, str(LISTIK_BIN)]
        if local:
            cmd.append("--local")
        cmd += ["--port", str(self.port), *args]
        env = {**os.environ, "LISTIK_CONFIG": str(self.config_path),
               "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(cwd))

    def test_timeline_paths_agree(self) -> None:
        local = self.run_cli_at("timeline", "--json", cwd=self.alpha, local=True)
        remote = self.run_cli_at("timeline", "--json", cwd=self.alpha, local=False)
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertEqual(remote.returncode, 0, remote.stderr)
        self.assertEqual([i["task_id"] for i in json.loads(remote.stdout)["items"]],
                         [i["task_id"] for i in json.loads(local.stdout)["items"]])

    def test_inbox_paths_agree(self) -> None:
        local = self.run_cli_at("inbox", "--json", cwd=self.alpha, local=True)
        remote = self.run_cli_at("inbox", "--json", cwd=self.alpha, local=False)
        self.assertEqual(local.returncode, 0, local.stderr)
        self.assertEqual(remote.returncode, 0, remote.stderr)

        def ids(proc):
            data = json.loads(proc.stdout)
            return ([t["id"] for t in data["questions"]],
                    [t["id"] for t in data["dropped"]])

        self.assertEqual(ids(remote), ids(local))


if __name__ == "__main__":
    unittest.main()
