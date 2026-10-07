"""Плагин claude-codex: пресеты конвейера на Claude + Codex (listik-r1pd, порция b).

Плагин ставится вместе с feature-pipeline, codex и listik и ничего из них не копирует: скилы
пресетов ссылаются на ядро feature-pipeline, своих агентов два — критики нового усилия, их тело
побайтно равно телу `feature-pipeline:pipeline-critic`.

Файлы читаются в момент вызова через константы модуля, а не при импорте. Набор пресетов —
словарь `PRESETS`, агенты — словарь `AGENTS`: новый пресет или агент добавляется строкой в
словарь, проверки при этом не меняются.
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / "plugins" / "claude-codex"
FEATURE_PIPELINE_DIR = REPO_DIR / "plugins" / "feature-pipeline"
MARKETPLACE_JSON = REPO_DIR / ".claude-plugin" / "marketplace.json"
SOL_SKILL = FEATURE_PIPELINE_DIR / "skills" / "sol-pipeline" / "SKILL.md"

PLUGIN_NAME = "claude-codex"
PLUGIN_MANIFEST = pathlib.Path(".claude-plugin") / "plugin.json"
SKILLS_SUBDIR = "skills"
AGENTS_SUBDIR = "agents"
SKILL_FILE = "SKILL.md"

CORE_LINK = "../../../feature-pipeline/references/pipeline-core.md"
LAUNCH_PHRASE = "Запускай только по явному имени"
JUDGE_TASK_LINE = "Задание, которое уходит в `codex:codex-delegate`:"

#: Подстроки, которые обязаны быть в каждом SKILL.md плагина; `{name}` — имя пресета.
SKILL_REQUIRED = ("Стоп-фактор", "пресет claude-codex:{name}", "`model` перебивает frontmatter",
                  "не того усилия", "согласие")

#: Подстроки, которых нет ни в одном SKILL.md плагина (без учёта регистра).
SKILL_FORBIDDEN_CI = ("pi:pi-", "devin", "grok", "dsh", "deepseek", "glm", "--provider", "--channel",
                      "линз", "/feature-pipeline:")

QUORUM_ANTHROPIC_EXTRA = "кворум — годные ответы `sonnet` и `codex`; `opus` в кворум не входит"
QUORUM_STOP = "стоп: критика — кворум не набран"
OPUS_REVIEW = "review-<X>.opus.md"

STAGE4_REQUIRED = ("codex:codex-delegate", "--model gpt-6-astra", "--effort high", "--permission write",
                   "--holder codex", "VERDICT: PASS")

#: Пресет → требования по этапам. `required` — подстроки раздела `### <n>.`, `pairs` — пары подстрок,
#: обязанные стоять в одной строке раздела, `forbidden` — подстроки, которых в разделе нет,
#: `judge_like_sol` — задание судье побайтно как в sol-pipeline.
PRESETS: dict[str, dict] = {
    "xhigh-pipeline": {
        "required": {
            1: ("`feature-pipeline:pipeline-spec-writer-xhigh`", "model: opus"),
            2: ("--model gpt-6.1-sol --effort high", OPUS_REVIEW, QUORUM_ANTHROPIC_EXTRA,
                "отменён по кворуму", QUORUM_STOP),
            3: ("feature-pipeline:pipeline-implementer-xhigh", "параметр `model` в вызове не передаётся"),
            4: STAGE4_REQUIRED,
        },
        "pairs": {
            2: (("claude-codex:pipeline-critic-xhigh", "model: sonnet"),
                ("`feature-pipeline:pipeline-critic`", "model: opus")),
        },
        "forbidden": {3: ("model: opus",)},
        "judge_like_sol": True,
    },
    "high-pipeline": {
        "required": {
            1: ("`feature-pipeline:pipeline-spec-writer`", "model: opus"),
            2: ("--model gpt-6.1-sol --effort high", OPUS_REVIEW, QUORUM_ANTHROPIC_EXTRA,
                "отменён по кворуму", QUORUM_STOP),
            4: STAGE4_REQUIRED,
        },
        "pairs": {
            2: (("`feature-pipeline:pipeline-critic`", "model: sonnet"),
                ("claude-codex:pipeline-critic-medium", "model: opus")),
            3: (("feature-pipeline:pipeline-implementer-high", "model: opus"),),
        },
        "forbidden": {},
        "judge_like_sol": True,
    },
    "medium-pipeline": {
        "required": {
            1: ("`feature-pipeline:pipeline-spec-writer-medium`", "model: opus"),
            2: ("--model gpt-6.1-sol --effort medium", "кворум — оба", QUORUM_STOP),
            4: STAGE4_REQUIRED,
        },
        "pairs": {
            2: (("claude-codex:pipeline-critic-medium", "model: sonnet"),),
            3: (("`feature-pipeline:pipeline-implementer`", "model: opus"),),
        },
        "forbidden": {2: (OPUS_REVIEW, "--effort high")},
        "judge_like_sol": True,
    },
}

#: Во всех этапах 2 нет этих подстрок.
STAGE2_FORBIDDEN_ALL = ("--permission write",)

CRITIC_SAMPLE = pathlib.Path("plugins") / "feature-pipeline" / "agents" / "pipeline-critic.md"

#: Файл агента → (model, effort, файл-образец тела относительно корня репозитория).
AGENTS: dict[str, tuple[str, str, pathlib.Path]] = {
    "pipeline-critic-xhigh.md": ("sonnet", "xhigh", CRITIC_SAMPLE),
    "pipeline-critic-medium.md": ("sonnet", "medium", CRITIC_SAMPLE),
}

#: Ссылка markdown `[текст](цель)`.
LINK_RE = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")


def _read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _skill_dirs() -> set[str]:
    skills = PLUGIN_DIR / SKILLS_SUBDIR
    return {path.name for path in skills.iterdir()
            if path.is_dir() and (path / SKILL_FILE).is_file()}


def _skill_path(name: str) -> pathlib.Path:
    return PLUGIN_DIR / SKILLS_SUBDIR / name / SKILL_FILE


def _frontmatter(lines: list[str]) -> list[str] | None:
    """Строки между первой и второй `---`; None, если рамки нет или она не закрыта."""
    if not lines or lines[0] != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index] == "---":
            return lines[1:index]
    return None


def _split_frontmatter(text: str) -> tuple[list[str] | None, str]:
    """Frontmatter и тело — всё после закрывающей `---` (включая перевод строки за ней)."""
    lines = text.split("\n")
    frontmatter = _frontmatter(lines)
    if frontmatter is None:
        return None, text
    return frontmatter, "\n".join(lines[len(frontmatter) + 2:])


def _fm_values(frontmatter: list[str], key: str) -> list[str]:
    return [line[len(key) + 1:].strip() for line in frontmatter if line.startswith(f"{key}:")]


def _stage_section(text: str, number: int) -> str:
    """Строки от начинающейся с `### <n>.` до следующего `### ` или `## ` (без неё)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(f"### {number}.")), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].startswith("### ") or lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _skill_problems(name: str, text: str, skill_dir: pathlib.Path) -> list[str]:
    """Проверка 2 и 4: frontmatter, ссылки, обязательные и запрещённые подстроки."""
    problems: list[str] = []
    frontmatter = _frontmatter(text.splitlines())
    if frontmatter is None:
        return ["нет frontmatter"]
    if _fm_values(frontmatter, "name") != [name]:
        problems.append(f"name во frontmatter не {name!r}")
    descriptions = _fm_values(frontmatter, "description")
    if len(descriptions) != 1:
        problems.append(f"строк description: {len(descriptions)}")
    else:
        value = descriptions[0]
        if not (len(value) >= 2 and value.startswith('"') and value.endswith('"')):
            problems.append("description не в двойных кавычках")
        if LAUNCH_PHRASE not in value:
            problems.append(f"в description нет {LAUNCH_PHRASE!r}")
    if CORE_LINK not in text:
        problems.append(f"нет ссылки {CORE_LINK}")
    for target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path = target.split("#", 1)[0]
        if not (skill_dir / path).exists():
            problems.append(f"ссылка ведёт в никуда: {target}")
    for needle in SKILL_REQUIRED:
        needle = needle.format(name=name)
        if needle not in text:
            problems.append(f"нет {needle!r}")
    lowered = text.lower()
    problems.extend(f"есть запрещённое {needle!r}" for needle in SKILL_FORBIDDEN_CI if needle in lowered)
    return problems


