"""Git-слой наблюдателя роя (swarm-5): тронутые файлы, снимок грязного дерева,
пробное слияние.

Чистый git-слой — модуль не знает о базе Listik и не импортирует `store`/
`server`/`client`/`launcher`. Ни одна функция не пишет в рабочее дерево, индекс
дерева или ссылки репозитория (`checkout`/`reset`/`stash`/`add` без
`GIT_INDEX_FILE`/`commit`/`branch`/`worktree add|remove` здесь не вызываются).

Следующие порции (`listik watch`, заморозка) опираются на функции этого модуля.
"""
from __future__ import annotations

import os
import shutil
import tempfile

from . import errors
from . import scope as scope_mod
from . import worktree

#: Автор/дата снимка — фиксированные, чтобы `snapshot` неизменного грязного
#: дерева был детерминированным (см. `snapshot`).
_SNAPSHOT_COMMIT_ENV = {
    "GIT_AUTHOR_NAME": "listik",
    "GIT_AUTHOR_EMAIL": "listik@local",
    "GIT_COMMITTER_NAME": "listik",
    "GIT_COMMITTER_EMAIL": "listik@local",
    "GIT_AUTHOR_DATE": "@0 +0000",
    "GIT_COMMITTER_DATE": "@0 +0000",
}

_SNAPSHOT_MESSAGE = "listik watch: снимок дерева"

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _git_out(repo, *args: str, env: dict | None = None) -> str:
    return worktree.git(repo, *args, env=env).stdout.strip()


def _is_oid(text: str) -> bool:
    return len(text) == 40 and all(ch in _HEX_DIGITS for ch in text)


# ------------------------------------------------------------------ changes


def _diff_via_index_copy(tree, merge_base: str) -> tuple[str, str]:
    """`--name-only`/`--numstat` через приватную копию индекса дерева.

    Ни порцеляновый `git diff`, ни плумбинг `git diff-index` в одиночку не годятся:
    `diff <commit>` на git 2.54.0 при racy-clean файле (stat разошёлся с кэшем,
    содержимое то же) читает содержимое, убеждается, что файла не в списке
    изменений, и — как побочный эффект — переписывает `.git/index` дерева воркера
    даже под `--no-optional-locks` (это и есть нарушение инварианта «индекс
    воркера не трогаем», подтверждено замером). `diff-index` индекс не трогает,
    но и содержимое не перечитывает: на том же racy-clean файле он не может
    доверять устаревшему stat и отдаёт файл как изменённый без проверки — ложное
    срабатывание, ломающее контракт `set(numstat) == set(files)` и `outside_scope`.
    Решение — диффить не по настоящему индексу дерева, а по его copy-on-write
    копии во временном файле: `git diff` на приватной копии свободен переписывать
    её как угодно (реальный индекс воркера при этом не открывается на запись),
    и даёт содержательно верный ответ на racy-clean файле (замер: пустой вывод,
    как и должно быть, реальный `.git/index` дерева не менялся байт в байт).
    """
    real_index = _git_out(tree, "rev-parse", "--git-path", "index")
    if not os.path.isabs(real_index):
        real_index = os.path.join(str(tree), real_index)

    fd, tmp_index = tempfile.mkstemp(prefix="listik-watch-diff-index-")
    os.close(fd)
    try:
        shutil.copyfile(real_index, tmp_index)
        diff_env = {"GIT_INDEX_FILE": tmp_index}
        diff_out = _git_out(tree, "-c", "core.quotepath=false", "diff", "--name-only",
                            "--no-renames", merge_base, env=diff_env)
        numstat_out = _git_out(tree, "-c", "core.quotepath=false", "diff", "--numstat",
                               "--no-renames", merge_base, env=diff_env)
    finally:
        for path in (tmp_index, tmp_index + ".lock"):
            if os.path.exists(path):
                os.remove(path)
    return diff_out, numstat_out


