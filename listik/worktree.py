"""Рабочее дерево задачи: `<проект>/.worktrees/<имя>` на ветке `task/<имя>`.

Модуль — чистая обвязка над `git`: он не знает ни о базе, ни о сервере. CLI
(`bin/listik`, `cmd_worktree`) читает карточку и проект, зовёт `ensure()` и сам
решает, писать ли путь/ветку в карточку (`card_fields`).

Главные инварианты (см. docs/API.md, «Рабочее дерево задачи»):

* незакоммиченная работа не теряется — грязное дерево переиспользуется как есть,
  `--recreate` на нём отказывает до любого удаления;
* коммиты ветки задачи, которых нет в `HEAD` проекта, не удаляются — `--recreate`
  отказывает, а протухшая запись пересоздаётся на существующей ветке без `-b`;
* основное дерево проекта не трогается: из git зовутся только `rev-parse`,
  `rev-list`, `log`, `status`, `worktree list/add/prune/remove` и `branch -d`
  (никаких `checkout`/`switch`/`reset`/`clean`/`stash`/`branch -D`);
* любой вызов git идёт с `--no-optional-locks`: опрос чужого дерева (`status`) не
  должен брать `index.lock` и срывать коммит воркера, который в этом дереве
  работает (найдено на стенде роя, listik-8q6c).
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import errors
from . import migrate

#: Часть имени из `--track`: непустая строка из строчных букв, цифр и дефисов.
TRACK_RE = re.compile(r"^[a-z0-9-]+$")

STATUS_TITLES = {"created": "создано", "reused": "переиспользовано", "recreated": "пересоздано"}


# ------------------------------------------------------------------ git


def _git_message(proc: subprocess.CompletedProcess) -> str:
    """Отказ git человеку: первые непустые строки stderr, без трейсбека."""
    text = (proc.stderr or "") + ("\n" + proc.stdout if not (proc.stderr or "").strip() else "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return f"git завершился с кодом {proc.returncode}"
    return "; ".join(lines[:3])


def git(repo: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Запуск git в каталоге `repo`. Отказ — `conflict` со stderr git в message."""
    proc = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise errors.ListikError(_git_message(proc), code=errors.CONFLICT)
    return proc


def git_out(repo: str | Path, *args: str) -> str:
    return git(repo, *args).stdout.strip()


def git_ok(repo: str | Path, *args: str) -> bool:
    return git(repo, *args, check=False).returncode == 0


def same_path(a: str | None, b: str | None) -> bool:
    """Сравнение путей с учётом симлинков (`/var` → `/private/var` на macOS)."""
    if not a or not b:
        return False
    return os.path.realpath(a) == os.path.realpath(b)


def worktree_list(repo: str | Path) -> list[dict]:
    """`git worktree list --porcelain` → [{path, branch, prunable}]."""
    out = git_out(repo, "worktree", "list", "--porcelain")
    entries: list[dict] = []
    current: dict | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            current = {"path": line[len("worktree "):].strip(), "branch": "", "prunable": False}
            entries.append(current)
        elif current is None:
            continue
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line.startswith("prunable"):
            current["prunable"] = True
    return entries


def head_sha(repo: str | Path) -> str:
    return git_out(repo, "rev-parse", "HEAD")


def base_info(path: str | Path) -> dict:
    """`база:` дерева — последний коммит того дерева, что вернула команда.

    `sha7` — ровно вывод `%h` (длина зависит от `core.abbrev`), не срез `sha`.
    """
    out = git_out(path, "log", "-1", "--format=%H%n%h%n%s")
    parts = out.split("\n", 2)
    while len(parts) < 3:
        parts.append("")
    return {"sha": parts[0], "sha7": parts[1], "subject": parts[2]}


def is_dirty(path: str | Path) -> bool:
    """В дереве есть незакоммиченные правки или неотслеживаемые файлы."""
    return bool(git_out(path, "status", "--porcelain"))