def _stage_problems(name: str, text: str) -> list[str]:
    """Проверки 4 и 5: состав по этапам и запрещённые подстроки; пусто — всё на месте."""
    spec = PRESETS[name]
    problems: list[str] = []
    for number in (1, 2, 3, 4):
        section = _stage_section(text, number)
        if not section:
            problems.append(f"нет раздела «### {number}.»")
            continue
        problems.extend(f"в «### {number}.» нет {needle!r}"
                        for needle in spec["required"].get(number, ()) if needle not in section)
        lines = section.splitlines()
        for first, second in spec["pairs"].get(number, ()):
            if not any(first in line and second in line for line in lines):
                problems.append(f"в «### {number}.» нет строки с {first!r} и {second!r}")
        forbidden = spec["forbidden"].get(number, ())
        if number == 2:
            forbidden = tuple(forbidden) + STAGE2_FORBIDDEN_ALL
        problems.extend(f"в «### {number}.» есть {needle!r}" for needle in forbidden if needle in section)
    lowered = text.lower()
    problems.extend(f"есть запрещённое {needle!r}" for needle in SKILL_FORBIDDEN_CI if needle in lowered)
    return problems


def _judge_block(text: str) -> str | None:
    """Fenced-блок сразу после строки задания судье в разделе этапа 4; None — не нашёлся."""
    lines = _stage_section(text, 4).splitlines()
    try:
        start = lines.index(JUDGE_TASK_LINE)
    except ValueError:
        return None
    rest = lines[start + 1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    if not rest or not rest[0].startswith("```"):
        return None
    for index in range(1, len(rest)):
        if rest[index].startswith("```"):
            return "\n".join(rest[:index + 1])
    return None


def _judge_problems(text: str, sample: str) -> list[str]:
    block, expected = _judge_block(text), _judge_block(sample)
    if expected is None:
        return ["в sol-pipeline нет задания судье"]
    if block is None:
        return ["нет fenced-блока задания судье в «### 4.»"]
    return [] if block == expected else ["задание судье разошлось с sol-pipeline"]


def _agent_problems(filename: str, text: str) -> list[str]:
    """Проверка 8 для одного агента; пусто — всё на месте."""
    model, effort, sample_path = AGENTS[filename]
    sample_fm, sample_body = _split_frontmatter((REPO_DIR / sample_path).read_text(encoding="utf-8"))
    frontmatter, body = _split_frontmatter(text)
    if frontmatter is None:
        return ["нет frontmatter"]
    problems: list[str] = []
    if _fm_values(frontmatter, "name") != [filename[:-len(".md")]]:
        problems.append("name не совпадает с именем файла")
    if _fm_values(frontmatter, "model") != [model]:
        problems.append(f"model не {model}")
    if _fm_values(frontmatter, "effort") != [effort]:
        problems.append(f"effort не {effort}")
    if _fm_values(frontmatter, "skills"):
        problems.append("есть skills:")
    descriptions = _fm_values(frontmatter, "description")
    if len(descriptions) != 1:
        problems.append(f"строк description: {len(descriptions)}")
    else:
        value = descriptions[0]
        if effort not in value or PLUGIN_NAME not in value:
            problems.append(f"в description нет {effort!r} или {PLUGIN_NAME!r}")
        if value.strip('"') == _fm_values(sample_fm or [], "description")[0].strip('"'):
            problems.append("description скопирован у образца")
    if body != sample_body:
        problems.append(f"тело не равно телу {sample_path}")
    return problems


class ClaudeCodexManifestTests(unittest.TestCase):
    def test_manifest_and_marketplace(self) -> None:
        manifest_path = PLUGIN_DIR / PLUGIN_MANIFEST
        self.assertTrue(manifest_path.is_file(), f"нет {manifest_path}")
        manifest = _read_json(manifest_path)
        self.assertEqual(manifest.get("name"), PLUGIN_NAME)
        for key in ("author", "homepage", "repository", "license", "keywords"):
            self.assertTrue(manifest.get(key), f"plugin.json: пустое {key}")
        entries = [e for e in _read_json(MARKETPLACE_JSON)["plugins"] if e.get("name") == PLUGIN_NAME]
        self.assertEqual(len(entries), 1, f"записей {PLUGIN_NAME} в маркетплейсе: {len(entries)}")
        entry = entries[0]
        self.assertEqual(entry.get("version"), manifest.get("version"))
        self.assertTrue((REPO_DIR / entry.get("source", "")).resolve() == PLUGIN_DIR.resolve())
        self.assertTrue(PLUGIN_DIR.is_dir())


class ClaudeCodexSkillTests(unittest.TestCase):
    def test_skill_dirs_match_presets(self) -> None:
        self.assertEqual(_skill_dirs(), set(PRESETS))

    def test_skill_texts(self) -> None:
        for name in sorted(_skill_dirs()):
            with self.subTest(skill=name):
                path = _skill_path(name)
                self.assertEqual(_skill_problems(name, path.read_text(encoding="utf-8"), path.parent), [])

    def test_skill_check_rejects_broken_texts(self) -> None:
        name = "xhigh-pipeline"
        path = _skill_path(name)
        text = path.read_text(encoding="utf-8")
        broken = {
            "битая ссылка": text + "\n[x](../nowhere.md#a)\n",
            "без согласия": text.replace("согласие", "разрешение"),
            "+ DeepSeek": text + "\nDeepSeek\n",
            "description без кавычек": re.sub(r'^description: "(.*)"$', r"description: \1", text,
                                              count=1, flags=re.M),
        }
        for case, mutated in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_skill_problems(name, mutated, path.parent), [])

    def test_stages(self) -> None:
        for name in sorted(PRESETS):
            with self.subTest(preset=name):
                self.assertEqual(_stage_problems(name, _skill_path(name).read_text(encoding="utf-8")), [])

    def test_stage_check_rejects_broken_texts(self) -> None:
        def in_stage(text: str, number: int, edit) -> str:
            section = _stage_section(text, number)
            self.assertTrue(section, f"нет раздела «### {number}.»")
            return text.replace(section, edit(section), 1)

        def swap(section: str) -> str:
            return (section.replace("model: sonnet", "\0").replace("model: opus", "model: sonnet")
                    .replace("\0", "model: opus"))

        def without_quorum(section: str) -> str:
            return section.replace(QUORUM_ANTHROPIC_EXTRA, "")

        xhigh = _skill_path("xhigh-pipeline").read_text(encoding="utf-8")
        high = _skill_path("high-pipeline").read_text(encoding="utf-8")
        broken = {
            ("xhigh-pipeline", "### 2. без --effort high"): in_stage(
                xhigh, 2, lambda s: s.replace("--effort high", "")),
            ("xhigh-pipeline", "### 4. без --permission write"): in_stage(
                xhigh, 4, lambda s: s.replace("--permission write", "")),
            ("xhigh-pipeline", "### 4. gpt-6-astra → gpt-6.1-sol"): in_stage(
                xhigh, 4, lambda s: s.replace("--model gpt-6-astra", "--model gpt-6.1-sol")),
            ("xhigh-pipeline", "+ /grok:delegate"): xhigh + "\n/grok:delegate\n",
            ("xhigh-pipeline", "+ DeepSeek"): xhigh + "\nDeepSeek\n",
            ("xhigh-pipeline", "### 2. sonnet ↔ opus"): in_stage(xhigh, 2, swap),
            ("high-pipeline", "### 2. sonnet ↔ opus"): in_stage(high, 2, swap),
            ("high-pipeline", "### 2. без фразы кворума"): in_stage(high, 2, without_quorum),
        }
        originals = {"xhigh-pipeline": xhigh, "high-pipeline": high}
        for (name, case), mutated in broken.items():
            with self.subTest(preset=name, case=case):
                self.assertNotEqual(mutated, originals[name], "изменение не применилось")
                self.assertNotEqual(_stage_problems(name, mutated), [])

    def test_judge_task_matches_sol(self) -> None:
        sample = SOL_SKILL.read_text(encoding="utf-8")
        for name in sorted(n for n, spec in PRESETS.items() if spec["judge_like_sol"]):
            with self.subTest(preset=name):
                self.assertEqual(_judge_problems(_skill_path(name).read_text(encoding="utf-8"), sample), [])

    def test_judge_check_rejects_changed_line(self) -> None:
        sample = SOL_SKILL.read_text(encoding="utf-8")
        text = _skill_path("xhigh-pipeline").read_text(encoding="utf-8")
        block = _judge_block(text)
        self.assertIsNotNone(block)
        lines = block.splitlines()
        lines[3] = lines[3] + " (изменено)"
        mutated = text.replace(block, "\n".join(lines), 1)
        self.assertNotEqual(mutated, text, "изменение не применилось")
        self.assertNotEqual(_judge_problems(mutated, sample), [])


