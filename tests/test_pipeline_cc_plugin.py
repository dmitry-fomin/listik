"""Плагин pipeline-cc: пресеты конвейера на Claude + Codex (listik-r1pd, порции b и c;
имя плагина и пресетов — listik-d9rj, порции b и c).

Плагин ставится вместе с pipeline-core, codex и listik и ничего из них не копирует: скилы
пресетов читают ядро через скил `pipeline-core:core`, их агенты (критики нового усилия, тело
побайтно равно телу `pipeline-core:pipeline-critic`, и исполнитель low) живут в pipeline-core.

Файлы читаются в момент вызова через константы модуля, а не при импорте. Набор пресетов —
словарь `PRESETS`, агенты — словарь `AGENTS`: новый пресет или агент добавляется строкой в
словарь, проверки при этом не меняются.
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

from listik import launcher
from listik import skills as skills_mod

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / "plugins" / "pipeline-cc"
CORE_PLUGIN_DIR = REPO_DIR / "plugins" / "pipeline-core"
MARKETPLACE_JSON = REPO_DIR / ".claude-plugin" / "marketplace.json"

PLUGIN_NAME = "pipeline-cc"
PLUGIN_MANIFEST = pathlib.Path(".claude-plugin") / "plugin.json"
SKILLS_SUBDIR = "skills"
AGENTS_SUBDIR = "agents"
SKILL_FILE = "SKILL.md"

#: Чтение ядра через скил-указатель: подстроки текста SKILL.md со схлопнутыми пробелами.
CORE_LINK = ("скил `pipeline-core:core`",
             "прочитай ядро по пути, который он назовёт, целиком до первого действия",
             "`plugins/pipeline-core/references/pipeline-core.md`")
LAUNCH_PHRASE = "Запускай только по явному имени"
JUDGE_TASK_LINE = "Задание, которое уходит в `codex:codex-delegate`:"

#: Подстроки, которые обязаны быть в каждом SKILL.md плагина; `{name}` — имя скила (каталог в skills/).
SKILL_REQUIRED = ("Стоп-фактор", "пресет pipeline-cc:{name}", "# Конвейер pipeline-cc:{name}",
                  "process:cc-{name}", "/pipeline-cc:{name}", "`model` перебивает frontmatter",
                  "не того усилия", "согласие")

#: Подстроки, которых нет ни в одном SKILL.md плагина (без учёта регистра).
SKILL_FORBIDDEN_CI = ("pi:pi-", "devin", "grok", "dsh", "deepseek", "glm", "--provider", "--channel",
                      "/feature-pipeline:")

#: Приёмка линзами Haiku 5.5 max (listik-jllp, порция b): линзы — локальные субагенты
#: `pipeline-core:pipeline-lens` на заходе `r1`; судья Codex Astra идёт по их находкам,
#: при чистых линзах порцию коммитит оркестратор. В xlow линз нет.
LENS_PRESETS = ("xhigh", "high", "medium", "low")
LENS_AGENT = "pipeline-core:pipeline-lens"
LENS_MODEL = "model: haiku"
LENS_CORE_REF = "`pipeline-core.md`, «Приёмка линзами»"
#: «линз» запрещена только пресету без линз (xlow) — ему линзы не положены.
NO_LENS_FORBIDDEN_CI = ("линз",)
#: Подстроки всего текста пресета с линзами: ссылка на ядро, локальность линз, коммит
#: оркестратора при чистых линзах, предполётная проверка линзы и две строки «Грабли».
LENS_TEXT_REQUIRED = (LENS_CORE_REF, LENS_AGENT, "Haiku 5.5 max", "локальные субагенты",
                      "коммитит оркестратор", "стоп: линза", "Линза изменила дерево",
                      "После красного снова зовутся линзы")
#: Этап 4 пресета с линзами: запуск одним сообщением, развилка и файлы линз в задании судье.
STAGE4_LENS = (LENS_AGENT, LENS_MODEL, "одним сообщением", LENS_CORE_REF, "Линзы чисты",
               "lens-<X>", "вынести:", "отбросить:", "чинить",
               'git -C "$WT" log --oneline -1')

QUORUM_ANTHROPIC_EXTRA = "кворум — годные ответы `sonnet` и `codex`; `opus` в кворум не входит"
QUORUM_STOP = "стоп: критика — кворум не набран"
OPUS_REVIEW = "review-<X>.opus.md"

STAGE4_REQUIRED = ("codex:codex-delegate", "--model gpt-6-astra", "--effort high", "--permission write",
                   "--holder codex", "VERDICT: PASS")

STAGE4_REQUIRED_MEDIUM = ("codex:codex-delegate", "--model gpt-6-astra --effort medium", "--permission write",
                          "--holder codex", "VERDICT: PASS")
#: Судья low и xlow — Astra medium: ни high-усилия, ни модели критика.
STAGE4_FORBIDDEN_MEDIUM = ("--effort high", "--model gpt-6.1-sol")
IMPLEMENTER_PAIR = ("`pipeline-core:pipeline-implementer`", "model: opus")
HOD1_TITLE = "## Ход 1"
GIT_STATUS = 'git -C "$WT" status --porcelain'

#: Пресет xlow: этапы 3–4 и условный ход 1 одного локального субагента, судья Astra medium.
_NO_SPEC_PRESET: dict = {
    "stages": (3, 4),
    "required": {
        4: STAGE4_REQUIRED_MEDIUM + ("Запрет codex на коммиты здесь снят автором", "adhoc"),
    },
    "pairs": {3: (IMPLEMENTER_PAIR,)},
    "forbidden": {3: ("codex:codex-delegate",), 4: STAGE4_FORBIDDEN_MEDIUM},
    "hod1": {
        "required": ("write_scope", GIT_STATUS, "главнее формата"),
        "pairs": (IMPLEMENTER_PAIR,),
        "forbidden": ("codex:codex-delegate",),
    },
    "text_required": ("SendMessage", "codex:codex-check"),
}

#: Пресет → требования по этапам. `required` — подстроки раздела `### <n>.`, `pairs` — пары подстрок,
#: обязанные стоять в одной строке раздела, `forbidden` — подстроки, которых в разделе нет,
#: Необязательные ключи: `stages` —
#: какие разделы `### <n>.` есть (остальных из 1–4 быть не должно; по умолчанию все четыре),
#: `hod1` — требования к разделу `## Ход 1` (required/pairs/forbidden), `text_required` — подстроки
#: всего текста, `judge_twin` — группа пресетов, чьи задания судье равны друг другу побайтно.
PRESETS: dict[str, dict] = {
    "xhigh": {
        "required": {
            1: ("`pipeline-core:pipeline-spec-writer-xhigh`", "model: opus"),
            2: ("--model gpt-6.1-sol --effort high", OPUS_REVIEW, QUORUM_ANTHROPIC_EXTRA,
                "отменён по кворуму", QUORUM_STOP),
            3: ("pipeline-core:pipeline-implementer-xhigh", "параметр `model` в вызове не передаётся"),
            4: STAGE4_REQUIRED + STAGE4_LENS,
        },
        "pairs": {
            2: (("pipeline-core:pipeline-critic-xhigh", "model: sonnet"),
                ("`pipeline-core:pipeline-critic`", "model: opus")),
        },
        "forbidden": {3: ("model: opus",)},
        "judge_twin": "with-spec",
    },
    "high": {
        "required": {
            1: ("`pipeline-core:pipeline-spec-writer`", "model: opus"),
            2: ("--model gpt-6.1-sol --effort high", OPUS_REVIEW, QUORUM_ANTHROPIC_EXTRA,
                "отменён по кворуму", QUORUM_STOP),
            4: STAGE4_REQUIRED + STAGE4_LENS,
        },
        "pairs": {
            2: (("`pipeline-core:pipeline-critic`", "model: sonnet"),
                ("pipeline-core:pipeline-critic-medium", "model: opus")),
            3: (("pipeline-core:pipeline-implementer-high", "model: opus"),),
        },
        "forbidden": {},
        "judge_twin": "with-spec",
    },
    "medium": {
        "required": {
            1: ("`pipeline-core:pipeline-spec-writer-medium`", "model: opus"),
            2: ("--model gpt-6.1-sol --effort medium", "кворум — оба", QUORUM_STOP),
            4: STAGE4_REQUIRED + STAGE4_LENS,
        },
        "pairs": {
            2: (("pipeline-core:pipeline-critic-medium", "model: sonnet"),),
            3: (("`pipeline-core:pipeline-implementer`", "model: opus"),),
        },
        "forbidden": {2: (OPUS_REVIEW, "--effort high")},
        "judge_twin": "with-spec",
    },
    "low": {
        "required": {
            1: ("`pipeline-core:pipeline-spec-writer-low`", "model: opus"),
            2: ("codex:codex-delegate", "--model gpt-6.1-sol --effort medium", "--permission read",
                "кворум — он один", QUORUM_STOP, "codex:codex-jobs"),
            3: ("pipeline-core:pipeline-implementer-low", "параметр `model` в вызове не передаётся"),
            4: STAGE4_REQUIRED_MEDIUM + STAGE4_LENS,
        },
        "pairs": {},
        "forbidden": {2: ("pipeline-critic", "model: sonnet"), 3: ("codex:codex-delegate", "model: opus"),
                      4: STAGE4_FORBIDDEN_MEDIUM},
        "text_required": ("codex:codex-check",),
        "judge_twin": "with-spec",
    },
    "xlow": _NO_SPEC_PRESET,
}

#: Во всех этапах 2 нет этих подстрок.
STAGE2_FORBIDDEN_ALL = ("--permission write",)

CRITIC_SAMPLE = pathlib.Path("plugins") / "pipeline-core" / "agents" / "pipeline-critic.md"
IMPLEMENTER_SAMPLE = pathlib.Path("plugins") / "pipeline-core" / "agents" / "pipeline-implementer.md"

#: Файл агента → (model, effort, файл-образец тела относительно корня репозитория, список `skills:`;
#: пустой список — строки `skills:` во frontmatter нет).
AGENTS: dict[str, tuple[str, str, pathlib.Path, tuple[str, ...]]] = {
    "pipeline-critic-xhigh.md": ("sonnet", "xhigh", CRITIC_SAMPLE, ()),
    "pipeline-critic-medium.md": ("sonnet", "medium", CRITIC_SAMPLE, ()),
    "pipeline-implementer-low.md": ("opus", "low", IMPLEMENTER_SAMPLE, ("listik:listik",)),
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


def _hod1_section(text: str) -> str:
    """Строки от начинающейся с `## Ход 1` до следующего `## ` (без неё)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(HOD1_TITLE)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end])


