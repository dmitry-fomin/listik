"""`listik rescope` / `POST /api/swarm/rescope` / `swarm_llm.rescope` (listik-kbh5, шаг
swarm-7, порция c): области `read_scope`/`write_scope` из готовых ТЗ и уточнение графа
`blocks` по копилке расхождений «объявил X, тронул Y».

Реальная сеть/подпроцессы не участвуют: функция/HTTP-тесты мокают `swarm_llm.complete_json`,
CLI-подпроцесс идёт через фикстуру `tests/fixtures/fake_model.py` (канал `command`).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

from listik import db as db_mod
from listik import errors, store, swarm_llm
from listik.swarm_watch import SCOPE_MARK
from tests.helpers import TempDbTestCase
from tests.test_owner_http import AUTH, LOCAL_CONFIG, OwnerHttpCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
REPO_ROOT = LISTIK_BIN.parent.parent
FAKE_MODEL = Path(__file__).resolve().parent / "fixtures" / "fake_model.py"

MERGED_MARK = swarm_llm.MERGED_MARK


def _snapshot(conn, table: str) -> set:
    cols = {"tasks": "*", "deps": "issue_id, depends_on, dep_type, created_by",
           "comments": "task_id, author, kind, text", "events": "*"}[table]
    return {tuple(r) for r in conn.execute(f"SELECT {cols} FROM {table}").fetchall()}


class RescopeTestCase(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        store.upsert_project(self.conn, "demo", path=str(self.tmp_path))
        self.spec_dir = self.tmp_path / "specs"
        self.spec_dir.mkdir(exist_ok=True)

    def _task(self, title: str, *, spec: str | None = None, status: str = "open") -> str:
        tid = store.create_task(self.conn, title=title, project="demo",
                                status=status)["id"]
        if spec is not None:
            spec_path = self.spec_dir / f"{tid}.md"
            spec_path.write_text(spec, encoding="utf-8")
            store.update_task(self.conn, tid, spec_path=str(spec_path))
        self.conn.commit()
        return tid

    def _watch_drift(self, tid: str, files: list[str], declared: list[str]) -> None:
        payload = json.dumps({"files": files, "declared": declared}, ensure_ascii=False)
        store.add_comment(self.conn, tid, f"{SCOPE_MARK} {payload}",
                          author="agent:listik-swarm", kind="journal")

    def _merged_drift(self, tid: str, *, files: list[str], declared: list[str],
                      outside: list[str] | None = None) -> None:
        data = {"sha": "abc", "branch": f"task/{tid}", "base": "main",
                "files": files, "declared": declared}
        if outside is not None:
            data["outside"] = outside
        payload = json.dumps(data, ensure_ascii=False)
        store.add_comment(self.conn, tid, f"{MERGED_MARK} {payload}",
                          author="agent:listik-swarm", kind="journal")


def _extract_reply(read_scope: list[str], write_scope: list[str], summary: str = "…") -> dict:
    return {"read_scope": read_scope, "write_scope": write_scope, "summary": summary}


def _graph_reply(edges: dict[str, list[str]]) -> dict:
    return {"tasks": [{"id": tid, "depends_on": list(deps_), "reason": "потому что"}
                      for tid, deps_ in edges.items()]}


def _reply_router(replies_by_task: dict[str, dict], graph_replies: list[dict]):
    """Ответы extract-фазы читаются по id задачи в теле запроса (порядок извлечения
    роем не гарантирован — задачи с одинаковым `created_at` сортируются по id)."""
    counters = {"__graph__": 0}

    def _side_effect(messages, schema, *, name, cfg_settings, opener=None, runner=None,
                     timeout=None):
        if name == "swarm_rescope_extract":
            payload = json.loads(messages[1]["content"])
            return replies_by_task[payload["task"]["id"]]
        index = counters["__graph__"]
        counters["__graph__"] = index + 1
        return graph_replies[min(index, len(graph_replies) - 1)]

    return _side_effect


# --------------------------------------------------------------------------- функция rescope


class ExtractionTests(RescopeTestCase):
    def test_extraction_and_graph(self) -> None:
        a = self._task("A", spec="ТЗ A: правь listik/store.py и tests")
        b = self._task("B", spec="ТЗ B: опирается на A")

        reply_a = _extract_reply(["docs/API.md"],
                                 ["./listik/store.py", "listik/store.py", "tests/"])
        reply_b = _extract_reply([], ["listik/other.py"])
        graph = _graph_reply({b: [a], a: []})

        router = _reply_router({a: reply_a, b: reply_b}, [graph])
        with mock.patch.object(swarm_llm, "complete_json", side_effect=router) as mocked:
            before_tasks = _snapshot(self.conn, "tasks")
            before_deps = _snapshot(self.conn, "deps")
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})

        self.assertEqual(out["extracted"], 2)
        self.assertEqual(out["attempts"], 1)
        self.assertEqual(out["tasks"][a]["write_scope"], ["listik/store.py", "tests"])
        self.assertEqual(out["tasks"][a]["source"], "spec")
        self.assertIs(out["tasks"][a]["changed"], True)
        self.assertEqual(out["edges"], [[a, b]])
        names = [c.kwargs["name"] for c in mocked.call_args_list]
        self.assertEqual(names, ["swarm_rescope_extract", "swarm_rescope_extract",
                                 "swarm_rescope_graph"])

        extract_calls = [c for c in mocked.call_args_list
                        if c.kwargs["name"] == "swarm_rescope_extract"]
        call_for_a = next(c for c in extract_calls
                          if json.loads(c[0][0][1]["content"])["task"]["id"] == a)
        self.assertEqual(call_for_a[0][1], swarm_llm.EXTRACT_SCHEMA)
        payload = json.loads(call_for_a[0][0][1]["content"])
        self.assertEqual(payload["task"]["id"], a)
        self.assertIn("ТЗ A", payload["spec"])
        self.assertEqual(payload["drift"], [])

        graph_call = next(c for c in mocked.call_args_list
                         if c.kwargs["name"] == "swarm_rescope_graph")
        self.assertEqual(graph_call[0][1], swarm_llm.PLAN_SCHEMA)
        graph_payload = json.loads(graph_call[0][0][1]["content"])
        task_a_view = next(t for t in graph_payload["tasks"] if t["id"] == a)
        self.assertIn("summary", task_a_view)
        self.assertEqual(task_a_view["write_scope"], ["listik/store.py", "tests"])

        # без apply — ничего не записано
        self.assertEqual(_snapshot(self.conn, "tasks"), before_tasks)
        self.assertEqual(_snapshot(self.conn, "deps"), before_deps)


class ApplyTests(RescopeTestCase):
    def test_apply_writes_scopes_and_edges(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        reply_a = _extract_reply(["docs/API.md"], ["listik/store.py"])
        reply_b = _extract_reply([], ["listik/other.py"])
        router = _reply_router({a: reply_a, b: reply_b}, [_graph_reply({b: [a]})])
        before_events = _snapshot(self.conn, "events")

        with mock.patch.object(swarm_llm, "complete_json", side_effect=router):
            out = swarm_llm.rescope(self.conn, project="demo", apply=True,
                                    cfg={"swarm": {"api_key": "k"}})

        task_a = store.get_task(self.conn, a)
        self.assertEqual(task_a["write_scope"], ["listik/store.py"])
        self.assertEqual(task_a["read_scope"], ["docs/API.md"])
        rows = {(r["issue_id"], r["depends_on"], r["dep_type"], r["created_by"])
               for r in self.conn.execute("SELECT * FROM deps").fetchall()}
        self.assertIn((b, a, "blocks", "agent:listik-swarm"), rows)
        self.assertEqual(sorted(out["applied"]["scopes"]), sorted([a, b]))
        self.assertEqual(out["applied"]["edges"]["added"], [[a, b]])
        self.assertEqual(_snapshot(self.conn, "events"), before_events)

        # повтор — идемпотентность
        router2 = _reply_router({a: reply_a, b: reply_b}, [_graph_reply({b: [a]})])
        with mock.patch.object(swarm_llm, "complete_json", side_effect=router2):
            out2 = swarm_llm.rescope(self.conn, project="demo", apply=True,
                                     cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out2["tasks"][a]["changed"], False)
        self.assertEqual(out2["tasks"][b]["changed"], False)
        self.assertEqual(out2["applied"]["scopes"], [])
        self.assertEqual(out2["applied"]["edges"]["added"], [])
        self.assertEqual(out2["applied"]["edges"]["removed"], [])


class UnspeccedTests(RescopeTestCase):
    def test_missing_and_broken_spec(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        c = self._task("C")
        d = self._task("D")
        store.update_task(self.conn, d, spec_path=str(self.tmp_path / "no-such-file.md"))
        self.conn.commit()

        replies = [_extract_reply([], ["x"]), _extract_reply([], ["y"]), _graph_reply({})]
        with mock.patch.object(swarm_llm, "complete_json", side_effect=replies):
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})

        self.assertEqual(out["unspecced"][c], "нет spec_path")
        self.assertTrue(out["unspecced"][d])
        self.assertEqual(out["extracted"], 2)
        self.assertEqual(out["tasks"][c]["source"], "card")
        self.assertEqual(out["tasks"][d]["source"], "card")


class InvalidTests(RescopeTestCase):
    def test_invalid_write_scope(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        router = _reply_router({a: _extract_reply([], ["/abs/x.py"]),
                                b: _extract_reply([], ["listik/store.py"])}, [_graph_reply({})])
        with mock.patch.object(swarm_llm, "complete_json", side_effect=router):
            out = swarm_llm.rescope(self.conn, project="demo", apply=True,
                                    cfg={"swarm": {"api_key": "k"}})
        self.assertTrue(out["invalid"][a].startswith("write_scope"))
        self.assertNotIn(a, out["applied"]["scopes"])
        self.assertEqual(store.get_task(self.conn, a)["write_scope"], [])
        self.assertIn(b, out["applied"]["scopes"])


class UnscopedTests(RescopeTestCase):
    def test_empty_write_scope(self) -> None:
        a = self._task("A", spec="ТЗ A")
        with mock.patch.object(swarm_llm, "complete_json", return_value=_extract_reply([], [])):
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertIn(a, out["unscoped"])
        self.assertEqual(store.get_task(self.conn, a)["write_scope"], [])
        self.assertEqual(store.get_task(self.conn, a)["read_scope"], [])

    def test_empty_write_scope_with_drift_outside(self) -> None:
        a = self._task("A", spec="ТЗ A")
        self._watch_drift(a, files=["x.py"], declared=[])
        with mock.patch.object(swarm_llm, "complete_json", return_value=_extract_reply([], [])):
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertNotIn(a, out["unscoped"])
        self.assertEqual(out["tasks"][a]["write_scope"], ["x.py"])


class DriftWatchTests(RescopeTestCase):
    def test_watch_drift_extends_write_scope(self) -> None:
        a = self._task("A", spec="ТЗ A")
        self._watch_drift(a, files=["listik/server.py"], declared=["listik/store.py"])
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_extract_reply([], ["listik/store.py"])) as mocked:
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out["tasks"][a]["write_scope"],
                         ["listik/store.py", "listik/server.py"])
        first_payload = json.loads(mocked.call_args_list[0][0][0][1]["content"])
        self.assertEqual(first_payload["drift"][0]["outside"], ["listik/server.py"])
        self.assertEqual(first_payload["drift"][0]["source"], "watch")
        self.assertEqual(out["drift"]["tasks_with_drift"], 1)
        self.assertEqual(out["drift"]["outside_files"], 1)
        self.assertEqual(out["drift"]["records"], 1)


class DriftMergedTests(RescopeTestCase):
    def test_merged_drift_counts_closed_task(self) -> None:
        a = self._task("A", spec="ТЗ A")
        z = self._task("Z", status="done")
        self._merged_drift(z, files=["a.py", "b.py"], declared=["a.py"], outside=["b.py"])
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_extract_reply([], ["listik/store.py"])):
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out["drift"]["tasks_with_drift"], 1)
        self.assertEqual(out["drift"]["tasks_total"], 2)
        self.assertNotIn(z, out["tasks"])


class DriftGarbageTests(RescopeTestCase):
    def test_other_author_and_garbage_ignored(self) -> None:
        a = self._task("A", spec="ТЗ A")
        store.add_comment(self.conn, a, f"{SCOPE_MARK} {json.dumps({'files': ['x']})}",
                          author="ann", kind="journal")
        store.add_comment(self.conn, a, f"{SCOPE_MARK} не json",
                          author="agent:listik-swarm", kind="journal")
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_extract_reply([], ["listik/store.py"])):
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out["drift"]["records"], 0)
        self.assertEqual(out["drift"]["ignored"], 1)


class DriftFileTests(RescopeTestCase):
    def test_drift_file_argument(self) -> None:
        a = self._task("A", spec="ТЗ A")
        drift = [
            {"task": a, "declared": ["listik/store.py"],
             "touched": ["listik/store.py", "listik/deps.py"]},
            {"task": "nope", "files": ["x"]},
            "мусор",
        ]
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_extract_reply([], ["listik/store.py"])):
            out = swarm_llm.rescope(self.conn, project="demo", drift=drift,
                                    cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out["drift"]["ignored"], 2)
        self.assertEqual(out["tasks"][a]["write_scope"], ["listik/store.py", "listik/deps.py"])

        with self.assertRaises(errors.BadArgument):
            swarm_llm.rescope(self.conn, project="demo", drift="не список",
                              cfg={"swarm": {"api_key": "k"}})


class TaskFilterTests(RescopeTestCase):
    def test_task_filter(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        with mock.patch.object(swarm_llm, "complete_json",
                               side_effect=[_extract_reply([], ["listik/store.py"]),
                                          _graph_reply({})]) as mocked:
            out = swarm_llm.rescope(self.conn, project="demo", tasks=[a], apply=True,
                                    cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out["extracted"], 1)
        self.assertEqual(out["tasks"][b]["source"], "card")
        self.assertNotIn(b, out["applied"]["scopes"])
        self.assertEqual(mocked.call_count, 2)

        with mock.patch.object(swarm_llm, "complete_json") as mocked2:
            with self.assertRaises(errors.BadArgument):
                swarm_llm.rescope(self.conn, project="demo", tasks=["nope"],
                                  cfg={"swarm": {"api_key": "k"}})
        mocked2.assert_not_called()


class CycleRemainsTests(RescopeTestCase):
    def test_model_cycle_still_writes_scopes(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        replies = [_extract_reply([], ["listik/store.py"]),
                  _extract_reply([], ["listik/other.py"]),
                  _graph_reply({a: [b], b: [a]}), _graph_reply({a: [b], b: [a]})]
        with mock.patch.object(swarm_llm, "complete_json", side_effect=replies):
            out = swarm_llm.rescope(self.conn, project="demo", apply=True,
                                    cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(len(out["cycles"]), 1)
        self.assertEqual(set(out["cycles"][0]), {a, b})
        self.assertEqual(out["cycles_from"], "model")
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(sorted(out["applied"]["scopes"]), sorted([a, b]))
        self.assertIsNone(out["applied"]["edges"])
        self.assertEqual(out["edges"], [])
        self.assertEqual(len(self.conn.execute(
            "SELECT * FROM deps WHERE dep_type='blocks' AND created_by=?",
            (swarm_llm.SWARM_AUTHOR,)).fetchall()), 0)


class CycleInDbTests(RescopeTestCase):
    def test_db_cycle_skips_graph_model(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        for issue_id, depends_on in ((a, b), (b, a)):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', 'ann')", (issue_id, depends_on))
        self.conn.commit()
        replies = [_extract_reply([], ["listik/store.py"]),
                  _extract_reply([], ["listik/other.py"])]
        with mock.patch.object(swarm_llm, "complete_json", side_effect=replies) as mocked:
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(out["extracted"], 2)
        self.assertEqual(out["attempts"], 0)
        self.assertEqual(out["cycles_from"], "db")
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(out["edges"], [])


class ModelErrorTests(RescopeTestCase):
    def test_error_in_phase1_writes_nothing(self) -> None:
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        before_deps = _snapshot(self.conn, "deps")

        def side_effect(*args, **kwargs):
            if kwargs.get("name") == "swarm_rescope_extract":
                if not side_effect.calls:
                    side_effect.calls.append(1)
                    return _extract_reply([], ["listik/store.py"])
                raise swarm_llm.SwarmLlmError("boom", status=504)
            raise AssertionError("не должен звать граф")

        side_effect.calls = []
        with mock.patch.object(swarm_llm, "complete_json", side_effect=side_effect):
            with self.assertRaises(swarm_llm.SwarmLlmError):
                swarm_llm.rescope(self.conn, project="demo", apply=True,
                                  cfg={"swarm": {"api_key": "k"}})
        self.assertEqual(store.get_task(self.conn, a)["write_scope"], [])
        self.assertEqual(_snapshot(self.conn, "deps"), before_deps)


class NotConfiguredTests(RescopeTestCase):
    def test_not_configured_no_network_calls(self) -> None:
        self._task("A", spec="ТЗ A")
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("LISTIK_SWARM")}
        opener = mock.Mock()
        with mock.patch.dict(os.environ, env_clean, clear=True):
            with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                swarm_llm.rescope(self.conn, project="demo", cfg={}, opener=opener)
        self.assertEqual(ctx.exception.status, 503)
        opener.assert_not_called()


# --------------------------------------------------------------------------- CLI


def _config_with_fake_model(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[swarm]\ncommand = ["%s", "%s"]\n' % (sys.executable, FAKE_MODEL),
        encoding="utf-8",
    )
    return cfg_path


class CliTests(RescopeTestCase):
    def _run(self, *args, env_extra=None):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        env.pop("LISTIK_PROJECT", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )

    def _seed(self):
        a = self._task("A", spec="ТЗ A")
        b = self._task("B", spec="ТЗ B")
        return a, b

    def test_cli_json(self) -> None:
        a, b = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        calls_path = self.tmp_path / "calls.jsonl"
        replies_path.write_text(json.dumps({
            "swarm_rescope_extract": [
                _extract_reply(["docs/API.md"], ["listik/store.py"]),
                _extract_reply([], ["listik/other.py"]),
            ],
            "swarm_rescope_graph": [_graph_reply({b: [a]})],
        }), encoding="utf-8")
        env_extra = {"LISTIK_CONFIG": str(cfg), "FAKE_MODEL_REPLIES": str(replies_path),
                    "FAKE_MODEL_CALLS": str(calls_path)}
        p = self._run("rescope", "--project", "demo", "--json", env_extra=env_extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        self.assertIn("tasks", payload)
        self.assertEqual(payload["extracted"], 2)
        self.assertIn("edges", payload)
        self.assertIn("drift", payload)
        self.assertIsNone(payload["applied"])

    def test_cli_text(self) -> None:
        a, b = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        replies_path.write_text(json.dumps({
            "swarm_rescope_extract": [
                _extract_reply(["docs/API.md"], ["listik/store.py"]),
                _extract_reply([], ["listik/other.py"]),
            ],
            "swarm_rescope_graph": [_graph_reply({b: [a]})],
        }), encoding="utf-8")
        env_extra = {"LISTIK_CONFIG": str(cfg), "FAKE_MODEL_REPLIES": str(replies_path)}
        p = self._run("rescope", "--project", "demo", env_extra=env_extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("rescope demo:", p.stdout)
        self.assertIn(f"{a}: правит listik/store.py; читает docs/API.md", p.stdout)
        self.assertIn("качество ТЗ:", p.stdout)

    def test_cli_drift_file(self) -> None:
        a, b = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        replies_path.write_text(json.dumps({
            "swarm_rescope_extract": [
                _extract_reply([], ["listik/store.py"]),
                _extract_reply([], ["listik/other.py"]),
            ],
            "swarm_rescope_graph": [_graph_reply({})],
        }), encoding="utf-8")
        drift_path = self.tmp_path / "drift.json"
        drift_path.write_text(json.dumps([
            {"task": a, "declared": [], "touched": ["listik/store.py"]},
        ]), encoding="utf-8")
        env_extra = {"LISTIK_CONFIG": str(cfg), "FAKE_MODEL_REPLIES": str(replies_path)}
        p = self._run("rescope", "--project", "demo", "--drift", str(drift_path), "--json",
                      env_extra=env_extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        self.assertEqual(payload["drift"]["records"], 1)

        p_no_file = self._run("rescope", "--project", "demo", "--drift", "/no/such/file.json",
                              "--json", env_extra=env_extra)
        self.assertNotEqual(p_no_file.returncode, 0)
        self.assertIn("bad_argument", (p_no_file.stdout + p_no_file.stderr))

        bad_drift = self.tmp_path / "bad-drift.json"
        bad_drift.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
        p_bad = self._run("rescope", "--project", "demo", "--drift", str(bad_drift), "--json",
                          env_extra=env_extra)
        self.assertNotEqual(p_bad.returncode, 0)
        self.assertIn("bad_argument", (p_bad.stdout + p_bad.stderr))

    def test_cli_apply_and_task_filter(self) -> None:
        a, b = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        replies_path.write_text(json.dumps({
            "swarm_rescope_extract": [_extract_reply([], ["listik/store.py"])],
            "swarm_rescope_graph": [_graph_reply({})],
        }), encoding="utf-8")
        env_extra = {"LISTIK_CONFIG": str(cfg), "FAKE_MODEL_REPLIES": str(replies_path)}
        p = self._run("rescope", "--project", "demo", "--task", a, "--apply", "--json",
                      env_extra=env_extra, )
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        self.assertEqual(payload["extracted"], 1)
        self.assertEqual(payload["applied"]["scopes"], [a])
        task_a = store.get_task(self.conn, a)
        self.assertEqual(task_a["write_scope"], ["listik/store.py"])

    def test_cli_cycle_returns_1(self) -> None:
        a, b = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        replies_path.write_text(json.dumps({
            "swarm_rescope_extract": [_extract_reply([], ["listik/store.py"]),
                                      _extract_reply([], ["listik/other.py"])],
            "swarm_rescope_graph": [_graph_reply({a: [b], b: [a]}),
                                    _graph_reply({a: [b], b: [a]})],
        }), encoding="utf-8")
        env_extra = {"LISTIK_CONFIG": str(cfg), "FAKE_MODEL_REPLIES": str(replies_path)}
        p = self._run("rescope", "--project", "demo", env_extra=env_extra)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("цикл:", p.stdout)

    def test_swarm_rescope_is_write_op(self) -> None:
        source = LISTIK_BIN.read_text(encoding="utf-8")
        self.assertIn('"swarm_rescope"', source)


# --------------------------------------------------------------------------- HTTP


class HttpTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def _seed(self, tmp_path: Path):
        conn = db_mod.init(self.db_path)
        try:
            store.upsert_project(conn, "demo", path=str(tmp_path))
            spec_dir = tmp_path / "specs"
            spec_dir.mkdir(exist_ok=True)
            a = store.create_task(conn, title="A", project="demo")["id"]
            b = store.create_task(conn, title="B", project="demo")["id"]
            for tid in (a, b):
                spec_path = spec_dir / f"{tid}.md"
                spec_path.write_text("ТЗ", encoding="utf-8")
                store.update_task(conn, tid, spec_path=str(spec_path))
            conn.commit()
            return a, b
        finally:
            conn.close()

    def test_http_200(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._seed(Path(tmp))
            with mock.patch("listik.server.swarm_llm.complete_json",
                            side_effect=[_extract_reply([], ["listik/store.py"]),
                                        _extract_reply([], ["listik/other.py"]),
                                        _graph_reply({b: [a]})]):
                status, _, payload = self.call("POST", "/api/swarm/rescope", AUTH,
                                               {"project": "demo"})
        self.assertEqual(status, 200, payload)
        self.assertIn("tasks", payload["data"])
        self.assertIn("drift", payload["data"])
        self.assertIn("generated_at", payload["data"])

    def test_http_cycle_apply_scopes_but_no_edges(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._seed(Path(tmp))
            replies = [_extract_reply([], ["listik/store.py"]),
                      _extract_reply([], ["listik/other.py"]),
                      _graph_reply({a: [b], b: [a]}), _graph_reply({a: [b], b: [a]})]
            with mock.patch("listik.server.swarm_llm.complete_json", side_effect=replies):
                status, _, payload = self.call(
                    "POST", "/api/swarm/rescope", AUTH, {"project": "demo", "apply": True})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["data"]["cycles"])
        self.assertIsNone(payload["data"]["applied"]["edges"])
        self.assertTrue(payload["data"]["applied"]["scopes"])

    def test_http_owner_and_actor_ignored_but_publishes(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            a, b = self._seed(Path(tmp))
            replies = [_extract_reply([], ["listik/store.py"]),
                      _extract_reply([], ["listik/other.py"]),
                      _graph_reply({b: [a]})]
            with mock.patch("listik.server.swarm_llm.complete_json", side_effect=replies):
                with mock.patch("listik.server.publish") as published:
                    headers = dict(AUTH)
                    headers["X-Listik-Owner"] = "ann"
                    status, _, payload = self.call(
                        "POST", "/api/swarm/rescope", headers,
                        {"project": "demo", "apply": True, "actor": "ann",
                         "drift": [{"task": a, "declared": [], "touched": ["x.py"]}]})
        self.assertEqual(status, 200, payload)
        conn = db_mod.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=?",
                (b, a)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["created_by"], swarm_llm.SWARM_AUTHOR)
        publish_ids = {(c.args[1]["id"], c.args[1]["action"]) for c in published.call_args_list}
        self.assertIn((a, "updated"), publish_ids)
        self.assertIn((b, "deps"), publish_ids)

    def test_http_tasks_not_list(self) -> None:
        status, _, payload = self.call("POST", "/api/swarm/rescope", AUTH,
                                       {"project": "demo", "tasks": "a"})
        self.assertEqual(status, 400)

    def test_http_drift_not_list(self) -> None:
        status, _, payload = self.call("POST", "/api/swarm/rescope", AUTH,
                                       {"project": "demo", "drift": {}})
        self.assertEqual(status, 400)

    def test_http_method_not_allowed(self) -> None:
        status, _, payload = self.call("GET", "/api/swarm/rescope?project=demo", AUTH)
        self.assertEqual(status, 405)

    def test_http_no_token(self) -> None:
        status, _, payload = self.call("POST", "/api/swarm/rescope", {}, {"project": "demo"})
        self.assertEqual(status, 401)

    def test_http_swarm_error_status(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(Path(tmp))
            with mock.patch("listik.server.swarm_llm.complete_json",
                            side_effect=swarm_llm.SwarmLlmError("timeout", status=502)):
                status, _, payload = self.call("POST", "/api/swarm/rescope", AUTH,
                                               {"project": "demo"})
        self.assertEqual(status, 502)
        self.assertEqual(payload["code"], "server_error")

    def test_http_not_found(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            self._seed(Path(tmp))
            with mock.patch("listik.server.swarm_llm.working_set",
                            side_effect=errors.NotFound("задача не найдена: x")):
                status, _, payload = self.call("POST", "/api/swarm/rescope", AUTH,
                                               {"project": "demo"})
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")


class DriftBadPathTests(RescopeTestCase):
    def test_bad_fact_path_goes_to_dropped_not_invalid(self) -> None:
        a = self._task("A", spec="ТЗ A")
        self._watch_drift(a, files=["/abs/x.py", "./listik/deps.py", "a*b.py", "c\\d.py",
                                    "listik/store.py"],
                          declared=[])
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_extract_reply([], ["listik/store.py"])):
            out = swarm_llm.rescope(self.conn, project="demo", apply=True,
                                    cfg={"swarm": {"api_key": "k"}})
        self.assertNotIn(a, out["invalid"])
        self.assertEqual(out["tasks"][a]["write_scope"], ["listik/store.py", "listik/deps.py"])
        self.assertIn(a, out["applied"]["scopes"])
        bad = [d for d in out["dropped"] if d.get("why") == "bad_path"]
        self.assertEqual(bad, [{"id": a, "file": "/abs/x.py", "why": "bad_path"},
                               {"id": a, "file": "a*b.py", "why": "bad_path"},
                               {"id": a, "file": "c\\d.py", "why": "bad_path"}])

    def test_bad_model_path_still_invalid(self) -> None:
        a = self._task("A", spec="ТЗ A")
        self._watch_drift(a, files=["ok.py"], declared=[])
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_extract_reply([], ["/abs.py"])):
            out = swarm_llm.rescope(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}})
        self.assertIn(a, out["invalid"])


class DriftOutsideNotListTests(RescopeTestCase):
    def test_merged_outside_not_list_falls_back(self) -> None:
        a = self._task("A", spec="ТЗ A")
        payload = json.dumps({"files": ["a.py", "b.py"], "declared": ["a.py"],
                              "outside": "b.py"})
        store.add_comment(self.conn, a, f"{MERGED_MARK} {payload}",
                          author="agent:listik-swarm", kind="journal")
        records, _ = swarm_llm.drift_records(self.conn, project="demo")
        self.assertEqual(records[0]["outside"], ["b.py"])

    def test_merged_outside_list_with_garbage_kept(self) -> None:
        a = self._task("A", spec="ТЗ A")
        payload = json.dumps({"files": ["a.py", "b.py"], "declared": ["a.py"],
                              "outside": ["c.py", 5]})
        store.add_comment(self.conn, a, f"{MERGED_MARK} {payload}",
                          author="agent:listik-swarm", kind="journal")
        records, _ = swarm_llm.drift_records(self.conn, project="demo")
        self.assertEqual(records[0]["outside"], ["c.py"])

    def test_extra_outside_not_list_falls_back(self) -> None:
        a = self._task("A", spec="ТЗ A")
        records, _ = swarm_llm.drift_records(
            self.conn, project="demo",
            extra=[{"task": a, "declared": ["a.py"], "touched": ["a.py", "b.py"],
                    "outside": None}])
        self.assertEqual(records[0]["outside"], ["b.py"])
