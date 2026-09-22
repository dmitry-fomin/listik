"""Области файлов и ограждение запуска: схема, версия 10, карточка (listik-s520, порция a).

Эта порция — только хранение и чтение: `read_scope`/`write_scope`/`dispatch_id`/
`generation` появляются в схеме и в карточке, но запись пока не поддержана
(`UPDATABLE` не расширяется).
"""
from __future__ import annotations

import pathlib
import sqlite3

from listik import db as db_mod
from listik import deps as deps_mod
from listik import store

from tests.helpers import TempDbTestCase

SCOPE_COLUMNS = ("read_scope", "write_scope", "dispatch_id", "generation")


class SchemaMigrationTests(TempDbTestCase):
    def test_migration_nine_to_ten(self) -> None:
        # «Старая» база версии 9: та же схема, но без четырёх новых колонок.
        kept = [line for line in db_mod.SCHEMA.splitlines()
                if not any(line.strip().startswith(name + " ") for name in SCOPE_COLUMNS)]
        old_lines = []
        for i, line in enumerate(kept):
            tail = [item for item in kept[i + 1:]
                    if item.strip() and not item.strip().startswith("--")]
            if tail and tail[0].strip() == ");":
                code, sep, comment = line.partition("--")
                code = code.rstrip()
                if code.endswith(","):
                    line = code[:-1] + ((" " + sep + comment) if sep else "")
            old_lines.append(line)
        old_schema = "\n".join(old_lines)

        # (а) вырезание сработало на уровне текста схемы.
        for name in SCOPE_COLUMNS:
            self.assertNotIn(name, old_schema)

        old_path = self.tmp_path / "old.db"
        old = sqlite3.connect(old_path)
        old.executescript(old_schema)
        old.execute("INSERT INTO projects(slug, title) VALUES('p1', 'Старый')")
        old.execute("INSERT INTO tasks(id, project, title) VALUES('t1', 'p1', 'Старая')")
        old.execute("INSERT INTO meta(key, value) VALUES('schema_version', '9')")
        old.commit()

        # (б) в «старой» базе нет ни одной из новых колонок.
        columns = {r[1] for r in old.execute("PRAGMA table_info(tasks)")}
        for name in SCOPE_COLUMNS:
            self.assertNotIn(name, columns)
        old.close()

        conn = db_mod.init(old_path)
        try:
            columns = {r["name"] for r in conn.execute("PRAGMA table_info(tasks)")}
            for name in SCOPE_COLUMNS:
                self.assertIn(name, columns, name)
            row = conn.execute(
                "SELECT read_scope, write_scope, dispatch_id, generation "
                "FROM tasks WHERE id = 't1'").fetchone()
            self.assertEqual(row["read_scope"], "[]")
            self.assertEqual(row["write_scope"], "[]")
            self.assertIsNone(row["dispatch_id"])
            self.assertEqual(row["generation"], 0)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0], 1)
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertEqual(version, "11")
            self.assertEqual(db_mod.SCHEMA_VERSION, 11)
            self.assertEqual(db_mod.migrate(conn), [])
        finally:
            conn.close()

        # Повторный init не падает и ничего не меняет.
        conn = db_mod.init(old_path)
        conn.close()

    def test_alembic_revision_file(self) -> None:
        path = (pathlib.Path(__file__).resolve().parent.parent
                / "alembic" / "versions" / "0008_task_scope_fencing.py")
        text = path.read_text(encoding="utf-8")
        self.assertIn('revision = "0008_task_scope_fencing"', text)
        self.assertIn('down_revision = "0007_routes"', text)
        self.assertIn("ALTER TABLE tasks ADD COLUMN", text)
        self.assertIn('("read_scope", "TEXT NOT NULL DEFAULT \'[]\'")', text)
        self.assertIn('("write_scope", "TEXT NOT NULL DEFAULT \'[]\'")', text)
        self.assertIn('("dispatch_id", "TEXT")', text)
        self.assertIn('("generation", "INTEGER NOT NULL DEFAULT 0")', text)


class ScopeCardTests(TempDbTestCase):
    def test_fresh_task_has_empty_scope_fields(self) -> None:
        task = store.create_task(self.conn, title="x", project="demo")
        card = store.get_task(self.conn, task["id"])
        self.assertEqual(card["read_scope"], [])
        self.assertEqual(card["write_scope"], [])
        self.assertIsNone(card["dispatch_id"])
        self.assertEqual(card["generation"], 0)

        ready = deps_mod.ready_tasks(self.conn, project="demo")
        by_id = {t["id"]: t for t in ready}
        self.assertIn(task["id"], by_id)
        ready_card = by_id[task["id"]]
        self.assertEqual(ready_card["read_scope"], [])
        self.assertEqual(ready_card["write_scope"], [])
        self.assertIsNone(ready_card["dispatch_id"])
        self.assertEqual(ready_card["generation"], 0)

    def test_garbage_in_scope_column_falls_back_to_empty_list(self) -> None:
        task = store.create_task(self.conn, title="x", project="demo")
        self.conn.execute(
            "UPDATE tasks SET read_scope = 'не json' WHERE id = ?", (task["id"],))
        self.conn.commit()
        card = store.get_task(self.conn, task["id"])
        self.assertEqual(card["read_scope"], [])

    def test_fencing_fields_are_not_writable_via_update_task(self) -> None:
        task = store.create_task(self.conn, title="x", project="demo")
        result = store.update_task(
            self.conn, task["id"], generation=5, dispatch_id="x", actor="автор")
        self.assertTrue(result.get("unchanged"))
        row = self.conn.execute(
            "SELECT generation, dispatch_id FROM tasks WHERE id = ?",
            (task["id"],)).fetchone()
        self.assertEqual(row["generation"], 0)
        self.assertIsNone(row["dispatch_id"])
