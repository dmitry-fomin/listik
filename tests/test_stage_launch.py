"""Режим роя: команда роли, первая строка, claim до процесса (listik-09c5)."""
from __future__ import annotations

import pathlib
import sys
import tempfile
import textwrap
import unittest

from unittest import mock

from listik import errors as errors_mod
from listik import launcher as launcher_mod
from listik import routes as routes_mod
from listik import routes_store
from listik import stage_launch
from listik import store
from tests.helpers import TempDbTestCase
from tests.test_autostart import AutostartTestCase


def role(command, harness="codex", provider="openai"):
    return {"provider": provider, "label": harness, "title": harness,
            "command": command, "harness": harness}


class ParseAnswerTests(unittest.TestCase):
    def test_exact_first_line(self) -> None:
        self.assertEqual(stage_launch.parse_answer("готово\nхвост\n".encode()), ("готово", "хвост"))

    def test_blank_first_line_is_empty(self) -> None:
        self.assertEqual(stage_launch.parse_answer("\nготово\n".encode()), ("", "готово"))

    def test_unfinished_line_over_cap_is_empty(self) -> None:
        data = b"x" * (stage_launch.ANSWER_CAP + 10)
        self.assertEqual(stage_launch.parse_answer(data), ("", ""))

    def test_read_answer_stops_at_cap(self) -> None:
        handle = tempfile.NamedTemporaryFile(delete=False)
        path = pathlib.Path(handle.name)
        try:
            handle.write("готово\n".encode() + b"x" * (stage_launch.ANSWER_CAP + 50))
            handle.close()
            first, rest = stage_launch.read_answer(str(path))
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(first, "готово")
        self.assertLessEqual(len(rest.encode()), stage_launch.ANSWER_CAP)

    def test_stderr_is_not_the_channel(self) -> None:
        self.assertEqual(stage_launch.classify("impl", "зелёный"), "bad")
        self.assertEqual(stage_launch.classify("judge", "готово"), "bad")
        self.assertEqual(stage_launch.classify("judge", "зелёный"), "зелёный")


