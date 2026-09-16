"""Владелец-человек и серверный режим на уровне store (listik-xt69, порция a).

Конфиг изолируется подменой `paths.CONFIG_PATH` (как в `tests/test_mcp_http.py`):
`config.load()` читает его в момент вызова, поэтому режим можно менять прямо в тесте.
Настоящие `config.toml`/`listik.db` не используются.
"""
from __future__ import annotations

import pathlib
import shutil
import sqlite3
import tempfile
import unittest

from listik import config as config_mod
from listik import db as db_mod
from listik import deps as deps_mod
from listik import errors as errors_mod
from listik import paths, store

from tests.helpers import TempDbTestCase

LOCAL_CONFIG = '[auth]\ntoken = "t"\n'
SERVER_CONFIG = '[auth]\ntoken = "t"\n\n[server]\nmode = "server"\nusers = ["ann", "bob"]\n'

TASK_COLUMNS = ("holder", "holder_at", "stage", "status", "owner", "updated_at", "title")


class ConfigCase(unittest.TestCase):
    """Только конфиг: базы тут не нужно."""

    def setUp(self) -> None:
        self._saved = paths.CONFIG_PATH
        self._tmp = pathlib.Path(tempfile.mkdtemp())
        paths.CONFIG_PATH = self._tmp / "config.toml"

    def tearDown(self) -> None:
        paths.CONFIG_PATH = self._saved
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write(self, text: str) -> None:
        paths.CONFIG_PATH.write_text(text, encoding="utf-8")

    # --- п. 1
    def test_server_mode(self) -> None:
        self.write(LOCAL_CONFIG)
        self.assertEqual(config_mod.server_mode(), "local")
        self.assertFalse(config_mod.is_server_mode())
        self.write(SERVER_CONFIG)
        self.assertEqual(config_mod.server_mode(), "server")
        self.assertTrue(config_mod.is_server_mode())
        self.write('[server]\nmode = "team"\n')
        with self.assertRaises(ValueError) as ctx:
            config_mod.server_mode()
        self.assertIn("server.mode", str(ctx.exception))

    # --- п. 2
    def test_users(self) -> None:
        self.write(LOCAL_CONFIG)
        self.assertEqual(config_mod.users(), [])
        self.write(SERVER_CONFIG)
        self.assertEqual(config_mod.users(), ["ann", "bob"])
        self.write('[server]\nusers = "ann"\n')
        with self.assertRaises(ValueError) as ctx:
            config_mod.users()
        self.assertIn("server.users", str(ctx.exception))
        self.write('[server]\nusers = ["ann", ""]\n')
        with self.assertRaises(ValueError) as ctx:
            config_mod.users()
        self.assertIn("server.users", str(ctx.exception))
        self.write('[server]\nusers = ["ann", 5]\n')
        with self.assertRaises(ValueError):
            config_mod.users()

    # --- п. 3
    def test_check_owner_server(self) -> None:
        self.write(SERVER_CONFIG)
        with self.assertRaises(errors_mod.BadArgument) as ctx:
            config_mod.check_owner("carol")
        self.assertIn("carol", str(ctx.exception))
        with self.assertRaises(errors_mod.BadArgument):
            config_mod.check_owner("ANN")
        self.assertEqual(config_mod.check_owner("ann"), "ann")
        self.assertEqual(config_mod.check_owner(" ann "), "ann")
        for empty in (None, "", "  "):
            self.assertIsNone(config_mod.check_owner(empty))

    # --- п. 4
    def test_check_owner_local(self) -> None:
        self.write(LOCAL_CONFIG)
        self.assertIsNone(config_mod.check_owner("carol"))
        self.assertIsNone(config_mod.check_owner(None))

    # --- п. 5
    def test_default_owner(self) -> None:
        self.write(LOCAL_CONFIG)
        self.assertEqual(config_mod.default_owner(), "")
        self.write('[auth]\ntoken = "t"\nowner = " ann "\n')
        self.assertEqual(config_mod.default_owner(), "ann")

    # --- п. 6
    def test_save_does_not_write_new_keys(self) -> None:
        self.write('[auth]\ntoken = "t"\n')
        config_mod.ensure_token()
        text = paths.CONFIG_PATH.read_text(encoding="utf-8")
        for key in ("mode", "users", "owner"):
            self.assertNotIn(key, text)
        dumped = str(config_mod.DEFAULTS)
        for key in ("mode", "users", "owner"):
            self.assertNotIn(f"'{key}'", dumped)


