"""PermissionRequest-хук плагина pipeline-core (listik-d9rj, порция a).

Хук запускается подпроцессом, как его зовёт Claude Code: JSON события на stdin, настройка плагина и
каталог проекта — в окружении. Одобрение — JSON с `"behavior": "allow"` на stdout, иначе stdout пуст.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
HOOK = REPO_DIR / "plugins" / "pipeline-core" / "hooks" / "approve-pipeline-agents.py"
#: Плагины, где агенты и каталог дампов жили до переноса в pipeline-core; имена собраны из частей,
#: чтобы grep старых имён по дереву (чек-лист порции) находил только настоящие хвосты.
OLD_FP = "feature" + "-pipeline"
OLD_CC = "claude" + "-codex"


@unittest.skipUnless(shutil.which("git"), "нужен git")
class PipelineCoreHookTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.project = pathlib.Path(tmp).resolve() / "proj"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.git_dir = pathlib.Path(subprocess.run(
            ["git", "rev-parse", "--absolute-git-dir"], cwd=self.project,
            capture_output=True, text=True, check=True).stdout.strip())

    def run_hook(self, agent: str, tool: str, tool_input: dict, option: str | None = "true") -> str:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_OPTION_AUTO_APPROVE_AGENTS"}
        env["CLAUDE_PROJECT_DIR"] = str(self.project)
        if option is not None:
            env["CLAUDE_PLUGIN_OPTION_AUTO_APPROVE_AGENTS"] = option
        event = {"agent_type": agent, "tool_name": tool, "tool_input": tool_input, "cwd": str(self.project)}
        result = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(event), env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def assertAllow(self, stdout: str) -> None:
        self.assertEqual(json.loads(stdout)["hookSpecificOutput"]["decision"], {"behavior": "allow"})
        self.assertIn('"behavior": "allow"', stdout)

    def test_01_listed_agent_bash_allowed(self) -> None:
        self.assertAllow(self.run_hook("pipeline-core:pipeline-implementer", "Bash", {"command": "ls"}))

    def test_02_old_feature_pipeline_prefix_silent(self) -> None:
        self.assertEqual(self.run_hook(f"{OLD_FP}:pipeline-implementer", "Bash", {"command": "ls"}), "")

    def test_03_old_claude_codex_prefix_silent(self) -> None:
        self.assertEqual(self.run_hook(f"{OLD_CC}:pipeline-implementer-low", "Bash", {"command": "ls"}), "")

    def test_04_unlisted_agent_silent(self) -> None:
        self.assertEqual(self.run_hook("pipeline-core:pipeline-critic", "Bash", {"command": "ls"}), "")

    def test_05_option_unset_silent(self) -> None:
        self.assertEqual(self.run_hook("pipeline-core:pipeline-judge", "Bash", {"command": "ls"}, option=None), "")

    def test_06_dangerous_command_silent(self) -> None:
        self.assertEqual(self.run_hook("pipeline-core:pipeline-judge", "Bash", {"command": "git push"}), "")

    def test_07_dump_dir_write_allowed(self) -> None:
        path = self.git_dir / "pipeline-core" / "x.diff-a.r1.txt"
        self.assertAllow(self.run_hook("pipeline-core:pipeline-judge", "Write", {"file_path": str(path)}))

    def test_08_old_dump_dir_write_silent(self) -> None:
        path = self.git_dir / OLD_FP / "x.txt"
        self.assertEqual(self.run_hook("pipeline-core:pipeline-judge", "Write", {"file_path": str(path)}), "")

    def test_09_git_config_write_silent(self) -> None:
        path = self.git_dir / "config"
        self.assertEqual(self.run_hook("pipeline-core:pipeline-judge", "Write", {"file_path": str(path)}), "")

    def test_10_removed_inherit_agents_silent(self) -> None:
        """listik-d9rj, порция f: агенты *-inherit удалены — автоодобрения у их имён нет."""
        for agent in ("pipeline-core:pipeline-judge-inherit", "pipeline-core:pipeline-implementer-inherit"):
            with self.subTest(agent=agent):
                self.assertEqual(self.run_hook(agent, "Bash", {"command": "ls"}), "")

    def test_11_judge_xhigh_allowed_like_judge(self) -> None:
        agent = "pipeline-core:pipeline-judge-xhigh"
        self.assertAllow(self.run_hook(agent, "Bash", {"command": "ls"}))
        self.assertEqual(self.run_hook(agent, "Bash", {"command": "git push"}), "")
        self.assertEqual(self.run_hook(agent, "Write", {"file_path": str(self.git_dir / "config")}), "")

    def test_12_lens_allowed_like_judge(self) -> None:
        """listik-jllp, порция b: линза в списке автоодобрения, опасное — по-прежнему запрос."""
        agent = "pipeline-core:pipeline-lens"
        self.assertAllow(self.run_hook(agent, "Bash", {"command": "ls"}))
        self.assertEqual(self.run_hook(agent, "Bash", {"command": "git push"}), "")
        self.assertEqual(self.run_hook(agent, "Write", {"file_path": str(self.git_dir / "config")}), "")


if __name__ == "__main__":
    unittest.main()
