"""Тесты автостарта задач (шаг 09, порция b; чек-лист `step-09.check-b.md`).

Настоящие харнессы не запускаются: команда маршрута — `[sys.executable, ...]`, а сам
`routes.json` лежит во временном каталоге и кладётся в состояние через
`routes.init_at_startup(source, target)`. Настоящий `~/.config/listik/` тесты не трогают.
Логи автостарта уходят во временный каталог (`log_dir` для `launcher.start`, подмена
`paths.ROOT_DIR` для запусков через сервер), поэтому в дереве репозитория после прогона
ничего не остаётся.
"""
from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from listik import client
from listik import db as db_mod
from listik import launcher as launcher_mod
from listik import paths
from listik import routes as routes_mod
from listik import server
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"

# Девять колонок запуска: ровно они и ничего больше (плюс autostart — одна из них).
NINE = ("autostart", "launch_route", "launched_by", "launch_pid", "launched_at",
        "launch_log", "launch_exit_code", "launch_finished_at", "launch_error")

# Программа-маршрут: пишет argv, cwd и переменные окружения в файл. Лежит отдельным
# файлом, а не в `python -c`: фигурные скобки внутри команды routes.json запрещает.
WRITER_PY = """\
import json, os, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"argv": sys.argv[2:], "cwd": os.getcwd(),
               "task_id": os.environ.get("LISTIK_TASK_ID"),
               "route": os.environ.get("LISTIK_ROUTE"),
               "launched_by": os.environ.get("LISTIK_LAUNCHED_BY")}, fh, ensure_ascii=False)
"""


def pipeline_record(key="low-pipeline", command=None, visible=True) -> dict:
    record = {"key": key, "kind": "pipeline", "title": "Демо", "hint": "", "visible": visible,
              "roles": {"impl": {"provider": "claude", "label": "Opus", "title": "Opus"}}}
    if command is not None:
        record["command"] = command
    return record


def _load_cli():
    """Загрузить `bin/listik` как модуль (у файла нет расширения .py)."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_autostart", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class AutostartTestCase(TempDbTestCase):
    """Общая обвязка: временные маршруты, логи в tmp, чистое состояние модуля routes."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_state = routes_mod._state
        routes_mod._state = None
        self.addCleanup(lambda: setattr(routes_mod, "_state", self._saved_state))
        # Логи по умолчанию идут в paths.ROOT_DIR / "logs" — в тестах это tmp.
        self._root_patch = mock.patch.object(paths, "ROOT_DIR", self.tmp_path)
        self._root_patch.start()
        self.addCleanup(self._root_patch.stop)
        launcher_mod._trackers.clear()
        self.log_dir = self.tmp_path / "logs"
        self.notify: list[tuple[str, dict]] = []

    def tearDown(self) -> None:
        # Потоки слежения должны дописать всё до закрытия соединения и удаления tmp.
        for thread in list(launcher_mod._trackers.values()):
            thread.join(timeout=15)
        super().tearDown()

    # -- обвязка -----------------------------------------------------------

    def notify_cb(self, kind: str, payload: dict) -> None:
        self.notify.append((kind, payload))

    def set_routes(self, *records):
        source = self.tmp_path / "routes-source.json"
        source.write_text(json.dumps({"version": 1, "routes": list(records)},
                                     ensure_ascii=False), encoding="utf-8")
        target = self.tmp_path / "runtime" / "routes.json"
        return routes_mod.init_at_startup(source=source, target=target)

    def break_routes(self):
        target = self.tmp_path / "broken" / "routes.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{ битый json", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return routes_mod.init_at_startup(source=self.tmp_path / "нет-такого.json",
                                              target=target)

    def make_project(self, slug="proj", path=None) -> dict:
        return store.upsert_project(self.conn, slug, title=slug,
                                    path=str(path) if path is not None else None)

    def make_task(self, *, title="Задача", project=None, autostart=False, route=None,
                  worktree=None) -> dict:
        task = store.create_task(self.conn, title=title, project=project,
                                 autostart=autostart, route=route)
        if worktree is not None:
            store.update_task(self.conn, task["id"], worktree=str(worktree))
        return store.get_task(self.conn, task["id"])

    def writer_command(self, out_path) -> list[str]:
        script = self.tmp_path / "writer.py"
        script.write_text(WRITER_PY, encoding="utf-8")
        return [sys.executable, str(script), str(out_path),
                "{task_id}", "{title}", "{cwd}", "{project}"]

    def launch(self, task_id, *, notify=None, log_dir=None):
        return launcher_mod.start(self.conn, task_id, notify=notify,
                                  log_dir=self.log_dir if log_dir is None else log_dir)

    def join_tracker(self, task_id, timeout=15) -> None:
        thread = launcher_mod.tracker(task_id)
        self.assertIsNotNone(thread, "поток слежения не зарегистрирован")
        thread.join(timeout=timeout)
        self.assertFalse(thread.is_alive(), "поток слежения не завершился")

    def row(self, task_id):
        return self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def comments(self, task_id, kind=None) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM comments WHERE task_id = ? ORDER BY created_at, rowid",
            (task_id,)).fetchall()
        return [dict(r) for r in rows if kind is None or r["kind"] == kind]

    def seed(self, task_id, **fields) -> None:
        sets = ", ".join(f"{name} = ?" for name in fields)
        self.conn.execute(f"UPDATE tasks SET {sets} WHERE id = ?",
                          (*fields.values(), task_id))
        self.conn.commit()

    def dead_pid(self) -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def log_files(self) -> list[pathlib.Path]:
        return sorted(self.log_dir.glob("*.log")) if self.log_dir.exists() else []


