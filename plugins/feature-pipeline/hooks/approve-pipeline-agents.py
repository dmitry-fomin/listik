#!/usr/bin/env python3
"""PermissionRequest-хук плагина feature-pipeline.

Одобряет запрос разрешения без участия автора, только если одновременно:
- в настройках плагина включён auto_approve_agents;
- запрос пришёл от субагента конвейера (исполнитель или судья);
- правка файла лежит в проекте, в одном из его worktree или в .git/feature-pipeline,
  и это не секрет; команда Bash не попадает в список опасного.

Во всех остальных случаях хук молчит — и Claude Code задаёт обычный вопрос.
Ошибка внутри хука тоже означает молчание: одобрение по сбою хуже лишнего запроса.
Deny-правила из настроек сильнее хука и продолжают действовать.
"""
import json
import os
import re
import subprocess
import sys

AGENTS = {
    "feature-pipeline:pipeline-implementer",
    "feature-pipeline:pipeline-implementer-high",
    "feature-pipeline:pipeline-implementer-solo",
    "feature-pipeline:pipeline-judge",
}

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

SECRET_PATH = re.compile(
    r"(^|/)(\.env(\.[^/]*)?|credentials\.json|id_(rsa|dsa|ecdsa|ed25519)[^/]*|[^/]*\.(pem|key|p12|pfx))$"
)

# Поиск по всей строке команды, включая подстановки и цепочки: лишний запрос дешевле пропуска.
DANGEROUS = [
    r"\bgit\s+(push|reset|clean|stash|rebase|switch|checkout|restore|worktree|config|filter-branch|filter-repo|update-ref|reflog|gc|prune)\b",
    r"\bgit\s+branch\b.*\s(-D|-d|-m|-M|--delete|--move|--force)\b",
    r"--amend\b",
    r"--no-verify\b",
    r"\brm\s+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)\b",
    r"\b(sudo|doas|su)\b",
    r"\b(chown|chgrp)\b",
    r"\bchmod\s+-R\b",
    r"\b(docker|podman|orb|orbctl|kubectl|helm|terraform)\b",
    r"\b(ssh|scp|sftp|rsync)\b",
    r"\b(curl|wget)\b[^|;&]*\|\s*(sh|bash|zsh|python3?|node)\b",
    r"(?i)\b(drop\s+(database|schema|table)|truncate)\b",
    r"\bdd\s+if=",
    r"\bmkfs\b",
    r"\b(shutdown|reboot|halt|launchctl|crontab)\b",
    r"\bkill(all)?\b",
    r"\b(npm|pnpm|yarn|cargo|twine|gem)\s+publish\b",
    r"\bgh\s",
    r"(\.env\b|\.pem\b|\.key\b|\.p12\b|credentials\.json|id_rsa|id_ed25519)",
    r">\s*/(etc|usr|bin|sbin|System|Library)/",
]
DANGEROUS = [re.compile(p) for p in DANGEROUS]


def truthy(value):
    return (value or "").strip().lower() in {"true", "1", "yes", "on"}


def git(args, cwd):
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5
        ).stdout
    except Exception:
        return ""


def inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def edit_allowed(path, project):
    if not path or SECRET_PATH.search(path):
        return False
    git_dir = git(["rev-parse", "--absolute-git-dir"], project).strip()
    if git_dir and inside(path, os.path.join(git_dir, "feature-pipeline")):
        return True
    if ".git" in os.path.realpath(path).split(os.sep):
        return False
    roots = [project] + [
        line[len("worktree "):]
        for line in git(["worktree", "list", "--porcelain"], project).splitlines()
        if line.startswith("worktree ")
    ]
    return any(inside(path, root) for root in roots)


def bash_allowed(command):
    return bool(command.strip()) and not any(p.search(command) for p in DANGEROUS)


def main():
    if not truthy(os.environ.get("CLAUDE_PLUGIN_OPTION_AUTO_APPROVE_AGENTS")):
        return
    event = json.load(sys.stdin)
    if event.get("agent_type") not in AGENTS:
        return
    tool = event.get("tool_name")
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or os.getcwd()
    project = os.environ.get("CLAUDE_PROJECT_DIR") or cwd

    if tool == "Bash":
        ok = bash_allowed(tool_input.get("command") or "")
    elif tool in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if path and not os.path.isabs(path):
            path = os.path.join(cwd, path)
        ok = edit_allowed(path, project)
    else:
        ok = False

    if ok:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"},
                }
            },
            sys.stdout,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
