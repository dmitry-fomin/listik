"""Два канала обвязки opencode: glm по умолчанию и deepseek (listik-nzhr).

Настоящий `opencode` не вызывается — бинарь подменяется скриптом во временном
каталоге, состояние обвязки тоже временное. Проверяется контракт вызывающего:
короткое имя канала раскрывается в полный идентификатор модели, полный
идентификатор принимается как есть, без `--model` прогон идёт на glm,
продолжение сессии остаётся на её модели и в той же сессии opencode, а `check`
печатает доступность обеих моделей.
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
OPENCODE_SH = REPO / "plugins" / "opencode" / "scripts" / "opencode-run.sh"

GLM = "b.ai/glm-5.3-flash"
DEEPSEEK = "b.ai/deepseek-v4.1-flash"
FAKE_SID = "ses_fake000000001"

# Поддельный opencode: пишет argv в лог и отвечает потоком событий --format
# json, как настоящий. Ответом считается текст последнего сообщения — в нём
# печатаем модель, чтобы её было видно и в stdout прогона.
FAKE_OPENCODE = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${FAKE_OPENCODE_LOG}"

case "${1:-}" in
  --version) echo "0.0.0-fake"; exit 0 ;;
  models)    printf '%s\n' "b.ai/glm-5.3-flash" "b.ai/deepseek-v4.1-flash" "other/model"; exit 0 ;;
  providers) echo "b.ai"; exit 0 ;;
  session)   echo "[]"; exit 0 ;;
esac

model=""
session=""
i=1
while [[ $i -le $# ]]; do
  eval "a=\${$i}"
  if [[ "$a" == "--model" ]]; then i=$((i+1)); eval "model=\${$i}"; fi
  if [[ "$a" == "--session" ]]; then i=$((i+1)); eval "session=\${$i}"; fi
  i=$((i+1))
done
[[ -n "$session" ]] || session="${FAKE_OPENCODE_SID:-ses_fake000000001}"
cat > /dev/null
printf '{"type":"text","part":{"messageID":"msg1","text":"model=%s"}, "sessionID":"%s"}\n' \
  "$model" "$session"
"""


class OpencodeChannelsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.log = self.root / "fake-opencode.log"
        self.bin = self.root / "fake-opencode"
        self.bin.write_text(FAKE_OPENCODE, encoding="utf-8")
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "OPENCODE_CLAUDE_STATE_DIR": str(self.state),
            "OPENCODE_BIN": str(self.bin),
            "FAKE_OPENCODE_LOG": str(self.log),
        })
        env.pop("OPENCODE_DEFAULT_MODEL", None)
        env.update(extra)
        return env

    def _run(self, args: list[str], stdin: str = "", **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(OPENCODE_SH), *args],
            input=stdin,
            capture_output=True,
            text=True,
            env=self._env(**extra),
            cwd=str(self.workdir),
            timeout=30,
        )

    def _log_lines(self) -> list[str]:
        if not self.log.exists():
            return []
        return [l for l in self.log.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _run_lines(self) -> list[str]:
        return [l for l in self._log_lines() if l.startswith("run ")]

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

    # --- выбор модели -------------------------------------------------------
    def test_default_run_goes_to_glm(self) -> None:
        proc = self._run(["run"], stdin="вопрос")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"model={GLM}")
        self.assertIn(f"--model {GLM}", self._run_lines()[0])

    def test_short_channel_name_expands_to_deepseek(self) -> None:
        proc = self._run(["run", "--model", "deepseek"], stdin="вопрос")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"model={DEEPSEEK}")
        self.assertIn(f"--model {DEEPSEEK}", self._run_lines()[0])

    def test_short_channel_name_expands_to_glm(self) -> None:
        proc = self._run(["run", "--model", "glm"], stdin="вопрос")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"--model {GLM}", self._run_lines()[0])

    def test_full_model_id_is_accepted(self) -> None:
        proc = self._run(["run", "--model", DEEPSEEK], stdin="вопрос")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), f"model={DEEPSEEK}")
        self.assertIn(f"--model {DEEPSEEK}", self._run_lines()[0])

    def test_foreign_full_model_id_passes_through(self) -> None:
        proc = self._run(["run", "--model", "other/model"], stdin="вопрос")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--model other/model", self._run_lines()[0])

    def test_unknown_channel_name_exits_2(self) -> None:
        proc = self._run(["run", "--model", "gpt"], stdin="вопрос")
        self.assertEqual(proc.returncode, 2, proc.stdout)
        self.assertIn("неизвестный канал", proc.stderr)
        self.assertIn("deepseek", proc.stderr)
        self.assertEqual(self._run_lines(), [])

    def test_default_model_env_accepts_channel_name(self) -> None:
        proc = self._run(["run"], stdin="вопрос", OPENCODE_DEFAULT_MODEL="deepseek")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"--model {DEEPSEEK}", self._run_lines()[0])

    # --- resume -------------------------------------------------------------
    def test_resume_by_job_id_keeps_deepseek_and_session(self) -> None:
        launched = self._run(
            ["run", "--model", "deepseek", "--background", "--label", "первый"],
            stdin="первый ход",
        )
        self.assertEqual(launched.returncode, 0, launched.stderr)
        job_id = launched.stdout.strip().splitlines()[0]
        card = self._wait_job(job_id)
        self.assertEqual(card.get("status"), "completed", card)
        self.assertEqual(card.get("model"), DEEPSEEK, card)
        self.assertEqual(card.get("opencode_session"), FAKE_SID, card)

        resumed = self._run(["resume", job_id, "--background"], stdin="второй ход")
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        resume_id = resumed.stdout.strip().splitlines()[0]
        resume_card = self._wait_job(resume_id)
        self.assertEqual(resume_card.get("status"), "completed", resume_card)
        self.assertEqual(resume_card.get("model"), DEEPSEEK, resume_card)
        self.assertEqual(resume_card.get("opencode_session"), FAKE_SID, resume_card)
        self.assertEqual(resume_card.get("resumed_from"), job_id, resume_card)
        self.assertIn(f"--session {FAKE_SID}", self._run_lines()[-1])
        self.assertIn(f"--model {DEEPSEEK}", self._run_lines()[-1])

    def test_resume_by_session_name_keeps_deepseek_and_session(self) -> None:
        first = self._run(
            ["run", "--session", "ветка", "--model", "deepseek"],
            stdin="первый ход",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        sessions = json.loads(self._run(["sessions", "--json"]).stdout)
        self.assertEqual([s["model"] for s in sessions], [DEEPSEEK], sessions)
        self.assertEqual(sessions[0]["id"], FAKE_SID, sessions)

        again = self._run(["resume", "ветка"], stdin="второй ход")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout.strip(), f"model={DEEPSEEK}")
        last = self._run_lines()[-1]
        self.assertIn(f"--model {DEEPSEEK}", last)
        self.assertIn(f"--session {FAKE_SID}", last)

    def test_sessions_table_shows_channel_model(self) -> None:
        # Человеческий вывод, а не --json: по нему выбирают, какую сессию
        # продолжать, и канал должен быть виден там же.
        first = self._run(
            ["run", "--session", "ветка3", "--model", "deepseek"],
            stdin="первый ход",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        listed = self._run(["sessions"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        rows = [l for l in listed.stdout.splitlines() if l.strip()]
        self.assertEqual(len(rows), 1, listed.stdout)
        self.assertIn("ветка3", rows[0])
        self.assertIn(FAKE_SID, rows[0])
        self.assertIn(DEEPSEEK, rows[0])

    def test_sessions_table_dashes_unknown_model(self) -> None:
        # Файл имени, записанный обвязкой до появления каналов: модели в нём
        # нет, и колонка должна показать прочерк, а не съехать.
        names = self.state / "sessions"
        names.mkdir(parents=True)
        (names / "старая").write_text(
            "name=старая\nid=%s\ncwd=%s\nupdated=2026-01-01T00:00:00Z\n"
            % (FAKE_SID, self.workdir),
            encoding="utf-8",
        )
        listed = self._run(["sessions"])
        self.assertEqual(listed.returncode, 0, listed.stderr)
        self.assertIn("старая", listed.stdout)
        self.assertIn("—", listed.stdout)
        self.assertNotIn(DEEPSEEK, listed.stdout)

    def test_resume_can_switch_channel_explicitly(self) -> None:
        first = self._run(
            ["run", "--session", "ветка2", "--model", "deepseek"],
            stdin="первый ход",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        again = self._run(["resume", "ветка2", "--model", "glm"], stdin="второй ход")
        self.assertEqual(again.returncode, 0, again.stderr)
        last = self._run_lines()[-1]
        self.assertIn(f"--model {GLM}", last)
        self.assertIn(f"--session {FAKE_SID}", last)

    # --- check --------------------------------------------------------------
    def test_check_reports_both_channels(self) -> None:
        proc = self._run(["check"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(GLM, proc.stdout)
        self.assertIn(DEEPSEEK, proc.stdout)
        self.assertIn("glm →", proc.stdout)
        self.assertIn("deepseek →", proc.stdout)
        self.assertIn("по умолчанию", proc.stdout)

    def test_check_json_lists_both_channels(self) -> None:
        proc = self._run(["check", "--json"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        card = json.loads(proc.stdout)
        channels = card["channels"]
        self.assertEqual([c["name"] for c in channels], ["glm", "deepseek"])
        self.assertEqual([c["model"] for c in channels], [GLM, DEEPSEEK])
        self.assertEqual([c["status"] for c in channels], ["ok", "ok"])
        self.assertEqual([c["default"] for c in channels], ["yes", "no"])
        self.assertEqual(card["model"], GLM)

    def test_check_json_marks_missing_channel(self) -> None:
        # Каталог моделей без deepseek: канал должен пометиться как missing,
        # а не молча сойти за доступный.
        thin = self.root / "thin-opencode"
        thin.write_text(
            FAKE_OPENCODE.replace(
                '"b.ai/glm-5.3-flash" "b.ai/deepseek-v4.1-flash" "other/model"',
                '"b.ai/glm-5.3-flash"',
            ),
            encoding="utf-8",
        )
        thin.chmod(thin.stat().st_mode | stat.S_IEXEC)
        proc = self._run(["check", "--json"], OPENCODE_BIN=str(thin))
        card = json.loads(proc.stdout)
        statuses = {c["name"]: c["status"] for c in card["channels"]}
        self.assertEqual(statuses, {"glm": "ok", "deepseek": "missing"})


if __name__ == "__main__":
    unittest.main()
