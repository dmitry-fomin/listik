"""Диспетчер стенда роя: заводит деревья/ветки по поколениям, запускает профили,
проверяет отчёты по git, а не на слово, гоняет fencing (снятие полномочий по
поколению) и таймауты. Модель механики роя только для тестов — не диспетчер
`listik-swarm` и не fencing из `listik/launcher.py` (их пишут swarm-3/swarm-4)."""

from __future__ import annotations

import os
import signal
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .journal import Journal
from .sandbox import MAIN_BRANCH, Sandbox
from .scenario import Scenario, scope_covers, task_by_id
from .workers import launch, read_report, worker_env


@dataclass
class RunConfig:
    timeout: float = 5.0
    poll: float = 0.05
    kill_on_timeout: bool = True
    retries_on_timeout: int = 1
    gates: str = "none"
    gate_timeout: float = 30.0
    ladder: bool = True  # читается в порции e; здесь не используется


@dataclass
class TaskState:
    task_id: str
    status: str
    generation: int = -1
    dispatch_id: str | None = None
    dispatch_index: int | None = None
    tree: str | None = None
    branch: str | None = None
    base: str | None = None
    first_change_tick: int | None = None
    head: str | None = None
    fail_reason: str | None = None
    timeouts: int = 0
    # служебные поля запуска — не часть публичного контракта состояния задачи
    proc: object = field(default=None, repr=False)
    deadline: float | None = field(default=None, repr=False)
    report_path: Path | None = field(default=None, repr=False)
    gate_start: Path | None = field(default=None, repr=False)
    gate_finish: Path | None = field(default=None, repr=False)


def check_report(state: TaskState, report: dict) -> str | None:
    if report["generation"] != state.generation:
        return "stale_generation"
    if report["dispatch_id"] != state.dispatch_id:
        return "unknown_dispatch"
    return None


