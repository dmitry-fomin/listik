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


#: Пути (относительно PLUGIN_DIR), которые должны называть работу по id карточки (`<id>`), а не
#: по номеру шага. `agents/*.md` собирается в момент вызова теста, а не при импорте.
CORE_DOC = pathlib.Path("references") / "pipeline-core.md"
TRACKS_DOC = pathlib.Path("skills") / "feature-pipeline" / "references" / "tracks.md"
INHERIT_SKILL = pathlib.Path("skills") / "inherit-pipeline" / SKILL_FILE
AGENTS_SUBDIR = "agents"

MANIFEST_LINE = (
    "трек <трек>: <id>, дерево <путь>, ветка task/<трек>, база <sha7>"
)


def _plugin_text(relative: pathlib.Path) -> str:
    return (PLUGIN_DIR / relative).read_text(encoding="utf-8")


#: Начало абзаца шага 0 про файл широкой механической правки.
ADHOC_PARAGRAPH_START = "**Adhoc-файл для широкой правки.**"


#: Начало абзаца шага 0 про журнал: список имён журнала по случаям.
JOURNAL_PARAGRAPH_START = "`ls <steps> <specs>`"


def _paragraph(relative: pathlib.Path, start: str) -> str:
    """Абзац `relative` от строки `start` до первой пустой строки, одной строкой."""
    lines = _plugin_text(relative).splitlines()
    for index, line in enumerate(lines):
        if line.startswith(start):
            end = index
            while end < len(lines) and lines[end].strip():
                end += 1
            return " ".join(lines[index:end])
    raise AssertionError(f"{relative}: не нашлось абзаца, начинающегося с {start!r}")


def _core_paragraph(start: str) -> str:
    """Абзац `CORE_DOC` от строки `start` до первой пустой строки, одной строкой."""
    return _paragraph(CORE_DOC, start)


def _agent_paths() -> list[pathlib.Path]:
    """Все `agents/*.md`, относительно PLUGIN_DIR, в момент вызова."""
    agents_dir = PLUGIN_DIR / AGENTS_SUBDIR
    return sorted(
        (pathlib.Path(AGENTS_SUBDIR) / path.name)
        for path in agents_dir.glob("*.md")
    )


def _spec_writer_paths() -> list[pathlib.Path]:
    agents_dir = PLUGIN_DIR / AGENTS_SUBDIR
    return sorted(
        (pathlib.Path(AGENTS_SUBDIR) / path.name)
        for path in agents_dir.glob("pipeline-spec-writer*.md")
    )


