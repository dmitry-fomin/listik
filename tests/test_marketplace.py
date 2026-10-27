"""Маркетплейс `.claude-plugin/marketplace.json` и манифесты плагинов не разъезжаются."""
import json
import pathlib
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
MARKETPLACE_JSON = REPO_DIR / ".claude-plugin" / "marketplace.json"
PLUGIN_MANIFEST = pathlib.Path(".claude-plugin") / "plugin.json"


def _read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


class MarketplaceTests(unittest.TestCase):

    def test_marketplace_plugin_sources_match_manifests(self) -> None:
        entries = _read_json(MARKETPLACE_JSON)["plugins"]
        names = [entry.get("name") for entry in entries]
        self.assertEqual(
            names,
            ["listik", "listik-pipeline", "dsh", "codex", "opencode", "pi", "devin",
             "second-opinion", "flow-map"],
            f"состав маркетплейса не тот: {names}",
        )
        for entry in entries:
            name = entry["name"]
            with self.subTest(plugin=name):
                source = entry.get("source")
                self.assertIsInstance(source, str, f"{name}: source не строка")
                plugin_dir = REPO_DIR / source
                self.assertTrue(plugin_dir.is_dir(), f"{name}: {source} не каталог")
                manifest = _read_json(plugin_dir / PLUGIN_MANIFEST)
                self.assertEqual(entry.get("version"), manifest.get("version"),
                                 f"{name}: версия marketplace.json и plugin.json разошлась")
                self.assertEqual(manifest.get("name"), name,
                                 f"{name}: name в plugin.json не совпал")
                description = entry.get("description")
                self.assertIsInstance(description, str, f"{name}: description не строка")
                self.assertTrue(description.strip(), f"{name}: description пустое")
                self.assertNotIn("\n", description, f"{name}: description многострочное")


if __name__ == "__main__":
    unittest.main()
