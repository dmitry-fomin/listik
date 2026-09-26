"""Перевод старой базы без вида `direct` (listik-ar8v): `db.init` на схеме 11.

Каждая строка `kind='direct'` становится маршрутом роя с одной ролью `impl`,
колонка `routes.harness` удаляется, харнесс не из каталога заводится в `harnesses`.
"""
from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
import unittest
from unittest import mock

from listik import db as db_mod
from listik import routes_store

#: Таблица `routes` схемы 11 — с колонкой `harness`.
OLD_ROUTES = """
CREATE TABLE routes (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    hint       TEXT NOT NULL DEFAULT '',
    icon       TEXT,
    visible    INTEGER NOT NULL DEFAULT 1,
    position   INTEGER NOT NULL DEFAULT 0,
    harness    TEXT,
    command    TEXT,
    roles      TEXT,
    driver     TEXT NOT NULL DEFAULT 'skill',
    created_at TEXT,
    updated_at TEXT
)
"""

PROMPT = "Задача {task_id} уже выдана тебе: claim, stage, done"
ROWS = [
    # key, harness, command
    ("r-grok", "grok", ["grok", "--yolo", "-p", PROMPT]),
    ("r-bot", "my-bot", ["my-bot", "run", "{task_id}", PROMPT]),
    ("r-one", "codex", ["codex"]),
    ("r-null", "devin", None),
]


class DirectMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "old.db"
        conn = db_mod.init(self.path)
        conn.execute("DROP TABLE routes")
        conn.execute(OLD_ROUTES)
        for position, (key, harness, command) in enumerate(ROWS):
            conn.execute(
                "INSERT INTO routes(key, kind, title, hint, icon, visible, position, harness, "
                "command, roles, driver, created_at, updated_at) "
                "VALUES(?, 'direct', ?, 'подсказка', 'direct', ?, ?, ?, ?, NULL, 'skill', "
                "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (key, f"Заголовок {key}", position % 2, 10 + position, harness,
                 None if command is None else json.dumps(command, ensure_ascii=False)))
        conn.execute("INSERT INTO projects(slug, title) VALUES('p1', 'Проект')")
        conn.execute("INSERT INTO tasks(id, project, title, launch_route) "
                     "VALUES('t1', 'p1', 'Старая', 'r-grok')")
        conn.execute("UPDATE meta SET value = '11' WHERE key = 'schema_version'")
        conn.commit()
        self.harnesses_before = self.harnesses(conn)
        self.routes_before = {row["key"]: dict(row) for row in
                              conn.execute("SELECT * FROM routes")}
        conn.close()

    @staticmethod
    def harnesses(conn) -> dict:
        return {row["key"]: dict(row) for row in conn.execute("SELECT * FROM harnesses")}

    @staticmethod
    def columns(conn) -> set:
        return {row["name"] for row in conn.execute("PRAGMA table_info(routes)")}

    def open_migrated(self) -> sqlite3.Connection:
        conn = db_mod.init(self.path)
        self.addCleanup(conn.close)
        return conn

    def test_rows_become_single_role_swarm_routes(self) -> None:
        conn = self.open_migrated()
        self.assertNotIn("harness", self.columns(conn))
        rows = {row["key"]: row for row in conn.execute("SELECT * FROM routes")}
        self.assertEqual(set(rows), {key for key, _, _ in ROWS})
        expected_roles = {
            "r-grok": {"impl": {"harness": "grok", "argv": ["grok", "--yolo", "-p"]}},
            "r-bot": {"impl": {"harness": "my-bot", "argv": ["my-bot", "run", "{task_id}"]}},
            "r-one": {"impl": {"harness": "codex"}},
            "r-null": {"impl": {"harness": "devin"}},
        }
        for key, row in rows.items():
            with self.subTest(key=key):
                self.assertEqual(row["kind"], "swarm")
                self.assertEqual(row["driver"], "swarm")
                self.assertIsNone(row["command"])
                self.assertEqual(json.loads(row["roles"]), expected_roles[key])
                before = self.routes_before[key]
                for name in ("title", "hint", "icon", "visible", "position"):
                    self.assertEqual(row[name], before[name], name)
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        self.assertEqual(version, "12")
        self.assertEqual(conn.execute(
            "SELECT launch_route FROM tasks WHERE id = 't1'").fetchone()[0], "r-grok")

    def test_unknown_harness_gets_catalogue_record(self) -> None:
        conn = self.open_migrated()
        after = self.harnesses(conn)
        self.assertEqual(set(after), set(self.harnesses_before) | {"my-bot"})
        for key, record in self.harnesses_before.items():
            self.assertEqual(after[key], record, key)
        bot = after["my-bot"]
        self.assertEqual(bot["label"], "my-bot")
        self.assertEqual(bot["hint"], "")
        self.assertIsNone(bot["icon"])
        self.assertEqual(json.loads(bot["argv"]), ["my-bot", "run", "{task_id}"])
        self.assertIsNone(bot["prompt"])
        self.assertEqual(bot["kind"], "exec")
        self.assertEqual(bot["builtin"], 0)
        self.assertEqual(bot["enabled"], 1)
        self.assertGreater(bot["position"],
                           max(r["position"] for r in self.harnesses_before.values()))
        self.assertTrue(bot["created_at"])
        self.assertTrue(bot["updated_at"])

    def test_records_have_no_harness_field(self) -> None:
        conn = self.open_migrated()
        for key, _, _ in ROWS:
            record = routes_store.get_route(conn, key)
            self.assertNotIn("harness", record)
            self.assertEqual(record["kind"], "swarm")

    def test_second_init_is_noop(self) -> None:
        conn = db_mod.init(self.path)
        routes = [dict(row) for row in conn.execute("SELECT * FROM routes ORDER BY key")]
        harnesses = self.harnesses(conn)
        conn.close()
        conn = self.open_migrated()
        self.assertEqual([dict(row) for row in
                          conn.execute("SELECT * FROM routes ORDER BY key")], routes)
        self.assertEqual(self.harnesses(conn), harnesses)
        self.assertFalse(db_mod.drop_direct_routes(conn))

    def test_failure_rolls_back_everything(self) -> None:
        broken = (*db_mod.DROP_DIRECT_SQL[:-1], "ALTER TABLE нет_такой DROP COLUMN x")
        with mock.patch.object(db_mod, "DROP_DIRECT_SQL", broken):
            with self.assertRaises(sqlite3.OperationalError):
                db_mod.init(self.path)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        self.assertIn("harness", self.columns(conn))
        self.assertEqual({row["key"]: dict(row) for row in
                          conn.execute("SELECT * FROM routes")}, self.routes_before)
        self.assertNotIn("my-bot", self.harnesses(conn))


if __name__ == "__main__":
    unittest.main()
