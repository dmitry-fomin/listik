"""Сквозная приёмка роя (`bin/listik-swarm`) на живом сервере Listik, настоящем
`bin/listik`, настоящем git и поддельном воркере, который ведёт карточку по
протоколу (`claim` -> коммит -> `done`) (listik-9hcc, порция d).

Юнит-тесты роя (b/c) гоняют `decide`/`tick` на подставном `listik`; здесь --
процесс роя (`node bin/listik-swarm`) реально общается с реальным сервером
Listik субпроцессами `bin/listik --json`, реально заводит git worktree и реально
запускает/снимает процессы. Обвязка — `FencingHttpCase` (временная база + живой
сервер) и `AutostartTestCase` (подмена `paths.ROOT_DIR`/`paths.LOGS_DIR`, маршруты
в базе, ожидание потоков слежения) через множественное наследование: обе ведут к
общему `TempDbTestCase`, кооперативный `super().setUp()`/`tearDown()` вызывает
обе цепочки по разу, ничего в исходных классах не меняется.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from listik import launcher as launcher_mod
from listik import paths
from listik import store
from tests.test_autostart import AutostartTestCase
from tests.test_fencing import FencingHttpCase

REPO_DIR = Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"
SWARM_BIN = REPO_DIR / "bin" / "listik-swarm"


def _missing_tool() -> str | None:
    for tool in ("node", "git"):
        if shutil.which(tool) is None:
            return tool
    return None


_MISSING_TOOL = _missing_tool()

# Воркер (шаблон, LISTIK-путь подставляется при записи в tmp): читает окружение
# запуска (LISTIK_TASK_ID/LISTIK_GENERATION/LISTIK_DEV_PORT), claim -> (зависание
# | падение | сон FAKE_WORKER_SLEEP) -> файл <id>.txt с портом -> git commit -> done.
# Все вызовы listik с --json; любой ненулевой код -- выход 1. Про рой и сервер
# воркер не знает ничего, кроме окружения и config.toml (LISTIK_HOME).
_WORKER_SRC = r'''
import json
import os
import subprocess
import sys
import time

LISTIK = "__LISTIK_BIN__"


def call(*args):
    proc = subprocess.run([sys.executable, LISTIK, *args, "--json"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(1)
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def git(*args):
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(1)


task_id = os.environ["LISTIK_TASK_ID"]
generation = os.environ.get("LISTIK_GENERATION", "")
port = os.environ.get("LISTIK_DEV_PORT", "")

call("claim", task_id, "--holder", "fake", "--actor", "agent:fake")

if os.environ.get("FAKE_HANG_GENERATION") == generation:
    call("heartbeat", task_id, "--holder", "fake", "--actor", "agent:fake")
    time.sleep(600)
    sys.exit(0)

if os.environ.get("FAKE_CRASH_GENERATION") == generation:
    sys.exit(1)

time.sleep(float(os.environ.get("FAKE_WORKER_SLEEP", "0") or "0"))

with open(task_id + ".txt", "w", encoding="utf-8") as fh:
    fh.write(port)

git("add", "-A")
git("commit", "-q", "-m", task_id)

call("done", task_id, "-r", "ok", "--actor", "agent:fake")
'''


@unittest.skipIf(_MISSING_TOOL is not None,
                 f"{_MISSING_TOOL} не найден в PATH -- e2e роя пропущен")
class SwarmE2ECase(AutostartTestCase, FencingHttpCase):
    """Общая обвязка сценария: git-репозиторий проекта, маршрут `fake-low`,
    поддельный воркер, окружение `LISTIK_HOME`/`GIT_CONFIG_*` на время сценария."""

    def setUp(self) -> None:
        super().setUp()
        self.project_dir = self.tmp_path / "project"
        self._init_git_repo(self.project_dir)
        self.gitconfig = self.tmp_path / "gitconfig"
        self.gitconfig.write_text("", encoding="utf-8")

        self._env_patch = mock.patch.dict(os.environ, {
            "LISTIK_HOME": str(self.tmp_path),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
        })
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        # `[server] host/port` дописывается в тот же временный config.toml, который
        # завела `FencingHttpCase` (с токеном) -- воркер берёт хост/порт только из него.
        with (paths.CONFIG_PATH).open("a", encoding="utf-8") as fh:
            fh.write(f'\n[server]\nhost = "127.0.0.1"\nport = {self.port}\n')

        self.make_project("p", path=self.project_dir)
        self.worker_py = self.tmp_path / "worker.py"
        self.worker_py.write_text(
            _WORKER_SRC.replace("__LISTIK_BIN__", str(LISTIK_BIN).replace("\\", "\\\\")),
            encoding="utf-8")
        self.set_routes({
            "key": "fake-low", "kind": "pipeline", "title": "fake-low", "hint": "",
            "visible": True, "icon": "low",
            "roles": {"impl": {"provider": "claude", "label": "Opus", "title": "Opus"}},
            "command": [sys.executable, str(self.worker_py)],
        })

        self.swarm_log_dir = self.tmp_path / "swarm-logs"
        self._swarm_procs: list[tuple[subprocess.Popen, object]] = []
        self._task_ids: list[str] = []

    # Уборка гарантирована независимо от исхода теста, но не через `addCleanup`:
    # `TempDbTestCase.tearDown` (общий предок в цепочке `super().tearDown()`)
    # закрывает `self.conn` и удаляет временный каталог раньше очереди
    # `addCleanup` -- к моменту её вызова снимать уже нечего и не из чего читать
    # `launch_pid`. Уборка процессов идёт первым действием `tearDown`, до того,
    # как цепочка предков закроет базу и сотрёт каталог.
    def tearDown(self) -> None:
        try:
            self._cleanup_processes()
        finally:
            super().tearDown()

    # ------------------------------------------------------------ обвязка git

    def _init_git_repo(self, path: Path) -> None:
        path.mkdir(parents=True)

        def git(*args):
            subprocess.run(["git", *args], cwd=str(path), check=True,
                           capture_output=True, text=True)

        git("init", "-q", "-b", "main")
        git("config", "user.name", "Тест")
        git("config", "user.email", "test@example.com")
        (path / "README.md").write_text("старт\n", encoding="utf-8")
        git("add", "README.md")
        git("commit", "-q", "-m", "старт")

    # ------------------------------------------------------------ обвязка карточек

    def make_scenario_task(self, title: str, *, route: str | None, write_scope=None) -> dict:
        task = self.make_task(title=title, project="p", route=route)
        if write_scope is not None:
            store.update_task(self.conn, task["id"], write_scope=write_scope)
        self._task_ids.append(task["id"])
        return self.row(task["id"])

    def question_texts(self, task_id: str) -> list[str]:
        return [c["text"] for c in self.comments(task_id, "question")]

    def journal_texts(self, task_id: str) -> list[str]:
        return [c["text"] for c in self.comments(task_id, "journal")
                if c["author"] == "agent:listik" and c["text"].startswith("автостарт: маршрут")]

    # ------------------------------------------------------------ рой: запуск/ожидание

    def start_swarm(self, *, parallel=2, interval=1, stale_minutes=None, max_restarts=None,
                    extra_env=None) -> subprocess.Popen:
        args = ["node", str(SWARM_BIN), "--project", "p", "--listik", str(LISTIK_BIN),
                "--listik-host", "127.0.0.1", "--listik-port", str(self.port),
                "--parallel", str(parallel), "--interval", str(interval),
                "--log-dir", str(self.swarm_log_dir),
                "--port-base", "5170", "--port-count", "10"]
        if stale_minutes is not None:
            args += ["--stale-minutes", str(stale_minutes)]
        if max_restarts is not None:
            args += ["--max-restarts", str(max_restarts)]
        if extra_env:
            # Воркер -- дитя сервера, который живёт в потоке этого же процесса
            # (`launcher.start`: `proc_env = os.environ | extra | ...`); FAKE_*
            # переменные должны попасть в окружение процесса теста, а не только
            # в окружение субпроцесса роя (рой их не читает и не передаёт).
            patch = mock.patch.dict(os.environ, extra_env)
            patch.start()
            self.addCleanup(patch.stop)
        env = dict(os.environ)
        out_path = self.tmp_path / f"swarm-stdout-{len(self._swarm_procs)}.log"
        out_fh = open(out_path, "w+", encoding="utf-8")
        proc = subprocess.Popen(args, cwd=str(self.tmp_path), env=env,
                                stdout=out_fh, stderr=subprocess.STDOUT)
        self._swarm_procs.append((proc, out_fh))
        proc._out_path = out_path  # type: ignore[attr-defined]
        return proc

    def _swarm_log_path(self, proc: subprocess.Popen) -> Path | None:
        out_path = getattr(proc, "_out_path", None)
        if out_path is None:
            return None
        for _ in range(50):
            text = Path(out_path).read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                candidate = Path(line.strip())
                if candidate.suffix == ".log" and candidate.exists():
                    return candidate
            time.sleep(0.05)
        return None

    def swarm_stdout(self, proc: subprocess.Popen) -> str:
        out_path = getattr(proc, "_out_path", None)
        if out_path is None:
            return ""
        return Path(out_path).read_text(encoding="utf-8", errors="replace")

    def swarm_log_tail(self, proc: subprocess.Popen, n=40) -> str:
        log_path = self._swarm_log_path(proc)
        if log_path is None:
            return "(лог не найден)"
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])

    def swarm_log_lines(self, proc: subprocess.Popen) -> list[str]:
        log_path = self._swarm_log_path(proc)
        if log_path is None:
            return []
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()

    def _scenario_state(self) -> str:
        parts = []
        for tid in self._task_ids:
            row = self.row(tid)
            if row is None:
                parts.append(f"{tid}: удалена")
                continue
            parts.append(
                f"{tid}: status={row['status']} generation={row['generation']} "
                f"launched_by={row['launched_by']} launch_finished_at={row['launch_finished_at']} "
                f"needs_owner={row['needs_owner']}")
        return "\n".join(parts)

    def wait_swarm(self, proc: subprocess.Popen, *, deadline=60) -> int:
        start = time.monotonic()
        while proc.poll() is None:
            if time.monotonic() - start > deadline:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
                self.fail(
                    f"рой не вышел за {deadline}с\n--- хвост лога ---\n"
                    f"{self.swarm_log_tail(proc)}\n--- карточки сценария ---\n"
                    f"{self._scenario_state()}")
            time.sleep(0.2)
        return proc.returncode

    def poll_until(self, predicate, *, deadline=60, interval=0.2, message="условие"):
        start = time.monotonic()
        while True:
            if predicate():
                return
            if time.monotonic() - start > deadline:
                self.fail(f"не дождался: {message}\n--- карточки сценария ---\n"
                         f"{self._scenario_state()}")
            time.sleep(interval)

    # ------------------------------------------------------------ уборка

    def _cleanup_processes(self) -> None:
        for proc, out_fh in self._swarm_procs:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        pass
            out_fh.close()

        for tid in self._task_ids:
            row = self.conn.execute(
                "SELECT launch_pid, launch_finished_at FROM tasks WHERE id = ?",
                (tid,)).fetchone()
            if row is None or not row["launch_pid"] or row["launch_finished_at"]:
                continue
            pid = row["launch_pid"]
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                continue
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.1)

        for thread in list(launcher_mod._trackers.values()):
            thread.join(timeout=15)


# ------------------------------------------------------------------ сценарий 1

class WaveToCompletionTests(SwarmE2ECase):
    """п.4: волна до конца без ручного вмешательства; задача без маршрута/области —
    needs-owner, не запускается."""

    def test_wave_runs_to_completion_and_flags_unroutable_and_unscoped(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])
        b = self.make_scenario_task("B", route="fake-low", write_scope=["b/"])
        c = self.make_scenario_task("C", route="fake-low", write_scope=["c/"])
        d = self.make_scenario_task("D", route=None, write_scope=["d/"])
        e = self.make_scenario_task("E", route="fake-low", write_scope=None)
        store.add_dep(self.conn, c["id"], a["id"], dep_type="blocks", created_by="dmitry")

        proc = self.start_swarm(parallel=2, interval=1)
        code = self.wait_swarm(proc, deadline=60)
        self.assertEqual(code, 2, self.swarm_log_tail(proc))

        for tid in (a["id"], b["id"], c["id"]):
            row = self.row(tid)
            self.assertEqual(row["status"], "done", tid)
            self.assertEqual(row["generation"], 1, tid)
            worktree = self.project_dir / ".worktrees" / tid
            self.assertEqual(row["worktree"], str(worktree), tid)
            self.assertEqual(row["branch"], f"task/{tid}", tid)
            log = subprocess.run(["git", "-C", str(self.project_dir), "log",
                                 f"task/{tid}", "--format=%s"],
                                 capture_output=True, text=True, check=True)
            self.assertIn(tid, log.stdout.splitlines(), tid)
            content = subprocess.run(
                ["git", "-C", str(self.project_dir), "show", f"task/{tid}:{tid}.txt"],
                capture_output=True, text=True, check=True).stdout.strip()
            port = next(lbl.split(":", 1)[1] for lbl in json.loads(row["labels"] or "[]")
                       if lbl.startswith("port:"))
            self.assertEqual(content, port, tid)
            self.assertIn(int(port), range(5170, 5180), tid)
            self.assertEqual(len(self.journal_texts(tid)), 1, tid)

        def port_of(row) -> str:
            return next(lbl.split(":", 1)[1] for lbl in json.loads(row["labels"] or "[]")
                       if lbl.startswith("port:"))

        self.assertNotEqual(port_of(self.row(a["id"])), port_of(self.row(b["id"])))

        row_a, row_b, row_c = self.row(a["id"]), self.row(b["id"]), self.row(c["id"])
        self.assertGreaterEqual(row_c["launched_at"], max(row_a["closed_at"], row_b["closed_at"]))

        lines = self.swarm_log_lines(proc)
        launch_a_idx = next(i for i, l in enumerate(lines) if f"запуск {a['id']}" in l)
        launch_b_idx = next(i for i, l in enumerate(lines) if f"запуск {b['id']}" in l)
        launch_c_idx = next(i for i, l in enumerate(lines) if f"запуск {c['id']}" in l)
        # Между партиями обязана быть сводка тика, где никто не «бежит» (partия A,B
        # закрылась до того, как C получила слот): «бежит 0 (...)».
        summary_idx = next(
            (i for i in range(max(launch_a_idx, launch_b_idx) + 1, launch_c_idx)
             if "бежит 0 (" in lines[i]), None)
        self.assertIsNotNone(summary_idx,
                             f"не нашёл сводку с «бежит 0» между партиями\n{chr(10).join(lines)}")
        self.assertLess(summary_idx, launch_c_idx)

        for tid in (d["id"], e["id"]):
            row = self.row(tid)
            self.assertTrue(row["needs_owner"], tid)
            self.assertEqual(row["generation"], 0, tid)
            self.assertFalse(row["launched_by"], tid)
            self.assertFalse(row["worktree"], tid)
            questions = self.question_texts(tid)
            self.assertEqual(len(questions), 1, tid)
        self.assertIn("маршрут", self.question_texts(d["id"])[0])
        self.assertIn("write_scope", self.question_texts(e["id"])[0])

        worktrees = list((self.project_dir / ".worktrees").iterdir())
        self.assertEqual(len(worktrees), 3, worktrees)

        stdout = self.swarm_stdout(proc)
        for tid in (a["id"], b["id"], c["id"]):
            self.assertIn(f"запуск {tid}", stdout)
        for tid in (d["id"], e["id"]):
            self.assertIn(f"needs-owner {tid}", stdout)
        log_text = "\n".join(lines)
        for tid in (a["id"], b["id"], c["id"]):
            self.assertIn(f"запуск {tid}", log_text)
        for tid in (d["id"], e["id"]):
            self.assertIn(f"needs-owner {tid}", log_text)


# ------------------------------------------------------------------ сценарий 2

class RestartMidBatchTests(SwarmE2ECase):
    """п.5: перезапуск роя посреди партии не теряет и не задваивает работу."""

    def test_restart_mid_wave_does_not_relaunch_running_tasks(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])
        b = self.make_scenario_task("B", route="fake-low", write_scope=["b/"])

        proc1 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_WORKER_SLEEP": "4"})
        self.poll_until(
            lambda: all(self.row(t["id"])["launched_by"] for t in (a, b)),
            deadline=30, message="A и B получили launched_by")

        proc1.send_signal(signal.SIGTERM)
        code1 = self.wait_swarm(proc1, deadline=30)
        self.assertEqual(code1, 143, self.swarm_log_tail(proc1))
        self.assertIn("остановлен сигналом", self.swarm_log_tail(proc1))

        snapshot = {t["id"]: dict(self.row(t["id"])) for t in (a, b)}

        proc2 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_WORKER_SLEEP": "4"})
        code2 = self.wait_swarm(proc2, deadline=45)
        self.assertEqual(code2, 0, self.swarm_log_tail(proc2))

        for t in (a, b):
            tid = t["id"]
            row = self.row(tid)
            before = snapshot[tid]
            self.assertEqual(row["status"], "done", tid)
            self.assertEqual(row["generation"], 1, tid)
            self.assertEqual(row["launch_pid"], before["launch_pid"], tid)
            self.assertEqual(row["launched_at"], before["launched_at"], tid)
            self.assertEqual(row["dispatch_id"], before["dispatch_id"], tid)
            self.assertEqual(len(self.journal_texts(tid)), 1, tid)
            labels = json.loads(row["labels"] or "[]")
            self.assertEqual(len([l for l in labels if l.startswith("port:")]), 1, tid)
            self.assertEqual(len(self.events(tid, "revoke")), 0, tid)

        worktrees = list((self.project_dir / ".worktrees").iterdir())
        self.assertEqual(len(worktrees), 2, worktrees)

        log2 = self.swarm_log_lines(proc2)
        for tid in (a["id"], b["id"]):
            self.assertFalse(any(f"worktree {tid}" in l for l in log2), tid)
            self.assertFalse(any(f"set {tid} labels" in l for l in log2), tid)
            self.assertFalse(any(f"запуск {tid}" in l for l in log2), tid)
            self.assertFalse(any(f"перезапуск {tid}" in l for l in log2), tid)
        self.assertFalse(any("уже запущена" in l for l in log2))

        stdout2 = self.swarm_stdout(proc2)
        for tid in (a["id"], b["id"]):
            self.assertNotIn(f"запуск {tid}", stdout2)
        summary_lines = [l for l in stdout2.splitlines() if "волна 0:" in l]
        self.assertTrue(summary_lines, stdout2)
        self.assertIn(a["id"], summary_lines[0])
        self.assertIn(b["id"], summary_lines[0])


# ------------------------------------------------------------------ сценарий 3

class HangGenerationTests(SwarmE2ECase):
    """п.6: зависший воркер -- надзор снимает его и перезапускает новым поколением."""

    def test_hang_triggers_restart_with_new_generation(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])

        proc = self.start_swarm(parallel=2, interval=1, stale_minutes=0.2, max_restarts=1,
                                extra_env={"FAKE_HANG_GENERATION": "1"})
        self.poll_until(lambda: self.row(a["id"])["launch_pid"],
                        deadline=30, message="A получила launch_pid поколения 1")
        pid_first = self.row(a["id"])["launch_pid"]

        code = self.wait_swarm(proc, deadline=60)
        self.assertEqual(code, 0, self.swarm_log_tail(proc))

        row = self.row(a["id"])
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["generation"], 3)
        self.assertFalse(row["needs_owner"])

        revokes = self.events(a["id"], "revoke")
        self.assertEqual(len(revokes), 1, revokes)
        self.assertEqual(revokes[0]["actor"], "agent:listik-swarm")
        self.assertTrue((revokes[0]["note"] or "").startswith("рой: перезапуск — stale"),
                        revokes[0]["note"])

        journals = self.journal_texts(a["id"])
        self.assertEqual(len(journals), 2, journals)

        labels = json.loads(row["labels"] or "[]")
        ports = [lbl.split(":", 1)[1] for lbl in labels if lbl.startswith("port:")]
        self.assertEqual(len(ports), 1, labels)
        port = ports[0]
        self.assertTrue(all(f"LISTIK_DEV_PORT={port}" in j for j in journals), journals)

        content = subprocess.run(
            ["git", "-C", str(self.project_dir), "show", f"task/{a['id']}:{a['id']}.txt"],
            capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(content, port)

        with self.assertRaises(ProcessLookupError):
            os.kill(pid_first, 0)


# ------------------------------------------------------------------ сценарий 4

class CrashedWorkerTests(SwarmE2ECase):
    """п.7: упавший воркер -- needs-owner, перезапуск после ответа человека."""

    def test_crash_flags_needs_owner_and_restarts_after_answer(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])

        proc1 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_CRASH_GENERATION": "1"})
        code1 = self.wait_swarm(proc1, deadline=60)
        self.assertEqual(code1, 2, self.swarm_log_tail(proc1))

        row = self.row(a["id"])
        # Задача открыта (воркер успел claim до падения -- "in_progress" тоже
        # открытый статус, не финальный "done").
        self.assertNotEqual(row["status"], "done")
        self.assertTrue(row["needs_owner"])
        self.assertEqual(row["generation"], 1)
        self.assertEqual(row["launch_exit_code"], 1)
        questions = self.question_texts(a["id"])
        self.assertEqual(len(questions), 1, questions)
        self.assertTrue(questions[0].startswith("рой: процесс задачи завершился (код 1"),
                        questions[0])
        self.assertIn(row["launch_log"], questions[0])
        self.assertEqual(len(self.events(a["id"], "revoke")), 0)

        store.set_needs_owner(self.conn, a["id"], value=False, text="разобрался",
                              actor="dmitry")

        proc2 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_CRASH_GENERATION": "1"})
        code2 = self.wait_swarm(proc2, deadline=60)
        self.assertEqual(code2, 0, self.swarm_log_tail(proc2))

        row = self.row(a["id"])
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["generation"], 3)
        revokes = self.events(a["id"], "revoke")
        self.assertEqual(len(revokes), 1, revokes)
        self.assertTrue((revokes[0]["note"] or "").startswith("рой: перезапуск разрешён человеком"),
                        revokes[0]["note"])
        journals = self.journal_texts(a["id"])
        self.assertEqual(len(journals), 2, journals)
        self.assertIn("поколение 3", journals[1])


if __name__ == "__main__":
    unittest.main()