def changes(tree, base_ref: str) -> dict:
    """Тронутые файлы дерева задачи относительно `base_ref` — sha `HEAD` проекта.

    `--name-only`/`--numstat` идут через `_diff_via_index_copy` — см. его
    docstring про то, почему ни `diff`, ни `diff-index` по настоящему индексу
    дерева не годятся сами по себе.
    """
    merge_base = _git_out(tree, "merge-base", base_ref, "HEAD")
    ahead = int(_git_out(tree, "rev-list", "--count", f"{merge_base}..HEAD"))
    dirty = bool(_git_out(tree, "status", "--porcelain"))

    diff_out, numstat_out = _diff_via_index_copy(tree, merge_base)
    diff_files = [line for line in diff_out.splitlines() if line]

    others_out = _git_out(tree, "-c", "core.quotepath=false", "ls-files", "--others",
                          "--exclude-standard")
    others = [line for line in others_out.splitlines() if line]

    numstat: dict[str, dict] = {}
    for line in numstat_out.splitlines():
        if not line:
            continue
        added_s, deleted_s, path = line.split("\t", 2)
        added = None if added_s == "-" else int(added_s)
        deleted = None if deleted_s == "-" else int(deleted_s)
        numstat[path] = {"added": added, "deleted": deleted}
    for path in others:
        numstat.setdefault(path, {"added": None, "deleted": None})

    files = sorted(set(diff_files) | set(others))

    return {"merge_base": merge_base, "ahead": ahead, "dirty": dirty,
            "files": files, "numstat": numstat}


# ------------------------------------------------------------------ snapshot


def snapshot(tree, *, dirty: bool | None = None) -> str:
    """Sha висячего коммита со всем содержимым дерева; чистое — просто `HEAD`."""
    if dirty is None:
        dirty = worktree.is_dirty(tree)
    if not dirty:
        return _git_out(tree, "rev-parse", "HEAD")

    fd, tmp_index = tempfile.mkstemp(prefix="listik-watch-index-")
    os.close(fd)
    os.remove(tmp_index)
    try:
        index_env = {"GIT_INDEX_FILE": tmp_index}
        worktree.git(tree, "read-tree", "HEAD", env=index_env)
        worktree.git(tree, "add", "-A", env=index_env)
        tree_sha = _git_out(tree, "write-tree", env=index_env)
        return _git_out(tree, "commit-tree", tree_sha, "-p", "HEAD", "-m", _SNAPSHOT_MESSAGE,
                        env=_SNAPSHOT_COMMIT_ENV)
    finally:
        for path in (tmp_index, tmp_index + ".lock"):
            if os.path.exists(path):
                os.remove(path)


# ------------------------------------------------------------------ probe


def probe(repo, side_a: str, side_b: str) -> dict:
    """Пробное слияние `side_a`/`side_b` — по stdout, не по коду возврата."""
    proc = worktree.git(repo, "-c", "core.quotepath=false", "merge-tree", "--write-tree",
                        "--name-only", side_a, side_b, check=False)
    if proc.returncode == 0:
        return {"clean": True, "files": []}

    lines = proc.stdout.splitlines()
    first = lines[0] if lines else ""
    if proc.returncode == 1 and _is_oid(first):
        conflict_files = []
        for line in lines[1:]:
            if line == "":
                break
            conflict_files.append(line)
        return {"clean": False, "files": sorted(conflict_files)}

    raise errors.ListikError(
        f"git merge-tree {side_a} {side_b} завершился кодом {proc.returncode}: "
        f"{proc.stderr.strip()}", code=errors.CONFLICT)


# ------------------------------------------------------------------ scope/пересечения


def outside_scope(files: list[str], write_scope: list[str]) -> list[str]:
    """Файлы, не покрытые ни одной записью `write_scope`, в исходном порядке."""
    return [f for f in files if not any(scope_mod.covers(entry, f) for entry in write_scope)]


def common_files(files_a: list[str], files_b: list[str]) -> list[str]:
    """Отсортированное пересечение двух списков путей без дубликатов."""
    return sorted(set(files_a) & set(files_b))