def _roles_section(text: str) -> list[str]:
    """Строки раздела `## Роли` до следующего `## ` (без неё); пусто — раздела нет."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("## Роли")), None)
    if start is None:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def _fm_list(frontmatter: list[str], key: str) -> list[str] | None:
    """Пункты списка `key:` вида `  - значение`; None — строки `key:` нет."""
    try:
        start = frontmatter.index(f"{key}:")
    except ValueError:
        return None
    items = []
    for line in frontmatter[start + 1:]:
        if not line.startswith("  - "):
            break
        items.append(line[len("  - "):].strip())
    return items


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
        if name in LENS_PRESETS and "Haiku 5.5 max" not in value:
            problems.append("в description нет 'Haiku 5.5 max'")
    collapsed = " ".join(text.split())
    problems.extend(f"нет чтения ядра {needle!r}" for needle in CORE_LINK if needle not in collapsed)
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
    forbidden = SKILL_FORBIDDEN_CI if name in LENS_PRESETS else SKILL_FORBIDDEN_CI + NO_LENS_FORBIDDEN_CI
    problems.extend(f"есть запрещённое {needle!r}" for needle in forbidden if needle in lowered)
    if name in LENS_PRESETS:
        problems.extend(f"нет {needle!r}" for needle in LENS_TEXT_REQUIRED if needle not in text)
        roles = _roles_section(text)
        if not any(LENS_AGENT in line and LENS_MODEL in line for line in roles):
            problems.append(f"в «## Роли» нет строки линз {LENS_AGENT!r} с {LENS_MODEL!r}")
        if not any("только при находке линз" in line for line in roles):
            problems.append("в «## Роли» нет строки судьи «только при находке линз»")
    return problems


def _stage_problems(name: str, text: str) -> list[str]:
    """Проверки 4 и 5: состав по этапам и запрещённые подстроки; пусто — всё на месте."""
    spec = PRESETS[name]
    problems: list[str] = []
    stages = spec.get("stages", (1, 2, 3, 4))
    problems.extend(f"есть лишний раздел «### {number}.»" for number in (1, 2, 3, 4)
                    if number not in stages and _stage_section(text, number))
    for number in stages:
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
    hod1 = spec.get("hod1")
    if hod1 is not None:
        section = _hod1_section(text)
        if not section:
            problems.append(f"нет раздела «{HOD1_TITLE}»")
        else:
            problems.extend(f"в «{HOD1_TITLE}» нет {needle!r}"
                            for needle in hod1["required"] if needle not in section)
            lines = section.splitlines()
            for first, second in hod1["pairs"]:
                if not any(first in line and second in line for line in lines):
                    problems.append(f"в «{HOD1_TITLE}» нет строки с {first!r} и {second!r}")
            problems.extend(f"в «{HOD1_TITLE}» есть {needle!r}"
                            for needle in hod1["forbidden"] if needle in section)
    problems.extend(f"в тексте нет {needle!r}" for needle in spec.get("text_required", ()) if needle not in text)
    lowered = text.lower()
    forbidden = SKILL_FORBIDDEN_CI if name in LENS_PRESETS else SKILL_FORBIDDEN_CI + NO_LENS_FORBIDDEN_CI
    problems.extend(f"есть запрещённое {needle!r}" for needle in forbidden if needle in lowered)
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
        return ["в образце нет задания судье"]
    if block is None:
        return ["нет fenced-блока задания судье в «### 4.»"]
    return [] if block == expected else ["задание судье разошлось с образцом"]


README = PLUGIN_DIR / "README.md"


def _readme_lens_problems(text: str) -> list[str]:
    """Строки «Скилы» README: линзы «Haiku 5.5 max ×3» и коммит оркестратора — ровно
    у пресетов с линзами; в строке xlow слова «линз» нет."""
    problems: list[str] = []
    for name in sorted(PRESETS):
        rows = [line for line in text.splitlines() if line.startswith(f"| `{name}` ")]
        if name in LENS_PRESETS:
            if not rows:
                problems.append(f"README: нет строки {name!r}")
            elif not any("Haiku 5.5 max ×3" in row for row in rows):
                problems.append(f"README: строка {name} не называет линзы Haiku 5.5 max ×3")
            elif not any("коммитит оркестратор" in row for row in rows):
                problems.append(f"README: строка {name} не называет коммит оркестратора")
        elif any("линз" in row for row in rows):
            problems.append(f"README: строка {name} называет линзы")
    return problems


def _agent_problems(filename: str, text: str) -> list[str]:
    """Проверка 8 для одного агента; пусто — всё на месте."""
    model, effort, sample_path, skills = AGENTS[filename]
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
    found_skills = _fm_list(frontmatter, "skills")
    if not skills and (found_skills is not None or _fm_values(frontmatter, "skills")):
        problems.append("есть skills:")
    if skills and found_skills != list(skills):
        problems.append(f"skills не {list(skills)}")
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
        name = "xhigh"
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

    def test_skill_check_rejects_broken_lens_texts(self) -> None:
        """listik-jllp, порция b: линзы — у четырёх пресетов, у xlow слова «линз» нет."""

        def without_in_roles_row(text: str, needle: str) -> str:
            lines = text.splitlines()
            for index, line in enumerate(lines):
                if "Приёмка: линзы" in line and LENS_AGENT in line:
                    lines[index] = line.replace(needle, "")
                    return "\n".join(lines)
            self.fail("нет строки роли линз")

        originals = {name: _skill_path(name).read_text(encoding="utf-8")
                     for name in ("medium", "low", "xlow")}
        medium, low, xlow = originals["medium"], originals["low"], originals["xlow"]
        broken = {
            ("medium", "строка роли линз без model: haiku"):
                ("medium", without_in_roles_row(medium, LENS_MODEL)),
            ("medium", "строка роли судьи без «только при находке линз»"):
                ("medium", medium.replace("только при находке линз", "")),
            ("low", "description без «Haiku 5.5 max»"):
                ("low", low.replace("Haiku 5.5 max", "", 1)),
            ("xlow", "xlow + «Приёмка линзами»"):
                ("xlow", xlow + "\nПриёмка — по «Приёмка линзами».\n"),
        }
        for (name, case), (preset, mutated) in broken.items():
            with self.subTest(preset=name, case=case):
                self.assertNotEqual(mutated, originals[preset], "изменение не применилось")
                self.assertNotEqual(_skill_problems(preset, mutated, _skill_path(preset).parent), [])

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

        xhigh = _skill_path("xhigh").read_text(encoding="utf-8")
        high = _skill_path("high").read_text(encoding="utf-8")
        broken = {
            ("xhigh", "### 2. без --effort high"): in_stage(
                xhigh, 2, lambda s: s.replace("--effort high", "")),
            ("xhigh", "### 4. без --permission write"): in_stage(
                xhigh, 4, lambda s: s.replace("--permission write", "")),
            ("xhigh", "### 4. gpt-6-astra → gpt-6.1-sol"): in_stage(
                xhigh, 4, lambda s: s.replace("--model gpt-6-astra", "--model gpt-6.1-sol")),
            ("xhigh", "+ /grok:delegate"): xhigh + "\n/grok:delegate\n",
            ("xhigh", "+ DeepSeek"): xhigh + "\nDeepSeek\n",
            ("xhigh", "### 2. sonnet ↔ opus"): in_stage(xhigh, 2, swap),
            ("high", "### 2. sonnet ↔ opus"): in_stage(high, 2, swap),
            ("high", "### 2. без фразы кворума"): in_stage(high, 2, without_quorum),
        }
        originals = {"xhigh": xhigh, "high": high}
        for (name, case), mutated in broken.items():
            with self.subTest(preset=name, case=case):
                self.assertNotEqual(mutated, originals[name], "изменение не применилось")
                self.assertNotEqual(_stage_problems(name, mutated), [])

    def test_judge_twins_equal(self) -> None:
        groups: dict[str, list[str]] = {}
        for name, spec in PRESETS.items():
            if spec.get("judge_twin"):
                groups.setdefault(spec["judge_twin"], []).append(name)
        self.assertTrue(groups, "нет ни одной группы judge_twin")
        for group, names in groups.items():
            with self.subTest(group=group):
                self.assertGreater(len(names), 1, f"в группе {group} один пресет")
                blocks = {name: _judge_block(_skill_path(name).read_text(encoding="utf-8")) for name in names}
                for name, block in blocks.items():
                    self.assertIsNotNone(block, f"{name}: нет fenced-блока задания судье")
                self.assertEqual(len(set(blocks.values())), 1, f"задания судье в группе {group} разошлись")

    def test_stage_check_rejects_broken_low_xlow(self) -> None:
        def in_stage(text: str, number: int, edit) -> str:
            section = _stage_section(text, number)
            self.assertTrue(section, f"нет раздела «### {number}.»")
            return text.replace(section, edit(section), 1)

        def in_hod1(text: str, edit) -> str:
            section = _hod1_section(text)
            self.assertTrue(section, f"нет раздела «{HOD1_TITLE}»")
            return text.replace(section, edit(section), 1)

        def without_line(text: str, needle: str) -> str:
            return "\n".join(line for line in text.split("\n") if needle not in line)

        originals = {name: _skill_path(name).read_text(encoding="utf-8")
                     for name in ("low", "xlow")}
        low, xlow = originals["low"], originals["xlow"]
        broken = {
            ("low", "### 3. исполнитель → codex:codex-delegate"): in_stage(
                low, 3, lambda s: s.replace("pipeline-core:pipeline-implementer-low", "codex:codex-delegate")),
            ("low", "### 4. --effort medium → --effort high"): in_stage(
                low, 4, lambda s: s.replace("--effort medium", "--effort high")),
            ("low", "### 4. без --permission write"): in_stage(
                low, 4, lambda s: s.replace("--permission write", "")),
            ("xlow", "Ход 1 без model: opus"): in_hod1(
                xlow, lambda s: s.replace("model: opus", "")),
            ("xlow", "без строки git status"): without_line(xlow, GIT_STATUS),
            ("xlow", "### 4. без --holder codex"): in_stage(
                xlow, 4, lambda s: s.replace("--holder codex", "")),
            ("low", "### 4. без model: haiku"): in_stage(
                low, 4, lambda s: s.replace("model: haiku", "")),
        }
        for (name, case), mutated in broken.items():
            with self.subTest(preset=name, case=case):
                self.assertNotEqual(mutated, originals[name], "изменение не применилось")
                self.assertNotEqual(_stage_problems(name, mutated), [])

    def test_judge_check_rejects_changed_line(self) -> None:
        sample = _skill_path("high").read_text(encoding="utf-8")
        text = _skill_path("xhigh").read_text(encoding="utf-8")
        block = _judge_block(text)
        self.assertIsNotNone(block)
        lines = block.splitlines()
        lines[3] = lines[3] + " (изменено)"
        mutated = text.replace(block, "\n".join(lines), 1)
        self.assertNotEqual(mutated, text, "изменение не применилось")
        self.assertNotEqual(_judge_problems(mutated, sample), [])

    def test_readme_lens_rows(self) -> None:
        self.assertEqual(_readme_lens_problems(README.read_text(encoding="utf-8")), [])

    def test_readme_lens_check_rejects_broken_rows(self) -> None:
        text = README.read_text(encoding="utf-8")
        low_row = "линзы Haiku 5.5 max ×3; при находке — GPT-6 Astra medium в Codex"
        broken = {
            "xlow с «линз»": text.replace("| `xlow` |", "| `xlow` | линзы,"),
            "low без «Haiku 5.5 max ×3»": text.replace(
                low_row, low_row.replace("Haiku 5.5 max ×3", "Haiku 5.5 ×3")),
            "xhigh без коммита оркестратора": text.replace(
                "при чистых линзах коммитит оркестратор", "", 1),
        }
        for case, mutated in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_readme_lens_problems(mutated), [])


class ClaudeCodexAgentTests(unittest.TestCase):
    def test_agent_files_match_dict(self) -> None:
        for filename in sorted(AGENTS):
            with self.subTest(agent=filename):
                self.assertTrue((CORE_PLUGIN_DIR / AGENTS_SUBDIR / filename).is_file(),
                                f"нет {CORE_PLUGIN_DIR / AGENTS_SUBDIR / filename}")
        self.assertFalse((PLUGIN_DIR / AGENTS_SUBDIR).exists(),
                         f"каталог {PLUGIN_DIR / AGENTS_SUBDIR} есть — агенты живут в pipeline-core")

    def test_agents(self) -> None:
        for filename in sorted(AGENTS):
            with self.subTest(agent=filename):
                text = (CORE_PLUGIN_DIR / AGENTS_SUBDIR / filename).read_text(encoding="utf-8")
                self.assertEqual(_agent_problems(filename, text), [])

    def test_agent_check_rejects_broken_texts(self) -> None:
        filename = "pipeline-critic-xhigh.md"
        text = (CORE_PLUGIN_DIR / AGENTS_SUBDIR / filename).read_text(encoding="utf-8")
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

    def test_implementer_low_check_rejects_broken_texts(self) -> None:
        filename = "pipeline-implementer-low.md"
        text = (CORE_PLUGIN_DIR / AGENTS_SUBDIR / filename).read_text(encoding="utf-8")
        sample_fm, _ = _split_frontmatter((REPO_DIR / IMPLEMENTER_SAMPLE).read_text(encoding="utf-8"))
        sample_description = _fm_values(sample_fm, "description")[0]
        broken = {
            "без skills": text.replace("skills:\n  - listik:listik\n", "", 1),
            "effort medium": text.replace("effort: low", "effort: medium", 1),
            "model sonnet": text.replace("model: opus", "model: sonnet", 1),
            "description образца": re.sub(r"^description: .*$",
                                          lambda _: f"description: {sample_description}",
                                          text, count=1, flags=re.M),
        }
        for case, mutated in broken.items():
            with self.subTest(case=case):
                self.assertNotEqual(mutated, text, "изменение не применилось")
                self.assertNotEqual(_agent_problems(filename, mutated), [])


ROUTES_JSON = REPO_DIR / "routes.json"
ROUTE_PREFIX = "cc-"


def _claude(label: str) -> dict:
    return {"provider": "claude", "label": label, "title": "Opus 5.5"}


def _codex(label: str, title: str, model: str, effort: str) -> dict:
    return {"provider": "openai", "label": label, "title": title,
            "skill": "codex:codex-delegate", "params": {"model": model, "effort": effort}}


_ASTRA_LENS_HIGH = _codex("линзы", "Haiku 5.5 · max · 3 линзы → GPT-6 Astra · high",
                          "gpt-6-astra", "high")
_ASTRA_LENS_MEDIUM = _codex("линзы", "Haiku 5.5 · max · 3 линзы → GPT-6 Astra · medium",
                            "gpt-6-astra", "medium")
_ASTRA_MEDIUM = _codex("medium", "GPT-6 Astra", "gpt-6-astra", "medium")

#: Маршруты `cc-*` в routes.json: ключ → (icon, title, hint, roles) — по таблицам ТЗ порции d.
CC_ROUTES: dict[str, tuple[str, str, str, dict]] = {
    "cc-xhigh": ("xhigh", "cc xhigh", "Claude + Codex · от 50 мин на задачу", {
        "spec": _claude("xhigh"),
        "critic": {"provider": "claude", "label": "S+O+Sol",
                   "title": "Sonnet 5.5 · xhigh + Opus 5.5 · high + GPT-6.1 Sol · high"},
        "impl": _claude("xhigh"), "judge": _ASTRA_LENS_HIGH}),
    "cc-high": ("high", "cc high", "Claude + Codex · от 40 мин на задачу", {
        "spec": _claude("high"),
        "critic": {"provider": "claude", "label": "S+O+Sol",
                   "title": "Sonnet 5.5 · high + Opus 5.5 · medium + GPT-6.1 Sol · high"},
        "impl": _claude("high"), "judge": _ASTRA_LENS_HIGH}),
    "cc-medium": ("medium", "cc medium", "Claude + Codex · 30 мин на задачу", {
        "spec": _claude("medium"),
        "critic": {"provider": "claude", "label": "S+Sol",
                   "title": "Sonnet 5.5 · medium + GPT-6.1 Sol · medium"},
        "impl": _claude("medium"), "judge": _ASTRA_LENS_HIGH}),
    "cc-low": ("low", "cc low", "Claude + Codex · на 20 мин, с ТЗ и критикой", {
        "spec": _claude("low"),
        "critic": _codex("Sol", "GPT-6.1 Sol · medium", "gpt-6.1-sol", "medium"),
        "impl": _claude("low"), "judge": _ASTRA_LENS_MEDIUM}),
    "cc-xlow": ("xlow", "cc xlow", "Claude + Codex · без ТЗ и критики · не для эпиков", {
        "impl": _claude("medium"), "judge": _ASTRA_MEDIUM}),
}


def _cc_records() -> dict[str, dict]:
    return {r["key"]: r for r in _read_json(ROUTES_JSON)["routes"]
            if r["key"].startswith(ROUTE_PREFIX)}


class ClaudeCodexRoutesTests(unittest.TestCase):
    def test_route_keys_match_skill_dirs(self) -> None:
        self.assertEqual(set(_cc_records()), {ROUTE_PREFIX + name for name in _skill_dirs()})
        self.assertEqual(set(CC_ROUTES), set(_cc_records()))

    def test_roles_icon_title_hint(self) -> None:
        for key, record in _cc_records().items():
            icon, title, hint, roles = CC_ROUTES[key]
            with self.subTest(route=key):
                self.assertEqual(record["kind"], "pipeline")
                self.assertIs(record["visible"], True)
                self.assertEqual((record["icon"], record["title"], record["hint"]),
                                 (icon, title, hint))
                self.assertEqual(record["roles"], roles)
                for cell in record["roles"].values():
                    self.assertIn(cell["provider"], ("claude", "openai"))
                    if "skill" in cell:
                        self.assertEqual(cell["skill"], "codex:codex-delegate")
                impl = record["roles"]["impl"]
                self.assertEqual(impl["provider"], "claude")
                self.assertNotIn("skill", impl)

    def test_command_calls_pipeline_cc_skill(self) -> None:
        for key, record in _cc_records().items():
            with self.subTest(route=key):
                self.assertEqual(record["plugin"], PLUGIN_NAME)
                prompt = launcher._substitute(record["command"][-1], {
                    "plugin": record["plugin"],
                    "skill": skills_mod.skill_of(record["plugin"], key)})
                self.assertIn(f"/{PLUGIN_NAME}:{key[len(ROUTE_PREFIX):]}", prompt)
                self.assertNotIn("/feature-pipeline:", prompt)


if __name__ == "__main__":
    unittest.main()
