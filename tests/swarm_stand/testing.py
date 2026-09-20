"""Общие утилиты для тестов стенда роя."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .sandbox import MAIN_BRANCH, Sandbox

_MIN_GIT_VERSION = (2, 38)


def _git_version() -> tuple[int, ...]:
    out = subprocess.run(
        ["git", "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    version_str = out.split()[2]
    parts = []
    for chunk in version_str.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def require_git(testcase_or_class) -> None:
    version = _git_version()
    if version < _MIN_GIT_VERSION:
        raise unittest.SkipTest(
            "стенд требует git >= 2.38: merge-tree --write-tree"
        )


def stand_tmpdir(testcase) -> Path:
    path = Path(tempfile.mkdtemp(prefix="swarm-stand-"))

    def _cleanup() -> None:
        import os

        if os.environ.get("SWARM_STAND_KEEP") == "1":
            print(f"SWARM_STAND_KEEP=1: {path}", file=sys.stderr)
            return
        shutil.rmtree(path, ignore_errors=True)

    testcase.addCleanup(_cleanup)
    return path


def make_worktree(sandbox: Sandbox, task_id: str, *, branch: str, path: Path) -> Path:
    sandbox.git(
        "worktree", "add", "-b", branch, str(path), MAIN_BRANCH, cwd=sandbox.root
    )
    return Path(path)