class Dispatcher:
    def __init__(
        self,
        sandbox: Sandbox,
        scenario: Scenario,
        config: RunConfig,
        journal: Journal,
        run_dir: Path | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.scenario = scenario
        self.config = config
        self.journal = journal
        self.run_dir = Path(run_dir) if run_dir is not None else sandbox.root.parent / "swarm-stand-run"
        self.states: dict[str, TaskState] = {
            task.id: TaskState(task_id=task.id, status="pending") for task in scenario.tasks
        }
        self.tick_no = 0
        self._revoked: list[tuple] = []
        self._gate_queue: list[tuple[str, int]] = []
        self._gate_cursor = 0
        self._gate_opened_at: dict[tuple[str, int], float] = {}

    # -- пути ----------------------------------------------------------

    def tree_path(self, task_id: str, generation: int) -> Path:
        if generation == 0:
            return self.sandbox.root / ".worktrees" / task_id
        return self.sandbox.root / ".worktrees" / f"{task_id}-g{generation}"

    def branch_name(self, task_id: str, generation: int) -> str:
        if generation == 0:
            return f"task/{task_id}"
        return f"task/{task_id}-g{generation}"

    def _report_path(self, task_id: str, generation: int) -> Path:
        return self.run_dir / "reports" / f"{task_id}-g{generation}.json"

    def _gate_path(self, task_id: str, generation: int, which: str) -> Path:
        return self.run_dir / "gates" / f"{task_id}-g{generation}.{which}"

    # -- запуск ----------------------------------------------------------

    def dispatch(self, task_ids: list[str], *, base: str | None = None) -> None:
        for index, task_id in enumerate(task_ids):
            state = self.states[task_id]
            if state.status != "pending":
                raise RuntimeError(
                    f"dispatch: задача {task_id!r} не pending (status={state.status!r})"
                )
            task = task_by_id(self.scenario, task_id)
            resolved_base = base if base is not None else self.sandbox.head(MAIN_BRANCH)

            if state.generation == -1:
                state.generation = 0
            generation = state.generation

            dispatch_id = uuid.uuid4().hex[:12]
            tree = self.tree_path(task_id, generation)
            branch = self.branch_name(task_id, generation)
            self.sandbox.git(
                "worktree", "add", "-b", branch, str(tree), resolved_base, cwd=self.sandbox.root
            )

            report_path = self._report_path(task_id, generation)
            report_path.parent.mkdir(parents=True, exist_ok=True)

            gate_start: Path | None = None
            gate_finish: Path | None = None
            if self.config.gates != "none":
                gate_start = self._gate_path(task_id, generation, "start")
                gate_start.parent.mkdir(parents=True, exist_ok=True)
            if self.config.gates == "manual":
                gate_finish = self._gate_path(task_id, generation, "finish")

            env = worker_env(
                self.sandbox,
                task,
                dispatch_id=dispatch_id,
                generation=generation,
                worktree=tree,
                branch=branch,
                report_path=report_path,
                gate_start=gate_start,
                gate_finish=gate_finish,
                gate_timeout=self.config.gate_timeout if self.config.gates != "none" else None,
            )
            proc = launch(self.sandbox, task, worktree=tree, env=env)

            state.status = "running"
            state.dispatch_id = dispatch_id
            state.dispatch_index = index
            state.tree = str(tree)
            state.branch = branch
            state.base = resolved_base
            state.first_change_tick = None
            state.head = None
            state.proc = proc
            # sequential: дедлайн ставит open_gate(..., "start") — ожидание в очереди
            # затворов не работа воркера и таймаутом не считается.
            state.deadline = (
                None if self.config.gates == "sequential" else time.monotonic() + self.config.timeout
            )
            state.report_path = report_path
            state.gate_start = gate_start
            state.gate_finish = gate_finish

            self.journal.add(
                "dispatched",
                task=task_id,
                generation=generation,
                dispatch_id=dispatch_id,
                tree=str(tree),
                branch=branch,
                base=resolved_base,
                dispatch_index=index,
            )

            if self.config.gates == "sequential":
                queue_index = len(self._gate_queue)
                self._gate_queue.append((task_id, generation))
                if self._gate_cursor == queue_index:
                    self.open_gate(task_id, "start", generation=generation)

    # -- опрос ----------------------------------------------------------

    def tick(self) -> None:
        self.tick_no += 1

        running_ids = sorted(
            (tid for tid, s in self.states.items() if s.status == "running"),
            key=lambda tid: self.states[tid].dispatch_index,
        )
        for task_id in running_ids:
            state = self.states[task_id]
            if state.status != "running":
                continue

            self._check_first_change(task_id)

            if state.proc.poll() is not None:
                self._finalize(task_id)
                continue

            if state.deadline is not None and time.monotonic() >= state.deadline:
                base = state.base
                self.revoke(task_id, kill=self.config.kill_on_timeout, reason="timeout")
                state.timeouts += 1
                if state.timeouts <= self.config.retries_on_timeout:
                    self.dispatch([task_id], base=base)
                else:
                    self.mark_failed(task_id, "timeout")

        self._process_revoked()
        self._advance_gate_cursor()

    def _check_first_change(self, task_id: str) -> None:
        state = self.states[task_id]
        if state.first_change_tick is not None:
            return
        tree = self.tree_path(task_id, state.generation)
        # --no-optional-locks: не берём index.lock при опросе — воркер в этом же
        # дереве может быть посреди `git add -A && git commit` (common.sh), и опрос
        # не должен с ним конкурировать за файл блокировки индекса.
        dirty = (
            self.sandbox.git(
                "--no-optional-locks", "status", "--porcelain", cwd=tree
            ).stdout.strip()
            != ""
        )
        if not dirty:
            count = int(
                self.sandbox.git_out(
                    "rev-list", "--count", f"{state.base}..{state.branch}", cwd=self.sandbox.root
                )
            )
            dirty = count > 0
        if dirty:
            state.first_change_tick = self.tick_no
            self.journal.add(
                "first_change", task=task_id, generation=state.generation, tick=self.tick_no
            )

    def _process_revoked(self) -> None:
        remaining: list[tuple] = []
        for entry in self._revoked:
            task_id, generation, dispatch_id, report_path, proc = entry
            current_generation = self.states[task_id].generation
            try:
                report = read_report(report_path)
            except ValueError:
                if proc.poll() is not None:
                    proc.wait()
                    self.journal.add(
                        "report_rejected",
                        task=task_id,
                        generation=generation,
                        current_generation=current_generation,
                        dispatch_id=dispatch_id,
                        reason="bad_report",
                    )
                else:
                    remaining.append(entry)
                continue

            if report is None:
                if proc.poll() is not None:
                    proc.wait()
                else:
                    remaining.append(entry)
                continue

            reason = check_report(self.states[task_id], report) or "stale_generation"
            self.journal.add(
                "report_rejected",
                task=task_id,
                generation=report["generation"],
                current_generation=current_generation,
                dispatch_id=report["dispatch_id"],
                reason=reason,
            )
            if proc.poll() is not None:
                proc.wait()

        self._revoked = remaining

    def _advance_gate_cursor(self) -> None:
        if self.config.gates != "sequential":
            return
        while self._gate_cursor < len(self._gate_queue):
            task_id, generation = self._gate_queue[self._gate_cursor]
            state = self.states.get(task_id)

            advance = False
            if state is None or state.status != "running" or state.generation != generation:
                advance = True
            elif state.first_change_tick is not None:
                advance = True
            elif state.proc is not None and state.proc.poll() is not None:
                advance = True
            else:
                opened_at = self._gate_opened_at.get((task_id, generation))
                if opened_at is not None and (time.monotonic() - opened_at) > self.config.timeout:
                    advance = True

            if not advance:
                break

            self._gate_cursor += 1
            if self._gate_cursor < len(self._gate_queue):
                next_task, next_generation = self._gate_queue[self._gate_cursor]
                self.open_gate(next_task, "start", generation=next_generation)

    def _finalize(self, task_id: str) -> None:
        state = self.states[task_id]
        proc = state.proc
        rc = proc.wait()
        generation = state.generation
        dispatch_id = state.dispatch_id
        report_path = state.report_path
        base = state.base
        branch = state.branch

        if rc != 0:
            self.mark_failed(task_id, "exit_code", exit_code=rc)
            return

        try:
            report = read_report(report_path)
        except ValueError:
            self.mark_failed(task_id, "bad_report")
            return

        if report is None:
            self.mark_failed(task_id, "no_report")
            return

        reason = check_report(state, report)
        if reason is not None:
            self.journal.add(
                "report_rejected",
                task=task_id,
                generation=report["generation"],
                current_generation=state.generation,
                dispatch_id=report["dispatch_id"],
                reason=reason,
            )
            self.mark_failed(task_id, "rejected_report")
            return

        self.journal.add(
            "report_accepted", task=task_id, generation=generation, dispatch_id=dispatch_id
        )

        count = int(
            self.sandbox.git_out("rev-list", "--count", f"{base}..{branch}", cwd=self.sandbox.root)
        )
        if count == 0:
            self.mark_failed(task_id, "empty_diff")
            return

        touched = self.touched_files(task_id)
        task = task_by_id(self.scenario, task_id)
        outside = [f for f in touched if not any(scope_covers(entry, f) for entry in task.write_scope)]
        if outside:
            declared = list(task.write_scope)
            self.journal.add(
                "scope_violation",
                task=task_id,
                generation=generation,
                declared=declared,
                touched=touched,
                outside=outside,
            )
            self.journal.discrepancies.append(
                {
                    "task": task_id,
                    "generation": generation,
                    "declared": declared,
                    "touched": touched,
                    "outside": outside,
                }
            )

        state.status = "done"
        state.head = self.sandbox.head(branch)
        self.journal.add("done", task=task_id, generation=generation, head=state.head, touched=touched)

    # -- управление ----------------------------------------------------------

    def revoke(self, task_id: str, *, kill: bool, reason: str) -> None:
        state = self.states[task_id]
        if state.status != "running":
            raise RuntimeError(f"revoke: задача {task_id!r} не running (status={state.status!r})")

        proc = state.proc
        generation = state.generation
        dispatch_id = state.dispatch_id
        report_path = state.report_path

        if kill:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                deadline = time.monotonic() + 1.0
                while proc.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                if proc.poll() is None:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
            proc.wait()

        self.journal.add("revoked", task=task_id, generation=generation, reason=reason, killed=kill)
        self._revoked.append((task_id, generation, dispatch_id, report_path, proc))

        state.generation += 1
        state.dispatch_id = None
        state.status = "pending"
        state.first_change_tick = None
        state.head = None
        state.proc = None
        state.deadline = None
        state.report_path = None
        state.gate_start = None
        state.gate_finish = None

    def open_gate(self, task_id: str, which: str, *, generation: int | None = None) -> None:
        if generation is None:
            generation = self.states[task_id].generation
        path = self._gate_path(task_id, generation, which)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        if which == "start":
            now = time.monotonic()
            self._gate_opened_at[(task_id, generation)] = now
            state = self.states[task_id]
            if (
                self.config.gates == "sequential"
                and state.status == "running"
                and state.generation == generation
                and state.deadline is None
            ):
                state.deadline = now + self.config.timeout

    def mark_failed(self, task_id: str, reason: str, **extra) -> None:
        state = self.states[task_id]
        if state.status in ("failed", "merged"):
            raise RuntimeError(f"mark_failed: задача {task_id!r} уже {state.status!r}")
        state.status = "failed"
        state.fail_reason = reason
        self.journal.add("failed", task=task_id, generation=state.generation, reason=reason, **extra)

    def mark_merged(self, task_id: str, *, sha: str) -> None:
        state = self.states[task_id]
        if state.status != "done":
            raise RuntimeError(f"mark_merged: задача {task_id!r} не done (status={state.status!r})")
        state.status = "merged"
        state.head = sha

    def touched_files(self, task_id: str) -> list[str]:
        state = self.states[task_id]
        out = self.sandbox.git_out(
            "diff", "--name-only", f"{state.base}..{state.branch}", cwd=self.sandbox.root
        )
        return sorted(line for line in out.splitlines() if line)

    def remove_tree(self, task_id: str, *, generation: int | None = None) -> None:
        if generation is None:
            generation = self.states[task_id].generation
        tree = self.tree_path(task_id, generation)
        branch = self.branch_name(task_id, generation)
        self.sandbox.git("worktree", "remove", "--force", str(tree), cwd=self.sandbox.root, check=False)
        self.sandbox.git("branch", "-D", branch, cwd=self.sandbox.root, check=False)

    def running(self) -> list[str]:
        return [tid for tid, s in self.states.items() if s.status == "running"]

    def wait(self, *, until=None, deadline: float = 30.0, on_tick=None) -> None:
        start = time.monotonic()
        while True:
            if until is not None:
                if until():
                    return
            elif not self.running():
                return

            if time.monotonic() - start > deadline:
                running_now = self.running()
                condition = "until" if until is not None else "running"
                raise RuntimeError(
                    f"wait: истёк дедлайн {deadline}с (условие={condition}), running={running_now}"
                )

            self.tick()
            if on_tick is not None:
                on_tick()
            time.sleep(self.config.poll)

    def withdraw(self, task_id: str, *, reason: str, kill: bool = True) -> None:
        """Снять задачу лестницей реакций: `running` — как `revoke`; `done` —
        поднять поколение и вернуть в `pending`, не трогая старое дерево (его
        сносит вызывающий, `remove_tree`)."""
        state = self.states[task_id]
        if state.status == "running":
            self.revoke(task_id, kill=kill, reason=reason)
            return
        if state.status != "done":
            raise RuntimeError(
                f"withdraw: задача {task_id!r} не running/done (status={state.status!r})"
            )

        old_generation = state.generation
        state.generation += 1
        state.dispatch_id = None
        state.head = None
        state.first_change_tick = None
        state.status = "pending"
        self.journal.add(
            "revoked", task=task_id, generation=old_generation, reason=reason, killed=False
        )
