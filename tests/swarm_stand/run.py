"""Цикл волн стенда роя: план → диспетч волны → барьер → следующая волна от нового
main, пока задачи не кончатся, план не зациклится, интеграция не покраснеет или не
кончится лимит волн. Настоящий цикл (наблюдатель, лестница реакций) пишет swarm-6
в `listik/`; этот модуль только модель для тестов, `config.ladder` здесь не читается.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import planner
from .barrier import run_barrier
from .dispatch import Dispatcher, RunConfig
from .journal import Journal
from .sandbox import Sandbox, build
from .scenario import Scenario, validate


@dataclass
class RunResult:
    sandbox: Sandbox
    dispatcher: Dispatcher
    journal: Journal
    waves: list[list[str]]
    merged: list[str]
    unfinished: dict[str, str]
    stopped: str


def run(
    scenario: Scenario,
    root: Path,
    config: RunConfig | None = None,
    *,
    max_waves: int = 20,
    integration_command: list[str] | None = None,
) -> RunResult:
    validate(scenario)
    root = Path(root)
    sandbox = build(root / "repo", scenario)
    journal = Journal()
    if config is None:
        config = RunConfig()
    dispatcher = Dispatcher(sandbox, scenario, config, journal, run_dir=root / "run")

    merged: list[str] = []
    waves_run: list[list[str]] = []
    reported_unroutable: set[str] = set()
    last_plan: dict | None = None
    stopped: str | None = None

    wave = 0
    while wave < max_waves:
        done_set = set(merged)
        failed_set = {tid for tid, s in dispatcher.states.items() if s.status == "failed"}
        plan = planner.waves(scenario, done=done_set, failed=failed_set)
        last_plan = plan

        if plan["cycles"]:
            stopped = "cycle"
            break

        for task_id in plan["unroutable"]:
            if task_id not in reported_unroutable:
                journal.add("needs_owner", task=task_id, reason="no_route")
                reported_unroutable.add(task_id)

        current = plan["waves"][0] if plan["waves"] else []
        if not current:
            stopped = "no_ready_tasks"
            break

        base = sandbox.head("main")
        journal.add("wave_started", wave=wave, tasks=current, base=base)
        dispatcher.dispatch(current, base=base)
        dispatcher.wait(deadline=120)

        # место для наблюдателя и лестницы реакций (порция e); config.ladder здесь
        # не читается.

        result = run_barrier(
            sandbox,
            scenario,
            dispatcher,
            journal,
            wave=wave,
            task_ids=current,
            integration_command=integration_command,
        )
        merged.extend(result.merged)
        waves_run.append(current)

        if result.integration is False:
            stopped = "integration_red"
            break

        wave += 1
    else:
        stopped = "max_waves"

    unfinished: dict[str, str] = {}
    merged_set = set(merged)
    for task in scenario.tasks:
        task_id = task.id
        if task_id in merged_set:
            continue
        state = dispatcher.states[task_id]
        if state.status == "failed":
            unfinished[task_id] = state.fail_reason
        elif last_plan is not None and task_id in last_plan["unroutable"]:
            unfinished[task_id] = "unroutable"
        elif last_plan is not None and task_id in last_plan["blocked"]:
            unfinished[task_id] = f"blocked:{last_plan['blocked'][task_id]}"
        else:
            unfinished[task_id] = "not_started"

    return RunResult(
        sandbox=sandbox,
        dispatcher=dispatcher,
        journal=journal,
        waves=waves_run,
        merged=merged,
        unfinished=unfinished,
        stopped=stopped,
    )
