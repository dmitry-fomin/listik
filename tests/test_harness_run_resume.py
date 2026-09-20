"""Resume-команда обвязок Codex и dsh (smith-ahva, smith-3xe5).

Настоящие `codex`/`dsh` не вызываются: бинарь подменяется скриптом во временном
каталоге, состояние джоб и CODEX_HOME — тоже временные. Проверяется контракт
оркестратора: id сессии в status/--json, `resume` зовёт `codex exec resume`,
у dsh headless `resume` выходит с кодом 2.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CODEX_SH = REPO / "plugins" / "codex" / "scripts" / "codex-run.sh"
DSH_SH = REPO / "plugins" / "dsh" / "scripts" / "dsh-run.sh"

FAKE_SID = "11111111-2222-3333-4444-555555555555"

FAKE_CODEX = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_CODEX_LOG}"
out=""
i=1
while [[ $i -le $# ]]; do
  eval "a=\${$i}"
  if [[ "$a" == "-o" || "$a" == "--output-last-message" ]]; then
    i=$((i+1)); eval "out=\${$i}"
  fi
  i=$((i+1))
done
# resume: argv содержит подкоманду resume и uuid
if printf '%s\n' "$*" | grep -q " resume "; then
  [[ -n "$out" ]] || exit 1
  { echo -n "resumed:"; cat; echo; } > "$out"
  exit 0
fi
# новый прогон: пишем rollout, чтобы обвязка нашла session_id
cwd="$(pwd)"
day="$(date -u +%Y/%m/%d)"
mkdir -p "${CODEX_HOME}/sessions/${day}"
ts="$(date -u +%Y-%m-%dT%H-%M-%S)"
roll="${CODEX_HOME}/sessions/${day}/rollout-${ts}-11111111-2222-3333-4444-555555555555.jsonl"
python3 - "$roll" "$cwd" <<'PY'
import json, sys
path, cwd = sys.argv[1], sys.argv[2]
sid = "11111111-2222-3333-4444-555555555555"
row = {
    "timestamp": "2026-01-01T00:00:00Z",
    "type": "session_meta",
    "payload": {"id": sid, "session_id": sid, "cwd": cwd, "originator": "codex_exec"},
}
with open(path, "w", encoding="utf-8") as fh:
    fh.write(json.dumps(row) + "\n")
PY
[[ -n "$out" ]] || exit 1
echo "hello-from-fake" > "$out"
"""


class HarnessRunResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.codex_state = self.root / "codex-state"
        self.codex_home = self.root / "codex-home"
        self.dsh_state = self.root / "dsh-state"
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.fake_log = self.root / "fake-codex.log"
        self.fake_bin = self.root / "fake-codex"
        self.fake_bin.write_text(FAKE_CODEX, encoding="utf-8")
        self.fake_bin.chmod(self.fake_bin.stat().st_mode | stat.S_IEXEC)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _codex_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "CODEX_CLAUDE_STATE_DIR": str(self.codex_state),
            "CODEX_HOME": str(self.codex_home),
            "CODEX_BIN": str(self.fake_bin),
            "FAKE_CODEX_LOG": str(self.fake_log),
        })
        return env

    def _dsh_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "DSH_CLAUDE_STATE_DIR": str(self.dsh_state),
            "DSH_HOME": str(self.root / "dsh-home"),
        })
        return env

    def _run_codex(self, args: list[str], stdin: str = "", timeout: int = 15) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(CODEX_SH), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self._codex_env(),
            cwd=str(self.workdir),
            timeout=timeout,
        )

    def _run_dsh(self, args: list[str], stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(DSH_SH), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self._dsh_env(),
            cwd=str(self.workdir),
            timeout=15,
        )

    def _wait_codex_job(self, job_id: str, timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            proc = self._run_codex(["status", "--json", job_id])
            if proc.returncode == 0 and proc.stdout.strip():
                last = json.loads(proc.stdout)
                if last.get("status") != "running":
                    return last
            time.sleep(0.1)
        self.fail(f"задача {job_id} не завершилась: {last}")

    def test_codex_usage_lists_resume(self) -> None:
        proc = self._run_codex(["-h"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("resume <job-id>", proc.stderr)

    def test_codex_run_stores_session_and_resume_calls_exec(self) -> None:
        launched = self._run_codex(
            ["run", "--background", "--label", "probe", "--cwd", str(self.workdir)],
            stdin="first turn",
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)
        job_id = launched.stdout.strip().splitlines()[0]
        self.assertTrue(job_id.startswith("codex-"), job_id)
        card = self._wait_codex_job(job_id)
        self.assertEqual(card.get("status"), "completed", card)
        self.assertEqual(card.get("codex_session"), FAKE_SID, card)

        resumed = self._run_codex(
            ["resume", job_id, "--background", "--label", "again"],
            stdin="second turn",
        )
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        resume_id = resumed.stdout.strip().splitlines()[0]
        self.assertNotEqual(resume_id, job_id)
        resume_card = self._wait_codex_job(resume_id)
        self.assertEqual(resume_card.get("status"), "completed", resume_card)
        self.assertEqual(resume_card.get("codex_session"), FAKE_SID)
        self.assertEqual(resume_card.get("resumed_from"), job_id)
        log = self.fake_log.read_text(encoding="utf-8")
        self.assertIn(f"resume {FAKE_SID}", log)
        result = self._run_codex(["result", resume_id])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("resumed:", result.stdout)

    def test_codex_resume_without_session_exits_2(self) -> None:
        launched = self._run_codex(
            ["run", "--background", "--label", "no-session"],
            stdin="x",
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)
        job_id = launched.stdout.strip().splitlines()[0]
        card = self._wait_codex_job(job_id)
        meta = self.codex_state / "jobs" / job_id / "meta"
        text = meta.read_text(encoding="utf-8")
        text = text.replace(f"codex_session={FAKE_SID}", "codex_session=—")
        meta.write_text(text, encoding="utf-8")
        # убрать rollout, чтобы discover тоже ничего не нашёл
        sessions = self.codex_home / "sessions"
        if sessions.is_dir():
            for path in sessions.rglob("rollout-*.jsonl"):
                path.unlink()
        proc = self._run_codex(["resume", job_id], stdin="again")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("has no Codex session id", proc.stderr)
        self.assertIn("fall back to a fresh run", proc.stderr)

    def test_dsh_usage_lists_resume(self) -> None:
        proc = self._run_dsh(["-h"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("resume <job-id>", proc.stderr)

    def test_dsh_resume_exits_2_headless_cannot(self) -> None:
        jobs = self.dsh_state / "jobs" / "dsh-test-job"
        jobs.mkdir(parents=True)
        (jobs / "meta").write_text(
            "id=dsh-test-job\nstatus=completed\ncwd=%s\nmode=read-only\n"
            "model=—\nprovider=—\neffort=—\nlabel=x\nsession=—\n"
            "dsh_session=session-aaaa\nstarted_epoch=1\n" % self.workdir,
            encoding="utf-8",
        )
        (jobs / "output.txt").write_text("done\n", encoding="utf-8")
        proc = self._run_dsh(["resume", "dsh-test-job"], stdin="continue")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("headless", proc.stderr)
        # Вывод скрипта переведён на английский (listik-h2cl); проверяем то же
        # требование — подсказку откатиться на новый прогон.
        self.assertIn("fall back to a fresh run", proc.stderr.lower())


if __name__ == "__main__":
    unittest.main()
