"""Расклад ролей конвейера v2, справочник скилов-режимов и каталог запускаторов.

Роли и исполнители проверяются через `routes.validate` (путь `routes[0].roles…`) и
напрямую через `routes.validate_pipeline_roles`. Каталог запускаторов — на временном
дереве с подменой `skills.PLUGINS_DIR`: настоящий `plugins/` репозитория как источник
данных для таких тестов не годится — он меняется вместе с плагинами.
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

WRITER = {"harness": "claude", "agent": "listik-pipeline:writer-high", "model": "opus",
          "effort": "high"}
PI_GLM = {"harness": "pi", "channel": "glm", "effort": "high"}
DEVIN = {"harness": "devin", "model": "swe-2", "effort": "max"}
GROK = {"harness": "grok", "model": "grok-4.7", "effort": "high"}


def document(roles) -> dict:
    return {"version": 2, "routes": [
        {"key": "lp-high", "plugin": "listik-pipeline", "skill": "high", "roles": roles}]}


class PipelineRolesTest(unittest.TestCase):
    """Пункты 10 и 12 чек-листа: форма расклада и число исполнителей."""

    def error(self, roles) -> str:
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate(document(roles))
        return str(ctx.exception)

    def roles(self, roles) -> dict:
        return routes_mod.validate(document(roles))[0]["roles"]

    def test_constants(self) -> None:
        self.assertEqual(routes_mod.PIPELINE_ROLE_KEYS,
                         ("writer", "critic", "implementer", "lens", "judge"))
        self.assertEqual(routes_mod.PIPELINE_HARNESSES, ("claude", "pi", "devin", "codex", "grok"))

    def test_roles_not_object(self) -> None:
        for bad in ("x", [], 1, None):
            with self.subTest(roles=bad):
                self.assertEqual(self.error(bad), "routes[0].roles: должен быть объектом с ролями "
                                                  "writer/critic/implementer/lens/judge")

    def test_unknown_role(self) -> None:
        for role in ("spec", "impl", "reviewer"):
            with self.subTest(role=role):
                self.assertEqual(self.error({role: [DEVIN]}),
                                 f"routes[0].roles.{role}: неизвестная роль, допустимы: "
                                 "writer, critic, implementer, lens, judge")

    def test_cell_not_nonempty_array(self) -> None:
        old_cell = {"provider": "claude", "label": "x", "title": "y"}
        for bad in ([], old_cell, None, "devin"):
            with self.subTest(cell=bad):
                self.assertEqual(self.error({"implementer": bad}),
                                 "routes[0].roles.implementer: непустой массив исполнителей")

    def test_one_executor_except_critic(self) -> None:
        for role in ("writer", "implementer", "lens", "judge"):
            with self.subTest(role=role):
                self.assertEqual(self.error({role: [DEVIN, GROK]}),
                                 f"routes[0].roles.{role}: у роли {role} ровно один исполнитель")

    def test_critic_up_to_ten(self) -> None:
        self.assertEqual(len(self.roles({"critic": [dict(PI_GLM)] * 10})["critic"]), 10)
        self.assertEqual(self.error({"critic": [dict(PI_GLM)] * 11}),
                         "routes[0].roles.critic: не больше 10 исполнителей")

    def test_xlow_without_writer(self) -> None:
        roles = {"implementer": [DEVIN], "judge": [GROK]}
        self.assertEqual(self.roles(roles), roles)

    def test_three_critics_of_different_harnesses(self) -> None:
        critics = [{"harness": "claude", "agent": "listik-pipeline:critic-high",
                    "model": "sonnet", "effort": "high"},
                   PI_GLM, {"harness": "codex", "effort": "high"}]
        self.assertEqual(self.roles({"critic": critics})["critic"], critics)

    def test_empty_roles_by_flag(self) -> None:
        self.assertEqual(routes_mod.validate_pipeline_roles({}, "roles", allow_empty=True), {})
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate_pipeline_roles({}, "roles", allow_empty=False)
        self.assertEqual(str(ctx.exception), "roles: нужна хотя бы одна роль")

    def test_role_order_kept(self) -> None:
        roles = {"judge": [GROK], "writer": [WRITER], "implementer": [DEVIN]}
        self.assertEqual(list(self.roles(roles)), ["judge", "writer", "implementer"])

    def test_repo_routes_json_valid(self) -> None:
        data = json.loads((REPO_DIR / "routes.json").read_text(encoding="utf-8"))
        out = routes_mod.validate(data)
        self.assertEqual([r["roles"] for r in out], [r["roles"] for r in data["routes"]])


class ExecutorTest(unittest.TestCase):
    """Пункт 11 чек-листа: исполнитель роли, текст ошибки целиком, путь до поля."""

    def error(self, executor, role: str = "critic", index: int = 1) -> str:
        cell = [dict(PI_GLM)] * index + [executor]
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate(document({role: cell}))
        return str(ctx.exception)

    def where(self, field: str = "", role: str = "critic", index: int = 1) -> str:
        return f"routes[0].roles.{role}[{index}]" + (f".{field}" if field else "")

    def test_not_object(self) -> None:
        for bad in ("claude", None, ["claude"]):
            with self.subTest(executor=bad):
                self.assertEqual(self.error(bad), self.where() + ": исполнитель — объект "
                                 "{harness, agent?, model?, channel?, effort}")

    def test_extra_field(self) -> None:
        self.assertEqual(self.error({**PI_GLM, "label": "x"}), self.where("label") + ": лишнее поле")

    def test_extra_field_goes_first(self) -> None:
        self.assertEqual(self.error({"harness": "claude", "label": "x"}),
                         self.where("label") + ": лишнее поле")

    def test_harness(self) -> None:
        for bad in ("dsh", "Claude", "", 1):
            with self.subTest(harness=bad):
                self.assertEqual(self.error({**PI_GLM, "harness": bad}),
                                 self.where("harness") + ": допустимы claude, pi, devin, codex, grok")
        self.assertEqual(self.error({"channel": "glm", "effort": "high"}),
                         self.where("harness") + ": обязательное поле отсутствует")

    def test_agent_only_for_claude(self) -> None:
        for executor in ({**PI_GLM, "agent": "listik-pipeline:x"},
                         {**DEVIN, "agent": "listik-pipeline:x"}):
            with self.subTest(harness=executor["harness"]):
                self.assertEqual(self.error(executor),
                                 self.where("agent") + ": только у harness=claude")

    def test_foreign_agent_goes_before_missing_channel(self) -> None:
        executor = {"harness": "pi", "agent": "listik-pipeline:x", "effort": "high"}
        self.assertEqual(self.error(executor), self.where("agent") + ": только у harness=claude")

    def test_channel_only_for_pi(self) -> None:
        for executor in ({**WRITER, "channel": "glm"}, {**GROK, "channel": "glm"}):
            with self.subTest(harness=executor["harness"]):
                self.assertEqual(self.error(executor),
                                 self.where("channel") + ": только у harness=pi")

    def test_missing_required(self) -> None:
        cases = (({k: v for k, v in WRITER.items() if k != "agent"}, "agent"),
                 ({k: v for k, v in WRITER.items() if k != "model"}, "model"),
                 ({k: v for k, v in WRITER.items() if k != "effort"}, "effort"),
                 ({"harness": "pi", "effort": "high"}, "channel"),
                 ({"harness": "pi", "channel": "glm"}, "effort"),
                 ({"harness": "devin"}, "effort"))
        for executor, field in cases:
            with self.subTest(executor=executor):
                self.assertEqual(self.error(executor),
                                 self.where(field) + ": обязательное поле отсутствует")

    def test_missing_in_order_agent_model_channel_effort(self) -> None:
        self.assertEqual(self.error({"harness": "claude"}),
                         self.where("agent") + ": обязательное поле отсутствует")
        self.assertEqual(self.error({"harness": "claude", "agent": "x"}),
                         self.where("model") + ": обязательное поле отсутствует")

    def test_missing_goes_before_format(self) -> None:
        self.assertEqual(self.error({"harness": "claude", "agent": "bad", "effort": "high"}),
                         self.where("model") + ": обязательное поле отсутствует")

    def test_agent_format(self) -> None:
        for bad in ("pipeline-core:x", "listik-pipeline:", "listik-pipeline:Writer", "writer", 1):
            with self.subTest(agent=bad):
                self.assertEqual(self.error({**WRITER, "agent": bad}),
                                 self.where("agent") + ": ожидается listik-pipeline:<имя>")

    def test_claude_model(self) -> None:
        for bad in ("gpt", "Opus", "", None):
            with self.subTest(model=bad):
                self.assertEqual(self.error({**WRITER, "model": bad}),
                                 self.where("model") + ": у claude допустимы opus, sonnet, haiku")

    def test_other_model_nonempty_string(self) -> None:
        for bad in ("", "  ", 1, None):
            with self.subTest(model=bad):
                self.assertEqual(self.error({**DEVIN, "model": bad}),
                                 self.where("model") + ": непустая строка")

    def test_channel_and_effort_format(self) -> None:
        self.assertEqual(self.error({**PI_GLM, "channel": "GLM"}),
                         self.where("channel") + ": строка под ^[a-z0-9][a-z0-9-]*$")
        self.assertEqual(self.error({**PI_GLM, "effort": ""}),
                         self.where("effort") + ": строка под ^[a-z0-9][a-z0-9-]*$")

    def test_path_names_role_and_index(self) -> None:
        message = self.error({"harness": "dsh", "effort": "high"}, role="judge", index=0)
        self.assertTrue(message.startswith("routes[0].roles.judge[0].harness:"), message)

    def test_normalized_has_only_given_fields_in_order(self) -> None:
        given = {"effort": "high", "model": "sonnet", "agent": "listik-pipeline:critic-high",
                 "harness": "claude"}
        roles = routes_mod.validate(document({"critic": [given, {"effort": "max",
                                                                 "harness": "codex"}]}))[0]["roles"]
        self.assertEqual(list(roles["critic"][0]), ["harness", "agent", "model", "effort"])
        self.assertEqual(roles["critic"][1], {"harness": "codex", "effort": "max"})
        self.assertEqual(list(roles["critic"][1]), ["harness", "effort"])

    def test_model_optional_outside_claude(self) -> None:
        roles = routes_mod.validate(document({"judge": [{"harness": "grok", "effort": "high"}]}))
        self.assertEqual(roles[0]["roles"]["judge"], [{"harness": "grok", "effort": "high"}])


class PipelineSkillsTest(unittest.TestCase):
    """Пункт 13 чек-листа: справочник скилов-режимов `listik-pipeline`."""

    def test_skill_keys(self) -> None:
        self.assertEqual(skills_mod.skill_keys(),
                         ["lp-devin", "lp-high", "lp-low", "lp-medium", "lp-xhigh", "lp-xlow"])
        self.assertTrue(skills_mod.skills_available())

    def test_skill_of(self) -> None:
        self.assertEqual(skills_mod.skill_of("listik-pipeline", "lp-high"), "high")
        self.assertIsNone(skills_mod.skill_of("pipeline-full", "full-high"))
        self.assertIsNone(skills_mod.skill_of("listik-pipeline", "lp-"))
        self.assertIsNone(skills_mod.skill_of("listik-pipeline", "full-high"))

    def test_skill_ref(self) -> None:
        self.assertEqual(skills_mod.skill_ref("lp-high"), "/listik-pipeline:high")
        self.assertIsNone(skills_mod.skill_ref("full-high"))

    def test_skill_info(self) -> None:
        info = skills_mod.skill_info("lp-high")
        self.assertEqual({k: v for k, v in info.items() if k != "hint"},
                         {"key": "lp-high", "plugin": "listik-pipeline", "skill": "high",
                          "title": "high",
                          "skill_path": "plugins/listik-pipeline/skills/high/SKILL.md"})
        self.assertTrue(info["hint"])
        self.assertEqual(info, skills_mod.skill_info("lp-high", "listik-pipeline"))

    def test_role_skills_are_not_modes(self) -> None:
        for skill in skills_mod.ROLE_SKILLS:
            with self.subTest(skill=skill):
                self.assertIsNone(skills_mod.skill_info(f"lp-{skill}"))
                self.assertNotIn(f"lp-{skill}", skills_mod.skill_keys())
        self.assertIsNone(skills_mod.skill_info("full-high"))
        self.assertIsNone(skills_mod.skill_info("lp-nope"))

    def test_no_plugins_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(skills_mod, "PIPELINE_PLUGINS_DIR", pathlib.Path(tmp)):
            self.assertEqual(skills_mod.skill_keys(), [])
            self.assertFalse(skills_mod.skills_available())
            self.assertIsNone(skills_mod.skill_info("lp-high"))


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