class SchemaCase(TempDbTestCase):
    # --- п. 8, 9
    def test_migration_adds_owner(self) -> None:
        schema = "\n".join(line for line in db_mod.SCHEMA.splitlines()
                           if not line.strip().startswith("owner "))
        self.assertNotIn("owner        TEXT", schema)
        old_path = self.tmp_path / "old.db"
        old = sqlite3.connect(old_path)
        old.executescript(schema)
        old.commit()
        old.close()

        conn = db_mod.init(old_path)
        try:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
            self.assertIn("owner", columns)
            indexes = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'")]
            self.assertEqual([n for n in indexes if "owner" in n], [])
            version = conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
            self.assertEqual(version, "8")
        finally:
            conn.close()
        self.assertEqual(db_mod.SCHEMA_VERSION, 8)

    # --- п. 10 (файл ревизии; прогон alembic — команда в чек-листе)
    def test_alembic_revision_file(self) -> None:
        path = (pathlib.Path(__file__).resolve().parent.parent
                / "alembic" / "versions" / "0006_task_owner.py")
        text = path.read_text(encoding="utf-8")
        self.assertIn('revision = "0006_task_owner"', text)
        self.assertIn('down_revision = "0005_task_launch"', text)
        self.assertIn("ALTER TABLE tasks ADD COLUMN", text)
        self.assertIn('("owner", "TEXT")', text)


class ErrorsCase(unittest.TestCase):
    # --- п. 11, 12
    def test_codes_and_hints(self) -> None:
        self.assertEqual(errors_mod.code_of(errors_mod.BadArgument("x")), "bad_argument")
        self.assertEqual(errors_mod.code_of(errors_mod.Forbidden("x")), "forbidden")
        err = errors_mod.as_error(errors_mod.Forbidden("x"))
        self.assertEqual(err.hint, errors_mod.OWNER_HINT)
        self.assertNotIn("токен", err.hint)
        self.assertEqual(errors_mod.code_of(ValueError("x")), "conflict")

    def test_mcp_error_text(self) -> None:
        text = errors_mod.mcp_error_text(errors_mod.Forbidden("чужая"))
        self.assertIn("чужая", text)
        self.assertNotIn("Forbidden", text)


class OwnerStoreCase(TempDbTestCase):
    """База + подменённый config.toml; режим выбирает сам тест."""

    def setUp(self) -> None:
        super().setUp()
        self._saved_config = paths.CONFIG_PATH
        paths.CONFIG_PATH = self.tmp_path / "config.toml"
        self.local_mode()

    def tearDown(self) -> None:
        paths.CONFIG_PATH = self._saved_config
        super().tearDown()

    def local_mode(self) -> None:
        paths.CONFIG_PATH.write_text(LOCAL_CONFIG, encoding="utf-8")

    def server_mode(self) -> None:
        paths.CONFIG_PATH.write_text(SERVER_CONFIG, encoding="utf-8")

    # --- вспомогательное

    def raw_owner(self, task_id: str):
        return self.conn.execute("SELECT owner FROM tasks WHERE id = ?",
                                 (task_id,)).fetchone()[0]

    def snapshot(self, task_id: str) -> tuple:
        row = self.conn.execute(
            f"SELECT {', '.join(TASK_COLUMNS)} FROM tasks WHERE id = ?", (task_id,)).fetchone()
        events = self.conn.execute("SELECT count(*) FROM events").fetchone()[0]
        return (tuple(row), events)

    def make(self, title: str = "задача", **kwargs) -> dict:
        return store.create_task(self.conn, title=title, **kwargs)