class SwarmLaunchTests(AutostartTestCase):
    def script(self, body: str) -> pathlib.Path:
        path = self.tmp_path / f"say-{len(list(self.tmp_path.glob('say-*.py')))}.py"
        path.write_text(textwrap.dedent(body), encoding="utf-8")
        return path

    def say(self, line: str, *extra: str) -> list[str]:
        path = self.script(
            "import sys\n"
            "sys.stdout.write(sys.argv[1] + '\\n')\n"
            "for arg in sys.argv[2:]:\n"
            "    sys.stdout.write(arg + '\\n')\n")
        return [sys.executable, str(path), line, *extra]

    def swarm(self, command, *, roles=None, key="swarm-route"):
        roles = roles or {"spec": role(command), "impl": role(command)}
        record = {"key": key, "kind": "pipeline", "title": "Рой", "hint": "",
                  "visible": True, "driver": "swarm", "roles": roles}
        routes_mod.validate({"version": 1, "routes": [record]})
        routes_store.create_route(self.conn, key=key, kind="pipeline", title="Рой",
                                  hint="", visible=True, roles=roles, driver="swarm")
        return key

    def task(self, route, *, project_path=None):
        path = project_path or self.tmp_path
        self.make_project("proj", path)
        return self.make_task(project="proj", route=route, worktree=str(path))

    def test_ready_advances_skips_missing_critic_and_clears_holder(self) -> None:
        command = self.say("готово")
        route = self.swarm(command, roles={"spec": role(command), "impl": role(command, "grok")})
        task = self.task(route)
        self.assertIsNone(self.launch(task["id"]))
        self.join_tracker(task["id"])
        done = store.get_task(self.conn, task["id"])
        self.assertEqual(done["launch_driver"], "swarm")
        self.assertEqual(done["stage"], "s3-impl")
        self.assertFalse(done["holder"])
        self.assertIsNone(done["launched_by"])
        claims = [e for e in done["events"] if e.get("kind") == "claim"
                  and e.get("actor") == "agent:codex"]
        self.assertTrue(claims, done["events"])
        journal = " ".join(c["text"] for c in self.comments(task["id"], "journal"))
        self.assertIn("карточку взял Listik", journal)
        self.assertIn("этап s1-spec → s3-impl", journal)
        self.assertNotIn("автостарт: процесс", journal)

    def test_blank_first_line_asks_and_does_not_advance(self) -> None:
        path = self.script("import sys\nsys.stdout.write('\\nготово\\n')\n")
        route = self.swarm([sys.executable, str(path)])
        task = self.task(route)
        self.launch(task["id"])
        self.join_tracker(task["id"])
        done = store.get_task(self.conn, task["id"])
        self.assertEqual(done["stage"], "s1-spec")
        self.assertTrue(done["needs_owner"])
        self.assertIsNone(done["launched_by"])
        question = [c for c in self.comments(task["id"]) if c["kind"] == "question"]
        self.assertTrue(question)
        self.assertIn("«пусто»", question[-1]["text"])

    def test_stderr_does_not_hide_stdout_answer(self) -> None:
        path = self.script(
            "import sys\nsys.stderr.write('noise\\n')\nsys.stdout.write('готово\\n')\n")
        route = self.swarm([sys.executable, str(path)],
                           roles={"spec": role([sys.executable, str(path)]),
                                  "impl": role([sys.executable, str(path)])})
        task = self.task(route)
        self.launch(task["id"])
        self.join_tracker(task["id"])
        self.assertEqual(store.get_task(self.conn, task["id"])["stage"], "s3-impl")

    def test_green_closes_and_red_returns_without_holder(self) -> None:
        green = self.say("зелёный")
        red = self.say("красный", "1. поправь проверку")
        route = self.swarm(green, roles={"judge": role(green)})
        task = self.task(route)
        self.seed(task["id"], stage="s4-judge")
        self.launch(task["id"])
        self.join_tracker(task["id"])
        closed = store.get_task(self.conn, task["id"])
        self.assertEqual(closed["status"], "done")
        verdicts = [c["text"] for c in self.comments(task["id"], "verdict")]
        self.assertIn("VERDICT: PASS", verdicts)

        route2 = self.swarm(red, roles={"judge": role(red), "impl": role(red)}, key="swarm-red")
        task2 = self.task(route2)
        self.seed(task2["id"], stage="s4-judge")
        self.launch(task2["id"])
        self.join_tracker(task2["id"])
        back = store.get_task(self.conn, task2["id"])
        self.assertEqual(back["stage"], "s3-impl")
        self.assertNotEqual(back["status"], "done")
        self.assertFalse(back["holder"])
        self.assertIsNone(back["launched_by"])
        self.assertTrue(any(c["text"].startswith("VERDICT: FAIL")
                            for c in self.comments(task2["id"], "verdict")))

    def test_slice_keeps_parent_and_promotes_blocks(self) -> None:
        flag = self.tmp_path / "sliced"
        path = self.script(
            "import sys, time\n"
            "from pathlib import Path\n"
            "flag = Path(sys.argv[1])\n"
            "for _ in range(200):\n"
            "    if flag.exists():\n"
            "        break\n"
            "    time.sleep(0.02)\n"
            "sys.stdout.write('готово\\n')\n")
        command = [sys.executable, str(path), str(flag)]
        route = self.swarm(command, roles={"spec": role(command), "impl": role(command)})
        parent = self.task(route)
        self.launch(parent["id"])
        child = store.create_task(self.conn, title="порция", project="proj", parent=parent["id"])
        self.conn.execute(
            "UPDATE tasks SET launch_driver = 'skill' WHERE id = ?", (child["id"],))
        self.conn.commit()
        other = store.create_task(self.conn, title="чужая", project="proj")
        store.add_dep(self.conn, child["id"], other["id"], "blocks", created_by="agent:codex")
        sib = store.create_task(self.conn, title="вторая", project="proj", parent=parent["id"])
        store.add_dep(self.conn, sib["id"], child["id"], "blocks", created_by="agent:codex")
        flag.write_text("1", encoding="utf-8")
        self.join_tracker(parent["id"])
        parent_done = store.get_task(self.conn, parent["id"])
        self.assertEqual(parent_done["stage"], "s1-spec")
        self.assertNotEqual(parent_done["status"], "done")
        self.assertIsNone(parent_done["launched_by"])
        self.assertTrue(parent_done["has_portions"])
        moved = store.get_task(self.conn, child["id"])
        self.assertEqual(moved["stage"], "s3-impl")
        self.assertEqual(moved["launch_route"], route)
        self.assertEqual(moved["launch_driver"], "swarm")
        edge = self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?",
            (sib["id"], child["id"])).fetchone()
        self.assertEqual(edge["dep_type"], "blocks")
        outside = self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?",
            (child["id"], other["id"])).fetchone()
        self.assertEqual(outside["dep_type"], "suggested-blocks")

    def test_skill_and_direct_do_not_read_the_first_line(self) -> None:
        command = self.say("готово")
        self.set_routes(
            {"key": "skill-route", "kind": "pipeline", "title": "Скил", "hint": "",
             "visible": True,
             "roles": {"impl": {"provider": "claude", "label": "C", "title": "C"}},
             "command": command},
            {"key": "direct-route", "kind": "direct", "title": "Прямой", "hint": "",
             "visible": True, "harness": "codex", "command": command},
        )
        skill = self.task("skill-route")
        self.launch(skill["id"])
        fresh = store.get_task(self.conn, skill["id"])
        self.assertNotEqual(fresh["holder"], "codex")
        self.assertFalse(fresh["holder_taken"])
        self.join_tracker(skill["id"])
        after = store.get_task(self.conn, skill["id"])
        self.assertEqual(after["launched_by"], "listik")
        self.assertNotEqual(after["status"], "done")
        self.assertIsNone(after["stage"])

        direct = self.make_task(project="proj", route="direct-route", worktree=str(self.tmp_path))
        self.launch(direct["id"])
        issued = store.get_task(self.conn, direct["id"])
        self.assertEqual(issued["holder"], "codex")
        self.assertFalse(issued["holder_taken"])
        self.join_tracker(direct["id"])
        finished = store.get_task(self.conn, direct["id"])
        self.assertEqual(finished["launched_by"], "listik")
        self.assertEqual(finished["stage"], "s1-spec")
        self.assertNotEqual(finished["status"], "done")

    def test_sliced_parent_on_later_stage_does_not_start(self) -> None:
        command = self.say("готово")
        route = self.swarm(command)
        parent = self.task(route)
        store.update_task(self.conn, parent["id"], stage="s3-impl")
        self.conn.execute(
            "UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?", (parent["id"],))
        self.conn.commit()
        store.create_task(self.conn, title="порция", project="proj", parent=parent["id"])
        reason = self.launch(parent["id"])
        self.assertEqual(reason, launcher_mod.STAGE_SKIPPED)
        stayed = store.get_task(self.conn, parent["id"])
        self.assertEqual(stayed["stage"], "s3-impl")
        self.assertIsNone(stayed["launched_by"])
        self.assertIsNone(stayed["launch_pid"])

    def test_skill_stage_placeholder_is_empty(self) -> None:
        marker = self.tmp_path / "argv.txt"
        path = self.script(
            "import sys\n"
            "from pathlib import Path\n"
            "Path(sys.argv[1]).write_text('|'.join(sys.argv[2:]), encoding='utf-8')\n")
        command = [sys.executable, str(path), str(marker), "stage={stage}", "role={role}"]
        self.set_routes(
            {"key": "skill-route", "kind": "pipeline", "title": "Скил", "hint": "",
             "visible": True,
             "roles": {"impl": {"provider": "claude", "label": "C", "title": "C"}},
             "command": command},
        )
        task = self.task("skill-route")
        store.update_task(self.conn, task["id"], stage="s3-impl")
        self.launch(task["id"])
        self.join_tracker(task["id"])
        self.assertEqual(marker.read_text(encoding="utf-8"), "stage=|role=")

    def test_claim_not_found_clears_capture(self) -> None:
        command = self.say("готово")
        route = self.swarm(command)
        task = self.task(route)
        with mock.patch("listik.stage_launch.store.claim",
                        side_effect=errors_mod.NotFound("задача не найдена: x")):
            reason = self.launch(task["id"])
        self.assertIsNotNone(reason)
        asked = store.get_task(self.conn, task["id"])
        self.assertTrue(asked["needs_owner"])
        self.assertIsNone(asked["launched_by"])
        self.assertIsNone(asked["launch_pid"])
        self.assertIsNone(asked["launch_error"])

    def test_devin_harness_is_stored_and_claim_asks(self) -> None:
        command = self.say("готово")
        route = self.swarm(command, roles={"spec": role(command, "devin", "devin")})
        saved = routes_store.get_route(self.conn, route)
        self.assertEqual(saved["roles"]["spec"]["harness"], "devin")
        task = self.task(route)
        reason = self.launch(task["id"])
        self.assertIsNotNone(reason)
        asked = store.get_task(self.conn, task["id"])
        self.assertTrue(asked["needs_owner"])
        self.assertIsNone(asked["launched_by"])
        self.assertIsNone(asked["launch_pid"])

    def test_direct_driver_is_rejected(self) -> None:
        with self.assertRaises(routes_mod.RoutesError):
            routes_mod.validate({"version": 1, "routes": [{
                "key": "d", "kind": "direct", "title": "D", "hint": "", "visible": True,
                "harness": "codex", "command": ["echo"], "driver": "swarm"}]})

    def test_old_launch_does_not_move_the_card(self) -> None:
        flag = self.tmp_path / "go"
        path = self.script(
            "import sys, time\n"
            "from pathlib import Path\n"
            "flag = Path(sys.argv[1])\n"
            "for _ in range(200):\n"
            "    if flag.exists():\n"
            "        break\n"
            "    time.sleep(0.02)\n"
            "sys.stdout.write('готово\\n')\n")
        command = [sys.executable, str(path), str(flag)]
        route = self.swarm(command, roles={"spec": role(command), "impl": role(command)})
        task = self.task(route)
        self.launch(task["id"])
        self.seed(task["id"], dispatch_id="other")
        flag.write_text("1", encoding="utf-8")
        self.join_tracker(task["id"])
        stayed = store.get_task(self.conn, task["id"])
        self.assertEqual(stayed["stage"], "s1-spec")
        self.assertEqual(stayed["holder"], "codex")


