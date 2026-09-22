"""Каталог харнессов (listik-2gry): сиды, CRUD, проверки, `used_by`, алиасы акторов."""
from __future__ import annotations

import unittest

from listik import errors
from listik import harnesses_store
from listik import routes_store
from tests.helpers import TempDbTestCase


class SeedTests(TempDbTestCase):
    def test_seeds_present_after_init(self) -> None:
        keys = {h["key"] for h in harnesses_store.list_harnesses(self.conn)}
        for key in ("claude", "dsh", "codex", "grok", "pi-glm",
                    "pi-deepseek", "devin", "me"):
            self.assertIn(key, keys)
        self.assertNotIn("gemini", keys)
        # И актора agent:gemini в реестре нет — он не возвращается seed_actors.
        self.assertIsNone(self.conn.execute(
            "SELECT key FROM actors WHERE key = 'agent:gemini'").fetchone())
        me = harnesses_store.get(self.conn, "me")
        self.assertEqual(me["kind"], "manual")
        self.assertIsNone(me["argv"])
        self.assertTrue(me["builtin"])

    def test_seed_is_idempotent_and_keeps_edits(self) -> None:
        harnesses_store.update(self.conn, "claude", {"hint": "своя подпись"})
        harnesses_store.seed(self.conn)
        self.assertEqual(harnesses_store.get(self.conn, "claude")["hint"],
                         "своя подпись")

    def test_seed_registers_actor_alias(self) -> None:
        row = self.conn.execute(
            "SELECT actor FROM actor_aliases WHERE raw = 'codex'").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["actor"], "agent:codex")

    def test_seed_drops_builtin_gemini_once(self) -> None:
        # Старая база: поставочный gemini ещё есть, флага фикса — нет.
        self.conn.execute(
            "INSERT INTO harnesses(key, label, kind, builtin, enabled, position, "
            "created_at, updated_at) VALUES('gemini', 'gemini', 'exec', 1, 1, 99, "
            "'2020', '2020')")
        self.conn.execute(
            "INSERT INTO actors(key, title, kind, kind_hint) "
            "VALUES('agent:gemini', 'gemini', 'agent', 'gemini')")
        self.conn.execute(
            "INSERT INTO actor_aliases(raw, actor) VALUES('gemini', 'agent:gemini')")
        self.conn.execute(
            "DELETE FROM meta WHERE key = 'seed_drop_gemini'")
        self.conn.commit()
        harnesses_store.seed(self.conn)
        self.assertIsNone(harnesses_store.by_key(self.conn, "gemini"))
        self.assertIsNone(self.conn.execute(
            "SELECT key FROM actors WHERE key = 'agent:gemini'").fetchone())
        # Повторный seed не трогает gemini, заведённого руками, — ни запись,
        # ни её алиас держателя.
        harnesses_store.create(self.conn, {"key": "gemini", "argv": ["gemini"]})
        harnesses_store.seed(self.conn)
        self.assertIsNotNone(harnesses_store.by_key(self.conn, "gemini"))
        row = self.conn.execute(
            "SELECT actor FROM actor_aliases WHERE raw = 'gemini'").fetchone()
        self.assertEqual(row["actor"], "agent:gemini")


class CrudTests(TempDbTestCase):
    def test_create_and_get(self) -> None:
        record = harnesses_store.create(self.conn, {
            "key": "mini", "label": "mini", "hint": "локальный",
            "argv": ["python3", "-m", "mini"], "prompt": "задача {task_id}"})
        self.assertEqual(record["key"], "mini")
        self.assertEqual(record["argv"], ["python3", "-m", "mini"])
        self.assertEqual(record["prompt"], "задача {task_id}")
        self.assertFalse(record["builtin"])
        self.assertTrue(record["enabled"])
        # Алиас agent:mini появился — claim под таким держателем засчитается.
        row = self.conn.execute(
            "SELECT actor FROM actor_aliases WHERE raw = 'mini'").fetchone()
        self.assertEqual(row["actor"], "agent:mini")

    def test_create_conflict_and_bad_key(self) -> None:
        with self.assertRaises(errors.ListikError) as ctx:
            harnesses_store.create(self.conn, {"key": "claude"})
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        for key in ("Mini", "-x", "x y", "a_b"):
            with self.subTest(key=key):
                with self.assertRaises(errors.BadArgument):
                    harnesses_store.create(self.conn, {"key": key})

    def test_create_manual_rejects_command(self) -> None:
        with self.assertRaises(ValueError):
            harnesses_store.create(self.conn, {"key": "hands", "kind": "manual",
                                               "argv": ["x"]})
        record = harnesses_store.create(self.conn, {"key": "hands",
                                                    "kind": "manual"})
        self.assertEqual(record["kind"], "manual")
        self.assertIsNone(record["argv"])

    def test_create_bad_argv_and_prompt(self) -> None:
        with self.assertRaises(ValueError):
            harnesses_store.create(self.conn, {"key": "bad", "argv": "claude -p"})
        with self.assertRaises(ValueError):
            harnesses_store.create(self.conn, {"key": "bad",
                                               "argv": ["x", "{nope}"]})
        with self.assertRaises(ValueError):
            harnesses_store.create(self.conn, {"key": "bad", "prompt": 42})
        with self.assertRaises(errors.BadArgument):
            harnesses_store.create(self.conn, {"key": "bad", "builtin": 1})

    def test_update_fields_and_guards(self) -> None:
        record = harnesses_store.update(self.conn, "codex",
                                        {"hint": "новая", "enabled": False})
        self.assertEqual(record["hint"], "новая")
        self.assertFalse(record["enabled"])
        for field in ("key", "kind", "builtin"):
            with self.subTest(field=field):
                with self.assertRaises(errors.BadArgument):
                    harnesses_store.update(self.conn, "codex", {field: "x"})
        with self.assertRaises(errors.BadArgument):
            harnesses_store.update(self.conn, "codex", {})
        with self.assertRaises(errors.NotFound):
            harnesses_store.update(self.conn, "nope", {"hint": "x"})
        # Manual не получает команду даже правкой.
        with self.assertRaises(ValueError):
            harnesses_store.update(self.conn, "me", {"argv": ["x"]})


class UsedByTests(TempDbTestCase):
    def test_used_by_direct_and_swarm(self) -> None:
        routes_store.create_route(
            self.conn, key="d-mini", kind="direct", title="d",
            harness="mini", command=["mini", "run"])
        harnesses_store.create(self.conn, {"key": "mini", "argv": ["mini", "run"]})
        routes_store.create_route(
            self.conn, key="roy", kind="swarm", title="рой",
            roles={"impl": {"harness": "mini"},
                   "judge": {"harness": "claude"}})
        used = harnesses_store.used_by(self.conn, "mini")
        self.assertIn({"route": "d-mini", "kind": "direct", "role": None}, used)
        self.assertIn({"route": "roy", "kind": "swarm", "role": "impl"}, used)
        # Чужое упоминание в roles не считается: ключа "mini2" нет в раскладе.
        self.assertEqual(harnesses_store.used_by(self.conn, "grok"), [])


if __name__ == "__main__":
    unittest.main()
