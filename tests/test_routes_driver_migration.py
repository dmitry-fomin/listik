"""Снятие `routes.driver` (listik-ujra): `db.init` на базе схемы 14 и alembic-ревизия 0013.

Способ исполнения маршрута выводится из `kind`. Запрещённое сочетание
`pipeline` + `driver='swarm'` с непустым раскладом становится роем тем же ключом,
с пустым — остаётся конвейером; `tasks.launch_driver` миграция не трогает.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

from listik import db as db_mod
from listik import routes_store

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
REVISION = REPO_DIR / "alembic" / "versions" / "0013_drop_route_driver.py"

#: Таблица `routes` схемы 14 — с колонкой `driver`.
OLD_ROUTES = """
CREATE TABLE routes (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    hint       TEXT NOT NULL DEFAULT '',
    icon       TEXT,
    visible    INTEGER NOT NULL DEFAULT 1,
    position   INTEGER NOT NULL DEFAULT 0,
    command    TEXT,
    roles      TEXT,
    driver     TEXT NOT NULL DEFAULT 'skill',
    created_at TEXT,
    updated_at TEXT
)
"""

SKILL_ROLES = {"impl": {"provider": "claude", "label": "Opus", "title": "Opus · medium"}}
SWARM_ROLES = {"impl": {"harness": "codex", "argv": ["codex", "exec", "{task_id}"]}}
ROWS = [
    # key, kind, driver, roles, command
    ("p-skill", "pipeline", "skill", SKILL_ROLES, ["claude", "-p", "{task_id}"]),
    ("roy", "swarm", "swarm", SWARM_ROLES, None),
    ("p-swarm", "pipeline", "swarm", SWARM_ROLES, ["x", "{task_id}"]),
    ("p-swarm-empty", "pipeline", "swarm", {}, None),
]
KEPT_COLUMNS = ("title", "hint", "icon", "visible", "position", "command", "roles",
                "created_at", "updated_at")


class DriverMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = pathlib.Path(tmp.name) / "old.db"
        conn = db_mod.init(self.path)
        conn.execute("DROP TABLE routes")
        conn.execute(OLD_ROUTES)
        for position, (key, kind, driver, roles, command) in enumerate(ROWS):
            conn.execute(
                "INSERT INTO routes(key, kind, title, hint, icon, visible, position, "
                "command, roles, driver, created_at, updated_at) "
                "VALUES(?, ?, ?, 'подсказка', 'high', ?, ?, ?, ?, ?, "
                "'2026-01-01T00:00:00Z', '2026-01-02T00:00:00Z')",
                (key, kind, f"Заголовок {key}", position % 2, 10 + position,
                 None if command is None else json.dumps(command), json.dumps(roles), driver))
        conn.execute("INSERT INTO projects(slug, title) VALUES('p1', 'Проект')")
        conn.execute("INSERT INTO tasks(id, project, title, launch_route, launch_driver) "
                     "VALUES('t1', 'p1', 'Старая', 'p-swarm', 'skill')")
        conn.execute("UPDATE meta SET value = '14' WHERE key = 'schema_version'")
        conn.commit()
        self.routes_before = self.routes(conn)
        conn.close()

    @staticmethod
    def routes(conn) -> dict:
        conn.row_factory = sqlite3.Row
        return {row["key"]: dict(row) for row in conn.execute("SELECT * FROM routes")}

    @staticmethod
    def columns(conn) -> set:
        return {row[1] for row in conn.execute("PRAGMA table_info(routes)")}

    def open_migrated(self) -> sqlite3.Connection:
        conn = db_mod.init(self.path)
        self.addCleanup(conn.close)
        return conn

    def raw(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_fresh_database_has_no_driver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = db_mod.init(pathlib.Path(tmp) / "fresh.db")
            try:
                self.assertNotIn("driver", self.columns(conn))
                self.assertEqual(conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], "16")
            finally:
                conn.close()

    def test_migration_keeps_rows_and_derives_kind(self) -> None:
        conn = self.open_migrated()
        self.assertNotIn("driver", self.columns(conn))
        after = self.routes(conn)
        self.assertEqual(set(after), set(self.routes_before))
        expected_kind = {"p-skill": "pipeline", "roy": "swarm", "p-swarm": "swarm",
                         "p-swarm-empty": "pipeline"}
        for key, row in after.items():
            with self.subTest(key=key):
                self.assertEqual(row["kind"], expected_kind[key])
                for name in KEPT_COLUMNS:
                    self.assertEqual(row[name], self.routes_before[key][name], name)
        records = {r["key"]: r for r in routes_store.list_routes(conn)}
        self.assertEqual(set(records), set(self.routes_before))
        for key in records:
            self.assertNotIn("driver", routes_store.get_route(conn, key))
        self.assertEqual(conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], "16")
        task = conn.execute(
            "SELECT launch_route, launch_driver FROM tasks WHERE id = 't1'").fetchone()
        self.assertEqual(tuple(task), ("p-swarm", "skill"))

    def test_second_init_is_noop(self) -> None:
        conn = db_mod.init(self.path)
        routes = self.routes(conn)
        conn.close()
        conn = self.open_migrated()
        self.assertEqual(self.routes(conn), routes)
        self.assertFalse(db_mod.drop_route_driver(conn))

    def test_failure_rolls_back_then_reinit_finishes(self) -> None:
        broken = (*db_mod.KIND_ONLY_ROUTES_SQL[:-1], "ALTER TABLE нет_такой DROP COLUMN x")
        with mock.patch.object(db_mod, "KIND_ONLY_ROUTES_SQL", broken):
            with self.assertRaises(sqlite3.OperationalError):
                db_mod.init(self.path)
        conn = self.raw()
        self.assertIn("driver", self.columns(conn))
        self.assertEqual(self.routes(conn), self.routes_before)
        conn.close()
        conn = self.open_migrated()
        self.assertNotIn("driver", self.columns(conn))
        self.assertEqual(self.routes(conn)["p-swarm"]["kind"], "swarm")


class DriverAlembicTests(unittest.TestCase):
    def setUp(self) -> None:
        if shutil.which("alembic") is None:
            self.skipTest("alembic не установлен")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = pathlib.Path(tmp.name) / "a.db"
        self.env = {**os.environ, "LISTIK_DB": str(self.db)}

    def alembic(self, *args) -> str:
        done = subprocess.run(["alembic", *args], cwd=REPO_DIR, capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def has_driver(self) -> bool:
        conn = sqlite3.connect(self.db)
        try:
            return "driver" in {row[1] for row in conn.execute("PRAGMA table_info(routes)")}
        finally:
            conn.close()

    def test_revision_links_to_event_transition(self) -> None:
        text = REVISION.read_text(encoding="utf-8")
        self.assertIn('revision = "0013_drop_route_driver"', text)
        self.assertIn('down_revision = "0012_event_transition"', text)

    def test_offline_sql(self) -> None:
        out = self.alembic("upgrade", "0012_event_transition:0013_drop_route_driver", "--sql")
        self.assertIn("ALTER TABLE routes DROP COLUMN driver", out)
        self.assertIn("UPDATE routes SET kind = 'swarm'", out)
        # Ревизия несёт тот же текст, что и `db.py`.
        for sql in db_mod.KIND_ONLY_ROUTES_SQL:
            self.assertIn(sql, out)

    def test_online_upgrade_downgrade_upgrade(self) -> None:
        self.alembic("upgrade", "head")
        self.assertFalse(self.has_driver())
        self.alembic("downgrade", "0012_event_transition")
        self.assertTrue(self.has_driver())
        self.alembic("upgrade", "head")
        self.assertFalse(self.has_driver())


if __name__ == "__main__":
    unittest.main()
