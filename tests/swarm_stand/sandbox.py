"""Одноразовый git-репозиторий-песочница для стенда роя."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .scenario import Scenario

MAIN_BRANCH = "main"
WORKTREES_DIR = ".worktrees"

_MODULE_NAMES = ("alpha", "beta", "gamma", "delta", "epsilon")


def _module_source(name: str) -> str:
    return (
        f'"""{name} — модуль песочницы стенда."""\n'
        "\n\n"
        "def value():\n"
        f'    return "{name}-v1"\n'
        "\n\n"
        "def describe():\n"
        '    return "module " + value()\n'
        "\n\n"
        "def size():\n"
        "    return len(value())\n"
        "\n\n"
        f"# end of {name}\n"
    )


_TEST_PKG_SOURCE = """import unittest

from pkg import alpha, beta, gamma, delta, epsilon


class PkgTests(unittest.TestCase):
    def test_values(self):
        for name, mod in (("alpha", alpha), ("beta", beta), ("gamma", gamma),
                          ("delta", delta), ("epsilon", epsilon)):
            with self.subTest(name):
                v = mod.value()
                self.assertIsInstance(v, str)
                self.assertTrue(v.startswith(name + "-"), v)
                self.assertEqual(mod.size(), len(v))
"""


def _build_default_files() -> dict[str, str]:
    files: dict[str, str] = {"pkg/__init__.py": "", "tests/__init__.py": ""}
    for name in _MODULE_NAMES:
        files[f"pkg/{name}.py"] = _module_source(name)
    files["tests/test_pkg.py"] = _TEST_PKG_SOURCE
    return files


DEFAULT_FILES: dict[str, str] = _build_default_files()


@dataclass
class Sandbox:
    root: Path
    _test_command: list[str] | None = None

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return env

    def git(
        self,
        *args: str,
        cwd: Path | str | None = None,
        check: bool = True,
        input: str | bytes | None = None,
    ) -> subprocess.CompletedProcess:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd is not None else str(self.root),
            env=self.env(),
            capture_output=True,
            text=True,
            input=input,
        )
        if check and result.returncode != 0:
            raise RuntimeError(
                f"git {' '.join(args)} failed ({result.returncode}): {result.stderr}"
            )
        return result

    def git_out(self, *args: str, cwd: Path | str | None = None) -> str:
        return self.git(*args, cwd=cwd).stdout.strip()

    def head(self, ref: str = "HEAD", cwd: Path | str | None = None) -> str:
        return self.git_out("rev-parse", ref, cwd=cwd)

    def test_command(self) -> list[str]:
        if self._test_command is not None:
            return self._test_command
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]


def build(root: Path, scenario: Scenario) -> Sandbox:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)

    sandbox = Sandbox(root=root, _test_command=scenario.test_command)

    sandbox.git("init", "-q", "-b", MAIN_BRANCH, cwd=root)
    sandbox.git("config", "user.name", "Swarm Stand", cwd=root)
    sandbox.git("config", "user.email", "swarm-stand@example.invalid", cwd=root)
    sandbox.git("config", "commit.gpgsign", "false", cwd=root)
    sandbox.git("config", "merge.conflictStyle", "merge", cwd=root)
    sandbox.git("config", "core.autocrlf", "false", cwd=root)

    files = scenario.files if scenario.files is not None else DEFAULT_FILES
    for relpath, content in files.items():
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    (root / ".gitignore").write_text(".worktrees/\n__pycache__/\n")

    sandbox.git("add", "-A", cwd=root)
    sandbox.git("commit", "-q", "-m", "песочница", cwd=root)

    (root / WORKTREES_DIR).mkdir(parents=True, exist_ok=True)

    return sandbox
