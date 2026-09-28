"""actors.resolve: явные алиасы раньше подсказок, подсказки — целыми токенами (listik-iuid)."""
import sqlite3
import unittest
from unittest.mock import patch

from listik import actors


def _conn(test: unittest.TestCase, with_table: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    test.addCleanup(conn.close)
    conn.row_factory = sqlite3.Row
    if with_table:
        conn.execute("CREATE TABLE actor_aliases(raw TEXT PRIMARY KEY, actor TEXT)")
    return conn


class ResolveTokensTest(unittest.TestCase):
    CASES = {
        "Podshivalov": ("podshivalov", "human"),
        "Claudia": ("claudia", "human"),
        "dsh.": ("dsh.", "human"),
        "sonnet-judge": ("agent:claude", "agent"),
        "agent:sonnet-judge": ("agent:claude", "agent"),
        "dsh/deepseek-flash": ("agent:dsh", "agent"),
        "claude": ("agent:claude", "agent"),
        "agent:claude": ("agent:claude", "agent"),
        "claude opus": ("agent:claude", "agent"),
        "pi-glm": ("agent:pi-glm", "agent"),
        "agent:pi-glm": ("agent:pi-glm", "agent"),
        "pi-deepseek": ("agent:pi-deepseek", "agent"),
        "agent:pi-deepseek": ("agent:pi-deepseek", "agent"),
        "deepseek": ("agent:dsh", "agent"),
        "dsh": ("agent:dsh", "agent"),
        "agent:listik-swarm": ("agent:listik-swarm", "agent"),
        "agent:": ("agent:", "human"),
    }

    def test_examples(self):
        for raw, want in self.CASES.items():
            with self.subTest(raw=raw):
                self.assertEqual(actors.resolve(raw), want)


class ResolveAliasOrderTest(unittest.TestCase):
    def test_table_alias_beats_hint(self):
        conn = _conn(self)
        conn.execute("INSERT INTO actor_aliases VALUES('sonnet-judge', 'me')")
        self.assertEqual(actors.resolve("sonnet-judge", conn), ("me", "human"))

    def test_table_alias_agent_actor(self):
        conn = _conn(self)
        conn.execute("INSERT INTO actor_aliases VALUES('x', 'agent:foo')")
        self.assertEqual(actors.resolve("x", conn), ("agent:foo", "agent"))

    def test_static_alias_beats_hint(self):
        with patch.dict(actors.ALIASES, {"claude bot": "me"}):
            self.assertEqual(actors.resolve("claude bot"), ("me", "human"))

    def test_static_alias_beats_table(self):
        conn = _conn(self)
        conn.execute("INSERT INTO actor_aliases VALUES('claude bot', 'agent:foo')")
        with patch.dict(actors.ALIASES, {"claude bot": "me"}):
            self.assertEqual(actors.resolve("claude bot", conn), ("me", "human"))


class SameActorTest(unittest.TestCase):
    def test_substring_is_not_agent(self):
        self.assertFalse(actors.same_actor("Podshivalov", "dsh"))
        self.assertFalse(actors.same_actor("Podshivalov", "agent:dsh"))
        self.assertFalse(actors.same_actor("Claudia", "claude"))

    def test_token_hint_is_agent(self):
        self.assertTrue(actors.same_actor("sonnet-judge", "agent:claude"))
        self.assertTrue(actors.same_actor("dsh/deepseek-flash", "agent:dsh"))


class RememberTest(unittest.TestCase):
    def test_writes_alias(self):
        conn = _conn(self)
        actors.remember(conn, " Sonnet-Judge ", "me")
        row = conn.execute("SELECT actor FROM actor_aliases WHERE raw = 'sonnet-judge'").fetchone()
        self.assertEqual(row["actor"], "me")

    def test_missing_table_raises(self):
        with self.assertRaises(sqlite3.OperationalError):
            actors.remember(_conn(self, with_table=False), "x", "me")


if __name__ == "__main__":
    unittest.main()
