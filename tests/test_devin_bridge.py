"""Мост devin: отказ инструмента — ошибка, resume снимает проверку доверия (listik-0hm2).

Настоящий devin не вызывается: бинарь подменяется python-скриптом во временном
каталоге, каталог состояния моста и база сессий — тоже временные, сети и
модели тут нет. Путь к мосту можно подменить переменной DEVIN_BRIDGE_SCRIPT
(негативный контроль на старом скрипте).
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
DEVIN_SH = pathlib.Path(
    os.environ.get("DEVIN_BRIDGE_SCRIPT")
    or REPO / "plugins" / "devin" / "scripts" / "devin-run.sh"
)

ANSWER = "FINAL-ANSWER-42"
INTERIM = "INTERIM-TEXT"

REASONS = {
    "read": "devin rejected a tool call in --permission read: the answer is partial; "
            "rerun with --permission bash or write if the task needs it",
    "bash": "devin rejected a tool call in --permission bash: the answer is partial; "
            "rerun with --permission write if the task needs it",
    "write": "devin rejected a tool call in --permission write: the answer is partial; "
             "every tool is already auto-approved in this mode - check the transcript",
}

# Подменный devin. Понимает ровно то, что зовёт мост:
#   devin --model … --permission-mode … [--sandbox] [--respect-workspace-trust false]
#         [-r <id>] --prompt-file <файл> -p
FAKE_DEVIN = r'''#!/usr/bin/env python3
import os, sqlite3, sys, time, uuid

argv = sys.argv[1:]
with open(os.environ["FAKE_DEVIN_LOG"], "a", encoding="utf-8") as fh:
    fh.write(" ".join(argv) + "\n")

pairs = list(zip(argv, argv[1:]))
if ("--respect-workspace-trust", "false") not in pairs:
    sys.stderr.write("Refusing to run in an untrusted workspace\n")
    sys.exit(1)

resume = dict(pairs).get("-r")
prompt_file = dict(pairs).get("--prompt-file")

con = sqlite3.connect(os.environ["DEVIN_CLAUDE_SESSIONS_DB"])
con.execute("create table if not exists sessions (id text primary key, working_directory text,"
            " model text, agent_mode text, created_at integer, last_activity_at integer, title text)")
con.execute("create table if not exists prompt_history (session_id text, content text)")
con.execute("create table if not exists message_nodes (session_id text, row_id integer, chat_message text)")
if not resume:
    sid = "sess-" + uuid.uuid4().hex[:12]
    now = int(time.time())
    con.execute("insert into sessions values (?,?,?,?,?,?,?)",
                (sid, os.getcwd(), "swe-2-medium", "auto", now, now, "fake"))
    with open(prompt_file, encoding="utf-8") as fh:
        con.execute("insert into prompt_history values (?,?)", (sid, fh.read()))
con.commit()
con.close()

if os.environ.get("FAKE_DEVIN_REJECT") == "1":
    sys.stdout.write("%s\n")
    sys.stderr.write("warning: rejected a tool call that requires confirmation. "
                     "Running in non-interactive mode\n")
    sys.exit(0)
sys.stdout.write("%s\n")
''' % (INTERIM, ANSWER)


def _missing_tools() -> str | None:
    if not shutil.which("bash"):
        return "нет bash"
    if not shutil.which("python3"):
        return "нет python3"
    try:
        import sqlite3  # noqa: F401
    except ImportError:
        return "нет модуля sqlite3"
    return None


class DevinBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        reason = _missing_tools()
        if reason:
            self.skipTest(reason)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # realpath: мост ищет сессию по working_directory = cwd, а подменный
        # devin пишет os.getcwd() (/private/var/… на macOS).
        self.root = pathlib.Path(os.path.realpath(self.tmp.name))
        self.state = self.root / "state"
        self.work = self.root / "work"
        self.work.mkdir()
        self.elsewhere = self.root / "elsewhere"
        self.elsewhere.mkdir()
        self.log = self.root / "fake-devin.log"
        self.db = self.root / "sessions.db"
        self.bin = self.root / "devin"
        self.bin.write_text(FAKE_DEVIN.replace("#!/usr/bin/env python3", "#!" + sys.executable, 1),
                            encoding="utf-8")
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)

    def _run(self, args: list[str], stdin: str = "", reject: bool = False,
             timeout: int = 30) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.update({
            "DEVIN_CLAUDE_BIN": str(self.bin),
            "DEVIN_CLAUDE_STATE_DIR": str(self.state),
            "DEVIN_CLAUDE_SESSIONS_DB": str(self.db),
            "FAKE_DEVIN_LOG": str(self.log),
        })
        env.pop("FAKE_DEVIN_REJECT", None)
        if reject:
            env["FAKE_DEVIN_REJECT"] = "1"
        return subprocess.run(
            ["bash", str(DEVIN_SH), *args],
            input=stdin, capture_output=True, text=True,
            env=env, cwd=str(self.elsewhere), timeout=timeout,
        )

    def _launch(self, args: list[str], reject: bool = False) -> str:
        proc = self._run(args, stdin="задача", reject=reject)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        job_id = proc.stdout.strip().splitlines()[0]
        self.assertTrue(job_id.startswith("devin-"), job_id)
        return job_id

    def _wait_job(self, job_id: str, timeout: float = 20.0) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            proc = self._run(["status", "--json", job_id], timeout=10)
            if proc.returncode == 0 and proc.stdout.strip():
                last = json.loads(proc.stdout)
                if last.get("status") != "running":
                    return last
            time.sleep(0.2)
        self.fail(f"джоба {job_id} не завершилась: {last}")

    def _log_lines(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines() if self.log.exists() else []

    # --- отказ инструмента ---------------------------------------------------
    def test_rejection_foreground_every_mode(self) -> None:
        for perm in ("read", "bash", "write"):
            with self.subTest(perm=perm):
                proc = self._run(["run", "--permission", perm, "--cwd", str(self.work)],
                                 stdin="задача", reject=True)
                self.assertEqual(proc.returncode, 6, proc.stderr)
                self.assertIn(f"error: {REASONS[perm]}", proc.stderr)
                self.assertIn(INTERIM, proc.stdout)

    def test_rejection_background(self) -> None:
        for perm in ("read", "bash"):
            with self.subTest(perm=perm):
                job_id = self._launch(["run", "--background", "--permission", perm,
                                       "--cwd", str(self.work)], reject=True)
                card = self._wait_job(job_id)
                self.assertEqual(card.get("status"), "failed", card)
                self.assertEqual(card.get("exit"), "0", card)
                self.assertEqual(card.get("error"), REASONS[perm], card)
                res = self._run(["result", job_id])
                self.assertEqual(res.returncode, 6, res.stderr)
                self.assertIn(INTERIM, res.stdout)
                self.assertIn(f"job failed (exit 0) - {REASONS[perm]}", res.stderr)

    # --- контроль без отказа ---------------------------------------------------
    def test_no_rejection_foreground_and_background(self) -> None:
        proc = self._run(["run", "--cwd", str(self.work)], stdin="задача")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), ANSWER)

        job_id = self._launch(["run", "--background", "--cwd", str(self.work)])
        card = self._wait_job(job_id)
        self.assertEqual(card.get("status"), "completed", card)
        res = self._run(["result", job_id])
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), ANSWER)

    # --- resume и доверие ------------------------------------------------------
    def test_resume_skips_workspace_trust(self) -> None:
        first = self._run(["run", "--session", "проба", "--cwd", str(self.work)], stdin="первый")
        self.assertEqual(first.returncode, 0, first.stderr)
        lines = self._log_lines()
        self.assertEqual(len(lines), 1, lines)

        import sqlite3
        con = sqlite3.connect(str(self.db))
        sid = con.execute("select id from sessions").fetchone()[0]
        con.close()

        second = self._run(["resume", "--session", "проба"], stdin="второй")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(second.stdout.strip(), ANSWER)
        lines = self._log_lines()
        self.assertEqual(len(lines), 2, lines)
        self.assertIn(f"-r {sid}", lines[1])
        self.assertIn("--respect-workspace-trust false", lines[1])

        job_id = self._launch(["resume", "--session", "проба", "--background"])
        card = self._wait_job(job_id)
        self.assertEqual(card.get("status"), "completed", card)
        self.assertEqual(card.get("trust_check"), "skipped", card)


if __name__ == "__main__":
    unittest.main()
