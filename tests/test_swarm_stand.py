"""Сквозной прогон стенда роя одной командой.

Запустить весь стенд:

    python3 -m unittest discover tests -p 'test_swarm_stand*.py'

Запустить только этот сквозной сценарий:

    python3 -m unittest tests.test_swarm_stand

`SWARM_STAND_KEEP=1` — не удалять песочницу после теста (путь печатается в stderr).
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.swarm_stand.dispatch import RunConfig
from tests.swarm_stand.run import run
from tests.swarm_stand.scenario import Scenario, Task
from tests.swarm_stand.sandbox import DEFAULT_FILES
from tests.swarm_stand.testing import require_git


def _scenario() -> Scenario:
    return Scenario(
        tasks=[
            Task(id="t1", profile="append", write_scope=["pkg/alpha.py"]),
            Task(id="t2", profile="rewrite", write_scope=["pkg/beta.py"]),
            Task(
                id="t3",
                profile="scope_break",
                write_scope=["pkg/gamma.py"],
                foreign="pkg/alpha.py",
            ),
            Task(id="t4", profile="crash", write_scope=["pkg/delta.py"]),
            Task(id="t5", profile="empty", write_scope=["pkg/delta.py"], deps=["t2"]),
            Task(id="t6", profile="red_tests", write_scope=["pkg/beta.py"], deps=["t2"]),
            Task(id="t7", profile="hang", write_scope=["pkg/gamma.py"], deps=["t3"]),
            Task(id="t8", profile="prepend", write_scope=["pkg/alpha.py"], deps=["t1"]),
            Task(id="t9", profile="append", write_scope=["pkg/epsilon.py"], launch_route=None),
            Task(id="t10", profile="append", write_scope=["pkg/delta.py"], deps=["t4"]),
            Task(
                id="t11",
                profile="scope_break",
                write_scope=["pkg/epsilon.py"],
                deps=["t1"],
                foreign="pkg/alpha.py",
                launch_route="xhigh-pipeline",
            ),
        ]
    )


class SwarmStandTests(unittest.TestCase):
    """`test_full_run` и `test_budget` делят один прогон (`setUpClass`) — сценарий
    тяжёлый (11 задач, три волны, реальные git-процессы), гонять его дважды
    удвоило бы время без всякой пользы: обе проверки читают один и тот же
    `RunResult`."""

    @classmethod
    def setUpClass(cls):
        require_git(cls)
        scenario = _scenario()
        cls._tmp = Path(tempfile.mkdtemp(prefix="swarm-stand-"))

        started = time.monotonic()
        cls.result = run(scenario, cls._tmp, RunConfig(timeout=3.0, gates="sequential"))
        cls.elapsed = time.monotonic() - started

    @classmethod
    def tearDownClass(cls):
        for task_id in list(cls.result.dispatcher.running()):
            try:
                cls.result.dispatcher.revoke(task_id, kill=True, reason="test-cleanup")
            except RuntimeError:
                pass

        if os.environ.get("SWARM_STAND_KEEP") == "1":
            print(f"SWARM_STAND_KEEP=1: {cls._tmp}", file=sys.stderr)
        else:
            shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_full_run(self):
        result = self.result
        self.assertLess(self.elapsed, 30.0, f"test_full_run уложился в {self.elapsed:.1f}с")

        journal = result.journal
        sandbox = result.sandbox
        dispatcher = result.dispatcher

        # -- волны и остановка ----------------------------------------------------------

        self.assertEqual(
            result.waves,
            [["t1", "t2", "t3", "t4"], ["t3", "t5", "t6", "t8", "t11"], ["t7"]],
        )
        self.assertEqual(result.stopped, "no_ready_tasks")

        # -- слияние и итог ----------------------------------------------------------

        self.assertEqual(result.merged, ["t1", "t2", "t3", "t8", "t11"])
        self.assertEqual(int(sandbox.git_out("rev-list", "--count", "main")), 6)
        self.assertEqual(sandbox.git_out("log", "--merges", "main"), "")

        self.assertEqual(
            result.unfinished,
            {
                "t4": "exit_code",
                "t5": "empty_diff",
                "t6": "tests_red",
                "t7": "timeout",
                "t9": "unroutable",
                "t10": "blocked:t4",
            },
        )

        # -- лестница реакций ----------------------------------------------------------

        dropped = journal.of("dropped")
        held = journal.of("held")
        self.assertEqual(len(dropped), 1)
        self.assertEqual(len(held), 1)
        self.assertEqual(dropped[0]["task"], "t3")
        self.assertEqual(dropped[0]["owner"], "t1")
        self.assertEqual(held[0]["task"], "t11")
        self.assertEqual(held[0]["owner"], "t3")

        resumed = journal.last("resumed", task="t11")
        self.assertIsNotNone(resumed)
        self.assertEqual(resumed["generation"], 1)

        self.assertFalse(journal.of("conflict_resolved"))
        self.assertFalse(journal.of("report_rejected"))

        needs_owner = journal.of("needs_owner")
        self.assertEqual(len(needs_owner), 1)
        self.assertEqual(needs_owner[0]["task"], "t9")
        self.assertEqual(needs_owner[0]["reason"], "no_route")

        # -- t7: ровно два таймаута, никто другой не таймаутит ----------------------------------------------------------

        t7_revoked_timeout = journal.of("revoked", task="t7", reason="timeout")
        self.assertEqual(len(t7_revoked_timeout), 2)
        for r in t7_revoked_timeout:
            self.assertTrue(r["killed"])
        t7_failed = journal.last("failed", task="t7")
        self.assertIsNotNone(t7_failed)
        self.assertEqual(t7_failed["reason"], "timeout")

        for task_id in ("t1", "t2", "t3", "t4", "t5", "t6", "t8", "t9", "t10", "t11"):
            self.assertFalse(
                journal.of("revoked", task=task_id, reason="timeout"),
                f"неожиданный таймаут у {task_id} — раздутый тик или маленький timeout",
            )

        t6_failed = journal.last("failed", task="t6")
        self.assertIsNotNone(t6_failed)
        self.assertEqual(t6_failed["reason"], "tests_red")

        # -- содержимое файлов в main ----------------------------------------------------------

        alpha_lines = (sandbox.root / "pkg" / "alpha.py").read_text().splitlines()
        self.assertEqual(alpha_lines[0], "# t8 header")
        original_alpha_lines = DEFAULT_FILES["pkg/alpha.py"].splitlines()
        self.assertEqual(alpha_lines[1 : 1 + len(original_alpha_lines)], original_alpha_lines)
        self.assertEqual(
            alpha_lines[1 + len(original_alpha_lines) :],
            ["# t1 line 1", "# t1 line 2", "# t3 foreign", "# t11 foreign"],
        )

        beta_content = (sandbox.root / "pkg" / "beta.py").read_text()
        self.assertIn('return "beta-t2"', beta_content)

        gamma_lines = (sandbox.root / "pkg" / "gamma.py").read_text().splitlines()
        self.assertEqual(gamma_lines[-2:], ["# t3 line 1", "# t3 line 2"])

        epsilon_lines = (sandbox.root / "pkg" / "epsilon.py").read_text().splitlines()
        self.assertEqual(epsilon_lines[-2:], ["# t11 line 1", "# t11 line 2"])

        self.assertEqual(
            (sandbox.root / "pkg" / "delta.py").read_text(), DEFAULT_FILES["pkg/delta.py"]
        )

        # -- копилка расхождений ----------------------------------------------------------

        self.assertEqual({d["task"] for d in journal.discrepancies}, {"t3", "t11"})
        for d in journal.discrepancies:
            self.assertEqual(d["outside"], ["pkg/alpha.py"])

        for task_id in ("t3", "t11"):
            for done_event in journal.of("done", task=task_id):
                matches = [
                    d
                    for d in journal.discrepancies
                    if d["task"] == task_id and d["generation"] == done_event["generation"]
                ]
                self.assertTrue(
                    matches,
                    f"нет записи в discrepancies для {task_id} generation="
                    f"{done_event['generation']}",
                )

        # -- деревья ----------------------------------------------------------

        worktrees = {p.name for p in (sandbox.root / ".worktrees").iterdir()}
        self.assertEqual(worktrees, {"t4", "t5", "t6", "t7", "t7-g1"})

        branches = [
            line.strip()
            for line in sandbox.git_out("branch", "--list", "task/*").splitlines()
            if line.strip()
        ]
        branches = [b.lstrip("*+ ").strip() for b in branches]
        self.assertEqual(
            set(branches), {"task/t4", "task/t5", "task/t6", "task/t7", "task/t7-g1"}
        )

        # -- журнал сериализуется ----------------------------------------------------------

        json.dumps(journal.events)

    def test_budget(self):
        # t7 даёт два таймаута по 3с — бюджет держится с запасом даже с их учётом.
        self.assertLess(self.elapsed, 30.0)


if __name__ == "__main__":
    unittest.main()
