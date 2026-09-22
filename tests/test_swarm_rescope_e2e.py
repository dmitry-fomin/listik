"""Сквозная приёмка rescope в тике роя на живом сервере (listik-3wul, порция c).

Путь целиком: рой → барьер вливает волну и пишет `рой: влито:` → рой зовёт
`listik rescope --apply` по HTTP → сервер читает ТЗ из файла и зовёт модель
(подставную, `tests/fixtures/fake_model.py` через `[swarm].command`) → области
записаны в карточку, копилка `рой: влито:` ушла модели в запросе графа → следующая
волна стартует. Контроль: `rescope: false` в `swarm.json` — модель не звалась.

Обвязка (`SwarmBarrierE2ECase`) наследуется как есть, воркер — её штатный.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

from listik import paths, store
from tests.test_swarm_barrier_e2e import (
    GREEN_INTEGRATION, MERGED_MARK, SWARM_AUTHOR, SwarmBarrierE2ECase,
)
from tests.test_swarm_rescope import _extract_reply, _graph_reply

FAKE_MODEL = Path(__file__).resolve().parent / "fixtures" / "fake_model.py"
_STAMP = re.compile(r"^\[[^\]]*\] ")


class SwarmRescopeE2ECase(SwarmBarrierE2ECase):
    def setUp(self) -> None:
        super().setUp()
        # Команду модели запускает сервер, живущий в процессе теста: `[swarm]` — в тот же
        # временный config.toml, окружение FAKE_MODEL_* — через `start_swarm(extra_env=)`.
        with paths.CONFIG_PATH.open("a", encoding="utf-8") as fh:
            fh.write("\n[swarm]\ncommand = [%s, %s]\n"
                     % (json.dumps(sys.executable), json.dumps(str(FAKE_MODEL))))
        self.replies_path = self.tmp_path / "fake-model-replies.json"
        self.calls_path = self.tmp_path / "fake-model-calls.jsonl"

    def model_env(self) -> dict:
        return {"FAKE_MODEL_REPLIES": str(self.replies_path),
                "FAKE_MODEL_CALLS": str(self.calls_path)}

    def spec_task(self, title: str, *, route: str | None, spec_text: str) -> dict:
        task = self.scenario_task(title, route=route)
        spec = self.project_dir / "specs" / f"{task['id']}.md"
        spec.parent.mkdir(exist_ok=True)
        # `<id>` в тексте ТЗ заменяется id задачи: до создания карточки он неизвестен.
        spec.write_text(spec_text.replace("<id>", task["id"]), encoding="utf-8")
        store.update_task(self.conn, task["id"], spec_path=str(spec.resolve()))
        self.conn.commit()
        return self.row(task["id"])

    def model_calls(self) -> list[dict]:
        if not self.calls_path.exists():
            return []
        calls = []
        for line in self.calls_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                request = json.loads(line)
                request["payload"] = json.loads(request["messages"][-1]["content"])
                calls.append(request)
        return calls

    def scenario(self, swarm_config: dict) -> tuple[dict, dict, dict, object]:
        """A, B, C с ТЗ; `C blocks A` от человека; прогон роя до выхода с кодом 0."""
        a, b, c = (self.spec_task(t, route="fake-low", spec_text="правит <id>.txt")
                   for t in ("A", "B", "C"))
        store.add_dep(self.conn, c["id"], a["id"], dep_type="blocks", created_by="dmitry")
        self.conn.commit()
        self.write_swarm_config(swarm_config)
        self.replies_path.write_text(json.dumps({
            "swarm_rescope_extract": [_extract_reply([], [f"{c['id']}.txt", "extra.txt"])],
            "swarm_rescope_graph": [_graph_reply({})],
        }), encoding="utf-8")

        proc = self.start_swarm(parallel=2, interval=1, extra_env=self.model_env())
        code = self.wait_swarm(proc, deadline=120)
        self.assertEqual(code, 0, self.swarm_log_tail(proc, 200))
        return a, b, c, proc

    def assert_wave_order(self, a, b, c) -> None:
        subjects = self.commit_subjects("main")
        self.assertEqual(subjects[0], c["id"], subjects)
        self.assertEqual(set(subjects[1:3]), {a["id"], b["id"]}, subjects)
        self.assertEqual(subjects[3], "старт", subjects)

    def log_texts(self, proc) -> list[str]:
        return [_STAMP.sub("", line) for line in self.swarm_log_lines(proc)]


class RescopeAfterMergedWaveTests(SwarmRescopeE2ECase):
    def test_rescope_after_merged_wave_writes_scopes(self):
        a, b, c, proc = self.scenario({"integration": GREEN_INTEGRATION})
        cid = c["id"]

        row_c = self.row(cid)
        self.assertEqual(json.loads(row_c["write_scope"]), [f"{cid}.txt", "extra.txt"])
        self.assertEqual(json.loads(row_c["read_scope"] or "[]"), [])
        for tid in (a["id"], b["id"]):
            self.assertEqual(json.loads(self.row(tid)["write_scope"]), [f"{tid}.txt"], tid)

        self.assert_wave_order(a, b, c)

        lines = self.log_texts(proc)
        tail = "\n".join(lines[-200:])
        prefix = f"rescope p: ТЗ прочитано 1, областей записано 1 ({cid})"
        rescope_idx = next((i for i, l in enumerate(lines) if l.startswith(prefix)), None)
        self.assertIsNotNone(rescope_idx, tail)

        def first(fragment: str) -> int:
            idx = next((i for i, l in enumerate(lines) if fragment in l), None)
            self.assertIsNotNone(idx, f"{fragment!r} нет в логе\n{tail}")
            return idx

        self.assertLess(rescope_idx, first(f"запуск {cid}"), tail)
        self.assertGreater(rescope_idx, first(f"влито {a['id']}"), tail)
        self.assertGreater(rescope_idx, first(f"влито {b['id']}"), tail)

        calls = self.model_calls()
        extracts = [x for x in calls if x["name"] == "swarm_rescope_extract"]
        self.assertTrue(any(x["payload"]["task"]["id"] == cid
                            and f"правит {cid}.txt" in x["payload"]["spec"]
                            for x in extracts), extracts)
        graphs = [x for x in calls if x["name"] == "swarm_rescope_graph"]
        self.assertTrue(graphs, calls)
        merged_drift = {d["task"] for d in graphs[0]["payload"]["drift"]
                        if d["source"] == "merged"}
        self.assertTrue({a["id"], b["id"]} <= merged_drift, graphs[0]["payload"]["drift"])

        self.assertEqual(len(self.marked_records(a["id"], MERGED_MARK)), 1)

        deps_c = [(r["depends_on"], r["dep_type"], r["created_by"]) for r in self.conn.execute(
            "SELECT * FROM deps WHERE issue_id = ?", (cid,)).fetchall()]
        self.assertEqual(deps_c, [(a["id"], "blocks", "dmitry")])
        swarm_blocks = self.conn.execute(
            "SELECT count(*) FROM deps d JOIN tasks t ON t.id = d.issue_id "
            "WHERE t.project = 'p' AND d.dep_type = 'blocks' AND d.created_by = ?",
            (SWARM_AUTHOR,)).fetchone()[0]
        self.assertEqual(swarm_blocks, 0)

        self.assertEqual(self.halt_card_ids(), [])
        self.assertEqual(self.comments(cid, "question"), [])


class RescopeDisabledTests(SwarmRescopeE2ECase):
    def test_rescope_disabled_in_swarm_json(self):
        a, b, c, proc = self.scenario({"integration": GREEN_INTEGRATION, "rescope": False})
        cid = c["id"]

        self.assertEqual(json.loads(self.row(cid)["write_scope"]), [f"{cid}.txt"])
        self.assertEqual(self.model_calls(), [])
        lines = self.log_texts(proc)
        self.assertIn("rescope: выключен в swarm.json", lines)
        self.assertFalse([l for l in lines if l.startswith("rescope p:")], lines)
        self.assert_wave_order(a, b, c)


if __name__ == "__main__":
    unittest.main()
