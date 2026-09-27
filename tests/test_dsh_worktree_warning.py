"""dsh-run.sh --write: предупреждение, когда git-dir вне рабочего каталога (listik-apci).

Настоящий dsh не вызывается: DSH_BIN — фейк, который пишет DSH_PERMISSION_MODE и свой pwd
в лог и печатает «ok». Git — настоящий, во временном каталоге; два сценария — на шиме git.
"""
from __future__ import annotations

import json
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
DSH_SH = REPO / "plugins" / "dsh" / "scripts" / "dsh-run.sh"

# Если задан FAKE_DSH_FLAG — ждёт появления этого файла (потолок 20 с), потом отвечает.
FAKE_DSH = r"""#!/usr/bin/env bash
if [[ -n "${FAKE_DSH_FLAG:-}" ]]; then
  for _ in $(seq 1 200); do [[ -e "$FAKE_DSH_FLAG" ]] && break; sleep 0.1; done
fi
printf '%s %s\n' "${DSH_PERMISSION_MODE:-}" "$(pwd)" >> "$FAKE_DSH_LOG"
echo ok
"""

FAKE_GIT = r"""#!/usr/bin/env bash
printf '%s\n%s\n' "$FAKE_GIT_DIR" "$FAKE_GIT_COMMON"
"""


def _git_ok() -> bool:
    git = shutil.which("git")
    if not git:
        return False
    m = re.search(r"(\d+)\.(\d+)", subprocess.run([git, "--version"], capture_output=True, text=True).stdout)
    return bool(m) and (int(m.group(1)), int(m.group(2))) >= (2, 31)


