"""Наблюдатель стенда роя: ловит нарушение write_scope на практике, пробным
слиянием, а не эвристикой над заявленными областями. Для пар активных деревьев,
тронувших общие файлы, решает пробное слияние — `git merge-tree --write-tree`
для закоммиченного, `git diff | git apply --check` для незакоммиченного — и
лестница реакций: чисто — никого не трогать; первый захвативший файл (по
`first_change_tick`, затем `dispatch_index`) владеет им; конфликт и опоздавший
на дешёвом маршруте — снять и снести дерево, вернуть в следующую волну; конфликт
и опоздавший на дорогом (`xhigh-pipeline`) — придержать. Настоящего наблюдателя
и лестницу пишет swarm-5 (не в `listik/`); этот модуль — только модель для
тестов.

Модель «дать доделать» на стенде: держать дерево и ребейзить его нечем — поддельный
воркер не умеет дорабатывать; поэтому придержанная задача перезапускается от нового
`main` после слияния владельца тем же профилем с новым поколением, в той же волне
(см. `barrier.run_barrier(..., held=...)`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dispatch import Dispatcher
from .journal import Journal
from .sandbox import Sandbox
from .scenario import EXPENSIVE_ROUTES, Scenario, task_by_id


@dataclass
class Probe:
    a: str
    b: str
    files: list[str]
    clean: bool
    mode: str  # "committed" | "uncommitted"


class Watcher:
    def __init__(
        self,
        sandbox: Sandbox,
        scenario: Scenario,
        dispatcher: Dispatcher,
        journal: Journal,
        *,
        wave: int,
    ) -> None:
        self.sandbox = sandbox
        self.scenario = scenario
        self.dispatcher = dispatcher
        self.journal = journal
        self.wave = wave
        self.held: list[str] = []
        self.decided: set[tuple[str, str]] = set()
        self._last_probe: dict[tuple[str, str], tuple] = {}

    # -- состояние ----------------------------------------------------------

    def active(self) -> list[str]:
        items = [
            (state.dispatch_index, tid)
            for tid, state in self.dispatcher.states.items()
            if state.status in ("running", "done")
        ]
        items.sort(key=lambda pair: pair[0])
        return [tid for _, tid in items]

    def files_of(self, task_id: str) -> tuple[list[str], bool]:
        state = self.dispatcher.states[task_id]
        tree = Path(state.tree)
        # --no-optional-locks: не берём index.lock во время опроса — воркер в
        # этом же дереве может быть посреди `git add -A && git commit`
        # (common.sh), как и в dispatch.py._check_first_change.
        status_out = self.sandbox.git(
            "--no-optional-locks", "status", "--porcelain", cwd=tree
        ).stdout
        working = {line[3:] for line in status_out.splitlines() if line}
        committed_diff = set(self.dispatcher.touched_files(task_id))
        files = sorted(working | committed_diff)

        # "процесс завершён" здесь и означает status == "done" — это одно и то
        # же событие (диспетчер сам переводит задачу в done, как только видит,
        # что процесс вышел, в своём tick()). Живой опрос state.proc.poll() тут
        # не годится: воркер может успеть выйти и записать отчёт МЕЖДУ тиком
        # диспетчера и этим вызовом, а диспетчер ещё не перевёл статус — тогда
        # withdraw() пойдёт по ветке running/revoke и убьёт уже мёртвый процесс,
        # чей отчёт потом отклонится как stale_generation (report_rejected),
        # хотя по факту слияние прошло чисто и снимать было нечего.
        committed = state.status == "done" and status_out.strip() == ""
        return files, committed

    def _exited_but_not_finalized(self, task_id: str) -> bool:
        state = self.dispatcher.states[task_id]
        return (
            state.status == "running"
            and state.proc is not None
            and state.proc.poll() is not None
        )

    def owner_of(self, a: str, b: str) -> str:
        def key(task_id: str) -> tuple[float, int]:
            state = self.dispatcher.states[task_id]
            tick = state.first_change_tick if state.first_change_tick is not None else float("inf")
            return (tick, state.dispatch_index)

        return a if key(a) <= key(b) else b

    # -- пробное слияние ----------------------------------------------------------

    def probe(self, a: str, b: str, *, _files: tuple | None = None) -> Probe | None:
        if _files is not None:
            (files_a, committed_a), (files_b, committed_b) = _files
        else:
            files_a, committed_a = self.files_of(a)
            files_b, committed_b = self.files_of(b)

        set_a = set(files_a)
        common = sorted(f for f in files_b if f in set_a)
        if not common:
            return None

        if committed_a and committed_b:
            return self._probe_committed(a, b, common)
        return self._probe_uncommitted(a, b, common, committed_a=committed_a)

    def _probe_committed(self, a: str, b: str, common: list[str]) -> Probe:
        branch_a = self.dispatcher.states[a].branch
        branch_b = self.dispatcher.states[b].branch
        result = self.sandbox.git(
            "merge-tree", "--write-tree", branch_a, branch_b, cwd=self.sandbox.root, check=False
        )
        if result.returncode == 0:
            clean = True
        elif result.returncode == 1:
            clean = False
        else:
            raise RuntimeError(
                f"git merge-tree --write-tree {branch_a} {branch_b} завершился "
                f"кодом {result.returncode}: {result.stderr}"
            )
        return Probe(a=a, b=b, files=common, clean=clean, mode="committed")

    def _probe_uncommitted(
        self, a: str, b: str, common: list[str], *, committed_a: bool, _retry: bool = True
    ) -> Probe | None:
        # незакоммиченная сторона — u; если обе незакоммичены, u = b (правило "если
        # обе — b" из ТЗ).
        u = b if committed_a else a
        o = a if u == b else b

        tree_u = Path(self.dispatcher.states[u].tree)
        tree_o = Path(self.dispatcher.states[o].tree)

        # git_out делает .strip() — на патче это срезает завершающий перевод строки
        # и `git apply` отвечает «corrupt patch» (код 128); нужен сырой stdout.
        patch = self.sandbox.git("--no-optional-locks", "diff", "HEAD", cwd=tree_u).stdout
        if patch == "":
            # успели закоммитить между files_of() и этим вызовом — пересчитать
            # свежими files_of; git apply на пустом входе не звать. _retry
            # ограничивает пересчёт одним разом — если статус ещё не
            # устаканился (диспетчер не перевёл задачу в done), нечего решать
            # в этом тике, следующий тик разберётся.
            if not _retry:
                return None
            files_a2, committed_a2 = self.files_of(a)
            files_b2, committed_b2 = self.files_of(b)
            common2 = sorted(f for f in files_b2 if f in set(files_a2))
            if not common2:
                return None
            if committed_a2 and committed_b2:
                return self._probe_committed(a, b, common2)
            return self._probe_uncommitted(
                a, b, common2, committed_a=committed_a2, _retry=False
            )

        result = self.sandbox.git(
            "apply", "--check", cwd=tree_o, input=patch, check=False
        )
        if result.returncode == 0:
            clean = True
        elif result.returncode == 1:
            clean = False
        else:
            raise RuntimeError(
                f"git apply --check в {tree_o} завершился кодом {result.returncode} "
                f"(патч из {tree_u} битый, не конфликт): {result.stderr}"
            )
        return Probe(a=a, b=b, files=common, clean=clean, mode="uncommitted")

    # -- тик ----------------------------------------------------------

    def tick(self) -> list[dict]:
        active = self.active()
        active_set = set(active)
        files_cache = {tid: self.files_of(tid) for tid in active}

        decisions: list[dict] = []

        for i, a in enumerate(active):
            if a not in active_set:
                continue
            for b in active[i + 1 :]:
                if b not in active_set:
                    continue
                if (a, b) in self.decided:
                    continue

                p = self.probe(a, b, _files=(files_cache[a], files_cache[b]))
                if p is None:
                    continue

                record = (tuple(p.files), p.clean, p.mode)
                if self._last_probe.get((a, b)) != record:
                    self._last_probe[(a, b)] = record
                    self.journal.add(
                        "probe",
                        wave=self.wave,
                        a=a,
                        b=b,
                        files=p.files,
                        clean=p.clean,
                        mode=p.mode,
                    )

                if p.clean:
                    continue

                owner = self.owner_of(a, b)
                late = b if owner == a else a

                if self._exited_but_not_finalized(late):
                    # процесс late уже вышел (и, вероятно, успел закоммитить и
                    # написать отчёт), а диспетчер ещё не перевёл её в done —
                    # решать сейчас нельзя: withdraw() пойдёт по ветке
                    # running/revoke и убьёт уже мёртвый процесс, чей отчёт
                    # потом отклонится как stale_generation. Пара не в
                    # decided — следующий тик (после того как диспетчер
                    # догонит статус до done) переоценит её корректно.
                    continue

                self.decided.add((a, b))

                task = task_by_id(self.scenario, late)
                old_generation = self.dispatcher.states[late].generation

                if task.launch_route in EXPENSIVE_ROUTES:
                    event = self.journal.add(
                        "held", wave=self.wave, task=late, owner=owner, files=p.files
                    )
                    self.dispatcher.withdraw(late, reason="held")
                    self.dispatcher.remove_tree(late, generation=old_generation)
                    self.dispatcher.states[late].status = "held"
                    self.held.append(late)
                else:
                    event = self.journal.add(
                        "dropped", wave=self.wave, task=late, owner=owner, files=p.files
                    )
                    self.dispatcher.withdraw(late, reason="dropped")
                    self.dispatcher.remove_tree(late, generation=old_generation)

                decisions.append(event)
                active_set.discard(late)

        return decisions
