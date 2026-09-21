"""`listik plan` / `POST /api/swarm/plan` / `swarm_llm.plan` (listik-kbh5, шаг swarm-6,
порция b): грубый граф зависимостей `blocks` между открытыми задачами проекта от модели.

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
from tests.helpers import TempDbTestCase
from tests.test_owner_http import AUTH, LOCAL_CONFIG, SERVER_CONFIG, OwnerHttpCase

LISTIK_BIN = Path(__file__).resolve().parent.parent / "bin" / "listik"
REPO_ROOT = LISTIK_BIN.parent.parent
FAKE_MODEL = Path(__file__).resolve().parent / "fixtures" / "fake_model.py"


def _task(conn, title: str, *, priority: int = 2, description: str = "", acceptance: str = "",
         stage: str | None = None) -> str:
    return store.create_task(conn, title=title, project="demo", priority=priority,
                             description=description, acceptance=acceptance,
                             stage=stage)["id"]


def _all_deps(conn):
    return {(r["issue_id"], r["depends_on"], r["dep_type"], r["created_by"])
            for r in conn.execute(
                "SELECT issue_id, depends_on, dep_type, created_by FROM deps").fetchall()}


def _reply(edges: dict[str, list[str]]) -> dict:
    """`{id: [depends_on, ...]}` → форма ответа модели (`reason` — заглушка)."""
    return {"tasks": [{"id": tid, "depends_on": list(deps_), "reason": "потому что"}
                      for tid, deps_ in edges.items()]}


# --------------------------------------------------------------------------- функция plan


class DryRunTests(TempDbTestCase):
    def test_dry_run_builds_edges(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        c = _task(self.conn, "C", priority=2)
        before = _all_deps(self.conn)
        with mock.patch.object(swarm_llm, "complete_json",
                               return_value=_reply({c: [b], b: [a], a: []})):
            out = swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {}})
        self.assertEqual(out["edges"], [[a, b], [b, c]])
        self.assertEqual(out["tasks"][c]["depends_on"], [b])
        self.assertEqual(out["attempts"], 1)
        self.assertIsNone(out["applied"])
        self.assertEqual(out["cycles"], [])
        self.assertIsNone(out["cycles_from"])
        self.assertEqual(_all_deps(self.conn), before)


class PromptTests(TempDbTestCase):
    def test_prompt_and_payload(self) -> None:
        a = _task(self.conn, "A", priority=0, description="d", acceptance="acc")
        b = _task(self.conn, "B", priority=1)
        c = _task(self.conn, "C", priority=2)
        store.add_dep(self.conn, b, a, "blocks", created_by="ann")

        calls: list = []

        def fake_complete_json(messages, schema, *, name, cfg_settings, opener=None,
                               runner=None, timeout=None):
            calls.append((messages, schema, name, cfg_settings))
            return _reply({})

        with mock.patch.object(swarm_llm, "complete_json", side_effect=fake_complete_json):
            swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {"api_key": "k"}},
                          opener=mock.Mock())

        messages, schema, name, cfg_settings = calls[0]
        self.assertEqual(messages[0], {"role": "system", "content": swarm_llm.PLAN_PROMPT})
        payload = json.loads(messages[1]["content"])
        self.assertEqual(payload["project"], "demo")
        self.assertEqual(len(payload["tasks"]), 3)
        ids_in_payload = {t["id"] for t in payload["tasks"]}
        self.assertEqual(ids_in_payload, {a, b, c})
        for t in payload["tasks"]:
            self.assertIn("id", t)
            self.assertIn("title", t)
            self.assertIn("description", t)
            self.assertIn("acceptance", t)
        self.assertEqual(payload["fixed"], [{"id": b, "depends_on": [a]}])
        self.assertEqual(payload["previous"], [])
        self.assertEqual(schema, swarm_llm.PLAN_SCHEMA)
        self.assertEqual(name, "swarm_plan")


class ApplyTests(TempDbTestCase):
    def test_apply_writes_and_updates(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        c = _task(self.conn, "C", priority=2)

        reply = _reply({c: [b], b: [a], a: []})
        with mock.patch.object(swarm_llm, "complete_json", return_value=reply):
            out = swarm_llm.plan(self.conn, project="demo", apply=True, cfg={"swarm": {}})

        rows = {(r["issue_id"], r["depends_on"]) for r in self.conn.execute(
            "SELECT issue_id, depends_on FROM deps WHERE dep_type='blocks' AND created_by=?",
            (swarm_llm.SWARM_AUTHOR,)).fetchall()}
        self.assertEqual(rows, {(b, a), (c, b)})
        self.assertEqual(sorted(out["applied"]["added"]), sorted([[a, b], [b, c]]))

        with mock.patch.object(swarm_llm, "complete_json", return_value=reply):
            out2 = swarm_llm.plan(self.conn, project="demo", apply=True, cfg={"swarm": {}})
        self.assertEqual(out2["applied"]["added"], [])
        self.assertEqual(out2["applied"]["removed"], [])
        self.assertEqual(out2["applied"]["kept"], 2)

        reply_drop_one = _reply({c: [], b: [a], a: []})
        with mock.patch.object(swarm_llm, "complete_json", return_value=reply_drop_one):
            out3 = swarm_llm.plan(self.conn, project="demo", apply=True, cfg={"swarm": {}})
        self.assertEqual(out3["applied"]["removed"], [[b, c]])


class HumanEdgeTests(TempDbTestCase):
    def test_covered_and_untouched(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        store.add_dep(self.conn, b, a, "blocks", created_by="ann")

        with mock.patch.object(swarm_llm, "complete_json", return_value=_reply({b: [a], a: []})):
            out = swarm_llm.plan(self.conn, project="demo", apply=True, cfg={"swarm": {}})
        rows = self.conn.execute(
            "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=?", (b, a)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["created_by"], "ann")
        self.assertEqual(out["applied"]["covered"], [[a, b]])

        with mock.patch.object(swarm_llm, "complete_json", return_value=_reply({b: [], a: []})):
            out2 = swarm_llm.plan(self.conn, project="demo", apply=True, cfg={"swarm": {}})
        rows2 = self.conn.execute(
            "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=?", (b, a)).fetchall()
        self.assertEqual(len(rows2), 1)
        self.assertEqual(out2["applied"]["removed"], [])


class DroppedTests(TempDbTestCase):
    def test_dropped_records(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        reply = {"tasks": [
            {"id": "nope", "depends_on": [], "reason": ""},
            {"id": a, "depends_on": ["zzz", a], "reason": ""},
        ]}
        with mock.patch.object(swarm_llm, "complete_json", return_value=reply):
            out = swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {}})
        whys = {(d.get("id"), d.get("why")) for d in out["dropped"]}
        self.assertIn(("nope", "unknown_task"), whys)
        self.assertIn((a, "unknown_id"), whys)
        self.assertIn((a, "self"), whys)
        self.assertEqual(out["edges"], [])
        self.assertEqual(out["tasks"][b]["depends_on"], [])


class CycleRetryTests(TempDbTestCase):
    def test_retry_resolves_cycle(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        replies = [_reply({a: [b], b: [a]}), _reply({b: [a], a: []})]
        with mock.patch.object(swarm_llm, "complete_json", side_effect=replies) as mocked:
            out = swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {}})
        self.assertEqual(out["attempts"], 2)
        self.assertEqual(out["cycles"], [])
        self.assertEqual(out["edges"], [[a, b]])
        second_call_messages = mocked.call_args_list[1][0][0]
        self.assertEqual(len(second_call_messages), 4)
        self.assertEqual(second_call_messages[2]["role"], "assistant")
        self.assertIn(f"{a} → {b} → {a}",
                      second_call_messages[3]["content"])


class CycleRemainsTests(TempDbTestCase):
    def test_cycle_from_model_blocks_apply(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        reply = _reply({a: [b], b: [a]})
        before = _all_deps(self.conn)
        with mock.patch.object(swarm_llm, "complete_json", return_value=reply):
            out = swarm_llm.plan(self.conn, project="demo", apply=True, cfg={"swarm": {}})
        self.assertEqual(out["cycles"], [[a, b]])
        self.assertEqual(out["cycles_from"], "model")
        self.assertEqual(out["attempts"], 2)
        self.assertIsNone(out["applied"])
        self.assertEqual(_all_deps(self.conn), before)


class CycleInDbTests(TempDbTestCase):
    def test_cycle_in_db_skips_model(self) -> None:
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        # Раздельный `INSERT`, не `add_dep`: сама `add_dep` отказывает на цикле, а тут
        # нужен цикл, уже лежащий в базе (человеческие рёбра, заведённые порознь).
        for issue_id, depends_on in ((a, b), (b, a)):
            self.conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?, ?, 'blocks', 'ann')", (issue_id, depends_on))
        self.conn.commit()
        with mock.patch.object(swarm_llm, "complete_json") as mocked:
            out = swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {}})
        mocked.assert_not_called()
        self.assertEqual(out["cycles"], [[a, b]])
        self.assertEqual(out["cycles_from"], "db")
        self.assertEqual(out["attempts"], 0)


class EmptyTests(TempDbTestCase):
    def test_empty_project(self) -> None:
        with mock.patch.object(swarm_llm, "complete_json") as mocked:
            out = swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {}})
        mocked.assert_not_called()
        self.assertEqual(out["attempts"], 0)
        self.assertEqual(out["tasks"], {})


class StageTests(TempDbTestCase):
    def test_stage_filters_working_set(self) -> None:
        a = _task(self.conn, "A", stage="s1-spec")
        b = _task(self.conn, "B", stage="s3-impl")
        reply = _reply({b: [a]})
        with mock.patch.object(swarm_llm, "complete_json", return_value=reply):
            out = swarm_llm.plan(self.conn, project="demo", stage="s3-impl", cfg={"swarm": {}})
        self.assertNotIn(a, out["tasks"])
        self.assertIn(b, out["tasks"])
        drop_whys = {d["why"] for d in out["dropped"]}
        self.assertIn("unknown_id", drop_whys)


class NotConfiguredTests(TempDbTestCase):
    def test_not_configured_is_swarm_error(self) -> None:
        _task(self.conn, "A")
        before = _all_deps(self.conn)
        env_clean = {k: v for k, v in os.environ.items() if not k.startswith("LISTIK_SWARM")}
        with mock.patch.dict(os.environ, env_clean, clear=True):
            with self.assertRaises(swarm_llm.SwarmLlmError) as ctx:
                swarm_llm.plan(self.conn, project="demo", cfg={})
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(_all_deps(self.conn), before)


class LimitTests(TempDbTestCase):
    def test_total_chars_limit(self) -> None:
        _task(self.conn, "A")
        with mock.patch.object(swarm_llm, "MAX_TOTAL_CHARS", 10):
            with mock.patch.object(swarm_llm, "complete_json") as mocked:
                with self.assertRaises(errors.BadArgument):
                    swarm_llm.plan(self.conn, project="demo", cfg={"swarm": {}})
        mocked.assert_not_called()


# --------------------------------------------------------------------------- CLI


def _config_with_fake_model(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        '[swarm]\ncommand = ["%s", "%s"]\n' % (sys.executable, FAKE_MODEL),
        encoding="utf-8",
    )
    return cfg_path


class CliTests(TempDbTestCase):
    def _run(self, *args, env_extra=None):
        env = {**os.environ, "LISTIK_DB": str(self.db_path)}
        env.pop("LISTIK_PROJECT", None)
        env.update(env_extra or {})
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
        )

    def _seed(self):
        a = _task(self.conn, "A", priority=0)
        b = _task(self.conn, "B", priority=1)
        c = _task(self.conn, "C", priority=2)
        return a, b, c

    def test_cli_json(self) -> None:
        a, b, c = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        calls_path = self.tmp_path / "calls.jsonl"
        replies_path.write_text(json.dumps({"swarm_plan": [_reply({c: [b], b: [a]})]}),
                                encoding="utf-8")
        p = self._run("plan", "--project", "demo", "--json", env_extra={
            "LISTIK_CONFIG": str(cfg),
            "FAKE_MODEL_REPLIES": str(replies_path),
            "FAKE_MODEL_CALLS": str(calls_path),
        })
        self.assertEqual(p.returncode, 0, p.stderr)
        payload = json.loads(p.stdout)
        self.assertIn("edges", payload)
        self.assertIn("tasks", payload)
        self.assertIn("attempts", payload)
        self.assertIsNone(payload["applied"])
        calls = [json.loads(line) for line in calls_path.read_text().splitlines() if line]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["name"], "swarm_plan")
        self.assertEqual(calls[0]["schema"], swarm_llm.PLAN_SCHEMA)

    def test_cli_text(self) -> None:
        a, b, c = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        calls_path = self.tmp_path / "calls.jsonl"
        replies_path.write_text(json.dumps({"swarm_plan": [_reply({c: [b], b: [a]})]}),
                                encoding="utf-8")
        env_extra = {
            "LISTIK_CONFIG": str(cfg),
            "FAKE_MODEL_REPLIES": str(replies_path),
            "FAKE_MODEL_CALLS": str(calls_path),
        }
        p = self._run("plan", "--project", "demo", env_extra=env_extra)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("план demo:", p.stdout)
        self.assertIn(f"{c} ждёт {b}", p.stdout)
        self.assertIn(f"без зависимостей: {a}", p.stdout)

        p_apply = self._run("plan", "--project", "demo", "--apply", env_extra=env_extra)
        self.assertEqual(p_apply.returncode, 0, p_apply.stderr)
        self.assertIn("записано рёбер: 2", p_apply.stdout)

        p_apply2 = self._run("plan", "--project", "demo", "--apply", env_extra=env_extra)
        self.assertEqual(p_apply2.returncode, 0, p_apply2.stderr)
        self.assertIn("без изменений: 2", p_apply2.stdout)

    def test_cli_cycle_and_errors(self) -> None:
        a, b, c = self._seed()
        cfg = _config_with_fake_model(self.tmp_path)
        replies_path = self.tmp_path / "replies.json"
        calls_path = self.tmp_path / "calls.jsonl"
        replies_path.write_text(json.dumps({"swarm_plan": [
            _reply({a: [b], b: [a]}), _reply({a: [b], b: [a]}),
        ]}), encoding="utf-8")
        before = _all_deps(self.conn)
        p = self._run("plan", "--project", "demo", env_extra={
            "LISTIK_CONFIG": str(cfg),
            "FAKE_MODEL_REPLIES": str(replies_path),
            "FAKE_MODEL_CALLS": str(calls_path),
        })
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertIn("цикл:", p.stdout)
        self.assertEqual(_all_deps(self.conn), before)

        p_no_project = self._run("plan")
        self.assertNotEqual(p_no_project.returncode, 0)
        p_no_project_json = self._run("plan", "--json")
        self.assertIn("bad_argument", p_no_project_json.stdout)

        empty_cfg = self.tmp_path / "empty-config.toml"
        empty_cfg.write_text("", encoding="utf-8")
        p_bad_cfg = self._run("plan", "--project", "demo", "--json",
                              env_extra={"LISTIK_CONFIG": str(empty_cfg)})
        self.assertNotEqual(p_bad_cfg.returncode, 0)
        self.assertIn("server_error", p_bad_cfg.stdout + p_bad_cfg.stderr)

    def test_swarm_plan_is_write_op(self) -> None:
        # `WRITE_OPS` живёт в `bin/listik` (не пакет) — проверяем текстом файла.
        source = LISTIK_BIN.read_text(encoding="utf-8")
        self.assertIn('"swarm_plan"', source)


# --------------------------------------------------------------------------- HTTP


class HttpTests(OwnerHttpCase):
    config_text = LOCAL_CONFIG

    def _seed(self):
        conn = db_mod.init(self.db_path)
        try:
            a = store.create_task(conn, title="A", project="demo", priority=0)["id"]
            b = store.create_task(conn, title="B", project="demo", priority=1)["id"]
            conn.commit()
            return a, b
        finally:
            conn.close()

    def test_http_200(self) -> None:
        a, b = self._seed()
        with mock.patch("listik.server.swarm_llm.complete_json",
                        return_value=_reply({b: [a]})):
            status, _, payload = self.call("POST", "/api/swarm/plan", AUTH,
                                           {"project": "demo"})
        self.assertEqual(status, 200, payload)
        self.assertIn("edges", payload["data"])
        self.assertIn("generated_at", payload["data"])

    def test_http_cycle_apply_no_publish(self) -> None:
        a, b = self._seed()
        with mock.patch("listik.server.swarm_llm.complete_json",
                        return_value=_reply({a: [b], b: [a]})):
            with mock.patch("listik.server.publish") as published:
                status, _, payload = self.call(
                    "POST", "/api/swarm/plan", AUTH, {"project": "demo", "apply": True})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["data"]["cycles"])
        self.assertIsNone(payload["data"]["applied"])
        published.assert_not_called()

    def test_http_owner_and_actor_ignored_but_publishes(self) -> None:
        a, b = self._seed()
        with mock.patch("listik.server.swarm_llm.complete_json",
                        return_value=_reply({b: [a]})):
            with mock.patch("listik.server.publish") as published:
                headers = dict(AUTH)
                headers["X-Listik-Owner"] = "ann"
                status, _, payload = self.call(
                    "POST", "/api/swarm/plan", headers,
                    {"project": "demo", "apply": True, "actor": "ann"})
        self.assertEqual(status, 200, payload)
        conn = db_mod.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT created_by FROM deps WHERE issue_id=? AND depends_on=?",
                (b, a)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["created_by"], swarm_llm.SWARM_AUTHOR)
        published_ids = {c.args[1]["id"] for c in published.call_args_list}
        self.assertEqual(published_ids, {a, b})

    def test_http_missing_project(self) -> None:
        status, _, payload = self.call("POST", "/api/swarm/plan", AUTH, {})
        self.assertEqual(status, 400)

    def test_http_method_not_allowed(self) -> None:
        status, _, payload = self.call("GET", "/api/swarm/plan?project=demo", AUTH)
        self.assertEqual(status, 405)

    def test_http_no_token(self) -> None:
        status, _, payload = self.call("POST", "/api/swarm/plan", {}, {"project": "demo"})
        self.assertEqual(status, 401)

    def test_http_swarm_error_status(self) -> None:
        self._seed()
        with mock.patch("listik.server.swarm_llm.complete_json",
                        side_effect=swarm_llm.SwarmLlmError("timeout", status=504)):
            status, _, payload = self.call("POST", "/api/swarm/plan", AUTH,
                                           {"project": "demo"})
        self.assertEqual(status, 504)
        self.assertEqual(payload["code"], "server_error")

    def test_http_conflict_from_apply(self) -> None:
        a, b = self._seed()
        with mock.patch("listik.server.swarm_llm.complete_json", return_value=_reply({b: [a]})):
            with mock.patch("listik.deps.apply_planned_blocks",
                            side_effect=errors.ListikError("boom", code=errors.CONFLICT)):
                status, _, payload = self.call(
                    "POST", "/api/swarm/plan", AUTH, {"project": "demo", "apply": True})
        self.assertEqual(status, 409)
        self.assertEqual(payload["code"], "conflict")

    def test_http_not_found(self) -> None:
        self._seed()
        with mock.patch("listik.server.swarm_llm.working_set",
                        side_effect=errors.NotFound("задача не найдена: x")):
            status, _, payload = self.call("POST", "/api/swarm/plan", AUTH,
                                           {"project": "demo"})
        self.assertEqual(status, 404)
        self.assertEqual(payload["code"], "not_found")
