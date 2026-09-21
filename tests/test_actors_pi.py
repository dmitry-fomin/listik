"""Акторы pi: pi-glm и pi-deepseek — свои агенты, не dsh."""
import unittest

from listik import actors, config


class PiActorsTests(unittest.TestCase):
    def test_resolve(self):
        for raw, key in [("pi-glm", "agent:pi-glm"), ("agent:pi-glm", "agent:pi-glm"),
                         ("pi-deepseek", "agent:pi-deepseek"), ("agent:pi-deepseek", "agent:pi-deepseek"),
                         ("deepseek", "agent:dsh"), ("dsh", "agent:dsh")]:
            self.assertEqual(actors.resolve(raw), (key, "agent"), raw)

    def test_default_routing_allows_pi(self):
        for stage, harnesses in config.DEFAULTS["routing"]["harnesses"].items():
            self.assertIn("pi-glm", harnesses, stage)
            self.assertIn("pi-deepseek", harnesses, stage)


if __name__ == "__main__":
    unittest.main()
