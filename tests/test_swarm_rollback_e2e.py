"""Сквозная приёмка предела откатов (listik-feox, порция c) на живом сервере Listik,
настоящем `bin/listik-swarm`, настоящем `listik watch` и git.

Юнит-тесты порции b гоняют тик на подставном `listik`. Здесь две задачи пишут один
`shared.txt`, наблюдатель замораживает опоздавшую, а рой либо паркует её по порогу
(`max_freezes: 0`), либо после разморозки перезапускает (дефолт 2). Кто из пары
владелец, тест не предполагает: замороженную находит по `рой: заморожена:`.

Обвязка (`SwarmBarrierE2ECase`) не копируется и не меняется.
"""
from __future__ import annotations

import json
import unittest

from listik import store

from tests.test_swarm_barrier_e2e import (
    ARBITER_CMD,
    MERGED_MARK,
    SWARM_AUTHOR,
    UNFROZEN_MARK,
    SwarmBarrierE2ECase,
)
from tests.test_swarm_e2e import LISTIK_BIN, _WORKER_SRC

FREEZE_MARK = "рой: заморожена:"

# Перед сном: если id задачи в `FAKE_SHARED_EARLY`, пишем `shared.txt` одной строкой
# `<id>`. Дальше шаблон прежний — сон, `<id>.txt`, `git add -A` (подхватит и
# `shared.txt`), commit, done. Новых `FAKE_*`, кроме `FAKE_SHARED_EARLY`, нет.
_SLEEP_LINE = 'time.sleep(float(os.environ.get("FAKE_WORKER_SLEEP", "0") or "0"))'
_ROLLBACK_WORKER_SRC = _WORKER_SRC.replace(
    _SLEEP_LINE,
    'early = [item.strip() for item in os.environ.get("FAKE_SHARED_EARLY", "").split(",") if item.strip()]\n'
    'if task_id in early:\n'
    '    with open("shared.txt", "w", encoding="utf-8") as fh:\n'
    '        fh.write(task_id + "\\n")\n'
    '\n'
    + _SLEEP_LINE,
    1,
)
assert _ROLLBACK_WORKER_SRC != _WORKER_SRC


