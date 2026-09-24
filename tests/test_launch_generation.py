"""Тесты поколений запуска (`listik-go61`, порция a): `generation`, `dispatch_id`,
пять переменных окружения воркера, ограждение потоков слежения по `dispatch_id`.

Обвязка — `tests.test_autostart.AutostartTestCase`/`pipeline_record`. Настоящие
харнессы не запускаются: команда маршрута — `[sys.executable, ...]` на отдельный
скрипт-писатель (фигурные скобки в команде маршрута запрещены).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys
import time
from unittest import mock

from listik import launcher as launcher_mod
from tests.test_autostart import AutostartTestCase, pipeline_record

_DISPATCH_RE = re.compile(r"^[0-9a-f]{32}$")

# Пишет argv, cwd и все пять переменных LISTIK_* в файл; писатель ждёт ~0.5с перед
# записью, чтобы тесты успели "отозвать" карточку из-под живого процесса.
WRITER_PY = """\
import json, os, sys, time
delay = float(sys.argv[-1]) if sys.argv[-1].replace('.', '', 1).isdigit() else 0.0
if delay:
    time.sleep(delay)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"argv": sys.argv[2:], "cwd": os.getcwd(),
               "task_id": os.environ.get("LISTIK_TASK_ID"),
               "route": os.environ.get("LISTIK_ROUTE"),
               "launched_by": os.environ.get("LISTIK_LAUNCHED_BY"),
               "generation": os.environ.get("LISTIK_GENERATION"),
               "dispatch_id": os.environ.get("LISTIK_DISPATCH_ID")}, fh, ensure_ascii=False)
