"""Обвязка opencode: run --session → resume --session (listik-gaag).

Настоящий `opencode` не вызывается: бинарь подменяется скриптом во временном
каталоге, каталог состояния обвязки — тоже временный, сети и модели тут нет.
Проверяется контракт обвязки: `run --session <имя>` заводит именованную
сессию, `resume` (по имени и по job-id) уходит в ТУ ЖЕ сессию opencode, и
гонка финализации (воркер жив, процесс opencode уже вышел) не выдаётся за
осиротевшую задачу.
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import stat
import subprocess
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
OPENCODE_SH = REPO / "plugins" / "opencode" / "scripts" / "opencode-run.sh"

# Подменный opencode. Понимает ровно то, что зовёт обвязка:
#   opencode run --format json --auto --model … --agent … [--title|--session]
#   opencode session list --format json
# Сессии хранит в FAKE_OPENCODE_STORE, вызовы пишет в FAKE_OPENCODE_LOG.
FAKE_OPENCODE = r'''#!/usr/bin/env python3
import json, os, sys, time

argv = sys.argv[1:]
log = os.environ.get("FAKE_OPENCODE_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(" ".join(argv) + "\n")

store_path = os.environ["FAKE_OPENCODE_STORE"]


def load():
    try:
        with open(store_path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


def save(rows):
    with open(store_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh)


if argv[:2] == ["session", "list"]:
    json.dump(load(), sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)

if not argv or argv[0] != "run":
    sys.stderr.write("fake opencode: неизвестная команда %r\n" % (argv,))
    sys.exit(3)

sid = title = None
i = 1
while i < len(argv):
    if argv[i] == "--session":
        sid = argv[i + 1]
        i += 2
    elif argv[i] == "--title":
        title = argv[i + 1]
        i += 2
    else:
        i += 1

rows = load()
if sid is None:
    sid = "ses_%04d" % (len(rows) + 1)
    rows.append({
        "id": sid,
        "title": title or "",
        "directory": os.getcwd(),
        "updated": int(time.time() * 1000) + len(rows),
    })
    save(rows)

prompt = sys.stdin.read().strip()

delay = float(os.environ.get("FAKE_OPENCODE_DELAY", "0") or 0)
if delay:
    time.sleep(delay)

msg = "msg_1"
for chunk in ("ответ[", prompt, "]"):
    # Компактный JSONL, как у настоящего opencode: обвязка достаёт id сессии
    # обычным grep по `"sessionID":"…"`, без пробела после двоеточия.
    sys.stdout.write(json.dumps({
        "type": "text",
        "sessionID": sid,
        "part": {"type": "text", "messageID": msg, "sessionID": sid, "text": chunk},
    }, ensure_ascii=False, separators=(",", ":")) + "\n")
sys.stdout.flush()
'''


class OpencodeRunResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.log = self.root / "fake-opencode.log"
        self.store = self.root / "sessions.json"
        self.bin = self.root / "opencode"
        self.bin.write_text(FAKE_OPENCODE, encoding="utf-8")
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    # --- запуск обвязки ----------------------------------------------------
    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "OPENCODE_CLAUDE_STATE_DIR": str(self.state),
            "OPENCODE_BIN": str(self.bin),
            "FAKE_OPENCODE_LOG": str(self.log),
            "FAKE_OPENCODE_STORE": str(self.store),
        })
        env.update(extra)
        return env

    def _run(self, args: list[str], stdin: str = "", timeout: int = 30,
             **env: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(OPENCODE_SH), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self._env(**env),
            cwd=str(self.workdir),
            timeout=timeout,
        )

    def _wait_job(self, job_id: str, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            proc = self._run(["status", "--json", job_id])
            if proc.returncode == 0 and proc.stdout.strip():
                last = json.loads(proc.stdout)
                if last.get("status") != "running":
                    return last
            time.sleep(0.1)
        self.fail(f"задача {job_id} не завершилась: {last}")

    def _launch(self, args: list[str], stdin: str) -> str:
        proc = self._run(args, stdin=stdin)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        job_id = proc.stdout.strip().splitlines()[0]
        self.assertTrue(job_id.startswith("opencode-"), job_id)
        return job_id

    def _fake_log(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    # --- тесты -------------------------------------------------------------
    def test_usage_lists_resume(self) -> None:
        proc = self._run(["-h"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("resume <session-name|job-id>", proc.stderr)

    def test_run_session_then_resume_session_same_session(self) -> None:
        job_id = self._launch(
            ["run", "--session", "проба", "--background", "--label", "первый"],
            stdin="первый ход",
        )
        card = self._wait_job(job_id)
        self.assertEqual(card.get("status"), "completed", card)
        sid = card.get("opencode_session")
        self.assertTrue(sid and sid.startswith("ses_"), card)
        self.assertEqual(card.get("session_name"), "проба", card)

        result = self._run(["result", job_id])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "ответ[первый ход]")

        resumed_id = self._launch(
            ["resume", "--session", "проба", "--background"],
            stdin="второй ход",
        )
        self.assertNotEqual(resumed_id, job_id)
        resumed = self._wait_job(resumed_id)
        self.assertEqual(resumed.get("status"), "completed", resumed)
        # Тот же id сессии — значит второй ход ушёл в ту же сессию opencode.
        self.assertEqual(resumed.get("opencode_session"), sid, resumed)
        self.assertIn(f"--session {sid}", self._fake_log())
        # Новую сессию второй прогон не заводил.
        self.assertEqual(len(json.loads(self.store.read_text(encoding="utf-8"))), 1)

        result2 = self._run(["result", resumed_id])
        self.assertEqual(result2.returncode, 0, result2.stderr)
        self.assertEqual(result2.stdout.strip(), "ответ[второй ход]")

    def test_resume_by_job_id_keeps_session_and_records_source(self) -> None:
        job_id = self._launch(
            ["run", "--session", "по-джобе", "--background"],
            stdin="первый ход",
        )
        sid = self._wait_job(job_id).get("opencode_session")
        self.assertTrue(sid and sid.startswith("ses_"))

        resumed_id = self._launch(["resume", job_id, "--background"], stdin="второй ход")
        resumed = self._wait_job(resumed_id)
        self.assertEqual(resumed.get("status"), "completed", resumed)
        self.assertEqual(resumed.get("opencode_session"), sid, resumed)
        self.assertEqual(resumed.get("resumed_from"), job_id, resumed)
        self.assertEqual(resumed.get("session_name"), "по-джобе", resumed)

    def test_run_with_taken_session_name_exits_2(self) -> None:
        job_id = self._launch(["run", "--session", "занято", "--background"], stdin="раз")
        self._wait_job(job_id)
        proc = self._run(["run", "--session", "занято", "--background"], stdin="два")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("already exists", proc.stderr)
        self.assertIn("resume", proc.stderr)

    def test_resume_unknown_session_exits_2(self) -> None:
        proc = self._run(["resume", "--session", "такой-нет"], stdin="ход")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("in the bridge state", proc.stderr)
        self.assertIn("run --session", proc.stderr)

    # --- гонка финализации --------------------------------------------------
    def _dead_pid(self) -> int:
        """Pid гарантированно мёртвого (и пожатого) процесса."""
        for _ in range(20):
            proc = subprocess.Popen(["sh", "-c", "exit 0"])
            proc.wait()
            try:
                os.kill(proc.pid, 0)
            except OSError:
                return proc.pid
            time.sleep(0.05)
        self.skipTest("не удалось получить заведомо свободный pid")

    def _job_with_meta(self, job_id: str, **fields: str) -> pathlib.Path:
        job_dir = self.state / "jobs" / job_id
        job_dir.mkdir(parents=True)
        (job_dir / "events.jsonl").write_text("", encoding="utf-8")
        (job_dir / "output.txt").write_text("", encoding="utf-8")
        meta = {
            "id": job_id,
            "status": "running",
            "cwd": str(self.workdir),
            "mode": "read-only",
            "model": "—",
            "agent": "build",
            "variant": "—",
            "label": "гонка",
            "session": "—",
            "session_name": "—",
            "opencode_session": "—",
            "resumed_from": "—",
            "timeout": "0",
            "started": "2026-01-01T00:00:00Z",
            "started_epoch": str(int(time.time()) - 600),
        }
        meta.update(fields)
        (job_dir / "meta").write_text(
            "".join(f"{k}={v}\n" for k, v in meta.items()), encoding="utf-8"
        )
        return job_dir

    def test_finalizing_worker_is_running_and_dead_worker_is_orphaned(self) -> None:
        """Процесс opencode уже вышел, воркер ещё дописывает итог.

        Регресс: такая задача считалась осиротевшей, и `result` успешной
        задачи падал кодом 6 «воркер задачи исчез».
        """
        worker = subprocess.Popen(["sleep", "60"])
        self.addCleanup(self._kill, worker)
        dead = self._dead_pid()
        job_id = "opencode-race"
        self._job_with_meta(job_id, pid=str(dead), worker_pid=str(worker.pid))

        card = json.loads(self._run(["status", "--json", job_id]).stdout)
        self.assertEqual(card.get("status"), "running", card)
        self.assertEqual(card.get("meta_status"), "running", card)

        res = self._run(["result", job_id])
        self.assertEqual(res.returncode, 5, res.stderr)
        self.assertIn("job still running", res.stderr)

        self._kill(worker)
        card = json.loads(self._run(["status", "--json", job_id]).stdout)
        self.assertEqual(card.get("status"), "orphaned", card)
        res = self._run(["result", job_id])
        self.assertEqual(res.returncode, 6, res.stderr)
        self.assertIn("the job worker vanished", res.stderr)

    def test_finished_job_is_not_orphaned_after_worker_exit(self) -> None:
        """Воркер дописал итог и вышел — статус берётся из meta, не orphaned."""
        job_id = "opencode-done"
        job_dir = self._job_with_meta(
            job_id,
            status="completed",
            pid=str(self._dead_pid()),
            worker_pid=str(self._dead_pid()),
            exit="0",
            finished="2026-01-01T00:01:00Z",
            finished_epoch=str(int(time.time()) - 540),
        )
        (job_dir / "output.txt").write_text("итог\n", encoding="utf-8")
        card = json.loads(self._run(["status", "--json", job_id]).stdout)
        self.assertEqual(card.get("status"), "completed", card)
        res = self._run(["result", job_id])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), "итог")

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait()


if __name__ == "__main__":
    unittest.main()
