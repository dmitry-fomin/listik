"""Плагины пресетов (listik-d9rj, порция d): `db.init` на базе схемы 15 и alembic-ревизия 0014.

Старые ключи конвейеров (`high-pipeline`, `cc-low-pipeline`…) переезжают на
`<плагин без pipeline->-<скил>` с явным `routes.plugin`; карточки и метки `process:`
идут за строкой `routes`, только если её действительно переименовали. Рой и свои
конвейеры автора не трогаются.

Alembic: старую базу строит сам alembic (`upgrade 0013_drop_route_driver` на пустом
файле), затем в неё вставляются те же строки `routes`/`tasks`/`task_fts`, что в базе
схемы 15 ниже, и `upgrade head`; результат сверяется с `db.init` на базе схемы 15.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
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
REVISION = REPO_DIR / "alembic" / "versions" / "0014_pipeline_plugins.py"

#: Таблица `routes` схемы 15 — без `plugin` (и без снятого в 15 `driver`).
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
    created_at TEXT,
    updated_at TEXT
)
"""

RENAMES = {old: (new, plugin) for old, new, plugin in db_mod.PIPELINE_ROUTE_RENAMES}
NEW_KEYS = sorted(new for new, _ in RENAMES.values())
ROLES = {"impl": {"provider": "claude", "label": "Opus", "title": "Opus · medium"}}
SWARM_ROLES = {"impl": {"harness": "codex", "argv": ["codex", "exec", "{task_id}"]}}
OLD_UPDATED = "2026-01-02T00:00:00Z"
TASK_UPDATED = "2026-02-03T04:05:06Z"


def old_ref(key: str) -> str:
    """Ссылка на скил в команде образца до порции b."""
    if key.startswith("cc-"):
        return f"/claude-codex:{key[3:]}"
    return "/feature-pipeline:{route}"


def old_command(key: str) -> list[str]:
    return ["claude", "--dangerously-skip-permissions", "-p",
            f"Задача {{task_id}} (проект {{project}}), маршрут {{route}}. Запусти скил "
            f"{old_ref(key)} и веди карточку по нему."]


def route_rows(skip=()) -> list[tuple]:
    """(key, kind, command, roles) базы схемы 15: 16 старых ключей, свой конвейер и рой."""
    rows = [(key, "pipeline", old_command(key), ROLES) for key in RENAMES if key not in skip]
    rows.append(("my-flow", "pipeline", ["my-tool", "/feature-pipeline:{route}", "{task_id}"], ROLES))
    rows.append(("roy", "swarm", None, SWARM_ROLES))
    return rows


TASKS = [
    # id, launch_route, labels
    ("t-high", "high-pipeline", ["harness:claude", "process:high-pipeline", "frontend"]),
    ("t-cc", "cc-low-pipeline", ["harness:claude", "process:cc-low-pipeline"]),
    ("t-my", "my-flow", ["harness:claude", "process:my-flow"]),
    ("t-x", None, ["process:high-pipeline-x"]),
]


def fill(conn: sqlite3.Connection, routes, tasks=TASKS) -> None:
    """Строки базы схемы 15: `routes`, `tasks`, их `task_fts` и одно событие."""
    for position, (key, kind, command, roles) in enumerate(routes):
        conn.execute(
            "INSERT INTO routes(key, kind, title, hint, icon, visible, position, command, roles, "
            "created_at, updated_at) VALUES(?, ?, ?, 'подсказка', NULL, 1, ?, ?, ?, "
            "'2026-01-01T00:00:00Z', ?)",
            (key, kind, f"Заголовок {key}", position,
             None if command is None else json.dumps(command, ensure_ascii=False),
             json.dumps(roles, ensure_ascii=False), OLD_UPDATED))
    conn.execute("INSERT INTO projects(slug, title) VALUES('p1', 'Проект')")
    for task_id, route, labels in tasks:
        text = json.dumps(labels, ensure_ascii=False)
        conn.execute("INSERT INTO tasks(id, project, title, launch_route, labels, updated_at) "
                     "VALUES(?, 'p1', ?, ?, ?, ?)", (task_id, f"Задача {task_id}", route, text,
                                                     TASK_UPDATED))
        conn.execute("INSERT INTO task_fts(task_id, title, body, labels) VALUES(?, ?, '', ?)",
                     (task_id, f"Задача {task_id}", text))
    conn.execute("INSERT INTO events(task_id, ts, kind) VALUES('t-high', ?, 'created')",
                 (TASK_UPDATED,))