"""


class GenerationTestCase(AutostartTestCase):
    """Общая обвязка порции: свой WRITER_PY (пять переменных LISTIK_*, задержка)."""

    def writer_command(self, out_path, delay=0.0):
        script = self.tmp_path / "writer_gen.py"
        script.write_text(WRITER_PY, encoding="utf-8")
        return [sys.executable, str(script), str(out_path), str(delay)]

    def sleepy_writer_command(self, out_path, sleep_s=0.5):
        script = self.tmp_path / "sleep_writer.py"
        script.write_text(
            "import sys, time\n"
            "time.sleep(float(sys.argv[1]))\n"
            "with open(sys.argv[2], 'w') as fh:\n"
            "    fh.write('done')\n",
            encoding="utf-8")
        return [sys.executable, str(script), str(sleep_s), str(out_path)]

    def prepare(self, command, *, project="proj", route="low-pipeline"):
        proj_dir = self.tmp_path / project
        proj_dir.mkdir(exist_ok=True)
        self.make_project(project, path=proj_dir)
        self.set_routes(pipeline_record(route, command=command))
        return self.make_task(project=project, autostart=True, route=route)

    def make_launched(self, pid, **extra) -> dict:
        """Как `RecoverTests.make_launched`, но своя (не наследуем `RecoverTests`)."""
        task = self.make_task(project=None, autostart=True, route="low-pipeline")
        fields = {"launched_by": "listik", "launch_pid": pid,
                  "launched_at": "2026-01-01T00:00:00Z", "launch_log": "/tmp/лог.log"}
        fields.update(extra)
        self.seed(task["id"], **fields)
        return task


class FirstLaunchTests(GenerationTestCase):
    """Пункты 1, 3, 4 требований: первое поколение, монотонность."""

    def test_first_launch_writes_generation_one_and_dispatch_id(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])

        row = self.row(task["id"])
        self.assertEqual(row["generation"], 1)
        self.assertIsNotNone(row["dispatch_id"])
        self.assertRegex(row["dispatch_id"], _DISPATCH_RE)

        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["generation"], "1")
        self.assertEqual(data["dispatch_id"], row["dispatch_id"])

        journal = [c for c in self.comments(task["id"], "journal")
                   if c["author"] == "agent:listik"][0]
        self.assertIn(f", поколение 1, запуск {row['dispatch_id']}", journal["text"])

    def test_refusal_after_capture_keeps_generation_and_clears_dispatch(self) -> None:
        # Маршрут без command — отказ после захвата.
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir()
        self.make_project("proj", path=proj_dir)
        self.set_routes(pipeline_record("low-pipeline", command=None))
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")

        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertEqual(reason, "у маршрута low-pipeline нет command в базе")
        row = self.row(task["id"])
        self.assertEqual(row["generation"], 1)
        self.assertIsNone(row["dispatch_id"])
        self.assertIsNone(row["launched_by"])
        self.assertEqual(row["launch_error"], reason)
        self.assertEqual(row["needs_owner"], 1)

        # Следующий успешный запуск: поколение 2, новый dispatch_id.
        out = self.tmp_path / "out2.json"
        self.set_routes(pipeline_record("low-pipeline", command=self.writer_command(out)))
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        row2 = self.row(task["id"])
        self.assertEqual(row2["generation"], 2)
        self.assertIsNotNone(row2["dispatch_id"])
        self.assertNotEqual(row2["dispatch_id"], row["dispatch_id"])

    def test_monotonic_generation_after_two_refusals_and_success(self) -> None:
        proj_dir = self.tmp_path / "proj"
        proj_dir.mkdir()
        self.make_project("proj", path=proj_dir)

        self.set_routes(pipeline_record("low-pipeline", command=None))
        task = self.make_task(project="proj", autostart=True, route="low-pipeline")
        with contextlib.redirect_stderr(io.StringIO()):
            self.launch(task["id"])
        with contextlib.redirect_stderr(io.StringIO()):
            self.launch(task["id"])
        self.assertEqual(self.row(task["id"])["generation"], 2)

        out = self.tmp_path / "out.json"
        self.set_routes(pipeline_record("low-pipeline", command=self.writer_command(out)))
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        self.assertEqual(self.row(task["id"])["generation"], 3)


class AlreadyStartedTests(GenerationTestCase):
    """Пункт 2 требований: повторный `start` не трогает generation/dispatch_id."""

    def test_already_started_keeps_generation_and_dispatch(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        before = dict(self.row(task["id"]))

        with contextlib.redirect_stderr(io.StringIO()):
            reason = self.launch(task["id"])
        self.assertEqual(reason, launcher_mod.ALREADY_STARTED)
        after = dict(self.row(task["id"]))
        self.assertEqual(before, after)
        self.assertEqual(len(self.log_files()), 1, "появился второй лог-файл")


class TrackerForeignDispatchTests(GenerationTestCase):
    """Пункт 5 требований: `_track` не трогает колонки чужого (отозванного) запуска."""

    def test_track_with_foreign_dispatch_id_leaves_columns_untouched(self) -> None:
        out = self.tmp_path / "out.txt"
        task = self.prepare(self.sleepy_writer_command(out, sleep_s=0.5))
        self.assertIsNone(self.launch(task["id"], notify=self.notify_cb))
        # start сам зовёт notify один раз; список должен содержать ровно эту запись.
        self.assertEqual(self.notify, [("task", {"id": task["id"], "action": "launch"})])
        self.notify.clear()

        # "Отзыв" в реальности произойдёт в порции c — здесь имитируем его подменой
        # dispatch_id, пока процесс ещё жив (писатель спит ~0.5с).
        self.seed(task["id"], dispatch_id="другой-dispatch")

        self.join_tracker(task["id"])
        row = self.row(task["id"])
        self.assertIsNone(row["launch_exit_code"])
        self.assertIsNone(row["launch_finished_at"])
        texts = [c["text"] for c in self.comments(task["id"], "journal")]
        self.assertTrue(any("после отзыва — карточка не менялась" in t for t in texts), texts)
        self.assertEqual(self.notify, [("task", {"id": task["id"], "action": "launch"})])


class TrackerProcsCleanupTests(GenerationTestCase):
    """`_track` убирает свою запись из `_procs` и не трогает чужую."""

    def test_own_entry_removed_after_exit(self) -> None:
        task = self.prepare(self.sleepy_writer_command(self.tmp_path / "o.txt", sleep_s=0.1))
        self.addCleanup(launcher_mod._procs.pop, task["id"], None)
        self.assertIsNone(self.launch(task["id"], notify=self.notify_cb))
        self.assertIn(task["id"], launcher_mod._procs)
        self.join_tracker(task["id"])
        self.assertNotIn(task["id"], launcher_mod._procs)

    def test_foreign_entry_kept(self) -> None:
        task = self.prepare(self.sleepy_writer_command(self.tmp_path / "o.txt", sleep_s=0.5))
        self.addCleanup(launcher_mod._procs.pop, task["id"], None)
        self.assertIsNone(self.launch(task["id"], notify=self.notify_cb))
        other = object()
        with launcher_mod._trackers_lock:
            launcher_mod._procs[task["id"]] = other
        self.join_tracker(task["id"])
        self.assertIs(launcher_mod._procs.get(task["id"]), other)


class RecoverGenerationTests(GenerationTestCase):
    """Пункт 6 требований: `recover`/`_poll` не переиздают поколение, ограждают по dispatch_id."""

    def test_recover_dead_pid_keeps_generation_and_dispatch(self) -> None:
        task = self.make_launched(self.dead_pid(), generation=3, dispatch_id="abc")
        self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [task["id"]])
        row = self.row(task["id"])
        self.assertEqual(row["generation"], 3)
        self.assertEqual(row["dispatch_id"], "abc")
        self.assertTrue(row["launch_finished_at"])

    def test_recover_live_pid_poller_keeps_generation_and_dispatch(self) -> None:
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        task = self.make_launched(proc.pid, generation=2, dispatch_id="live-dispatch")
        with mock.patch.object(launcher_mod, "POLL_INTERVAL", 0.05):
            self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [])
            thread = launcher_mod.tracker(task["id"])
            self.assertIsNotNone(thread)
            proc.wait()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        row = self.row(task["id"])
        self.assertEqual(row["generation"], 2)
        self.assertEqual(row["dispatch_id"], "live-dispatch")
        self.assertTrue(row["launch_finished_at"])

    def test_poll_with_mismatched_dispatch_id_does_not_write(self) -> None:
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        task = self.make_launched(proc.pid, generation=1, dispatch_id="orig-dispatch")
        with mock.patch.object(launcher_mod, "POLL_INTERVAL", 0.05):
            self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [])
            thread = launcher_mod.tracker(task["id"])
            self.assertIsNotNone(thread)
            # Задачу "перезапустили" под другим dispatch_id, пока опросчик ещё смотрит
            # на старый pid.
            self.seed(task["id"], dispatch_id="new-dispatch")
            proc.wait()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        row = self.row(task["id"])
        self.assertIsNone(row["launch_finished_at"])
        self.assertEqual(row["dispatch_id"], "new-dispatch")

    def test_pre_generation_launch_still_handled_by_poll(self) -> None:
        """Запуск до поколений: `launched_by='listik'`, `generation=0`, `dispatch_id IS NULL`."""
        import subprocess
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.3)"])
        task = self.make_launched(proc.pid)  # generation/dispatch_id не указаны — по умолчанию
        row0 = self.row(task["id"])
        self.assertEqual(row0["generation"], 0)
        self.assertIsNone(row0["dispatch_id"])

        with mock.patch.object(launcher_mod, "POLL_INTERVAL", 0.05):
            self.assertEqual(launcher_mod.recover(self.conn, notify=self.notify_cb), [])
            thread = launcher_mod.tracker(task["id"])
            self.assertIsNotNone(thread)
            proc.wait()
            thread.join(10)
        self.assertFalse(thread.is_alive())
        row = self.row(task["id"])
        self.assertTrue(row["launch_finished_at"])
        self.assertEqual(row["generation"], 0)
        texts = [c["text"] for c in self.comments(task["id"], "journal")]
        self.assertTrue(any("код выхода неизвестен" in t for t in texts), texts)


class ShowCommandGenerationTests(GenerationTestCase):
    """Пункт 8 требований: строка `show` с поколением/запуском."""

    def setUp(self) -> None:
        super().setUp()
        from tests.test_autostart import _load_cli
        from listik import db as db_mod
        from listik import paths
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

    def test_show_prints_generation_and_dispatch_for_launched_task(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        self.seed(task["id"], launched_by="listik", launch_pid=4242,
                  launched_at="2026-09-13T20:00:00Z", generation=1,
                  dispatch_id="aabbccddeeff00112233445566778899")
        code, out, _ = self.run_cli(["--local", "show", task["id"]])
        self.assertEqual(code, 0)
        self.assertIn("поколение 1, запуск aabbccddeeff00112233445566778899", out)

    def test_show_does_not_print_generation_for_unlaunched_task(self) -> None:
        task = self.make_task(autostart=True, route="low-pipeline")
        code, out, _ = self.run_cli(["--local", "show", task["id"]])
        self.assertEqual(code, 0)
        self.assertNotIn("поколение", out)
