"""Сквозная приёмка автономности роя (listik-evgc, порция e) на живом сервере
Listik, настоящем `bin/listik`, настоящем git и поддельном воркере.

Обвязка — `SwarmBarrierE2ECase` (не копируется). Режим роя (listik-w7ge, порция f):
каждый этап — отдельный запуск роли, `generation` +1 на запуск и +1 на `revoke`; ветки
сценариев (красный файл, пустой дифф, мягкий вопрос) — флаги воркера `_WORKER_SRC` у роли
`impl`, «один раз» = первый запуск `impl` карточки. Переоткрытая барьером или отвеченная
карточка запускается снова обычным кандидатом — без `revoke`.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from listik import store
from tests.test_swarm_barrier_e2e import GREEN_INTEGRATION, MERGED_MARK, SwarmBarrierE2ECase

VERIFY_BAD = [[sys.executable, "-c",
               "import os,sys; sys.exit(1 if os.path.exists('bad.txt') else 0)"]]

REJECTED_MARK = "рой: не принята:"


class SwarmAutonomyE2ECase(SwarmBarrierE2ECase):
    """Воркер — унаследованный (`_WORKER_SRC` + `shared.txt` барьера); ветки сценариев
    включаются флагами `FAKE_BAD_ONCE`/`FAKE_EMPTY_ONCE`/`FAKE_QUESTION_ONCE`. Уборка —
    унаследованная (`lsof` по `.worktrees` уже видит `detached`-верификатор)."""

    def _ctx(self, proc) -> str:
        return (f"--- хвост лога ---\n{self.swarm_log_tail(proc, 200)}\n"
                f"--- карточки ---\n{self._scenario_state()}")


class RedVerifyReopensTests(SwarmAutonomyE2ECase):
    """Сценарий 1: красный верификатор переоткрывает, рой снова запускает и вливает."""

    def test_red_verify_reopens_relaunches_and_merges(self):
        a = self.scenario_task("A", route="fake-low")
        aid = a["id"]
        self.write_swarm_config({"verify": VERIFY_BAD, "integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_BAD_ONCE": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 0, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertEqual(row["generation"], 6, ctx)  # spec, critic, impl, judge, impl, judge

        rejected = self.marked_records(aid, REJECTED_MARK)
        self.assertEqual(len(rejected), 1, rejected)
        rec = rejected[0]
        self.assertEqual(rec["reason"], "red")
        self.assertEqual(rec["attempt"], 1)
        self.assertEqual(rec["command"], VERIFY_BAD[0])
        self.assertEqual(rec["code"], 1)
        self.assertIs(rec["timed_out"], False)
        log_path = Path(rec["log"])
        self.assertTrue(log_path.is_file(), rec["log"])
        self.assertEqual(log_path.parent, self.swarm_log_dir)
        self.assertTrue(log_path.name.startswith(f"verify-{aid}-") and log_path.name.endswith(".log"),
                        log_path.name)
        self.assertIsInstance(rec["tail"], str)
        self.assertIn("listik done", rec["next"])

        status_ev = self.events(aid, "status")
        self.assertTrue(
            any(e["from_value"] == "done" and e["to_value"] == "open" for e in status_ev),
            status_ev)
        stage_ev = self.events(aid, "stage")
        self.assertTrue(any(e["to_value"] == "s3-impl" for e in stage_ev), stage_ev)

        # Переоткрытую карточку рой берёт обычным кандидатом — без `revoke`.
        self.assertEqual(self.events(aid, "revoke"), [])
        self.assertEqual(self.launch_stages(aid).count("s3-impl"), 2, self.journal_texts(aid))

        merged = self.marked_records(aid, MERGED_MARK)
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(merged[0]["files"], [f"{aid}.txt"], merged)
        self.assertEqual(self.head_sha("main"), merged[0]["sha"])
        self.assertNotIn("bad.txt", self.git("ls-tree", "-r", "--name-only", "main").splitlines())
        self.assertFalse((self.project_dir / "bad.txt").exists())
        subjects = self.commit_subjects("main")
        self.assertEqual(subjects.count(aid), 2, subjects)
        bad_log = self.git("log", "main", "--format=%s", "--name-status", "--", "bad.txt")
        self.assertIn("A\tbad.txt", bad_log)
        self.assertIn("D\tbad.txt", bad_log)

        self.assertEqual(self.question_texts(aid), [])
        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertIn(f"не принята {aid}: red, попытка 1 из 1 → воркеру", log_text)
        self.assertIn(f"верификатор {aid}:", log_text)
        self.assertIn(f"не приняты 1 ({aid})", log_text)


class EmptyDiffRejectedTests(SwarmAutonomyE2ECase):
    """Сценарий 2: пустой дифф отклоняется, следующий запуск `impl` вливается."""

    def test_empty_diff_rejected_then_fixed(self):
        a = self.scenario_task("A", route="fake-low")
        aid = a["id"]
        self.write_swarm_config({"verify": [], "integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_EMPTY_ONCE": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 0, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertEqual(row["generation"], 6, ctx)  # spec, critic, impl, judge, impl, judge

        rejected = self.marked_records(aid, REJECTED_MARK)
        self.assertEqual(len(rejected), 1, rejected)
        rec = rejected[0]
        self.assertEqual(rec["reason"], "empty")
        self.assertIsNone(rec["command"])
        self.assertIsNone(rec["log"])

        # Переоткрытую карточку рой берёт обычным кандидатом — без `revoke`.
        self.assertEqual(self.events(aid, "revoke"), [])
        self.assertEqual(self.launch_stages(aid).count("s3-impl"), 2, self.journal_texts(aid))

        merged = self.marked_records(aid, MERGED_MARK)
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(merged[0]["files"], [f"{aid}.txt"], merged)


class VerifyRetriesExhaustedTests(SwarmAutonomyE2ECase):
    """Сценарий 3: предел отклонений — человеку, зависимая не стартует, стопа нет."""

    def test_verify_retries_exhausted_goes_to_human(self):
        a = self.scenario_task("A", route="fake-low")
        c = self.scenario_task("C", route="fake-low")
        aid = a["id"]
        store.add_dep(self.conn, c["id"], aid, dep_type="blocks", created_by="dmitry")
        self.write_swarm_config({
            "verify": VERIFY_BAD,
            "verify_retries": 0,
            "integration": GREEN_INTEGRATION,
        })
        start_head = self.head_sha("main")

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_BAD_ONCE": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 2, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertTrue(row["needs_owner"])
        self.assertEqual(row["generation"], 4, ctx)  # spec, critic, impl, judge

        questions = self.question_texts(aid)
        self.assertEqual(len(questions), 1, questions)
        self.assertTrue(questions[0].startswith("рой: не влита — "), questions[0])
        self.assertIn("отклонена 1 раз", questions[0])
        self.assertIn("тесты красные", questions[0])

        rejected = self.marked_records(aid, REJECTED_MARK)
        self.assertEqual(len(rejected), 1, rejected)
        self.assertEqual(rejected[0]["attempt"], 1)
        self.assertEqual(self.events(aid, "revoke"), [])

        self.assertEqual(self.head_sha("main"), start_head)
        row_c = self.row(c["id"])
        self.assertEqual(row_c["generation"], 0)
        self.assertFalse(row_c["launched_by"])
        self.assertEqual(self.halt_card_ids(), [])

        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertIn("→ человеку", log_text)
        self.assertIn("не влиты 1", log_text)


class SoftQuestionDefaultedTests(SwarmAutonomyE2ECase):
    """Сценарий 4: мягкий вопрос по таймауту — автоответ и новый запуск этапа, не «упала»."""

    def test_soft_question_defaulted_after_timeout(self):
        a = self.scenario_task("A", route="fake-low")
        aid = a["id"]
        self.write_swarm_config({
            "question_timeout": 0.02,
            "verify": [],
            "integration": GREEN_INTEGRATION,
        })

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_QUESTION_ONCE": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 0, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertEqual(row["generation"], 5, ctx)  # spec, critic, impl, impl, judge
        self.assertFalse(row["needs_owner"])

        # Вопрос роли (`вопрос` последней строкой) пишет Listik от `agent:listik`.
        questions = self.comments(aid, "question")
        self.assertEqual(len(questions), 1, questions)
        self.assertIn("по умолчанию: JSON", questions[0]["text"])
        self.assertEqual(questions[0]["author"], "agent:listik")

        answers = self.comments(aid, "answer")
        self.assertEqual(len(answers), 1, answers)
        self.assertEqual(answers[0]["author"], "agent:listik-swarm")
        self.assertTrue(
            answers[0]["text"].startswith(
                "рой: ответа не было 0.02 мин — действует вариант по умолчанию: JSON"),
            answers[0]["text"])
        self.assertFalse(any("рой: процесс задачи завершился" in text or "не сдал работу" in text
                             for text in self.question_texts(aid)))

        # После автоответа карточка — обычный кандидат: новый запуск `impl` без `revoke`.
        self.assertEqual(self.events(aid, "revoke"), [])
        self.assertEqual(self.launch_stages(aid).count("s3-impl"), 2, self.journal_texts(aid))

        merged = self.marked_records(aid, MERGED_MARK)
        self.assertEqual(len(merged), 1, merged)

        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertIn(f"ответ по умолчанию {aid}: JSON", log_text)
        self.assertIn(f"дефолт 1 ({aid})", log_text)


class BudgetMaxLaunchesTests(SwarmAutonomyE2ECase):
    """Сценарий 5: бюджет запусков останавливает прогон кодом 5 — одна карточка
    закрыта и влита, вторая не тронута. В режиме роя каждый этап — запуск, поэтому
    бюджет — четыре запуска (весь проход A), а `parallel=1` и приоритет B ниже A
    не дают B взять запуск из бюджета."""

    def test_budget_max_launches_stops_with_code_5(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        # При равных приоритетах порядок кандидатов не гарантирован — B ниже A.
        store.update_task(self.conn, b["id"], priority=a["priority"] + 1)
        self.write_swarm_config({"verify": [], "integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=1, interval=1, extra_args=["--max-launches", "4"])
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 5, ctx)

        rows = {t["id"]: self.row(t["id"]) for t in (a, b)}
        done_id, open_id = a["id"], b["id"]
        self.assertEqual(rows[done_id]["status"], "done", ctx)
        self.assertEqual(rows[open_id]["status"], "open", ctx)

        self.assertEqual(rows[done_id]["generation"], 4, ctx)  # spec, critic, impl, judge
        merged = self.marked_records(done_id, MERGED_MARK)
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(self.head_sha("main"), merged[0]["sha"])

        self.assertEqual(rows[open_id]["generation"], 0)
        self.assertFalse(rows[open_id]["launched_by"])
        self.assertFalse(rows[open_id]["needs_owner"])

        lines = self.swarm_log_lines(proc)
        log_text = "\n".join(lines)
        self.assertIn("бюджет: минут", log_text)
        self.assertIn("итог: бюджет исчерпан — минут", log_text)
        self.assertIn("запусков 4 из 4", log_text)
        waiting = [ln for ln in lines if "ждут:" in ln]
        self.assertTrue(any(f"{open_id} бюджет" in ln for ln in waiting), waiting)
        self.assertEqual(self.halt_card_ids(), [])

        wt = self.project_dir / ".worktrees"
        self.assertFalse(wt.exists() and any(wt.iterdir()))


if __name__ == "__main__":
    unittest.main()