def branch_exists(repo: str | Path, branch: str) -> bool:
    return git_ok(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")


# ------------------------------------------------------------------ имена и карточка


def check_track(part: str | None) -> str:
    """Часть имени из `--track`: `[a-z0-9-]`, непустая."""
    value = (part or "").strip()
    if not value or not TRACK_RE.match(value):
        raise errors.ListikError(
            f"--track ждёт непустую строку из [a-z0-9-], получено: {part!r}",
            code=errors.BAD_ARGUMENT, hint="например: listik worktree <id> --track api")
    return value


def worktree_name(task_id: str, track: str | None = None) -> str:
    return f"{task_id}-{check_track(track)}" if track is not None else task_id


def card_fields(task: dict, path: str, branch: str) -> dict:
    """Что дописать в карточку: только отличающиеся поля, иначе `{}`.

    Правило «пишем только при отличии» живёт здесь, чтобы повторный вызов
    команды не двигал `updated_at` карточки на ровном месте.
    """
    fields = {}
    if ((task or {}).get("worktree") or "").strip() != (path or "").strip():
        fields["worktree"] = path
    if ((task or {}).get("branch") or "").strip() != (branch or "").strip():
        fields["branch"] = branch
    return fields


# ------------------------------------------------------------------ проверки проекта


def check_repo(project_path: str) -> None:
    """Каталог проекта существует, это git-репозиторий и в нём есть коммиты."""
    if not os.path.isdir(project_path):
        raise errors.ListikError(f"каталога проекта нет: {project_path}",
                                 code=errors.BAD_ARGUMENT,
                                 hint="поправь путь: listik projects --add <каталог>")
    if not git_ok(project_path, "rev-parse", "--show-toplevel"):
        raise errors.ListikError(f"каталог проекта не git-репозиторий: {project_path}",
                                 code=errors.BAD_ARGUMENT,
                                 hint="рабочее дерево заводится только в git-репозитории")
    if not git_ok(project_path, "rev-parse", "--verify", "--quiet", "HEAD"):
        raise errors.ListikError(f"в репозитории нет коммитов: {project_path}",
                                 code=errors.BAD_ARGUMENT,
                                 hint="сделай первый коммит — дерево заводится от HEAD")


def _check_recreate(project_path: str, path: str, branch: str) -> None:
    """`--recreate` допустим, только если терять нечего: чисто и без своих коммитов."""
    if is_dirty(path):
        raise errors.ListikError(
            f"в дереве {path} есть незакоммиченные правки — оно не пересоздаётся",
            code=errors.CONFLICT,
            hint="закоммить или убери правки сам, потом повтори --recreate")
    if branch:
        count = git_out(project_path, "rev-list", "--count", f"HEAD..{branch}")
        if count and count != "0":
            raise errors.ListikError(
                f"у ветки {branch} есть коммиты ({count}), которых нет в HEAD проекта — "
                f"дерево {path} не пересоздаётся",
                code=errors.CONFLICT,
                hint="влей ветку в основную или удали её сам, потом повтори --recreate")


def _add_worktree(project_path: str, path: str, branch: str) -> None:
    """Новое дерево: на существующей ветке — без `-b` (её коммиты сохраняются)."""
    if branch_exists(project_path, branch):
        git(project_path, "worktree", "add", path, branch)
    else:
        git(project_path, "worktree", "add", "-b", branch, path, head_sha(project_path))


# ------------------------------------------------------------------ основное


def ensure(project_path: str, name: str, *, card_path: str | None = None,
           recreate: bool = False) -> dict:
    """Завести или переиспользовать дерево задачи.

    `card_path` — путь из карточки (при `--track` не передаётся): если он
    зарегистрирован в этом репозитории и существует на диске, переиспользуется
    именно он, а `.worktrees/<name>` не трогается.

    Возвращает `{path, branch, status, base, dirty, gitignore}`.
    """
    check_repo(project_path)
    branch = f"task/{name}"
    path = os.path.join(project_path, ".worktrees", name)
    entries = worktree_list(project_path)

    # 2. Путь из карточки: зарегистрирован и лежит на диске — работаем с ним.
    card = (card_path or "").strip()
    target = None
    if card:
        for entry in entries:
            if same_path(entry["path"], card) and os.path.isdir(card):
                target = {"path": card, "branch": entry["branch"]}
                break

    if target is not None:
        path, branch = target["path"], target["branch"]
        mode = "recreate" if recreate else "reuse"
    else:
        registered = next((e for e in entries if same_path(e["path"], path)), None)
        # 3. Каталог занят, но деревом не зарегистрирован — ничего не трогаем.
        if registered is None and os.path.isdir(path):
            raise errors.ListikError(
                f"каталог {path} занят, но не зарегистрирован как дерево",
                code=errors.CONFLICT,
                hint="убери каталог сам или дай дереву другое имя: --track <часть>")
        # 4. Ветка выдана другому дереву — тоже отказ без изменений.
        busy = next((e for e in entries
                     if e["branch"] == branch and not same_path(e["path"], path)), None)
        if busy is not None:
            raise errors.ListikError(
                f"ветка {branch} уже выдана дереву {busy['path']}",
                code=errors.CONFLICT,
                hint="освободи ветку (git worktree remove) или дай дереву другое имя: "
                     "--track <часть>")
        if registered is None:
            mode = "create"
        elif not os.path.isdir(path):
            mode = "prune"
        else:
            mode = "recreate" if recreate else "reuse"

    # Отказы `--recreate` считаем до любых изменений на диске.
    if mode == "recreate":
        _check_recreate(project_path, path, branch)

    gitignore = migrate.ensure_gitignore(Path(project_path))

    if mode == "create":
        _add_worktree(project_path, path, branch)
        status = "created"
    elif mode == "prune":
        git(project_path, "worktree", "prune")
        _add_worktree(project_path, path, branch)
        status = "recreated"
    elif mode == "recreate":
        git(project_path, "worktree", "remove", path)
        if branch:
            git(project_path, "branch", "-d", branch)
        git(project_path, "worktree", "add", "-b", branch, path, head_sha(project_path))
        status = "recreated"
    else:
        status = "reused"

    return {"path": path, "branch": branch, "status": status,
            "base": base_info(path), "dirty": is_dirty(path), "gitignore": gitignore}


def human_lines(payload: dict) -> list[str]:
    """Три строки вывода команды плюс четвёртая, если в дереве есть правки."""
    base = payload.get("base") or {}
    lines = [
        f"дерево: {payload.get('path')} ({STATUS_TITLES.get(payload.get('status'), '')})",
        f"ветка:  {payload.get('branch')}",
        f"база:   {base.get('sha7', '')} {base.get('subject', '')}".rstrip(),
    ]
    if payload.get("dirty"):
        lines.append("в дереве незакоммиченные правки")
    return lines