class RollbackLimitE2ETests(SwarmBarrierE2ECase):
    """Парковка по пределу и штатный перезапуск после той же заморозки."""

    def setUp(self) -> None:
        super().setUp()
        self.worker_py.write_text(
            _ROLLBACK_WORKER_SRC.replace("__LISTIK_BIN__", str(LISTIK_BIN).replace("\\", "\\\\")),
            encoding="utf-8")

    def _log_messages(self, proc) -> list[str]:
        messages = []
        for line in self.swarm_log_lines(proc):
            bracket = line.find("] ")
            if line.startswith("[") and bracket != -1:
                messages.append(line[bracket + 2:])
            else:
                messages.append(line)
        return messages

    def _frozen_and_owner(self, a_id: str, b_id: str) -> tuple[str, str]:
        found = []
        for tid in (a_id, b_id):
            records = self.marked_records(tid, FREEZE_MARK)
            if records:
                found.append((tid, records))
        self.assertEqual(len(found), 1, found)
        frozen, records = found[0]
        self.assertEqual(len(records), 1, records)
        owner = b_id if frozen == a_id else a_id
        self.assertEqual(records[0]["owner"], owner, records[0])
        self.assertEqual(records[0]["files"], ["shared.txt"], records[0])
        self.assertEqual(self.marked_records(owner, FREEZE_MARK), [])
        return frozen, owner

    def _worktree(self, task_id: str):
        return self.project_dir / ".worktrees" / task_id

    def test_freeze_limit_parks_task_others_ride(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        c = self.scenario_task("C", route="fake-low")
        # Приоритет по умолчанию у всех троих одинаковый — при равных `updated_at`
        # порядок волны (priority ASC, updated_at DESC) не определён. Понижаем C, чтобы
        # в партию из двух гарантированно попали A и B, а порядок создания не менять.
        store.update_task(self.conn, c["id"], priority=max(a["priority"], b["priority"]) + 1)
        c = self.row(c["id"])
        self.write_swarm_config({"integration": [], "max_freezes": 0})

        proc = self.start_swarm(
            parallel=2, interval=1,
            extra_env={
                "FAKE_SHARED_EARLY": f"{a['id']},{b['id']}",
                "FAKE_WORKER_SLEEP": "6",
            })
        code = self.wait_swarm(proc, deadline=120)
        self.assertEqual(code, 2, self.swarm_log_tail(proc, 200))

        frozen, owner = self._frozen_and_owner(a["id"], b["id"])
        c_id = c["id"]

        for tid in (owner, c_id):
            row = self.row(tid)
            self.assertEqual(row["status"], "done", tid)
            self.assertEqual(len(self.marked_records(tid, MERGED_MARK)), 1, tid)
            self.git("show", f"main:{tid}.txt")
            self.assertFalse(self._worktree(tid).exists(), tid)
        self.assertEqual(self.git("show", "main:shared.txt").strip(), owner)

        m = self.marked_records(c_id, MERGED_MARK)[0]
        self.assertEqual(m["files"], [f"{c_id}.txt"], m)
        self.assertEqual(m["outside"], [], m)

        row_f = self.row(frozen)
        self.assertIn(row_f["status"], ("open", "in_progress"), row_f["status"])
        self.assertEqual(row_f["needs_owner"], 1)
        self.assertEqual(row_f["generation"], 2)
        labels = json.loads(row_f["labels"] or "[]")
        self.assertFalse(
            any(isinstance(label, str) and label.startswith("frozen-by:") for label in labels),
            labels)
        unfrozen = self.marked_records(frozen, UNFROZEN_MARK)
        self.assertEqual(len(unfrozen), 1, unfrozen)
        self.assertIs(unfrozen[0]["rebased"], False, unfrozen[0])
        self.assertEqual(unfrozen[0]["conflicts"], ["shared.txt"], unfrozen[0])
        self.assertEqual(len(self.journal_texts(frozen)), 1, self.journal_texts(frozen))
        self.assertTrue(self._worktree(frozen).is_dir(), frozen)

        questions = self.question_texts(frozen)
        self.assertTrue(questions, questions)
        question = questions[-1]
        self.assertTrue(question.startswith("рой: предел откатов"), question)
        self.assertIn(owner, question)
        self.assertIn("shared.txt", question)
        self.assertIn("max_freezes = 0", question)
        self.assertIn(f"listik needs-owner {frozen} --clear", question)
        self.assertEqual(self.comments(frozen, "question")[-1]["author"], SWARM_AUTHOR)

        messages = self._log_messages(proc)
        log_text = "\n".join(messages)
        self.assertLess(
            log_text.index(f"needs-owner {frozen}: freeze_limit"),
            log_text.index(f"запуск {c_id}"),
            log_text)
        self.assertIn(f"откат {frozen} (владелец {owner})", log_text)
        self.assertIn(f"по пределу 1 ({frozen})", log_text)
        itogs = [line for line in messages if line.startswith("итог:")]
        self.assertTrue(itogs, log_text)
        itog = itogs[-1]
        self.assertTrue(itog.startswith("итог: закрыто 2 ("), itog)
        self.assertIn(
            f"откатов 1 (на откаты 0 мин), по пределу 1 ({frozen}), оставлено человеку 1",
            itog)
        self.assertIn(f"{frozen} — предел откатов", log_text)
        self.assertEqual(sum(line.count(f"запуск {frozen}") for line in messages), 1, log_text)

    def test_default_limit_relaunches_after_unfreeze(self):
        a = self.scenario_task("A", route="fake-low")
        b = self.scenario_task("B", route="fake-low")
        self.write_swarm_config({"integration": [], "arbiter": ARBITER_CMD})

        proc = self.start_swarm(
            parallel=2, interval=1,
            extra_env={
                "FAKE_SHARED_EARLY": f"{a['id']},{b['id']}",
                "FAKE_WORKER_SLEEP": "6",
                "FAKE_ARBITER_MODE": "ok",
            })
        code = self.wait_swarm(proc, deadline=150)
        self.assertEqual(code, 0, self.swarm_log_tail(proc, 200))

        frozen, owner = self._frozen_and_owner(a["id"], b["id"])
        self.assertFalse(
            any("рой: предел откатов" in text for text in self.question_texts(frozen)),
            self.question_texts(frozen))
        self.assertEqual(len(self.journal_texts(frozen)), 2, self.journal_texts(frozen))
        row_f = self.row(frozen)
        self.assertEqual(row_f["generation"], 3)
        self.assertEqual(row_f["status"], "done")
        merged = self.marked_records(frozen, MERGED_MARK)
        self.assertEqual(len(merged), 1, merged)
        self.assertEqual(merged[0]["arbiter"], True, merged[0])
        self.git("show", f"main:{a['id']}.txt")
        self.git("show", f"main:{b['id']}.txt")

        log_text = "\n".join(self._log_messages(proc))
        self.assertIn(
            f"откат {frozen} (владелец {owner}): заморозка 1 из 2 в окне",
            log_text)
        self.assertIn("по пределу 0 ()", log_text)
        self.assertIn(f"откаты 1 ({frozen})", log_text)
        self.assertIn("на откаты 0 мин", log_text)
        self.assertFalse(list((self.project_dir / ".worktrees").iterdir()))


if __name__ == "__main__":
    unittest.main()
