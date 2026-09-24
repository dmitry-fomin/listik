"""Схема ячейки роли v2 и каталог скилов-запускаторов (listik-syu8, порция a).

Каталог проверяется на временном дереве с подменой `skills.PLUGINS_DIR`: настоящий
`plugins/` репозитория как источник данных для тестов не годится — он меняется вместе
с плагинами.
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from unittest import mock

from listik import routes as routes_mod
from listik import skills as skills_mod

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent


def role(**over) -> dict:
    cell = {"provider": "claude", "label": "medium", "title": "Opus · medium"}
    cell.update(over)
    return cell


def record(roles: dict) -> dict:
    return {"version": 1, "routes": [
        {"key": "demo", "kind": "pipeline", "title": "Демо", "hint": "", "visible": False,
         "roles": roles},
    ]}


class RoleCellSchemaTest(unittest.TestCase):
    def validate(self, roles: dict) -> dict:
        out = routes_mod.validate(record(roles))
        return out[0]["roles"]

    def test_old_cell_unchanged(self):
        """Старая ячейка проходит и не получает ключей skill/params."""
        roles = self.validate({"impl": role()})
        self.assertEqual(roles["impl"], {"provider": "claude", "label": "medium",
                                         "title": "Opus · medium"})
        self.assertNotIn("skill", roles["impl"])
        self.assertNotIn("params", roles["impl"])

    def test_skill_and_params_kept(self):
        roles = self.validate({"impl": role(skill="pi:pi-delegate",
                                           params={"channel": "glm", "thinking": "high"})})
        self.assertEqual(roles["impl"]["skill"], "pi:pi-delegate")
        self.assertEqual(roles["impl"]["params"], {"channel": "glm", "thinking": "high"})

    def test_param_scalar_types_kept(self):
        roles = self.validate({"impl": role(skill="pi:pi-delegate",
                                            params={"a": True, "b": 1.5, "c": "", "d": 7})})
        params = roles["impl"]["params"]
        self.assertIs(params["a"], True)
        self.assertEqual(params["b"], 1.5)
        self.assertEqual(params["c"], "")
        self.assertEqual(params["d"], 7)
        # значения не подменяются типом и после сериализации в файл
        self.assertEqual(json.loads(json.dumps(params)), params)

    def test_empty_params_ok(self):
        roles = self.validate({"impl": role(skill="pi:pi-delegate", params={})})
        self.assertEqual(roles["impl"]["params"], {})

    def test_skill_format_rejected(self):
        for bad in ("", " pi:pi-delegate", "x pi:pi-delegate", "a:b:c", "Pi:Delegate",
                    "pi:", ":delegate", "pi", 5):
            with self.subTest(bad=bad):
                with self.assertRaises(routes_mod.RoutesError) as ctx:
                    self.validate({"impl": role(skill=bad)})
                self.assertIn("roles.impl.skill", str(ctx.exception))

    def test_param_values_rejected(self):
        for bad in (None, [1], {"x": 1}):
            with self.subTest(bad=bad):
                with self.assertRaises(routes_mod.RoutesError) as ctx:
                    self.validate({"impl": role(skill="pi:pi-delegate", params={"a": bad})})
                self.assertIn("roles.impl.params.a", str(ctx.exception))

    def test_param_keys_rejected(self):
        for bad in ("Channel", "1a", "", "с_каналом"):
            with self.subTest(bad=bad):
                with self.assertRaises(routes_mod.RoutesError) as ctx:
                    self.validate({"impl": role(skill="pi:pi-delegate", params={bad: "x"})})
                self.assertIn("roles.impl.params", str(ctx.exception))

    def test_params_without_skill(self):
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            self.validate({"impl": role(params={"channel": "glm"})})
        self.assertIn("roles.impl.params", str(ctx.exception))
        self.assertIn("skill", str(ctx.exception))

    def test_params_too_many(self):
        params = {f"p{i}": i for i in range(routes_mod.PARAMS_MAX_KEYS + 1)}
        with self.assertRaises(routes_mod.RoutesError):
            self.validate({"impl": role(skill="pi:pi-delegate", params=params)})

    def test_required_fields_still_required(self):
        for missing in ("provider", "label", "title"):
            cell = role(skill="pi:pi-delegate")
            cell.pop(missing)
            with self.subTest(missing=missing):
                with self.assertRaises(routes_mod.RoutesError):
                    self.validate({"impl": cell})

    def test_no_filesystem_access(self):
        """Проверка схемы не смотрит на диск: каталога plugins/ может не быть вовсе."""
        with mock.patch.object(skills_mod, "PLUGINS_DIR", pathlib.Path("/нет/такого")):
            roles = self.validate({"impl": role(skill="no-such:launcher")})
        self.assertEqual(roles["impl"]["skill"], "no-such:launcher")

    def test_repo_routes_json_still_valid(self):
        """Реальный routes.json репозитория читается той же проверкой."""
        data = json.loads((REPO_DIR / "routes.json").read_text(encoding="utf-8"))
        out = routes_mod.validate(data)
        self.assertTrue(out)


class LauncherCatalogueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        patch = mock.patch.object(skills_mod, "PLUGINS_DIR", self.root / "plugins")
        patch.start()
        self.addCleanup(patch.stop)
        patch_root = mock.patch.object(skills_mod.paths, "ROOT_DIR", self.root)
        patch_root.start()
        self.addCleanup(patch_root.stop)
        patch_ext = mock.patch.object(skills_mod, "EXTERNAL_LAUNCHERS", {})
        patch_ext.start()
        self.addCleanup(patch_ext.stop)

    def write_skill(self, plugin: str, skill: str, front: str = "") -> None:
        path = self.root / "plugins" / plugin / "skills" / skill
        path.mkdir(parents=True, exist_ok=True)
        (path / "SKILL.md").write_text(front, encoding="utf-8")

    def test_keys_only_launchers(self):
        self.write_skill("pi", "pi-delegate", "---\nname: pi-delegate\n---\n")
        self.write_skill("grok", "delegate", "---\nname: delegate\n---\n")
        self.write_skill("pi", "pi-check", "---\nname: pi-check\n---\n")  # не запускатор
        (self.root / "plugins" / "dsh" / "skills" / "dsh-delegate").mkdir(parents=True)
        self.assertEqual(skills_mod.launcher_keys(), ["grok:delegate", "pi:pi-delegate"])
        self.assertTrue(skills_mod.launchers_available())

    def test_no_plugins_dir(self):
        self.assertEqual(skills_mod.launcher_keys(), [])
        self.assertEqual(skills_mod.launchers(), [])
        self.assertFalse(skills_mod.launchers_available())

    def test_launchers_matches_infos(self):
        self.write_skill("pi", "pi-delegate", "---\nname: pi-delegate\n---\n")
        self.write_skill("grok", "delegate", "---\nname: delegate\n---\n")
        expected = [skills_mod.launcher_info(k) for k in skills_mod.launcher_keys()]
        self.assertEqual(skills_mod.launchers(), expected)
        self.assertTrue(all(isinstance(item, dict) for item in skills_mod.launchers()))

    def test_info_fields(self):
        desc = "Отдать задачу pi. " + "х" * 300
        self.write_skill("pi", "pi-delegate", f"---\nname: pi-delegate\ndescription: {desc}\n---\n")
        info = skills_mod.launcher_info("pi:pi-delegate")
        self.assertEqual(info["key"], "pi:pi-delegate")
        self.assertEqual(info["plugin"], "pi")
        self.assertEqual(info["title"], "pi-delegate")
        self.assertEqual(info["hint"], "Отдать задачу pi.")
        self.assertLessEqual(len(info["hint"]), skills_mod.HINT_MAX_CHARS)
        self.assertEqual(info["provider"], "glm")
        self.assertEqual(info["skill_path"], "plugins/pi/skills/pi-delegate/SKILL.md")

    def test_provider_by_plugin(self):
        self.write_skill("grok", "delegate", "---\nname: delegate\n---\n")
        self.write_skill("strange", "delegate", "---\nname: delegate\n---\n")
        self.assertEqual(skills_mod.launcher_info("grok:delegate")["provider"], "grok")
        self.assertIsNone(skills_mod.launcher_info("strange:delegate")["provider"])

    def test_provider_devin(self):
        self.write_skill("devin", "devin-delegate", "---\nname: devin-delegate\n---\n")
        self.assertEqual(skills_mod.launcher_info("devin:devin-delegate")["provider"], "devin")

    def test_info_fallbacks(self):
        self.write_skill("pi", "pi-delegate", "нет frontmatter вовсе\n")
        info = skills_mod.launcher_info("pi:pi-delegate")
        self.assertEqual(info["title"], "pi-delegate")
        self.assertEqual(info["hint"], "")

    def test_info_none(self):
        for bad in ("нет:такого", "мусор", "a:b:c", "", 7, None):
            with self.subTest(bad=bad):
                self.assertIsNone(skills_mod.launcher_info(bad))


class ExternalLauncherTest(unittest.TestCase):
    def test_grok_delegate_in_catalogue(self):
        """Плагин grok стоит вне репозитория, но ссылаться на него можно."""
        self.assertIn("grok:delegate", skills_mod.launcher_keys())
        info = skills_mod.launcher_info("grok:delegate")
        self.assertEqual(info["provider"], "grok")
        self.assertIsNone(info["skill_path"])
        self.assertIn(info, skills_mod.launchers())


if __name__ == "__main__":
    unittest.main()
