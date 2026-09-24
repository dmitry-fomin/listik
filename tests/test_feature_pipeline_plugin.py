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

    def test_marketplace_plugin_sources_match_manifests(self) -> None:
        entries = _read_json(MARKETPLACE_JSON)["plugins"]
        names = [entry.get("name") for entry in entries]
        self.assertEqual(
            names,
            ["listik", "feature-pipeline", "dsh", "codex", "opencode", "pi", "devin",
             "second-opinion"],
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
INHERIT_SKILL = pathlib.Path("skills") / "inherit-pipeline" / SKILL_FILE
FEATURE_SKILL = pathlib.Path("skills") / "feature-pipeline" / SKILL_FILE
AGENTS_SUBDIR = "agents"

MANIFEST_LINE = (
    "трек <трек>: <id>, дерево <путь>, ветка task/<трек>, база <sha7>"
)


def _plugin_text(relative: pathlib.Path) -> str:
    return (PLUGIN_DIR / relative).read_text(encoding="utf-8")


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
        tracks_doc = PLUGIN_DIR / "skills" / "feature-pipeline" / "references" / "tracks.md"
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
    "high-pipeline",
    "inherit-pipeline",
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

#: Ссылка на ядро, которую держит каждый SKILL.md пресета.
CORE_LINK = "[pipeline-core.md](../../references/pipeline-core.md)"

#: Пресет, который ссылается на ядро разделом Listik, структура своя.
PRESETS_WITHOUT_CORE_LINK = ("opus-sonnet-pipeline",)


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
            "[pipeline-core.md](../../references/pipeline-core.md)",
            "Ниже — только то, чем inherit-pipeline отличается",
            "process:inherit-pipeline",
            "## Роли",
            "## Нужные скилы",
            "## Префикс",
            "STEPS=<paths.steps",
            "BASE=<id>",
            "WT=",
            "feature-pipeline:pipeline-spec-writer",
            "feature-pipeline:pipeline-critic",
            "feature-pipeline:pipeline-implementer",
            "feature-pipeline:pipeline-judge",
            "пресет inherit-pipeline",
            "judge_preflight",
            "VERDICT: PASS",
            "VERDICT: FAIL",
            'git -C "$WT" log --oneline -1',
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"inherit-pipeline/{SKILL_FILE}: не нашлось обязательной подстроки {needle!r}",
                )

    def test_inherit_pipeline_has_no_core_copies(self) -> None:
        """Пресет не держит своей копии ядра: ни шага 0, ни треков, ни пределов, ни общих граблей."""
        text = _skill_text("inherit-pipeline")
        forbidden = [
            "## Жёсткие правила",
            "**Цикл.**",
            "## Протокол вопросов",
            "## Шаг 0",
            "## Треки",
            "## Пределы на порцию",
            "## Журнал",
            "### Правила треков",
            "### Сведение и уборка",
            "listik worktree",
            "merge --no-ff",
            "git worktree remove",
            "add -A -N",
            "diff HEAD -U10",
            "reset -q",
            "M ≤ 2",
            MANIFEST_LINE,
            ADHOC_PARAGRAPH_START,
            "Названная правка в одном-двух файлах",
        ]
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(
                    needle, text,
                    f"inherit-pipeline/{SKILL_FILE}: осталась копия ядра — {needle!r}",
                )
        with self.subTest(needle="## Грабли"):
            self.assertIsNone(
                re.search(r"^## Грабли$", text, re.MULTILINE),
                f"inherit-pipeline/{SKILL_FILE}: остался заголовок общих граблей ядра "
                "«## Грабли» (у пресета бывает только «## Грабли пресета»)",
            )

    def test_presets_read_core(self) -> None:
        """Каждый пресет отсылает к ядру ссылкой на pipeline-core.md."""
        for name in sorted(_skill_names()):
            if name in PRESETS_WITHOUT_CORE_LINK:
                continue
            with self.subTest(skill=name):
                self.assertIn(
                    CORE_LINK, _skill_text(name),
                    f"{name}/{SKILL_FILE}: нет ссылки на ядро {CORE_LINK!r}",
                )

    def test_feature_pipeline_naming(self) -> None:
        text = _skill_text("feature-pipeline")
        required = [
            "[pipeline-core.md](../../references/pipeline-core.md)",
            "Ниже — только то, чем feature-pipeline отличается",
            "process:feature-pipeline",
            "## Роли",
            "## Конфиг пресета",
            "## Нужные скилы",
            "## Префикс",
            "JOB",
            "executor.primary",
            "executor.fallback",
            "executor.local",
            "on_fallback",
            "second_opinion.when",
            "spec-only",
            "feature-pipeline:pipeline-spec-writer",
            "feature-pipeline:pipeline-implementer",
            "feature-pipeline:pipeline-judge",
            "--no-system",
            "VERDICT: PASS",
            "VERDICT: FAIL",
            "фолбэк",
            'git -C "$WT" log --oneline -1',
        ]
        for needle in required:
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"feature-pipeline/{SKILL_FILE}: не нашлось обязательной подстроки {needle!r}",
                )

    def test_feature_pipeline_has_no_core_copies(self) -> None:
        """Пресет не держит своей копии ядра: ни шага 0, ни пределов, ни общих граблей."""
        text = _skill_text("feature-pipeline")
        lowered = text.lower()
        forbidden = [
            "pipeline-<",
            "EnterWorktree",
            "--ff-only",
            "git worktree add",
            "grok:grok-delegate",
            "dsh:dsh-runner",
            "tracks.md",
            "## Жёсткие правила",
            "## Пределы на порцию",
            "## Формат отчёта",
            "Сколько раз можно",
            "полный круг",
            "листинги, логи, цитаты кода",
            "paths.tz",
            "add -A -N",
            "diff HEAD -U10",
            "reset -q",
            "disable-model-invocation",
            "синхронный",
            "SECOND_OPINION_NO_SYSTEM",
        ]
        for needle in forbidden:
            with self.subTest(needle=needle):
                self.assertNotIn(
                    needle, text,
                    f"feature-pipeline/{SKILL_FILE}: осталась копия ядра — {needle!r}",
                )
        with self.subTest(needle="разведк"):
            self.assertNotIn(
                "разведк", lowered,
                f"feature-pipeline/{SKILL_FILE}: остался класс задачи «разведка», "
                "которого у пресета нет",
            )

    def test_config_example_names_readers(self) -> None:
        """Образец конфига называет каналы именами каналов и честно — своих читателей."""
        path = PLUGIN_DIR / "skills" / "feature-pipeline" / "config.example.yaml"
        text = path.read_text(encoding="utf-8")
        for needle in (
            "primary: grok",
            "fallback: dsh",
            "local: feature-pipeline:pipeline-implementer",
            "feature-pipeline",
            "opus-sonnet-pipeline",
        ):
            with self.subTest(needle=needle):
                self.assertIn(
                    needle, text,
                    f"{path.name}: не нашлось обязательной подстроки {needle!r}",
                )
        for needle in ("grok:grok-delegate", "dsh:dsh-runner", "пресетами не читаются"):
            with self.subTest(needle=needle):
                self.assertNotIn(
                    needle, text,
                    f"{path.name}: осталась устаревшая подстрока {needle!r}",
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

    def test_no_pipeline_branch_prefix(self) -> None:
        """pipeline-< и git worktree add -b pipeline больше не используются."""
        for relative in (CORE_DOC, INHERIT_SKILL, FEATURE_SKILL):
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
    "feature-pipeline": "SendMessage",
    "high-pipeline": "SendMessage",
    "medium-pipeline": "SendMessage",
    "inherit-pipeline": "SendMessage",
    "opus-single-pipeline": "SendMessage",
    "opus-sonnet-pipeline": "SendMessage",
    "xhigh-pipeline": "сессия <id>",
    "low-pipeline": "сессия <id>",
    "xlow-pipeline": "сессия <id>",
    "nano-pipeline": "сессия <id>",
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
}

