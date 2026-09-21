"""Неизвестный `agent:<имя>` — агент, а не человек."""
import sqlite3
import unittest

from listik import actors


class AgentPrefixTests(unittest.TestCase):
    def test_resolve_without_conn(self):
        for raw, expected in [("agent:listik-swarm", ("agent:listik-swarm", "agent")),
                              ("Agent:Foo", ("agent:foo", "agent")),
                              ("agent:pi-deepseek", ("agent:pi-deepseek", "agent")),
                              ("agent:sonnet-judge", ("agent:claude", "agent")),
                              ("alice", ("alice", "human")),
                              (None, (None, "unknown")),
                              ("", (None, "unknown"))]:
            self.assertEqual(actors.resolve(raw), expected, raw)

    def test_bare_prefix_is_not_agent(self):
        self.assertNotEqual(actors.resolve("agent:")[1], "agent")

    def test_same_actor(self):
        self.assertTrue(actors.same_actor("agent:listik-swarm", "Agent:Listik-Swarm"))
        self.assertFalse(actors.same_actor("agent:listik-swarm", "agent:claude"))

    def test_db_alias_wins_over_prefix(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE actor_aliases(raw TEXT PRIMARY KEY, actor TEXT)")
        conn.execute("INSERT INTO actor_aliases VALUES('agent:foo', 'agent:claude')")
        self.assertEqual(actors.resolve("agent:foo", conn), ("agent:claude", "agent"))
        conn.close()


if __name__ == "__main__":
    unittest.main()
