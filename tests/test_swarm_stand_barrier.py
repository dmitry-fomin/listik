import json
import unittest

from tests.swarm_stand.arbiter import resolve_both_sides
from tests.swarm_stand.barrier import run_barrier
from tests.swarm_stand.dispatch import Dispatcher, RunConfig
from tests.swarm_stand.journal import Journal
from tests.swarm_stand.run import run
from tests.swarm_stand.sandbox import DEFAULT_FILES, build
from tests.swarm_stand.scenario import Scenario, Task, validate
from tests.swarm_stand.testing import require_git, stand_tmpdir


class BarrierTests(unittest.TestCase):
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
        config = config if config is not None else RunConfig(timeout=5, gates="sequential")
        dispatcher = Dispatcher(sandbox, scenario, config, journal)
        self.addCleanup(self._revoke_running, dispatcher)
        return sandbox, scenario, journal, dispatcher

    # -- 1. арбитр ----------------------------------------------------------

    def test_arbiter_resolve_both_sides(self):
        text = "a\n<<<<<<< HEAD\nx\n=======\ny\n>>>>>>> task\nb\n"
        self.assertEqual(resolve_both_sides(text), "a\nx\ny\nb\n")

        self.assertIsNone(resolve_both_sides("никаких маркеров тут нет\n"))
        self.assertIsNone(resolve_both_sides("<<<<<<< HEAD\nx\n=======\ny\n"))

        two_blocks = (
            "<<<<<<< HEAD\np\n=======\nq\n>>>>>>> task\n"
            "mid\n"
            "<<<<<<< HEAD\nr\n=======\ns\n>>>>>>> task\n"
        )
        self.assertEqual(resolve_both_sides(two_blocks), "p\nq\nmid\nr\ns\n")

    # -- 2. ff-only требует ребейза ----------------------------------------------------------

    def test_ffonly_requires_rebase(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"]),
        ]
        sandbox, scenario, journal, dispatcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t2"])
        dispatcher.wait(deadline=15)
        orig2 = sandbox.head("task/t2")

        result = run_barrier(sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t2"])

        self.assertEqual(result.merged, ["t1", "t2"])
        self.assertIs(result.integration, True)

        self.assertEqual(int(sandbox.git_out("rev-list", "--count", "main")), 3)
        self.assertEqual(sandbox.git_out("log", "--merges", "main"), "")

        ancestor = sandbox.git("merge-base", "--is-ancestor", orig2, "main", check=False)
        self.assertEqual(ancestor.returncode, 1)
        beta_content = (sandbox.root / "pkg" / "beta.py").read_text()
        self.assertIn('return "beta-t2"', beta_content)

        for task_id in ("t1", "t2"):
            rebased = journal.last("rebased", task=task_id)
            self.assertIsNotNone(rebased)
            self.assertEqual(rebased["conflicts"], [])
            self.assertTrue(journal.of("cleaned", task=task_id))

        self.assertFalse((sandbox.root / ".worktrees" / "t1").exists())
        self.assertFalse((sandbox.root / ".worktrees" / "t2").exists())
        for branch in ("task/t1", "task/t2"):
            check = sandbox.git("rev-parse", "--verify", branch, cwd=sandbox.root, check=False)
            self.assertNotEqual(check.returncode, 0)

        wf = journal.last("wave_finished")
        self.assertEqual(wf["merged"], ["t1", "t2"])
        self.assertEqual(wf["failed"], [])

        # Контрольная проверка: без ребейза второй ff-only падает даже без
        # пересечения файлов — подтверждает, что ребейз в барьере не лишний.
        tasks2 = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"]),
        ]
        sandbox2, _scenario2, _journal2, dispatcher2 = self._build(tasks2)
        dispatcher2.dispatch(["t1", "t2"])
        dispatcher2.wait(deadline=15)

        m1 = sandbox2.git("merge", "--ff-only", "task/t1", cwd=sandbox2.root, check=False)
        self.assertEqual(m1.returncode, 0)
        m2 = sandbox2.git("merge", "--ff-only", "task/t2", cwd=sandbox2.root, check=False)
        self.assertNotEqual(m2.returncode, 0)

    # -- 3. конфликт → арбитр ----------------------------------------------------------

    def test_conflict_resolved_by_arbiter(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(
                id="t3",
                profile="scope_break",
                write_scope=["pkg/gamma.py"],
                foreign="pkg/alpha.py",
            ),
        ]
        sandbox, scenario, journal, dispatcher = self._build(tasks)

        dispatcher.dispatch(["t1", "t3"])
        dispatcher.wait(deadline=15)

        result = run_barrier(sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t1", "t3"])

        self.assertEqual(result.merged, ["t1", "t3"])

        conflict_resolved = journal.last("conflict_resolved", task="t3")
        self.assertIsNotNone(conflict_resolved)
        self.assertEqual(conflict_resolved["files"], ["pkg/alpha.py"])

        rebased = journal.last("rebased", task="t3")
        self.assertIsNotNone(rebased)
        self.assertEqual(rebased["conflicts"], ["pkg/alpha.py"])
        self.assertEqual(conflict_resolved["files"], rebased["conflicts"])

        alpha_lines = (sandbox.root / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(alpha_lines[-3:], ["# t1 line 1", "# t1 line 2", "# t3 foreign"])

        gamma_lines = (sandbox.root / "pkg" / "gamma.py").read_text().splitlines()
        self.assertEqual(gamma_lines[-2:], ["# t3 line 1", "# t3 line 2"])

        self.assertIs(result.integration, True)
        self.assertTrue(journal.of("scope_violation", task="t3"))

    # -- 4. красные тесты не пускают в слияние ----------------------------------------------------------

    def test_red_tests_block_merge(self):
        tasks = [
            Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"]),
            Task(id="t6", profile="red_tests", write_scope=["pkg/gamma.py"]),
        ]
        sandbox, scenario, journal, dispatcher = self._build(tasks)

        dispatcher.dispatch(["t2", "t6"])
        dispatcher.wait(deadline=15)

        result = run_barrier(sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t2", "t6"])

        self.assertEqual(result.merged, ["t2"])
        self.assertEqual(result.failed, ["t6"])

        self.assertTrue(journal.of("tests_red", task="t6"))
        failed_event = journal.last("failed", task="t6")
        self.assertIsNotNone(failed_event)
        self.assertEqual(failed_event["reason"], "tests_red")

        gamma_content = (sandbox.root / "pkg" / "gamma.py").read_text()
        self.assertEqual(gamma_content, DEFAULT_FILES["pkg/gamma.py"])

        self.assertTrue((sandbox.root / ".worktrees" / "t6").exists())
        branch_check = sandbox.git(
            "rev-parse", "--verify", "task/t6", cwd=sandbox.root, check=False
        )
        self.assertEqual(branch_check.returncode, 0)

        self.assertTrue(journal.of("cleaned", task="t2"))
        self.assertFalse(journal.of("cleaned", task="t6"))
        self.assertIs(result.integration, True)

    # -- 5. интеграция красная — стоп и needs-owner ----------------------------------------------------------

    def test_integration_red_stops_run(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t8", profile="prepend", write_scope=["pkg/alpha.py"], deps=["t1"]),
        ]
        scenario = Scenario(tasks=tasks)
        tmp = stand_tmpdir(self)

        result = run(
            scenario,
            tmp,
            RunConfig(timeout=5, gates="sequential", ladder=False),
            integration_command=["sh", "-c", "exit 1"],
        )
        self.addCleanup(self._revoke_running, result.dispatcher)

        self.assertEqual(result.merged, ["t1"])
        self.assertEqual(result.stopped, "integration_red")

        journal = result.journal
        integration_red = journal.last("integration_red")
        self.assertIsNotNone(integration_red)
        self.assertEqual(integration_red["wave"], 0)

        needs_owner = journal.last("needs_owner", reason="integration_red")
        self.assertIsNotNone(needs_owner)

        self.assertEqual(len(journal.of("wave_started")), 1)
        self.assertEqual(journal.of("cleaned"), [])

        self.assertTrue((result.sandbox.root / ".worktrees" / "t1").exists())
        self.assertEqual(result.unfinished, {"t8": "not_started"})

        stopped = journal.of("stopped")
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["reason"], "integration_red")
        self.assertEqual(stopped[0]["wave"], 0)
        self.assertNotIn("cycles", stopped[0])

    # -- 6. run без лестницы ----------------------------------------------------------

    def test_run_cycles_waves(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"]),
            Task(id="t4", profile="crash", write_scope=["pkg/delta.py"]),
            Task(id="t5", profile="empty", write_scope=["pkg/epsilon.py"], deps=["t2"]),
            Task(id="t8", profile="prepend", write_scope=["pkg/alpha.py"], deps=["t1"]),
            Task(
                id="t9",
                profile="append",
                write_scope=["pkg/epsilon.py"],
                launch_route=None,
            ),
            Task(id="t10", profile="append", write_scope=["pkg/delta.py"], deps=["t4"]),
        ]
        scenario = Scenario(tasks=tasks)
        tmp = stand_tmpdir(self)

        result = run(scenario, tmp, RunConfig(timeout=5, gates="sequential", ladder=False))
        self.addCleanup(self._revoke_running, result.dispatcher)

        self.assertEqual(result.waves, [["t1", "t2", "t4"], ["t5", "t8"]])
        self.assertEqual(result.merged, ["t1", "t2", "t8"])
        self.assertEqual(result.stopped, "no_ready_tasks")
        self.assertEqual(
            result.unfinished,
            {"t4": "exit_code", "t5": "empty_diff", "t9": "unroutable", "t10": "blocked:t4"},
        )

        journal = result.journal
        self.assertEqual(len(journal.of("needs_owner", task="t9", reason="no_route")), 1)

        self.assertEqual(int(result.sandbox.git_out("rev-list", "--count", "main")), 4)

        alpha_lines = (result.sandbox.root / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(alpha_lines[0], "# t8 header")
        self.assertEqual(alpha_lines[-2:], ["# t1 line 1", "# t1 line 2"])

        wave1_started = journal.of("wave_started", wave=1)
        self.assertEqual(len(wave1_started), 1)
        expected_base = result.sandbox.head("main")
        # база волны 1 — HEAD main после барьера волны 0: только t1 и t2 в неё
        # вошли (t8 меняет main позже, в барьере волны 1), проверяем через число
        # предков базы.
        base_ancestors = int(
            result.sandbox.git_out("rev-list", "--count", wave1_started[0]["base"])
        )
        self.assertEqual(base_ancestors, 3)
        self.assertEqual(int(result.sandbox.git_out("rev-list", "--count", expected_base)), 4)

        for task_id in ("t1", "t2", "t8"):
            self.assertFalse((result.sandbox.root / ".worktrees" / task_id).exists())
        for task_id in ("t4", "t5"):
            self.assertTrue((result.sandbox.root / ".worktrees" / task_id).exists())

        stopped = journal.of("stopped")
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["reason"], "no_ready_tasks")
        self.assertEqual(stopped[0]["wave"], 2)
        self.assertNotIn("cycles", stopped[0])
        self.assertEqual(journal.events[-1]["event"], "stopped")

        json.dumps(journal.events)

    # -- 6a. цикл зависимостей — стоп до первого диспетча, событие stopped с cycles --------

    def test_cycle_stops_run_with_event(self):
        tasks = [
            Task(id="a", profile="append", write_scope=["pkg/alpha.py"], deps=["c"]),
            Task(id="b", profile="append", write_scope=["pkg/beta.py"], deps=["a"]),
            Task(id="c", profile="append", write_scope=["pkg/gamma.py"], deps=["b"]),
        ]
        result = run(Scenario(tasks=tasks), stand_tmpdir(self), RunConfig(timeout=5))
        self.addCleanup(self._revoke_running, result.dispatcher)

        self.assertEqual(result.stopped, "cycle")
        self.assertFalse(result.journal.of("wave_started"))
        stopped = result.journal.of("stopped")
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["reason"], "cycle")
        self.assertEqual(stopped[0]["wave"], 0)
        self.assertEqual(stopped[0]["cycles"], [["a", "b", "c"]])

    # -- 6b. исчерпан max_waves — одно событие stopped без cycles --------

    def test_max_waves_stops_run_with_event(self):
        tasks = [Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"])]
        result = run(
            Scenario(tasks=tasks),
            stand_tmpdir(self),
            RunConfig(timeout=5, gates="sequential", ladder=False),
            max_waves=0,
        )
        self.addCleanup(self._revoke_running, result.dispatcher)

        self.assertEqual(result.stopped, "max_waves")
        stopped = result.journal.of("stopped")
        self.assertEqual(len(stopped), 1)
        self.assertEqual(stopped[0]["reason"], "max_waves")
        self.assertEqual(stopped[0]["wave"], 0)
        self.assertNotIn("cycles", stopped[0])

    # -- 7. пустая волна не гоняет интеграцию ----------------------------------------------------------

    def test_empty_wave_no_integration(self):
        tasks = [Task(id="t4", profile="crash", write_scope=["pkg/delta.py"])]
        sandbox, scenario, journal, dispatcher = self._build(tasks)

        dispatcher.dispatch(["t4"])
        dispatcher.wait(deadline=15)

        result = run_barrier(sandbox, scenario, dispatcher, journal, wave=0, task_ids=["t4"])

        self.assertIsNone(result.integration)
        self.assertEqual(result.merged, [])
        self.assertFalse(journal.of("integration_green"))
        self.assertFalse(journal.of("integration_red"))
        self.assertTrue(journal.of("wave_finished"))


if __name__ == "__main__":
    unittest.main()
