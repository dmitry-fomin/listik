"""codex-run.sh --write в git worktree: --add-dir на git-dir и objects/refs/logs (listik-51pz);
в основной копии — objects/refs/logs и файлы index/HEAD/COMMIT_EDITMSG с lock-файлами (listik-lon8).

Настоящий codex не вызывается: CODEX_BIN — фейк, который пишет argv по элементу на строку,
блок на вызов. Git — настоящий, во временном каталоге.
"""
from __future__ import annotations

import os
import pathlib
import re
import shutil
import stat
import subprocess
import tempfile
import time
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
CODEX_SH = REPO / "plugins" / "codex" / "scripts" / "codex-run.sh"
FAKE_SID = "11111111-2222-3333-4444-555555555555"

# argv по строке, вызовы разделены '--END--'; для нового прогона пишет rollout,
# чтобы обвязка нашла session id и resume было из чего делать.
FAKE_CODEX = r"""#!/usr/bin/env bash
set -euo pipefail
{ printf '%s\n' "$@"; echo '--END--'; } >> "${FAKE_CODEX_LOG}"
out=""; prev=""; is_resume=0
for a in "$@"; do
  [[ "$prev" == "-o" ]] && out="$a"
  [[ "$a" == "resume" ]] && is_resume=1
  prev="$a"
done
cat >/dev/null
if [[ $is_resume -eq 0 ]]; then
  d="${CODEX_HOME}/sessions/$(date -u +%Y/%m/%d)"; mkdir -p "$d"
  printf '{"timestamp":"2026-01-01T00:00:00Z","type":"session_meta","payload":{"id":"%s","session_id":"%s","cwd":"%s","originator":"codex_exec"}}\n' \
    "__SID__" "__SID__" "$(pwd)" > "$d/rollout-$(date -u +%Y-%m-%dT%H-%M-%S)-__SID__.jsonl"
fi
echo ok > "$out"
""".replace("__SID__", FAKE_SID)


def _git_ok() -> bool:
    git = shutil.which("git")
    if not git:
        return False
    m = re.search(r"(\d+)\.(\d+)", subprocess.run([git, "--version"], capture_output=True, text=True).stdout)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (2, 31)


