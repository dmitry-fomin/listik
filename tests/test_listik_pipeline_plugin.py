"""Плагин listik-pipeline: пути `${CLAUDE_PLUGIN_ROOT}/…` ведут в существующие файлы,
`name` скила совпадает с каталогом, `description` в лимите, у судьи нет права править код.

Проверка дешёвая, но ловит главный класс поломок: скил или агент ссылается на тело роли
относительным или мёртвым путём, роль не загружается, а ответ всё равно нужен.
"""
from __future__ import annotations

import pathlib
import re
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_DIR / "plugins" / "listik-pipeline"
PLUGIN_ROOT_VAR = "${CLAUDE_PLUGIN_ROOT}"
DESCRIPTION_LIMIT = 1024

PLUGIN_PATH_RE = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/([^\s`'\"<>)]+)")


def _skill_files() -> list[pathlib.Path]:
    return sorted(PLUGIN_DIR.glob("skills/*/SKILL.md"))


def _agent_files() -> list[pathlib.Path]:
    return sorted(PLUGIN_DIR.glob("agents/*.md"))


def _frontmatter(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    head, sep, _ = text.partition("\n---\n")
    return head if sep else ""


class ListikPipelinePluginTests(unittest.TestCase):
    def test_plugin_root_paths_exist(self) -> None:
        for path in _skill_files() + _agent_files():
            for target in PLUGIN_PATH_RE.findall(path.read_text(encoding="utf-8")):
                resolved = PLUGIN_DIR / target.split("#", 1)[0].rstrip(".,")
                self.assertTrue(resolved.exists(), f"{path}: нет {resolved}")
                # Скилы читает главная сессия, агенты — субагент: путь обязан быть
                # абсолютным только через переменную, иначе из кэша он не резолвится.
                self.assertNotIn("plugins/listik-pipeline/", path.read_text(encoding="utf-8"),
                                 f"{path}: относительный путь к файлу плагина")

    def test_skill_name_matches_dir_and_description_fits(self) -> None:
        for path in _skill_files():
            front = _frontmatter(path)
            self.assertIn(f"name: {path.parent.name}", front, f"{path}: name != каталог")
            match = re.search(r"^description: \"?(.*)\"?$", front, re.M)
            self.assertIsNotNone(match, f"{path}: нет description")
            self.assertLessEqual(len(match.group(1)), DESCRIPTION_LIMIT, f"{path}: description длиннее лимита")

    def test_every_role_body_is_referenced(self) -> None:
        # Критику зовёт писатель, своего скила у неё нет — проверяем только роли со скилом.
        for role in ("writer", "implementer", "lens", "judge"):
            self.assertIn(f"references/{role}.md",
                          (PLUGIN_DIR / "skills" / role / "SKILL.md").read_text(encoding="utf-8"),
                          f"skills/{role}: нет ссылки на тело роли")
            self.assertTrue(_agent_files_for(role), f"нет агентов роли {role}")
        self.assertTrue((PLUGIN_DIR / "references" / "critic.md").is_file())
        self.assertTrue(_agent_files_for("critic"))

    def test_judge_cannot_edit_code(self) -> None:
        # references/judge.md: «Код не правишь никогда».
        for path in _agent_files_for("judge"):
            self.assertNotIn("Edit", _frontmatter(path), f"{path}: судье выдано право правки")

    def test_worktree_step_uses_the_cli(self) -> None:
        # `git worktree add -b <id>` даёт ветку не task/<id> и пустые поля карточки.
        text = (PLUGIN_DIR / "agents" / "orchestrator.md").read_text(encoding="utf-8")
        step = next(line for line in text.splitlines() if line.startswith("3. **Worktree"))
        self.assertIn("listik worktree", step)


def _agent_files_for(role: str) -> list[pathlib.Path]:
    return sorted(PLUGIN_DIR.glob(f"agents/{role}-*.md"))


if __name__ == "__main__":
    unittest.main()
