"""Согласие плагина feature-pipeline с routes.json (шаг 10, порция a).

Скил плагина — это каталог `plugins/feature-pipeline/skills/<имя>` с файлом
`SKILL.md`; каталог без него скилом не считается (поэтому пустой
`skills/codex-pipeline/agents` старого репозитория сюда не переехал). Имена
скилов обязаны совпадать с ключами записей `kind == "pipeline"` в `routes.json`:
доска выбирает конвейер по ключу маршрута, а плагин исполняет его скилом с тем
же именем. Записи `kind == "direct"` — чужие харнессы, скилов у них нет.

Файлы читаются в момент вызова через константы модуля, а не при импорте:
поэтому `unittest.mock.patch.object` на `ROUTES_JSON`, `PLUGIN_DIR` и
`MARKETPLACE_JSON` уводит проверку на копию во временном каталоге.
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
ROUTES_JSON = REPO_DIR / "routes.json"
PLUGIN_DIR = REPO_DIR / "plugins" / "feature-pipeline"
MARKETPLACE_JSON = REPO_DIR / ".claude-plugin" / "marketplace.json"

#: Имя плагина в маркетплейсе; оно же — имя каталога плагина.
PLUGIN_NAME = "feature-pipeline"
SKILLS_SUBDIR = "skills"
SKILL_FILE = "SKILL.md"
PLUGIN_MANIFEST = pathlib.Path(".claude-plugin") / "plugin.json"

#: Строка frontmatter с именем скила: `name: <имя каталога>`.
NAME_LINE_RE = re.compile(r"^name:[ \t]*(?P<name>\S+?)[ \t]*$")


def _read_json(path: pathlib.Path):
    """Прочитать JSON-файл; путь берётся в момент вызова, не при импорте."""
    return json.loads(path.read_text(encoding="utf-8"))


def _routes() -> list[dict]:
    return _read_json(ROUTES_JSON)["routes"]


def _keys_of_kind(kind: str) -> set[str]:
    """Ключи записей routes.json указанного вида."""
    return {record["key"] for record in _routes() if record.get("kind") == kind}


def _skill_names() -> set[str]:
    """Имена скилов плагина: каталоги `skills/<имя>` с файлом `SKILL.md`."""
    skills = PLUGIN_DIR / SKILLS_SUBDIR
    return {path.name for path in skills.iterdir()
            if path.is_dir() and (path / SKILL_FILE).is_file()}


def _skill_text(name: str) -> str:
    return (PLUGIN_DIR / SKILLS_SUBDIR / name / SKILL_FILE).read_text(encoding="utf-8")


def _frontmatter(lines: list[str]) -> list[str] | None:
    """Строки между первой и второй `---`; None, если рамки нет или она не закрыта."""
    if not lines or lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return lines[1:index]
    return None


class FeaturePipelinePluginTests(unittest.TestCase):
    """routes.json, скилы плагина и маркетплейс не разъезжаются."""

    def test_routes_pipelines_match_plugin_skills(self) -> None:
        keys = _keys_of_kind("pipeline")
        skills = _skill_names()
        self.assertTrue(keys, "в routes.json нет ни одной записи kind == pipeline")
        extra = sorted(keys - skills)
        missing = sorted(skills - keys)
        self.assertEqual(
            (extra, missing), ([], []),
            "ключи конвейеров в routes.json и скилы плагина разошлись: "
            f"лишние ключи без скила {extra}, скилы без ключа {missing}",
        )

    def test_skill_frontmatter_name_matches_dir(self) -> None:
        skills = sorted(_skill_names())
        self.assertTrue(skills, "в плагине нет ни одного каталога скила")
        for name in skills:
            with self.subTest(skill=name):
                lines = _skill_text(name).splitlines()
                self.assertTrue(lines and lines[0] == "---",
                                f"{name}/{SKILL_FILE}: первая строка не ---")
                frontmatter = _frontmatter(lines)
                self.assertIsNotNone(frontmatter,
                                     f"{name}/{SKILL_FILE}: frontmatter не закрыт строкой ---")
                found = [match.group("name") for line in frontmatter or []
                         if (match := NAME_LINE_RE.match(line))]
                self.assertEqual(
                    found, [name],
                    f"{name}/{SKILL_FILE}: во frontmatter ожидалась строка name: {name}, "
                    f"а нашлись {found}",
                )

    def test_marketplace_lists_feature_pipeline(self) -> None:
        entries = _read_json(MARKETPLACE_JSON)["plugins"]
        matching = [entry for entry in entries if entry.get("name") == PLUGIN_NAME]
        self.assertEqual(len(matching), 1,
                         f"в marketplace.json не ровно одна запись {PLUGIN_NAME}: {entries}")
        entry = matching[0]
        source = entry.get("source")
        self.assertIsInstance(source, str, f"source записи {PLUGIN_NAME} — не строка: {source}")
        self.assertTrue((REPO_DIR / source).is_dir(),
                        f"источник плагина {source} не каталог в корне репозитория")
        version = _read_json(PLUGIN_DIR / PLUGIN_MANIFEST).get("version")
        self.assertEqual(entry.get("version"), version,
                         f"версия в marketplace.json и в {PLUGIN_MANIFEST} разошлась")
        description = entry.get("description")
        self.assertIsInstance(description, str,
                              f"description записи {PLUGIN_NAME} — не строка: {description}")
        self.assertTrue(description.strip(), f"description записи {PLUGIN_NAME} пустое")
        self.assertNotIn("\n", description, f"description записи {PLUGIN_NAME} многострочное")

    def test_direct_routes_are_not_skills(self) -> None:
        keys = _keys_of_kind("direct")
        skills = _skill_names()
        self.assertTrue(keys, "в routes.json нет ни одной записи kind == direct")
        self.assertTrue(skills, "в плагине нет ни одного каталога скила")
        overlap = sorted(keys & skills)
        self.assertEqual(overlap, [],
                         f"ключи прямых маршрутов совпали с именами скилов плагина: {overlap}")


if __name__ == "__main__":
    unittest.main()
