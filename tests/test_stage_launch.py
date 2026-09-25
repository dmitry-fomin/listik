"""Режим роя (`driver=swarm`): claim за роль, последняя строка `.out`, нарезка,
закрытие родителя, `launched:false` (listik-2gry, docs/specs/swarm-stage-launch.md).

Процессы — настоящие `python3 -c` короткоживущие скрипты (харнесс `probe`),
слежение — штатный поток `launcher.tracker`.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

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
                     "print('1. поправь foo')\nprint('красный')"]})
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

    def test_impl_after_red_gets_fixes_first_and_prompt_logged(self) -> None:
        # listik-po5v: s3 после красного вердикта — его правки первым блоком промпта,
        # промпт виден в `.log` запуска.
        harnesses_store.update(self.conn, "probe", {
            "argv": [sys.executable, "-c",
                     "import sys; print(sys.argv[-1]); print('готово')"]})
        self.add_route({"impl": {"harness": "probe"},
                        "judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s3-impl")
        store.add_comment(self.conn, task_id, "VERDICT: FAIL\n1. старый пункт",
                          author="human", kind="verdict")
        store.add_comment(self.conn, task_id,
                          "VERDICT: FAIL\n1. README.md:17 — замени формулировку",
                          author="human", kind="verdict", created_at="2099-01-01T00:00:00Z")
        self.assertIsNone(self.start_and_wait(task_id))
        log = Path(self.task(task_id)["launch_log"])
        prompt = stage_launch.out_path_of(str(log)).read_text(encoding="utf-8")
        self.assertTrue(prompt.startswith("Приёмка вернула работу красным вердиктом"), prompt)
        fixes = prompt.index("README.md:17")
        self.assertLess(fixes, prompt.index(f"Задача {task_id}"))
        self.assertNotIn("старый пункт", prompt)
        logged = log.read_text(encoding="utf-8")
        self.assertIn("рой: промпт (sha256 ", logged)
        self.assertIn("README.md:17 — замени формулировку", logged)

    def test_impl_without_red_verdict_prompt_unchanged(self) -> None:
        harnesses_store.update(self.conn, "probe", {
            "argv": [sys.executable, "-c",
                     "import sys; print(sys.argv[-1]); print('готово')"]})
        self.add_route({"impl": {"harness": "probe"},
                        "judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s3-impl")
        self.assertIsNone(self.start_and_wait(task_id))
        log = Path(self.task(task_id)["launch_log"])
        prompt = stage_launch.out_path_of(str(log)).read_text(encoding="utf-8")
        self.assertTrue(prompt.startswith(f"Задача {task_id}"), prompt)
        self.assertIn(f"Задача {task_id}", log.read_text(encoding="utf-8"))

    def test_question_and_garbage_open_question(self) -> None:
        for line, marker, key in (("как проверять?\nвопрос", "как проверять?", "roy-q"),
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
        self.add_route({"impl": {"harness": "probe", "argv": script("почему?\nвопрос")}})
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


    # ---------------------------------------------- критерии роли (listik-2cu2)

    BRIDGE = ("Критерии роли {role} — разделы агента {stem} "
              "(plugins/feature-pipeline/agents/{stem}.md). Где они расходятся с протоколом "
              "выше, действует протокол: ответ — последняя строка вывода, а не первая строка "
              "отчёта; claim, heartbeat, release, stage, needs-owner и вердикт (-k verdict) "
              "ты не делаешь — карточку ведёт Listik; оркестратора и дампов диффа нет, всё "
              "нужное — в listik context.")
    JUDGE_NOTE = ("Красные пункты пиши строками над последней строкой «красный» — Listik "
                  "переносит их в VERDICT: FAIL дословно. При зелёном коммит делаешь ты, до "
                  "ответа «зелёный».")
    JUDGE_LAST = ("Последняя строка вывода — одно слово: «зелёный», «красный», «вопрос» "
                  "или «не смог».")
    ROLE_CASES = (("spec", "s1-spec", "pipeline-spec-writer",
                   ("Вопрос автору — это остановка, а не абзац в отчёте",
                    "Что тебе запрещено", "Файлы"), "готово"),
                  ("critic", "s2-review", "pipeline-critic",
                   ("Что ты смотришь", "Что тебе запрещено"), "готово"),
                  ("impl", "s3-impl", "pipeline-implementer",
                   ("Порядок работы", "Что тебе запрещено", "Если не получается"), "готово"),
                  ("judge", "s4-judge", "pipeline-judge",
                   ("Что тебе запрещено", "Работа", "Коммит при зелёном вердикте"),
                   "зелёный"))

    def echo_probe(self, answer: str) -> None:
        harnesses_store.update(self.conn, "probe", {
            "argv": [sys.executable, "-c",
                     f"import sys; print(sys.argv[-1]); print({answer!r})"]})

    def run_role(self, role: str, stage: str, answer: str, cell: dict | None = None,
                 key: str = "roy") -> tuple[str, str, str]:
        """Запуск роли зондом, печатающим промпт; (id, промпт из `.out`, `.log`)."""
        self.echo_probe(answer)
        self.add_route({role: cell or {"harness": "probe"}}, key=key)
        task_id = self.add_task(stage=stage, route=key)
        self.assertIsNone(self.start_and_wait(task_id))
        log = Path(self.task(task_id)["launch_log"])
        out = stage_launch.out_path_of(str(log)).read_text(encoding="utf-8")
        self.assertTrue(out.endswith(f"\n{answer}\n"), out[-200:])
        return task_id, out[:-len(answer) - 2], log.read_text(encoding="utf-8")

    def patch_agents_dir(self, path: Path) -> None:
        patcher = mock.patch.object(stage_launch, "AGENTS_DIR", path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_role_sees_its_criteria_in_log(self) -> None:
        agents = Path(__file__).resolve().parent.parent / "plugins/feature-pipeline/agents"
        for role, stage, stem, titles, answer in self.ROLE_CASES:
            with self.subTest(role=role):
                lines = (agents / f"{stem}.md").read_text(encoding="utf-8").splitlines()
                _, _, logged = self.run_role(role, stage, answer, key=f"roy-{role}")
                self.assertIn(self.BRIDGE.format(role=role, stem=stem), logged)
                for title in titles:
                    start = lines.index(f"## {title}")
                    first = next(line for line in lines[start + 1:] if line.strip())
                    self.assertIn(first, logged)
                absent = {"judge": ("## Карточка Listik", "## Отчёт"),
                          "impl": ("## Карточка Listik", "## Формат отчёта")}
                for header in absent.get(role, ()):
                    self.assertNotIn(header, logged)

    def test_judge_prompt_has_duplicates_point(self) -> None:
        agents = Path(__file__).resolve().parent.parent / "plugins/feature-pipeline/agents"
        point = next(line for line in (agents / "pipeline-judge.md").read_text(
            encoding="utf-8").splitlines() if "Отдельно — **дубликаты**" in line)
        _, prompt, logged = self.run_role("judge", "s4-judge", "зелёный")
        self.assertIn(point, prompt)
        self.assertIn(point, logged)
        last = [line for line in prompt.splitlines() if line.strip()][-1]
        self.assertEqual(last, self.JUDGE_LAST)

    def test_own_cell_prompt_has_no_criteria(self) -> None:
        self.patch_agents_dir(self.tmp_path / "нет-агентов")  # файлы не читаются
        task_id, prompt, logged = self.run_role(
            "impl", "s3-impl", "готово", cell={"harness": "probe", "prompt": "свой {task_id}"})
        self.assertEqual(prompt, f"свой {task_id}")
        self.assertNotIn("Критерии роли", logged)

    def test_judge_prompt_exact_assembly(self) -> None:
        agents = self.tmp_path / "agents"
        agents.mkdir()
        (agents / "pipeline-judge.md").write_text(
            "# Судья\n\n"
            "## Карточка Listik\nлишнее до\n\n"
            "## Что тебе запрещено\n- не трогай {task_id}\n\n\n\n"
            "## Работа судьи\nне тот раздел\n\n"
            "## Работа   \n1. шаг\n### подраздел\n2. ещё\n\n"
            "## Коммит при зелёном вердикте\nкоммить\n\n\n"
            "## Отчёт\nлишнее после\n", encoding="utf-8")
        self.patch_agents_dir(agents)
        task_id, prompt, _ = self.run_role("judge", "s4-judge", "зелёный")
        cwd = self.conn.execute("SELECT path FROM projects WHERE slug = 'proj'").fetchone()[0]
        protocol = harnesses_store.SWARM_PROMPT.format(
            task_id=task_id, project="proj", stage="s4-judge", role="judge",
            worktree=cwd, branch="", cwd=cwd)
        bridge = ("Критерии роли judge — разделы агента pipeline-judge "
                  "(plugins/feature-pipeline/agents/pipeline-judge.md). Где они расходятся с "
                  "протоколом выше, действует протокол: ответ — последняя строка вывода, а не "
                  "первая строка отчёта; claim, heartbeat, release, stage, needs-owner и "
                  "вердикт (-k verdict) ты не делаешь — карточку ведёт Listik; оркестратора и "
                  "дампов диффа нет, всё нужное — в listik context.")
        criteria = ("## Что тебе запрещено\n- не трогай {task_id}\n\n"
                    "## Работа   \n1. шаг\n### подраздел\n2. ещё\n\n"
                    "## Коммит при зелёном вердикте\nкоммить")
        self.assertEqual(prompt, "\n\n".join(
            [protocol, bridge, self.JUDGE_NOTE, criteria, self.JUDGE_LAST]))

    def assert_refused(self, task_id: str, result) -> str:
        self.assertEqual(result, {"launched": False, "needs_owner": True})
        task = self.task(task_id)
        self.assertTrue(task["needs_owner"])
        self.assertFalse(task["holder"])
        self.assertIsNone(task["launched_by"])
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launch_log"])
        claims = self.conn.execute("SELECT COUNT(*) FROM events WHERE task_id = ? "
                                   "AND kind = 'claim'", (task_id,)).fetchone()[0]
        self.assertEqual(claims, 0)
        return self.comments(task_id, "question")[-1]

    def test_missing_agent_file_is_refusal(self) -> None:
        agents = self.tmp_path / "agents"
        agents.mkdir()
        self.patch_agents_dir(agents)
        self.add_route({"judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s4-judge")
        question = self.assert_refused(task_id, self.start_and_wait(task_id))
        self.assertEqual(question, "рой: нет критериев роли judge (этап s4-judge): "
                                   f"нет файла {agents}/pipeline-judge.md")

    def test_missing_agent_section_is_refusal(self) -> None:
        agents = self.tmp_path / "agents"
        agents.mkdir()
        (agents / "pipeline-judge.md").write_text(
            "## Что тебе запрещено\nx\n\n## Работа судьи\ny\n\n"
            "## Коммит при зелёном вердикте\nz\n", encoding="utf-8")
        self.patch_agents_dir(agents)
        self.add_route({"judge": {"harness": "probe"}})
        task_id = self.add_task(stage="s4-judge")
        question = self.assert_refused(task_id, self.start_and_wait(task_id))
        self.assertTrue(question.endswith("в pipeline-judge.md нет раздела «Работа»"),
                        question)

    def test_roles_visible_without_agents_dir(self) -> None:
        record = self.add_route({"impl": {"harness": "probe"},
                                 "judge": {"harness": "probe"}})
        real = (stage_launch.has_roles(self.conn, record),
                [stage_launch.next_stage_with_role(self.conn, record, s) for s in _STAGES])
        self.patch_agents_dir(self.tmp_path / "нет-агентов")
        self.assertEqual((stage_launch.has_roles(self.conn, record),
                          [stage_launch.next_stage_with_role(self.conn, record, s)
                           for s in _STAGES]), real)
        self.assertEqual(real, (True, ["s3-impl", "s3-impl", "s4-judge", None]))

    def test_role_prompt_fits_argv(self) -> None:
        for role, *_ in self.ROLE_CASES:
            with self.subTest(role=role):
                prompt = f"{harnesses_store.SWARM_PROMPT}\n\n{stage_launch.role_tail(role)}"
                self.assertLess(len(prompt.encode("utf-8")), 32768)


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
        self.assertEqual(task["close_reason"], f"подзадачи закрыты: {child_a}, {child_b}")

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
        # Ни одной done-порции — эпик отменяется сам, без вопроса человеку
        # (правило эпика, `store.sync_epic`).
        task = self.task(parent)
        self.assertEqual(task["status"], "cancelled")
        self.assertTrue(task["close_reason"].startswith("подзадачи отменены:"))
        self.assertFalse(task["needs_owner"])


class HelpersTests(SwarmCase):
    def test_out_path_and_answer_line(self) -> None:
        out = stage_launch.out_path_of("/tmp/launch-x.log")
        self.assertEqual(str(out), "/tmp/launch-x.out")
        out.write_text("\ufeffтекст\nготово\n\n", encoding="utf-8")
        self.assertEqual(stage_launch.read_answer(out), ("готово", "текст"))
        self.assertEqual(stage_launch.read_answer(
            self.tmp_path / "нет-файла.out"), ("", ""))

    def test_glued_chatter_before_answer(self) -> None:
        # devin -p склеивает промежуточные реплики без переводов строки —
        # ответ всё равно последней строкой (listik-utw9).
        out = stage_launch.out_path_of("/tmp/launch-g.log")
        out.write_text("Let me look.Now I write.готово-не-тут\nИтог.\nготово\n",
                       encoding="utf-8")
        self.assertEqual(stage_launch.read_answer(out)[0], "готово")

    def test_answer_after_long_output_is_read(self) -> None:
        out = stage_launch.out_path_of("/tmp/launch-z.log")
        out.write_bytes(b"x" * (stage_launch.OUT_READ_LIMIT + 10)
                        + "\nправка\nкрасный\n".encode())
        answer, text = stage_launch.read_answer(out)
        self.assertEqual(answer, "красный")
        self.assertEqual(text, "x" * stage_launch.CUT_LINE_KEEP + "\nправка")

    def test_question_glued_to_long_chatter_keeps_text(self) -> None:
        # Вопрос приклеен к склейке длиннее окна — текст не теряется.
        out = stage_launch.out_path_of("/tmp/launch-q.log")
        out.write_bytes(b"x" * (stage_launch.OUT_READ_LIMIT + 50)
                        + "Как проверять?\nвопрос\n".encode())
        answer, text = stage_launch.read_answer(out)
        self.assertEqual(answer, "вопрос")
        self.assertTrue(text.endswith("Как проверять?"))
        self.assertLessEqual(len(text), stage_launch.CUT_LINE_KEEP)

    def test_whole_first_line_of_window_is_kept(self) -> None:
        # Окно начинается ровно после перевода строки — строка целая.
        out = stage_launch.out_path_of("/tmp/launch-w.log")
        body = "правка 1\nкрасный\n".encode()
        out.write_bytes(b"y" * 9 + b"\n" + b"z" * (stage_launch.OUT_READ_LIMIT - len(body) - 1)
                        + b"\n" + body)
        answer, text = stage_launch.read_answer(out)
        self.assertEqual(answer, "красный")
        self.assertIn("правка 1", text)

    def test_unfinished_line_over_limit_is_empty(self) -> None:
        out = stage_launch.out_path_of("/tmp/launch-y.log")
        out.write_bytes(b"x" * (stage_launch.OUT_READ_LIMIT + 10))
        self.assertEqual(stage_launch.read_answer(out), ("", ""))

    def test_resolve_role_and_next_stage(self) -> None:
        record = self.add_route({"impl": {"harness": "probe"},
                                 "judge": {"harness": "probe"},
                                 "spec": None})
        self.assertIsNone(stage_launch.resolve_role(self.conn, record, "spec"))
        impl = stage_launch.resolve_role(self.conn, record, "impl")
        self.assertEqual(impl["argv"], [sys.executable, "-c", "print('готово')"])
        # Роль без своего промпта получает протокол ответа последней строкой — `prompt`
        # харнесса (текст прямой выдачи) не наследуется.
        self.assertEqual(impl["prompt"], harnesses_store.SWARM_PROMPT)
        self.assertIn("{role}", impl["prompt"])
        self.assertEqual(stage_launch.next_stage_with_role(self.conn, record,
                                                         "s1-spec"), "s3-impl")
        self.assertIsNone(stage_launch.next_stage_with_role(self.conn, record,
                                                          "s4-judge"))


class AdoptPortionsTests(SwarmCase):
    """listik-zr05, порция d: `portions_stuck`, `write_scope` порций, `portions adopt`."""

    fake_finish = SlicingTests.fake_finish

    def add_route(self, roles: dict | None = None, key: str = "roy") -> dict:
        if self.conn.execute("SELECT 1 FROM routes WHERE key = ?", (key,)).fetchone():
            return routes_store.get_route(self.conn, key)
        return super().add_route(roles or {"spec": {"harness": "probe"},
                                           "impl": {"harness": "probe"},
                                           "judge": {"harness": "probe"}}, key=key)

    def stuck_parent(self, *, scope: list | None = None) -> tuple[str, str, str]:
        """Родитель роя на `s1-spec` с вопросом и двумя порциями без маршрута и этапа."""
        self.add_route()
        parent = self.add_task(stage="s1-spec")
        if scope:
            store.update_task(self.conn, parent, write_scope=scope, actor="agent:t")
        a = self.add_task(parent=parent, route="")
        b = self.add_task(parent=parent, route="")
        store.set_needs_owner(self.conn, parent, value=True, actor="agent:listik",
                              text="рой: исход s1-spec не засчитан")
        return parent, a, b

    def conflict(self, task_id: str) -> None:
        from listik import errors
        with self.assertRaises(errors.ListikError) as caught:
            stage_launch.adopt_portions(self.conn, task_id, actor="agent:t")
        self.assertEqual(caught.exception.code, errors.CONFLICT)
        self.assertEqual(caught.exception.status, 409)

    def test_portions_stuck_flag(self) -> None:  # 1, 7
        parent, a, b = self.stuck_parent()
        self.assertTrue(self.task(parent)["portions_stuck"])
        listed = {t["id"]: t for t in store.list_tasks(self.conn, project="proj")["tasks"]}
        self.assertTrue(listed[parent]["portions_stuck"])
        self.assertFalse(listed[a]["portions_stuck"])  # детей нет
        store.update_task(self.conn, a, holder="agent:x", actor="agent:t")
        self.assertFalse(self.task(parent)["portions_stuck"])
        store.update_task(self.conn, a, holder="", actor="agent:t")
        self.assertTrue(self.task(parent)["portions_stuck"])
        store.update_task(self.conn, a, launch_route="roy", actor="agent:t")
        self.assertTrue(self.task(parent)["portions_stuck"])  # маршрут без этапа — не движение
        store.update_task(self.conn, a, stage="s3-impl", actor="agent:t")
        self.assertFalse(self.task(parent)["portions_stuck"])
        store.set_needs_owner(self.conn, a, value=True, actor="agent:t", text="вопрос")
        self.assertFalse(self.task(parent)["portions_stuck"])
        for kid in (a, b):
            store.update_task(self.conn, kid, status="done", stage="done", actor="agent:t")
        self.assertFalse(self.task(parent)["portions_stuck"])

    def test_write_scope_inherited_on_slice(self) -> None:  # 2
        self.add_route()
        parent = self.add_task()
        store.update_task(self.conn, parent, write_scope=["src/a.py"], actor="agent:t")
        a = self.add_task(parent=parent, route="")
        b = self.add_task(parent=parent, route="")
        store.update_task(self.conn, b, write_scope=["src/b.py"], actor="agent:t")
        self.fake_finish(parent, "s1-spec", "готово")
        self.assertEqual(self.task(a)["write_scope"], ["src/a.py"])
        self.assertIn(f"write_scope унаследован от {parent}", self.comments(a, "journal"))
        self.assertEqual(self.task(b)["write_scope"], ["src/b.py"])
        self.assertFalse(any("унаследован" in t for t in self.comments(b)))

    def test_no_parent_scope_leaves_portions_empty(self) -> None:  # 2
        self.add_route()
        parent = self.add_task()
        a = self.add_task(parent=parent, route="")
        self.fake_finish(parent, "s1-spec", "готово")
        self.assertEqual(self.task(a)["write_scope"], [])
        self.assertFalse(any("унаследован" in t for t in self.comments(a)))

    def test_adopt(self) -> None:  # 3
        parent, a, b = self.stuck_parent(scope=["src/a.py"])
        out = stage_launch.adopt_portions(self.conn, parent, actor="agent:t")
        self.assertEqual(out["adopted"], [a, b])
        self.assertEqual(out["stage"], "s3-impl")
        for kid in (a, b):
            task = self.task(kid)
            self.assertEqual(task["launch_route"], "roy")
            self.assertEqual(task["stage"], "s3-impl")
            self.assertEqual(task["launch_driver"], "swarm")
            self.assertEqual(task["write_scope"], ["src/a.py"])
            self.assertIn(f"write_scope унаследован от {parent}",
                          self.comments(kid, "journal"))
        task = self.task(parent)
        self.assertFalse(task["needs_owner"])
        self.assertFalse(task["portions_stuck"])
        self.assertFalse(task["holder"])

    def test_adopt_refusals(self) -> None:  # 4
        for status in ("done", "cancelled"):
            parent, _a, _b = self.stuck_parent()
            self.conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, parent))
            self.conflict(parent)
        routes_store.create_route(self.conn, key="skillish", kind="pipeline", title="Скил")
        skill_parent = self.add_task(route="skillish", stage="s1-spec")
        self.add_task(parent=skill_parent, route="")
        self.conflict(skill_parent)
        parent, _a, _b = self.stuck_parent()
        self.conn.execute("UPDATE tasks SET stage = 's3-impl' WHERE id = ?", (parent,))
        self.conflict(parent)
        parent, a, b = self.stuck_parent()
        for kid in (a, b):
            store.update_task(self.conn, kid, holder="agent:x", actor="agent:t")
        self.conflict(parent)
        parent, _a, _b = self.stuck_parent()
        self.conn.execute("UPDATE tasks SET launch_route = 'gone', launch_driver = 'swarm' "
                          "WHERE id = ?", (parent,))
        self.conflict(parent)

    # --- порция e: одна порция сливается в родителя

    def single_portion_parent(self, *, stage: str | None = None) -> tuple[str, str, str]:
        self.add_route()
        parent = self.add_task(stage=stage)
        step = self.tmp_path / f"{parent}.md"
        step.write_text("# шаг\n", encoding="utf-8")
        store.update_task(self.conn, parent, spec_path=str(step), actor="agent:t")
        file = self.tmp_path / f"{parent}.a.md"
        file.write_text("# a\n", encoding="utf-8")
        kid = store.create_task(self.conn, title="порция a", project="proj",
                                parent=parent, spec_path=str(file))["id"]
        return parent, kid, str(file)

    def assert_merged(self, parent: str, kid: str, file: str) -> None:
        child = self.task(kid)
        self.assertEqual(child["status"], "cancelled")
        self.assertEqual(child["close_reason"], "слита в родителя")
        types = [r[0] for r in self.conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id = ? AND depends_on = ?", (kid, parent))]
        self.assertEqual(types, ["discovered-from"])
        task = self.task(parent)
        self.assertEqual(task["spec_path"], file)
        self.assertEqual(task["stage"], "s3-impl")
        self.assertNotEqual(task["issue_type"], "epic")
        self.assertFalse(task["has_portions"])
        self.assertFalse(task["review_path"])
        self.assertFalse(task["holder"])
        journal = self.comments(parent, "journal")
        self.assertIn(f"ТЗ шага: {self.tmp_path / f'{parent}.md'}", journal)
        self.assertIn("одна порция a — ведёт сам родитель", journal)

    def test_single_portion_gotovo_merges(self) -> None:
        parent, kid, file = self.single_portion_parent()
        self.fake_finish(parent, "s1-spec", "готово")
        self.assert_merged(parent, kid, file)
        self.assertFalse(self.task(parent)["needs_owner"])

    def test_single_portion_adopt_merges(self) -> None:
        parent, kid, file = self.single_portion_parent(stage="s1-spec")
        store.set_needs_owner(self.conn, parent, value=True, actor="agent:listik",
                              text="рой: исход s1-spec не засчитан")
        out = stage_launch.adopt_portions(self.conn, parent, actor="agent:t")
        self.assertTrue(out["merged"])
        self.assertEqual(out["adopted"], [])
        self.assertEqual(out["stage"], "s3-impl")
        self.assert_merged(parent, kid, file)
        self.assertFalse(self.task(parent)["needs_owner"])

    def test_single_started_portion_slices_as_before(self) -> None:
        parent, kid, _file = self.single_portion_parent()
        store.update_task(self.conn, kid, stage="s1-spec", actor="agent:t")
        self.fake_finish(parent, "s1-spec", "готово")
        task = self.task(parent)
        self.assertEqual(task["stage"], "s1-spec")
        self.assertTrue(task["has_portions"])
        self.assertEqual(self.task(kid)["status"], "open")
        self.assertTrue(any("нарезано" in t for t in self.comments(parent)))

    def test_adopt_two_portions_not_merged(self) -> None:
        parent, a, b = self.stuck_parent()
        for kid, letter in ((a, "a"), (b, "b")):
            file = self.tmp_path / f"{parent}.{letter}.md"
            file.write_text("# x\n", encoding="utf-8")
            store.update_task(self.conn, kid, spec_path=str(file), actor="agent:t")
        out = stage_launch.adopt_portions(self.conn, parent, actor="agent:t")
        self.assertFalse(out["merged"])
        self.assertEqual(out["adopted"], [a, b])

    def spec_only_parent(self) -> str:
        self.add_route({"spec": {"harness": "probe"}}, key="spec-only")
        parent = self.add_task(route="spec-only", stage="s1-spec")
        self.add_task(parent=parent, route="")
        return parent

    def test_adopt_without_role_after_spec(self) -> None:  # 5
        parent = self.spec_only_parent()
        out = stage_launch.adopt_portions(self.conn, parent, actor="agent:t")
        self.assertIsNone(out["stage"])
        self.assertEqual(out["adopted"], [])
        self.assertTrue(self.task(parent)["needs_owner"])

    def cli(self, *argv: str):
        import os
        import pathlib
        import subprocess
        self.conn.commit()
        binary = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"
        return subprocess.run(
            [sys.executable, str(binary), "--local", "portions", "adopt", *argv],
            capture_output=True, text=True, cwd=str(self.tmp_path),
            env={**os.environ, "LISTIK_DB": str(self.db_path), "LISTIK_PROJECT": ""})

    def test_cli_human_without_role(self) -> None:  # 5
        parent = self.spec_only_parent()
        run = self.cli(parent)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("порции не получили этап: после s1-spec роли нет", run.stdout)

    def test_cli_json(self) -> None:  # 6
        parent, a, b = self.stuck_parent()
        run = self.cli(parent, "--json")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)["adopted"], [a, b])

    def test_http(self) -> None:  # 6
        import threading
        from listik import errors, paths, server
        saved = (paths.DB_PATH, server._conn_made, server._conn_local)
        paths.DB_PATH = self.db_path
        server._conn_made = False
        server._conn_local = threading.local()

        def restore() -> None:
            conn = getattr(server._conn_local, "conn", None)
            if conn is not None:
                conn.close()
            paths.DB_PATH, server._conn_made, server._conn_local = saved
        self.addCleanup(restore)

        def post(tid: str):
            self.conn.commit()
            try:
                return server.handle("POST", f"/api/tasks/{tid}/portions/adopt", {},
                                     {"actor": "agent:t"}, authed=True) + ("",)
            except Exception as exc:  # noqa: BLE001 — разбираем как сервер
                return server.error_response(exc)

        parent, a, b = self.stuck_parent()
        status, data, _ = post(parent)
        self.assertEqual(status, 200, data)
        self.assertEqual(data["adopted"], [a, b])
        other, _a, _b = self.stuck_parent()
        self.conn.execute("UPDATE tasks SET stage = 's3-impl' WHERE id = ?", (other,))
        status, message, code = post(other)
        self.assertEqual((status, code), (409, errors.CONFLICT), message)


if __name__ == "__main__":
    unittest.main()
