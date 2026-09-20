import os
import signal
import subprocess
import sys
import time
import unittest

from tests.swarm_stand import sandbox as sandbox_mod
from tests.swarm_stand import workers
from tests.swarm_stand.scenario import Scenario, Task, scopes_intersect, validate
from tests.swarm_stand.testing import make_worktree, require_git, stand_tmpdir


def _wait_for_report(proc, report_path, timeout=10):
    proc.wait(timeout=timeout)
    return workers.read_report(report_path)


class WorkerProfilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        require_git(cls)

    def _build_single_task(self, tmp, profile, *, foreign=None):
        task_id = f"t-{profile}"
        write_scope = ["pkg/alpha.py"]
        task = Task(id=task_id, profile=profile, write_scope=write_scope, foreign=foreign)
        scenario = Scenario(tasks=[task])
        validate(scenario)
        sandbox_root = tmp / "sandbox"
        sandbox = sandbox_mod.build(sandbox_root, scenario)
        branch = f"task/{task_id}"
        worktree = sandbox_root / ".worktrees" / task_id
        make_worktree(sandbox, task_id, branch=branch, path=worktree)
        return sandbox, task, branch, worktree

    def _run(self, sandbox, task, worktree, tmp, **kwargs):
        report_path = tmp / f"{task.id}.report.json"
        env = workers.worker_env(
            sandbox,
            task,
            dispatch_id="d1",
            generation=0,
            worktree=worktree,
            branch=f"task/{task.id}",
            report_path=report_path,
            **kwargs,
        )
        proc = workers.launch(sandbox, task, worktree=worktree, env=env)
        return proc, report_path

    def test_append(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "append")
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")
        self.assertIsInstance(report["generation"], int)
        self.assertEqual(report["head"], sandbox.head(branch))

        diff = sandbox.git_out("diff", "--name-only", "main..task/t-append")
        self.assertEqual(diff, "pkg/alpha.py")

        content = (worktree / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(content[-2:], ["# t-append line 1", "# t-append line 2"])

        status = sandbox.git_out("status", "--porcelain", cwd=worktree)
        self.assertEqual(status, "")

    def test_prepend(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "prepend")
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")

        original = sandbox_mod.DEFAULT_FILES["pkg/alpha.py"]
        content = (worktree / "pkg" / "alpha.py").read_text()
        lines = content.split("\n")
        self.assertEqual(lines[0], "# t-prepend header")
        self.assertEqual("\n".join(lines[1:]), original)

    def test_rewrite(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "rewrite")
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")

        content = (worktree / "pkg" / "alpha.py").read_text()
        self.assertIn('return "alpha-t-rewrite"', content)
        self.assertNotIn("alpha-v1", content)

        result = subprocess.run(
            sandbox.test_command(), cwd=worktree, env=sandbox.env(),
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_scope_break(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(
            tmp, "scope_break", foreign="pkg/beta.py"
        )
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")

        diff = sandbox.git_out(
            "diff", "--name-only", "main..task/t-scope_break", cwd=sandbox.root
        )
        self.assertEqual(set(diff.splitlines()), {"pkg/alpha.py", "pkg/beta.py"})

        beta = (worktree / "pkg" / "beta.py").read_text().splitlines()
        self.assertEqual(beta[-1], "# t-scope_break foreign")

    def test_crash(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "crash")
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 3)
        self.assertIsNone(workers.read_report(report_path))

        status = sandbox.git_out("status", "--porcelain", cwd=worktree)
        self.assertNotEqual(status, "")

        count = sandbox.git_out(
            "rev-list", "--count", "main..task/t-crash", cwd=sandbox.root
        )
        self.assertEqual(count, "0")

    def test_empty(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "empty")
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")

        count = sandbox.git_out(
            "rev-list", "--count", "main..task/t-empty", cwd=sandbox.root
        )
        self.assertEqual(count, "0")

        status = sandbox.git_out("status", "--porcelain", cwd=worktree)
        self.assertEqual(status, "")

    def test_red_tests(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "red_tests")
        proc, report_path = self._run(sandbox, task, worktree, tmp)
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")

        result = subprocess.run(
            sandbox.test_command(), cwd=worktree, env=sandbox.env(),
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

        result_main = subprocess.run(
            sandbox.test_command(), cwd=sandbox.root, env=sandbox.env(),
            capture_output=True, text=True,
        )
        self.assertEqual(result_main.returncode, 0, result_main.stdout + result_main.stderr)

    def test_hang(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "hang")
        proc, report_path = self._run(sandbox, task, worktree, tmp)

        time.sleep(0.5)
        self.assertIsNone(proc.poll())
        self.assertIsNone(workers.read_report(report_path))

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=10)

    def test_gate_finish(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "hang")
        gate_finish = tmp / "gate-finish"
        proc, report_path = self._run(
            sandbox, task, worktree, tmp, gate_finish=gate_finish
        )

        time.sleep(0.3)
        self.assertIsNone(proc.poll())
        self.assertIsNone(workers.read_report(report_path))
        status = sandbox.git_out("status", "--porcelain", cwd=worktree)
        self.assertNotEqual(status, "")

        gate_finish.write_text("go")
        proc.wait(timeout=10)
        report = workers.read_report(report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")
        count = sandbox.git_out(
            "rev-list", "--count", "main..task/t-hang", cwd=sandbox.root
        )
        self.assertEqual(count, "1")

    def test_gate_start(self):
        tmp = stand_tmpdir(self)
        sandbox, task, branch, worktree = self._build_single_task(tmp, "append")
        gate_start = tmp / "gate-start"
        proc, report_path = self._run(
            sandbox, task, worktree, tmp, gate_start=gate_start
        )

        time.sleep(0.3)
        self.assertIsNone(proc.poll())
        status = sandbox.git_out("status", "--porcelain", cwd=worktree)
        self.assertEqual(status, "")

        gate_start.write_text("go")
        report = _wait_for_report(proc, report_path)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(report["status"], "done")
        content = (worktree / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(content[-2:], ["# t-append line 1", "# t-append line 2"])

    def test_prepend_append_merge_clean(self):
        tmp = stand_tmpdir(self)
        tasks = [
            Task(id="t-prepend", profile="prepend", write_scope=["pkg/alpha.py"]),
            Task(id="t-append", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t-append2", profile="append", write_scope=["pkg/alpha.py"]),
        ]
        scenario = Scenario(tasks=tasks)
        validate(scenario)
        sandbox_root = tmp / "sandbox"
        sandbox = sandbox_mod.build(sandbox_root, scenario)

        procs = []
        for task in tasks:
            branch = f"task/{task.id}"
            worktree = sandbox_root / ".worktrees" / task.id
            make_worktree(sandbox, task.id, branch=branch, path=worktree)
            proc, report_path = self._run(sandbox, task, worktree, tmp)
            procs.append((task, proc, report_path))

        for task, proc, report_path in procs:
            report = _wait_for_report(proc, report_path)
            self.assertEqual(proc.returncode, 0, task.id)
            self.assertEqual(report["status"], "done", task.id)

        clean = sandbox.git(
            "merge-tree", "--write-tree", "task/t-prepend", "task/t-append",
            cwd=sandbox.root, check=False,
        )
        self.assertEqual(clean.returncode, 0, clean.stdout + clean.stderr)

        conflict = sandbox.git(
            "merge-tree", "--write-tree", "task/t-append", "task/t-append2",
            cwd=sandbox.root, check=False,
        )
        self.assertEqual(conflict.returncode, 1, conflict.stdout + conflict.stderr)

    def test_validate(self):
        base_task = dict(write_scope=["pkg/alpha.py"])

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[Task(id="a", profile="bogus", **base_task)]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", **base_task),
                Task(id="a", profile="append", **base_task),
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", deps=["missing"], **base_task),
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", deps=["a"], **base_task),
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[Task(id="a", profile="append", write_scope=[])]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", write_scope=["/pkg/alpha.py"])
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", write_scope=["pkg/../alpha.py"])
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="scope_break", **base_task)
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="scope_break", write_scope=["pkg/"],
                     foreign="pkg/alpha.py")
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", write_scope=["pkg/"])
            ]))

        with self.assertRaises(ValueError):
            validate(Scenario(tasks=[
                Task(id="a", profile="append", write_scope=["pkg/nope.py"])
            ]))

        # проходит
        validate(Scenario(tasks=[
            Task(id="a", profile="append", write_scope=["pkg/", "pkg/alpha.py"])
        ]))
        validate(Scenario(tasks=[
            Task(id="a", profile="empty", write_scope=["pkg/"])
        ]))

    def test_scopes_intersect(self):
        self.assertTrue(scopes_intersect(["pkg/"], ["pkg/a.py"]))
        self.assertFalse(scopes_intersect(["pkg/a.py"], ["pkg/b.py"]))
        self.assertTrue(scopes_intersect(["pkg/a.py"], ["pkg/a.py"]))

    def test_sandbox_test_command(self):
        tmp = stand_tmpdir(self)

        default_scenario = Scenario(tasks=[
            Task(id="a", profile="append", write_scope=["pkg/alpha.py"])
        ])
        default_sandbox = sandbox_mod.build(tmp / "sandbox-default", default_scenario)
        self.assertEqual(
            default_sandbox.test_command(),
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
        )

        custom = ["echo", "custom"]
        custom_scenario = Scenario(
            tasks=[Task(id="a", profile="append", write_scope=["pkg/alpha.py"])],
            test_command=custom,
        )
        custom_sandbox = sandbox_mod.build(tmp / "sandbox-custom", custom_scenario)
        self.assertEqual(custom_sandbox.test_command(), custom)


if __name__ == "__main__":
    unittest.main()
