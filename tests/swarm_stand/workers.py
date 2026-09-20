"""Запуск профилей поддельных воркеров стенда роя."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .sandbox import Sandbox
from .scenario import Task, target_of

PROFILES_DIR = Path(__file__).parent / "profiles"

_REQUIRED_REPORT_FIELDS = ("task_id", "dispatch_id", "generation", "status", "head")


def script_for(profile: str) -> Path:
    return PROFILES_DIR / f"{profile}.sh"


def worker_env(
    sandbox: Sandbox,
    task: Task,
    *,
    dispatch_id: str,
    generation: int,
    worktree: Path | str,
    branch: str,
    report_path: Path | str,
    gate_start: Path | str | None = None,
    gate_finish: Path | str | None = None,
    gate_timeout: int | None = None,
) -> dict[str, str]:
    env = sandbox.env()
    env["SWARM_STAND_TASK_ID"] = task.id
    env["SWARM_STAND_PROFILE"] = task.profile
    env["SWARM_STAND_DISPATCH_ID"] = dispatch_id
    env["SWARM_STAND_GENERATION"] = str(generation)
    env["SWARM_STAND_WORKTREE"] = str(worktree)
    env["SWARM_STAND_BRANCH"] = branch
    env["SWARM_STAND_TARGET"] = target_of(task)
    env["SWARM_STAND_REPORT_PATH"] = str(report_path)
    if task.foreign:
        env["SWARM_STAND_FOREIGN"] = task.foreign
    if gate_start is not None:
        env["SWARM_STAND_GATE_START"] = str(gate_start)
    if gate_finish is not None:
        env["SWARM_STAND_GATE_FINISH"] = str(gate_finish)
    if gate_timeout is not None:
        env["SWARM_STAND_GATE_TIMEOUT"] = str(gate_timeout)
    return env


def launch(sandbox: Sandbox, task: Task, *, worktree: Path | str, env: dict[str, str]) -> subprocess.Popen:
    report_path = Path(env["SWARM_STAND_REPORT_PATH"])
    log_path = report_path.with_suffix(".log")
    with open(log_path, "wb") as log_file:
        return subprocess.Popen(
            ["sh", str(script_for(task.profile))],
            cwd=str(worktree),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def read_report(report_path: Path | str) -> dict | None:
    path = Path(report_path)
    if not path.exists():
        return None
    text = path.read_text().strip()
    if not text:
        raise ValueError(f"отчёт {path} пуст")
    data = json.loads(text)
    for field_name in _REQUIRED_REPORT_FIELDS:
        if field_name not in data:
            raise ValueError(f"в отчёте {path} нет поля {field_name!r}")
    if not isinstance(data["generation"], int):
        raise ValueError(f"generation в отчёте {path} не int")
    return data
