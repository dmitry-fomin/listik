"""Каналы харнессов (listik-46q6, порция a): колонка, сиды pi/opencode, разовая
миграция `seed_bridge_channels`, проверка формата, alembic и CLI `listik harnesses`."""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from listik import actors as actors_mod
from listik import db as db_mod
from listik import errors
from listik import harnesses_store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"

PI_CHANNELS = [
    {"name": "glm", "model": "glm-opendesign/glm-5.3-flash", "effort": None, "default": True},
    {"name": "deepseek", "model": "deepseek-opendesign/deepseek-v4.1-flash", "effort": None, "default": False},
]
OPENCODE_CHANNELS = [
    {"name": "glm", "model": "b.ai/glm-5.3-flash", "effort": None, "default": True},
    {"name": "deepseek", "model": "b.ai/deepseek-v4.1-flash", "effort": None, "default": False},
]


def _drop_flag(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM meta WHERE key = 'seed_bridge_channels'")


class SeedTests(TempDbTestCase):
    def test_pi_seed(self) -> None:
        pi = harnesses_store.get(self.conn, "pi")
        self.assertEqual(pi["kind"], "exec")
        self.assertIsNone(pi["argv"])
        self.assertFalse(pi["enabled"])
        self.assertTrue(pi["builtin"])
        self.assertEqual(pi["channels"], PI_CHANNELS)

    def test_opencode_seed(self) -> None:
        oc = harnesses_store.get(self.conn, "opencode")
        self.assertFalse(oc["enabled"])
        self.assertEqual(oc["channels"], OPENCODE_CHANNELS)

    def test_no_actors_and_positions_after_me(self) -> None:
        for key in ("agent:pi", "agent:opencode"):
            self.assertIsNone(self.conn.execute(
                "SELECT key FROM actors WHERE key = ?", (key,)).fetchone())
        for raw in ("pi", "opencode"):
            self.assertNotEqual(actors_mod.resolve(raw, self.conn)[1], "agent", raw)
        me = harnesses_store.get(self.conn, "me")["position"]
        self.assertGreater(harnesses_store.get(self.conn, "pi")["position"], me)
        self.assertGreater(harnesses_store.get(self.conn, "opencode")["position"], me)

    def test_old_seeds_unchanged(self) -> None:
        for key in ("claude", "pi-glm", "pi-deepseek"):
            record = harnesses_store.get(self.conn, key)
            self.assertTrue(record["enabled"])
            self.assertEqual(record["channels"], [])
            seed = next(s for s in harnesses_store.SEEDS if s["key"] == key)
            self.assertEqual(record["argv"], seed["argv"])

    def test_fresh_column(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(harnesses)")}
        self.assertIn("channels", cols)
        self.assertEqual(db_mod.SCHEMA_VERSION, 17)


class OldDatabaseTests(unittest.TestCase):
    def test_schema16_gets_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "old.db"
            lines = [line for line in db_mod.SCHEMA.splitlines()
                     if not line.strip().startswith("channels ")]
            raw = sqlite3.connect(path)
            raw.executescript("\n".join(lines))
            self.assertNotIn("channels", {r[1] for r in raw.execute(
                "PRAGMA table_info(harnesses)")})
            raw.execute(
                "INSERT INTO harnesses(key, label, argv, kind, builtin, enabled, position) "
                "VALUES('claude', 'мой claude', '[\"claude\", \"-p\"]', 'exec', 1, 0, 0)")
            raw.execute("INSERT INTO meta(key, value) VALUES('schema_version', '16')")
            raw.commit()
            raw.close()
            conn = db_mod.init(path)
            try:
                cols = {r["name"] for r in conn.execute("PRAGMA table_info(harnesses)")}
                self.assertIn("channels", cols)
                claude = harnesses_store.get(conn, "claude")
                self.assertEqual(claude["argv"], ["claude", "-p"])
                self.assertEqual(claude["label"], "мой claude")
                self.assertFalse(claude["enabled"])
                self.assertEqual(conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0], "17")
            finally:
                conn.close()


class BridgeMigrationTests(TempDbTestCase):
    def test_human_pi_without_channels_gets_seed(self) -> None:
        self.conn.execute("DELETE FROM harnesses WHERE key = 'pi'")
        self.conn.execute(
            "INSERT INTO harnesses(key, label, kind, builtin, enabled, position) "
            "VALUES('pi', 'мой pi', 'exec', 0, 1, 50)")
        _drop_flag(self.conn)
        harnesses_store.seed(self.conn)
        pi = harnesses_store.get(self.conn, "pi")
        self.assertEqual(pi["channels"], PI_CHANNELS)
        self.assertEqual(pi["label"], "мой pi")

    def test_manual_opencode_untouched(self) -> None:
        self.conn.execute("DELETE FROM harnesses WHERE key = 'opencode'")
        self.conn.execute(
            "INSERT INTO harnesses(key, label, kind, builtin, enabled, position) "
            "VALUES('opencode', 'opencode', 'manual', 0, 1, 50)")
        _drop_flag(self.conn)
        harnesses_store.seed(self.conn)
        self.assertEqual(harnesses_store.get(self.conn, "opencode")["channels"], [])

    def test_cleared_channels_stay_cleared(self) -> None:
        harnesses_store.update(self.conn, "pi", {"channels": []})
        self.assertIsNone(self.conn.execute(
            "SELECT channels FROM harnesses WHERE key = 'pi'").fetchone()[0])
        harnesses_store.seed(self.conn)
        self.assertEqual(harnesses_store.get(self.conn, "pi")["channels"], [])

    def test_model_edit_survives_seed(self) -> None:
        harnesses_store.update(self.conn, "pi", {"channels": [
            {"name": "glm", "model": "x/y", "default": True},
            {"name": "deepseek", "model": "deepseek/deepseek-flash"}]})
        harnesses_store.seed(self.conn)
        self.assertEqual(harnesses_store.get(self.conn, "pi")["channels"][0]["model"], "x/y")


class FormatTests(TempDbTestCase):
    def test_normalization(self) -> None:
        record = harnesses_store.update(self.conn, "pi", {"channels": [
            {"name": "a", "model": "p/m"}, {"name": "b", "model": "q/n", "effort": "high"}]})
        self.assertEqual(record["channels"], [
            {"name": "a", "model": "p/m", "effort": None, "default": True},
            {"name": "b", "model": "q/n", "effort": "high", "default": False}])
        for channel in record["channels"]:
            self.assertEqual(list(channel), ["name", "model", "effort", "default"])

    def test_patch_replaces_list(self) -> None:
        record = harnesses_store.update(self.conn, "pi", {"channels": [
            {"name": "deepseek", "model": "deepseek/deepseek-flash"}]})
        self.assertEqual([c["name"] for c in record["channels"]], ["deepseek"])
        self.assertTrue(record["channels"][0]["default"])
        self.assertEqual(harnesses_store.update(
            self.conn, "pi", {"channels": None})["channels"], [])

    def test_rejections_with_path(self) -> None:
        ok = {"name": "a", "model": "p/m"}
        cases = [
            ([{**ok, "default": True}, {"name": "b", "model": "p/m", "default": True}],
             "channels:"),
            ([ok, dict(ok)], "channels[1].name"),
            ([{"name": "a", "model": "p m"}], "channels[0].model"),
            ([{"name": "a", "model": ""}], "channels[0].model"),
            ([{"name": "a", "model": "p/" + "m" * 199}], "channels[0].model"),
            ([{"name": "a", "model": "p/m\n"}], "channels[0].model"),
            ([{"name": "Glm", "model": "p/m"}], "channels[0].name"),
            ([{"name": "g_m", "model": "p/m"}], "channels[0].name"),
            ([{"name": "glm\n", "model": "p/m"}], "channels[0].name"),
            ([{**ok, "effort": "High"}], "channels[0].effort"),
            ([{**ok, "effort": 1}], "channels[0].effort"),
            ([{**ok, "effort": "high\n"}], "channels[0].effort"),
            ([{**ok, "default": "yes"}], "channels[0].default"),
            ([{**ok, "x": 1}], "channels[0]"),
            (["glm"], "channels[0]"),
            ("glm", "channels:"),
            ([{"name": f"c{i}", "model": "p/m"} for i in range(21)], "channels:"),
        ]
        before = harnesses_store.get(self.conn, "pi")
        for value, prefix in cases:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as ctx:
                    harnesses_store.update(self.conn, "pi", {"channels": value})
                self.assertTrue(errors.message_of(ctx.exception).startswith(prefix),
                                errors.message_of(ctx.exception))
                self.assertEqual(harnesses_store.get(self.conn, "pi"), before)
        with self.assertRaises(ValueError) as ctx:
            harnesses_store.update(self.conn, "pi", {"channels": [{**ok, "x": 1}]})
        self.assertIn("x", errors.message_of(ctx.exception))
        self.assertEqual(len(harnesses_store.update(self.conn, "pi", {"channels": [
            {"name": f"c{i}", "model": "p/m"} for i in range(20)]})["channels"]), 20)

    def test_manual_has_no_channels(self) -> None:
        channels = [{"name": "a", "model": "p/m"}]
        with self.assertRaises(ValueError) as ctx:
            harnesses_store.create(self.conn, {"key": "hand", "kind": "manual",
                                               "channels": channels})
        self.assertTrue(errors.message_of(ctx.exception).startswith("channels"))
        self.assertIsNone(harnesses_store.by_key(self.conn, "hand"))
        with self.assertRaises(ValueError):
            harnesses_store.update(self.conn, "me", {"channels": channels})
        self.assertEqual(harnesses_store.get(self.conn, "me")["channels"], [])
        # Пустой список у manual допустим.
        self.assertEqual(harnesses_store.update(self.conn, "me", {"channels": []})["channels"], [])

    def test_create_with_channels(self) -> None:
        record = harnesses_store.create(self.conn, {
            "key": "mini", "argv": ["mini"], "channels": [{"name": "a", "model": "m"}]})
        self.assertEqual(record["channels"],
                         [{"name": "a", "model": "m", "effort": None, "default": True}])


@unittest.skipIf(importlib.util.find_spec("alembic") is None, "alembic не установлен")
class AlembicTests(unittest.TestCase):
    def test_upgrade_head_matches_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a_db = pathlib.Path(tmp) / "a.db"
            env = {**os.environ, "LISTIK_DB": str(a_db)}
            for _ in range(2):  # повторный upgrade head — без ошибки
                done = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                                      cwd=REPO_DIR, capture_output=True, text=True, env=env)
                self.assertEqual(done.returncode, 0, done.stderr)
            raw = sqlite3.connect(a_db)
            alembic_cols = [r[1] for r in raw.execute("PRAGMA table_info(harnesses)")]
            raw.close()
            conn = db_mod.init(pathlib.Path(tmp) / "i.db")
            init_cols = [r["name"] for r in conn.execute("PRAGMA table_info(harnesses)")]
            conn.close()
        self.assertIn("channels", alembic_cols)
        self.assertEqual(sorted(alembic_cols), sorted(init_cols))
        text = (REPO_DIR / "alembic" / "versions" / "0015_harness_channels.py").read_text()
        self.assertIn('down_revision = "0014_pipeline_plugins"', text)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.env = {**os.environ, "LISTIK_HOME": self._tmp.name}
        for name in ("LISTIK_DB", "LISTIK_LOG", "LISTIK_CONFIG"):
            self.env.pop(name, None)

    def run_cli(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=self.env, cwd=self._tmp.name)

    def test_one_record_json(self) -> None:
        proc = self.run_cli("harnesses", "pi", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        data = json.loads(proc.stdout)
        self.assertEqual(data["key"], "pi")
        self.assertEqual(data["channels"], PI_CHANNELS)
        self.assertEqual(data["used_by"], [])

    def test_list_json(self) -> None:
        proc = self.run_cli("harnesses", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        keys = {h["key"] for h in data["harnesses"]}
        self.assertTrue({"pi", "opencode"} <= keys)
        for record in data["harnesses"]:
            self.assertIsInstance(record["used_by"], list)

    def test_list_text(self) -> None:
        proc = self.run_cli("harnesses")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = next(l for l in proc.stdout.splitlines() if l.startswith("pi "))
        self.assertIn("glm*=glm-opendesign/glm-5.3-flash", line)
        self.assertIn("deepseek=deepseek-opendesign/deepseek-v4.1-flash", line)

    def test_unknown_key(self) -> None:
        proc = self.run_cli("harnesses", "nope", "--json")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("not_found", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