class ServerModeCreateCase(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()

    # --- п. 13, 14
    def test_owner_written(self) -> None:
        task = self.make(as_owner="ann")
        self.assertEqual(task["owner"], "ann")
        self.assertEqual(self.raw_owner(task["id"]), "ann")
        self.assertEqual(self.make(as_owner="ann", owner="bob")["owner"], "bob")
        self.assertEqual(self.make(as_owner="ann", owner="")["owner"], "ann")

    # --- п. 15
    def test_owner_required(self) -> None:
        for kwargs in ({}, {"owner": ""}):
            with self.assertRaises(errors_mod.BadArgument) as ctx:
                self.make(**kwargs)
            self.assertIn("владельца", str(ctx.exception))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    # --- п. 16
    def test_unknown_owner_refused(self) -> None:
        for kwargs in ({"as_owner": "carol"}, {"owner": "carol", "as_owner": "ann"}):
            with self.assertRaises(errors_mod.BadArgument) as ctx:
                self.make(**kwargs)
            self.assertIn("carol", str(ctx.exception))
        self.assertEqual(self.conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)


class ServerModeUpdateCase(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()
        self.task = self.make(as_owner="ann")
        self.tid = self.task["id"]

    # --- п. 17
    def test_set_owner(self) -> None:
        self.assertEqual(store.update_task(self.conn, self.tid, owner="bob")["owner"], "bob")
        self.assertIsNone(store.update_task(self.conn, self.tid, owner="")["owner"])
        self.assertIsNone(self.raw_owner(self.tid))
        store.update_task(self.conn, self.tid, owner="ann")
        with self.assertRaises(errors_mod.BadArgument):
            store.update_task(self.conn, self.tid, owner="carol")
        self.assertEqual(self.raw_owner(self.tid), "ann")

    # --- п. 18
    def test_foreign_edit_refused(self) -> None:
        before = self.snapshot(self.tid)
        with self.assertRaises(errors_mod.Forbidden) as ctx:
            store.update_task(self.conn, self.tid, as_owner="bob", title="x")
        self.assertIn("ann", str(ctx.exception))
        self.assertEqual(self.snapshot(self.tid), before)

    # --- п. 19
    def test_owner_only_edit_allowed(self) -> None:
        out = store.update_task(self.conn, self.tid, as_owner="bob", owner="bob")
        self.assertEqual(out["owner"], "bob")
        store.update_task(self.conn, self.tid, owner="ann")
        self.assertEqual(store.update_task(self.conn, self.tid, title="x")["title"], "x")
        self.assertEqual(
            store.update_task(self.conn, self.tid, as_owner="ann", title="y")["title"], "y")

    # --- п. 20
    def test_ownerless_task_editable(self) -> None:
        store.update_task(self.conn, self.tid, owner="")
        out = store.update_task(self.conn, self.tid, as_owner="bob", title="x")
        self.assertEqual(out["title"], "x")


class ServerModeListCase(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()
        self.ann = self.make("ann-задача", as_owner="ann")["id"]
        self.bob = self.make("bob-задача", as_owner="bob")["id"]
        self.free = self.make("общая", as_owner="ann")["id"]
        store.update_task(self.conn, self.free, owner="")

    # --- п. 21
    def test_list_filter(self) -> None:
        out = store.list_tasks(self.conn, as_owner="ann")
        self.assertEqual(out["total"], 2)
        ids = {t["id"] for t in out["tasks"]}
        self.assertNotIn(self.bob, ids)
        self.assertEqual(store.list_tasks(self.conn)["total"], 3)
        owners = {t["id"]: t["owner"] for t in store.list_tasks(self.conn)["tasks"]}
        self.assertEqual(owners[self.ann], "ann")
        self.assertIsNone(owners[self.free])

    # --- п. 22
    def test_board_filter(self) -> None:
        board = store.board(self.conn, as_owner="ann")
        tasks = [t for col in board["columns"] for t in col["tasks"]]
        self.assertEqual(len(tasks), 2)
        self.assertNotIn(self.bob, {t["id"] for t in tasks})
        self.assertIn("owner", tasks[0])
        self.assertNotIn(self.bob, {t["id"] for t in board["ready"]})

    # --- п. 23
    def test_ready_filter(self) -> None:
        ids = {t["id"] for t in deps_mod.ready_tasks(self.conn, as_owner="ann")}
        self.assertNotIn(self.bob, ids)
        self.assertIn(self.free, ids)
        self.assertIn(self.ann, ids)

    # --- п. 24
    def test_unknown_owner_refused(self) -> None:
        with self.assertRaises(errors_mod.BadArgument):
            store.list_tasks(self.conn, as_owner="carol")
        with self.assertRaises(errors_mod.BadArgument):
            store.board(self.conn, as_owner="carol")
        with self.assertRaises(errors_mod.BadArgument):
            deps_mod.ready_tasks(self.conn, as_owner="carol")


class ServerModeTakeCase(OwnerStoreCase):
    def setUp(self) -> None:
        super().setUp()
        self.server_mode()
        self.tid = self.make(as_owner="ann")["id"]

    # --- п. 25
    def test_claim_foreign(self) -> None:
        before = self.snapshot(self.tid)
        with self.assertRaises(errors_mod.Forbidden) as ctx:
            store.claim(self.conn, self.tid, holder="agent:dsh", as_owner="bob")
        self.assertIn("ann", str(ctx.exception))
        self.assertEqual(self.snapshot(self.tid), before)
        self.assertIsNone(self.conn.execute(
            "SELECT holder FROM tasks WHERE id = ?", (self.tid,)).fetchone()[0])

    # --- п. 26
    def test_claim_foreign_beats_holder_check(self) -> None:
        self.conn.execute("UPDATE tasks SET holder = 'agent:codex' WHERE id = ?", (self.tid,))
        self.conn.commit()
        with self.assertRaises(errors_mod.Forbidden) as ctx:
            store.claim(self.conn, self.tid, holder="agent:dsh", as_owner="bob")
        self.assertIn("ann", str(ctx.exception))
        self.assertNotIn("удерж", str(ctx.exception))

    # --- п. 27
    def test_force_does_not_bypass(self) -> None:
        with self.assertRaises(errors_mod.Forbidden):
            store.claim(self.conn, self.tid, holder="agent:dsh", as_owner="bob", force=True)

    # --- п. 28
    def test_claim_own(self) -> None:
        out = store.claim(self.conn, self.tid, holder="agent:dsh", as_owner="ann")
        self.assertEqual(out["holder"], "agent:dsh")
        self.assertEqual(out["owner"], "ann")

    # --- п. 29
    def test_ownerless_task_taken_by_anyone(self) -> None:
        free = self.make("общая", as_owner="ann")["id"]
        store.update_task(self.conn, free, owner="")
        out = store.claim(self.conn, free, holder="agent:dsh", as_owner="bob")
        self.assertIsNone(out["owner"])
        beat = store.heartbeat(self.conn, free, holder="agent:dsh", as_owner="bob")
        self.assertIsNone(beat["owner"])

    # --- п. 30
    def test_claim_without_owner(self) -> None:
        before = self.snapshot(self.tid)
        with self.assertRaises(errors_mod.BadArgument) as ctx:
            store.claim(self.conn, self.tid, holder="agent:dsh")
        self.assertIn("от чьего имени", str(ctx.exception))
        self.assertEqual(self.snapshot(self.tid), before)

    # --- п. 31
    def test_unknown_owner_is_bad_argument(self) -> None:
        before = self.snapshot(self.tid)
        for call in (
            lambda: store.claim(self.conn, self.tid, holder="agent:dsh", as_owner="carol"),
            lambda: store.heartbeat(self.conn, self.tid, holder="agent:dsh", as_owner="carol"),
            lambda: store.next_stage(self.conn, self.tid, holder="agent:codex",
                                     as_owner="carol"),
        ):
            with self.assertRaises(errors_mod.BadArgument):
                call()
        self.assertEqual(self.snapshot(self.tid), before)

    # --- п. 32
    def test_heartbeat_foreign(self) -> None:
        before = self.snapshot(self.tid)
        with self.assertRaises(errors_mod.Forbidden):
            store.heartbeat(self.conn, self.tid, holder="agent:dsh", as_owner="bob")
        self.assertEqual(self.snapshot(self.tid), before)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM events WHERE kind = 'heartbeat'").fetchone()[0], 0)

    # --- п. 33
    def test_next_stage_with_holder_foreign(self) -> None:
        before = self.snapshot(self.tid)
        with self.assertRaises(errors_mod.Forbidden):
            store.next_stage(self.conn, self.tid, holder="agent:codex", as_owner="bob")
        self.assertEqual(self.snapshot(self.tid), before)

    # --- п. 34
    def test_next_stage_without_holder_ignores_owner(self) -> None:
        out = store.next_stage(self.conn, self.tid, as_owner="bob")
        self.assertEqual(out["stage"], "s1-spec")
        out = store.next_stage(self.conn, self.tid, as_owner="carol")
        self.assertEqual(out["stage"], "s2-review")

    # --- п. 35
    def test_next_stage_holder_requires_owner(self) -> None:
        with self.assertRaises(errors_mod.BadArgument) as ctx:
            store.next_stage(self.conn, self.tid, holder="agent:codex")
        self.assertIn("от чьего имени", str(ctx.exception))
        self.assertEqual(store.next_stage(self.conn, self.tid)["stage"], "s1-spec")


class LocalModeCase(OwnerStoreCase):
    # --- п. 36
    def test_create_ignores_owner(self) -> None:
        task = self.make(as_owner="carol", owner="carol")
        self.assertIsNone(task["owner"])
        self.assertIsNone(self.raw_owner(task["id"]))
        self.assertIsNone(self.make("вторая")["owner"])

    # --- п. 37
    def test_take_and_read_ignore_owner(self) -> None:
        tid = self.make()["id"]
        self.conn.execute("UPDATE tasks SET owner = 'ann' WHERE id = ?", (tid,))
        self.conn.commit()
        other = self.make("вторая")["id"]
        self.assertEqual(
            store.claim(self.conn, tid, holder="agent:dsh", as_owner="bob")["holder"],
            "agent:dsh")
        store.heartbeat(self.conn, tid, holder="agent:dsh", as_owner="carol")
        self.assertEqual(store.list_tasks(self.conn, as_owner="ann")["total"], 2)
        board = store.board(self.conn, as_owner="carol")
        self.assertEqual(board["total"], 2)
        self.assertIn(other, {t["id"] for col in board["columns"] for t in col["tasks"]})

    # --- п. 38
    def test_update_ignores_owner(self) -> None:
        tid = self.make()["id"]
        store.update_task(self.conn, tid, owner="ann")
        self.assertIsNone(self.raw_owner(tid))
        self.conn.execute("UPDATE tasks SET owner = 'ann' WHERE id = ?", (tid,))
        self.conn.commit()
        self.assertEqual(
            store.update_task(self.conn, tid, as_owner="bob", title="x")["title"], "x")

    def test_card_has_owner_field(self) -> None:
        self.assertIn("owner", self.make())


if __name__ == "__main__":
    unittest.main()
