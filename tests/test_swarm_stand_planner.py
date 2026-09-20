import unittest

from tests.swarm_stand.planner import ready_now, waves
from tests.swarm_stand.scenario import Scenario, Task


def _task(task_id, files, deps=None, launch_route="nano-pipeline", tree_key=None):
    return Task(
        id=task_id,
        profile="append",
        write_scope=list(files),
        deps=list(deps or []),
        launch_route=launch_route,
        tree_key=tree_key,
    )


EMPTY_RESULT_EXTRAS = {"cycles": [], "unroutable": [], "blocked": {}, "resource_blocks": []}


class PlannerTests(unittest.TestCase):
    def test_chain(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"], deps=["b"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(
            result,
            {"waves": [["a"], ["b"], ["c"]], **EMPTY_RESULT_EXTRAS},
        )

    def test_diamond(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"], deps=["a"])
        d = _task("d", ["pkg/delta.py"], deps=["b", "c"])
        scenario = Scenario(tasks=[a, b, c, d])

        result = waves(scenario)

        self.assertEqual(
            result,
            {"waves": [["a"], ["b", "c"], ["d"]], **EMPTY_RESULT_EXTRAS},
        )

    def test_independent(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"])
        c = _task("c", ["pkg/gamma.py"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(
            result,
            {"waves": [["a", "b", "c"]], **EMPTY_RESULT_EXTRAS},
        )

    def test_cycle(self):
        a = _task("a", ["pkg/alpha.py"], deps=["c"])
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"], deps=["b"])
        d = _task("d", ["pkg/delta.py"])
        scenario = Scenario(tasks=[a, b, c, d])

        result = waves(scenario)

        self.assertEqual(result["cycles"], [["a", "b", "c"]])
        self.assertEqual(result["waves"], [])

    def test_scope_overlap(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"])
        c = _task("c", ["pkg/alpha.py"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(result["waves"], [["a", "b"], ["c"]])
        self.assertEqual(result["resource_blocks"], [["a", "c"]])

    def test_directory_covers_file(self):
        a = _task("a", ["pkg/alpha.py", "pkg/"])
        b = _task("b", ["pkg/beta.py"])
        scenario = Scenario(tasks=[a, b])

        result = waves(scenario)

        self.assertEqual(result["waves"], [["a"], ["b"]])
        self.assertEqual(result["resource_blocks"], [["a", "b"]])

    def test_invalid_scenario_raises(self):
        a = _task("a", ["pkg/"])
        scenario = Scenario(tasks=[a])

        with self.assertRaises(ValueError):
            waves(scenario)

    def test_shift_pulls_dependents(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/alpha.py"])
        c = _task("c", ["pkg/gamma.py"], deps=["b"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(result["waves"], [["a"], ["b"], ["c"]])

    def test_three_on_one_file(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/alpha.py"])
        c = _task("c", ["pkg/alpha.py"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(result["waves"], [["a"], ["b"], ["c"]])
        self.assertEqual(
            result["resource_blocks"],
            [["a", "b"], ["a", "c"], ["b", "c"]],
        )

    def test_tree_key(self):
        a = _task("a", ["pkg/alpha.py"], tree_key="main")
        b = _task("b", ["pkg/beta.py"], tree_key="main")
        c = _task("c", ["pkg/gamma.py"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(result["waves"], [["a", "c"], ["b"]])

    def test_unroutable(self):
        a = _task("a", ["pkg/alpha.py"], launch_route=None)
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario)

        self.assertEqual(result["waves"], [["c"]])
        self.assertEqual(result["unroutable"], ["a"])
        self.assertEqual(result["blocked"], {"b": "a"})

    def test_done_is_excluded(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"], deps=["b"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario, done={"a"})

        self.assertEqual(result["waves"], [["b"], ["c"]])

    def test_failed_blocks(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"], deps=["b"])
        scenario = Scenario(tasks=[a, b, c])

        result = waves(scenario, failed={"a"})

        self.assertEqual(result["waves"], [])
        self.assertEqual(result["blocked"], {"b": "a", "c": "b"})

    def test_done_resolves_conflict(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/alpha.py"])
        scenario = Scenario(tasks=[a, b])

        result = waves(scenario, done={"a"})

        self.assertEqual(result["waves"], [["b"]])
        self.assertEqual(result["resource_blocks"], [])

    def test_deterministic(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/alpha.py"])
        c = _task("c", ["pkg/alpha.py"])
        scenario = Scenario(tasks=[a, b, c])

        results = [waves(scenario) for _ in range(10)]

        for result in results[1:]:
            self.assertEqual(result, results[0])

    def test_ready_now(self):
        a = _task("a", ["pkg/alpha.py"])
        b = _task("b", ["pkg/beta.py"], deps=["a"])
        c = _task("c", ["pkg/gamma.py"], deps=["a"])
        d = _task("d", ["pkg/delta.py"], deps=["b", "c"])
        scenario = Scenario(tasks=[a, b, c, d])

        self.assertEqual(ready_now(scenario), ["a"])
        self.assertEqual(ready_now(scenario, done={"a"}), ["b", "c"])


if __name__ == "__main__":
    unittest.main()