class PortionCloseTests(TempDbTestCase):
    def test_parent_closes_when_last_portion_is_done(self) -> None:
        parent = store.create_task(self.conn, title="шаг", project="p")
        self.conn.execute("UPDATE tasks SET launch_driver = 'swarm', stage = 's1-spec' WHERE id = ?",
                          (parent["id"],))
        self.conn.commit()
        first = store.create_task(self.conn, title="a", project="p", parent=parent["id"])
        second = store.create_task(self.conn, title="b", project="p", parent=parent["id"])
        store.update_task(self.conn, first["id"], status="done")
        self.assertEqual(store.get_task(self.conn, parent["id"])["status"], "open")
        store.update_task(self.conn, second["id"], status="cancelled")
        closed = store.get_task(self.conn, parent["id"])
        self.assertEqual(closed["status"], "done")
        self.assertEqual(closed["close_reason"], "порции закрыты")

    def test_all_cancelled_asks_and_does_not_close(self) -> None:
        parent = store.create_task(self.conn, title="шаг", project="p")
        self.conn.execute(
            "UPDATE tasks SET launch_driver = 'swarm', stage = 's1-spec' WHERE id = ?",
            (parent["id"],))
        self.conn.commit()
        child = store.create_task(self.conn, title="a", project="p", parent=parent["id"])
        store.update_task(self.conn, child["id"], status="cancelled")
        stayed = store.get_task(self.conn, parent["id"])
        self.assertEqual(stayed["status"], "open")
        self.assertTrue(stayed["needs_owner"])
        self.assertTrue(stayed["portions_cancelled_only"])
