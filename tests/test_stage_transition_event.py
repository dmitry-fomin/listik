"""Тип перехода в событии `stage` и общая таблица маршрутизации в `/api/meta` (listik-cvm8, порция a).

`events.transition` пишет только `store.next_stage` — тип из таблицы маршрутизации, при
любой заметке; досыпка старой истории — по заметке по умолчанию `этап -> X (тип)`.
`/api/meta` и `local_call("meta")` отдают `routing` — общую таблицу config.toml без
переопределений проектов.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from listik import client
from listik import config as config_mod
from listik import db as db_mod
from listik import errors
from listik import paths
from listik import server
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
REVISION = REPO_DIR / "alembic" / "versions" / "0012_event_transition.py"

TOML_OVERRIDES = (
    '[routing.transitions]\n'
    '"s3-impl:s4-judge" = "handoff"\n'
    '\n'
    '[routing.projects.demo.transitions]\n'
    '"s1-spec:s2-review" = "sticky"\n'
)


def _old_schema() -> str:
    """`db.SCHEMA` схемы 13: таблица `events` без колонки `transition`."""
    lines = [line for line in db_mod.SCHEMA.splitlines()
             if not line.strip().startswith("transition ")]
    old = "\n".join(lines)
    assert old.count("duration_s INTEGER,") == 1
    return old.replace("duration_s INTEGER,", "duration_s INTEGER")


class SchemaTests(TempDbTestCase):
    """Пункт 1: колонка на свежей базе и номер схемы."""

    def test_fresh_database_has_transition_column(self) -> None:
        cols = {r["name"]: r for r in self.conn.execute("PRAGMA table_info(events)")}
        self.assertIn("transition", cols)
        self.assertEqual(cols["transition"]["type"], "TEXT")
        self.assertEqual(cols["transition"]["notnull"], 0)
        self.assertIsNone(cols["transition"]["dflt_value"])
        self.assertEqual(db_mod.SCHEMA_VERSION, 15)
        version = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        self.assertEqual(version, "15")


class BackfillTests(unittest.TestCase):
    """Пункты 2, 3: досыпка старой истории и её однократность."""

    # (метка, kind, from_value, to_value, note, ожидаемый transition)
    EVENTS = (
        ("а", "stage", "s1-spec", "s2-review", "этап -> s2-review (handoff)", "handoff"),
        ("б", "stage", "s3-impl", "s4-judge", "этап -> s4-judge (sticky)", "sticky"),
        ("в", "stage", "s4-judge", "s3-impl", "этап -> s3-impl (sticky-return)", "sticky-return"),
        ("г", "stage", "s2-review", "s3-impl", "перешёл (sticky) к делу", None),
        ("д", "note", None, None, "этап -> s2-review (sticky)", None),
        ("е", "stage", "s1-spec", "s2-review", "этап -> s3-impl (sticky)", None),
        ("ж", "stage", "s4-judge", "done", "этап -> done (handoff)", None),
        ("з", "stage", None, "s1-spec", "этап -> s1-spec (sticky)", "sticky"),
    )

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "old.db"
        old_schema = _old_schema()
        self.assertNotIn("transition", old_schema)
        raw = sqlite3.connect(self.path)
        raw.executescript(old_schema)
        raw.execute("INSERT INTO meta(key, value) VALUES('schema_version', '13')")
        self.ids = {}
        for label, kind, from_value, to_value, note, _ in self.EVENTS:
            cur = raw.execute(
                "INSERT INTO events(task_id, ts, kind, from_value, to_value, note) "
                "VALUES('t1', '2026-09-01T00:00:00Z', ?, ?, ?, ?)",
                (kind, from_value, to_value, note))
            self.ids[label] = cur.lastrowid
        raw.commit()
        self.assertNotIn("transition",
                         {r[1] for r in raw.execute("PRAGMA table_info(events)")})
        raw.close()

    def _transitions(self, conn) -> dict:
        return {r[0]: r[1] for r in conn.execute("SELECT id, transition FROM events")}

    def test_backfill_marks_only_default_notes(self) -> None:
        conn = db_mod.init(self.path)
        self.addCleanup(conn.close)
        self.assertIn("transition", {r["name"] for r in conn.execute("PRAGMA table_info(events)")})
        got = self._transitions(conn)
        for label, *_, expected in self.EVENTS:
            with self.subTest(label=label):
                self.assertEqual(got[self.ids[label]], expected)

    def test_reinit_does_not_backfill_again(self) -> None:
        conn = db_mod.init(self.path)
        self.addCleanup(conn.close)
        conn.execute("UPDATE events SET transition = NULL WHERE id = ?", (self.ids["а"],))
        conn.commit()
        before = self._transitions(conn)
        self.assertIsNone(before[self.ids["а"]])

        again = db_mod.init(self.path)
        self.addCleanup(again.close)
        self.assertEqual(db_mod.migrate(again), [])
        self.assertEqual(self._transitions(again), before)
        self.assertIsNone(self._transitions(again)[self.ids["а"]])


class AlembicTests(unittest.TestCase):
    """Пункт 4: ревизия 0012 и её offline-SQL."""

    def test_revision_links_to_orchestrator(self) -> None:
        text = REVISION.read_text(encoding="utf-8")
        self.assertIn('revision = "0012_event_transition"', text)
        self.assertIn('down_revision = "0011_task_orchestrator"', text)

    def test_offline_sql_adds_column_and_backfills(self) -> None:
        if shutil.which("alembic") is None:
            self.skipTest("alembic не установлен")
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "LISTIK_DB": str(pathlib.Path(tmp) / "sql.db")}
            done = subprocess.run(
                ["alembic", "upgrade", "0011_task_orchestrator:0012_event_transition", "--sql"],
                cwd=REPO_DIR, capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ALTER TABLE events ADD COLUMN transition TEXT", done.stdout)
        # Ревизия несёт тот же текст досыпки, что и `db.py`.
        for sql in db_mod.BACKFILL_TRANSITION_SQL:
            self.assertIn("to_value <> 'done'", sql)
            self.assertIn(sql, done.stdout)


class TransitionEventTests(TempDbTestCase):
    """Пункты 5–13: запись типа перехода и его недоступность извне."""

    def setUp(self) -> None:
        super().setUp()
        self._config_path = self.tmp_path / "config.toml"
        patch = mock.patch.object(paths, "CONFIG_PATH", self._config_path)
        patch.start()
        self.addCleanup(patch.stop)
        store.upsert_project(self.conn, "demo")
        self.tid = store.create_task(self.conn, title="t", project="demo", stage="s1-spec")["id"]
        store.claim(self.conn, self.tid, holder="agent:x")

    def _last_stage(self, tid: str) -> sqlite3.Row:
        return self.conn.execute(
            "SELECT from_value, to_value, note, transition FROM events "
            "WHERE task_id = ? AND kind = 'stage' ORDER BY id DESC LIMIT 1", (tid,)).fetchone()

    def _holder(self, tid: str):
        return self.conn.execute("SELECT holder FROM tasks WHERE id = ?", (tid,)).fetchone()[0]

    def _http(self, method: str, path: str, body: dict):
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            return server.handle(method, path, {}, body, authed=True)

    def test_custom_note_keeps_transition(self) -> None:
        store.next_stage(self.conn, self.tid, note="своя заметка")
        ev = self._last_stage(self.tid)
        self.assertEqual((ev["from_value"], ev["to_value"]), ("s1-spec", "s2-review"))
        self.assertEqual(ev["transition"], "handoff")
        self.assertEqual(ev["note"], "своя заметка")
        self.assertFalse(self._holder(self.tid))

    def test_default_note_format_unchanged(self) -> None:
        store.next_stage(self.conn, self.tid)
        ev = self._last_stage(self.tid)
        self.assertEqual(ev["note"], "этап -> s2-review (handoff)")
        self.assertEqual(ev["transition"], "handoff")

    def test_project_override_sticky(self) -> None:
        store.update_project(self.conn, "demo",
                             routing={"transitions": {"s1-spec:s2-review": "sticky"}})
        store.next_stage(self.conn, self.tid, note="моё")
        self.assertEqual(self._last_stage(self.tid)["transition"], "sticky")
        self.assertEqual(self._holder(self.tid), "agent:x")

    def test_red_verdict_return_is_sticky_return(self) -> None:
        store.next_stage(self.conn, self.tid, to_stage="s4-judge")
        store.add_comment(self.conn, self.tid, "VERDICT: FAIL\n1. починить", author="me",
                          kind="verdict")
        ev = self._last_stage(self.tid)
        self.assertEqual((ev["from_value"], ev["to_value"]), ("s4-judge", "s3-impl"))
        self.assertEqual(ev["transition"], "sticky-return")
        self.assertEqual(ev["note"], "возврат после красного verdict")

    def test_non_pipeline_stage_events_have_no_transition(self) -> None:
        store.next_stage(self.conn, self.tid, to_stage="s4-judge")
        store.next_stage(self.conn, self.tid, to_stage="done")
        ev = self._last_stage(self.tid)
        self.assertEqual((ev["from_value"], ev["to_value"]), ("s4-judge", "done"))
        self.assertIsNone(ev["transition"])

        tid2 = store.create_task(self.conn, title="t2", project="demo", stage="s1-spec")["id"]
        rows = self.conn.execute(
            "SELECT from_value, transition FROM events WHERE task_id = ? AND kind = 'stage'",
            (tid2,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["from_value"])
        self.assertIsNone(rows[0]["transition"])

        store.update_task(self.conn, tid2, stage="s3-impl")
        ev = self._last_stage(tid2)
        self.assertEqual((ev["from_value"], ev["to_value"]), ("s1-spec", "s3-impl"))
        self.assertIsNone(ev["transition"])

    def test_get_task_events_carry_transition(self) -> None:
        store.next_stage(self.conn, self.tid)
        events = store.get_task(self.conn, self.tid)["events"]
        for ev in events:
            self.assertIn("transition", ev)
        kinds = {ev["kind"] for ev in events}
        self.assertTrue({"created", "claim", "release"} <= kinds, kinds)
        for ev in events:
            if ev["kind"] != "stage":
                self.assertIsNone(ev["transition"], ev)

    def test_http_stage_with_custom_note(self) -> None:
        self._http("POST", f"/api/tasks/{self.tid}/stage", {"note": "своя"})
        _, task = self._http("GET", f"/api/tasks/{self.tid}", {})
        stage = [ev for ev in task["events"] if ev["kind"] == "stage"
                 and ev["from_value"] == "s1-spec" and ev["to_value"] == "s2-review"]
        self.assertEqual(len(stage), 1)
        self.assertEqual(stage[0]["note"], "своя")
        self.assertEqual(stage[0]["transition"], "handoff")

    def test_http_patch_transition_is_bad_argument(self) -> None:
        def snapshot():
            row = self.conn.execute("SELECT stage, holder FROM tasks WHERE id = ?",
                                    (self.tid,)).fetchone()
            n = self.conn.execute("SELECT count(*) FROM events WHERE task_id = ?",
                                  (self.tid,)).fetchone()[0]
            return tuple(row) + (n,)

        before = snapshot()
        with self.assertRaises(server.ApiError) as ctx:
            self._http("PATCH", f"/api/tasks/{self.tid}", {"transition": "sticky"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertEqual(snapshot(), before)

    def test_local_update_transition_is_bad_argument(self) -> None:
        # Локальный фолбэк `listik set` без сервера — тот же отказ, что у PATCH.
        def n_events() -> int:
            return self.conn.execute("SELECT count(*) FROM events WHERE task_id = ?",
                                     (self.tid,)).fetchone()[0]

        before = n_events()
        with mock.patch.object(db_mod, "init", return_value=self.conn):
            with self.assertRaises(errors.BadArgument):
                client.local_call("update", task_id=self.tid, stage="s3-impl",
                                  transition="sticky", actor="me", harness=None,
                                  as_owner=None)
            self.assertEqual(n_events(), before)
            self.assertEqual(self.conn.execute("SELECT stage FROM tasks WHERE id = ?",
                                               (self.tid,)).fetchone()[0], "s1-spec")
            # Служебные ключи CLI (`actor`, `harness`, `note`, `as_owner`) проходят.
            out = client.local_call("update", task_id=self.tid, priority=1, actor="me",
                                    harness=None, note="n", as_owner=None)
        self.assertEqual(out["priority"], 1)

    def test_http_stage_ignores_transition_in_body(self) -> None:
        self._http("POST", f"/api/tasks/{self.tid}/stage", {"transition": "sticky"})
        self.assertEqual(self._last_stage(self.tid)["transition"], "handoff")
        self.assertFalse(self._holder(self.tid))


class MetaRoutingTests(TempDbTestCase):
    """Пункты 14–16: общая таблица маршрутизации в `/api/meta` и `local_call("meta")`."""

    def setUp(self) -> None:
        super().setUp()
        self._config_path = self.tmp_path / "config.toml"
        patch = mock.patch.object(paths, "CONFIG_PATH", self._config_path)
        patch.start()
        self.addCleanup(patch.stop)
        store.upsert_project(self.conn, "demo")

    def _meta(self) -> dict:
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            status, data = server.handle("GET", "/api/meta", {}, {}, authed=True)
        self.assertEqual(status, 200)
        return data

    @staticmethod
    def _demo(meta: dict) -> dict:
        return next(p for p in meta["projects"] if p["slug"] == "demo")

    def test_default_config(self) -> None:
        meta = self._meta()
        routing = meta["routing"]
        self.assertEqual(routing["transitions"], config_mod.DEFAULTS["routing"]["transitions"])
        self.assertEqual(routing["transitions"]["s1-spec:s2-review"], "handoff")
        self.assertEqual(routing["return_window_hours"], 24)
        self.assertNotIn("projects", routing)
        self.assertIn("routing_effective", self._demo(meta))

    def test_global_config_without_project_overrides(self) -> None:
        self._config_path.write_text(TOML_OVERRIDES, encoding="utf-8")
        meta = self._meta()
        self.assertEqual(meta["routing"]["transitions"]["s3-impl:s4-judge"], "handoff")
        self.assertEqual(meta["routing"]["transitions"]["s1-spec:s2-review"], "handoff")
        self.assertEqual(
            self._demo(meta)["routing_effective"]["transitions"]["s1-spec:s2-review"], "sticky")

    def test_local_meta_matches_server(self) -> None:
        self._config_path.write_text(TOML_OVERRIDES, encoding="utf-8")
        with mock.patch.object(db_mod, "init", return_value=self.conn):
            local = client.local_call("meta")
        self.assertEqual(local["routing"], self._meta()["routing"])
        self.assertEqual(local["routing"]["transitions"]["s3-impl:s4-judge"], "handoff")


def _load_cli():
    """Загрузить `bin/listik` как модуль — у файла нет расширения `.py`."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_transition_test",
                                                  str(REPO_DIR / "bin" / "listik"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TimelineTransitionTests(TempDbTestCase):
    """listik-hnp5, порция a: `transition` в ленте, `listik show` и `listik timeline`."""

    def setUp(self) -> None:
        super().setUp()
        self._config_path = self.tmp_path / "config.toml"
        patch = mock.patch.object(paths, "CONFIG_PATH", self._config_path)
        patch.start()
        self.addCleanup(patch.stop)
        store.upsert_project(self.conn, "demo")
        self.tid = store.create_task(self.conn, title="t", project="demo", stage="s1-spec")["id"]
        store.claim(self.conn, self.tid, holder="agent:x")
        store.next_stage(self.conn, self.tid, note="своя заметка")

    @staticmethod
    def _stage_item(items: list) -> dict:
        found = [e for e in items if e["kind"] == "stage" and e["from_value"] == "s1-spec"]
        assert len(found) == 1, found
        return found[0]

    def test_store_timeline_carries_transition(self) -> None:
        items = store.task_timeline(self.conn)
        stage = self._stage_item(items)
        self.assertEqual(stage["transition"], "handoff")
        self.assertEqual(stage["note"], "своя заметка")
        others = [e for e in items if e["kind"] != "stage"]
        self.assertTrue(others)
        for ev in others:
            self.assertIn("transition", ev)
            self.assertIsNone(ev["transition"], ev)

    def test_http_timeline_carries_transition(self) -> None:
        with mock.patch.object(server, "get_conn", return_value=self.conn):
            status, data = server.handle("GET", "/api/timeline", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(self._stage_item(data["items"])["transition"], "handoff")

    def test_local_timeline_carries_transition(self) -> None:
        with mock.patch.object(db_mod, "init", return_value=self.conn):
            out = client.local_call("timeline", limit=100, project=None)
        self.assertEqual(self._stage_item(out["items"])["transition"], "handoff")

    def test_show_task_prints_transition(self) -> None:
        cli = _load_cli()
        task = {"id": self.tid, "title": "t", "events": [
            {"ts": "2026-09-28T10:00:00Z", "kind": "stage", "from_value": "s1-spec",
             "to_value": "s2-review", "duration_s": 300, "note": "своя заметка",
             "transition": "handoff"},
            {"ts": "2026-09-28T09:00:00Z", "kind": "stage", "from_value": None,
             "to_value": "s1-spec", "duration_s": None, "note": None, "transition": None},
        ]}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.show_task(task)
        lines = buf.getvalue().splitlines()
        first = next(line for line in lines if "2026-09-28T10:00:00Z" in line)
        second = next(line for line in lines if "2026-09-28T09:00:00Z" in line)
        self.assertIn("s1-spec → s2-review · handoff", first)
        self.assertTrue(first.endswith(" — своя заметка"), first)
        self.assertNotIn(" · ", second)

    def test_cmd_timeline_prints_transition(self) -> None:
        cli = _load_cli()
        items = [
            {"ts": "2026-09-28T10:00:00Z", "kind": "stage", "task_id": self.tid,
             "from_value": "s3-impl", "to_value": "s4-judge", "actor_title": "a",
             "note": "n", "transition": "sticky"},
            {"ts": "2026-09-28T09:00:00Z", "kind": "stage", "task_id": self.tid,
             "from_value": "s4-judge", "to_value": "done", "actor_title": "a",
             "note": "n", "transition": None},
        ]
        args = argparse.Namespace(limit=10, project=None, json=False)
        buf = io.StringIO()
        with mock.patch.object(cli, "call", return_value={"items": items}), \
                contextlib.redirect_stdout(buf):
            self.assertEqual(cli.cmd_timeline(args), 0)
        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertIn("s3-impl → s4-judge · sticky", lines[0])
        self.assertNotIn(" · ", lines[1])


if __name__ == "__main__":
    unittest.main()