def make_schema15(path: pathlib.Path, routes=None, tasks=TASKS, additions=None) -> None:
    conn = db_mod.init(path)
    try:
        conn.execute("DROP TABLE routes")
        conn.execute(OLD_ROUTES)
        conn.execute("DELETE FROM meta WHERE key = 'routes_additions'")
        if additions is not None:
            conn.execute("INSERT INTO meta(key, value) VALUES('routes_additions', ?)",
                         (str(additions),))
        fill(conn, route_rows() if routes is None else routes, tasks)
        conn.execute("UPDATE meta SET value = '15' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()


def snapshot(conn: sqlite3.Connection) -> dict:
    """То, что сверяем между `db.init` и alembic: ключи, плагины, карточки, метки."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(routes)")}
    plugin = "plugin" if "plugin" in columns else "NULL"
    return {
        "routes": sorted(tuple(row) for row in conn.execute(
            f"SELECT key, {plugin}, command FROM routes")),
        "tasks": sorted(tuple(row) for row in conn.execute(
            "SELECT id, launch_route, labels FROM tasks")),
        "fts": sorted(tuple(row) for row in conn.execute("SELECT task_id, labels FROM task_fts")),
    }


def dump(conn: sqlite3.Connection) -> tuple:
    return ([tuple(r) for r in conn.execute("SELECT * FROM routes ORDER BY key")],
            [tuple(r) for r in conn.execute("SELECT * FROM tasks ORDER BY id")])


class Schema15Base(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        self.path = self.dir / "old.db"

    def raw(self, path=None) -> sqlite3.Connection:
        conn = sqlite3.connect(path or self.path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def init(self, path=None) -> sqlite3.Connection:
        conn = db_mod.init(path or self.path)
        self.addCleanup(conn.close)
        return conn

    @staticmethod
    def columns(conn) -> set:
        return {row[1] for row in conn.execute("PRAGMA table_info(routes)")}

    @staticmethod
    def task(conn, task_id: str):
        row = conn.execute("SELECT launch_route, labels, updated_at FROM tasks WHERE id = ?",
                           (task_id,)).fetchone()
        return row["launch_route"], json.loads(row["labels"]), row["updated_at"]


class FreshDatabaseTests(Schema15Base):
    def test_fresh_database_has_plugin_and_nothing_to_rename(self) -> None:
        self.assertEqual(db_mod.SCHEMA_VERSION, 16)
        conn = self.init(self.dir / "fresh.db")
        self.assertIn("plugin", self.columns(conn))
        self.assertEqual(conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], "16")
        self.assertFalse(db_mod.rename_pipeline_routes(conn))

    def test_plugin_column_is_not_in_generic_migrations(self) -> None:
        self.assertNotIn(("routes", "plugin"),
                         {(table, column) for table, column, _ in db_mod.MIGRATIONS})


#: Пресеты, снятые после переименования (09.10.2026): записи в базе остаются без скила.
#: Снятый пресет — переименованный ключ, которого уже нет в поставке `routes.json`.
SHIPPED_KEYS = {r["key"] for r in json.loads(
    (REPO_DIR / "routes.json").read_text(encoding="utf-8"))["routes"]}
REMOVED_PRESETS = tuple(key for key in NEW_KEYS if key not in SHIPPED_KEYS)


class PluginMigrationTests(Schema15Base):
    def setUp(self) -> None:
        super().setUp()
        make_schema15(self.path)
        conn = self.raw()
        self.routes_before = {row["key"]: dict(row) for row in conn.execute("SELECT * FROM routes")}
        self.events_before = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        conn.close()

    def test_keys_and_plugins(self) -> None:
        conn = self.init()
        rows = {row["key"]: row for row in conn.execute("SELECT * FROM routes")}
        self.assertEqual(sorted(rows), sorted([*NEW_KEYS, "my-flow", "roy"]))
        self.assertEqual(len(rows), len(self.routes_before))
        self.assertFalse(set(RENAMES) & set(rows))
        for old, (new, plugin) in RENAMES.items():
            with self.subTest(key=new):
                self.assertEqual(rows[new]["plugin"], plugin)
                before = self.routes_before[old]
                for name in ("title", "hint", "icon", "visible", "position", "roles",
                             "created_at"):
                    self.assertEqual(rows[new][name], before[name], name)
                self.assertNotEqual(rows[new]["updated_at"], OLD_UPDATED)
        self.assertIsNone(rows["my-flow"]["plugin"])
        self.assertIsNone(rows["roy"]["plugin"])
        for key in ("my-flow", "roy"):
            self.assertEqual(dict(rows[key], plugin=None),
                             dict(self.routes_before[key], plugin=None), key)

    def test_command_reference_becomes_placeholder(self) -> None:
        conn = self.init()
        rows = {row["key"]: row for row in conn.execute("SELECT key, command FROM routes")}
        high = rows["full-high"]["command"]
        self.assertIn("/{plugin}:{skill}", high)
        self.assertNotIn("feature-pipeline", high)
        self.assertNotIn("claude-codex", rows["cc-low"]["command"])
        for old, (new, _plugin) in RENAMES.items():
            with self.subTest(key=new):
                expected = self.routes_before[old]["command"].replace(
                    old_ref(old), "/{plugin}:{skill}")
                self.assertEqual(rows[new]["command"], expected)
        # Свой конвейер автора: та же подстрока `/feature-pipeline:{route}` не трогается.
        self.assertEqual(rows["my-flow"]["command"], self.routes_before["my-flow"]["command"])

    def test_tasks_follow_renamed_routes(self) -> None:
        conn = self.init()
        self.assertEqual(self.task(conn, "t-high")[:2],
                         ("full-high", ["harness:claude", "process:full-high", "frontend"]))
        self.assertEqual(self.task(conn, "t-cc")[:2],
                         ("cc-low", ["harness:claude", "process:cc-low"]))

    def test_foreign_labels_updated_at_and_events_untouched(self) -> None:
        conn = self.init()
        self.assertEqual(self.task(conn, "t-my")[:2],
                         ("my-flow", ["harness:claude", "process:my-flow"]))
        self.assertEqual(self.task(conn, "t-x")[:2], (None, ["process:high-pipeline-x"]))
        for task_id, _route, _labels in TASKS:
            self.assertEqual(self.task(conn, task_id)[2], TASK_UPDATED, task_id)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                         self.events_before)

    def test_search_index_sees_new_labels(self) -> None:
        conn = self.init()
        labels = conn.execute("SELECT labels FROM task_fts WHERE task_id = 't-high'").fetchone()[0]
        self.assertIn("process:full-high", labels)
        self.assertNotIn('process:high-pipeline"', labels)
        found = [row[0] for row in conn.execute(
            "SELECT task_id FROM task_fts WHERE task_fts MATCH '\"process:full-high\"'")]
        self.assertEqual(found, ["t-high"])
        x = conn.execute("SELECT labels FROM task_fts WHERE task_id = 't-x'").fetchone()[0]
        self.assertEqual(json.loads(x), ["process:high-pipeline-x"])

    def test_no_renamed_route_is_skill_missing(self) -> None:
        conn = self.init()
        data = routes_store.routes_response(conn)
        records = {r["key"]: r for r in data["routes"]}
        for key in NEW_KEYS:
            if key in REMOVED_PRESETS:
                continue
            with self.subTest(key=key):
                self.assertNotIn("skill_missing", records[key])
                self.assertIsNotNone(records[key]["skill_path"])
        # Снятые пресеты (sol, opus, nano у pipeline-cc): переименованные записи остаются,
        # но без скила — скрыты. Список выводится из поставки и имена здесь не зашиты.
        self.assertEqual(len(REMOVED_PRESETS), 3)
        for key in REMOVED_PRESETS:
            self.assertTrue(records[key]["skill_missing"], key)
        self.assertTrue(records["my-flow"]["skill_missing"])

    def test_second_init_is_noop(self) -> None:
        conn = db_mod.init(self.path)
        first = dump(conn)
        conn.close()
        conn = self.init()
        self.assertEqual(dump(conn), first)
        self.assertFalse(db_mod.rename_pipeline_routes(conn))

    def test_failure_mid_migration_rolls_back(self) -> None:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        broken = (("high-pipeline", "full-high", "pipeline-full"), ("x",))
        with mock.patch.object(db_mod, "PIPELINE_ROUTE_RENAMES", broken):
            with self.assertRaises(ValueError):
                db_mod.rename_pipeline_routes(conn)
        self.assertNotIn("plugin", self.columns(conn))
        keys = {row[0] for row in conn.execute("SELECT key FROM routes")}
        self.assertIn("high-pipeline", keys)
        self.assertNotIn("full-high", keys)
        for task_id, route, labels in TASKS:
            self.assertEqual(self.task(conn, task_id)[:2], (route, labels), task_id)
        fts = conn.execute("SELECT labels FROM task_fts WHERE task_id = 't-high'").fetchone()[0]
        self.assertIn('"process:high-pipeline"', fts)
        conn.close()
        # Следующий init доводит миграцию до конца.
        done = self.init()
        self.assertEqual(self.task(done, "t-high")[0], "full-high")


class CornerCaseTests(Schema15Base):
    def test_existing_new_key_blocks_rename_and_cards_stay(self) -> None:
        routes = [*route_rows(), ("full-high", "pipeline", ["own", "{task_id}"], ROLES)]
        make_schema15(self.path, routes=routes)
        before = {row["key"]: dict(row) for row in self.raw().execute("SELECT * FROM routes")}
        conn = self.init()
        rows = {row["key"]: dict(row) for row in conn.execute("SELECT * FROM routes")}
        self.assertEqual(rows["high-pipeline"]["command"], before["high-pipeline"]["command"])
        self.assertIsNone(rows["high-pipeline"]["plugin"])
        self.assertEqual(dict(rows["full-high"], plugin=None), before["full-high"] | {"plugin": None})
        self.assertIsNone(rows["full-high"]["plugin"])
        self.assertEqual(self.task(conn, "t-high")[:2],
                         ("high-pipeline", ["harness:claude", "process:high-pipeline", "frontend"]))
        # Остальные переименования прошли.
        self.assertEqual(rows["cc-low"]["plugin"], "pipeline-cc")

    def test_deleted_old_route_keeps_cards(self) -> None:
        make_schema15(self.path, routes=route_rows(skip=("cc-low-pipeline",)))
        conn = self.init()
        keys = {row[0] for row in conn.execute("SELECT key FROM routes")}
        self.assertNotIn("cc-low", keys)
        self.assertEqual(self.task(conn, "t-cc")[:2],
                         ("cc-low-pipeline", ["harness:claude", "process:cc-low-pipeline"]))

    def test_swarm_with_old_key_is_not_renamed(self) -> None:
        routes = [r for r in route_rows() if r[0] != "low-pipeline"]
        routes.append(("low-pipeline", "swarm", None, SWARM_ROLES))
        make_schema15(self.path, routes=routes,
                      tasks=[("t-low", "low-pipeline", ["process:low-pipeline"])])
        conn = self.init()
        row = conn.execute("SELECT kind, plugin FROM routes WHERE key = 'low-pipeline'").fetchone()
        self.assertEqual(tuple(row), ("swarm", None))
        self.assertEqual(self.task(conn, "t-low")[:2], ("low-pipeline", ["process:low-pipeline"]))

    def test_route_additions_bring_cc_routes_with_plugin(self) -> None:
        cc = {"cc-xhigh-pipeline", "cc-high-pipeline", "cc-medium-pipeline", "cc-low-pipeline",
              "cc-xlow-pipeline"}
        make_schema15(self.path, routes=route_rows(skip=cc), additions=2)
        conn = self.init()
        with contextlib.redirect_stderr(io.StringIO()):
            result = routes_store.ensure_imported(conn)
        self.assertNotIn("error", result)
        rows = {row["key"]: row["plugin"] for row in conn.execute("SELECT key, plugin FROM routes")}
        for key in ("cc-xhigh", "cc-high", "cc-medium", "cc-low", "cc-xlow"):
            with self.subTest(key=key):
                self.assertEqual(rows.get(key), "pipeline-cc")


def load_revision():
    spec = importlib.util.spec_from_file_location("rev_0014_pipeline_plugins", REVISION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PluginAlembicTests(Schema15Base):
    def setUp(self) -> None:
        if shutil.which("alembic") is None:
            self.skipTest("alembic не установлен")
        super().setUp()
        self.db = self.dir / "a.db"
        self.env = {**os.environ, "LISTIK_DB": str(self.db)}

    def alembic(self, *args) -> str:
        done = subprocess.run(["alembic", *args], cwd=REPO_DIR, capture_output=True,
                              text=True, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_revision_links_to_drop_route_driver(self) -> None:
        text = REVISION.read_text(encoding="utf-8")
        self.assertIn('revision = "0014_pipeline_plugins"', text)
        self.assertIn('down_revision = "0013_drop_route_driver"', text)

    def test_rename_table_matches_db(self) -> None:
        try:
            revision = load_revision()
        except ImportError as exc:  # пакет alembic не виден этому python
            self.skipTest(f"ревизия не импортируется: {exc}")
        self.assertEqual(revision.PIPELINE_ROUTE_RENAMES, db_mod.PIPELINE_ROUTE_RENAMES)
        for row in db_mod.PIPELINE_ROUTE_RENAMES:
            self.assertEqual(revision.upgrade_sql(*row), db_mod.pipeline_rename_sql(*row))

    def test_offline_sql(self) -> None:
        out = self.alembic("upgrade", "0013_drop_route_driver:0014_pipeline_plugins", "--sql")
        self.assertIn("ALTER TABLE routes ADD COLUMN plugin", out)
        self.assertIn("UPDATE routes SET key = 'full-high'", out)
        for sql in db_mod.pipeline_rename_sql("cc-low-pipeline", "cc-low", "pipeline-cc"):
            self.assertIn(sql, out)

    def test_online_upgrade_matches_init_and_downgrade_restores(self) -> None:
        self.alembic("upgrade", "0013_drop_route_driver")
        conn = self.raw(self.db)
        self.assertNotIn("plugin", self.columns(conn))
        fill(conn, route_rows())
        conn.commit()
        before = snapshot(conn)
        conn.close()

        self.alembic("upgrade", "head")
        make_schema15(self.path)
        expected = snapshot(self.init())
        conn = self.raw(self.db)
        self.assertEqual(snapshot(conn), expected)
        conn.close()

        self.alembic("downgrade", "0013_drop_route_driver")
        conn = self.raw(self.db)
        self.assertNotIn("plugin", self.columns(conn))
        after = snapshot(conn)
        self.assertEqual(after["tasks"], before["tasks"])
        self.assertEqual(after["fts"], before["fts"])
        self.assertEqual(sorted(key for key, _p, _c in after["routes"]),
                         sorted(key for key, _p, _c in before["routes"]))
        commands = {key: command for key, _p, command in after["routes"]}
        self.assertIn("/feature-pipeline:{route}", commands["high-pipeline"])
        self.assertIn("/claude-codex:low-pipeline", commands["cc-low-pipeline"])
        self.assertIn("/feature-pipeline:{route}", commands["sol-pipeline"])


if __name__ == "__main__":
    unittest.main()
