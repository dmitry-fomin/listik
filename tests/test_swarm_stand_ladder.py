"""Тесты наблюдателя и лестницы реакций стенда роя (`tests/swarm_stand/watch.py`).

Планировщик здесь обходится — пара задач диспетчеризуется напрямую. Профили и
write_scope подобраны так, чтобы конфликт (или его отсутствие) определялся
фактическим содержимым файла, а не заявленным write_scope.

Когда сценарий подразумевает, что владеет `t1` (тесты 1-4, 6-7), стартовое
ворото `t1` открывается и его `first_change_tick` дожидается ДО открытия
старта `t2` — без этого владение решает случайность планировщика ОС между
двумя почти мгновенными профилями на одном файле, а не порядок захвата.
Наблюдатель во время этой синхронизации не тикает (используется
`dispatcher.wait`, не `tick_until`): иначе он может застать `t2` уже
пишущим (`git status` вживую) раньше, чем диспетчер пометит её
`first_change_tick`, и снять её до того, как тест успеет это проверить.
"""

import time
import unittest

from tests.swarm_stand.barrier import run_barrier
from tests.swarm_stand.dispatch import Dispatcher, RunConfig
from tests.swarm_stand.journal import Journal
from tests.swarm_stand.sandbox import build
from tests.swarm_stand.scenario import Scenario, Task, validate
from tests.swarm_stand.testing import require_git, stand_tmpdir
from tests.swarm_stand.watch import Watcher


def tick_until(dispatcher, watcher, pred, deadline=10.0):
    start = time.monotonic()
    while not pred():
        if time.monotonic() - start > deadline:
            raise AssertionError(f"tick_until: дедлайн {deadline}с истёк")
        dispatcher.tick()
        watcher.tick()
        time.sleep(dispatcher.config.poll)


class LadderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_git(cls)

    # -- обвязка ----------------------------------------------------------

    def _revoke_running(self, dispatcher):
        for task_id in list(dispatcher.running()):
            try:
                dispatcher.revoke(task_id, kill=True, reason="test-cleanup")
            except RuntimeError:
                pass

    def _build(self, tasks, config=None):
        tmp = stand_tmpdir(self)
        scenario = Scenario(tasks=tasks)
        validate(scenario)
        sandbox = build(tmp / "repo", scenario)
        journal = Journal()
        config = config if config is not None else RunConfig(timeout=5, gates="manual")
        dispatcher = Dispatcher(sandbox, scenario, config, journal)
        watcher = Watcher(sandbox, scenario, dispatcher, journal, wave=0)
        self.addCleanup(self._revoke_running, dispatcher)
        return sandbox, scenario, journal, dispatcher, watcher

    def _start_t1_first(self, dispatcher):
        """Открыть t1.start и дождаться его first_change_tick без участия
        наблюдателя, прежде чем t2 вообще стартует — единственный надёжный
        способ гарантировать t1 владельцем файла на любой машине."""
        dispatcher.open_gate("t1", "start")
        dispatcher.wait(
            until=lambda: dispatcher.states["t1"].first_change_tick is not None, deadline=15
        )

    # -- 1. разные концы файла сливаются ----------------------------------------------------------

    def test_clean_merge_different_ends(self):
        tasks = [
            Task(id="t1", profile="prepend", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        self._start_t1_first(dispatcher)
        dispatcher.open_gate("t2", "start")

        dispatcher.open_gate("t1", "finish")
        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15, on_tick=watcher.tick)
        watcher.tick()

        probe = journal.last("probe", a="t1", b="t2")
        self.assertIsNotNone(probe)
        self.assertEqual(probe["clean"], True)
        self.assertEqual(probe["mode"], "committed")
        self.assertEqual(probe["files"], ["pkg/alpha.py"])
        self.assertFalse(journal.of("dropped"))
        self.assertFalse(journal.of("held"))

        result = run_barrier(
            sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t2"], held=watcher.held
        )
        self.assertEqual(result.merged, ["t1", "t2"])

        lines = (sandbox.root / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(lines[0], "# t1 header")
        self.assertEqual(lines[-2:], ["# t2 line 1", "# t2 line 2"])

    # -- 2. дописывания в конец конфликтуют — незакоммиченный опоздавший снимается --------

    def test_append_conflict_uncommitted_dropped(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        self._start_t1_first(dispatcher)
        dispatcher.open_gate("t1", "finish")
        dispatcher.wait(until=lambda: dispatcher.states["t1"].status == "done", deadline=15)

        # t2 стартует только теперь, когда t1 уже done (закоммичен) — решение
        # лестницы наступит в mode "uncommitted" (t1 committed, t2 ещё нет).
        dispatcher.open_gate("t2", "start")
        tick_until(dispatcher, watcher, lambda: journal.of("dropped") or journal.of("held"))

        probe = journal.last("probe", a="t1", b="t2")
        self.assertIsNotNone(probe)
        self.assertEqual(probe["mode"], "uncommitted")
        self.assertEqual(probe["clean"], False)

        dropped = journal.last("dropped")
        self.assertIsNotNone(dropped)
        self.assertEqual(dropped["task"], "t2")
        self.assertEqual(dropped["owner"], "t1")

        state_t2 = dispatcher.states["t2"]
        # withdraw() снял t2 через revoke(kill=True) — процесс убит и proc уже
        # сброшен в None самим revoke (см. test_timeout_with_kill/test_zombie).
        self.assertIsNone(state_t2.proc)
        self.assertEqual(state_t2.status, "pending")
        self.assertEqual(state_t2.generation, 1)

        self.assertFalse((sandbox.root / ".worktrees" / "t2").exists())
        branch_check = sandbox.git("rev-parse", "--verify", "task/t2", cwd=sandbox.root, check=False)
        self.assertNotEqual(branch_check.returncode, 0)

        revoked = journal.last("revoked", task="t2", reason="dropped")
        self.assertIsNotNone(revoked)

        result = run_barrier(sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t2"])
        self.assertEqual(result.merged, ["t1"])

        base = sandbox.head("main")
        dispatcher.dispatch(["t2"], base=base)
        dispatcher.open_gate("t2", "start")
        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15)

        result1 = run_barrier(sandbox, scenario, dispatcher, journal, wave=1, task_ids=["t2"])
        self.assertEqual(result1.merged, ["t2"])

        lines = (sandbox.root / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(lines[-4:], ["# t1 line 1", "# t1 line 2", "# t2 line 1", "# t2 line 2"])

        self.assertFalse(journal.of("conflict_resolved"))

    # -- 3. закоммиченный опоздавший снимается ----------------------------------------------------------

    def test_append_conflict_committed_dropped(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        self._start_t1_first(dispatcher)

        # финишные ворота обеих открыты ДО того, как наблюдатель хоть раз
        # потикает — и t1, и t2 успевают закоммититься раньше первого решения.
        dispatcher.open_gate("t1", "finish")
        dispatcher.open_gate("t2", "start")
        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15)

        watcher.tick()

        probe = journal.last("probe", a="t1", b="t2")
        self.assertIsNotNone(probe)
        self.assertEqual(probe["mode"], "committed")
        self.assertEqual(probe["clean"], False)

        dropped = journal.last("dropped")
        self.assertIsNotNone(dropped)
        self.assertEqual(dropped["task"], "t2")
        self.assertEqual(dropped["owner"], "t1")

        revoked = journal.last("revoked", task="t2", reason="dropped")
        self.assertIsNotNone(revoked)
        self.assertEqual(revoked["killed"], False)

    # -- 4. дорогой опоздавший придерживается и доделывает в той же волне -----------------

    def test_expensive_late_held_resumes_same_wave(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(
                id="t2",
                profile="append",
                write_scope=["pkg/alpha.py"],
                launch_route="xhigh-pipeline",
            ),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        self._start_t1_first(dispatcher)

        dispatcher.open_gate("t1", "finish")
        dispatcher.open_gate("t2", "start")
        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15)

        watcher.tick()

        held_event = journal.last("held")
        self.assertIsNotNone(held_event)
        self.assertEqual(held_event["task"], "t2")
        self.assertEqual(held_event["owner"], "t1")
        self.assertEqual(dispatcher.states["t2"].status, "held")
        self.assertEqual(watcher.held, ["t2"])

        result = run_barrier(
            sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t2"], held=watcher.held
        )
        self.assertEqual(result.merged, ["t1", "t2"])

        merged_events = journal.of("merged")
        resumed_events = journal.of("resumed")
        self.assertEqual(len(merged_events), 2)
        self.assertEqual(len(resumed_events), 1)

        m1, m2 = merged_events
        r1 = resumed_events[0]
        self.assertEqual(m1["task"], "t1")
        self.assertEqual(m2["task"], "t2")
        self.assertLess(m1["seq"], r1["seq"])
        self.assertLess(r1["seq"], m2["seq"])

        self.assertEqual(r1["task"], "t2")
        self.assertEqual(r1["generation"], 1)
        # база резюме — main сразу после слияния t1: одна коммита-песочницы плюс
        # один коммит t1 (ff-only), и не позже (t2 в неё ещё не входит).
        self.assertEqual(int(sandbox.git_out("rev-list", "--count", r1["base"])), 2)
        self.assertEqual(m2["branch"], "task/t2-g1")

        lines = (sandbox.root / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(lines[-4:], ["# t1 line 1", "# t1 line 2", "# t2 line 1", "# t2 line 2"])

        self.assertFalse(journal.of("conflict_resolved"))

    # -- 5. владеет первый захвативший, а не первый запущенный ----------------------------------------------------------

    def test_owner_is_first_to_capture(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        dispatcher.open_gate("t2", "start")
        dispatcher.wait(
            until=lambda: dispatcher.states["t2"].first_change_tick is not None, deadline=15
        )
        dispatcher.open_gate("t1", "start")

        dispatcher.open_gate("t1", "finish")
        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15)

        watcher.tick()

        self.assertEqual(watcher.owner_of("t1", "t2"), "t2")
        dropped = journal.last("dropped")
        self.assertIsNotNone(dropped)
        self.assertEqual(dropped["task"], "t1")
        self.assertEqual(dropped["owner"], "t2")

    # -- 6. решение по паре принимается один раз ----------------------------------------------------------

    def test_decision_is_made_once(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        self._start_t1_first(dispatcher)

        dispatcher.open_gate("t1", "finish")
        dispatcher.open_gate("t2", "start")
        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15)

        watcher.tick()
        self.assertEqual(len(journal.of("dropped")), 1)

        for _ in range(5):
            watcher.tick()

        self.assertEqual(len(journal.of("dropped")), 1)
        self.assertEqual(watcher.decided, {("t1", "t2")})

    # -- 6a. снятая сторона a выходит из остальных пар тика ----------------------------------------------------------

    def test_withdrawn_a_leaves_remaining_pairs(self):
        # захват: t2, затем t1, затем t3. Пара (t1, t2) снимает t1 (late == a);
        # пара (t1, t3) по устаревшему files_cache[t1] сняла бы t3 с владельцем
        # t1 — вместо этого t3 снимается парой (t2, t3) с владельцем t2.
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t3", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2", "t3"])
        for task_id in ("t2", "t1", "t3"):
            dispatcher.open_gate(task_id, "start")
            dispatcher.wait(
                until=lambda t=task_id: dispatcher.states[t].first_change_tick is not None,
                deadline=15,
            )
        for task_id in ("t1", "t2", "t3"):
            dispatcher.open_gate(task_id, "finish")
        dispatcher.wait(deadline=15)

        watcher.tick()

        dropped = {e["task"]: e["owner"] for e in journal.of("dropped")}
        self.assertEqual(dropped, {"t1": "t2", "t3": "t2"})
        self.assertIsNone(journal.last("probe", a="t1", b="t3"))
        self.assertEqual(watcher.decided, {("t1", "t2"), ("t2", "t3")})

    # -- 7. чужой файл при чистом слиянии — вливается, факт записан ----------------------------------------------------------

    def test_no_common_files_still_records_scope_violation(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(
                id="t2",
                profile="scope_break",
                write_scope=["pkg/gamma.py"],
                foreign="pkg/beta.py",
            ),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        self._start_t1_first(dispatcher)
        dispatcher.open_gate("t2", "start")
        dispatcher.open_gate("t1", "finish")
        dispatcher.open_gate("t2", "finish")

        dispatcher.wait(deadline=15, on_tick=watcher.tick)
        watcher.tick()

        self.assertIsNone(watcher.probe("t1", "t2"))

        result = run_barrier(
            sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t2"], held=watcher.held
        )
        self.assertEqual(result.merged, ["t1", "t2"])

        self.assertTrue(journal.of("scope_violation", task="t2"))
        self.assertTrue(any(d["task"] == "t2" for d in journal.discrepancies))

    # -- 8. незакоммиченное пробное слияние умеет отвечать «чисто» ----------------------------------------------------------

    def test_uncommitted_probe_can_be_clean(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="prepend", write_scope=["pkg/alpha.py"]),
        ]
        sandbox, scenario, journal, dispatcher, watcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        dispatcher.open_gate("t1", "start")
        dispatcher.open_gate("t1", "finish")
        dispatcher.wait(until=lambda: dispatcher.states["t1"].status == "done", deadline=15)

        dispatcher.open_gate("t2", "start")
        dispatcher.wait(
            until=lambda: dispatcher.states["t2"].first_change_tick is not None, deadline=15
        )

        probe = watcher.probe("t1", "t2")
        self.assertIsNotNone(probe)
        self.assertTrue(probe.clean)
        self.assertEqual(probe.mode, "uncommitted")
        self.assertEqual(probe.files, ["pkg/alpha.py"])

        decisions = watcher.tick()
        self.assertEqual(decisions, [])
        self.assertFalse(journal.of("dropped"))
        self.assertFalse(journal.of("held"))
        self.assertFalse(journal.of("revoked", task="t2"))

        dispatcher.open_gate("t2", "finish")
        dispatcher.wait(deadline=15)

        result = run_barrier(sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t2"])
        self.assertEqual(result.merged, ["t1", "t2"])


if __name__ == "__main__":
    unittest.main()
