import os
import signal
import time
import unittest
from unittest import mock

from tests.swarm_stand import dispatch as dispatch_mod
from tests.swarm_stand.dispatch import Dispatcher, RunConfig, TaskState, check_report
from tests.swarm_stand.journal import Journal
from tests.swarm_stand.sandbox import build
from tests.swarm_stand.scenario import Scenario, Task, validate
from tests.swarm_stand.testing import require_git, stand_tmpdir


class DispatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_git(cls)

    # -- обвязка ----------------------------------------------------------

    def _make_stand(self, tasks, config):
        tmp = stand_tmpdir(self)
        scenario = Scenario(tasks=tasks)
        validate(scenario)
        sandbox = build(tmp / "repo", scenario)
        journal = Journal()
        dispatcher = Dispatcher(sandbox, scenario, config, journal)

        procs: list = []
        original_launch = dispatch_mod.launch

        def _capture(*args, **kwargs):
            proc = original_launch(*args, **kwargs)
            procs.append(proc)
            return proc

        patcher = mock.patch.object(dispatch_mod, "launch", side_effect=_capture)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._kill_all, procs)
        self.addCleanup(self._revoke_running, dispatcher)

        return sandbox, journal, dispatcher, procs

    def _revoke_running(self, dispatcher):
        for task_id in list(dispatcher.running()):
            try:
                dispatcher.revoke(task_id, kill=True, reason="test-cleanup")
            except RuntimeError:
                pass

    def _kill_all(self, procs):
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass

    # -- тесты ----------------------------------------------------------

    def test_normal_run(self):
        tasks = [
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"]),
        ]
        sandbox, journal, dispatcher, _procs = self._make_stand(tasks, RunConfig())

        main_sha = sandbox.head("main")
        dispatcher.dispatch(["t1", "t2"])
        dispatcher.wait(deadline=10)

        self.assertEqual(dispatcher.states["t1"].status, "done")
        self.assertEqual(dispatcher.states["t2"].status, "done")

        names = journal.names()
        self.assertEqual(names[:2], ["dispatched", "dispatched"])
        self.assertIn("first_change", names)
        for task_id in ("t1", "t2"):
            self.assertTrue(journal.of("first_change", task=task_id))
            self.assertTrue(journal.of("report_accepted", task=task_id))
            self.assertTrue(journal.of("done", task=task_id))

        d1 = journal.last("dispatched", task="t1")
        self.assertEqual(d1["generation"], 0)
        self.assertTrue(d1["tree"].endswith(".worktrees/t1"))
        self.assertEqual(d1["branch"], "task/t1")
        self.assertEqual(d1["base"], main_sha)

        self.assertEqual(dispatcher.touched_files("t1"), ["pkg/alpha.py"])
        self.assertEqual(journal.discrepancies, [])
        self.assertEqual(dispatcher.states["t1"].head, sandbox.head("task/t1"))

    def test_crash(self):
        tasks = [Task(id="t-crash", profile="crash", write_scope=["pkg/alpha.py"])]
        sandbox, journal, dispatcher, _procs = self._make_stand(tasks, RunConfig())

        dispatcher.dispatch(["t-crash"])
        dispatcher.wait(deadline=10)

        self.assertEqual(dispatcher.states["t-crash"].status, "failed")
        self.assertEqual(dispatcher.states["t-crash"].fail_reason, "exit_code")

        failed = journal.last("failed", task="t-crash")
        self.assertEqual(failed["exit_code"], 3)

        tree = dispatcher.tree_path("t-crash", 0)
        self.assertTrue(tree.exists())
        status = sandbox.git("status", "--porcelain", cwd=tree).stdout.strip()
        self.assertNotEqual(status, "")

        branch_check = sandbox.git(
            "rev-parse", "--verify", "task/t-crash", cwd=sandbox.root, check=False
        )
        self.assertEqual(branch_check.returncode, 0)

    def test_empty_diff(self):
        tasks = [Task(id="t-empty", profile="empty", write_scope=["pkg/alpha.py"])]
        _sandbox, journal, dispatcher, _procs = self._make_stand(tasks, RunConfig())

        dispatcher.dispatch(["t-empty"])
        dispatcher.wait(deadline=10)

        self.assertTrue(journal.of("report_accepted", task="t-empty"))
        self.assertEqual(dispatcher.states["t-empty"].status, "failed")
        self.assertEqual(dispatcher.states["t-empty"].fail_reason, "empty_diff")

    def test_scope_violation(self):
        tasks = [
            Task(
                id="t-scope",
                profile="scope_break",
                write_scope=["pkg/gamma.py"],
                foreign="pkg/alpha.py",
            )
        ]
        _sandbox, journal, dispatcher, _procs = self._make_stand(tasks, RunConfig())

        dispatcher.dispatch(["t-scope"])
        dispatcher.wait(deadline=10)

        self.assertEqual(dispatcher.states["t-scope"].status, "done")

        violation = journal.last("scope_violation", task="t-scope")
        self.assertEqual(violation["declared"], ["pkg/gamma.py"])
        self.assertEqual(violation["touched"], ["pkg/alpha.py", "pkg/gamma.py"])
        self.assertEqual(violation["outside"], ["pkg/alpha.py"])

        self.assertEqual(len(journal.discrepancies), 1)
        self.assertEqual(
            journal.discrepancies[0],
            {
                "task": "t-scope",
                "generation": 0,
                "declared": ["pkg/gamma.py"],
                "touched": ["pkg/alpha.py", "pkg/gamma.py"],
                "outside": ["pkg/alpha.py"],
            },
        )

    def test_timeout_with_kill(self):
        task_id = "t-hang"
        tasks = [Task(id=task_id, profile="hang", write_scope=["pkg/alpha.py"])]
        config = RunConfig(timeout=0.5, kill_on_timeout=True, retries_on_timeout=1, poll=0.05)
        _sandbox, journal, dispatcher, procs = self._make_stand(tasks, config)

        started = time.monotonic()
        dispatcher.dispatch([task_id])
        dispatcher.wait(deadline=10)
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 5.0)

        self.assertEqual(dispatcher.states[task_id].status, "failed")
        self.assertEqual(dispatcher.states[task_id].timeouts, 2)

        d0 = journal.last("dispatched", task=task_id, generation=0)
        fc0 = journal.last("first_change", task=task_id, generation=0)
        r0 = journal.last("revoked", task=task_id, generation=0)
        d1 = journal.last("dispatched", task=task_id, generation=1)
        r1 = journal.last("revoked", task=task_id, generation=1)
        f = journal.last("failed", task=task_id)

        for record in (d0, fc0, r0, d1, r1, f):
            self.assertIsNotNone(record)

        self.assertEqual(r0["reason"], "timeout")
        self.assertTrue(r0["killed"])
        self.assertEqual(d1["branch"], f"task/{task_id}-g1")
        self.assertTrue(d1["tree"].endswith(f".worktrees/{task_id}-g1"))
        self.assertEqual(f["reason"], "timeout")
        self.assertEqual(f["generation"], 2)

        seqs = [d0["seq"], fc0["seq"], r0["seq"], d1["seq"], r1["seq"], f["seq"]]
        self.assertEqual(seqs, sorted(seqs))

        self.assertEqual(len(procs), 2)
        for proc in procs:
            with self.assertRaises(ProcessLookupError):
                os.killpg(proc.pid, 0)

    def test_zombie(self):
        task_id = "t-zombie"
        tasks = [Task(id=task_id, profile="hang", write_scope=["pkg/alpha.py"])]
        config = RunConfig(timeout=30, kill_on_timeout=False, gates="manual")
        sandbox, journal, dispatcher, procs = self._make_stand(tasks, config)

        main_before = sandbox.head("main")

        dispatcher.dispatch([task_id])
        dispatcher.open_gate(task_id, "start")
        dispatcher.wait(
            until=lambda: dispatcher.states[task_id].first_change_tick is not None,
            deadline=10,
        )

        proc0 = procs[0]

        dispatcher.revoke(task_id, kill=False, reason="manual")
        revoked0 = journal.last("revoked", task=task_id, generation=0)
        self.assertEqual(revoked0["killed"], False)
        self.assertEqual(dispatcher.states[task_id].generation, 1)
        self.assertEqual(dispatcher.states[task_id].status, "pending")
        self.assertIsNone(proc0.poll())

        dispatcher.dispatch([task_id])
        d1 = journal.last("dispatched", task=task_id, generation=1)
        self.assertEqual(d1["branch"], f"task/{task_id}-g1")

        dispatcher.open_gate(task_id, "finish", generation=0)
        dispatcher.wait(until=lambda: journal.of("report_rejected"), deadline=10)

        rejected = journal.last("report_rejected")
        self.assertEqual(rejected["generation"], 0)
        self.assertEqual(rejected["current_generation"], 1)
        self.assertEqual(rejected["reason"], "stale_generation")

        branch0 = dispatcher.branch_name(task_id, 0)
        count0 = int(sandbox.git_out("rev-list", "--count", f"{main_before}..{branch0}"))
        self.assertEqual(count0, 1)
        self.assertEqual(sandbox.head("main"), main_before)

        self.assertEqual(dispatcher.states[task_id].status, "running")
        self.assertIsNone(dispatcher.states[task_id].head)

        dispatcher.open_gate(task_id, "start")
        dispatcher.open_gate(task_id, "finish")
        dispatcher.wait(deadline=10)

        self.assertEqual(dispatcher.states[task_id].status, "done")
        self.assertEqual(dispatcher.states[task_id].generation, 1)
        self.assertEqual(dispatcher.states[task_id].branch, f"task/{task_id}-g1")
        self.assertEqual(dispatcher.states[task_id].head, sandbox.head(f"task/{task_id}-g1"))

        branch1 = dispatcher.branch_name(task_id, 1)
        count1 = int(sandbox.git_out("rev-list", "--count", f"{main_before}..{branch1}"))
        self.assertEqual(count0, 1)
        self.assertEqual(count1, 1)
        self.assertEqual(sandbox.head("main"), main_before)
        self.assertEqual(journal.discrepancies, [])

    def test_sequential_gates(self):
        def _order(seed):
            tasks = [
                Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
                Task(id="t2", profile="append", write_scope=["pkg/beta.py"]),
                Task(id="t3", profile="append", write_scope=["pkg/gamma.py"]),
            ]
            _sandbox, _journal, dispatcher, _procs = self._make_stand(
                tasks, RunConfig(gates="sequential")
            )
            dispatcher.dispatch(["t1", "t2", "t3"])
            dispatcher.wait(deadline=10)
            return (
                dispatcher.states["t1"].first_change_tick,
                dispatcher.states["t2"].first_change_tick,
                dispatcher.states["t3"].first_change_tick,
            )

        t1a, t2a, t3a = _order(1)
        self.assertLess(t1a, t2a)
        self.assertLess(t2a, t3a)

        t1b, t2b, t3b = _order(2)
        self.assertLess(t1b, t2b)
        self.assertLess(t2b, t3b)

    def test_external_revoke(self):
        task_id = "t-manual"
        tasks = [Task(id=task_id, profile="append", write_scope=["pkg/alpha.py"])]
        config = RunConfig(gates="manual")
        _sandbox, journal, dispatcher, procs = self._make_stand(tasks, config)

        dispatcher.dispatch([task_id])
        dispatcher.tick()

        proc0 = procs[0]
        dispatcher.revoke(task_id, kill=True, reason="manual")
        self.assertIsNotNone(proc0.poll())
        self.assertEqual(dispatcher.states[task_id].status, "pending")
        self.assertEqual(dispatcher.states[task_id].generation, 1)

        dispatcher.dispatch([task_id])
        dispatcher.open_gate(task_id, "start")
        dispatcher.open_gate(task_id, "finish")
        dispatcher.wait(deadline=10)

        self.assertEqual(dispatcher.states[task_id].status, "done")
        self.assertEqual(dispatcher.states[task_id].generation, 1)
        self.assertEqual(dispatcher.states[task_id].branch, f"task/{task_id}-g1")

    def test_check_report(self):
        state = TaskState(task_id="t", status="running", generation=2, dispatch_id="d1")

        self.assertEqual(
            check_report(state, {"generation": 1, "dispatch_id": "d1"}), "stale_generation"
        )
        self.assertEqual(
            check_report(state, {"generation": 2, "dispatch_id": "other"}), "unknown_dispatch"
        )
        self.assertIsNone(check_report(state, {"generation": 2, "dispatch_id": "d1"}))

    def test_wait_does_not_hang(self):
        task_id = "t-hang-wait"
        tasks = [Task(id=task_id, profile="hang", write_scope=["pkg/alpha.py"])]
        config = RunConfig(timeout=60)
        _sandbox, _journal, dispatcher, _procs = self._make_stand(tasks, config)

        dispatcher.dispatch([task_id])

        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            dispatcher.wait(deadline=0.5)
        elapsed = time.monotonic() - started

        self.assertIn(task_id, str(ctx.exception))
        self.assertLess(elapsed, 5.0)

        dispatcher.revoke(task_id, kill=True, reason="test")
        self.assertEqual(dispatcher.states[task_id].status, "pending")


if __name__ == "__main__":
    unittest.main()
