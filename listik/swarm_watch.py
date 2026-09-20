"""Git-слой наблюдателя роя (swarm-5): тронутые файлы, снимок грязного дерева,
пробное слияние.

Чистый git-слой — модуль не знает о базе Listik и не импортирует `store`/
`server`/`client`/`launcher`. Ни одна функция не пишет в рабочее дерево, индекс
дерева или ссылки репозитория (`checkout`/`reset`/`stash`/`add` без
`GIT_INDEX_FILE`/`commit`/`branch`/`worktree add|remove` здесь не вызываются).

Следующие порции (`listik watch`, заморозка) опираются на функции этого модуля.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone

from . import errors
from . import scope as scope_mod
from . import worktree

#: Маркер «работа в основной ветке» — своя копия `store.MAIN_WORKTREE_MARKERS`:
#: этот модуль не импортирует `store` (см. докстрингу модуля), поэтому набор
#: значений держится здесь отдельно. Любая правка одного набора требует правки
#: другого — их обязаны совпадать.
MAIN_MARKERS = ("main", "master")

#: Журнальная запись `listik watch` о первой замеченной правке дерева задачи.
FIRST_CHANGE_MARK = "рой: первая правка замечена:"

#: Журнальная запись о файлах, тронутых задачей вне её write_scope.
SCOPE_MARK = "рой: вне write_scope:"

#: Журнальная запись о заморозке опоздавшего (лестница реакций, §2 порции c).
FREEZE_MARK = "рой: заморожена:"

#: Журнальная запись у владельца о том, чьи файлы за ним закреплены.
OWN_MARK = "рой: владеет файлами:"

#: Метка, которой опоздавший помечается заморожённым; полное значение — `<метка><id владельца>`.
FROZEN_LABEL = "frozen-by:"

#: Начало `note` отзыва при заморозке — по нему «добивка» узнаёт недоделанную
#: заморозку прошлого прогона (revoke прошёл, метка — нет).
FREEZE_NOTE = "рой: заморожена — пробное слияние с "

#: Актор всех записей заморозки — решение машинное, не подписывается человеком:
#: `--actor`/`LISTIK_OWNER` его не подменяют (см. `_SwarmCards` в `bin/listik`).
SWARM_ACTOR = "agent:listik-swarm"

#: Открытые статусы — своя копия `store.OPEN_STATUSES`: этот модуль `store` не
#: импортирует (см. докстрингу модуля и `MAIN_MARKERS` выше). Наборы обязаны
#: совпадать — правка одного требует правки другого.
OPEN_STATUSES = ("open", "in_progress", "blocked", "review")

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


# ------------------------------------------------------------------ лестница реакций (§2, порция c)


def _is_resumable(card: dict, launch_alive: bool) -> bool:
    """Недоделанная заморозка прошлого прогона: `revoke` прошёл, метка — нет."""
    labels = card.get("labels") or []
    if any((lbl or "").startswith(FROZEN_LABEL) for lbl in labels):
        return False
    if launch_alive or (card.get("launched_by") or ""):
        return False
    for ev in card.get("events") or []:
        if ev.get("kind") == "revoke" and (ev.get("note") or "").startswith(FREEZE_NOTE):
            return True
    return False


def _freeze(cards, owner: str, late: str, conflicts: list[str], late_card: dict, *,
           resumed: bool, dry_run: bool) -> dict:
    """Шаги 1–5 заморозки опоздавшего (§2); `resumed` пропускает шаг 1 (`revoke`)."""
    if dry_run:
        return {"action": "freeze", "task": late, "owner": owner, "files": conflicts,
               "ok": True, "generation": None, "dry_run": True}

    done: list[str] = []
    if resumed:
        generation = late_card.get("generation")
    else:
        try:
            note = f"{FREEZE_NOTE}{owner} конфликтует: {', '.join(conflicts)}"
            result = cards.revoke(late, note)
        except (errors.ListikError, ValueError, KeyError) as exc:
            err = errors.as_error(exc)
            return {"action": "freeze", "task": late, "owner": owner, "files": conflicts,
                   "ok": False, "error": err.message, "done": []}
        done.append("revoke")
        generation = result.get("generation")

    try:
        cards.release(late, note="рой: держатель снят при заморозке")
        done.append("release")

        labels = list(late_card.get("labels") or [])
        label = f"{FROZEN_LABEL}{owner}"
        if label not in labels:
            labels.append(label)
        cards.set_labels(late, labels)
        done.append("labels")

        payload = {"owner": owner, "files": conflicts, "worktree": late_card.get("worktree"),
                  "branch": late_card.get("branch"), "generation": generation,
                  "next": "после слияния владельца — rebase дерева и продолжение (барьер роя)"}
        cards.comment(late, f"{FREEZE_MARK} {json.dumps(payload, ensure_ascii=False)}")
        done.append("comment_late")

        own_payload = {"files": conflicts, "frozen": late}
        cards.comment(owner, f"{OWN_MARK} {json.dumps(own_payload, ensure_ascii=False)}")
    except (errors.ListikError, ValueError, KeyError) as exc:
        err = errors.as_error(exc)
        return {"action": "freeze", "task": late, "owner": owner, "files": conflicts,
               "ok": False, "error": err.message, "done": done}

    decision = {"action": "freeze", "task": late, "owner": owner, "files": conflicts,
               "ok": True, "generation": generation}
    if resumed:
        decision["resumed"] = True
    return decision


def _decide_pair(cards, owner: str, late: str, conflicts: list[str], late_t: dict,
                 late_card: dict, *, dry_run: bool) -> dict:
    """Одно решение лестницы для пары `(owner, late)` с конфликтом (§2)."""
    resumed = _is_resumable(late_card, late_t["launch_alive"])
    if not resumed and (not late_t["launch_alive"] or late_t["status"] not in OPEN_STATUSES):
        decision = {"action": "report", "task": late, "owner": owner, "files": conflicts,
                   "reason": "late_not_running"}
        if dry_run:
            decision["dry_run"] = True
        return decision
    return _freeze(cards, owner, late, conflicts, late_card, resumed=resumed, dry_run=dry_run)


def _decide_all(probes: list[dict], tasks_out: dict, card_map: dict, cards, *,
                dry_run: bool) -> list[dict]:
    """Решения лестницы по всем пробам этого прогона, в порядке `probes`."""
    decisions: list[dict] = []
    frozen_now: set[str] = set()
    for p in probes:
        if "error" in p or p.get("clean"):
            continue
        owner, late = p["a"], p["b"]
        conflicts = p["conflicts"]
        if late in frozen_now or owner in frozen_now:
            continue
        try:
            decision = _decide_pair(cards, owner, late, conflicts, tasks_out[late],
                                    card_map[late], dry_run=dry_run)
        except (errors.ListikError, ValueError, KeyError) as exc:
            # Ошибка одной заморозки никогда не роняет скан целиком (запасная
            # сеть поверх той, что уже внутри `_freeze`).
            err = errors.as_error(exc)
            decision = {"action": "freeze", "task": late, "owner": owner, "files": conflicts,
                       "ok": False, "error": err.message, "done": []}
        decisions.append(decision)
        # Только настоящая заморозка исключает задачу из дальнейших пар этого
        # вызова: `report` ничего не замораживает (§2) — иначе опоздавшая, что
        # просто не в работе, ложно считалась бы обработанной, и конфликт с
        # третьей задачей остался бы без решения вовсе (не лечится следующими
        # прогонами, пока опоздавшая не в работе).
        if decision["action"] == "freeze" and decision.get("ok", True):
            frozen_now.add(late)
    return decisions


# ------------------------------------------------------------------ scan


def _first_change_records(comments: list[dict]) -> list[dict]:
    return sorted(
        (c for c in comments
         if c.get("author") == "agent:listik-swarm"
         and (c.get("text") or "").startswith(FIRST_CHANGE_MARK)),
        key=lambda c: c.get("created_at") or "")


def _recorded_scope_files(comments: list[dict]) -> set[str]:
    """Файлы, уже записанные предыдущими `SCOPE_MARK`-заметками; битый JSON — пропуск."""
    recorded: set[str] = set()
    for c in comments:
        if c.get("author") != "agent:listik-swarm":
            continue
        text = c.get("text") or ""
        if not text.startswith(SCOPE_MARK):
            continue
        try:
            data = json.loads(text[len(SCOPE_MARK):].strip())
        except (ValueError, TypeError):
            continue
        recorded.update(data.get("files") or [])
    return recorded


def _select_candidates(project_path, tasks: list[dict], registered: list[dict],
                       skipped: dict) -> list[dict]:
    """Шаг 2: отбор задач с рабочим деревом, годным для наблюдения."""
    candidates = []
    for task in tasks:
        tid = task.get("id")
        wt = (task.get("worktree") or "").strip()
        if not wt:
            skipped[tid] = "no_worktree"
            continue
        if wt.lower() in MAIN_MARKERS:
            skipped[tid] = "main_worktree"
            continue
        if worktree.same_path(wt, project_path):
            skipped[tid] = "main_tree"
            continue
        if not os.path.isdir(wt):
            skipped[tid] = "missing_dir"
            continue
        entry = next((e for e in registered if worktree.same_path(e["path"], wt)), None)
        if entry is None:
            skipped[tid] = "unregistered"
            continue
        try:
            top = worktree.git_out(wt, "rev-parse", "--show-toplevel")
        except errors.ListikError as exc:
            skipped[tid] = f"git_error: {exc.message}"
            continue
        if not worktree.same_path(top, wt):
            skipped[tid] = "broken_tree"
            continue
        branch = entry["branch"] or (task.get("branch") or "")
        candidates.append({"id": tid, "task": task, "worktree": wt, "branch": branch})
    return candidates


def scan(project_path, tasks: list[dict], cards, *, dry_run: bool = False,
        now: str | None = None) -> dict:
    """Один тик наблюдателя роя: тронутые файлы, первая правка, расхождения, пробы,
    лестница реакций (§2 порции c) — замораживает опоздавшего при конфликте.

    `tasks` — карточки проекта из `list` (открытые и закрытые), `cards` — порт с
    методами `show(task_id) -> dict`, `comment(task_id, text) -> dict`,
    `revoke(task_id, note) -> dict`, `release(task_id, note) -> dict` и
    `set_labels(task_id, labels) -> dict`.
    """
    now_str = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    main_head = worktree.head_sha(project_path)
    registered = worktree.worktree_list(project_path)

    skipped: dict[str, str] = {}
    candidates = _select_candidates(project_path, tasks, registered, skipped)

    tasks_out: dict[str, dict] = {}
    card_map: dict[str, dict] = {}

    for cand in candidates:
        tid = cand["id"]
        card = cards.show(tid)  # ошибка порта не ловится — скан не продолжается
        try:
            ch = changes(cand["worktree"], main_head)
        except errors.ListikError as exc:
            skipped[tid] = f"git_error: {exc.message}"
            continue

        card_map[tid] = card
        live = bool(ch["dirty"] or ch["ahead"] > 0)
        labels = card.get("labels") or []
        frozen_by = None
        for lbl in labels:
            if lbl.startswith("frozen-by:"):
                frozen_by = lbl[len("frozen-by:"):]
                break
        launch_alive = bool(card.get("launched_by") == "listik" and card.get("launch_pid")
                            and not card.get("launch_finished_at"))

        records = _first_change_records(card.get("comments") or [])
        if records:
            first_change = records[0]["created_at"]
        elif live and not dry_run:
            payload = json.dumps({"ts": now_str, "files": ch["files"]}, ensure_ascii=False)
            res = cards.comment(tid, f"{FIRST_CHANGE_MARK} {payload}")
            first_change = res["created_at"]
        else:
            first_change = None

        tasks_out[tid] = {
            "worktree": cand["worktree"], "branch": cand["branch"], "status": card.get("status"),
            "live": live, "frozen_by": frozen_by, "launch_alive": launch_alive,
            "first_change": first_change, "files": ch["files"], "numstat": ch["numstat"],
            "dirty": ch["dirty"], "ahead": ch["ahead"], "outside_scope": [],
        }

    # ------------------------------------------------------------ расхождения
    discrepancies: list[dict] = []
    for tid, t in tasks_out.items():
        files = t["files"]
        if not files:
            continue
        card = card_map[tid]
        write_scope = card.get("write_scope") or []
        outside = outside_scope(files, write_scope)
        t["outside_scope"] = outside
        if not outside:
            continue
        recorded = _recorded_scope_files(card.get("comments") or [])
        new = [f for f in outside if f not in recorded]
        if new and not dry_run:
            payload = json.dumps({"files": new, "declared": write_scope}, ensure_ascii=False)
            cards.comment(tid, f"{SCOPE_MARK} {payload}")
        discrepancies.append({"task": tid, "files": outside, "declared": write_scope, "new": new})

    # ------------------------------------------------------------ порядок владения
    order = sorted(
        (tid for tid, t in tasks_out.items() if t["live"] and t["frozen_by"] is None),
        key=lambda tid: (tasks_out[tid]["first_change"] or "~",
                         card_map[tid].get("launched_at") or "~", tid))

    # ------------------------------------------------------------ пробы
    pairs: list[tuple[str, str, list[str]]] = []
    for i in range(len(order)):
        for j in range(i + 1, len(order)):
            a, b = order[i], order[j]
            common = common_files(tasks_out[a]["files"], tasks_out[b]["files"])
            if common:
                pairs.append((a, b, common))

    needed_trees = {tid for pair in pairs for tid in (pair[0], pair[1])}
    snapshots: dict[str, str] = {}
    failed_trees: dict[str, str] = {}
    for tid in needed_trees:
        try:
            snapshots[tid] = snapshot(tasks_out[tid]["worktree"], dirty=tasks_out[tid]["dirty"])
        except errors.ListikError as exc:
            failed_trees[tid] = f"git_error: {exc.message}"

    if failed_trees:
        for tid, message in failed_trees.items():
            skipped[tid] = message
            tasks_out.pop(tid, None)
        order = [tid for tid in order if tid not in failed_trees]
        discrepancies = [d for d in discrepancies if d["task"] not in failed_trees]
        pairs = [p for p in pairs if p[0] not in failed_trees and p[1] not in failed_trees]

    probes: list[dict] = []
    for a, b, common in pairs:
        try:
            result = probe(project_path, snapshots[a], snapshots[b])
        except errors.ListikError as exc:
            probes.append({"a": a, "b": b, "files": common, "error": exc.message})
            continue
        probes.append({"a": a, "b": b, "files": common,
                       "clean": result["clean"], "conflicts": result["files"]})

    decisions = _decide_all(probes, tasks_out, card_map, cards, dry_run=dry_run)

    return {
        "project_path": project_path, "main_head": main_head, "dry_run": dry_run,
        "truncated": False,
        "tasks": tasks_out, "skipped": skipped, "order": order,
        "probes": probes, "discrepancies": discrepancies, "decisions": decisions,
    }