class FeaturePipelineStepNamingTests(unittest.TestCase):
    """Имена бумаг и деревьев (listik-223b, порция a) — по id карточки, не по номеру шага."""

    def test_no_old_step_number_naming(self) -> None:
        paths = [CORE_DOC, TRACKS_DOC, *_agent_paths()]
        self.assertGreater(len(_agent_paths()), 0, "в agents/ не нашлось ни одного файла")
        for relative in paths:
            with self.subTest(file=str(relative)):
                text = _plugin_text(relative)
                self.assertNotIn(
                    "step-NN", text,
                    f"{relative}: осталась подстрока 'step-NN' (старое имя по номеру шага)",
                )
                self.assertNotIn(
                    "шаг NN", text,
                    f"{relative}: осталась подстрока 'шаг NN' (старое имя по номеру шага)",
                )

    def test_pipeline_core_names_section(self) -> None:
        text = _plugin_text(CORE_DOC)
        required = [
            "## Имена бумаг и деревьев",
            "<id>.journal.md",
            "<id>.check-<X>.md",
            "<id>.diff-<X>.r<R>.txt",
            "adhoc-<ГГГГ-ММ-ДД>-<слаг>",
            "listik-n5fe",
            "<id>-<часть>",
            ".worktrees/<id>",
            "task/<id>",
            "listik worktree <id>",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(needle, text,
                              f"{CORE_DOC}: не нашлось обязательной подстроки {needle!r}")
        self.assertNotIn(
            "следующим свободным", text,
            f"{CORE_DOC}: осталась подстрока 'следующим свободным' (старое правило нумерации)",
        )

    def test_adhoc_paragraph_names_paper_by_card(self) -> None:
        """Абзац «Adhoc-файл для широкой правки» (listik-vus7) — по правилу 4: при карточке файл
        `<steps>/<id>.a.md`, adhoc-имя остаётся ветке без карточки."""
        paragraph = _core_paragraph(ADHOC_PARAGRAPH_START)
        self.assertIn(
            "<steps>/<id>.a.md", paragraph,
            f"{CORE_DOC}: абзац {ADHOC_PARAGRAPH_START!r} не называет файл по карточке",
        )
        self.assertIn(
            "при карточке", paragraph,
            f"{CORE_DOC}: абзац {ADHOC_PARAGRAPH_START!r} не оговаривает случай карточки",
        )
        self.assertIn(
            "<steps>/adhoc-<ГГГГ-ММ-ДД>-<слаг>.a.md", paragraph,
            f"{CORE_DOC}: абзац {ADHOC_PARAGRAPH_START!r} не оставил adhoc-имя ветке без карточки",
        )
        self.assertIn(
            "без карточки", paragraph,
            f"{CORE_DOC}: абзац {ADHOC_PARAGRAPH_START!r} не оговаривает случай без карточки",
        )
        self.assertLess(
            paragraph.index("<steps>/<id>.a.md"),
            paragraph.index("<steps>/adhoc-<ГГГГ-ММ-ДД>-<слаг>.a.md"),
            f"{CORE_DOC}: в абзаце {ADHOC_PARAGRAPH_START!r} adhoc-имя названо раньше файла "
            "по карточке (правило 4 требует обратного)",
        )

    def test_adhoc_paragraph_in_inherit_pipeline_names_paper_by_card(self) -> None:
        """Абзац «Adhoc-файл для широкой правки» скила inherit-pipeline (listik-ivt3) — по правилу 4:
        при карточке файл `<steps>/<id>.a.md`, adhoc-имя остаётся ветке без карточки."""
        paragraph = _paragraph(INHERIT_SKILL, ADHOC_PARAGRAPH_START)
        self.assertIn(
            "<steps>/<id>.a.md", paragraph,
            f"{INHERIT_SKILL}: абзац {ADHOC_PARAGRAPH_START!r} не называет файл по карточке",
        )
        self.assertIn(
            "при карточке", paragraph,
            f"{INHERIT_SKILL}: абзац {ADHOC_PARAGRAPH_START!r} не оговаривает случай карточки",
        )
        self.assertIn(
            "<steps>/adhoc-<ГГГГ-ММ-ДД>-<слаг>.a.md", paragraph,
            f"{INHERIT_SKILL}: абзац {ADHOC_PARAGRAPH_START!r} не оставил adhoc-имя ветке без карточки",
        )
        self.assertIn(
            "без карточки", paragraph,
            f"{INHERIT_SKILL}: абзац {ADHOC_PARAGRAPH_START!r} не оговаривает случай без карточки",
        )
        self.assertLess(
            paragraph.index("<steps>/<id>.a.md"),
            paragraph.index("<steps>/adhoc-<ГГГГ-ММ-ДД>-<слаг>.a.md"),
            f"{INHERIT_SKILL}: в абзаце {ADHOC_PARAGRAPH_START!r} adhoc-имя названо раньше файла "
            "по карточке (правило 4 требует обратного)",
        )

    def test_journal_paragraph_names_wide_edit_by_card(self) -> None:
        """Абзац шага 0 про журнал (listik-ivt3): adhoc-имя остаётся широкой правке без карточки,
        при карточке действует правило 2 — `<steps>/<id>.journal.md`."""
        paragraph = _paragraph(CORE_DOC, JOURNAL_PARAGRAPH_START)
        self.assertIn(
            "<steps>/<id>.journal.md", paragraph,
            f"{CORE_DOC}: абзац {JOURNAL_PARAGRAPH_START!r} не называет журнал по карточке "
            "(правило 2)",
        )
        adhoc_name = "<steps>/adhoc-<ГГГГ-ММ-ДД>-<слаг>.journal.md"
        self.assertIn(
            adhoc_name, paragraph,
            f"{CORE_DOC}: абзац {JOURNAL_PARAGRAPH_START!r} не оставил adhoc-имя без карточки",
        )
        self.assertIn(
            "без карточки", paragraph[paragraph.index(adhoc_name):],
            f"{CORE_DOC}: абзац {JOURNAL_PARAGRAPH_START!r} называет adhoc-журнал широкой правке "
            "безусловно: после него нет оговорки «без карточки» (listik-ivt3)",
        )

    def test_manifest_line_matches_in_core_and_tracks(self) -> None:
        for relative in (CORE_DOC, TRACKS_DOC):
            with self.subTest(file=str(relative)):
                text = _plugin_text(relative)
                self.assertIn(
                    MANIFEST_LINE, text,
                    f"{relative}: не нашлось строки манифеста трека {MANIFEST_LINE!r}",
                )

    def test_heartbeat_note_uses_id(self) -> None:
        text = _plugin_text(CORE_DOC)
        self.assertIn(
            '--note "<id>, порция X, этап N:', text,
            f"{CORE_DOC}: heartbeat --note не использует '<id>, порция X, этап N:'",
        )

    def test_spec_writer_agents_use_paper_name(self) -> None:
        paths = _spec_writer_paths()
        self.assertTrue(paths, "в agents/ не нашлось ни одного pipeline-spec-writer*.md")
        for relative in paths:
            with self.subTest(file=str(relative)):
                text = _plugin_text(relative)
                self.assertIn(
                    "<id>.check-<X>.md", text,
                    f"{relative}: не нашлось имени чек-листа '<id>.check-<X>.md'",
                )
                self.assertNotIn(
                    "номер шага", text,
                    f"{relative}: осталась фраза 'номер шага'",
                )

    def test_judge_agent_names_dump_by_id(self) -> None:
        relative = pathlib.Path(AGENTS_SUBDIR) / "pipeline-judge.md"
        text = _plugin_text(relative)
        self.assertIn(
            "<id>.judge-<X>.r<R>.md", text,
            f"{relative}: не нашлось имени файла судьи '<id>.judge-<X>.r<R>.md'",
        )


#: Пресеты, у которых по канону есть строка `BASE=<id>` в примере префикса команд.
BASE_ID_SKILLS = (
    "high-pipeline",
    "xhigh-pipeline",
    "medium-pipeline",
    "low-pipeline",
    "xlow-pipeline",
)

#: Пресеты, у которых `argument-hint` называет бумаги по `<id>.<X>.md`.
ARGUMENT_HINT_ID_SKILLS = (
    "high-pipeline",
    "xhigh-pipeline",
    "medium-pipeline",
    "low-pipeline",
    "inherit-pipeline",
    "feature-pipeline",
)

BASE_LINE_RE = re.compile(r"^BASE=<id>", re.MULTILINE)


class FeaturePipelineSkillNamingTests(unittest.TestCase):
    """Имена бумаг и деревьев в самих скилах (listik-223b, порция b) — по id карточки."""

    def test_no_old_step_number_naming_in_skills(self) -> None:
        names = sorted(_skill_names())
        self.assertTrue(names, "в плагине нет ни одного каталога скила")
        forbidden = ("step-NN", "шаг NN", "pipeline-step-", "следующим свободным")
        for name in names:
            with self.subTest(skill=name):
                text = _skill_text(name)
                for needle in forbidden:
                    self.assertNotIn(
                        needle, text,
                        f"{name}/{SKILL_FILE}: осталась запрещённая подстрока {needle!r}",
                    )

    def test_base_id_line(self) -> None:
        for name in BASE_ID_SKILLS:
            with self.subTest(skill=name):
                text = _skill_text(name)
                self.assertTrue(
                    BASE_LINE_RE.search(text),
                    f"{name}/{SKILL_FILE}: не нашлось строки, начинающейся с 'BASE=<id>'",
                )

    def test_argument_hint_uses_id(self) -> None:
        for name in ARGUMENT_HINT_ID_SKILLS:
            with self.subTest(skill=name):
                text = _skill_text(name)
                hint_lines = [line for line in text.splitlines()
                              if line.startswith("argument-hint:")]
                self.assertTrue(hint_lines, f"{name}/{SKILL_FILE}: нет строки argument-hint")
                self.assertIn(
                    "<id>.<X>.md", hint_lines[0],
                    f"{name}/{SKILL_FILE}: argument-hint не содержит '<id>.<X>.md'",
                )

    def test_inherit_pipeline_naming(self) -> None:
        text = _skill_text("inherit-pipeline")
        required = [
            "<id>.journal.md",
            "*.journal.md",
            "<id>-<часть>",
            "<id>.diff-<X>.r<R>.txt",
            "adhoc-<ГГГГ-ММ-ДД>-<слаг>.journal.md",
            MANIFEST_LINE,
            "task/<id>",
            "listik worktree <id>",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"inherit-pipeline/{SKILL_FILE}: не нашлось обязательной подстроки {needle!r}",
                )

    def test_feature_pipeline_naming(self) -> None:
        text = _skill_text("feature-pipeline")
        required = [
            "<id>.journal.md",
            "pipeline-<id>",
            ".worktrees/<id>",
            "<id>.diff-<X>.r<R>.txt",
            "adhoc-<ГГГГ-ММ-ДД>-<слаг>.journal.md",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"feature-pipeline/{SKILL_FILE}: не нашлось обязательной подстроки {needle!r}",
                )

    def test_opus_single_pipeline_naming(self) -> None:
        text = _skill_text("opus-single-pipeline")
        required = [
            "<steps>/<id>.md",
            "<steps>/<id>.journal.md",
            "single-<ГГГГ-ММ-ДД>-<слаг>",
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"opus-single-pipeline/{SKILL_FILE}: не нашлось обязательной подстроки {needle!r}",
                )

    def test_xlow_pipeline_naming(self) -> None:
        text = _skill_text("xlow-pipeline")
        self.assertIn(
            "<steps>/<id>.a.md", text,
            f"xlow-pipeline/{SKILL_FILE}: не нашлось обязательной подстроки '<steps>/<id>.a.md'",
        )

    def test_no_pipeline_branch_prefix_in_core_tracks_inherit(self) -> None:
        """pipeline-< и git worktree add -b pipeline больше не используются."""
        for relative in (CORE_DOC, TRACKS_DOC, INHERIT_SKILL):
            with self.subTest(file=str(relative)):
                text = _plugin_text(relative)
                self.assertNotIn(
                    "pipeline-<", text,
                    f"{relative}: осталась подстрока 'pipeline-<' (старое имя ветки)",
                )
                self.assertNotIn(
                    "git worktree add -b pipeline", text,
                    f"{relative}: осталась команда 'git worktree add -b pipeline' (заменена на listik worktree)",
                )

    def test_core_tracks_inherit_create_tree_with_listik_worktree(self) -> None:
        """Дерево заводится через listik worktree <id> [--track <часть>]."""
        for relative in (CORE_DOC, TRACKS_DOC, INHERIT_SKILL):
            with self.subTest(file=str(relative)):
                text = _plugin_text(relative)
                self.assertIn(
                    "listik worktree <id>", text,
                    f"{relative}: не нашлось команды 'listik worktree <id>'",
                )
        for relative in (CORE_DOC, TRACKS_DOC):
            with self.subTest(file=str(relative), flag="--track"):
                text = _plugin_text(relative)
                self.assertIn(
                    "--track <часть>", text,
                    f"{relative}: не нашлось флага '--track <часть>' для трекового режима",
                )


if __name__ == "__main__":
    unittest.main()
