"""Сквозная приёмка автономности роя (listik-evgc, порция e) на живом сервере
Listik, настоящем `bin/listik`, настоящем git и поддельном воркере.

Обвязка — `SwarmBarrierE2ECase` (не копируется). Свой шаблон воркера — три ветки
поколения поверх `_WORKER_SRC`: красный файл, пустой дифф, мягкий вопрос.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from listik import store
from tests.test_swarm_barrier_e2e import GREEN_INTEGRATION, MERGED_MARK, SwarmBarrierE2ECase
from tests.test_swarm_e2e import LISTIK_BIN, _WORKER_SRC

VERIFY_BAD = [[sys.executable, "-c",
               "import os,sys; sys.exit(1 if os.path.exists('bad.txt') else 0)"]]

REJECTED_MARK = "рой: не принята:"

# Воркер `_WORKER_SRC` плюс три ветки по `LISTIK_GENERATION`. Hang/crash родителя
# не трогаем. `bad.txt` пишется только в «плохом» поколении и снимается следующим,
# чтобы починка была видна коммитом.
_AUTONOMY_WORKER_SRC = (
    _WORKER_SRC
    .replace(
        'call("claim", task_id, "--holder", "fake", "--actor", "agent:fake")\n',
        'call("claim", task_id, "--holder", "fake", "--actor", "agent:fake")\n'
        '\n'
        'if os.environ.get("FAKE_QUESTION_GENERATION") == generation:\n'
        '    call("needs-owner", task_id, "Какой формат?\\nпо умолчанию: JSON", '
        '"--actor", "agent:fake")\n'
        '    sys.exit(0)\n',
        1,
    )
    .replace(
        'if os.environ.get("FAKE_CRASH_GENERATION") == generation:\n'
        '    sys.exit(1)\n',
        'if os.environ.get("FAKE_CRASH_GENERATION") == generation:\n'
        '    sys.exit(1)\n'
        '\n'
        'if os.environ.get("FAKE_NO_COMMIT_GENERATION") == generation:\n'
        '    call("done", task_id, "-r", "ok", "--actor", "agent:fake")\n'
        '    sys.exit(0)\n',
        1,
    )
    .replace(
        'git("add", "-A")',
        'if os.environ.get("FAKE_BAD_GENERATION") == generation:\n'
        '    with open("bad.txt", "w", encoding="utf-8") as fh:\n'
        '        fh.write("bad\\n")\n'
        'elif os.path.exists("bad.txt"):\n'
        '    os.remove("bad.txt")\n'
        '\n'
        'git("add", "-A")',
        1,
    )
)
assert _AUTONOMY_WORKER_SRC != _WORKER_SRC
assert "FAKE_BAD_GENERATION" in _AUTONOMY_WORKER_SRC
assert "FAKE_NO_COMMIT_GENERATION" in _AUTONOMY_WORKER_SRC
assert "FAKE_QUESTION_GENERATION" in _AUTONOMY_WORKER_SRC
assert "FAKE_HANG_GENERATION" in _AUTONOMY_WORKER_SRC
assert "FAKE_CRASH_GENERATION" in _AUTONOMY_WORKER_SRC


class SwarmAutonomyE2ECase(SwarmBarrierE2ECase):
    """Общий воркер порции e. Уборка — унаследованная (`lsof` по `.worktrees`
    уже видит `detached`-верификатор)."""

    def setUp(self) -> None:
        super().setUp()
        self.worker_py.write_text(
            _AUTONOMY_WORKER_SRC.replace("__LISTIK_BIN__", str(LISTIK_BIN).replace("\\", "\\\\")),
            encoding="utf-8")

    def _ctx(self, proc) -> str:
        return (f"--- хвост лога ---\n{self.swarm_log_tail(proc, 200)}\n"
                f"--- карточки ---\n{self._scenario_state()}")


class RedVerifyReopensTests(SwarmAutonomyE2ECase):
    """Сценарий 1: красный верификатор переоткрывает, перезапускает и вливает."""

    def test_red_verify_reopens_relaunches_and_merges(self):
        a = self.scenario_task("A", route="fake-low")
        aid = a["id"]
        self.write_swarm_config({"verify": VERIFY_BAD, "integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_BAD_GENERATION": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 0, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertEqual(row["generation"], 3, ctx)

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

        revokes = self.events(aid, "revoke")
        self.assertEqual(len(revokes), 1, revokes)
        self.assertTrue(
            (revokes[0]["note"] or "").startswith("рой: перезапуск — не принята (верификатор)"),
            revokes[0]["note"])

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
    """Сценарий 2: пустой дифф отклоняется, следующее поколение вливается."""

    def test_empty_diff_rejected_then_fixed(self):
        a = self.scenario_task("A", route="fake-low")
        aid = a["id"]
        self.write_swarm_config({"verify": [], "integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_NO_COMMIT_GENERATION": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 0, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertEqual(row["generation"], 3, ctx)

        rejected = self.marked_records(aid, REJECTED_MARK)
        self.assertEqual(len(rejected), 1, rejected)
        rec = rejected[0]
        self.assertEqual(rec["reason"], "empty")
        self.assertIsNone(rec["command"])
        self.assertIsNone(rec["log"])

        revokes = self.events(aid, "revoke")
        self.assertEqual(len(revokes), 1, revokes)
        self.assertTrue(
            (revokes[0]["note"] or "").startswith("рой: перезапуск — не принята (верификатор)"),
            revokes[0]["note"])

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
                                extra_env={"FAKE_BAD_GENERATION": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 2, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertTrue(row["needs_owner"])
        self.assertEqual(row["generation"], 1, ctx)

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
    """Сценарий 4: мягкий вопрос по таймауту — автоответ и перезапуск, не «упала»."""

    def test_soft_question_defaulted_after_timeout(self):
        a = self.scenario_task("A", route="fake-low")
        aid = a["id"]
        self.write_swarm_config({
            "question_timeout": 0.02,
            "verify": [],
            "integration": GREEN_INTEGRATION,
        })

        proc = self.start_swarm(parallel=2, interval=1,
                                extra_env={"FAKE_QUESTION_GENERATION": "1"})
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 0, ctx)

        row = self.row(aid)
        self.assertEqual(row["status"], "done", ctx)
        self.assertEqual(row["generation"], 3, ctx)
        self.assertFalse(row["needs_owner"])

        questions = self.comments(aid, "question")
        self.assertEqual(len(questions), 1, questions)
        self.assertIn("по умолчанию: JSON", questions[0]["text"])
        self.assertEqual(questions[0]["author"], "agent:fake")

        answers = self.comments(aid, "answer")
        self.assertEqual(len(answers), 1, answers)
        self.assertEqual(answers[0]["author"], "agent:listik-swarm")
        self.assertTrue(
            answers[0]["text"].startswith(
                "рой: ответа не было 0.02 мин — действует вариант по умолчанию: JSON"),
            answers[0]["text"])
        self.assertFalse(any("рой: процесс задачи завершился" in text
                             for text in self.question_texts(aid)))

        revokes = self.events(aid, "revoke")
        self.assertEqual(len(revokes), 1, revokes)
        self.assertTrue(
            (revokes[0]["note"] or "").startswith("рой: перезапуск — ответ по умолчанию"),
            revokes[0]["note"])

        merged = self.marked_records(aid, MERGED_MARK)
        self.assertEqual(len(merged), 1, merged)

        log_text = "\n".join(self.swarm_log_lines(proc))
        self.assertIn(f"ответ по умолчанию {aid}: JSON", log_text)
        self.assertIn(f"дефолт 1 ({aid})", log_text)


class BudgetMaxLaunchesTests(SwarmAutonomyE2ECase):
    """Сценарий 5: --max-launches 1 останавливает прогон кодом 5."""

    def test_budget_max_launches_stops_with_code_5(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        self.write_swarm_config({"verify": [], "integration": GREEN_INTEGRATION})

        proc = self.start_swarm(parallel=2, interval=1, extra_args=["--max-launches", "1"])
        code = self.wait_swarm(proc, deadline=90)
        ctx = self._ctx(proc)
        self.assertEqual(code, 5, ctx)

        rows = {t["id"]: self.row(t["id"]) for t in (a, b)}
        done_ids = [i for i, row in rows.items() if row["status"] == "done"]
        open_ids = [i for i, row in rows.items() if row["status"] == "open"]
        self.assertEqual(len(done_ids), 1, ctx)
        self.assertEqual(len(open_ids), 1, ctx)
        done_id, open_id = done_ids[0], open_ids[0]

        self.assertEqual(rows[done_id]["generation"], 1, ctx)
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
        self.assertIn("запусков 1 из 1", log_text)
        waiting = [ln for ln in lines if "ждут:" in ln]
        self.assertTrue(any(f"{open_id} бюджет" in ln for ln in waiting), waiting)
        self.assertEqual(self.halt_card_ids(), [])

        wt = self.project_dir / ".worktrees"
        self.assertFalse(wt.exists() and any(wt.iterdir()))


if __name__ == "__main__":
    unittest.main()