def git(*args: str, cwd: pathlib.Path) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=str(cwd), check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def _exe(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def warnings(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.startswith("warning:")]


@unittest.skipUnless(_git_ok(), "нужен git >= 2.31")
class DshWorktreeWarningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = pathlib.Path(self.tmp.name)
        self.main = self.root / "main"
        self.main.mkdir()
        git("init", "-q", cwd=self.main)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=self.main)
        self.wt = self.root / "wt"
        git("worktree", "add", "-q", str(self.wt), cwd=self.main)
        self.log = self.root / "fake.log"
        fake = self.root / "fake-dsh"
        _exe(fake, FAKE_DSH)
        self.env = os.environ.copy()
        self.env.update({
            "DSH_BIN": str(fake),
            "DSH_CLAUDE_STATE_DIR": str(self.root / "state"),
            "DSH_HOME": str(self.root / "dsh-home"),
            "FAKE_DSH_LOG": str(self.log),
        })
        self.env.pop("FAKE_DSH_FLAG", None)

    def dsh(self, *args: str, cwd: pathlib.Path | None = None) -> subprocess.CompletedProcess[str]:
        argv = ["bash", str(DSH_SH), *args]
        if cwd is not None:
            argv[3:3] = ["--cwd", str(cwd)]
        return subprocess.run(argv, input="do it", capture_output=True, text=True,
                              env=self.env, cwd=str(self.root), timeout=30)

    def run_(self, cwd: pathlib.Path, *flags: str) -> subprocess.CompletedProcess[str]:
        return self.dsh("run", *flags, cwd=cwd)

    def wt_gitdir(self) -> pathlib.Path:
        return (self.main / ".git" / "worktrees" / "wt").resolve()

    def assert_warns(self, proc: subprocess.CompletedProcess[str], path: pathlib.Path) -> None:
        w = warnings(proc.stderr)
        self.assertEqual(len(w), 1, proc.stderr)
        self.assertIn(str(path.resolve()), w[0])
        self.assertNotIn("warning:", proc.stdout)

    def assert_quiet(self, proc: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(warnings(proc.stderr), [], proc.stderr)
        self.assertEqual(proc.stdout, "ok\n")

    def test_write_in_worktree(self) -> None:
        proc = self.run_(self.wt, "--permission", "write")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_warns(proc, self.wt_gitdir())
        self.assertEqual(proc.stdout, "ok\n")
        self.assertEqual(self.log.read_text().splitlines(), [f"workspace-write {self.wt}"])

    def test_write_alias_in_worktree(self) -> None:
        self.assert_warns(self.run_(self.wt, "--write"), self.wt_gitdir())

    def test_write_in_subdir(self) -> None:
        sub = self.main / "sub"
        sub.mkdir()
        self.assert_warns(self.run_(sub, "--permission", "write"), self.main / ".git")

    def test_write_in_submodule(self) -> None:
        subsrc = self.root / "subsrc"
        subsrc.mkdir()
        git("init", "-q", cwd=subsrc)
        git("commit", "-q", "--allow-empty", "-m", "s", cwd=subsrc)
        git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(subsrc), "sub", cwd=self.main)
        git("commit", "-q", "-m", "sub", cwd=self.main)
        self.assert_warns(self.run_(self.main / "sub", "--permission", "write"),
                          self.main / ".git" / "modules" / "sub")

    def test_write_in_main_root_quiet(self) -> None:
        self.assert_quiet(self.run_(self.main, "--permission", "write"))

    def test_read_bash_default_quiet(self) -> None:
        for flags in (["--permission", "read"], ["--permission", "bash"], []):
            with self.subTest(flags=flags):
                self.assert_quiet(self.run_(self.wt, *flags))
        self.assertTrue(all(l.startswith("read-only ") for l in self.log.read_text().splitlines()))

    def test_write_outside_git_quiet(self) -> None:
        plain = self.root / "plain"
        plain.mkdir()
        self.assert_quiet(self.run_(plain, "--permission", "write"))

    def test_git_failing_quiet(self) -> None:
        shim = self.root / "shim"
        shim.mkdir()
        _exe(shim / "git", "#!/usr/bin/env bash\necho 'fatal: nope' >&2\nexit 1\n")
        self.env["PATH"] = f"{shim}{os.pathsep}{self.env['PATH']}"
        proc = self.run_(self.wt, "--permission", "write")
        self.assert_quiet(proc)
        self.assertNotIn("fatal", proc.stderr)

    def test_background_in_worktree(self) -> None:
        proc = self.run_(self.wt, "--permission", "write", "--background")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_warns(proc, self.wt_gitdir())
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertTrue(lines[0].startswith("dsh-"), proc.stdout)
        deadline = time.time() + 20
        status = None
        while time.time() < deadline:
            st = self.dsh("status", "--json", lines[0])
            if st.returncode == 0 and st.stdout.strip():
                status = json.loads(st.stdout).get("status")
                if status not in ("running", "queued"):
                    break
            time.sleep(0.1)
        self.assertEqual(status, "completed")

    def test_warning_before_dsh_exits(self) -> None:
        flag = self.root / "flag"
        self.env["FAKE_DSH_FLAG"] = str(flag)
        err_path = self.root / "stderr.txt"
        with open(err_path, "w") as err:
            p = subprocess.Popen(["bash", str(DSH_SH), "run", "--cwd", str(self.wt), "--permission", "write"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err,
                                 env=self.env, cwd=str(self.root), text=True)
            try:
                p.stdin.write("do it")
                p.stdin.close()
                deadline = time.time() + 15
                seen = False
                while time.time() < deadline:
                    if warnings(err_path.read_text()):
                        seen = True
                        break
                    time.sleep(0.05)
                self.assertTrue(seen, "warning не появился, пока dsh ждал")
                self.assertFalse(self.log.exists(), "dsh уже вышел")
            finally:
                flag.touch()
                out, _ = p.communicate(timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(out, "ok\n")

    def _shim_git(self, git_dir: pathlib.Path, common: pathlib.Path) -> None:
        shim = self.root / "shim"
        shim.mkdir()
        _exe(shim / "git", FAKE_GIT)
        self.env["PATH"] = f"{shim}{os.pathsep}{self.env['PATH']}"
        self.env["FAKE_GIT_DIR"] = str(git_dir)
        self.env["FAKE_GIT_COMMON"] = str(common)

    def test_shim_common_dir_outside(self) -> None:
        repo, other = self.root / "repo", self.root / "other"
        (repo / ".git").mkdir(parents=True)
        (other / ".git").mkdir(parents=True)
        self._shim_git(repo / ".git", other / ".git")
        self.assert_warns(self.run_(repo, "--permission", "write"), other / ".git")

    def test_shim_component_boundary(self) -> None:
        repo, repo2 = self.root / "repo", self.root / "repo2"
        repo.mkdir()
        (repo2 / ".git").mkdir(parents=True)
        self._shim_git(repo2 / ".git", repo2 / ".git")
        self.assert_warns(self.run_(repo, "--permission", "write"), repo2 / ".git")


if __name__ == "__main__":
    unittest.main()