def git(*args: str, cwd: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


@unittest.skipUnless(_git_ok(), "нужен git >= 2.31")
class CodexWorktreeSandboxTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.main = self.root / "main"
        self.main.mkdir()
        git("init", "-q", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.main)
        self.wt = self.root / "wt"
        git("worktree", "add", "-q", str(self.wt), cwd=self.main)
        self.fake_log = self.root / "fake.log"
        fake = self.root / "fake-codex"
        fake.write_text(FAKE_CODEX, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.env = os.environ.copy()
        self.env.update({
            "CODEX_CLAUDE_STATE_DIR": str(self.root / "state"),
            "CODEX_HOME": str(self.root / "codex-home"),
            "CODEX_BIN": str(fake),
            "FAKE_CODEX_LOG": str(self.fake_log),
        })

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def codex(self, *args: str) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(["bash", str(CODEX_SH), *args], input="go", capture_output=True,
                              text=True, env=self.env, cwd=str(self.root), timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def calls(self) -> list[list[str]]:
        text = self.fake_log.read_text(encoding="utf-8")
        return [c.strip("\n").split("\n") for c in text.split("--END--\n") if c.strip()]

    def wait(self, job_id: str) -> None:
        deadline = time.time() + 10
        while time.time() < deadline:
            out = subprocess.run(["bash", str(CODEX_SH), "status", "--json", job_id],
                                 capture_output=True, text=True, env=self.env).stdout
            if out.strip() and '"running"' not in out:
                return
            time.sleep(0.1)
        self.fail(f"{job_id} не завершилась")

    def expected(self) -> list[str]:
        gd = git("rev-parse", "--path-format=absolute", "--git-dir", cwd=self.wt)
        common = git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=self.wt)
        self.common = common
        return [gd, f"{common}/objects", f"{common}/refs", f"{common}/logs"]

    @staticmethod
    def add_dirs(argv: list[str]) -> list[str]:
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]

    def assert_full_set(self, argv: list[str]) -> None:
        self.assertEqual(self.add_dirs(argv), self.expected())
        for bad in (self.common, f"{self.common}/hooks", f"{self.common}/config"):
            self.assertNotIn(bad, argv)

    def test_write_in_worktree_foreground(self) -> None:
        self.codex("run", "--permission", "write", "--cwd", str(self.wt))
        self.assert_full_set(self.calls()[-1])

    def test_write_in_worktree_background(self) -> None:
        job = self.codex("run", "--write", "--background", "--cwd", str(self.wt)).stdout.split()[0]
        self.wait(job)
        self.assert_full_set(self.calls()[-1])

    def test_read_only_in_worktree(self) -> None:
        self.codex("run", "--permission", "read", "--cwd", str(self.wt))
        self.assertEqual(self.add_dirs(self.calls()[-1]), [])

    def main_expected(self) -> list[str]:
        g = git("rev-parse", "--path-format=absolute", "--git-dir", cwd=self.main)
        self.g = g
        return [f"{g}/objects", f"{g}/refs", f"{g}/logs",
                f"{g}/index", f"{g}/index.lock", f"{g}/HEAD", f"{g}/HEAD.lock", f"{g}/COMMIT_EDITMSG"]

    def assert_main_set(self, argv: list[str]) -> None:
        self.assertEqual(self.add_dirs(argv), self.main_expected())
        for bad in (self.g, f"{self.g}/hooks", f"{self.g}/config"):
            self.assertNotIn(bad, argv)

    def test_write_in_main_copy(self) -> None:
        self.codex("run", "--permission", "write", "--cwd", str(self.main))
        self.assert_main_set(self.calls()[-1])

    def test_write_in_main_copy_subdir(self) -> None:
        sub = self.main / "a" / "b"
        sub.mkdir(parents=True)
        self.codex("run", "--permission", "write", "--cwd", str(sub))
        self.assert_main_set(self.calls()[-1])

    def test_write_in_main_copy_background(self) -> None:
        job = self.codex("run", "--write", "--background", "--cwd", str(self.main)).stdout.split()[0]
        self.wait(job)
        self.assert_main_set(self.calls()[-1])

    def test_read_only_in_main_copy(self) -> None:
        self.codex("run", "--permission", "read", "--cwd", str(self.main))
        argv = self.calls()[-1]
        self.assertEqual(self.add_dirs(argv), [])
        g = git("rev-parse", "--path-format=absolute", "--git-dir", cwd=self.main)
        for bad in (g, f"{g}/hooks", f"{g}/config"):
            self.assertNotIn(bad, argv)

    def test_resume_write_of_read_only_run_in_main_copy(self) -> None:
        job = self.codex("run", "--background", "--permission", "read", "--cwd", str(self.main)).stdout.split()[0]
        self.wait(job)
        self.codex("resume", job, "--write")
        argv = self.calls()[-1]
        self.assertIn("resume", argv)
        self.assert_main_set(argv)
        last_add = max(i for i, a in enumerate(argv) if a == "--add-dir")
        self.assertLess(last_add, argv.index("resume"))

    def test_write_in_submodule(self) -> None:
        subsrc = self.root / "subsrc"
        subsrc.mkdir()
        git("init", "-q", cwd=subsrc)
        git("commit", "-q", "--allow-empty", "-m", "s", cwd=subsrc)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(subsrc), "sub", cwd=self.main)
        git("commit", "-q", "-m", "sub", cwd=self.main)
        self.codex("run", "--permission", "write", "--cwd", str(self.main / "sub"))
        self.assertEqual(self.add_dirs(self.calls()[-1]), [])

    def test_write_outside_git(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        self.codex("run", "--permission", "write", "--cwd", str(plain))
        self.assertEqual(self.add_dirs(self.calls()[-1]), [])

    def run_then_resume(self, run_perm: str, resume_perm: list[str]) -> list[str]:
        job = self.codex("run", "--background", "--permission", run_perm, "--cwd", str(self.wt)).stdout.split()[0]
        self.wait(job)
        self.codex("resume", job, *resume_perm)
        argv = self.calls()[-1]
        self.assertIn("resume", argv)
        return argv

    def test_resume_write_of_read_only_run(self) -> None:
        argv = self.run_then_resume("read", ["--write"])
        self.assert_full_set(argv)
        last_add = max(i for i, a in enumerate(argv) if a == "--add-dir")
        self.assertLess(last_add, argv.index("resume"))

    def test_resume_read_of_write_run(self) -> None:
        argv = self.run_then_resume("write", ["--permission", "read"])
        self.assertEqual(self.add_dirs(argv), [])


if __name__ == "__main__":
    unittest.main()