#: Канал в пресете называется запуском; у него в тексте ещё и путь `plugins/…`.
VENDORED_LAUNCH_SKILLS = (
    "dsh:dsh-delegate",
    "codex:codex-delegate",
    "second-opinion:ask",
    "listik:listik",
    "pi:pi-delegate",
)

MD_SKILL_LINK_RE = re.compile(r"\[`(?P<name>[^`]+)`\]\((?P<href>[^)]+)\)")
VENDORED_HREF_MARKS = ("/dsh/", "/codex/", "/second-opinion/", "/listik/")
VENDORED_HREF_MARKS += ("/pi/",)


def _pipeline_docs() -> list[pathlib.Path]:
    """Ядро и SKILL.md всех пресетов — там, где оркестратор ищет внешний скил."""
    docs = [PLUGIN_DIR / CORE_DOC]
    docs.extend(
        PLUGIN_DIR / SKILLS_SUBDIR / name / SKILL_FILE
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


#: Пресеты, где этап 3 ведёт pi на канале deepseek (listik-47y8), и что обязано быть в их тексте.
PI_EXECUTOR_PRESETS = ("low-pipeline", "xlow-pipeline")
PI_EXECUTOR_REQUIRED = (
    "pi:pi-delegate",
    "--channel deepseek",
    "--holder pi-deepseek",
    '--label "$BASE <X>"',
    "--cwd $WT",
    "порция <X>: pi <job-id>",
    '--session "$BASE-<X>"',
)
PI_EXECUTOR_FORBIDDEN = ("dsh:dsh-delegate", "DJOB")


class FeaturePipelinePiExecutorTests(unittest.TestCase):
    """low/xlow: исполнитель этапа 3 — pi на канале deepseek, следов dsh нет (listik-47y8)."""

    def test_low_and_xlow_run_pi_deepseek(self) -> None:
        for name in PI_EXECUTOR_PRESETS:
            text = _skill_text(name)
            for needle in PI_EXECUTOR_FORBIDDEN:
                with self.subTest(skill=name, forbidden=needle):
                    self.assertNotIn(needle, text, f"{name}/{SKILL_FILE}: осталось {needle!r}")
            for needle in PI_EXECUTOR_REQUIRED:
                with self.subTest(skill=name, required=needle):
                    self.assertIn(needle, text, f"{name}/{SKILL_FILE}: нет {needle!r}")



#: Двухходовка пресетов без писателя ТЗ (listik-47y8, порция d): ход 1 на чтении возвращает
#: границы правки и план, ход 2 — продолжение той же сессии с правом записи.
TWO_TURN_COMMON = (
    "Ход 1",
    "--permission read",
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
    "xlow-pipeline": ("resume --session", "--channel deepseek"),
    "nano-pipeline": ("resume <job-id", "--model gpt-6-astra", "--effort high"),
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
        for path in (PLUGIN_DIR / REFERENCES_SUBDIR).glob("*.md")
    )
    paths.extend(_agent_paths())
    paths.extend(
        pathlib.Path(SKILLS_SUBDIR) / name / SKILL_FILE
        for name in sorted(_skill_names())
    )
    return paths


def _line_forbids_amend(line: str) -> bool:
    """True, если строка с `amend` содержит запрещающий маркер, а не разрешение."""
    lowered = line.lower()
    return any(marker in lowered for marker in AMEND_FORBID_MARKERS)


#: Семь `SKILL.md`, где задание судье лежит inline (listik-4n4y, порция b). Список закрытый
#: и явный: именно он — основа выбора файлов, страховочный скан ниже лишь ловит расширение.
INLINE_JUDGE_SKILLS = (
    pathlib.Path(SKILLS_SUBDIR) / "high-pipeline" / SKILL_FILE,
    pathlib.Path(SKILLS_SUBDIR) / "xhigh-pipeline" / SKILL_FILE,
    pathlib.Path(SKILLS_SUBDIR) / "medium-pipeline" / SKILL_FILE,
    pathlib.Path(SKILLS_SUBDIR) / "low-pipeline" / SKILL_FILE,
    pathlib.Path(SKILLS_SUBDIR) / "xlow-pipeline" / SKILL_FILE,
    pathlib.Path(SKILLS_SUBDIR) / "nano-pipeline" / SKILL_FILE,
    pathlib.Path(SKILLS_SUBDIR) / "devin-pipeline" / SKILL_FILE,
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

    def test_solo_implementer_states_commit_rule(self) -> None:
        relative = pathlib.Path(AGENTS_SUBDIR) / "pipeline-implementer-solo.md"
        text = _plugin_text(relative)
        required = (
            "git rm",
            "did not match any files",
            "git commit -m",
            "git show --stat --name-status",
            "$BASE..HEAD",
            "второй коммит",
            "--amend",
            "оба хеша",
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
            relative = pathlib.Path(SKILLS_SUBDIR) / name / SKILL_FILE
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
    "У `opus-single-pipeline` та же строка стоит в файле задачи.",
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


if __name__ == "__main__":
    unittest.main()
