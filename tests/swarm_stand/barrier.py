"""Барьер волны стенда роя: ребейз каждой задачи на текущий main, тесты в её дереве,
слияние `--ff-only`, интеграционные тесты на main после всей волны. Настоящий барьер
пишет swarm-6 (не в `listik/`); этот модуль только модель для тестов.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from . import arbiter
from .dispatch import Dispatcher
from .journal import Journal
from .sandbox import Sandbox
from .scenario import Scenario

_MAX_REBASE_ITERATIONS = 10
_OUTPUT_TAIL = 2000


@dataclass
class BarrierResult:
    merged: list[str]
    failed: list[str]
    integration: bool | None


def _decode_tail(stdout: bytes | None, stderr: bytes | None) -> str:
    data = (stdout or b"") + (stderr or b"")
    return data.decode("utf-8", errors="replace")[-_OUTPUT_TAIL:]


def merge_order(dispatcher: Dispatcher, task_ids: list[str]) -> list[str]:
    done = [tid for tid in task_ids if dispatcher.states[tid].status == "done"]

    def key(tid: str) -> tuple[float, int]:
        state = dispatcher.states[tid]
        tick = state.first_change_tick if state.first_change_tick is not None else float("inf")
        return (tick, state.dispatch_index)

    return sorted(done, key=key)


def merge_task(
    sandbox: Sandbox,
    scenario: Scenario,
    dispatcher: Dispatcher,
    journal: Journal,
    task_id: str,
    *,
    wave: int,
) -> bool:
    state = dispatcher.states[task_id]
    tree = Path(state.tree)
    branch = state.branch

    dirty = sandbox.git("status", "--porcelain", cwd=tree).stdout
    if dirty.strip():
        dispatcher.mark_failed(task_id, "dirty_tree")
        return False

    rebase = sandbox.git("rebase", "main", cwd=tree, check=False)
    if rebase.returncode == 0:
        journal.add("rebased", task=task_id, wave=wave, conflicts=[])
    else:
        all_conflicts: list[str] = []
        resolved = False
        for _ in range(_MAX_REBASE_ITERATIONS):
            files = [
                f
                for f in sandbox.git_out(
                    "diff", "--name-only", "--diff-filter=U", cwd=tree
                ).splitlines()
                if f
            ]

            ok = bool(files)
            if ok:
                for f in files:
                    if not arbiter.resolve_file(tree / f):
                        ok = False
                        break

            if not ok:
                sandbox.git("rebase", "--abort", cwd=tree, check=False)
                dispatcher.mark_failed(task_id, "merge_conflict", files=files)
                return False

            all_conflicts.extend(files)
            sandbox.git("add", *files, cwd=tree)
            cont = sandbox.git(
                "-c", "core.editor=true", "rebase", "--continue", cwd=tree, check=False
            )
            if cont.returncode == 0:
                journal.add("rebased", task=task_id, wave=wave, conflicts=all_conflicts)
                journal.add("conflict_resolved", task=task_id, wave=wave, files=files)
                resolved = True
                break

        if not resolved:
            sandbox.git("rebase", "--abort", cwd=tree, check=False)
            dispatcher.mark_failed(task_id, "merge_conflict", files=all_conflicts)
            return False

    tests_result = subprocess.run(
        sandbox.test_command(), cwd=tree, env=sandbox.env(), capture_output=True
    )
    if tests_result.returncode != 0:
        output = _decode_tail(tests_result.stdout, tests_result.stderr)
        journal.add("tests_red", task=task_id, wave=wave, output=output)
        dispatcher.mark_failed(task_id, "tests_red")
        return False
    journal.add("tests_green", task=task_id, wave=wave)

    merge = sandbox.git("merge", "--ff-only", branch, cwd=sandbox.root, check=False)
    if merge.returncode == 0:
        sha = sandbox.head("main")
        dispatcher.mark_merged(task_id, sha=sha)
        journal.add("merged", task=task_id, wave=wave, sha=sha, branch=branch)
        return True

    journal.add("merge_failed", task=task_id, wave=wave, error=merge.stderr)
    dispatcher.mark_failed(task_id, "merge_failed")
    return False


def run_barrier(
    sandbox: Sandbox,
    scenario: Scenario,
    dispatcher: Dispatcher,
    journal: Journal,
    *,
    wave: int,
    task_ids: list[str],
    integration_command: list[str] | None = None,
    held: Sequence[str] = (),
) -> BarrierResult:
    order = merge_order(dispatcher, task_ids)

    merged: list[str] = []
    failed: list[str] = []
    for task_id in order:
        if merge_task(sandbox, scenario, dispatcher, journal, task_id, wave=wave):
            merged.append(task_id)
        else:
            failed.append(task_id)

    if held:
        for task_id in held:
            dispatcher.states[task_id].status = "pending"
            base = sandbox.head("main")
            dispatcher.dispatch([task_id], base=base)
            generation = dispatcher.states[task_id].generation
            journal.add("resumed", wave=wave, task=task_id, generation=generation, base=base)

            if dispatcher.config.gates == "manual":
                dispatcher.open_gate(task_id, "start")
                dispatcher.open_gate(task_id, "finish")
            elif dispatcher.config.gates == "sequential":
                # затвор поколения ещё не создан — при исчерпанном курсоре очередь
                # откроет его и сама; повторное создание файла безвредно.
                dispatcher.open_gate(task_id, "start")
            # "none" — затворов нет, ничего не открывать.

        dispatcher.wait(deadline=120)

        for task_id in held:
            if dispatcher.states[task_id].status == "done":
                if merge_task(sandbox, scenario, dispatcher, journal, task_id, wave=wave):
                    merged.append(task_id)
                else:
                    failed.append(task_id)
            else:
                failed.append(task_id)

    integration: bool | None
    if not merged:
        integration = None
    else:
        command = integration_command if integration_command is not None else sandbox.test_command()
        result = subprocess.run(command, cwd=sandbox.root, env=sandbox.env(), capture_output=True)
        if result.returncode == 0:
            integration = True
            journal.add("integration_green", wave=wave)
        else:
            integration = False
            output = _decode_tail(result.stdout, result.stderr)
            journal.add("integration_red", wave=wave, output=output)
            journal.add("needs_owner", wave=wave, reason="integration_red")

    if integration is not False:
        for task_id in merged:
            generation = dispatcher.states[task_id].generation
            for g in range(generation + 1):
                dispatcher.remove_tree(task_id, generation=g)
            journal.add("cleaned", task=task_id, wave=wave)

    journal.add("wave_finished", wave=wave, merged=merged, failed=failed, integration=integration)

    return BarrierResult(merged=merged, failed=failed, integration=integration)