class SchemaAndFieldsTests(AutostartTestCase):
    """Пункты 1–3 чек-листа: колонки, версия схемы, поля в карточке."""

    def test_old_database_gets_nine_columns_and_version_seven(self) -> None:
        # База версии 6: та же схема, но без девяти колонок запуска.
        kept = [line for line in db_mod.SCHEMA.splitlines()
                if not any(line.strip().startswith(name + " ") for name in NINE)]
        old_lines = []
        for i, line in enumerate(kept):
            tail = [item for item in kept[i + 1:]
                    if item.strip() and not item.strip().startswith("--")]
            if line.rstrip().endswith(",") and tail and tail[0].strip() == ");":
                line = line.rstrip()[:-1]  # висячая запятая от последней колонки
            old_lines.append(line)
        old = self.tmp_path / "old.db"
        raw = sqlite3.connect(old)
        raw.executescript("\n".join(old_lines))
        raw.execute("INSERT INTO meta(key, value) VALUES('schema_version', '6')")
        raw.commit()
        raw.close()

        conn = db_mod.init(old)
        try:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            for name in NINE:
                self.assertIn(name, columns, name)
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            self.assertEqual(version["value"], "7")
            self.assertEqual(db_mod.SCHEMA_VERSION, 7)
        finally:
            conn.close()

    def test_new_task_has_nine_fields_and_autostart_is_bool(self) -> None:
        task = self.make_task()
        for name in NINE:
            self.assertIn(name, task, name)
        self.assertIs(task["autostart"], False)
        self.assertIsNone(task["launch_route"])
        self.assertIsNone(task["launched_by"])
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launched_at"])
        self.assertIsNone(task["launch_log"])
        self.assertIsNone(task["launch_exit_code"])
        self.assertIsNone(task["launch_finished_at"])
        self.assertIsNone(task["launch_error"])

    def test_create_task_writes_autostart_and_route_only(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        self.assertIs(task["autostart"], True)
        self.assertEqual(task["launch_route"], "low-pipeline")
        self.assertIsNone(task["launch_error"])
        self.assertIsNone(task["launched_by"])
        self.assertIsNone(self.row(task["id"])["launch_pid"])

    def test_autostart_without_route_is_saved_as_is(self) -> None:
        task = self.make_task(route="some-route")
        self.assertIs(task["autostart"], False)
        self.assertEqual(task["launch_route"], "some-route")

    def test_get_via_api_returns_nine_fields(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            status, data = server.handle("GET", f"/api/tasks/{task['id']}", {}, {}, authed=True)
        self.assertEqual(status, 200)
        for name in NINE:
            self.assertIn(name, data, name)
        self.assertIsInstance(data["autostart"], bool)


class RefusalTests(AutostartTestCase):
    """Пункты 6–11, 18–20 чек-листа: отказы запуска и их след в задаче."""

    def test_broken_routes_refuses(self) -> None:
        self.break_routes()
        task = self.make_task(project=None, autostart=True, route="low-pipeline")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            reason = self.launch(task["id"], notify=self.notify_cb)
        self.assertTrue(reason.startswith("routes.json с ошибкой"), reason)
        self.assertIn(f"autostart {task['id']}:", err.getvalue())

        row = self.row(task["id"])
        self.assertEqual(row["launch_error"], reason)
        self.assertIsNone(row["launched_by"], "захват не снят")
        self.assertIsNone(row["launched_at"])
        self.assertIsNone(row["launch_pid"])
        self.assertEqual(row["needs_owner"], 1)
        self.assertIsNone(row["launch_log"])
        question = self.comments(task["id"], "question")
        self.assertEqual(len(question), 1)
        self.assertEqual(question[0]["text"],
                         f"автостарт не выполнен: {reason} — нужен ты")
        self.assertEqual(self.notify, [("task", {"id": task["id"], "action": "launch"})])

    def test_unknown_route_refuses_with_exact_question(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        task = self.make_task(project=None, autostart=True, route="xhigh-pipeline")
        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertEqual(reason, "маршрута xhigh-pipeline нет в routes.json")
        self.assertEqual(self.row(task["id"])["launch_error"], reason)
        question = self.comments(task["id"], "question")
        self.assertEqual([c["text"] for c in question],
                         ["автостарт не выполнен: маршрута xhigh-pipeline нет в routes.json — "
                          "нужен ты"])

    def test_route_without_command_refuses(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=None))
        task = self.make_task(project=None, autostart=True, route="low-pipeline")
        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertEqual(reason, "у маршрута low-pipeline нет command в routes.json")
        self.assertEqual(self.row(task["id"])["launch_error"], reason)
        self.assertEqual(self.row(task["id"])["needs_owner"], 1)

    def test_no_workdir_refuses(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        self.make_project("proj", path=None)
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")
        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertTrue(reason.startswith("нет рабочего каталога"), reason)
        self.assertIn("path проекта proj", reason)
        self.assertEqual(self.row(task["id"])["launch_error"], reason)

    def test_missing_project_dir_refuses(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        self.make_project("proj", path=self.tmp_path / "нет-такого-каталога")
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")
        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertTrue(reason.startswith("нет рабочего каталога"), reason)

    def test_missing_worktree_refuses(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir()
        self.make_project("proj", path=proj_dir)
        task = self.make_task(project="proj", autostart=True, route="low-pipeline",
                              worktree=self.tmp_path / "нет-дерева")
        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertTrue(reason.startswith("нет рабочего каталога"), reason)

    def test_oserror_refuses_and_releases_capture(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=["/nonexistent/bin/listik"]))
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir()
        self.make_project("proj", path=proj_dir)
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")
        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertTrue(reason.startswith("не удалось запустить"), reason)
        row = self.row(task["id"])
        self.assertEqual(row["launch_error"], reason)
        self.assertIsNone(row["launched_by"], "захват не снят после OSError")
        self.assertIsNone(row["launch_pid"])
        self.assertEqual(row["needs_owner"], 1)

    def test_invisible_route_still_launches(self) -> None:
        out = self.tmp_path / "out.json"
        self.set_routes(pipeline_record("low-pipeline", command=self.writer_command(out),
                                        visible=False))
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir()
        self.make_project("proj", path=proj_dir)
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        self.assertEqual(self.row(task["id"])["launched_by"], "listik")
        self.assertTrue(out.exists())

    def test_refusal_does_not_touch_existing_launch_state(self) -> None:
        """Отказ «уже запущена» не пишет launch_error, не поднимает needs_owner и молчит."""
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir()
        self.make_project("proj", path=proj_dir)
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        before = dict(self.row(task["id"]))
        comments_before = len(self.comments(task["id"]))

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            reason = self.launch(task["id"], notify=self.notify_cb)
        self.assertEqual(reason, launcher_mod.ALREADY_STARTED)
        self.assertIn(f"autostart {task['id']}: {launcher_mod.ALREADY_STARTED}", err.getvalue())
        after = dict(self.row(task["id"]))
        self.assertEqual(before, after)
        self.assertEqual(len(self.comments(task["id"])), comments_before)
        self.assertEqual(self.notify, [])
        self.assertEqual(len(self.log_files()), 1, "появился второй лог-файл")


class LaunchTests(AutostartTestCase):
    """Пункты 12–17, 20 чек-листа: успешный запуск, argv, env, код выхода, гонка."""

    def prepare(self, command, *, title="Задача", project="proj", worktree=None,
                route="low-pipeline"):
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir(exist_ok=True)
        self.make_project(project, path=proj_dir)
        self.set_routes(pipeline_record(route, command=command))
        return self.make_task(title=title, project=project, autostart=True, route=route,
                              worktree=worktree), proj_dir

    def test_success_writes_fields_journal_and_env(self) -> None:
        out = self.tmp_path / "out.json"
        task, proj_dir = self.prepare(self.writer_command(out))
        before = dict(self.row(task["id"]))

        self.assertIsNone(self.launch(task["id"], notify=self.notify_cb))
        self.join_tracker(task["id"])

        row = self.row(task["id"])
        self.assertEqual(row["launched_by"], "listik")
        self.assertIsInstance(row["launch_pid"], int)
        self.assertTrue(row["launched_at"])
        self.assertEqual(row["launch_error"], None)
        self.assertTrue(pathlib.Path(row["launch_log"]).exists(), row["launch_log"])
        self.assertEqual(row["launch_exit_code"], 0)
        self.assertTrue(row["launch_finished_at"])

        # Запуск не делает claim за агента: этап, держатель и статус не изменились.
        for name in ("stage", "holder", "status"):
            self.assertEqual(row[name], before[name], name)

        journal = [c for c in self.comments(task["id"], "journal")
                   if c["author"] == "agent:listik"]
        self.assertEqual(len(journal), 2)
        self.assertIn(f"автостарт: маршрут low-pipeline, pid {row['launch_pid']}, "
                      f"лог {row['launch_log']}", journal[0]["text"])
        self.assertIn(f"автостарт: процесс {row['launch_pid']} завершился с кодом 0",
                      journal[1]["text"])

        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["argv"][0], task["id"])
        self.assertEqual(data["argv"][1], "Задача")
        self.assertEqual(data["task_id"], task["id"])
        self.assertEqual(data["route"], "low-pipeline")
        self.assertEqual(data["launched_by"], "listik")
        self.assertEqual(pathlib.Path(data["cwd"]).resolve(), proj_dir.resolve())
        self.assertEqual(self.notify, [("task", {"id": task["id"], "action": "launch"}),
                                       ("task", {"id": task["id"], "action": "launch"})])

    def test_worktree_wins_over_project_path(self) -> None:
        out = self.tmp_path / "out.json"
        worktree = self.tmp_path / "дерево"
        worktree.mkdir()
        task, proj_dir = self.prepare(self.writer_command(out), worktree=worktree)
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(pathlib.Path(data["cwd"]).resolve(), worktree.resolve())
        self.assertEqual(data["argv"][2], str(worktree))
        self.assertNotEqual(pathlib.Path(data["cwd"]).resolve(), proj_dir.resolve())

    def test_exit_code_three_is_recorded(self) -> None:
        task, _ = self.prepare([sys.executable, "-c", "import sys; sys.exit(3)"])
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        row = self.row(task["id"])
        self.assertEqual(row["launch_exit_code"], 3)
        texts = [c["text"] for c in self.comments(task["id"], "journal")]
        self.assertIn(f"автостарт: процесс {row['launch_pid']} завершился с кодом 3", texts)

    def test_title_goes_literally_and_no_shell(self) -> None:
        out = self.tmp_path / "out.json"
        pwned = self.tmp_path / "pwned"
        title = f"x; touch {pwned} {{task_id}}"
        task, _ = self.prepare(self.writer_command(out), title=title)
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["argv"][1], title, "title не дошёл буквально")
        self.assertEqual(data["argv"][0], task["id"])
        self.assertFalse(pwned.exists(), "заголовок попал в shell")
        # Подстановка однопроходная: {task_id} внутри значения остался как есть
        self.assertIn("{task_id}", data["argv"][1])

    def test_race_starts_exactly_one_process(self) -> None:
        task, _ = self.prepare([sys.executable, "-c", "pass"])
        barrier = threading.Barrier(2)
        results: list[str | None] = []

        def worker() -> None:
            conn = db_mod.connect(self.db_path)
            try:
                barrier.wait(timeout=10)
                results.append(launcher_mod.start(conn, task["id"], log_dir=self.log_dir))
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(2)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertEqual(len(results), 2)
        self.assertEqual(results.count(None), 1, results)
        self.assertEqual(results.count(launcher_mod.ALREADY_STARTED), 1, results)
        self.assertIn(launcher_mod.ALREADY_STARTED, err.getvalue())
        self.assertEqual(len(self.log_files()), 1, "должен быть ровно один лог-файл")
        self.assertEqual(self.row(task["id"])["needs_owner"], 0)
        self.assertIsNone(self.row(task["id"])["launch_error"])
        self.join_tracker(task["id"])


class ServerPostTests(AutostartTestCase):
    """Пункты 4–6, 10, 14, 18–20, 23 чек-листа: поведение POST /api/tasks."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)
        self.proj_dir = self.tmp_path / "proj"
        self.proj_dir.mkdir()
        self.make_project("proj", path=self.proj_dir)

    def post(self, **body):
        return server.handle("POST", "/api/tasks", {}, body, authed=True)

    def test_autostart_without_route_is_400_and_creates_nothing(self) -> None:
        before = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        for route in (None, "", "   "):
            body = {"title": "t", "autostart": True}
            if route is not None:
                body["route"] = route
            with self.assertRaises(server.ApiError) as ctx:
                self.post(**body)
            self.assertEqual(ctx.exception.status, 400, route)
        after = self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        self.assertEqual(after, before, "задача всё-таки создана")

    def test_route_without_autostart_is_only_saved(self) -> None:
        status, task = self.post(title="t", route="low-pipeline")
        self.assertEqual(status, 201)
        self.assertIs(task["autostart"], False)
        self.assertEqual(task["launch_route"], "low-pipeline")
        self.assertIsNone(task["launched_by"])
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launch_error"])
        self.assertEqual(self.log_files(), [])

    def test_task_without_autostart_is_not_launched(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        status, task = self.post(title="t", project="proj", route="low-pipeline")
        self.assertEqual(status, 201)
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launched_by"])
        self.assertEqual(self.log_files(), [])

    def test_post_launches_and_does_not_wait_for_the_process(self) -> None:
        self.set_routes(pipeline_record("low-pipeline",
                                        command=[sys.executable, "-c",
                                                 "import time; time.sleep(2)"]))
        started = time.monotonic()
        status, task = self.post(title="t", project="proj", autostart=True,
                                 route="low-pipeline")
        elapsed = time.monotonic() - started
        self.assertEqual(status, 201)
        self.assertLess(elapsed, 1.0, "POST ждал завершения процесса")
        self.assertIsInstance(task["launch_pid"], int)
        self.assertIsNone(task["launch_exit_code"])

        status, again = server.handle("GET", f"/api/tasks/{task['id']}", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertIsNone(again["launch_exit_code"])
        self.assertEqual(again["launched_by"], "listik")
        self.join_tracker(task["id"])
        self.assertEqual(self.row(task["id"])["launch_exit_code"], 0)

    def test_broken_routes_post_is_201_with_needs_owner(self) -> None:
        self.break_routes()
        with contextlib.redirect_stderr(io.StringIO()):
            status, task = self.post(title="t", project="proj", autostart=True,
                                     route="low-pipeline")
        self.assertEqual(status, 201)
        self.assertIs(task["needs_owner"], True)
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launched_by"])
        self.assertTrue(task["launch_error"].startswith("routes.json с ошибкой"),
                        task["launch_error"])

    def test_oserror_post_is_201_not_500(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=["/nonexistent/bin/listik"]))
        with contextlib.redirect_stderr(io.StringIO()):
            status, task = self.post(title="t", project="proj", autostart=True,
                                     route="low-pipeline")
        self.assertEqual(status, 201)
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launched_by"])
        self.assertTrue(task["launch_error"].startswith("не удалось запустить"),
                        task["launch_error"])
        self.assertIs(task["needs_owner"], True)

    def test_command_from_request_is_ignored(self) -> None:
        evil = self.tmp_path / "evil"
        self.set_routes(pipeline_record("low-pipeline", command=None))
        with contextlib.redirect_stderr(io.StringIO()):
            status, task = self.post(title="t", project="proj", autostart=True,
                                     route="low-pipeline",
                                     command=["touch", str(evil)])
        self.assertEqual(status, 201)
        self.assertEqual(task["launch_error"],
                         "у маршрута low-pipeline нет command в routes.json")
        self.assertFalse(evil.exists())
        self.assertIsNone(task["launch_pid"])

    def test_post_passes_publish_as_notify(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        with mock.patch.object(server, "publish") as publish:
            status, task = self.post(title="t", project="proj", autostart=True,
                                     route="low-pipeline")
            self.join_tracker(task["id"])
        self.assertEqual(status, 201)
        launches = [c for c in publish.call_args_list
                    if c.args == ("task", {"id": task["id"], "action": "launch"})]
        self.assertTrue(launches, publish.call_args_list)
        self.assertIn(mock.call("task", {"id": task["id"], "action": "created"}),
                      publish.call_args_list)

    def test_patch_does_not_change_launch_fields(self) -> None:
        self.set_routes(pipeline_record("low-pipeline", command=[sys.executable, "-c", "pass"]))
        _, task = self.post(title="t", project="proj", autostart=True, route="low-pipeline")
        self.join_tracker(task["id"])
        before = {name: self.row(task["id"])[name] for name in NINE}

        with mock.patch.object(server, "publish"):
            status, updated = server.handle(
                "PATCH", f"/api/tasks/{task['id']}", {},
                {"autostart": False, "launch_route": "xhigh-pipeline", "launched_by": None,
                 "launch_pid": 1, "launch_error": "подмена", "launch_exit_code": 42,
                 "launch_log": "/tmp/подмена.log", "launch_finished_at": "2020-01-01T00:00:00Z",
                 "launched_at": "2020-01-01T00:00:00Z"}, authed=True)
        self.assertEqual(status, 200)
        for name in NINE:
            self.assertEqual(updated[name], before[name], name)


class LocalFallbackTests(AutostartTestCase):
    """Пункт 21 чек-листа: локальный фолбэк CLI без сервера."""

    def setUp(self) -> None:
        super().setUp()
        self._db_patch = mock.patch.object(paths, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        # local_call открывает соединение сам и не закрывает его; в тесте отдаём ему
        # уже открытое соединение с временной базой — иначе оно утечёт.
        self._init_patch = mock.patch.object(db_mod, "init", return_value=self.conn)
        self._init_patch.start()
        self.addCleanup(self._init_patch.stop)

    def test_local_create_with_autostart_refuses(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            task = client.local_call("create", title="t", project=None, autostart=True,
                                     route="low-pipeline")
        self.assertEqual(task["launch_error"], "сервер Listik не запущен")
        self.assertIs(task["needs_owner"], True)
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launched_by"])
        self.assertIn(f"autostart {task['id']}: сервер Listik не запущен", err.getvalue())
        self.assertEqual(self.log_files(), [])
        questions = self.comments(task["id"], "question")
        self.assertEqual([c["text"] for c in questions],
                         ["автостарт не выполнен: сервер Listik не запущен — нужен ты"])

    def test_local_create_without_autostart_does_not_refuse(self) -> None:
        task = client.local_call("create", title="t", project=None, route="low-pipeline")
        self.assertIsNone(task["launch_error"])
        self.assertIs(task["needs_owner"], False)
        self.assertEqual(task["launch_route"], "low-pipeline")


class RecoverTests(AutostartTestCase):
    """Пункт 22 чек-листа: recover после перезапуска сервера."""

    def make_launched(self, pid, **extra) -> dict:
        task = self.make_task(project=None, autostart=True, route="low-pipeline")
        fields = {"launched_by": "listik", "launch_pid": pid,
                  "launched_at": "2026-01-01T00:00:00Z", "launch_log": "/tmp/лог.log"}
        fields.update(extra)
        self.seed(task["id"], **fields)
        return task

    def test_dead_pid_marks_tracking_lost(self) -> None:
        task = self.make_launched(self.dead_pid())
        self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [task["id"]])
        row = self.row(task["id"])
        self.assertTrue(row["launch_finished_at"])
        self.assertIsNone(row["launch_exit_code"], "код выхода неизвестен — должен остаться NULL")
        self.assertEqual(row["launched_by"], "listik")
        texts = [c["text"] for c in self.comments(task["id"], "journal")]
        self.assertIn("автостарт: отслеживание потеряно при перезапуске сервера", texts)
        self.assertEqual(self.notify, [("task", {"id": task["id"], "action": "launch"})])

    def test_live_pid_is_untouched(self) -> None:
        task = self.make_launched(os.getpid())
        self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [])
        row = self.row(task["id"])
        self.assertIsNone(row["launch_finished_at"])
        self.assertEqual(self.comments(task["id"], "journal"), [])
        self.assertEqual(self.notify, [])

    def test_permission_error_means_alive(self) -> None:
        task = self.make_launched(1)
        with mock.patch.object(launcher_mod.os, "kill", side_effect=PermissionError):
            self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [])
        self.assertIsNone(self.row(task["id"])["launch_finished_at"])
        self.assertEqual(self.notify, [])

    def test_finished_and_foreign_tasks_are_skipped(self) -> None:
        finished = self.make_launched(self.dead_pid(),
                                      launch_finished_at="2026-01-02T00:00:00Z")
        foreign = self.make_task(project=None)
        self.seed(foreign["id"], launch_pid=self.dead_pid())
        self.assertEqual(launcher_mod.recover(self.conn), [])
        self.assertIsNone(self.row(finished["id"])["launch_exit_code"])
        self.assertEqual(self.row(finished["id"])["launch_finished_at"],
                         "2026-01-02T00:00:00Z")
        self.assertEqual(self.comments(finished["id"], "journal"), [])

    def test_serve_calls_recover_with_publish(self) -> None:
        cfg_path = self.tmp_path / "config.toml"
        cfg_path.write_text('[server]\nhost = "127.0.0.1"\nport = 0\n[auth]\ntoken = "t"\n',
                            encoding="utf-8")
        fake = mock.Mock()
        fake.serve_forever.side_effect = KeyboardInterrupt
        with mock.patch.object(paths, "CONFIG_PATH", cfg_path), \
             mock.patch.object(server, "get_conn", return_value=self.conn), \
             mock.patch.object(server.routes_mod, "init_at_startup"), \
             mock.patch.object(server.launcher_mod, "recover") as recover, \
             mock.patch.object(server, "make_server", return_value=fake), \
             mock.patch.object(server, "start_embed_worker"), \
             contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            server.serve("127.0.0.1", 0, quiet=True, no_embed=True)
        self.assertEqual(recover.call_count, 1)
        self.assertIs(recover.call_args.kwargs.get("notify"), server.publish)
        self.assertIs(recover.call_args.args[0], self.conn)


class CliTests(AutostartTestCase):
    """Пункт 24 чек-листа: `listik new --autostart --route` и строка в `show`."""

    def setUp(self) -> None:
        super().setUp()
        self._db_patch = mock.patch.object(paths, "DB_PATH", self.db_path)
        self._db_patch.start()
        self.addCleanup(self._db_patch.stop)
        self._init_patch = mock.patch.object(db_mod, "init", return_value=self.conn)
        self._init_patch.start()
        self.addCleanup(self._init_patch.stop)
        self.cli = _load_cli()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = self.cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_new_autostart_without_route_fails_and_creates_nothing(self) -> None:
        code, _, err = self.run_cli(["--local", "new", "t", "--autostart"])
        self.assertEqual(code, 2)
        self.assertIn("--route", err)
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_new_local_autostart_refuses_because_server_is_down(self) -> None:
        code, out, _ = self.run_cli(["--local", "new", "t", "--autostart", "--route",
                                     "low-pipeline"])
        self.assertEqual(code, 0)
        row = self.conn.execute("SELECT * FROM tasks ORDER BY rowid DESC LIMIT 1").fetchone()
        self.assertEqual(row["launch_error"], "сервер Listik не запущен")
        self.assertEqual(row["needs_owner"], 1)
        self.assertEqual(row["launch_route"], "low-pipeline")
        self.assertIn(row["id"], out)
        self.assertIsNone(row["launch_pid"])

    def test_show_prints_autostart_line(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        self.seed(task["id"], launched_by="listik", launch_pid=4242,
                  launched_at="2026-09-13T20:00:00Z")
        code, out, _ = self.run_cli(["--local", "show", task["id"]])
        self.assertEqual(code, 0)
        self.assertIn("автостарт: low-pipeline · запущена listik pid 4242 "
                      "2026-09-13T20:00:00Z · код идёт", out)

    def test_show_prints_finished_code(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        self.seed(task["id"], launched_by="listik", launch_pid=4242,
                  launched_at="2026-09-13T20:00:00Z", launch_exit_code=0,
                  launch_finished_at="2026-09-13T20:01:00Z")
        _, out, _ = self.run_cli(["--local", "show", task["id"]])
        self.assertIn("код 0", out)

    def test_show_prints_error_line(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        self.seed(task["id"], launch_error="маршрута low-pipeline нет в routes.json")
        _, out, _ = self.run_cli(["--local", "show", task["id"]])
        self.assertIn("автостарт: ОШИБКА маршрута low-pipeline нет в routes.json", out)

    def test_show_has_no_autostart_line_for_plain_task(self) -> None:
        task = self.make_task()
        _, out, _ = self.run_cli(["--local", "show", task["id"]])
        self.assertNotIn("автостарт", out)


class AlembicTests(unittest.TestCase):
    """Пункт 2 чек-листа: ревизия 0005 и девять ALTER в offline-SQL."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp_path = pathlib.Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_revision_links_to_document_content(self) -> None:
        text = (REPO_DIR / "alembic" / "versions" / "0005_task_launch.py").read_text(
            encoding="utf-8")
        self.assertIn('revision = "0005_task_launch"', text)
        self.assertIn('down_revision = "0004_document_content"', text)

    def test_offline_sql_adds_nine_task_columns(self) -> None:
        if shutil.which("alembic") is None:
            self.skipTest("alembic не установлен")
        env = {**os.environ, "LISTIK_DB": str(self.tmp_path / "sql.db")}
        done = subprocess.run(
            ["alembic", "upgrade", "0004_document_content:0005_task_launch", "--sql"],
            cwd=REPO_DIR, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(done.stdout.count("ALTER TABLE tasks ADD COLUMN"), 9, done.stdout)


class RepoHygieneTests(unittest.TestCase):
    """Пункт 25 чек-листа: `logs/` игнорируется и в git status логов нет."""

    def git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=REPO_DIR, capture_output=True, text=True)

    def test_logs_dir_is_ignored(self) -> None:
        done = self.git("check-ignore", "-q", "logs/")
        if done.returncode not in (0, 1):
            self.skipTest(f"git недоступен: {done.stderr}")
        self.assertEqual(done.returncode, 0, "logs/ не в .gitignore")

    def test_git_status_has_no_log_files(self) -> None:
        done = self.git("status", "--porcelain")
        if done.returncode != 0:
            self.skipTest(f"git недоступен: {done.stderr}")
        offenders = [line for line in done.stdout.splitlines() if "logs/" in line]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
