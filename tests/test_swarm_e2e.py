"""Сквозная приёмка роя (`bin/listik-swarm`) на живом сервере Listik, настоящем
`bin/listik`, настоящем git и поддельном воркере (listik-9hcc, порция d; режим
`driver=swarm` — listik-w7ge, порция d).

Юнит-тесты роя (b/c) гоняют `decide`/`tick` на подставном `listik`; здесь --
процесс роя (`node bin/listik-swarm`) реально общается с реальным сервером
Listik субпроцессами `bin/listik --json`, реально заводит git worktree и реально
запускает/снимает процессы. Рой берёт только карточки роя, поэтому маршрут
сценария `fake-low` — `kind: "swarm"`: каждый этап карточки — отдельный процесс
роли (`spec`/`critic`/`impl`/`judge`), которым служит поддельный воркер; карточку
за роль ведёт сам Listik (`claim`, журнал запуска, разбор последней строки
вывода). Обвязка — `FencingHttpCase` (временная база + живой сервер) и
`AutostartTestCase` (подмена `paths.ROOT_DIR`/`paths.LOGS_DIR`, маршруты в базе,
ожидание потоков слежения) через множественное наследование: обе ведут к общему
`TempDbTestCase`, кооперативный `super().setUp()`/`tearDown()` вызывает обе
цепочки по разу, ничего в исходных классах не меняется.
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

from listik import harnesses_store
from listik import launcher as launcher_mod
from listik import paths
from listik import stage_launch
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

# Воркер роли этапа (режим `driver=swarm`): роль — из `LISTIK_ROLE`, ответ этапа —
# последняя строка stdout. `listik` воркер не зовёт вовсе: claim, журнал запуска и
# переходы делает Listik. `spec`/`critic` -> «готово», `judge` -> «зелёный», `impl` ->
# (зависание | падение при первом запуске `impl` карточки) | сон FAKE_WORKER_SLEEP ->
# файл <id>.txt с портом -> git commit -> «готово». «Первый запуск» — по файлу-метке в
# FAKE_MARK_DIR (вне дерева задачи: `git add -A` метку не закоммитит). Ошибка git --
# вывод в stderr и выход 1. Флаги сценариев автономности/откатов (w7ge.f), только у `impl`:
# FAKE_QUESTION_ONCE=1 — первый `impl` задаёт мягкий вопрос («по умолчанию: JSON») и
# отвечает «вопрос»; FAKE_EMPTY_ONCE=1 — первый `impl` сдаёт «готово» без коммита;
# FAKE_BAD_ONCE=1 — первый `impl` коммитит ещё и `bad.txt`, следующие его удаляют;
# FAKE_SHARED_EARLY=<id,id> — до сна пишет `shared.txt` строкой `<id>`.
_WORKER_SRC = r'''
import os
import subprocess
import sys
import time


def git(*args):
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        sys.exit(1)


task_id = os.environ["LISTIK_TASK_ID"]
role = os.environ.get("LISTIK_ROLE", "")
port = os.environ.get("LISTIK_DEV_PORT", "")

if role == "judge":
    print("зелёный")
    sys.exit(0)

if role != "impl":
    print("готово")
    sys.exit(0)

mark = os.path.join(os.environ["FAKE_MARK_DIR"], task_id + ".impl")
first_impl = not os.path.exists(mark)
open(mark, "a", encoding="utf-8").close()

if first_impl and os.environ.get("FAKE_HANG_ONCE") == "1":
    time.sleep(600)
    sys.exit(0)

if first_impl and os.environ.get("FAKE_CRASH_ONCE") == "1":
    sys.exit(1)

if first_impl and os.environ.get("FAKE_QUESTION_ONCE") == "1":
    print("Какой формат?")
    print("по умолчанию: JSON")
    print("вопрос")
    sys.exit(0)

if first_impl and os.environ.get("FAKE_EMPTY_ONCE") == "1":
    print("готово")
    sys.exit(0)

early = [item.strip() for item in os.environ.get("FAKE_SHARED_EARLY", "").split(",")]
if task_id in early:
    with open("shared.txt", "w", encoding="utf-8") as fh:
        fh.write(task_id + "\n")

time.sleep(float(os.environ.get("FAKE_WORKER_SLEEP", "0") or "0"))

if os.environ.get("FAKE_BAD_ONCE") == "1":
    if first_impl:
        with open("bad.txt", "w", encoding="utf-8") as fh:
            fh.write("bad\n")
    elif os.path.exists("bad.txt"):
        os.remove("bad.txt")

with open(task_id + ".txt", "w", encoding="utf-8") as fh:
    fh.write(port)

git("add", "-A")
git("commit", "-q", "-m", task_id)

print("готово")
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

        # Метки «первый запуск impl» — вне дерева задачи; путь доходит до воркера через
        # окружение процесса теста (сервер в потоке этого процесса, `os.environ | …`).
        self.mark_dir = self.tmp_path / "worker-marks"
        self.mark_dir.mkdir()
        self._env_patch = mock.patch.dict(os.environ, {
            "LISTIK_HOME": str(self.tmp_path),
            "GIT_CONFIG_GLOBAL": str(self.gitconfig),
            "GIT_CONFIG_NOSYSTEM": "1",
            "FAKE_MARK_DIR": str(self.mark_dir),
        })
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

        # `[server] host/port` дописывается в тот же временный config.toml, который
        # завела `FencingHttpCase` (с токеном) -- воркер берёт хост/порт только из него.
        with (paths.CONFIG_PATH).open("a", encoding="utf-8") as fh:
            fh.write(f'\n[server]\nhost = "127.0.0.1"\nport = {self.port}\n')

        # Промпт роли дописывает критерии агента из `stage_launch.AGENTS_DIR`; константа
        # берётся из `paths.ROOT_DIR` при импорте, а `AutostartTestCase` подменяет его
        # временным каталогом — указываем на настоящие файлы агентов репозитория.
        agents_patch = mock.patch.object(
            stage_launch, "AGENTS_DIR", REPO_DIR / "plugins" / "feature-pipeline" / "agents")
        agents_patch.start()
        self.addCleanup(agents_patch.stop)

        self.make_project("p", path=self.project_dir)
        self.worker_py = self.tmp_path / "worker.py"
        self.worker_py.write_text(_WORKER_SRC, encoding="utf-8")
        worker_argv = [sys.executable, str(self.worker_py)]
        # Харнесс роли должен быть в каталоге до записи маршрута `kind: "swarm"`.
        harnesses_store.create(self.conn, {"key": "fake", "label": "fake", "argv": worker_argv})
        self.set_routes(
            # Маршрут роя: все четыре роли — воркер, чтобы у карточки не было пропусков
            # этапа. `icon: "low"` — вес партии роя (`config.weights`), не менять.
            {"key": "fake-low", "kind": "swarm", "title": "fake-low", "hint": "",
             "visible": True, "icon": "low",
             "roles": {role: {"harness": "fake", "argv": worker_argv}
                       for role in ("spec", "critic", "impl", "judge")}},
            # «Чужой» маршрут режима скила — прежняя запись `fake-low` под новым ключом.
            {"key": "skill-low", "kind": "pipeline", "title": "skill-low", "hint": "",
             "visible": True, "icon": "low",
             "roles": {"impl": {"provider": "claude", "label": "Opus", "title": "Opus"}},
             "command": worker_argv},
        )

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

    def launch_journals(self, task_id: str) -> list[dict]:
        """Журналы запуска этапа (`launcher._start_swarm`) в порядке создания."""
        return [c for c in self.comments(task_id, "journal")
                if c["author"] == "agent:listik" and c["text"].startswith("рой: этап ")
                and "карточку взял Listik" in c["text"]]

    def journal_texts(self, task_id: str) -> list[str]:
        return [c["text"] for c in self.launch_journals(task_id)]

    def launch_stages(self, task_id: str) -> list[str]:
        """Этапы запусков: `рой: этап <stage>, роль …` -> `<stage>`."""
        return [text[len("рой: этап "):].split(",", 1)[0]
                for text in self.journal_texts(task_id)]

    # ------------------------------------------------------------ рой: запуск/ожидание

    def start_swarm(self, *, parallel=2, interval=1, max_restarts=None,
                    extra_env=None, extra_args: list[str] | None = None) -> subprocess.Popen:
        args = ["node", str(SWARM_BIN), "--project", "p", "--listik", str(LISTIK_BIN),
                "--listik-host", "127.0.0.1", "--listik-port", str(self.port),
                "--parallel", str(parallel), "--interval", str(interval),
                "--log-dir", str(self.swarm_log_dir),
                "--port-base", "5170", "--port-count", "10",
                "--exit-when-idle"]
        if max_restarts is not None:
            args += ["--max-restarts", str(max_restarts)]
        if extra_args:
            args += list(extra_args)
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

def _port_of(row) -> str:
    return next(lbl.split(":", 1)[1] for lbl in json.loads(row["labels"] or "[]")
                if lbl.startswith("port:"))


class WaveToCompletionTests(SwarmE2ECase):
    """п.4: волна до конца без ручного вмешательства; задача без маршрута не
    запускается и без вопроса, без области — needs-owner; карточки режима скила
    рой не трогает (ни запуска, ни вопроса, ни ресурсных рёбер, ни цикла)."""

    def test_wave_runs_to_completion_and_flags_unroutable_and_unscoped(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])
        b = self.make_scenario_task("B", route="fake-low", write_scope=["b/"])
        c = self.make_scenario_task("C", route="fake-low", write_scope=["c/"])
        d = self.make_scenario_task("D", route=None, write_scope=["d/"])
        e = self.make_scenario_task("E", route="fake-low", write_scope=None)
        # «Чужие» карточки режима скила: F делит область с A, F↔G — цикл жёстких рёбер.
        f = self.make_scenario_task("F", route="skill-low", write_scope=["a/"])
        g = self.make_scenario_task("G", route="skill-low", write_scope=["g/"])
        # Пустой этап и s1/s2 идут без области. Без области остаётся разработка.
        self.conn.execute("UPDATE tasks SET stage = 's3-impl' WHERE id = ?", (e["id"],))
        # `store.add_dep` цикл не пустит — вставка напрямую, как в тестах волн.
        for issue, blocker in ((f["id"], g["id"]), (g["id"], f["id"])):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', ?)", (issue, blocker, "dmitry"))
        self.conn.commit()
        store.add_dep(self.conn, c["id"], a["id"], dep_type="blocks", created_by="dmitry")
        # Без явного `integration` барьер (listik-dzf0) после первого же слияния ставит
        # карточку-стоп и останавливает волну -- явный зелёный список делает интеграцию
        # тривиальной и не мешает волне дойти до конца.
        (self.tmp_path / "swarm.json").write_text(json.dumps({"integration": []}), encoding="utf-8")

        proc = self.start_swarm(parallel=2, interval=1)
        code = self.wait_swarm(proc, deadline=120)
        self.assertEqual(code, 2, self.swarm_log_tail(proc))

        for tid in (a["id"], b["id"], c["id"]):
            row = self.row(tid)
            self.assertEqual(row["status"], "done", tid)
            self.assertEqual(row["generation"], 4, tid)  # spec, critic, impl, judge
            # Барьер (listik-dzf0) вливает закрытую задачу в `main` и убирает её дерево/ветку
            # зелёной интеграцией -- на живом сервере это происходит раньше, чем рой выходит.
            self.assertFalse(row["worktree"], tid)
            self.assertFalse(row["branch"], tid)
            log = subprocess.run(["git", "-C", str(self.project_dir), "log",
                                 "main", "--format=%s"],
                                 capture_output=True, text=True, check=True)
            self.assertIn(tid, log.stdout.splitlines(), tid)
            content = subprocess.run(
                ["git", "-C", str(self.project_dir), "show", f"main:{tid}.txt"],
                capture_output=True, text=True, check=True).stdout.strip()
            port = _port_of(row)
            self.assertEqual(content, port, tid)
            self.assertIn(int(port), range(5170, 5180), tid)
            journals = self.journal_texts(tid)
            self.assertEqual(len(journals), 4, journals)
            self.assertEqual(self.launch_stages(tid),
                             ["s1-spec", "s2-review", "s3-impl", "s4-judge"], journals)
            self.assertTrue(all(f"LISTIK_DEV_PORT={port}" in j for j in journals), journals)

        self.assertNotEqual(_port_of(self.row(a["id"])), _port_of(self.row(b["id"])))

        # C ждёт A (`blocks`): первый запуск C — не раньше закрытия A.
        first_c = self.launch_journals(c["id"])[0]
        self.assertGreaterEqual(first_c["created_at"], self.row(a["id"])["closed_at"])

        for tid in (d["id"], e["id"]):
            row = self.row(tid)
            self.assertEqual(row["generation"], 0, tid)
            self.assertFalse(row["launched_by"], tid)
            self.assertFalse(row["worktree"], tid)
        # Без маршрута рой карточку не берёт и не спрашивает (listik-utw9).
        self.assertFalse(self.row(d["id"])["needs_owner"])
        self.assertEqual(self.question_texts(d["id"]), [])
        self.assertTrue(self.row(e["id"])["needs_owner"])
        questions = self.question_texts(e["id"])
        self.assertEqual(len(questions), 1)
        self.assertIn("write_scope", questions[0])

        # Карточки режима скила рой не видит: ни запуска, ни метки порта, ни вопроса.
        for tid in (f["id"], g["id"]):
            row = self.row(tid)
            self.assertEqual(row["generation"], 0, tid)
            self.assertFalse(row["launched_by"], tid)
            self.assertFalse(row["worktree"], tid)
            self.assertFalse(row["needs_owner"], tid)
            self.assertEqual(self.question_texts(tid), [], tid)
            labels = json.loads(row["labels"] or "[]")
            self.assertFalse([l for l in labels if l.startswith("port:")], (tid, labels))
        resource = self.conn.execute(
            "SELECT issue_id, depends_on FROM deps WHERE dep_type = 'resource-blocks' "
            "AND (issue_id IN (?, ?) OR depends_on IN (?, ?))",
            (f["id"], g["id"], f["id"], g["id"])).fetchall()
        self.assertEqual([tuple(r) for r in resource], [])

        # Барьер убирает деревья закрытых задач после зелёной интеграции.
        worktrees = list((self.project_dir / ".worktrees").iterdir())
        self.assertEqual(len(worktrees), 0, worktrees)

        # В `main` — только старт и файлы воркера A/B/C: метки «первый запуск» не влиты.
        tree = subprocess.run(
            ["git", "-C", str(self.project_dir), "ls-tree", "-r", "--name-only", "main"],
            capture_output=True, text=True, check=True).stdout.split()
        self.assertEqual(sorted(tree),
                         sorted(["README.md", *(f"{t['id']}.txt" for t in (a, b, c))]))

        lines = self.swarm_log_lines(proc)
        stdout = self.swarm_stdout(proc)
        for tid in (a["id"], b["id"], c["id"]):
            self.assertIn(f"запуск {tid}", stdout)
        self.assertNotIn(f"needs-owner {d['id']}", stdout)
        self.assertIn(f"needs-owner {e['id']}", stdout)
        log_text = "\n".join(lines)
        for tid in (a["id"], b["id"], c["id"]):
            self.assertIn(f"запуск {tid}", log_text)
        self.assertNotIn(f"needs-owner {d['id']}", log_text)
        self.assertIn(f"needs-owner {e['id']}", log_text)
        self.assertFalse([l for l in lines if "циклы:" in l], log_text)


# ------------------------------------------------------------------ сценарий 2

class RestartMidBatchTests(SwarmE2ECase):
    """п.5: перезапуск роя посреди партии не теряет и не задваивает работу."""

    def test_restart_mid_wave_does_not_relaunch_running_tasks(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])
        b = self.make_scenario_task("B", route="fake-low", write_scope=["b/"])

        proc1 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_WORKER_SLEEP": "4"})

        def impl_running(tid: str) -> bool:
            row = self.row(tid)
            return (row["stage"] == "s3-impl" and bool(row["launch_pid"])
                    and not row["launch_finished_at"])

        self.poll_until(lambda: all(impl_running(t["id"]) for t in (a, b)),
                        deadline=60, message="у A и B идёт процесс этапа s3-impl")
        impl_dispatch = {t["id"]: self.row(t["id"])["dispatch_id"] for t in (a, b)}

        proc1.send_signal(signal.SIGTERM)
        code1 = self.wait_swarm(proc1, deadline=30)
        self.assertEqual(code1, 143, self.swarm_log_tail(proc1))
        self.assertIn("остановлен сигналом", self.swarm_log_tail(proc1))

        # Старт второго роя: что запущено после него — журналы, которых сейчас нет
        # (сравнение по набору, а не по секундным меткам `created_at`).
        before_second = {t["id"]: {c["id"] for c in self.launch_journals(t["id"])}
                         for t in (a, b)}

        proc2 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_WORKER_SLEEP": "4"})
        code2 = self.wait_swarm(proc2, deadline=90)
        self.assertEqual(code2, 0, self.swarm_log_tail(proc2))

        for t in (a, b):
            tid = t["id"]
            row = self.row(tid)
            self.assertEqual(row["status"], "done", tid)
            self.assertEqual(row["generation"], 4, tid)  # spec, critic, impl, judge
            journals = self.launch_journals(tid)
            self.assertEqual(self.launch_stages(tid),
                             ["s1-spec", "s2-review", "s3-impl", "s4-judge"], tid)
            # Процесс `s3-impl` пережил рой: его журнал — тот самый запуск.
            self.assertIn(f"запуск {impl_dispatch[tid]}", journals[2]["text"], tid)
            # Второй рой запускал только то, что осталось, — приёмку.
            after = [c["text"] for c in journals if c["id"] not in before_second[tid]]
            self.assertEqual([text[len("рой: этап "):].split(",", 1)[0] for text in after],
                             ["s4-judge"], tid)
            labels = json.loads(row["labels"] or "[]")
            self.assertEqual(len([l for l in labels if l.startswith("port:")]), 1, tid)
            self.assertEqual(len(self.events(tid, "revoke")), 0, tid)

        worktrees = list((self.project_dir / ".worktrees").iterdir())
        self.assertEqual(len(worktrees), 2, worktrees)

        log2 = self.swarm_log_lines(proc2)
        for tid in (a["id"], b["id"]):
            self.assertFalse(any(f"set {tid} labels" in l for l in log2), tid)
            self.assertFalse(any(f"перезапуск {tid}" in l for l in log2), tid)
        self.assertFalse(any("уже запущена" in l for l in log2))

        stdout2 = self.swarm_stdout(proc2)
        summary_lines = [l for l in stdout2.splitlines() if "волна 0:" in l]
        self.assertTrue(summary_lines, stdout2)
        self.assertIn(a["id"], summary_lines[0])
        self.assertIn(b["id"], summary_lines[0])


# ------------------------------------------------------------------ сценарий 3

class HangGenerationTests(SwarmE2ECase):
    """п.6: зависший этап -- надзор снимает его по таймауту и запускает тот же
    этап новым поколением."""

    def test_hang_triggers_restart_with_new_generation(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])

        proc = self.start_swarm(parallel=2, interval=1, max_restarts=1,
                                extra_env={"FAKE_HANG_ONCE": "1"},
                                extra_args=["--timeout-minutes", "0.2"])

        def impl_running() -> bool:
            row = self.row(a["id"])
            return (row["stage"] == "s3-impl" and bool(row["launch_pid"])
                    and not row["launch_finished_at"])

        self.poll_until(impl_running, deadline=60, message="у A идёт первый процесс s3-impl")
        pid_first = self.row(a["id"])["launch_pid"]

        code = self.wait_swarm(proc, deadline=120)
        self.assertEqual(code, 0, self.swarm_log_tail(proc))

        row = self.row(a["id"])
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["generation"], 6)  # spec, critic, impl, revoke, impl, judge
        self.assertFalse(row["needs_owner"])

        revokes = self.events(a["id"], "revoke")
        self.assertEqual(len(revokes), 1, revokes)
        self.assertEqual(revokes[0]["actor"], "agent:listik-swarm")
        self.assertTrue((revokes[0]["note"] or "").startswith("рой: перезапуск —"),
                        revokes[0]["note"])

        journals = self.journal_texts(a["id"])
        self.assertEqual(len(journals), 5, journals)
        self.assertEqual(self.launch_stages(a["id"]).count("s3-impl"), 2, journals)

        labels = json.loads(row["labels"] or "[]")
        ports = [lbl.split(":", 1)[1] for lbl in labels if lbl.startswith("port:")]
        self.assertEqual(len(ports), 1, labels)
        port = ports[0]
        self.assertTrue(all(f"LISTIK_DEV_PORT={port}" in j for j in journals), journals)

        # Файл воркера — в ветке задачи, а если барьер уже влил её — в `main`.
        shown = subprocess.run(
            ["git", "-C", str(self.project_dir), "show", f"task/{a['id']}:{a['id']}.txt"],
            capture_output=True, text=True)
        if shown.returncode != 0:
            shown = subprocess.run(
                ["git", "-C", str(self.project_dir), "show", f"main:{a['id']}.txt"],
                capture_output=True, text=True, check=True)
        self.assertEqual(shown.stdout.strip(), port)

        with self.assertRaises(ProcessLookupError):
            os.kill(pid_first, 0)


# ------------------------------------------------------------------ сценарий 4

class CrashedWorkerTests(SwarmE2ECase):
    """п.7: этап не сдал работу -- needs-owner, после ответа человека тот же этап снова."""

    def test_crash_flags_needs_owner_and_restarts_after_answer(self):
        a = self.make_scenario_task("A", route="fake-low", write_scope=["a/"])

        proc1 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_CRASH_ONCE": "1"})
        code1 = self.wait_swarm(proc1, deadline=90)
        self.assertEqual(code1, 2, self.swarm_log_tail(proc1))

        row = self.row(a["id"])
        self.assertNotEqual(row["status"], "done")
        self.assertTrue(row["needs_owner"])
        self.assertEqual(row["generation"], 3)  # spec, critic, impl
        self.assertEqual(row["launch_exit_code"], 1)
        questions = self.question_texts(a["id"])
        self.assertEqual(len(questions), 1, questions)
        self.assertTrue(questions[0].startswith("рой: этап s3-impl (impl) не сдал работу"),
                        questions[0])
        self.assertIn(row["launch_log"], questions[0])
        self.assertEqual(len(self.events(a["id"], "revoke")), 0)

        store.set_needs_owner(self.conn, a["id"], value=False, text="разобрался",
                              actor="dmitry")

        proc2 = self.start_swarm(parallel=2, interval=1,
                                 extra_env={"FAKE_CRASH_ONCE": "1"})
        code2 = self.wait_swarm(proc2, deadline=90)
        self.assertEqual(code2, 0, self.swarm_log_tail(proc2))

        row = self.row(a["id"])
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["generation"], 5)  # spec, critic, impl + impl, judge
        self.assertEqual(len(self.events(a["id"], "revoke")), 0)
        self.assertEqual(self.launch_stages(a["id"]).count("s3-impl"), 2,
                         self.journal_texts(a["id"]))


if __name__ == "__main__":
    unittest.main()
