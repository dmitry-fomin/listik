"""Акторы pi: pi-glm и pi-deepseek — свои агенты, не dsh."""
import unittest

from listik import actors, harnesses_store


class PiActorsTests(unittest.TestCase):
    def test_resolve(self):
        for raw, key in [("pi-glm", "agent:pi-glm"), ("agent:pi-glm", "agent:pi-glm"),
                         ("pi-deepseek", "agent:pi-deepseek"), ("agent:pi-deepseek", "agent:pi-deepseek"),
                         ("deepseek", "agent:dsh"), ("dsh", "agent:dsh")]:
            self.assertEqual(actors.resolve(raw), (key, "agent"), raw)

    def test_pi_harnesses_are_in_seed_catalog(self):
        seeds = {item["key"]: item for item in harnesses_store.SEEDS}
        for key in ("pi-glm", "pi-deepseek"):
            self.assertIn(key, seeds)
            self.assertEqual(seeds[key]["kind"], "exec")
            self.assertTrue(seeds[key]["argv"], key)


if __name__ == "__main__":
    unittest.main()
