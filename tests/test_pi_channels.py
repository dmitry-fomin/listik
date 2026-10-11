"""Каналы моста pi берутся из Listik (listik-46q6, порция b).

Ни настоящий `pi`, ни настоящий `listik` не вызываются: оба подменяются
скриптами во временном каталоге, состояние моста тоже временное. Поддельный
`listik` пишет свой argv в лог и печатает файл из FAKE_LISTIK_OUT с кодом
FAKE_LISTIK_RC; поддельный `pi` пишет argv в лог, отвечает на `--version` и
`--list-models`, а любой другой вызов (в том числе `--mode rpc`) проваливает —
модель прогона видна по его логу.
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
PI_SH = REPO / "plugins" / "pi" / "scripts" / "pi-run.sh"

FAKE_PI = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_PI_LOG}"
case "${1:-}" in
  --version) echo "0.0.0-fake"; exit 0 ;;
  --list-models)
    printf '%s\n' \
      "provider    model" \
      "openrouter  z-ai/glm-5.3-flash" \
      "openrouter  z-ai/glm-9" \
      "deepseek    deepseek-flash" \
      "deepseek    deepseek-9"
    exit 0 ;;
esac
exit 3
"""

FAKE_LISTIK = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "${FAKE_LISTIK_LOG}"
cat "${FAKE_LISTIK_OUT}" 2>/dev/null
exit "${FAKE_LISTIK_RC:-0}"
"""

GLM9 = {"name": "glm", "model": "openrouter/z-ai/glm-9", "default": True}
DS = {"name": "deepseek", "model": "deepseek/deepseek-flash", "default": False}


def _exe(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


class PiChannelsFromListikTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.state = self.root / "state"
        self.workdir = self.root / "work"
        self.workdir.mkdir()
        self.pi_log = self.root / "pi.log"
        self.listik_log = self.root / "listik.log"
        self.listik_out = self.root / "listik.json"
        self.pi = self.root / "fake-pi"
        self.listik = self.root / "fake-listik"
        _exe(self.pi, FAKE_PI)
        _exe(self.listik, FAKE_LISTIK)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _listik(self, channels, *, raw: str | None = None) -> None:
        text = raw if raw is not None else json.dumps({"key": "pi", "channels": channels})
        self.listik_out.write_text(text, encoding="utf-8")

    def _env(self, **extra: str) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("PI_CLAUDE_DEFAULT_MODEL", None)
        env.update({
            "PI_CLAUDE_STATE_DIR": str(self.state),
            "PI_CLAUDE_BIN": str(self.pi),
            "FAKE_PI_LOG": str(self.pi_log),
            "LISTIK_BIN": str(self.listik),
            "FAKE_LISTIK_LOG": str(self.listik_log),
            "FAKE_LISTIK_OUT": str(self.listik_out),
        })
        env.update(extra)
        return env

    def _run(self, args: list[str], stdin: str = "", **extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(PI_SH), *args], input=stdin, capture_output=True, text=True,
            env=self._env(**extra), cwd=str(self.workdir), timeout=60,
        )

    def _check(self, **extra: str) -> dict:
        proc = self._run(["check", "--json", "--no-probe"], **extra)
        return json.loads(proc.stdout)

    @staticmethod
    def _lines(path: pathlib.Path) -> list[str]:
        if not path.exists():
            return []
        return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def _rpc_lines(self) -> list[str]:
        return [l for l in self._lines(self.pi_log) if "--mode rpc" in l]

    def _wait_job(self, job_id: str, timeout: float = 30.0) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            proc = self._run(["status", "--json", job_id])
            if proc.returncode == 0 and proc.stdout.strip():
                last = json.loads(proc.stdout)
                if last.get("status") != "running":
                    return last
            time.sleep(0.2)
        self.fail(f"задача {job_id} не завершилась: {last}")

    # --- 1. таблица из Listik ------------------------------------------------
    def test_check_takes_channels_from_listik(self) -> None:
        self._listik([GLM9, DS])
        card = self._check()
        glm = {c["name"]: c for c in card["channels"]}["glm"]
        self.assertEqual((glm["provider"], glm["model"]), ("openrouter", "z-ai/glm-9"))
        self.assertEqual(card["channels_source"], "listik")
        self.assertEqual(card["channels_source_error"], "")
        self.assertEqual(self._lines(self.listik_log), ["harnesses pi --json"])

        text = self._run(["check", "--no-probe"]).stdout
        self.assertIn("openrouter/z-ai/glm-9", text)
        line = [l for l in text.splitlines() if l.startswith("channels from:")]
        self.assertEqual(len(line), 1, text)
        self.assertIn("listik", line[0])

    # --- 2. запасной путь ------------------------------------------------------
    def test_fallback_cases(self) -> None:
        cases = {
            "no binary": ({"LISTIK_BIN": str(self.root / "missing")}, None, None),
            "exit 1": ({"FAKE_LISTIK_RC": "1"}, [GLM9, DS], None),
            "not json": ({}, None, "not json at all"),
            "empty": ({}, [], None),
            "no model": ({}, [{"name": "glm"}], None),
            "no slash": ({}, [{"name": "glm", "model": "glm-9"}], None),
        }
        for label, (env, channels, raw) in cases.items():
            with self.subTest(label):
                self._listik(channels, raw=raw)
                card = self._check(**env)
                self.assertEqual(card["channels_source"], "fallback", card)
                self.assertTrue(card["channels_source_error"], card)
                got = {c["name"]: (c["provider"], c["model"]) for c in card["channels"]}
                self.assertEqual(got, {
                    "glm": ("openrouter", "z-ai/glm-5.3-flash"),
                    "deepseek": ("deepseek", "deepseek-flash"),
                })
                if label == "no slash":
                    self.assertIn("not a provider/model id", card["channels_source_error"])

    # --- 3. канал по умолчанию -------------------------------------------------
    def test_default_channel(self) -> None:
        self._listik([dict(GLM9, default=False), dict(DS, default=True)])
        self.assertEqual(self._check()["default_channel"], "deepseek")

        self._listik([dict(DS, default=False), dict(GLM9, default=False)])
        self.assertEqual(self._check()["default_channel"], "deepseek")

        self._listik([dict(GLM9, default=False), dict(DS, default=True)])
        self.assertEqual(self._check(PI_CLAUDE_DEFAULT_MODEL="glm")["default_channel"], "glm")

    # --- 4. канал удалён в Listik ------------------------------------------------
    def test_run_channel_missing_in_listik(self) -> None:
        self._listik([dict(DS, default=True)])
        proc = self._run(["run", "--channel", "glm"], stdin="вопрос")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("unknown channel 'glm'", proc.stderr)
        self.assertIn("deepseek", proc.stderr)
        self.assertEqual(self._rpc_lines(), [])

    # --- 5. прогон на модели из Listik --------------------------------------------
    def test_run_uses_listik_model(self) -> None:
        self._listik([GLM9, DS])
        self._run(["run", "--channel", "glm"], stdin="вопрос")
        rpc = self._rpc_lines()
        self.assertEqual(len(rpc), 1, self._lines(self.pi_log))
        self.assertIn("--provider openrouter", rpc[0])
        self.assertIn("--model z-ai/glm-9", rpc[0])

    # --- 6. откат в run ------------------------------------------------------------
    def test_run_fallback_warns_once(self) -> None:
        proc = self._run(["run", "--channel", "glm"], stdin="вопрос",
                         LISTIK_BIN=str(self.root / "missing"))
        warn = [l for l in proc.stderr.splitlines() if l.startswith("channels: built-in fallback - ")]
        self.assertEqual(len(warn), 1, proc.stderr)
        self.assertEqual(proc.stderr.count("channels:"), 1, proc.stderr)
        rpc = self._rpc_lines()
        self.assertEqual(len(rpc), 1, self._lines(self.pi_log))
        self.assertIn("--model z-ai/glm-5.3-flash", rpc[0])

    # --- 7. resume остаётся на модели сессии ---------------------------------------
    def _session(self, name: str = "s1") -> None:
        sess_file = self.root / "pi-session.jsonl"
        sess_file.write_text("{}\n", encoding="utf-8")
        names = self.state / "sessions"
        names.mkdir(parents=True, exist_ok=True)
        (names / name).write_text(
            f"name={name}\nid=\nfile={sess_file}\ncwd={self.workdir}\n"
            "model=openrouter/z-ai/glm-5.3-flash\nthinking=\nupdated=2026-01-01T00:00:00Z\n",
            encoding="utf-8")

    def test_resume_keeps_session_model(self) -> None:
        self._session()
        self._listik([GLM9, DS])
        self._run(["resume", "s1"], stdin="ещё")
        rpc = self._rpc_lines()
        self.assertEqual(len(rpc), 1, self._lines(self.pi_log))
        self.assertIn("--model z-ai/glm-5.3-flash", rpc[0])
        self.assertEqual(self._lines(self.listik_log), ["harnesses pi --json"])

    # --- 8. сколько раз мост зовёт Listik -------------------------------------------
    def test_listik_call_count(self) -> None:
        self._listik([GLM9, DS])
        launched = self._run(["run", "--background", "--channel", "glm"], stdin="вопрос")
        self.assertEqual(launched.returncode, 0, launched.stderr)
        job_id = launched.stdout.strip().splitlines()[0]
        self.assertEqual(len(self._lines(self.listik_log)), 1)

        self.listik_log.unlink()
        self._wait_job(job_id)
        self._run(["status"])
        self._run(["result", job_id])
        self._run(["logs", job_id])
        self.assertEqual(self._lines(self.listik_log), [])

        self._session()
        for args in (["check", "--no-probe"], ["run", "--channel", "glm"], ["resume", "s1"]):
            with self.subTest(args[0]):
                if self.listik_log.exists():
                    self.listik_log.unlink()
                self._run(args, stdin="вопрос")
                self.assertEqual(self._lines(self.listik_log), ["harnesses pi --json"])


if __name__ == "__main__":
    unittest.main()