class ClaudeCodexAgentTests(unittest.TestCase):
    def test_agent_files_match_dict(self) -> None:
        found = {path.name for path in (PLUGIN_DIR / AGENTS_SUBDIR).glob("*.md")}
        self.assertEqual(found, set(AGENTS))

    def test_agents(self) -> None:
        for filename in sorted(AGENTS):
            with self.subTest(agent=filename):
                text = (PLUGIN_DIR / AGENTS_SUBDIR / filename).read_text(encoding="utf-8")
                self.assertEqual(_agent_problems(filename, text), [])

    def test_agent_check_rejects_broken_texts(self) -> None:
        filename = "pipeline-critic-xhigh.md"
        text = (PLUGIN_DIR / AGENTS_SUBDIR / filename).read_text(encoding="utf-8")
        sample_fm, _ = _split_frontmatter((REPO_DIR / CRITIC_SAMPLE).read_text(encoding="utf-8"))
        sample_description = _fm_values(sample_fm, "description")[0]
        lines = text.split("\n")
        body_line = len(_frontmatter(lines)) + 4
        changed_body = lines[:]
        changed_body[body_line] = changed_body[body_line] + " (изменено)"
        broken = {
            "тело изменено на строку": "\n".join(changed_body),
            "description образца": re.sub(r"^description: .*$",
                                          lambda _: f"description: {sample_description}",
                                          text, count=1, flags=re.M),
        }
        for case, mutated in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_agent_problems(filename, mutated), [])


if __name__ == "__main__":
    unittest.main()
