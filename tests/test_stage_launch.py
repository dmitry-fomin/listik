"""Режим роя (`driver=swarm`): claim за роль, первая строка `.out`, нарезка,
закрытие родителя, `launched:false` (listik-2gry, docs/specs/swarm-stage-launch.md).

Процессы — настоящие `python3 -c` короткоживущие скрипты (харнесс `probe`),
слежение — штатный поток `launcher.tracker`.
"""
from __future__ import annotations

import json
import sys
import unittest

from listik import harnesses_store, launcher, routes_store, stage_launch, store
from tests.helpers import TempDbTestCase


def script(line: str) -> list[str]:
    return [sys.executable, "-c", f"print({line!r})"]


_STAGES = ("s1-spec", "s2-review", "s3-impl", "s4-judge")


class SwarmCase(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        store.add_project(self.conn, path=str(self.tmp_path), slug="proj",
                          title="Проект")
        harnesses_store.create(self.conn, {
            "key": "probe", "label": "probe",
            "argv": [sys.executable, "-c", "print('готово')"]})

    def add_route(self, roles: dict, key: str = "roy") -> dict:
        return routes_store.create_route(self.conn, key=key, kind="swarm",
                                         title="Рой", roles=roles)

    def add_task(self, *, route: str = "roy", stage: str | None = None,
                 parent: str | None = None) -> str:
        return store.create_task(self.conn, title="Задача", project="proj",
                                 route=route, stage=stage, parent=parent)["id"]

    def start_and_wait(self, task_id: str):
        result = launcher.start(self.conn, task_id, log_dir=str(self.tmp_path))
        if result is None:  # процесс пошёл — ждём слежение
            thread = launcher.tracker(task_id)
            if thread is not None:
                thread.join(timeout=30)
        return result

    def task(self, task_id: str) -> dict:
        return store.get_task(self.conn, task_id)

    def comments(self, task_id: str, kind: str | None = None) -> list[str]:
        rows = self.conn.execute(
            "SELECT kind, text FROM comments WHERE task_id = ? ORDER BY created_at, id",
            (task_id,)).fetchall()
        return [r["text"] for r in rows if kind is None or r["kind"] == kind]


class LaunchTests(SwarmCase):
    def test_missing_role_skips_stage(self) -> None:
        # Ролей spec/critic нет — с пустого этапа прыжок на s3-impl без процесса.
        self.add_route({"impl": {"harness": "probe"},
                        "judge": {"harness": "probe"}})
        task_id = self.add_task()
        result = self.start_and_wait(task_id)
        self.assertEqual(result, {"launched": False, "stage_skipped": "s3-impl"})
        task = self.task(task_id)
        self.assertEqual(task["stage"], "s3-impl")
        self.assertFalse(task["needs_owner"])
        self.assertIsNone(task["launched_by"])
        self.assertEqual(task["launch_driver"], "swarm")
        self.assertTrue(any("пропуск" in t or "нет" in t for t in
                            self.comments(task_id, "journal")))

    def test_role_run_and_gotovo_advances(self) -> None:
        self.add_route({"impl": {"harness": "probe"},
                        "judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s3-impl")
        result = self.start_and_wait(task_id)
        self.assertIsNone(result)
        task = self.task(task_id)
        self.assertEqual(task["stage"], "s4-judge")
        self.assertEqual(task["holder"], "")  # держатель-харнесс снят роем
        self.assertIsNone(task["launched_by"])
        self.assertEqual(task["launch_exit_code"], 0)
        self.assertTrue(any("готово" in t for t in self.comments(task_id)))

    def test_judge_green_closes_card(self) -> None:
        harnesses_store.update(self.conn, "probe", {"argv": script("зелёный")})
        self.add_route({"judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s4-judge")
        self.assertIsNone(self.start_and_wait(task_id))
        task = self.task(task_id)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["stage"], "done")
        self.assertIn("VERDICT: PASS", self.comments(task_id, "verdict"))

    def test_judge_red_returns_to_impl(self) -> None:
        harnesses_store.update(self.conn, "probe", {
            "argv": [sys.executable, "-c",
                     "print('красный')\nprint('1. поправь foo')"]})
        self.add_route({"impl": {"harness": "probe"},
                        "judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s4-judge")
        self.assertIsNone(self.start_and_wait(task_id))
        task = self.task(task_id)
        self.assertEqual(task["stage"], "s3-impl")
        self.assertNotEqual(task["status"], "done")
        self.assertEqual(task["holder"], "")
        verdicts = self.comments(task_id, "verdict")
        self.assertTrue(any(v.startswith("VERDICT: FAIL") and "поправь foo" in v
                            for v in verdicts))

    def test_question_and_garbage_open_question(self) -> None:
        for line, marker, key in (("вопрос\nкак проверять?", "как проверять?", "roy-q"),
                                  ("сюр", "не сдал работу", "roy-x")):
            with self.subTest(line=line):
                harnesses_store.update(self.conn, "probe", {"argv": script(line)})
                self.add_route({"impl": {"harness": "probe"}}, key=key)
                task_id = self.add_task(stage="s3-impl", route=key)
                self.assertIsNone(self.start_and_wait(task_id))
                task = self.task(task_id)
                self.assertTrue(task["needs_owner"])
                self.assertEqual(task["stage"], "s3-impl")
                self.assertEqual(task["holder"], "")
                self.assertIsNone(task["launched_by"])
                self.assertTrue(any(marker in t for t in self.comments(task_id)))

    def test_empty_output_is_not_delivered(self) -> None:
        harnesses_store.update(self.conn, "probe",
                               {"argv": [sys.executable, "-c", "pass"]})
        self.add_route({"impl": {"harness": "probe"}})
        task_id = self.add_task(stage="s3-impl")
        self.assertIsNone(self.start_and_wait(task_id))
        task = self.task(task_id)
        self.assertTrue(task["needs_owner"])
        self.assertIn("не сдал работу",
                      "\n".join(self.comments(task_id, "question")))

    def test_no_roles_is_refusal(self) -> None:
        # Все роли без команды (харнесс manual): маршрут завести нельзя через
        # проверку, поэтому сначала exec-харнесс, потом argv снимаем правкой.
        self.add_route({"impl": {"harness": "probe"}})
        self.conn.execute("UPDATE harnesses SET argv = NULL WHERE key = 'probe'")
        self.conn.commit()
        task_id = self.add_task(stage="s3-impl")
        result = self.start_and_wait(task_id)
        self.assertEqual(result["launched"], False)
        self.assertTrue(result["needs_owner"])
        self.assertTrue(self.task(task_id)["needs_owner"])
        self.assertIsNone(self.task(task_id)["launch_error"])

    def test_role_argv_overrides_harness(self) -> None:
        self.add_route({"impl": {"harness": "probe", "argv": script("вопрос\nпочему?")}})
        task_id = self.add_task(stage="s3-impl")
        self.assertIsNone(self.start_and_wait(task_id))
        self.assertTrue(self.task(task_id)["needs_owner"])
        self.assertTrue(any("почему?" in t for t in self.comments(task_id)))

    def test_second_launch_while_running_is_conflict(self) -> None:
        harnesses_store.update(self.conn, "probe", {
            "argv": [sys.executable, "-c",
                     "import time; time.sleep(30); print('готово')"]})
        self.add_route({"impl": {"harness": "probe"}})
        task_id = self.add_task(stage="s3-impl")
        try:
            self.assertIsNone(launcher.start(self.conn, task_id,
                                             log_dir=str(self.tmp_path)))
            self.assertEqual(launcher.start(self.conn, task_id,
                                            log_dir=str(self.tmp_path)),
                             launcher.ALREADY_STARTED)
            self.assertEqual(self.task(task_id)["holder"], "probe")
        finally:
            launcher.revoke(self.conn, task_id, note="тест кончился")
            thread = launcher.tracker(task_id)
            if thread is not None:
                thread.join(timeout=30)


class SlicingTests(SwarmCase):
    def add_route(self, roles: dict | None = None, key: str = "roy") -> dict:
        return super().add_route(roles or {"spec": {"harness": "probe"},
                                           "impl": {"harness": "probe"},
                                           "judge": {"harness": "probe"}}, key=key)

    def fake_finish(self, task_id: str, stage: str, first_line: str) -> None:
        """Имитация завершившегося запуска роя: захват + `.out` + apply_outcome.

        Порции рождаются, пока родитель на `s1-spec` работает, — настоящий
        `launcher.start` карточки с живыми порциями уже не запускает процесс,
        поэтому слепок запуска ставим руками.
        """
        log = self.tmp_path / f"launch-{task_id}.log"
        log.write_text("", encoding="utf-8")
        out = stage_launch.out_path_of(str(log))
        out.write_text(first_line + "\n", encoding="utf-8")
        self.conn.execute(
            "UPDATE tasks SET stage = ?, launched_by = 'listik', "
            "dispatch_id = 'd1', launch_log = ?, launch_pid = 424242 "
            "WHERE id = ?", (stage, str(log), task_id))
        self.conn.commit()
        stage_launch.apply_outcome(self.conn, task_id)

    def test_gotovo_with_children_slices_parent(self) -> None:
        self.add_route()
        parent = self.add_task()
        child = self.add_task(parent=parent, route="")  # без своего маршрута
        self.fake_finish(parent, "s1-spec", "готово")
        task = self.task(parent)
        self.assertEqual(task["stage"], "s1-spec")  # родитель дальше не идёт
        self.assertFalse(task["holder"])
        self.assertIsNone(task["launched_by"])
        self.assertTrue(task["has_portions"])
        kid = self.task(child)
        self.assertEqual(kid["stage"], "s3-impl")  # ближайший этап с ролью
        self.assertEqual(kid["launch_route"], "roy")
        self.assertEqual(kid["launch_driver"], "swarm")
        self.assertTrue(any("нарезано" in t for t in self.comments(parent)))

    def test_sliced_parent_launch_is_noop(self) -> None:
        self.add_route()
        parent = self.add_task()
        self.add_task(parent=parent, route="")
        self.fake_finish(parent, "s1-spec", "готово")
        result = self.start_and_wait(parent)
        self.assertEqual(result, {"launched": False, "reason": "sliced"})
        self.assertIsNone(self.task(parent)["launched_by"])
        self.assertFalse(self.task(parent)["needs_owner"])
        self.assertTrue(any("родитель нарезан" in t
                            for t in self.comments(parent, "journal")))

    def test_suggested_blockers_become_hard(self) -> None:
        self.add_route()
        parent = self.add_task()
        child_a = self.add_task(parent=parent, route="")
        child_b = self.add_task(parent=parent, route="")
        store.add_dep(self.conn, child_b, child_a, "suggested-blocks",
                      created_by="agent:probe")
        self.fake_finish(parent, "s1-spec", "готово")
        hard = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id = ? AND depends_on = ? "
            "AND dep_type = 'blocks'", (child_b, child_a)).fetchone()
        self.assertIsNotNone(hard)

    def test_portion_on_s1_spec_without_route_is_left_alone(self) -> None:
        # Порция с этапом — начатая, даже если это `s1-spec` и маршрута нет:
        # её не трогают, остальные порции нарезки обрабатываются (listik-itjo).
        self.add_route()
        parent = self.add_task()
        child_a = self.add_task(parent=parent, route="")
        child_b = self.add_task(parent=parent, route="", stage="s1-spec")
        child_c = self.add_task(parent=parent, route="")
        store.add_dep(self.conn, child_c, child_a, "suggested-blocks",
                      created_by="agent:probe")
        self.fake_finish(parent, "s1-spec", "готово")
        task = self.task(parent)
        self.assertFalse(task["needs_owner"])
        self.assertEqual(task["stage"], "s1-spec")
        for child in (child_a, child_c):
            kid = self.task(child)
            self.assertEqual(kid["stage"], "s3-impl")
            self.assertEqual(kid["launch_route"], "roy")
            self.assertEqual(kid["launch_driver"], "swarm")
        middle = self.task(child_b)
        self.assertEqual(middle["stage"], "s1-spec")
        self.assertFalse(middle["launch_route"])
        self.assertIsNone(middle["launch_driver"])
        hard = self.conn.execute(
            "SELECT 1 FROM deps WHERE issue_id = ? AND depends_on = ? "
            "AND dep_type = 'blocks'", (child_c, child_a)).fetchone()
        self.assertIsNotNone(hard)

    def test_parent_closes_when_all_portions_done(self) -> None:
        self.add_route()
        parent = self.add_task()
        child_a = self.add_task(parent=parent, route="")
        child_b = self.add_task(parent=parent, route="")
        self.fake_finish(parent, "s1-spec", "готово")
        store.update_task(self.conn, child_a, status="done", stage="done",
                          actor="agent:listik")
        self.assertEqual(self.task(parent)["status"], "open")  # ждём вторую
        store.update_task(self.conn, child_b, status="done", stage="done",
                          actor="agent:listik")
        task = self.task(parent)
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["close_reason"], "порции закрыты")

    def test_parent_stays_open_without_done_portion(self) -> None:
        self.add_route()
        parent = self.add_task()
        child_a = self.add_task(parent=parent, route="")
        child_b = self.add_task(parent=parent, route="")
        self.fake_finish(parent, "s1-spec", "готово")
        store.update_task(self.conn, child_a, status="cancelled", stage="done",
                          actor="agent:listik")
        # Пока живая порция есть — вопроса ещё нет.
        self.assertFalse(self.task(parent)["needs_owner"])
        store.update_task(self.conn, child_b, status="cancelled", stage="done",
                          actor="agent:listik")
        # Ни одной done-порции — родитель не закрывается сам, на нём вопрос
        # человеку, и рой его не запускает (portions_cancelled_only).
        task = self.task(parent)
        self.assertEqual(task["status"], "open")
        self.assertTrue(task["needs_owner"])
        self.assertTrue(task["portions_cancelled_only"])
        self.assertFalse(task["has_portions"])
        result = self.start_and_wait(parent)
        self.assertEqual(result, {"launched": False, "reason": "sliced"})
        self.assertIsNone(self.task(parent)["launched_by"])


class HelpersTests(SwarmCase):
    def test_out_path_and_first_line(self) -> None:
        out = stage_launch.out_path_of("/tmp/launch-x.log")
        self.assertEqual(str(out), "/tmp/launch-x.out")
        out.write_text("﻿готово\nхвост\n", encoding="utf-8")
        self.assertEqual(stage_launch.read_first_line(out), ("готово", "хвост"))
        self.assertEqual(stage_launch.read_first_line(
            self.tmp_path / "нет-файла.out"), ("", ""))

    def test_unfinished_line_over_limit_is_empty(self) -> None:
        # Первая строка не кончается в окне 64 КиБ — обрезанный префикс
        # ответом не считается (docs/specs/swarm-stage-launch.md).
        out = stage_launch.out_path_of("/tmp/launch-y.log")
        out.write_bytes(b"x" * (stage_launch.OUT_READ_LIMIT + 10))
        self.assertEqual(stage_launch.read_first_line(out), ("", ""))

    def test_first_line_within_limit_is_read(self) -> None:
        out = stage_launch.out_path_of("/tmp/launch-z.log")
        out.write_bytes("готово\n".encode()
                        + b"x" * stage_launch.OUT_READ_LIMIT)
        first, _rest = stage_launch.read_first_line(out)
        self.assertEqual(first, "готово")

    def test_resolve_role_and_next_stage(self) -> None:
        record = self.add_route({"impl": {"harness": "probe"},
                                 "judge": {"harness": "probe"},
                                 "spec": None})
        self.assertIsNone(stage_launch.resolve_role(self.conn, record, "spec"))
        impl = stage_launch.resolve_role(self.conn, record, "impl")
        self.assertEqual(impl["argv"], [sys.executable, "-c", "print('готово')"])
        # Роль без своего промпта получает протокол первой строки — `prompt`
        # харнесса (текст прямой выдачи) не наследуется.
        self.assertEqual(impl["prompt"], harnesses_store.SWARM_PROMPT)
        self.assertIn("{role}", impl["prompt"])
        self.assertEqual(stage_launch.next_stage_with_role(self.conn, record,
                                                         "s1-spec"), "s3-impl")
        self.assertIsNone(stage_launch.next_stage_with_role(self.conn, record,
                                                          "s4-judge"))


if __name__ == "__main__":
    unittest.main()
