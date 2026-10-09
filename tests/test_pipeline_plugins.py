"""Согласие плагинов пресетов pipeline-full, pipeline-cc и pipeline-claude с routes.json
(шаг 10, порция a; раскладка по трём плагинам — listik-d9rj, порция b).

Скил пресета — это каталог `plugins/<плагин>/skills/<имя>` с файлом `SKILL.md`;
каталог без него скилом не считается (поэтому пустой
`skills/codex-pipeline/agents` старого репозитория сюда не переехал). Здесь скил
зовётся полным именем `<плагин>:<имя>` (`pipeline-full:high`). Ключи записей
`kind == "pipeline"` в `routes.json` обязаны совпадать с ключами скилов
`skills.skill_keys()` (`full-high` ↔ `plugins/pipeline-full/skills/high`): доска
выбирает конвейер по ключу маршрута, а плагин исполняет его скилом. Записей
`kind == "direct"` в `routes.json` больше нет (listik-ar8v).

Файлы читаются в момент вызова через константы модуля, а не при импорте:
поэтому `unittest.mock.patch.object` на `ROUTES_JSON`, `PLUGINS_DIR` и
`MARKETPLACE_JSON` уводит проверку на копию во временном каталоге.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import pathlib
import re
import shutil
import tempfile
import unittest

from listik import skills as skills_mod

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
ROUTES_JSON = REPO_DIR / "routes.json"
PLUGINS_DIR = REPO_DIR / "plugins"
#: Плагин ядра: агенты, хук и `references` пресетов живут здесь, скилы — в PRESET_PLUGINS.
CORE_PLUGIN_DIR = REPO_DIR / "plugins" / "pipeline-core"
MARKETPLACE_JSON = REPO_DIR / ".claude-plugin" / "marketplace.json"

#: Плагины пресетов: имя в маркетплейсе — оно же имя каталога плагина.
PRESET_PLUGINS = ("pipeline-full", "pipeline-cc", "pipeline-claude")
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


#: Скилы pipeline-cc: их тексты сверяет tests/test_pipeline_cc_plugin.py,
#: здесь — только имя во frontmatter и связь с routes.json.
CC_SKILLS = frozenset(f"pipeline-cc:{level}" for level in ("xhigh", "high", "medium", "low", "xlow", "nano"))


def _all_skill_names() -> set[str]:
    """Полные имена скилов `<плагин>:<имя>`: каталоги `plugins/<плагин>/skills/<имя>` с `SKILL.md`."""
    names = set()
    for plugin in PRESET_PLUGINS:
        skills = PLUGINS_DIR / plugin / SKILLS_SUBDIR
        names.update(f"{plugin}:{path.name}" for path in skills.iterdir()
                     if path.is_dir() and (path / SKILL_FILE).is_file())
    return names


def _skill_names() -> set[str]:
    """Скилы пресетов, кроме CC_SKILLS (pipeline-full и pipeline-claude), — их тексты сверяет этот файл."""
    return _all_skill_names() - CC_SKILLS


def _skill_rel(name: str) -> pathlib.Path:
    """`SKILL.md` скила `<плагин>:<имя>` относительно `plugins/`."""
    plugin, skill = name.split(":", 1)
    return pathlib.Path(plugin) / SKILLS_SUBDIR / skill / SKILL_FILE


def _skill_dir_name(name: str) -> str:
    """Имя каталога скила `<плагин>:<имя>` — то, что стоит в `name:` frontmatter и в README."""
    return name.split(":", 1)[1]


def _skill_text(name: str) -> str:
    return (PLUGINS_DIR / _skill_rel(name)).read_text(encoding="utf-8")


def _frontmatter(lines: list[str]) -> list[str] | None:
    """Строки между первой и второй `---`; None, если рамки нет или она не закрыта."""
    if not lines or lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return lines[1:index]
    return None


#: Скилы плагина без маршрута в routes.json (сейчас таких нет).
SKILLS_WITHOUT_ROUTE: set[str] = set()

class FeaturePipelinePluginTests(unittest.TestCase):
    """routes.json, скилы плагина и маркетплейс не разъезжаются."""

    def test_routes_pipelines_match_plugin_skills(self) -> None:
        keys = _keys_of_kind("pipeline")
        skills = set(skills_mod.skill_keys())
        self.assertTrue(keys, "в routes.json нет ни одной записи kind == pipeline")
        extra = sorted(keys - skills)
        missing = sorted(skills - keys - SKILLS_WITHOUT_ROUTE)
        self.assertEqual(
            (extra, missing), ([], []),
            "ключи конвейеров в routes.json и скилы плагина разошлись: "
            f"лишние ключи без скила {extra}, скилы без ключа {missing}",
        )

    def test_skill_frontmatter_name_matches_dir(self) -> None:
        skills = sorted(_all_skill_names())
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
                directory = _skill_dir_name(name)
                self.assertEqual(
                    found, [directory],
                    f"{name}/{SKILL_FILE}: во frontmatter ожидалась строка name: {directory}, "
                    f"а нашлись {found}",
                )

    def test_marketplace_lists_preset_plugins(self) -> None:
        entries = _read_json(MARKETPLACE_JSON)["plugins"]
        for plugin_name in PRESET_PLUGINS:
            with self.subTest(plugin=plugin_name):
                matching = [entry for entry in entries if entry.get("name") == plugin_name]
                self.assertEqual(len(matching), 1,
                                 f"в marketplace.json не ровно одна запись {plugin_name}: {entries}")
                entry = matching[0]
                source = entry.get("source")
                self.assertIsInstance(source, str, f"source записи {plugin_name} — не строка: {source}")
                self.assertTrue((REPO_DIR / source).is_dir(),
                                f"источник плагина {source} не каталог в корне репозитория")
                version = _read_json(PLUGINS_DIR / plugin_name / PLUGIN_MANIFEST).get("version")
                self.assertEqual(entry.get("version"), version,
                                 f"версия в marketplace.json и в {PLUGIN_MANIFEST} разошлась")
                description = entry.get("description")
                self.assertIsInstance(description, str,
                                      f"description записи {plugin_name} — не строка: {description}")
                self.assertTrue(description.strip(), f"description записи {plugin_name} пустое")
                self.assertNotIn("\n", description, f"description записи {plugin_name} многострочное")

    def test_marketplace_plugin_sources_match_manifests(self) -> None:
        entries = _read_json(MARKETPLACE_JSON)["plugins"]
        names = [entry.get("name") for entry in entries]
        self.assertEqual(
            names,
            ["listik", "pipeline-core", "pipeline-full", "pipeline-cc", "pipeline-claude", "dsh", "codex",
             "opencode", "pi", "devin", "second-opinion"],
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
                self.assertEqual(
                    entry.get("version"), manifest.get("version"),
                    f"{name}: версия marketplace.json и plugin.json разошлась",
                )
                self.assertEqual(manifest.get("name"), name,
                                 f"{name}: name в plugin.json не совпал")

    def test_no_direct_routes(self) -> None:
        self.assertEqual(_keys_of_kind("direct"), set(),
                         "в routes.json осталась запись kind == direct")

#: Пути (относительно плагина, см. `_plugin_path`), которые должны называть работу по id карточки (`<id>`), а не
#: по номеру шага. `agents/*.md` собирается в момент вызова теста, а не при импорте.
CORE_DOC = pathlib.Path("references") / "pipeline-core.md"
AGENTS_SUBDIR = "agents"

MANIFEST_LINE = (
    "трек <трек>: <id>, дерево <путь>, ветка task/<трек>, база <sha7>"
)


#: Каталоги, переехавшие в плагин ядра: путь с таким первым компонентом читается из CORE_PLUGIN_DIR,
#: остальные пути (`<плагин>/skills/…`, `<плагин>/README.md`) — от PLUGINS_DIR.
CORE_SUBDIRS = ("agents", "hooks", "references")


def _plugin_path(relative: pathlib.Path) -> pathlib.Path:
    root = CORE_PLUGIN_DIR if pathlib.Path(relative).parts[0] in CORE_SUBDIRS else PLUGINS_DIR
    return root / relative


def _plugin_text(relative: pathlib.Path) -> str:
    return _plugin_path(relative).read_text(encoding="utf-8")


#: Начало абзаца шага 0 про файл широкой механической правки.
ADHOC_PARAGRAPH_START = "**Adhoc-файл для широкой правки.**"


#: Начало абзаца ядра про переиспользованное дерево трека.
TRACKS_REUSE_PARAGRAPH_START = "Каталог или ветка уже есть"


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
    """Все `agents/*.md`, относительно CORE_PLUGIN_DIR, в момент вызова."""
    agents_dir = CORE_PLUGIN_DIR / AGENTS_SUBDIR
    return sorted(
        (pathlib.Path(AGENTS_SUBDIR) / path.name)
        for path in agents_dir.glob("*.md")
    )


def _spec_writer_paths() -> list[pathlib.Path]:
    agents_dir = CORE_PLUGIN_DIR / AGENTS_SUBDIR
    return sorted(
        (pathlib.Path(AGENTS_SUBDIR) / path.name)
        for path in agents_dir.glob("pipeline-spec-writer*.md")
    )



#: Скил-указатель на ядро в плагине pipeline-core (listik-d9rj, порция a).
CORE_SKILL = pathlib.Path("skills") / "core" / SKILL_FILE
CORE_SKILL_PATH_LINE = "${CLAUDE_PLUGIN_ROOT}/references/pipeline-core.md"
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
#: Относительная markdown-ссылка `[текст](путь)` — та же форма, что LINK_RE в test_pipeline_cc_plugin.py.
LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")


def _core_path_from_skill(plugin_root: pathlib.Path) -> pathlib.Path | None:
    """Путь ядра из скила `core` копии плагина после подстановки `${CLAUDE_PLUGIN_ROOT}`."""
    text = (plugin_root / CORE_SKILL).read_text(encoding="utf-8").replace(PLUGIN_ROOT_VAR, str(plugin_root))
    resolved = CORE_SKILL_PATH_LINE.replace(PLUGIN_ROOT_VAR, str(plugin_root))
    if resolved not in text:
        return None
    return pathlib.Path(resolved)


def _broken_links(skill_path: pathlib.Path) -> list[str]:
    """Относительные ссылки SKILL.md, которые не ведут в существующий файл."""
    broken = []
    for target in LINK_RE.findall(skill_path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        if not (skill_path.parent / target.split("#", 1)[0]).exists():
            broken.append(target)
    return broken


class PipelineCorePluginTests(unittest.TestCase):
    """Плагин pipeline-core: скил-указатель, доставка ядра одним плагином, зависимости."""

    def test_core_skill(self) -> None:
        path = CORE_PLUGIN_DIR / CORE_SKILL
        self.assertTrue(path.is_file(), f"нет {path}")
        text = path.read_text(encoding="utf-8")
        frontmatter = _frontmatter(text.splitlines())
        self.assertIsNotNone(frontmatter, f"{path}: нет frontmatter")
        self.assertIn("name: core", frontmatter)
        self.assertIn("user-invocable: false", frontmatter)
        self.assertIn(CORE_SKILL_PATH_LINE, text)
        self.assertTrue((CORE_PLUGIN_DIR / "references" / "pipeline-core.md").is_file())

    def _copy_plugin(self) -> pathlib.Path:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "pipeline-core" / "1.0.0"
        shutil.copytree(CORE_PLUGIN_DIR, root)
        return root

    def test_core_delivered_by_single_plugin(self) -> None:
        """Копия одного плагина, как в кэше Claude Code: путь из скила ведёт в файл внутри копии."""
        root = self._copy_plugin()
        core = _core_path_from_skill(root)
        self.assertIsNotNone(core, f"в скиле нет строки {CORE_SKILL_PATH_LINE!r}")
        self.assertTrue(core.is_file(), f"ядра нет по пути из скила: {core}")
        self.assertTrue(core.resolve().is_relative_to(root.resolve()), f"{core} вне копии плагина {root}")

    def test_core_delivery_check_rejects_broken_copy(self) -> None:
        root = self._copy_plugin()
        (root / "references" / "pipeline-core.md").unlink()
        core = _core_path_from_skill(root)
        self.assertFalse(core is not None and core.is_file(), "проверка не заметила пропавшее ядро")
        root = self._copy_plugin()
        skill = root / CORE_SKILL
        skill.write_text(skill.read_text(encoding="utf-8").replace(
            CORE_SKILL_PATH_LINE, PLUGIN_ROOT_VAR + "/../pipeline-full/references/pipeline-core.md"),
            encoding="utf-8")
        core = _core_path_from_skill(root)
        self.assertFalse(core is not None and core.is_file(), "проверка не заметила путь вне плагина")

    def test_preset_links_resolve(self) -> None:
        skills = sorted(_skill_names())
        self.assertTrue(skills)
        for name in skills:
            with self.subTest(skill=name):
                self.assertEqual(_broken_links(PLUGINS_DIR / _skill_rel(name)), [])

    def test_link_check_rejects_old_references_link(self) -> None:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        skill = tmp / "plugins" / "pipeline-full" / "skills" / "high" / SKILL_FILE
        skill.parent.mkdir(parents=True)
        old_link = "../../references/ROLES.md"
        skill.write_text(_skill_text("pipeline-full:high") + f"\n[ROLES.md]({old_link})\n", encoding="utf-8")
        self.assertIn(old_link, _broken_links(skill))

    def test_preset_plugins_depend_on_core(self) -> None:
        for plugin in PRESET_PLUGINS:
            with self.subTest(plugin=plugin):
                manifest = _read_json(REPO_DIR / "plugins" / plugin / PLUGIN_MANIFEST)
                self.assertIn("pipeline-core", manifest.get("dependencies", []))
        with_option = sorted(
            entry["name"] for entry in _read_json(MARKETPLACE_JSON)["plugins"]
            if "auto_approve_agents" in _read_json(REPO_DIR / entry["source"] / PLUGIN_MANIFEST)
            .get("userConfig", {}))
        self.assertEqual(with_option, ["pipeline-core"])


class FeaturePipelineStepNamingTests(unittest.TestCase):
    """Имена бумаг и деревьев (listik-223b, порция a) — по id карточки, не по номеру шага."""

    def test_no_old_step_number_naming(self) -> None:
        paths = [CORE_DOC, *_agent_paths()]
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

    def test_manifest_line_in_core(self) -> None:
        text = _plugin_text(CORE_DOC)
        self.assertIn(
            MANIFEST_LINE, text,
            f"{CORE_DOC}: не нашлось строки манифеста трека {MANIFEST_LINE!r}",
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

    def test_tracks_doc_removed(self) -> None:
        """`tracks.md` удалён, и ни один `SKILL.md` на него больше не ссылается."""
        for plugin in PRESET_PLUGINS:
            tracks_doc = PLUGINS_DIR / plugin / "skills/feature-pipeline/references/tracks.md"
            self.assertFalse(
                tracks_doc.exists(),
                f"{tracks_doc}: файл должен быть удалён — его содержимое переехало в {CORE_DOC}",
            )
        for name in sorted(_skill_names()):
            with self.subTest(skill=name):
                self.assertNotIn(
                    "tracks.md", _skill_text(name),
                    f"{name}/{SKILL_FILE}: ссылка на удалённый tracks.md",
                )

    def test_core_tracks_mention_worktree_list(self) -> None:
        """Абзац про переиспользованное дерево зовёт `git worktree list` после `/clear`."""
        paragraph = _paragraph(CORE_DOC, TRACKS_REUSE_PARAGRAPH_START)
        for needle in ("`git worktree list`", "/clear"):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, paragraph,
                    f"{CORE_DOC}: в абзаце {TRACKS_REUSE_PARAGRAPH_START!r} нет {needle!r}",
                )


#: Пресеты, у которых по канону есть строка `BASE=<id>` в примере префикса команд.
BASE_ID_SKILLS = (
    "pipeline-full:high",
    "pipeline-full:xhigh",
    "pipeline-full:medium",
    "pipeline-full:low",
    "pipeline-full:xlow",
)

#: Пресеты, у которых `argument-hint` называет бумаги по `<id>.<X>.md`.
ARGUMENT_HINT_ID_SKILLS = (
    "pipeline-full:high",
    "pipeline-full:xhigh",
    "pipeline-full:medium",
    "pipeline-full:low",
)

BASE_LINE_RE = re.compile(r"^BASE=<id>", re.MULTILINE)

#: Чтение ядра, которое держит каждый SKILL.md пресета: подстроки текста со схлопнутыми пробелами.
CORE_LINK = ("скил `pipeline-core:core`",
             "прочитай ядро по пути, который он назовёт, целиком до первого действия",
             "`plugins/pipeline-core/references/pipeline-core.md`")

#: Пресеты без ссылки на ядро (сейчас таких нет).
PRESETS_WITHOUT_CORE_LINK: tuple[str, ...] = ()


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

    def test_presets_read_core(self) -> None:
        """Каждый пресет читает ядро через скил `pipeline-core:core` (пробелы и переносы схлопнуты)."""
        for name in sorted(_skill_names()):
            if name in PRESETS_WITHOUT_CORE_LINK:
                continue
            collapsed = " ".join(_skill_text(name).split())
            for needle in CORE_LINK:
                with self.subTest(skill=name, needle=needle):
                    self.assertIn(
                        needle, collapsed,
                        f"{name}/{SKILL_FILE}: нет чтения ядра {needle!r}",
                    )

    def test_xlow_pipeline_naming(self) -> None:
        text = _skill_text("pipeline-full:xlow")
        self.assertIn(
            "<steps>/<id>.a.md", text,
            f"pipeline-full:xlow/{SKILL_FILE}: не нашлось обязательной подстроки '<steps>/<id>.a.md'",
        )

    def test_no_pipeline_branch_prefix(self) -> None:
        """pipeline-< и git worktree add -b pipeline больше не используются."""
        for relative in (CORE_DOC,):
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

    def test_core_creates_tree_with_listik_worktree(self) -> None:
        """Дерево заводится через listik worktree <id> [--track <часть>]."""
        for relative in (CORE_DOC,):
            with self.subTest(file=str(relative)):
                text = _plugin_text(relative)
                self.assertIn(
                    "listik worktree <id>", text,
                    f"{relative}: не нашлось команды 'listik worktree <id>'",
                )
        with self.subTest(file=str(CORE_DOC), flag="--track"):
            text = _plugin_text(CORE_DOC)
            self.assertIn(
                "--track <часть>", text,
                f"{CORE_DOC}: не нашлось флага '--track <часть>' для трекового режима",
            )


#: Строки журнала шага про сессию исполнителя (listik-auj4): id сессии, факт продолжения
#: и откат на новый прогон — «три строки, как сделано», ответ автора.
SESSION_JOURNAL_LINES = (
    "<харнесс> сессия <id>",
    "<харнесс> продолжение <id>",
    "<харнесс> продолжение <id> не удалось — новый прогон",
)

#: Пресеты с исполнителем этапа 3 и подстрока, которой пресет называет продолжение той же
#: сессии: локальный субагент — `SendMessage` по agent id, внешний харнесс — сессия по id
#: из журнала. Продолжение обязательно и после `вопрос`, и после красного вердикта.
EXECUTOR_PRESETS = {
    "pipeline-full:high": "SendMessage",
    "pipeline-full:medium": "SendMessage",
    "pipeline-full:xhigh": "SendMessage",
    "pipeline-full:low": "сессия <id>",
    "pipeline-full:xlow": "сессия <id>",
    "pipeline-full:nano": "сессия <id>",
}


class FeaturePipelineResumeRuleTests(unittest.TestCase):
    """Продолжение сессии исполнителя (listik-auj4): одно правило для всех пресетов, задано
    протоколом, ключа настройки в конфиге нет, откат — новый прогон или новый субагент."""

    def test_core_keeps_session_journal_lines(self) -> None:
        text = _plugin_text(CORE_DOC)
        for needle in SESSION_JOURNAL_LINES:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"{CORE_DOC}: нет строки журнала шага про сессию {needle!r}",
                )

    def test_core_has_no_resume_setting_key(self) -> None:
        """Ответ автора: настройка живёт только в коде, ключа и места «выбирает автор» нет."""
        text = _plugin_text(CORE_DOC)
        for needle in ("resume_after_red", "выбирает автор"):
            with self.subTest(needle=needle):
                self.assertNotIn(
                    needle, text,
                    f"{CORE_DOC}: осталось место {needle!r} — правило задано протоколом, "
                    "а не ключом настройки",
                )

    def test_every_executor_preset_continues_session(self) -> None:
        for name, needle in sorted(EXECUTOR_PRESETS.items()):
            with self.subTest(skill=name):
                text = _skill_text(name)
                self.assertIn(
                    needle, text,
                    f"{name}/{SKILL_FILE}: не нашлось продолжения той же сессии ({needle!r})",
                )
                self.assertIn(
                    "откат", text,
                    f"{name}/{SKILL_FILE}: нет отката на новый прогон, когда продолжить нельзя",
                )


#: Имя скила в сессии → файл в репозитории Listik. Grok сюда не входит: его в маркетплейсе нет.
VENDORED_SESSION_TO_PATH = {
    "dsh:dsh-delegate": "plugins/dsh/skills/dsh-delegate/SKILL.md",
    "dsh:dsh-check": "plugins/dsh/skills/dsh-check/SKILL.md",
    "dsh:dsh-jobs": "plugins/dsh/skills/dsh-jobs/SKILL.md",
    "dsh:dsh-runtime": "plugins/dsh/skills/dsh-runtime/SKILL.md",
    "codex:codex-delegate": "plugins/codex/skills/codex-delegate/SKILL.md",
    "codex:codex-check": "plugins/codex/skills/codex-check/SKILL.md",
    "codex:codex-jobs": "plugins/codex/skills/codex-jobs/SKILL.md",
    "codex:codex-runtime": "plugins/codex/skills/codex-runtime/SKILL.md",
    "second-opinion:ask": "plugins/second-opinion/skills/ask/SKILL.md",
    "listik:listik": "plugins/listik/skills/listik/SKILL.md",
    "pi:pi-delegate": "plugins/pi/skills/pi-delegate/SKILL.md",
    "pi:pi-check": "plugins/pi/skills/pi-check/SKILL.md",
    "pi:pi-jobs": "plugins/pi/skills/pi-jobs/SKILL.md",
    "pi:pi-runtime": "plugins/pi/skills/pi-runtime/SKILL.md",
    "devin:devin-delegate": "plugins/devin/skills/devin-delegate/SKILL.md",
    "devin:devin-check": "plugins/devin/skills/devin-check/SKILL.md",
    "devin:devin-jobs": "plugins/devin/skills/devin-jobs/SKILL.md",
    "devin:devin-runtime": "plugins/devin/skills/devin-runtime/SKILL.md",
}

#: Канал в пресете называется запуском; у него в тексте ещё и путь `plugins/…`.
VENDORED_LAUNCH_SKILLS = (
    "dsh:dsh-delegate",
    "codex:codex-delegate",
    "second-opinion:ask",
    "listik:listik",
    "pi:pi-delegate",
    "devin:devin-delegate",
)

MD_SKILL_LINK_RE = re.compile(r"\[`(?P<name>[^`]+)`\]\((?P<href>[^)]+)\)")
VENDORED_HREF_MARKS = ("/dsh/", "/codex/", "/second-opinion/", "/listik/")
VENDORED_HREF_MARKS += ("/pi/", "/devin/")


def _pipeline_docs() -> list[pathlib.Path]:
    """Ядро и SKILL.md всех пресетов — там, где оркестратор ищет внешний скил."""
    docs = [_plugin_path(CORE_DOC)]
    docs.extend(
        PLUGINS_DIR / _skill_rel(name)
        for name in sorted(_skill_names())
    )
    return docs


class FeaturePipelineVendoredSkillPathTests(unittest.TestCase):
    """Скилы dsh/Codex/второго мнения/Listik живут в этом репозитории; пресеты указывают путь."""

    def test_vendored_skill_files_exist(self) -> None:
        for name, relative in VENDORED_SESSION_TO_PATH.items():
            with self.subTest(skill=name):
                path = REPO_DIR / relative
                self.assertTrue(path.is_file(), f"{name}: нет файла {relative}")

    def test_markdown_links_to_listik_plugins_resolve(self) -> None:
        for path in _pipeline_docs():
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_DIR)
            for match in MD_SKILL_LINK_RE.finditer(text):
                href = match.group("href")
                if not any(mark in href for mark in VENDORED_HREF_MARKS):
                    continue
                resolved = (path.parent / href).resolve()
                with self.subTest(file=str(rel), href=href):
                    self.assertTrue(
                        resolved.is_file(),
                        f"{rel}: ссылка {href} не указывает на файл (ожидался {resolved})",
                    )

    def test_mentioned_vendored_skills_have_link_and_repo_path(self) -> None:
        for path in _pipeline_docs():
            text = path.read_text(encoding="utf-8")
            rel = path.relative_to(REPO_DIR)
            linked = {match.group("name") for match in MD_SKILL_LINK_RE.finditer(text)}
            for name, repo_path in VENDORED_SESSION_TO_PATH.items():
                mentioned = f"`{name}`" in text or f"[`{name}`]" in text
                if not mentioned:
                    continue
                with self.subTest(file=str(rel), skill=name):
                    self.assertIn(
                        name, linked,
                        f"{rel}: упоминается {name}, но нет markdown-ссылки [`{name}`](...)",
                    )
                    if name in VENDORED_LAUNCH_SKILLS:
                        self.assertIn(
                            f"`{repo_path}`", text,
                            f"{rel}: упоминается {name}, но нет пути `{repo_path}`",
                        )


#: low/xlow: этап 3 ведёт devin на SWE-2 max, запасного нет (правило владельца: роль
#: недоступна — конвейер стоит).
DEVIN_EXECUTOR_PRESETS = ("pipeline-full:low", "pipeline-full:xlow")
DEVIN_EXECUTOR_REQUIRED = (
    "devin:devin-delegate",
    "--thinking max",
    "--holder devin",
    '--label "$BASE <X>"',
    "--cwd $WT",
    '--session "$BASE-<X>"',
)
NO_FALLBACK_PRESETS = ("pipeline-full:low", "pipeline-full:xlow", "pipeline-full:nano")
NO_FALLBACK_FORBIDDEN = ("dsh:dsh-delegate", "pi-deepseek", "--channel deepseek", "запасной —",
                         "Запасной исполнитель")
#: Формулировка стоп-фактора: строка журнала из ядра (`pipeline-core.md`, «Стоп-фактор»).
STOP_LINE = "стоп: <роль> — <харнесс> недоступен: <причина>"
STOP_MARK = "Стоп-фактор"


class FeaturePipelineNoFallbackTests(unittest.TestCase):
    """Недоступная роль останавливает конвейер: запасных исполнителей нет нигде."""

    def test_low_and_xlow_run_devin_without_fallback(self) -> None:
        for name in DEVIN_EXECUTOR_PRESETS:
            text = _skill_text(name)
            for needle in DEVIN_EXECUTOR_REQUIRED:
                with self.subTest(skill=name, required=needle):
                    self.assertIn(needle, text, f"{name}/{SKILL_FILE}: нет {needle!r}")
            with self.subTest(skill=name, required="стоп devin"):
                self.assertIn("стоп: исполнитель — devin недоступен", text)

    def test_low_xlow_nano_have_no_fallback(self) -> None:
        for name in NO_FALLBACK_PRESETS:
            text = _skill_text(name)
            for needle in NO_FALLBACK_FORBIDDEN:
                with self.subTest(skill=name, forbidden=needle):
                    self.assertNotIn(needle, text, f"{name}/{SKILL_FILE}: осталось {needle!r}")

    def test_every_preset_has_stop_factor(self) -> None:
        skills = sorted(f"{plugin}:{p.parent.name}" for plugin in PRESET_PLUGINS
                        for p in (PLUGINS_DIR / plugin / "skills").glob("*/" + SKILL_FILE))
        self.assertTrue(skills)
        for name in skills:
            text = _skill_text(name)
            for needle in (STOP_MARK, STOP_LINE, "needs-owner"):
                with self.subTest(skill=name, required=needle):
                    self.assertIn(needle, text, f"{name}/{SKILL_FILE}: нет стоп-фактора {needle!r}")

    def test_core_defines_stop_factor(self) -> None:
        core = (CORE_PLUGIN_DIR / "references" / "pipeline-core.md").read_text(encoding="utf-8")
        for needle in ("### Стоп-фактор", "`стоп: <роль> — <харнесс> недоступен: <причина>`",
                       "Запасного исполнителя нет", "стоп-фактор недоступной роли"):
            with self.subTest(required=needle):
                self.assertIn(needle, core)



#: Двухходовка пресетов без писателя ТЗ (listik-47y8, порция d): ход 1 на чтении возвращает
#: границы правки и план, ход 2 — продолжение той же сессии с правом записи.
TWO_TURN_COMMON = (
    "Ход 1",
    "write_scope=",
    "## Границы правки",
    "Файлы:",
    "Не трогать:",
    "## План",
    "ход 1 пропущен — write_scope задан",
    "пути названы автором",
    "ничего не правь, команд не запускай",
    "только из этого списка",
    "оба",
)
TWO_TURN_PER_SKILL = {
    "pipeline-full:xlow": ("resume --session", "--thinking max"),
    "pipeline-full:nano": ("resume --session", "--thinking high", "devin:devin-delegate",
                      "--permission read"),
}


class FeaturePipelineTwoTurnTests(unittest.TestCase):
    """xlow/nano: ход 1 — границы правки на чтении, ход 2 — продолжение сессии (listik-47y8)."""

    def test_xlow_and_nano_describe_two_turns(self) -> None:
        for name, own in sorted(TWO_TURN_PER_SKILL.items()):
            text = _skill_text(name)
            for needle in TWO_TURN_COMMON + own:
                with self.subTest(skill=name, required=needle):
                    self.assertIn(needle, text, f"{name}/{SKILL_FILE}: нет {needle!r}")

#: Канон правила о коммите порции приёмкой (listik-4n4y): заголовок раздела ядра.
COMMIT_RULE_HEADING = "## Коммит порции приёмкой"

#: Запрещающие маркеры для строки, где упомянут `amend`: строка обязана содержать хотя бы один
#: из них, иначе скан считает, что amend в ней разрешён. Все маркеры — запрет по смыслу:
#: «запрещ…», «нельзя», «недопустим», «не трогать», «не твои», «не делает», «не обходит».
AMEND_FORBID_MARKERS = (
    "запрещ",
    "нельзя",
    "недопустим",
    "не трогать",
    "не твои",
    "не делает",
    "не обходит",
)

#: Направления скана «нигде не разрешён amend»: references/*.md, agents/*.md, skills/*/SKILL.md.
REFERENCES_SUBDIR = "references"


def _commit_rule_block() -> str:
    """Раздел `CORE_DOC` от заголовка COMMIT_RULE_HEADING до следующего `## `."""
    lines = _plugin_text(CORE_DOC).splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(COMMIT_RULE_HEADING):
            start = index
        elif start is not None and line.startswith("## "):
            return "\n".join(lines[start:index])
    if start is None:
        raise AssertionError(f"{CORE_DOC}: не нашлось раздела {COMMIT_RULE_HEADING!r}")
    return "\n".join(lines[start:])


def _amend_scan_paths() -> list[pathlib.Path]:
    """Файлы скана: references/*.md, agents/*.md и skills/*/SKILL.md (hooks/ не входит)."""
    paths = sorted(
        pathlib.Path(REFERENCES_SUBDIR) / path.name
        for path in (CORE_PLUGIN_DIR / REFERENCES_SUBDIR).glob("*.md")
    )
    paths.extend(_agent_paths())
    paths.extend(
        _skill_rel(name)
        for name in sorted(_skill_names())
    )
    return paths


def _line_forbids_amend(line: str) -> bool:
    """True, если строка с `amend` содержит запрещающий маркер, а не разрешение."""
    lowered = line.lower()
    return any(marker in lowered for marker in AMEND_FORBID_MARKERS)


#: Шесть `SKILL.md`, где задание судье лежит inline (listik-4n4y, порция b). Список закрытый
#: и явный: именно он — основа выбора файлов, страховочный скан ниже лишь ловит расширение.
INLINE_JUDGE_SKILLS = (
    _skill_rel("pipeline-full:high"),
    _skill_rel("pipeline-full:xhigh"),
    _skill_rel("pipeline-full:medium"),
    _skill_rel("pipeline-full:low"),
    _skill_rel("pipeline-full:xlow"),
    _skill_rel("pipeline-full:nano"),
)

#: Начало блока inline-задания судье: строка про зелёный вердикт и коммит порции. В пяти
#: пресетах это «сам закоммить порцию», в nano сжатый пересказ — «сам коммитит порцию».
INLINE_JUDGE_START_MARKERS = ("закоммить порцию", "сам коммитит порцию")

#: Маркер для страховочного скана «в списке нет лишнего и список не устарел»: перечень
#: запретов, который несёт только inline-задание судье.
INLINE_JUDGE_MARKER = "reset, stash"

#: Требования к блоку задания судье: имя пункта и обязательные фрагменты внутри блока.
INLINE_JUDGE_REQUIREMENTS = (
    ("git rm", ("git rm",)),
    ("commit с -- и путями", ("git commit", "-- <все пути")),
    ("проверка diff коммита", ("git show --stat --name-status",)),
    ("второй коммит вместо amend", ("второй коммит поверх", "amend")),
    ("оба хеша во второй строке", ("оба хеша", "вторая строка")),
)


def _judge_task_block(relative: pathlib.Path) -> str:
    """Блок inline-задания судье: от строки про зелёный коммит до конца задания.

    Для пяти пресетов задание лежит в fenced-блоке — конец по закрывающему ```. У nano
    задание — пункт списка: конец по следующему `## ` или нумерованному пункту верхнего
    уровня.
    """
    lines = _plugin_text(relative).splitlines()
    start = None
    for index, line in enumerate(lines):
        if any(marker in line for marker in INLINE_JUDGE_START_MARKERS):
            start = index
            break
    if start is None:
        raise AssertionError(f"{relative}: не нашлось начала inline-задания судье")
    fenced = sum(1 for line in lines[:start] if line.strip().startswith("```")) % 2 == 1
    block = [lines[start]]
    for line in lines[start + 1:]:
        if fenced:
            block.append(line)
            if line.strip().startswith("```"):
                break
        elif line.startswith("## ") or re.match(r"^\d+\. ", line):
            break
        else:
            block.append(line)
    return "\n".join(block)


class FeaturePipelineCommitRuleTests(unittest.TestCase):
    """Коммит порции с удалёнными через `git rm` файлами (listik-4n4y): правило вместо amend."""

    def test_core_has_commit_rule_block(self) -> None:
        block = _commit_rule_block()
        required = (
            "rev-parse",
            "BASE=",
            "git add",
            "новые и изменённые пути",
            "git commit -m",
            "-- <все пути",
            "git rm",
            "did not match any files",
            "git show --stat --name-status",
            "$BASE..HEAD",
            "2.50.1",
            "второй коммит",
            "Оба хеша",
            "--amend",
            "reset",
            "stash",
            "checkout",
            "clean",
            "rebase",
            "--no-verify",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, block,
                    f"{CORE_DOC}: в разделе {COMMIT_RULE_HEADING!r} нет {needle!r}",
                )

    def test_core_journal_and_done_know_several_hashes(self) -> None:
        text = _plugin_text(CORE_DOC)
        required = (
            "коммит <hash7>",
            "коммиты <hash7>, <hash7>",
            "<коммиты <hash7>, <hash7>>",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"{CORE_DOC}: нет варианта с несколькими хешами {needle!r}",
                )

    def test_judge_agent_states_commit_rule(self) -> None:
        relative = pathlib.Path(AGENTS_SUBDIR) / "pipeline-judge.md"
        text = _plugin_text(relative)
        required = (
            "rev-parse",
            "git add",
            "git rm",
            "did not match any files",
            "git commit -m",
            "-- <все пути",
            "git show --stat --name-status",
            "$BASE..HEAD",
            "второй коммит",
            "--amend",
            "через запятую",
        )
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"{relative}: нет элемента правила коммита порции {needle!r}",
                )

    def test_nothing_in_plugin_allows_amend(self) -> None:
        """Ни одна строка с `amend` в references/, agents/ и skills/*/SKILL.md не разрешает его.

        Запрещающие маркеры (AMEND_FORBID_MARKERS) перечислены явно: «запрещ…», «нельзя»,
        «недопустим», «не трогать», «не твои», «не делает», «не обходит». Строка с `amend`,
        не содержащая ни одного из них, считается разрешением и валит скан; `hooks/` в скан
        не входит — там `--amend` это регулярка запрещающего списка.
        """
        found = 0
        for relative in _amend_scan_paths():
            for number, line in enumerate(_plugin_text(relative).splitlines(), start=1):
                if "amend" not in line:
                    continue
                found += 1
                with self.subTest(file=str(relative), line=number):
                    self.assertTrue(
                        _line_forbids_amend(line),
                        f"{relative}:{number}: amend упомянут без запрещающего маркера: {line!r}",
                    )
        self.assertGreater(found, 0, "скан не нашёл ни одной строки с amend — сломан обход")

    def test_amend_predicate_rejects_permitting_line(self) -> None:
        """Синтетическая строка, разрешающая amend, обязана быть отвергнута предикатом."""
        synthetic = "при неполном коммите допустим `git commit --amend`"
        self.assertIn("amend", synthetic)
        self.assertFalse(
            _line_forbids_amend(synthetic),
            "предикат принял строку, разрешающую amend, за запрет",
        )
        self.assertTrue(
            _line_forbids_amend("`git commit --amend` запрещён"),
            "предикат отверг настоящий запрет amend",
        )


class FeaturePipelineInlineJudgeRuleTests(unittest.TestCase):
    """Inline-задание судье в шести SKILL.md несёт правило коммита порции (listik-4n4y, порция b)."""

    def test_inline_judge_states_commit_rule(self) -> None:
        """Каждый из шести SKILL.md: внутри блока задания судье — все пункты правила."""
        for relative in INLINE_JUDGE_SKILLS:
            block = _judge_task_block(relative)
            for name, needles in INLINE_JUDGE_REQUIREMENTS:
                with self.subTest(file=str(relative), requirement=name):
                    missing = [needle for needle in needles if needle not in block]
                    self.assertFalse(
                        missing,
                        f"{relative}: в блоке задания судье нет {missing!r} "
                        f"(требование {name!r})",
                    )

    def test_judge_marker_skills_are_all_listed(self) -> None:
        """Любой SKILL.md с маркером inline-задания судье обязан входить в INLINE_JUDGE_SKILLS."""
        listed = set(INLINE_JUDGE_SKILLS)
        marked = 0
        for name in sorted(_skill_names()):
            relative = _skill_rel(name)
            if INLINE_JUDGE_MARKER not in _plugin_text(relative):
                continue
            marked += 1
            with self.subTest(file=str(relative)):
                self.assertIn(
                    relative, listed,
                    f"{relative}: несёт маркер inline-задания судье "
                    f"{INLINE_JUDGE_MARKER!r}, но не входит в INLINE_JUDGE_SKILLS",
                )
        self.assertGreater(
            marked, 0,
            "страховочный скан не нашёл ни одного inline-задания судье — сломан маркер",
        )


#: Дословные строки раздела ядра `## Рабочее дерево в файле порции` (listik-tula, порция a):
#: заголовок, правило строки, эталон и оба сообщения предстартовой проверки.
CORE_WORKTREE_LINES = (
    "## Рабочее дерево в файле порции",
    "Первой строкой шапки файла порции стоит `Рабочее дерево: <абсолютный путь>` — "
    "отдельной строкой, без пояснений и без кавычек после пути.",
    "Строка обязательна в любом режиме, а не только в треке.",
    "Своего дерева у работы нет — в строке стоит абсолютный путь основного дерева.",
    "Эталон — путь из строки журнала шага `дерево <путь>`.",
    "Сравнение — точное равенство путей после `cd … && pwd -P`, не префикс и не вхождение "
    "подстроки.",
    "порция <X>: в ТЗ нет пути рабочего дерева — стоп",
    "порция <X>: путь в ТЗ не совпал с деревом работы — стоп",
    "Путь из этой строки идёт первой строкой задачи исполнителю в любом режиме, а внешнему "
    "харнессу — рабочим каталогом в вызове скила запуска.",
)

#: Пять строк блока предстартовой проверки перед этапом 3: присваивание `WT`, поиск маркера
#: с якорем `^`, извлечение пути через `sed` и стоп-сообщение.
PRESTART_CHECK_LINES = (
    'WT="$(cd <дерево работы> && pwd -P)"',
    "grep -n '^Рабочее дерево: /' <steps>/<имя>.<X>.md \\",
    'P="$(sed -n \'s/^Рабочее дерево: //p\' <steps>/<имя>.<X>.md | head -1)"',
    '[ "$(cd "$P" 2>/dev/null && pwd -P)" = "$WT" ] \\',
    '|| echo "порция <X>: в ТЗ нет пути рабочего дерева — стоп"',
)

#: Заголовки ядра, порядок которых закреплён: новый раздел стоит между именами бумаг и каналом.
CORE_NAMES_HEADING = "## Имена бумаг и деревьев"
CORE_WORKTREE_HEADING = "## Рабочее дерево в файле порции"
CORE_EXTERNAL_HEADING = "## Внешние скилы"

#: Начало абзаца автора ТЗ про обязательную строку рабочего дерева в каждом файле порции.
SPEC_WRITER_PARAGRAPH_START = (
    "**Рабочее дерево — обязательная строка каждого файла порции.**"
)

#: Начало абзаца `## Файлы` про несколько одновременно пишущихся частей: он не должен пропасть,
#: когда рядом встал абзац про рабочее дерево.
SPEC_WRITER_MULTI_PARTS_PARAGRAPH_START = (
    "Задача может быть **одной из нескольких частей, которые пишутся одновременно**"
)

#: Ожидаемые пути `_spec_writer_paths()`: четыре писателя ТЗ без потерь.
EXPECTED_SPEC_WRITER_PATHS = (
    pathlib.Path(AGENTS_SUBDIR) / "pipeline-spec-writer-low.md",
    pathlib.Path(AGENTS_SUBDIR) / "pipeline-spec-writer-medium.md",
    pathlib.Path(AGENTS_SUBDIR) / "pipeline-spec-writer-xhigh.md",
    pathlib.Path(AGENTS_SUBDIR) / "pipeline-spec-writer.md",
)


class FeaturePipelineSpecWriterWorktreeTests(unittest.TestCase):
    """Рабочее дерево — обязательная строка файла порции (listik-tula, порция b): проверка
    в ядре конвейера и дословный абзац во всех определениях автора ТЗ."""

    def test_core_worktree_lines_verbatim(self) -> None:
        """Десять дословных строк раздела ядра о рабочем дереве на месте."""
        text = _plugin_text(CORE_DOC)
        for needle in CORE_WORKTREE_LINES:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"{CORE_DOC}: не нашлось дословной строки {needle!r}",
                )

    def test_core_prestart_check_block_verbatim(self) -> None:
        """Пять строк блока предстартовой проверки перед этапом 3 на месте."""
        text = _plugin_text(CORE_DOC)
        for needle in PRESTART_CHECK_LINES:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"{CORE_DOC}: в блоке предстартовой проверки нет строки {needle!r}",
                )

    def test_core_worktree_heading_order(self) -> None:
        """`## Имена бумаг и деревьев` < `## Рабочее дерево в файле порции` < `## Внешние скилы`."""
        lines = _plugin_text(CORE_DOC).splitlines()
        for heading in (CORE_NAMES_HEADING, CORE_WORKTREE_HEADING, CORE_EXTERNAL_HEADING):
            self.assertIn(heading, lines, f"{CORE_DOC}: нет заголовка {heading!r}")
        self.assertLess(
            lines.index(CORE_NAMES_HEADING), lines.index(CORE_WORKTREE_HEADING),
            f"{CORE_DOC}: {CORE_WORKTREE_HEADING!r} стоит не после {CORE_NAMES_HEADING!r}",
        )
        self.assertLess(
            lines.index(CORE_WORKTREE_HEADING), lines.index(CORE_EXTERNAL_HEADING),
            f"{CORE_DOC}: {CORE_WORKTREE_HEADING!r} стоит не перед {CORE_EXTERNAL_HEADING!r}",
        )

    def test_spec_writer_worktree_paragraph(self) -> None:
        """У каждого писателя ТЗ абзац велит ставить строку и не выдумывать путь."""
        for relative in _spec_writer_paths():
            with self.subTest(file=str(relative)):
                paragraph = _paragraph(relative, SPEC_WRITER_PARAGRAPH_START)
                for needle in (
                    "`Рабочее дерево: <абсолютный путь>`",
                    "а не только когда работа идёт несколькими треками",
                    "первая строка которого `вопрос`",
                ):
                    with self.subTest(needle=needle):
                        self.assertIn(
                            needle, paragraph,
                            f"{relative}: в абзаце {SPEC_WRITER_PARAGRAPH_START!r} нет {needle!r}",
                        )

    def test_spec_writer_paths_exactly_four(self) -> None:
        """Список писателей ТЗ — ровно четыре пути, ни один не потерян."""
        self.assertEqual(
            _spec_writer_paths(), list(EXPECTED_SPEC_WRITER_PATHS),
            "список pipeline-spec-writer*.md разошёлся с ожидаемым",
        )

    def test_spec_writer_bodies_identical(self) -> None:
        """Тела четырёх писателей ТЗ после frontmatter совпадают посимвольно."""
        paths = _spec_writer_paths()
        self.assertTrue(paths, "в agents/ не нашлось ни одного pipeline-spec-writer*.md")
        bodies: dict[str, str] = {}
        for relative in paths:
            lines = _plugin_text(relative).splitlines()
            frontmatter = _frontmatter(lines)
            self.assertIsNotNone(
                frontmatter, f"{relative}: frontmatter не закрыт строкой ---",
            )
            bodies[str(relative)] = "\n".join(lines[len(frontmatter or []) + 2:])
        first = bodies[str(paths[0])]
        for relative in paths:
            with self.subTest(file=str(relative)):
                self.assertEqual(
                    bodies[str(relative)], first,
                    f"{relative}: тело разъехалось с {paths[0]}",
                )

    def test_spec_writer_multi_parts_paragraph_kept(self) -> None:
        """Абзац про несколько одновременно пишущихся частей остался у каждого писателя ТЗ."""
        for relative in _spec_writer_paths():
            with self.subTest(file=str(relative)):
                paragraph = _paragraph(relative, SPEC_WRITER_MULTI_PARTS_PARAGRAPH_START)
                self.assertTrue(
                    paragraph.startswith(SPEC_WRITER_MULTI_PARTS_PARAGRAPH_START),
                    f"{relative}: пропал абзац {SPEC_WRITER_MULTI_PARTS_PARAGRAPH_START!r}",
                )


class FeaturePipelineListikCardTests(unittest.TestCase):
    """Исполнители и судья преднагружают `listik:listik` и ведут карточку сами (listik-5qzq, порция c)."""

    CARD_AGENTS = {
        "pipeline-implementer.md": "-k journal",
        "pipeline-implementer-high.md": "-k journal",
        "pipeline-judge.md": "-k verdict",
        "pipeline-judge-xhigh.md": "-k verdict",
    }

    @staticmethod
    def _has_listik_skill(frontmatter: list[str]) -> bool:
        """Строка `skills:` и сразу под ней `  - listik:listik`."""
        return any(line == "skills:" and frontmatter[index + 1] == "  - listik:listik"
                   for index, line in enumerate(frontmatter[:-1]))

    def test_card_agents_preload_listik_skill(self) -> None:
        for name, marker in self.CARD_AGENTS.items():
            with self.subTest(agent=name):
                text = _plugin_text(pathlib.Path(AGENTS_SUBDIR) / name)
                frontmatter = _frontmatter(text.splitlines())
                self.assertIsNotNone(frontmatter, f"{name}: нет frontmatter")
                self.assertTrue(self._has_listik_skill(frontmatter),
                                f"{name}: во frontmatter нет skills: с - listik:listik")
                self.assertIn("\n## Карточка Listik\n", text, f"{name}: нет раздела «Карточка Listik»")
                self.assertIn(marker, text, f"{name}: нет {marker!r}")

    def test_spec_and_critic_agents_do_not_preload_listik(self) -> None:
        agents = CORE_PLUGIN_DIR / AGENTS_SUBDIR
        names = ["pipeline-critic.md"] + sorted(path.name for path in agents.glob("pipeline-spec-writer*.md"))
        self.assertGreater(len(names), 1, "не нашлось ни одного pipeline-spec-writer*.md")
        for name in names:
            with self.subTest(agent=name):
                frontmatter = _frontmatter(_plugin_text(pathlib.Path(AGENTS_SUBDIR) / name).splitlines())
                self.assertIsNotNone(frontmatter, f"{name}: нет frontmatter")
                self.assertFalse(any("listik:listik" in line for line in frontmatter),
                                 f"{name}: listik:listik не должен преднагружаться")


class FeaturePipelineLocalCardLineTests(unittest.TestCase):
    """Локальному субагенту — строка карточки, вердикт пишет судья сам (listik-5qzq, порция d)."""

    def test_core_has_local_subagent_card_line(self) -> None:
        text = _plugin_text(CORE_DOC)
        heading = "\n### Строка задания локальному субагенту\n"
        self.assertIn(heading, text, "в ядре нет подраздела «Строка задания локальному субагенту»")
        section = text.split(heading, 1)[1].split("\n### ", 1)[0]
        self.assertIn("`Listik, карточка <P>`", section)

    def test_no_skill_says_session_writes_verdict(self) -> None:
        for name in sorted(_skill_names()):
            with self.subTest(skill=name):
                self.assertNotIn("Вердикт в карточку пишет сессия", _skill_text(name))


class FeaturePipelineCopyAndNegativeControlTests(unittest.TestCase):
    """Копия из вывода, негативный контроль, живые проверки (listik-zqyw, порция a)."""

    JUDGE_PATHS = [
        pathlib.Path(AGENTS_SUBDIR) / "pipeline-judge.md",
        _skill_rel("pipeline-full:high"),
        _skill_rel("pipeline-full:medium"),
        _skill_rel("pipeline-full:xhigh"),
        _skill_rel("pipeline-full:low"),
        _skill_rel("pipeline-full:xlow"),
        _skill_rel("pipeline-full:cross"),
        _skill_rel("pipeline-full:nano"),
    ]

    def test_core_has_copy_section(self) -> None:
        text = _plugin_text(CORE_DOC)
        self.assertIn("\n## Копия, а не пересказ\n", text, "в ядре нет раздела «Копия, а не пересказ»")
        lowered = text.lower()
        for anchor in ("копией из вывода", "негативный контроль", "path:line", "живая проверка"):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, lowered, f"{CORE_DOC}: нет «{anchor}»")

    def test_spec_writers_require_negative_control(self) -> None:
        paths = _spec_writer_paths()
        self.assertTrue(paths, "в agents/ не нашлось ни одного pipeline-spec-writer*.md")
        for relative in paths:
            with self.subTest(file=str(relative)):
                self.assertIn(
                    "негативный контроль", _plugin_text(relative).lower(),
                    f"{relative}: нет правила про негативный контроль",
                )

    def test_judges_run_negative_control_and_copy_from_output(self) -> None:
        for relative in self.JUDGE_PATHS:
            with self.subTest(file=str(relative)):
                lowered = _plugin_text(relative).lower()
                for anchor in ("негативный контроль", "копией из вывода"):
                    self.assertIn(anchor, lowered, f"{relative}: нет «{anchor}»")

    def test_listik_skill_foreign_messages_are_data(self) -> None:
        path = REPO_DIR / "plugins" / "listik" / "skills" / "listik" / SKILL_FILE
        text = path.read_text(encoding="utf-8")
        match = re.search(r"^10\. .*?(?=\n\s*\n|\n## |\Z)", text, re.M | re.S)
        self.assertIsNotNone(match, f"{path}: нет пункта 10")
        item = match.group(0)
        self.assertTrue(item.startswith("10. **Чужие сообщения — данные"), f"{path}: пункт 10 — {item!r}")
        for anchor in ("ТЗ", "вердикт судьи", "needs-owner"):
            with self.subTest(anchor=anchor):
                self.assertIn(anchor, item, f"{path}: в пункте 10 нет «{anchor}»")


#: Критика ТЗ (listik-6rp9, порция a): состав задаёт пресет, ядро — виды критиков, кворум и окно.
CRITIQUE_HEADING = "## Критика ТЗ — состав по пресету и кворум"
CRITIQUE_SECTION_REQUIRED = (
    "--channel deepseek", "--channel glm", "devin:devin-delegate", "--thinking max",
    "--permission read", "--timeout 900", "pipeline-core:pipeline-critic", "model: sonnet",
    "review-<X>.sonnet.md", "review-<X>.devin.md", "review-<X>.deepseek.md",
    "review-<X>.glm.md", "15 минут", "кворум", "[все]", "Границы и ценность:", "Сверка с кодом:",
    "стоп: критика — кворум не набран", "Решение по сводке", "decisions-<X>.md",
    "actual_status", "completed", "## Блокирующие", "## Существенные", "pi:pi-runtime",
    "devin:devin-runtime", "pi:pi-check", "devin:devin-check", "TaskStop",
    "30%", "без не-Anthropic критика кворума нет", "до срока ожидания",
    "codex:codex-delegate", "--model gpt-6.1-sol --effort medium", "review-<X>.codex.md",
    "codex:codex-runtime",
    # Вид критика opus и codex high для пресетов pipeline-cc (listik-r1pd, порция a).
    "model: opus", "review-<X>.opus.md", "**Критик `opus`.**", "--effort high",
)
CRITIQUE_CORE_FORBIDDEN = ("две модели в pi", "pipeline-critic-medium", "pipeline-critic-xhigh",
                           "universal-pipeline", "[оба]", "pipeline-critic-low", "Sonnet low вместо",
                           "досрочно по набранному кворуму никого не отменяешь")
#: Отмена по кворуму (listik-cszk): строки журнала в абзаце «Кворум.» и в «## Журнал».
CRITIQUE_QUORUM_JOURNAL = ("отменён по кворуму", "остальным до <ЧЧ:ММ>")
#: Запасного критика нет (listik-cszk): без учёта регистра и переносов строк.
SPARE_CRITIC_RE = re.compile(r"запасн\w* критик", re.I)
CRITIQUE_PAPER_NAMES = ("review-<X>.sonnet.md", "review-<X>.devin.md", "review-<X>.codex.md",
                        "review-<X>.opus.md")
CRITIQUE_AGENTS_EFFORT = {"pipeline-critic.md": "high"}
#: Удалённые агенты-критики: medium/xhigh законно живут в pipeline-core (пресеты pipeline-cc), low — нигде.
CRITIQUE_AGENTS_REMOVED = ("pipeline-critic-low.md",)


#: Исключение на коммит судьи всех pipeline-cc:* в мосте codex (listik-rdwp, порция a).
CODEX_DELEGATE_SKILL = "plugins/codex/skills/codex-delegate/SKILL.md"
CODEX_RUNTIME_SKILL = "plugins/codex/skills/codex-runtime/SKILL.md"
CODEX_RUNTIME_RED_LINES = "## Red lines (apply inside every codex run)"
CODEX_COMMIT_EXCEPTION_REQUIRED = ("push", "amend", "reset", "explicit paths",
                                   "pipeline-cc:*")
#: Исключение на --model/--effort для пресетов pipeline-* в мосте codex (listik-r1pd, порция a).
CODEX_RUNNER_AGENT = "plugins/codex/agents/codex-runner.md"
CODEX_README = "plugins/codex/README.md"
CODEX_MODEL_PIN_PLUGINS = ("pipeline-full", "pipeline-cc", "pipeline-claude")


def _codex_commit_exception_problems(block: str) -> list[str]:
    """Чего не хватает блоку запрета коммитов в мосте codex; пустой список — всё на месте."""
    return [f"в блоке нет {needle!r}" for needle in CODEX_COMMIT_EXCEPTION_REQUIRED
            if needle not in block]


def _codex_model_pin_missing(block: str) -> list[str]:
    """Какие плагины не названы в исключении на --model/--effort; пустой список — все."""
    return [name for name in CODEX_MODEL_PIN_PLUGINS if name not in block]


def _is_blank(line: str) -> bool:
    return not line.strip()


def _has_spare_critic(text: str) -> bool:
    """Есть ли «запасной критик» в любом регистре и с любыми переносами между словами."""
    return bool(SPARE_CRITIC_RE.search(" ".join(text.split())))


def _critique_core_problems(text: str) -> list[str]:
    """Проблемы «Критики ТЗ» в тексте ядра; пустой список — всё на месте."""
    problems: list[str] = []
    section = _text_section(text, CRITIQUE_HEADING)
    if not section:
        problems.append(f"нет раздела {CRITIQUE_HEADING!r}")
    else:
        problems.extend(f"в разделе нет {needle!r}" for needle in CRITIQUE_SECTION_REQUIRED
                        if needle not in section)
        quorum = _text_block(section, "**Кворум.**", lambda line: not line.strip())
        problems.extend(f"в абзаце «Кворум.» нет {needle!r}" for needle in CRITIQUE_QUORUM_JOURNAL
                        if needle not in quorum)
        if "`codex`" not in quorum:
            problems.append("в абзаце «Кворум.» нет '`codex`'")
        if "`opus`" not in quorum:
            problems.append("в абзаце «Кворум.» нет '`opus`'")
        rows = section.splitlines()
        opus_at = next((i for i, line in enumerate(rows) if line.startswith("| `opus` |")), None)
        if opus_at is None:
            problems.append("в таблице видов нет строки '| `opus` |'")
        elif opus_at == 0 or not rows[opus_at - 1].startswith("| `sonnet` |"):
            problems.append("строка '| `opus` |' идёт не сразу после '| `sonnet` |'")
        if "--provider" in section:
            problems.append("в разделе есть '--provider'")
        preflight = _text_block(section, "**Предполётная проверка**", lambda line: not line.strip())
        if "codex:codex-check" not in preflight:
            problems.append("в абзаце «Предполётная проверка» нет 'codex:codex-check'")
        if "`opus`" not in preflight:
            problems.append("в абзаце «Предполётная проверка» нет '`opus`'")
        collect = _text_block(section, "**Забор.**", lambda line: not line.strip())
        if "codex:codex-jobs" not in collect:
            problems.append("в абзаце «Забор.» нет 'codex:codex-jobs'")
    problems.extend(f"в ядре есть {needle!r}" for needle in CRITIQUE_CORE_FORBIDDEN if needle in text)
    if _has_spare_critic(text):
        problems.append("в ядре есть «запасной критик»")
    names_rule = _text_block(_text_section(text, "## Имена бумаг и деревьев"), "1. ",
                             lambda line: line.startswith("2. "))
    problems.extend(f"правило 1 «Имена бумаг и деревьев» не называет {name!r}"
                    for name in CRITIQUE_PAPER_NAMES if name not in names_rule)
    journal = _text_section(text, "## Журнал")
    if "glm+deepseek" in journal:
        problems.append("в «Журнал» есть 'glm+deepseek'")
    if "порция <X>: кворум" not in journal:
        problems.append("в «Журнал» нет 'порция <X>: кворум'")
    problems.extend(f"в «Журнал» нет {needle!r}" for needle in CRITIQUE_QUORUM_JOURNAL
                    if needle not in journal)
    if "devin:devin-delegate" not in _text_section(text, "## Внешние скилы"):
        problems.append("в «Внешние скилы» нет 'devin:devin-delegate'")
    who_writes = _text_block(text, "**Кто пишет в карточку.**", lambda line: not line.strip())
    if "pipeline-critic*" not in who_writes:
        problems.append("абзац «Кто пишет в карточку» не называет 'pipeline-critic*'")
    return problems


CRITIQUE_PRESETS = ("pipeline-full:xhigh", "pipeline-full:high", "pipeline-full:medium", "pipeline-full:low",
                    "pipeline-full:cross")
#: Обязательные для всех пресетов с критикой подстроки: скилы внешнего критика (pi) и общие.
CRITIQUE_PRESET_REQUIRED = ("pi:pi-delegate", "pi:pi-jobs", "`pipeline-core.md`, «Критика ТЗ»",
                            "$STEPS/$BASE.review-<X>.md")


CRITIQUE_PRESET_FORBIDDEN = ("second-opinion:ask", "--no-system")
CRITIQUE_STAGE2_FORBIDDEN = ("--write", "--permission write")
NO_CRITIQUE_PRESETS = ("pipeline-full:xlow", "pipeline-full:nano")
NO_CRITIQUE_FORBIDDEN = ("review-<X>.glm.md", "«Критика ТЗ»")


def _stage2_section(text: str) -> str:
    """Строки от начинающейся с `### 2.` до начинающейся с `### 3.` (без неё)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("### 2.")), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("### 3.")),
               len(lines))
    return "\n".join(lines[start:end])


class FeaturePipelineCritiqueTests(unittest.TestCase):
    """Этап 2 всех пресетов с критикой — GLM и DeepSeek в pi по ядру (listik-jmxl)."""

    def test_core_defines_critique(self) -> None:
        self.assertEqual(_critique_core_problems(_plugin_text(CORE_DOC)), [])

    def test_critique_check_rejects_broken_core(self) -> None:
        text = _plugin_text(CORE_DOC)
        section = _text_section(text, CRITIQUE_HEADING)
        journal = _text_section(text, "## Журнал")
        quorum = _text_block(section, "**Кворум.**", lambda line: not line.strip())
        self.assertTrue(quorum, f"{CORE_DOC}: нет абзаца «Кворум.»")
        preflight = _text_block(section, "**Предполётная проверка**", lambda line: not line.strip())
        collect = _text_block(section, "**Забор.**", lambda line: not line.strip())
        codex_row = next((line for line in section.splitlines() if line.startswith("| `codex` |")), "")
        self.assertTrue(codex_row, f"{CORE_DOC}: нет строки таблицы вида codex")
        opus_row = next((line for line in section.splitlines() if line.startswith("| `opus` |")), "")
        self.assertTrue(opus_row, f"{CORE_DOC}: нет строки таблицы вида opus")
        opus_critic = _text_block(section, "**Критик `opus`.**", lambda line: not line.strip())
        self.assertTrue(opus_critic, f"{CORE_DOC}: нет абзаца «Критик `opus`.»")
        table_end = _text_block(section, opus_row, lambda line: not line.startswith("| ")) \
            .splitlines()[-1]
        self.assertNotEqual(table_end, opus_row, f"{CORE_DOC}: строка opus уже последняя")
        self.assertTrue(section, f"{CORE_DOC}: нет раздела {CRITIQUE_HEADING!r}")
        self.assertTrue(journal, f"{CORE_DOC}: нет раздела «Журнал»")
        broken = {
            "без заголовка": text.replace(CRITIQUE_HEADING + "\n", "", 1),
            "дописан pipeline-critic-xhigh": text.replace(
                section, section + "\nзапасной: pipeline-critic-xhigh\n", 1),
            "дописан [оба]": text.replace(section, section + "\n- [оба] [код] пункт\n", 1),
            "в «Журнал» дописано критика glm+deepseek": text.replace(
                journal, journal + "\n| старое | `порция <X>: критика glm+deepseek — …` |\n", 1),
            "в раздел дописан Запасной критик": text.replace(
                section, section + "\n**Запасной критик** — Sonnet.\n", 1),
            "в раздел дописано строчное запасной\\nкритик": text.replace(
                section, section + "\nидёт запасной\nкритик Sonnet.\n", 1),
            "в раздел дописано досрочно … не отменяешь": text.replace(
                section, section + "\nдосрочно по набранному кворуму никого не отменяешь.\n", 1),
            "из абзаца Кворум убрано отменён по кворуму": text.replace(
                quorum, quorum.replace("отменён по кворуму", ""), 1),
            "из раздела убрана строка вида codex": text.replace(codex_row + "\n", "", 1),
            "из абзаца Кворум убрано `codex`": text.replace(
                quorum, quorum.replace("`codex`", ""), 1),
            "в раздел дописано --provider": text.replace(
                section, section + "\n--provider a6api\n", 1),
            "из абзаца Предполётная проверка убрано codex:codex-check": text.replace(
                preflight, preflight.replace("codex:codex-check", ""), 1),
            "из абзаца Забор убрано codex:codex-jobs": text.replace(
                collect, collect.replace("codex:codex-jobs", ""), 1),
            "убрана строка вида opus": text.replace(opus_row + "\n", "", 1),
            "строка вида opus переставлена в конец таблицы": text.replace(
                opus_row + "\n", "", 1).replace(table_end + "\n", table_end + "\n" + opus_row + "\n", 1),
            "убран абзац Критик `opus`": text.replace(opus_critic + "\n", "", 1),
            "из абзаца Кворум убрано `opus`": text.replace(
                quorum, quorum.replace("`opus`", ""), 1),
            "из строки codex убрано --effort high": text.replace(
                codex_row, codex_row.replace("--effort high", ""), 1),
        }
        for needle in ("15 минут", "--permission read", "стоп: критика — кворум не набран",
                       "completed", "30%", "до срока ожидания"):
            broken[f"без {needle!r}"] = text.replace(section, section.replace(needle, ""), 1)
        for case, mutated in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_critique_core_problems(mutated), [])

    def test_codex_bridge_allows_cc_pipeline_judge_commit(self) -> None:
        delegate = (REPO_DIR / CODEX_DELEGATE_SKILL).read_text(encoding="utf-8")
        runtime = (REPO_DIR / CODEX_RUNTIME_SKILL).read_text(encoding="utf-8")
        blocks = {
            CODEX_DELEGATE_SKILL: _text_block(delegate, "Project red lines",
                                              lambda line: not line.strip()),
            CODEX_RUNTIME_SKILL: _text_block(_text_section(runtime, CODEX_RUNTIME_RED_LINES),
                                             "- Never commit", lambda line: line.startswith("- ")),
        }
        for path, block in blocks.items():
            with self.subTest(file=path):
                self.assertTrue(block, f"{path}: нет блока запрета коммитов")
                self.assertEqual(_codex_commit_exception_problems(block), [], path)
                for needle in ("push", "pipeline-cc:*"):
                    self.assertNotEqual(
                        _codex_commit_exception_problems(block.replace(needle, "")), [],
                        f"{path}: проверка не ловит блок без {needle!r}")

    def test_codex_bridge_allows_claude_codex_model_pins(self) -> None:
        delegate = (REPO_DIR / CODEX_DELEGATE_SKILL).read_text(encoding="utf-8")
        runtime = (REPO_DIR / CODEX_RUNTIME_SKILL).read_text(encoding="utf-8")
        runner = (REPO_DIR / CODEX_RUNNER_AGENT).read_text(encoding="utf-8")
        readme = (REPO_DIR / CODEX_README).read_text(encoding="utf-8")
        blocks = {
            f"{CODEX_DELEGATE_SKILL}: Model, provider": _text_block(
                delegate, "Model, provider and effort are never your choice", _is_blank),
            f"{CODEX_RUNTIME_SKILL}: Model, provider": _text_block(
                runtime, "- **Model, provider and effort are the human's choice.**",
                lambda line: line.startswith("- ")),
            f"{CODEX_RUNNER_AGENT}: Model, provider": _text_block(
                runner, "Model, provider and effort stay as the human configured them", _is_blank),
            f"{CODEX_README}: one exception": _text_block(
                readme, next((line for line in readme.splitlines()
                              if "The one exception is a run started by" in line), "\0"), _is_blank),
            f"{CODEX_README}: --model": _text_block(
                readme, "| `--model <name>`", lambda line: True),
            f"{CODEX_README}: --effort": _text_block(
                readme, "| `--effort <level>`", lambda line: True),
        }
        for name, block in blocks.items():
            with self.subTest(place=name):
                self.assertTrue(block, f"{name}: блок не найден")
                self.assertEqual(_codex_model_pin_missing(block), [], name)
                self.assertNotEqual(_codex_model_pin_missing(block.replace("pipeline-cc", "")), [],
                                    f"{name}: проверка не ловит блок без 'pipeline-cc'")

    def test_critic_agents(self) -> None:
        agents = CORE_PLUGIN_DIR / AGENTS_SUBDIR
        for name, effort in CRITIQUE_AGENTS_EFFORT.items():
            frontmatter = _frontmatter(_plugin_text(pathlib.Path(AGENTS_SUBDIR) / name).splitlines())
            with self.subTest(agent=name):
                self.assertIsNotNone(frontmatter, f"{name}: нет frontmatter")
                self.assertIn("model: sonnet", frontmatter, f"{name}: не model: sonnet")
                self.assertIn(f"effort: {effort}", frontmatter, f"{name}: не effort: {effort}")
        for plugin in PRESET_PLUGINS:
            plugin_agents = PLUGINS_DIR / plugin / AGENTS_SUBDIR
            self.assertFalse(plugin_agents.exists(),
                             f"{plugin_agents}: агенты переехали в pipeline-core, каталога быть не должно")
        for name in CRITIQUE_AGENTS_REMOVED:
            with self.subTest(removed=name):
                self.assertFalse((agents / name).exists(), f"{name} должен быть удалён")

    def test_critique_presets_point_to_core(self) -> None:
        for name in CRITIQUE_PRESETS:
            text = _skill_text(name)
            for needle in CRITIQUE_PRESET_REQUIRED:
                with self.subTest(skill=name, required=needle):
                    self.assertIn(needle, text, f"{name}/{SKILL_FILE}: нет {needle!r}")
            for needle in CRITIQUE_PRESET_FORBIDDEN:
                with self.subTest(skill=name, forbidden=needle):
                    self.assertNotIn(needle, text, f"{name}/{SKILL_FILE}: осталось {needle!r}")

    def test_critique_stage2_has_no_write(self) -> None:
        for name in CRITIQUE_PRESETS:
            section = _stage2_section(_skill_text(name))
            with self.subTest(skill=name, check="раздел есть"):
                self.assertTrue(section, f"{name}/{SKILL_FILE}: нет раздела «### 2.»")
            for needle in CRITIQUE_STAGE2_FORBIDDEN:
                with self.subTest(skill=name, forbidden=needle):
                    self.assertNotIn(needle, section, f"{name}/{SKILL_FILE}: в этапе 2 есть {needle!r}")

    def test_presets_without_critique_stay_clean(self) -> None:
        for name in NO_CRITIQUE_PRESETS:
            text = _skill_text(name)
            for needle in NO_CRITIQUE_FORBIDDEN:
                with self.subTest(skill=name, forbidden=needle):
                    self.assertNotIn(needle, text, f"{name}/{SKILL_FILE}: есть {needle!r}")


#: Приёмка линзами (listik-3exw, порция a): раздел ядра, его место и дословные строки.
LENS_HEADING = "## Приёмка линзами"
LENS_AFTER_HEADING = "## Коммит порции приёмкой"
LENS_BEFORE_HEADING = "## Пределы на порцию"
LENS_JOURNAL_LINES = (
    "порция <X>: отпечаток до линз <sha>",
    "порция <X>: отпечаток после линз <sha>",
    "порция <X>: pi-glm job <id> (линза <имя>)",
    "порция <X>: pi-glm повтор линзы <имя> — <причина>",
    "порция <X>: в диффе похоже на секрет — стоп",
    "порция <X>: линза изменила дерево — стоп",
    "порция <X>: линзы — чисто, коммит <hash7>",
    "порция <X>: линзы — находки <N>, судья",
    "порция <X>: вынесено <id> — <заголовок>",
)
LENS_SECTION_REQUIRED = (
    "«сборка и границы»", "«технические дыры»", "«соответствие намерению»",
    "$STEPS/$BASE.lens-<X>.scope.md", "$STEPS/$BASE.lens-<X>.holes.md",
    "$STEPS/$BASE.lens-<X>.intent.md",
    "--channel glm", "--permission bash", "--permission read",
    '--label "$BASE <X>: линза <имя>"',
    "Границы правки", "Проверки порции", "негативный контроль вне «Проверок порции»",
    "Код читай в дереве работы", "чисто", "находки", "## Критичные инварианты",
    "вынести:", "отбросить:", "чинить", "--discovered-from <P>",
    "<id> (порция <X>): <суть порции>", "находки 0", "Находки линз — не красное",
    "стоп: линза <линза> — pi glm недоступен: <причина>",
    "порция <X>: готово (коммит <hash7>)",
    *LENS_JOURNAL_LINES,
)
LENS_SECTION_FORBIDDEN = ("--permission write", "--write", "--channel deepseek")
LENS_PAPER_NAMES = ("<id>.lens-<X>.scope.md", "<id>.lens-<X>.holes.md", "<id>.lens-<X>.intent.md")
HEADLESS_PARAGRAPH_START = "Что не дефолтится и в headless"


def _text_section(text: str, heading: str) -> str:
    """Раздел `text` от строки `heading` до следующей строки, начинающейся с `## `; пусто — нет."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line == heading), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _text_block(text: str, start_prefix: str, stop) -> str:
    """Строки `text` от начинающейся с `start_prefix` до первой, где `stop(line)` истинно."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(start_prefix)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if stop(lines[i])), len(lines))
    return "\n".join(lines[start:end])


def _lens_core_problems(text: str) -> list[str]:
    """Проблемы «Приёмки линзами» в тексте ядра; пустой список — всё на месте."""
    problems: list[str] = []
    lines = text.splitlines()
    headings = [line for line in lines if line.startswith("## ")]
    section = _text_section(text, LENS_HEADING)
    if not section:
        problems.append(f"нет раздела {LENS_HEADING!r}")
    else:
        for neighbour in (LENS_AFTER_HEADING, LENS_BEFORE_HEADING):
            if neighbour not in headings:
                problems.append(f"нет раздела {neighbour!r}")
        if LENS_AFTER_HEADING in headings and LENS_BEFORE_HEADING in headings:
            index = headings.index(LENS_HEADING)
            if not headings.index(LENS_AFTER_HEADING) < index < headings.index(LENS_BEFORE_HEADING):
                problems.append(f"{LENS_HEADING!r} стоит не между {LENS_AFTER_HEADING!r} "
                                f"и {LENS_BEFORE_HEADING!r}")
        problems.extend(f"в разделе нет {needle!r}" for needle in LENS_SECTION_REQUIRED
                        if needle not in section)
        problems.extend(f"в разделе есть {needle!r}" for needle in LENS_SECTION_FORBIDDEN
                        if needle in section)
    journal = _text_section(text, "## Журнал")
    problems.extend(f"в «Журнал» нет {line!r}" for line in LENS_JOURNAL_LINES if line not in journal)
    rules = _text_section(text, "## Жёсткие правила")
    first_rule = _text_block(rules, "- ", lambda line: line.startswith("- ") or not line.strip())
    if "«Приёмка линзами»" not in first_rule:
        problems.append("первый пункт «Жёсткие правила» не называет «Приёмка линзами»")
    names_rule = _text_block(_text_section(text, "## Имена бумаг и деревьев"), "1. ",
                             lambda line: line.startswith("2. "))
    problems.extend(f"правило 1 «Имена бумаг и деревьев» не называет {name!r}"
                    for name in LENS_PAPER_NAMES if name not in names_rule)
    headless = _text_block(text, HEADLESS_PARAGRAPH_START, lambda line: not line.strip())
    problems.extend(f"абзац {HEADLESS_PARAGRAPH_START!r} не называет {needle!r}"
                    for needle in ("секрет", "линз") if needle not in headless)
    return problems


class FeaturePipelineLensAcceptanceTests(unittest.TestCase):
    """Приёмка линзами в ядре (listik-3exw, порция a): три линзы GLM перед судьёй."""

    def test_core_defines_lens_acceptance(self) -> None:
        self.assertEqual(_lens_core_problems(_plugin_text(CORE_DOC)), [])

    def test_lens_check_rejects_broken_core(self) -> None:
        text = _plugin_text(CORE_DOC)
        section = _text_section(text, LENS_HEADING)
        self.assertTrue(section, f"{CORE_DOC}: нет раздела {LENS_HEADING!r}")
        broken = {
            "без заголовка": text.replace(LENS_HEADING + "\n", "", 1),
            "--permission write": text.replace(
                section, section + "\nлинза scope: --permission write\n", 1),
            "--channel deepseek": text.replace(
                section, section + "\nлинза intent: --channel deepseek\n", 1),
            "без «Находки линз — не красное»": text.replace(
                section, section.replace("Находки линз — не красное", ""), 1),
        }
        for case, mutated in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_lens_core_problems(mutated), [])


#: Приёмка линзами в пресетах (listik-3exw, порция b): кто ссылается на ядро и что несёт.
LENS_PRESETS = ("pipeline-full:high", "pipeline-full:xhigh")
LENS_CORE_REF = "`pipeline-core.md`, «Приёмка линзами»"
LENS_REF = "«Приёмка линзами»"
LENS_FILE_MARK = "lens-<X>"
LENS_ROLES_REQUIRED = ("--channel glm", "линз")
LENS_STAGE4_REQUIRED = (LENS_REF, "pi:pi-delegate")
LENS_JUDGE_REQUIRED = ("вынести:", "отбросить:", "чинить", LENS_FILE_MARK)
LENS_JUDGE_BLOCK_MARK = "Ты — приёмка одной порции ТЗ"


def _readme(plugin: str) -> pathlib.Path:
    """README плагина пресетов относительно `plugins/`: таблица «Скилы» у каждого своя."""
    return pathlib.Path(plugin) / "README.md"


#: Пресеты с линзами-субагентами Claude вместо pi glm: запуск — по скилу пресета, остальное — по ядру.
CLAUDE_LENS_PRESETS = ("pipeline-claude:high", "pipeline-claude:xhigh")
CLAUDE_LENS_REQUIRED = (LENS_CORE_REF, "вынести:", "отбросить:", LENS_FILE_MARK)
#: Агенты линзы и судьи каждого из CLAUDE_LENS_PRESETS — своей колонки; сверка — по имени целиком.
CLAUDE_LENS_AGENTS = {
    "pipeline-claude:high": ("pipeline-core:pipeline-lens", "pipeline-core:pipeline-judge"),
    "pipeline-claude:xhigh": ("pipeline-core:pipeline-lens-xhigh", "pipeline-core:pipeline-judge-xhigh"),
}


def _whole_name(name: str) -> re.Pattern[str]:
    """Имя агента или пресета целиком: после него не идёт буква, цифра, `_` или `-`."""
    return re.compile(re.escape(name) + r"(?![\w-])")


def _fenced_block_with(text: str, needle: str) -> str:
    """Fenced-блок (между строками ```), в котором есть строка с `needle`; пусто — нет."""
    block: list[str] | None = None
    for line in text.splitlines():
        if line.strip().startswith("```"):
            if block is None:
                block = []
                continue
            if any(needle in inner for inner in block):
                return "\n".join(block)
            block = None
        elif block is not None:
            block.append(line)
    return ""


def _lens_preset_problems(name: str, text: str) -> list[str]:
    """Проблемы приёмки линзами в тексте SKILL.md пресета; пусто — всё на месте."""
    problems: list[str] = []
    if name in LENS_PRESETS:
        if LENS_CORE_REF not in text:
            problems.append(f"{name}: нет ссылки {LENS_CORE_REF!r}")
        roles = _text_section(text, "## Роли")
        problems.extend(f"{name}: в «## Роли» нет {needle!r}"
                        for needle in LENS_ROLES_REQUIRED if needle not in roles)
        stage4 = _text_block(text, "### 4.", lambda line: line.startswith("## "))
        problems.extend(f"{name}: в этапе 4 нет {needle!r}"
                        for needle in LENS_STAGE4_REQUIRED if needle not in stage4)
        judge = _fenced_block_with(text, LENS_JUDGE_BLOCK_MARK)
        problems.extend(f"{name}: в задании судье нет {needle!r}"
                        for needle in LENS_JUDGE_REQUIRED if needle not in judge)
    elif name in CLAUDE_LENS_PRESETS:
        problems.extend(f"{name}: нет {needle!r}" for needle in CLAUDE_LENS_REQUIRED if needle not in text)
        problems.extend(f"{name}: нет агента {agent!r}" for agent in CLAUDE_LENS_AGENTS[name]
                        if not _whole_name(agent).search(text))
    else:
        problems.extend(f"{name}: чужой пресет несёт {needle!r}"
                        for needle in (LENS_REF, LENS_FILE_MARK) if needle in text)
    return problems


def _lens_readme_problems(text: str, skills: set[str]) -> list[str]:
    """Строки таблицы «Скилы» README плагина: `линз` ровно у LENS_PRESETS; строка — по имени каталога."""
    problems: list[str] = []
    for name in sorted(skills):
        prefix = f"| `{_skill_dir_name(name)}` "
        rows = [line for line in text.splitlines() if line.startswith(prefix)]
        if name in LENS_PRESETS + CLAUDE_LENS_PRESETS:
            if not rows:
                problems.append(f"README: нет строки {prefix!r}")
            elif not any("линз" in row for row in rows):
                problems.append(f"README: строка {name} не называет линзы")
        elif any("линз" in row for row in rows):
            problems.append(f"README: строка {name} называет линзы")
    return problems


class FeaturePipelineLensPresetTests(unittest.TestCase):
    """Пресеты high и xhigh ведут приёмку линзами по ядру (listik-3exw, порция b)."""

    def test_skills_follow_lens_acceptance(self) -> None:
        names = _skill_names()
        for name in LENS_PRESETS + CLAUDE_LENS_PRESETS:
            self.assertIn(name, names, f"нет скила {name}")
        for name in sorted(names):
            with self.subTest(skill=name):
                self.assertEqual(_lens_preset_problems(name, _skill_text(name)), [])

    def test_readme_names_lenses_only_for_lens_presets(self) -> None:
        for plugin in PRESET_PLUGINS:
            with self.subTest(plugin=plugin):
                skills = {name for name in _skill_names() if name.startswith(f"{plugin}:")}
                self.assertEqual(_lens_readme_problems(_plugin_text(_readme(plugin)), skills), [])

    def test_lens_checks_reject_broken_texts(self) -> None:
        high = _skill_text("pipeline-full:high")
        medium = _skill_text("pipeline-full:medium")
        xhigh = _skill_text("pipeline-full:xhigh")
        judge = _fenced_block_with(xhigh, LENS_JUDGE_BLOCK_MARK)
        self.assertIn("вынести:", judge, "в задании судье xhigh нет «вынести:»")
        broken = {
            "high без «Приёмка линзами»": ("pipeline-full:high", high, high.replace(LENS_REF, "")),
            "medium с «Приёмка линзами»": ("pipeline-full:medium", medium,
                                           medium + f"\nПриёмка — по {LENS_CORE_REF}.\n"),
            "xhigh: задание судье без «вынести:»": (
                "pipeline-full:xhigh", xhigh, xhigh.replace(judge, judge.replace("вынести:", ""), 1)),
        }
        for case, (name, text, mutated) in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_lens_preset_problems(name, mutated), [])


#: Состав критиков по пресетам (listik-6rp9, порция b): что обязано быть и чего нет в этапе 2.
CRITIQUE_QUORUM_STOP = "стоп: критика — кворум не набран"
CRITIQUE_SONNET = ("pipeline-core:pipeline-critic", "model: sonnet")
CRITIQUE_COMPOSITION = {
    # имя: (есть в этапе 2, нет в этапе 2 (с учётом регистра), нет без учёта регистра)
    "pipeline-full:xhigh": (CRITIQUE_SONNET + ("deepseek", "glm"), ("devin:devin-delegate",), ()),
    "pipeline-full:high": (CRITIQUE_SONNET + ("deepseek", "glm"), ("devin:devin-delegate",), ()),
    "pipeline-full:medium": (CRITIQUE_SONNET + ("deepseek",), ("devin:devin-delegate",), ("glm",)),
    # в cross автор ТЗ — devin: возврат автору законно зовёт devin:devin-delegate, а
    # devin-критика выдали бы devin:devin-check или --thinking max.
    "pipeline-full:cross": (CRITIQUE_SONNET + ("deepseek",), ("devin:devin-check", "--thinking max"),
                       ("glm",)),
    "pipeline-full:low": (("deepseek", "glm", "`pipeline-core:pipeline-critic`", "model: haiku"),
                     ("devin:devin-delegate", "pipeline-critic-low", "Sonnet low"), ()),
}
#: Кто назван критиком: во frontmatter `description` и в столбце «Критика ТЗ» README плагина.
CRITIQUE_NAMES = {
    "pipeline-full:xhigh": ("Sonnet", "DeepSeek", "GLM"),
    "pipeline-full:high": ("Sonnet", "DeepSeek", "GLM"),
    "pipeline-full:medium": ("Sonnet", "DeepSeek"),
    "pipeline-full:cross": ("Sonnet", "DeepSeek"),
    "pipeline-full:low": ("DeepSeek", "GLM", "Haiku"),
}
CRITIQUE_OLD_DESCRIPTION = "GLM 5.3 Flash и DeepSeek V4.1 Flash"
CRITIQUE_README_COLUMN = "Критика ТЗ"
CRITIC_LABELS = {"full-xhigh": "S+DS+GLM", "full-high": "S+DS+GLM",
                 "full-medium": "S+DS", "full-cross": "S+DS", "full-low": "GLM+DS+H"}
CROSS_HINT = "Devin ТЗ · Sonnet+DS критика · GLM код · Grok приёмка"


def _description(text: str) -> str:
    """Значение `description:` из frontmatter; пусто — нет."""
    frontmatter = _frontmatter(text.splitlines()) or []
    return next((line for line in frontmatter if line.startswith("description:")), "")


def _critique_preset_problems(name: str, text: str) -> list[str]:
    """Проблемы состава критиков в SKILL.md пресета; пусто — всё на месте."""
    required, forbidden, forbidden_ci = CRITIQUE_COMPOSITION[name]
    stage2 = _stage2_section(text)
    if not stage2:
        return [f"{name}: нет раздела «### 2.»"]
    problems = [f"{name}: в этапе 2 нет {needle!r}"
                for needle in ("кворум", CRITIQUE_QUORUM_STOP, *required) if needle not in stage2]
    problems.extend(f"{name}: в этапе 2 есть {needle!r}" for needle in forbidden if needle in stage2)
    problems.extend(f"{name}: в этапе 2 есть {needle!r}" for needle in forbidden_ci
                    if needle in stage2.lower())
    description = _description(text)
    problems.extend(f"{name}: description не называет {needle!r}"
                    for needle in CRITIQUE_NAMES[name] if needle not in description)
    if CRITIQUE_OLD_DESCRIPTION in description:
        problems.append(f"{name}: в description старое {CRITIQUE_OLD_DESCRIPTION!r}")
    if name == "pipeline-full:medium" and "GLM" in description:
        problems.append(f"{name}: в description есть 'GLM'")
    if name == "pipeline-full:low" and _has_spare_critic(text):
        problems.append(f"{name}: есть «запасной критик»")
    return problems


ROLES_DOC = pathlib.Path("references") / "ROLES.md"
ROLES_CRITIQUE_PREFIX = "### Критика — состав по пресету"
XHIGH_HEADING = "# Конвейер pipeline-full:xhigh"


def _xhigh_intro(text: str) -> str:
    """Первый абзац после заголовка xhigh: от первой непустой строки до следующей пустой."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line == XHIGH_HEADING), None)
    if start is None:
        return ""
    first = next((i for i in range(start + 1, len(lines)) if lines[i].strip()), len(lines))
    end = next((i for i in range(first, len(lines)) if not lines[i].strip()), len(lines))
    return "\n".join(lines[first:end])


def _xhigh_intro_problems(text: str) -> list[str]:
    """xhigh только у Opus: в первом абзаце роли без «Каждая роль на xhigh»."""
    paragraph = _xhigh_intro(text)
    problems = ["xhigh: есть 'Каждая роль на xhigh'"] if "Каждая роль на xhigh" in text else []
    problems.extend(f"xhigh: в первом абзаце нет {needle!r}"
                    for needle in ("Opus 5.5", "Sonnet 5.5 high") if needle not in paragraph)
    return problems


def _critique_readme_problems(plugin: str, text: str) -> list[str]:
    """Столбец «Критика ТЗ» таблицы «Скилы» README плагина называет состав его пресетов."""
    lines = text.splitlines()
    header = next((line for line in lines if line.startswith("| Скил |")), "")
    columns = [cell.strip() for cell in header.strip("|").split("|")]
    if CRITIQUE_README_COLUMN not in columns:
        return [f"README: в таблице «Скилы» нет столбца {CRITIQUE_README_COLUMN!r}"]
    index = columns.index(CRITIQUE_README_COLUMN)
    problems: list[str] = []
    for name, needles in CRITIQUE_NAMES.items():
        if not name.startswith(f"{plugin}:"):
            continue
        row = next((line for line in lines if line.startswith(f"| `{_skill_dir_name(name)}` ")), None)
        if row is None:
            problems.append(f"README: нет строки {name}")
            continue
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        cell = cells[index] if index < len(cells) else ""
        problems.extend(f"README: {name}, «Критика ТЗ» не называет {needle!r}"
                        for needle in needles if needle not in cell)
        if "GLM" not in needles and "GLM" in cell:
            problems.append(f"README: {name}, «Критика ТЗ» называет 'GLM'")
    return problems


class FeaturePipelineCritiqueCompositionTests(unittest.TestCase):
    """Свой состав критиков в каждом пресете, кворум по ядру (listik-6rp9, порция b)."""

    def test_compositions_in_place(self) -> None:
        for name in CRITIQUE_PRESETS:
            with self.subTest(skill=name):
                self.assertEqual(_critique_preset_problems(name, _skill_text(name)), [])
        for plugin in sorted({name.split(":", 1)[0] for name in CRITIQUE_NAMES}):
            with self.subTest(readme=plugin):
                self.assertEqual(_critique_readme_problems(plugin, _plugin_text(_readme(plugin))), [])

    def test_composition_checks_reject_broken_texts(self) -> None:
        texts = {name: _skill_text(name) for name in CRITIQUE_PRESETS}

        def in_stage2(name: str, edit) -> tuple[str, str]:
            text = texts[name]
            stage2 = _stage2_section(text)
            self.assertTrue(stage2, f"{name}: нет раздела «### 2.»")
            return name, text.replace(stage2, edit(stage2), 1)

        broken = {
            "medium + --channel glm": in_stage2("pipeline-full:medium", lambda s: s + "\n--channel glm"),
            "high без model: sonnet": in_stage2("pipeline-full:high",
                                                lambda s: s.replace("model: sonnet", "")),
            "low + devin:devin-delegate": in_stage2("pipeline-full:low",
                                                    lambda s: s + "\ndevin:devin-delegate"),
            "medium + devin:devin-delegate": in_stage2("pipeline-full:medium",
                                                       lambda s: s + "\ndevin:devin-delegate"),
            "cross + GLM 5.3 Flash": in_stage2("pipeline-full:cross", lambda s: s + "\nGLM 5.3 Flash"),
            "medium без «кворум»": in_stage2("pipeline-full:medium", lambda s: s.replace("кворум", "")),
            "low + pipeline-critic-low": in_stage2(
                "pipeline-full:low", lambda s: s + "\n`pipeline-core:pipeline-critic-low`"),
            "low: в таблицу Роли дописан запасной критик": (
                "pipeline-full:low", texts["pipeline-full:low"].replace(
                    "| 2. Критика ТЗ |", "| 2. Критика ТЗ, запасной критик Sonnet |", 1)),
        }
        for case, (name, mutated) in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, texts[name], "изменение не применилось")
                self.assertNotEqual(_critique_preset_problems(name, mutated), [])

    def test_roles_critique_has_no_spare_critic(self) -> None:
        text = _plugin_text(ROLES_DOC)
        section = _text_block(text, ROLES_CRITIQUE_PREFIX, lambda line: line.startswith("#"))
        self.assertTrue(section, f"{ROLES_DOC}: нет раздела {ROLES_CRITIQUE_PREFIX!r}")
        self.assertFalse(_has_spare_critic(section), f"{ROLES_DOC}: есть «запасной критик»")
        self.assertNotIn("pipeline-critic-low", section)

    def test_xhigh_first_paragraph(self) -> None:
        text = _skill_text("pipeline-full:xhigh")
        self.assertEqual(_xhigh_intro_problems(text), [])
        paragraph = _xhigh_intro(text)
        mutated = text.replace(paragraph, paragraph.replace("Роли:", "Каждая роль на xhigh:", 1), 1)
        self.assertNotEqual(mutated, text, "изменение не применилось")
        self.assertNotEqual(_xhigh_intro_problems(mutated), [])

    def test_routes_critic_cells(self) -> None:
        routes = {record["key"]: record for record in _routes()}
        for name, label in CRITIC_LABELS.items():
            cell = routes[name]["roles"]["critic"]
            with self.subTest(route=name):
                self.assertEqual(cell["label"], label)
                if name == "full-low":
                    self.assertEqual(cell.get("skill"), "pi:pi-delegate")
                else:
                    self.assertEqual(cell["provider"], "claude")
                    self.assertNotIn("skill", cell)
        self.assertEqual(routes["full-cross"]["hint"], CROSS_HINT)


#: Старые имена плагинов и пресетов до listik-d9rj (порция c): в текстах плагинов их быть не должно.
OLD_NAMES_RE = re.compile(
    r"feature-pipeline|claude-codex|(xhigh|high|medium|low|xlow|nano|cross|sol|opus|claude)-pipeline")
#: Исторические записи и сгенерированные данные (fnmatch от корня `plugins/`): старые имена в них законны.
OLD_NAMES_EXCLUDED = (
    "pipeline-core/references/ROLES.md",
    "pipeline-core/references/presets.py",
    "pipeline-core/references/presets-2026-09-24.md",
    "pipeline-core/references/models.*",
    "pipeline-core/references/openrouter.*",
    "pipeline-core/references/fetch_aa.py",
    "pipeline-core/references/fetch_openrouter.py",
)


def _old_name_hits(root: pathlib.Path) -> list[tuple[str, int]]:
    """(путь от `root`, номер строки) каждой строки со старым именем; бинарные файлы и исключения — мимо."""
    hits: list[tuple[str, int]] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if not path.is_file() or any(fnmatch.fnmatch(relative, mask) for mask in OLD_NAMES_EXCLUDED):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits.extend((relative, number) for number, line in enumerate(text.splitlines(), 1)
                    if OLD_NAMES_RE.search(line))
    return hits


#: Префикс одного пресетного плагина в текстах другого: (каталог от `plugins/`, чужой префикс).
FOREIGN_PREFIXES = (("pipeline-cc", "pipeline-full:"), ("pipeline-full/skills", "pipeline-cc:"))
#: Строки, которые прямо сравнивают с пресетом другого плагина: (путь от `plugins/`, подстрока строки).
FOREIGN_PREFIX_ALLOWED = ()


def _foreign_prefix_hits(root: pathlib.Path) -> list[tuple[str, int]]:
    """Строки с префиксом чужого пресетного плагина вне FOREIGN_PREFIX_ALLOWED."""
    hits: list[tuple[str, int]] = []
    for subdir, prefix in FOREIGN_PREFIXES:
        for path in sorted((root / subdir).rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            hits.extend((relative, number) for number, line in enumerate(lines, 1)
                        if prefix in line and not any(relative == allowed and needle in line
                                                      for allowed, needle in FOREIGN_PREFIX_ALLOWED))
    return hits


HOOK_SCRIPT = CORE_PLUGIN_DIR / "hooks" / "approve-pipeline-agents.py"
HOOKS_JSON = CORE_PLUGIN_DIR / "hooks" / "hooks.json"
HOOK_AGENT_PREFIX = "pipeline-core:pipeline-"
CORE_MANIFEST = CORE_PLUGIN_DIR / PLUGIN_MANIFEST
#: AGENTS хука после listik-d9rj, порция f: исполнители и два судьи, без `*-inherit`.
HOOK_AGENTS_EXPECTED = {f"pipeline-core:{name}" for name in (
    "pipeline-implementer", "pipeline-implementer-high", "pipeline-implementer-xhigh",
    "pipeline-implementer-low", "pipeline-judge", "pipeline-judge-xhigh")}


def _hook_descriptions() -> dict[str, str]:
    """Описания, которые перечисляют агентов хука: `hooks.json` и настройка `auto_approve_agents`."""
    return {
        "hooks.json": _read_json(HOOKS_JSON)["description"],
        "plugin.json": _read_json(CORE_MANIFEST)["userConfig"]["auto_approve_agents"]["description"],
    }


def _hook_agents() -> set[str]:
    """Множество `AGENTS` хука — литерал из исходника, без исполнения хука."""
    tree = ast.parse(HOOK_SCRIPT.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "AGENTS" for t in node.targets):
            return set(ast.literal_eval(node.value))
    return set()


def _hook_agent_problems(agents: set[str], description: str) -> list[str]:
    """Имена агентов хука: префикс `pipeline-core:pipeline-`, файл агента есть, description их называет.

    И обратно (listik-d9rj, порция f): исполнители и судьи (`pipeline-implementer…`, `pipeline-judge…`, имя
    целиком, без `pipeline-core:`), названные в description, — ровно короткие имена `agents`; `inherit` нет.
    """
    named = {token.removeprefix("pipeline-core:") for token in re.findall(r"[\w:-]+", description)}
    problems: list[str] = []
    extra = sorted({token for token in named if re.fullmatch(r"pipeline-(implementer|judge)[\w-]*", token)}
                   - {name.split(":", 1)[-1] for name in agents})
    problems.extend(f"{token}: description называет агента, которого нет в AGENTS" for token in extra)
    if "inherit" in description:
        problems.append("в description есть 'inherit'")
    for name in sorted(agents):
        short = name.split(":", 1)[-1]
        if not name.startswith(HOOK_AGENT_PREFIX):
            problems.append(f"{name}: префикс не {HOOK_AGENT_PREFIX!r}")
        elif not (CORE_PLUGIN_DIR / AGENTS_SUBDIR / f"{short}.md").is_file():
            problems.append(f"{name}: нет файла агента {short}.md")
        if short not in named:
            problems.append(f"{name}: description его не называет")
    return problems


def _self_name_problems(name: str, text: str) -> list[str]:
    """Пресет `<плагин>:<уровень>` называет себя новым именем: заголовок, метка, команда, description."""
    plugin, level = name.split(":", 1)
    key = f"{plugin.removeprefix('pipeline-')}-{level}"
    problems = [f"{name}: нет {needle!r}" for needle in
                (f"# Конвейер {name}\n", f"`process:{key}`", f"`/{name}`") if needle not in text]
    frontmatter = _frontmatter(text.splitlines()) or []
    description = next((line for line in frontmatter if line.startswith("description:")), "")
    if name not in description:
        problems.append(f"{name}: description не называет {name!r}")
    return problems


LISTIK_SKILL = "plugins/listik/skills/listik/SKILL.md"
LISTIK_SKILL_REQUIRED = ("`pipeline-<p>:<уровень>`", "`full-xlow` → `pipeline-full:xlow`",
                         "`cc-high` → `pipeline-cc:high`", "`claude-high` → `pipeline-claude:high`")
LISTIK_SKILL_FORBIDDEN = ("feature-pipeline", "claude-codex", "*-pipeline")


class PipelineNamesTests(unittest.TestCase):
    """Имена плагинов и пресетов после listik-d9rj (порция c): старых нет, префиксы не перепутаны."""

    def _copy_plugins(self) -> pathlib.Path:
        tmp = pathlib.Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / "plugins"
        shutil.copytree(PLUGINS_DIR, root)
        return root

    def test_no_old_names(self) -> None:
        self.assertEqual(_old_name_hits(PLUGINS_DIR), [])
        self.assertEqual(_old_name_hits(MARKETPLACE_JSON.parent), [])

    def test_old_names_check_rejects_old_name(self) -> None:
        line = "\nзапуск: /feature-pipeline:high-pipeline\n"
        root = self._copy_plugins()
        medium = root / "pipeline-cc" / "skills" / "medium" / SKILL_FILE
        medium.write_text(medium.read_text(encoding="utf-8") + line, encoding="utf-8")
        # Имя плагина без имени пресета рядом: ловится только альтернативой `feature-pipeline`.
        xlow = root / "pipeline-cc" / "skills" / "xlow" / SKILL_FILE
        xlow.write_text(xlow.read_text(encoding="utf-8") + "\nплагин `feature-pipeline`\n", encoding="utf-8")
        hits = {path for path, _ in _old_name_hits(root)}
        self.assertIn("pipeline-cc/skills/medium/SKILL.md", hits)
        self.assertIn("pipeline-cc/skills/xlow/SKILL.md", hits)
        root = self._copy_plugins()
        roles = root / "pipeline-core" / "references" / "ROLES.md"
        roles.write_text(roles.read_text(encoding="utf-8") + line, encoding="utf-8")
        self.assertEqual(_old_name_hits(root), [], "исключение ROLES.md не сработало")

    def test_no_foreign_preset_prefix(self) -> None:
        self.assertEqual(_foreign_prefix_hits(PLUGINS_DIR), [])

    def test_foreign_prefix_check_rejects_mixup(self) -> None:
        root = self._copy_plugins()
        nano = root / "pipeline-cc" / "skills" / "nano" / SKILL_FILE
        nano.write_text(nano.read_text(encoding="utf-8") + "\nкак в pipeline-full:xlow\n", encoding="utf-8")
        low = root / "pipeline-full" / "skills" / "low" / SKILL_FILE
        low.write_text(low.read_text(encoding="utf-8") + "\nкак в pipeline-cc:low\n", encoding="utf-8")
        self.assertEqual({path for path, _ in _foreign_prefix_hits(root)},
                         {"pipeline-cc/skills/nano/SKILL.md", "pipeline-full/skills/low/SKILL.md"})

    def test_no_agent_prefix_of_preset_plugin(self) -> None:
        pattern = re.compile(r"pipeline-(full|cc|claude):pipeline-")
        for path in sorted(PLUGINS_DIR.rglob("*.md")):
            with self.subTest(file=str(path.relative_to(PLUGINS_DIR))):
                self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")))

    def test_hook_agents(self) -> None:
        agents = _hook_agents()
        self.assertTrue(agents, f"{HOOK_SCRIPT}: не нашлось множества AGENTS")
        for source, description in _hook_descriptions().items():
            with self.subTest(description=source):
                self.assertEqual(_hook_agent_problems(agents, description), [])

    def test_hook_agents_exactly_seven(self) -> None:
        """listik-d9rj, порция f: AGENTS хука — ровно семь имён, без `*-inherit`."""
        self.assertEqual(_hook_agents(), HOOK_AGENTS_EXPECTED)

    def test_hook_agents_check_rejects_extra_and_inherit(self) -> None:
        agents, description = _hook_agents(), _read_json(HOOKS_JSON)["description"]
        for case, broken in {
            "лишний агент в description": description.replace("pipeline-judge-xhigh",
                                                              "pipeline-judge-xhigh, pipeline-judge-inherit"),
            "агент description без пары в AGENTS": description + " pipeline-implementer-max",
        }.items():
            with self.subTest(case=case):
                self.assertNotEqual(broken, description, "изменение не применилось")
                self.assertNotEqual(_hook_agent_problems(agents, broken), [])
        self.assertNotEqual(_hook_agent_problems(agents | {"pipeline-core:pipeline-judge-inherit"}, description), [])

    def test_hook_agents_check_rejects_foreign_prefix(self) -> None:
        agents, description = _hook_agents(), _read_json(HOOKS_JSON)["description"]
        for bad in ("claude-codex:pipeline-implementer-low", "pipeline-cc:pipeline-implementer-low"):
            with self.subTest(agent=bad):
                problems = _hook_agent_problems(agents | {bad}, description)
                self.assertTrue(any(bad in problem for problem in problems), problems)

    def test_presets_name_themselves(self) -> None:
        names = sorted(_all_skill_names())
        self.assertTrue(names)
        for name in names:
            with self.subTest(skill=name):
                self.assertEqual(_self_name_problems(name, _skill_text(name)), [])

    def test_self_name_check_rejects_old_heading(self) -> None:
        text = _skill_text("pipeline-full:high")
        broken = text.replace("# Конвейер pipeline-full:high\n", "# Конвейер high\n", 1)
        self.assertNotEqual(broken, text, "изменение не применилось")
        self.assertNotEqual(_self_name_problems("pipeline-full:high", broken), [])

    def test_listik_skill_routes_presets(self) -> None:
        text = (REPO_DIR / LISTIK_SKILL).read_text(encoding="utf-8")
        for needle in LISTIK_SKILL_REQUIRED:
            with self.subTest(required=needle):
                self.assertIn(needle, text)
        for needle in LISTIK_SKILL_FORBIDDEN:
            with self.subTest(forbidden=needle):
                self.assertNotIn(needle, text)



#: Состав пресетов pipeline-claude (listik-d9rj, порция f): агент роли (`pipeline-core:<имя>`) → `model` в вызове.
CLAUDE_COLUMNS = {
    "pipeline-claude:high": {
        "pipeline-spec-writer": ("opus",),
        "pipeline-critic": ("sonnet", "opus"),
        "pipeline-implementer-high": ("opus",),
        "pipeline-lens": ("sonnet",),
        "pipeline-judge": ("sonnet",),
    },
    "pipeline-claude:xhigh": {
        "pipeline-spec-writer-xhigh": ("opus",),
        "pipeline-critic-xhigh": ("sonnet", "opus"),
        "pipeline-implementer-xhigh": ("opus",),
        "pipeline-lens-xhigh": ("sonnet",),
        "pipeline-judge-xhigh": ("sonnet",),
    },
}
CLAUDE_SELF_NAMES = {
    "pipeline-claude:high": ("name: high", "process:claude-high", "/pipeline-claude:high"),
    "pipeline-claude:xhigh": ("name: xhigh", "process:claude-xhigh", "/pipeline-claude:xhigh"),
}
EFFORT_PARAGRAPH_START = "**Усилие.**"
EFFORT_PARAGRAPH_REQUIRED = ("frontmatter", "--effort")
EFFORT_OUTSIDE_FORBIDDEN = ("усилие сессии", "--effort", "/effort", "наследует")
EFFORT_JOURNAL_LINE = "шаг <id>: усилие"
INHERIT_MARK = "-inherit"


def _claude_composition_problems(name: str, text: str) -> list[str]:
    """Состав пресета pipeline-claude: пары «агент — model» в строках «## Роли», чужих агентов и пресета нет."""
    rows = [line for line in _text_section(text, "## Роли").splitlines() if line.startswith("| ")]
    problems: list[str] = []
    for agent, models in CLAUDE_COLUMNS[name].items():
        pattern = _whole_name(f"pipeline-core:{agent}")
        agent_rows = [row for row in rows if pattern.search(row)]
        if not agent_rows:
            problems.append(f"{name}: в «## Роли» нет строки с pipeline-core:{agent}")
        problems.extend(f"{name}: строка pipeline-core:{agent} без 'model: {model}'" for model in models
                        if agent_rows and not any(f"model: {model}" in row for row in agent_rows))
    for other, agents in CLAUDE_COLUMNS.items():
        if other == name:
            continue
        if _whole_name(other).search(text):
            problems.append(f"{name}: называет пресет {other}")
        problems.extend(f"{name}: называет агента {agent} пресета {other}" for agent in agents
                        if _whole_name(agent).search(text))
    return problems


def _effort_paragraph_bounds(lines: list[str]) -> tuple[int, int] | None:
    """[начало, конец) абзаца «Усилие.»: строка с `**Усилие.**` и до ближайшей пустой."""
    start = next((i for i, line in enumerate(lines) if line.startswith(EFFORT_PARAGRAPH_START)), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(lines)) if not lines[i].strip()), len(lines))
    return start, end


def _claude_effort_problems(name: str, text: str) -> list[str]:
    """Усилие ролей — из агентов: `inherit` нет, про сессию и `--effort` — только абзац «Усилие.»."""
    problems = [f"{name}: есть 'inherit'"] if "inherit" in text else []
    lines = text.splitlines()
    bounds = _effort_paragraph_bounds(lines)
    if bounds is None:
        return problems + [f"{name}: нет абзаца {EFFORT_PARAGRAPH_START!r}"]
    paragraph = "\n".join(lines[bounds[0]:bounds[1]])
    outside = "\n".join(lines[:bounds[0]] + lines[bounds[1]:]).lower()
    problems.extend(f"{name}: в абзаце «Усилие.» нет {needle!r}" for needle in EFFORT_PARAGRAPH_REQUIRED
                    if needle not in paragraph)
    problems.extend(f"{name}: вне абзаца «Усилие.» есть {needle!r}" for needle in EFFORT_OUTSIDE_FORBIDDEN
                    if needle in outside)
    if EFFORT_JOURNAL_LINE in text:
        problems.append(f"{name}: есть строка журнала {EFFORT_JOURNAL_LINE!r}")
    return problems


def _claude_xhigh_from_high(text: str) -> str:
    """Правило п. 3.2: имена агентов high-колонки → xhigh-колонки (целиком), затем слово `high` → `xhigh`."""
    pairs = zip(CLAUDE_COLUMNS["pipeline-claude:high"], CLAUDE_COLUMNS["pipeline-claude:xhigh"])
    for high, xhigh in pairs:
        text = _whole_name(high).sub(xhigh, text)
    return re.sub(r"(?<!\w)high(?!\w)", "xhigh", text)


def _claude_parity_problems(high: str, xhigh: str) -> list[str]:
    """Расхождение xhigh/SKILL.md с high/SKILL.md после правила п. 3.2; пусто — тексты равны."""
    expected = _claude_xhigh_from_high(high)
    if expected == xhigh:
        return []
    got, want = xhigh.splitlines(), expected.splitlines()
    index = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), min(len(got), len(want)))
    return [f"xhigh/SKILL.md расходится с high/SKILL.md со строки {index + 1}"]


def _agent_parts(name: str) -> tuple[list[str], str]:
    """Frontmatter агента строками и тело — всё после второй строки `---`, побайтно."""
    text = _plugin_text(pathlib.Path(AGENTS_SUBDIR) / name)
    frontmatter = _frontmatter(text.splitlines())
    if frontmatter is None:
        raise AssertionError(f"{name}: frontmatter не закрыт строкой ---")
    head = "---\n" + "".join(line + "\n" for line in frontmatter) + "---\n"
    if not text.startswith(head):
        raise AssertionError(f"{name}: frontmatter не разобрался")
    return frontmatter, text[len(head):]


def _inherit_hits(root: pathlib.Path) -> list[str]:
    """Аналог `git grep -- -inherit -- plugins`: файлы под `root` с подстрокой `-inherit` (имя или текст)."""
    hits: list[str] = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if INHERIT_MARK in relative:
            hits.append(relative)
            continue
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if INHERIT_MARK in text:
            hits.append(relative)
    return hits


class PipelineClaudePresetsTests(unittest.TestCase):
    """Пресеты pipeline-claude:high и pipeline-claude:xhigh: усилие ролей — из агентов (listik-d9rj, порция f)."""

    def test_skills_present(self) -> None:
        self.assertEqual(sorted(path.name for path in (PLUGINS_DIR / "pipeline-claude" / SKILLS_SUBDIR).iterdir()
                                if path.is_dir()), ["high", "xhigh"])

    def test_composition(self) -> None:
        for name in CLAUDE_COLUMNS:
            with self.subTest(skill=name):
                self.assertEqual(_claude_composition_problems(name, _skill_text(name)), [])

    def test_composition_check_rejects_foreign_agent(self) -> None:
        text = _skill_text("pipeline-claude:xhigh")
        broken = text.replace("pipeline-core:pipeline-critic-xhigh", "pipeline-core:pipeline-critic", 1)
        self.assertNotEqual(broken, text, "изменение не применилось")
        self.assertNotEqual(_claude_composition_problems("pipeline-claude:xhigh", broken), [])

    def test_self_names(self) -> None:
        for name, needles in CLAUDE_SELF_NAMES.items():
            text = _skill_text(name)
            frontmatter = _frontmatter(text.splitlines()) or []
            for needle in needles:
                with self.subTest(skill=name, needle=needle):
                    if needle.startswith("name: "):
                        self.assertIn(needle, frontmatter)
                    else:
                        self.assertRegex(text, _whole_name(needle))

    def test_effort_from_agents(self) -> None:
        for name in CLAUDE_COLUMNS:
            with self.subTest(skill=name):
                self.assertEqual(_claude_effort_problems(name, _skill_text(name)), [])

    def test_effort_check_rejects_session_effort(self) -> None:
        text = _skill_text("pipeline-claude:high")
        broken = text.replace("## Роли\n", "## Роли\n\nУсилие ролей — усилие сессии.\n", 1)
        self.assertNotEqual(broken, text, "изменение не применилось")
        self.assertNotEqual(_claude_effort_problems("pipeline-claude:high", broken), [])

    def test_xhigh_is_high_with_xhigh_agents(self) -> None:
        high, xhigh = _skill_text("pipeline-claude:high"), _skill_text("pipeline-claude:xhigh")
        self.assertEqual(_claude_parity_problems(high, xhigh), [])

    def test_parity_check_rejects_missing_stage2_line(self) -> None:
        high, xhigh = _skill_text("pipeline-claude:high"), _skill_text("pipeline-claude:xhigh")
        lines = xhigh.split("\n")
        stage2 = _stage2_section(xhigh).split("\n")
        index = lines.index(stage2[2])
        broken = "\n".join(lines[:index] + lines[index + 1:])
        self.assertNotEqual(broken, xhigh, "изменение не применилось")
        self.assertNotEqual(_claude_parity_problems(high, broken), [])


class PipelineClaudeAgentsTests(unittest.TestCase):
    """Агенты pipeline-core после listik-d9rj, порция f: усилие во frontmatter, копии xhigh, без `*-inherit`."""

    def test_every_agent_has_effort(self) -> None:
        paths = _agent_paths()
        self.assertTrue(paths)
        for relative in paths:
            with self.subTest(agent=relative.name):
                frontmatter = _frontmatter(_plugin_text(relative).splitlines()) or []
                self.assertTrue(any(line.startswith("effort:") for line in frontmatter),
                                f"{relative}: во frontmatter нет effort")

    def test_xhigh_copies_keep_body(self) -> None:
        for copy, original in (("pipeline-lens-xhigh.md", "pipeline-lens.md"),
                               ("pipeline-judge-xhigh.md", "pipeline-judge.md")):
            with self.subTest(agent=copy):
                self.assertEqual(_agent_parts(copy)[1], _agent_parts(original)[1])

    def test_lens_and_judge_frontmatter(self) -> None:
        expected = {
            "pipeline-lens.md": ("name: pipeline-lens", "model: sonnet", "effort: high"),
            "pipeline-lens-xhigh.md": ("name: pipeline-lens-xhigh", "model: sonnet", "effort: xhigh"),
            "pipeline-judge-xhigh.md": ("name: pipeline-judge-xhigh", "model: sonnet", "effort: xhigh",
                                        "skills:", "  - listik:listik"),
        }
        descriptions = {
            "pipeline-lens.md": ("Модель — Sonnet, усилие high", "уровень xhigh — pipeline-lens-xhigh",
                                 "Зовётся только по имени из пресетов pipeline-claude"),
            "pipeline-lens-xhigh.md": ("усилие xhigh", "Зовётся только по имени из пресетов pipeline-claude"),
            "pipeline-judge-xhigh.md": ("pipeline-claude:xhigh", "Sonnet", "усилие xhigh"),
        }
        for name, lines in expected.items():
            frontmatter = _agent_parts(name)[0]
            description = next((line for line in frontmatter if line.startswith("description:")), "")
            for line in lines:
                with self.subTest(agent=name, line=line):
                    self.assertIn(line, frontmatter)
            for needle in descriptions[name]:
                with self.subTest(agent=name, description=needle):
                    self.assertIn(needle, description)
            if name.startswith("pipeline-lens"):
                for needle in ("наследует", "усилие сессии", "claude-pipeline"):
                    with self.subTest(agent=name, forbidden=needle):
                        self.assertNotIn(needle, description)

    def test_no_inherit_in_plugins(self) -> None:
        self.assertEqual(_inherit_hits(PLUGINS_DIR), [])


if __name__ == "__main__":
    unittest.main()
