"""Зависимости задач: что реально блокирует, что можно брать, кого ждём.

Две вещи, которые нужны агенту перед тем, как взять задачу:

1. **Жёсткая блокировка** (`dep_type='blocks'`, а также `blocked-by`) — задача ждёт
   завершения другой. Пока блокер открыт, брать нельзя.
2. **Мягкая зависимость** (`parent-child`, `relates-to`, `discovered-from`, `supersedes`) —
   не запрет, но сигнал «сначала почитай».

Тип `parent-child` при этом несёт ещё один смысл: родитель-эпик закрывается,
когда закрыты его дети. Поэтому «могу ли я закрыть эпик» — это отдельная проверка
(`finishable`), не то же самое, что «могу ли я взять задачу» (`ready`).

`resource-blocks` — тоже жёсткая блокировка, но машинного происхождения: её ставит
планировщик роя (swarm-2), когда у двух задач одной волны пересекается `write_scope`, и
пересчитывает заново на каждом проходе. Через `dep add` (CLI/HTTP/MCP) её поставить нельзя —
`store.add_dep` отказывает `bad_argument` независимо от актора. От смыслового `blocks`
отличается только происхождением: гейтит `claim`/`ready` точно так же, но смысловое ребро
на той же паре пишется рядом, а не поглощается им, и проверка цикла в `add_dep` его не
учитывает (см. `SEMANTIC_HARD`). Расчёт волн — чистая функция `waves` в этом модуле; запись
ресурсных рёбер в базу — `apply_resource_blocks`: пересчитывает план заново (`waves`) и
переписывает ресурсные рёбра задач рабочего множества под него — устаревшие снимает,
недостающие ставит, смысловые (`SEMANTIC_HARD`) и рёбра чужих задач не трогает. Автор
ресурсного ребра — всегда `RESOURCE_BLOCK_AUTHOR` (машина), ни один вход не может его
переопределить. При расчёте уже существующие ресурсные рёбра во вход не берутся — на каждом
проходе они выводятся заново.

Ещё один машинный слой — обычные `blocks`, поставленные `agent:listik-swarm`
(`PLANNED_BLOCK_AUTHOR`, тот же автор, что у ресурсных рёбер): их ставит проход роя
`listik plan --apply`/`listik rescope --apply` (шаг swarm-6, порции b/c) по графу, который
вернула модель. Запись — `apply_planned_blocks`: переписывает только свои строки `blocks`
и только внутри переданного рабочего множества (оба конца пары должны в нём быть — иначе
проход снял бы своё ребро на задачу другого этапа, отфильтрованную `--stage`), человеческие
и любые другие жёсткие рёбра не трогает, цикл в множестве — отказ без записи. `listik dep rm
<id> <блокер>` снимает такое ребро как обычное `blocks` — специального отката для машинных
рёбер нет.
"""
from __future__ import annotations

import re
import sqlite3

from . import actors as actors_mod
from . import errors as errors_mod
from . import util

OPEN_STATUSES = ("open", "in_progress", "blocked", "review")
FINAL_STATUSES = ("done", "cancelled")

# Типы связей, которые физически запрещают начинать/закрывать задачу
HARD_BLOCKERS = ("blocks", "blocked-by", "waits-for", "conditional-blocks", "resource-blocks")
# Типы связей, которые стоит прочитать, но они не запрещают работу
SOFT_LINKS = ("parent-child", "relates-to", "related", "discovered-from", "duplicates",
              "supersedes", "parent", "replies-to", "suggested-blocks")

# Ресурсный блокер: жёсткий, но машинный — ставит только планировщик роя, через `dep add`
# не принимается (см. `store.add_dep`).
RESOURCE_BLOCK = "resource-blocks"
# Жёсткие типы, которые может поставить человек/агент по смыслу задачи (все, кроме ресурсного).
SEMANTIC_HARD = tuple(t for t in HARD_BLOCKERS if t != RESOURCE_BLOCK)

# Автор ресурсного ребра на всех путях записи (локальный фолбэк, HTTP, MCP): ребро машинное
# по построению, его ставит только планировщик, подпись человеком была бы ложью; префикс
# `agent:` нужен, чтобы `actors.resolve` считал автора машиной. Не переопределяется ни
# `--actor`/`--owner`, ни `X-Listik-Owner`, ни `actor`/`owner` из тела/аргументов запроса.
RESOURCE_BLOCK_AUTHOR = "agent:listik-swarm"

DEP_TITLES = {
    "blocks": "блокирует",
    "blocked-by": "заблокирована",
    "waits-for": "ждёт",
    "conditional-blocks": "блокирует условно",
    "parent-child": "родитель",
    "parent": "родитель",
    "relates-to": "связана",
    "related": "связана",
    "discovered-from": "найдена при",
    "duplicates": "дублирует",
    "supersedes": "заменяет",
    "replies-to": "ответ на",
    "suggested-blocks": "предложенный блокер",
    "resource-blocks": "ресурсный блокер",
}


def _fetch(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        # база старой версии без новых колонок — не роняем чтение
        return []


def _statuses(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["id"]: r["status"] for r in _fetch(conn, "SELECT id, status FROM tasks")}


def _tasks_by_id(conn: sqlite3.Connection, ids: list[str]) -> dict[str, sqlite3.Row]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: r for r in _fetch(conn, f"SELECT * FROM tasks WHERE id IN ({marks})", tuple(ids))}


def _info(conn: sqlite3.Connection, task_id: str, dep_type: str) -> dict:
    """Короткая справка о блокере: что это, кто держит, сколько стоит."""
    from . import store  # локальный импорт: store сам зовёт deps.*
    row = _tasks_by_id(conn, [task_id]).get(task_id)
    if row is None:
        return {"id": task_id, "dep_type": dep_type, "missing": True,
                "title": "(задача не найдена)", "status": "missing"}
    task = store.row_to_task(conn, row)
    return {
        "id": task_id,
        "dep_type": dep_type,
        "dep_title": DEP_TITLES.get(dep_type, dep_type),
        "title": task["title"],
        "status": task["status"],
        "closed": task["status"] in FINAL_STATUSES,
        "project": task["project"],
        "stage": task["stage"],
        "stage_title": task["stage_title"],
        "holder": task["holder"],
        "holder_title": task["holder_title"],
        "holder_age": task["holder_age"],
        "idle_age": task["idle_age"],
        "stale": task["stale"],
        "missing": False,
    }


def blockers(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """Незакрытые жёсткие блокеры задачи — то, из-за чего её нельзя брать."""
    statuses = _statuses(conn)
    rows = _fetch(
        conn,
        f"SELECT depends_on, dep_type FROM deps WHERE issue_id = ? AND dep_type IN "
        f"({','.join('?' * len(HARD_BLOCKERS))})",
        (task_id, *HARD_BLOCKERS),
    )
    out = []
    for r in rows:
        dep = r["depends_on"]
        if statuses.get(dep, "open") in FINAL_STATUSES:
            continue
        out.append(_info(conn, dep, r["dep_type"]))
    out.sort(key=lambda b: (b["status"] != "in_progress", b.get("holder_age") is None))
    return out


def waiting_for(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """Что зависит от этой задачи: пока она не закрыта, эти задачи стоят."""
    rows = _fetch(
        conn,
        f"SELECT issue_id, dep_type FROM deps WHERE depends_on = ? AND dep_type IN "
        f"({','.join('?' * len(HARD_BLOCKERS))})",
        (task_id, *HARD_BLOCKERS),
    )
    statuses = _statuses(conn)
    out = [_info(conn, r["issue_id"], r["dep_type"]) for r in rows
           if statuses.get(r["issue_id"], "open") not in FINAL_STATUSES]
    out.sort(key=lambda b: b["status"] != "in_progress")
    return out


def children(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    rows = _fetch(conn, "SELECT issue_id FROM deps WHERE depends_on = ? AND dep_type = "
                        "'parent-child'", (task_id,))
    return [_info(conn, r["issue_id"], "parent-child") for r in rows]


def parent(conn: sqlite3.Connection, task_id: str) -> dict | None:
    rows = _fetch(conn, "SELECT depends_on FROM deps WHERE issue_id = ? AND dep_type IN "
                        "('parent-child','parent')", (task_id,))
    if not rows:
        return None
    return _info(conn, rows[0]["depends_on"], "parent-child")


def soft_links(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    """Мягкие связи задачи: исходящие плюс входящие `discovered-from`.

    `discovered-from` — единственная мягкая связь, у которой вторая сторона тоже
    должна быть видна: карточка-источник обязана показать, что при работе над ней
    нашли другие задачи (listik-0wpx). Остальные входящие мягкие связи в сводку не
    попадают — они уже есть в `dependents` карточки и дублировали бы список.
    """
    marks = ",".join("?" * len(SOFT_LINKS))
    rows = _fetch(conn, f"SELECT depends_on, dep_type FROM deps WHERE issue_id = ? "
                        f"AND dep_type IN ({marks})", (task_id, *SOFT_LINKS))
    out: list[dict] = []
    seen: set[str] = set()
    for r in rows:
        info = _info(conn, r["depends_on"], r["dep_type"])
        info["incoming"] = False
        out.append(info)
        seen.add(r["depends_on"])
    incoming = _fetch(conn, "SELECT issue_id FROM deps WHERE depends_on = ? "
                            "AND dep_type = 'discovered-from' ORDER BY issue_id", (task_id,))
    for r in incoming:
        discovered = r["issue_id"]
        if discovered in seen:
            continue
        seen.add(discovered)
        info = _info(conn, discovered, "discovered-from")
        info["incoming"] = True
        out.append(info)
    return out


def refresh_blocked_column(conn: sqlite3.Connection) -> int:
    """Обновляет денормализованный blocked_by (список незакрытых жёстких блокеров).

    Статус задачи при этом не трогаем: «заблокирована» — вычисляемое состояние,
    а не то, что кто-то проставил руками и забыл снять (так и появились 125-дневные
    задачи «в работе»).
    """
    statuses = _statuses(conn)
    per_task: dict[str, list[str]] = {}
    for r in _fetch(conn, "SELECT issue_id, depends_on, dep_type FROM deps WHERE dep_type IN "
                          f"({','.join('?' * len(HARD_BLOCKERS))})",
                    tuple(HARD_BLOCKERS)):
        if statuses.get(r["depends_on"], "open") not in FINAL_STATUSES:
            per_task.setdefault(r["issue_id"], []).append(r["depends_on"])
    changed = 0
    for r in _fetch(conn, "SELECT id, blocked_by FROM tasks"):
        new = util.json_dumps(sorted(set(per_task.get(r["id"], []))))
        if (r["blocked_by"] or "[]") != new:
            conn.execute("UPDATE tasks SET blocked_by = ? WHERE id = ?", (new, r["id"]))
            changed += 1
    return changed


def refresh_task(conn: sqlite3.Connection, task_id: str) -> int:
    """Обновляет денормализованный blocked_by у задачи и у тех, кто ждёт её.

    Полный пересчёт по всей базе слишком дорог для каждой правки, а держать
    денормализацию расходящейся нельзя: именно из-за этого раньше «заблокирована»
    переживала свой блокер на месяцы.
    """
    statuses = _statuses(conn)
    targets = {task_id}
    targets |= {r["issue_id"] for r in _fetch(
        conn, "SELECT issue_id FROM deps WHERE depends_on = ?", (task_id,))}
    changed = 0
    marks = ",".join("?" * len(HARD_BLOCKERS))
    for tid in targets:
        rows = _fetch(conn, f"SELECT depends_on FROM deps WHERE issue_id = ? AND dep_type IN "
                            f"({marks})", (tid, *HARD_BLOCKERS))
        fresh = util.json_dumps(sorted({r["depends_on"] for r in rows
                                        if statuses.get(r["depends_on"], "open")
                                        not in FINAL_STATUSES}))
        row = _fetch(conn, "SELECT blocked_by FROM tasks WHERE id = ?", (tid,))
        if row and (row[0]["blocked_by"] or "[]") != fresh:
            conn.execute("UPDATE tasks SET blocked_by = ? WHERE id = ?", (fresh, tid))
            changed += 1
    return changed


def worktree_conflict(conn: sqlite3.Connection, row: sqlite3.Row,
                      holder: str | None = None) -> sqlite3.Row | None:
    """First other task holding the same project tree write lock, if any.

    The lock only applies to *writing* tasks: `s3-impl`, `s4-judge`, or no stage at
    all (a direct claim -> code -> done task) — both for `row` itself and for the
    candidate that might conflict with it. A task on `s1-spec`/`s2-review` neither
    holds the lock nor counts as a conflict. `holder=None` means "any held task
    conflicts" (used by `ready`, which has no specific claimant to exempt);
    passing a holder exempts that same holder (sticky: judge and implementer in
    the same session hold two writing tasks in one worktree).

    The tree is compared by its canonical key, not by the raw `worktree` string
    (`store.worktree_lock_key`): an empty `worktree`, the `main`/`master` markers
    and an explicit path equal to the project directory are one and the same
    "project main tree" and therefore conflict with each other.
    """
    stage = (row["stage"] or "").strip()
    if stage not in ("", "s3-impl", "s4-judge"):
        return None
    from . import store  # store импортирует deps на уровне модуля — импорт отложенный
    project = row["project"] or ""
    project_path = store.project_path(conn, project)
    key = store.worktree_lock_key(row["worktree"], project_path)
    candidates = _fetch(
        conn,
        "SELECT * FROM tasks WHERE coalesce(project,'') = ? AND id != ? AND archived = 0 "
        "AND status IN ('open','in_progress','review','blocked') "
        "AND holder IS NOT NULL AND holder != '' "
        "AND coalesce(stage,'') IN ('', 's3-impl', 's4-judge') ORDER BY id",
        (project, row["id"]),
    )
    for cand in candidates:
        # Исключение «тот же держатель» — по актору, а не по строке: судья
        # `agent:grok` держит вторую пишущую задачу в дереве, где первую держит
        # `grok`. Другой актор по-прежнему конфликтует, `holder=None` — тоже.
        if holder is not None and actors_mod.same_actor(cand["holder"], holder, conn):
            continue
        if store.worktree_lock_key(cand["worktree"], project_path) == key:
            return cand
    return None


def ready(conn: sqlite3.Connection, task_id: str) -> dict:
    """Можно ли брать задачу: короткий ответ и причины.

    `ready` — не занята другим держателем и нет незакрытых жёстких блокеров.
    `claimable` — то же плюс задача ещё в очереди (open/in_progress).
    """
    from . import store
    row = _tasks_by_id(conn, [task_id]).get(task_id)
    if row is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    task = store.row_to_task(conn, row)
    hard = blockers(conn, task_id)
    children_open = [c for c in children(conn, task_id) if not c["closed"]]
    finished = task["status"] in FINAL_STATUSES
    occupied = bool(task["holder"])
    reasons: list[str] = []
    if finished:
        reasons.append(f"задача уже {task['status_title']}")
    if hard:
        names = ", ".join(f"{b['id']} ({b['status']}, {b['idle_age']})" for b in hard[:4])
        reasons.append(f"ждёт завершения: {names}")
    if occupied:
        reasons.append(f"держит {task['holder_title']} ({task['holder_age']})")
    conflict = worktree_conflict(conn, row, holder=None)
    worktree_busy = None
    if conflict is not None:
        conflict_task = store.row_to_task(conn, conflict)
        worktree_busy = {
            "id": conflict["id"], "title": conflict_task["title"],
            "holder": conflict_task["holder"], "holder_title": conflict_task["holder_title"],
            "holder_age": conflict_task["holder_age"], "stale": conflict_task["stale"],
            "worktree": (conflict["worktree"] or "").strip(),
        }
        reasons.append(f"дерево занято: {conflict['id']} (держит {conflict_task['holder_title']})")
    return {
        "task_id": task_id,
        "title": task["title"],
        "status": task["status"],
        "stage": task["stage"],
        "ready": not finished and not hard and not occupied,
        "claimable": not finished and not hard,
        "can_finish": not finished and not [c for c in children_open if c["status"] != "done"],
        "blocked_by": hard,
        "waiting_for": waiting_for(conn, task_id),
        "children_open": children_open,
        "parent": parent(conn, task_id),
        "soft_links": soft_links(conn, task_id),
        "holder": task["holder"],
        "holder_title": task["holder_title"],
        "holder_age": task["holder_age"],
        "stale_holder": bool(task["stale"]),
        "reasons": reasons,
        "verdict": ("можно брать" if not finished and not hard and not occupied else
                    "занята другим" if occupied and not hard and not finished else
                    "нельзя: ждёт другие задачи" if hard else
                    "уже завершена" if finished else "можно брать"),
        "worktree_busy": worktree_busy,
    }


def expire_return_handoffs(conn: sqlite3.Connection, *, task_id: str | None = None) -> int:
    """Release holders whose red-verdict return window has elapsed.

    Scope is every `s3-impl` task with a holder, or a single one when `task_id` is
    given (called from `store.claim` before it decides whether the task is free).
    A holder who was active *after* the return (a `heartbeat`, or a repeat `claim`
    which now also refreshes `holder_at`) keeps the task — only silence counts.
    """
    from . import store
    # Return windows may be overridden per project (`[routing.projects.<slug>]`).
    where = "stage='s3-impl' AND holder IS NOT NULL AND holder != ''"
    params: tuple = ()
    if task_id is not None:
        where += " AND id = ?"
        params = (task_id,)
    rows = _fetch(conn, f"SELECT id, project, holder, holder_at FROM tasks WHERE {where}", params)
    released = 0
    for row in rows:
        ev = _fetch(conn, "SELECT ts FROM events WHERE task_id=? AND kind='stage' AND from_value='s4-judge' AND to_value='s3-impl' ORDER BY ts DESC LIMIT 1", (row["id"],))
        if not ev:
            continue
        routing = util.routing(row["project"], conn=conn)
        hours = float((routing.get("return_window_hours") or 24))
        age = store.hours_since(ev[0]["ts"])
        if age is None or age <= hours:
            continue
        ev_ts = store.parse_ts(ev[0]["ts"])
        holder_ts = store.parse_ts(row["holder_at"])
        active_after = holder_ts is not None and ev_ts is not None and holder_ts > ev_ts
        if active_after:
            continue
        ts = store.now_iso()
        conn.execute("UPDATE tasks SET holder='', holder_note=NULL, updated_at=? WHERE id=?", (ts, row["id"]))
        store.event(conn, row["id"], "release", from_value=row["holder"], to_value="",
                    note=f"истёк срок возврата после красного verdict ({int(hours)} ч без активности)",
                    ts=ts)
        released += 1
    if released:
        conn.commit()
    return released


def ready_tasks(conn: sqlite3.Connection, *, project: str | None = None,
                stage: str | None = None,
                include_occupied: bool = False,
                limit: int = 50, as_owner: str | None = None) -> list[dict]:
    """Задачи, которые можно взять прямо сейчас (нет незакрытых блокеров).

    `as_owner` — кто спрашивает (серверный режим): в ответ идут его задачи и общий
    пул (без владельца). В локальном режиме аргумент игнорируется.
    """
    from . import config as config_mod
    from . import store
    expire_return_handoffs(conn)
    where = ["t.archived = 0", "t.status IN ('open','in_progress','review')"]
    params: list = []
    owner_cfg = config_mod.load()
    if config_mod.is_server_mode(owner_cfg):
        owner_value = config_mod.check_owner(as_owner, owner_cfg)
        if owner_value:
            where.append("(t.owner = ? OR t.owner IS NULL)")
            params.append(owner_value)
    if project:
        where.append("t.project = ?")
        params.append(project)
    if stage:
        where.append("t.stage = ?")
        params.append(stage)
    if not include_occupied:
        where.append("(t.holder IS NULL OR t.holder = '')")
    rows = _fetch(conn, f"SELECT t.* FROM tasks t WHERE {' AND '.join(where)} "
                        "ORDER BY t.priority ASC, t.updated_at DESC LIMIT ?",
                  (*params, limit * 3 if limit else 3000))
    statuses = _statuses(conn)
    hard_rows = _fetch(conn, "SELECT issue_id, depends_on FROM deps WHERE dep_type IN "
                             f"({','.join('?' * len(HARD_BLOCKERS))})", tuple(HARD_BLOCKERS))
    blocked_by: dict[str, list[str]] = {}
    for r in hard_rows:
        if statuses.get(r["depends_on"], "open") not in FINAL_STATUSES:
            blocked_by.setdefault(r["issue_id"], []).append(r["depends_on"])
    out = []
    for row in rows:
        if blocked_by.get(row["id"]):
            continue
        task = store.row_to_task(conn, row)
        task["waiting_for_count"] = len(waiting_for(conn, row["id"]))
        out.append(task)
        if limit and len(out) >= limit:
            break
    return out


def blocked_tasks(conn: sqlite3.Connection, *, project: str | None = None,
                  limit: int = 100) -> list[dict]:
    """Задачи, которые стоят из-за других задач, с объяснением — из-за кого."""
    from . import store
    where = ["t.archived = 0", "t.status IN ('open','in_progress','review','blocked')"]
    params: list = []
    if project:
        where.append("t.project = ?")
        params.append(project)
    rows = _fetch(conn, f"SELECT t.* FROM tasks t WHERE {' AND '.join(where)} "
                        "ORDER BY t.priority ASC, t.updated_at DESC", tuple(params))
    out = []
    for row in rows:
        info = blockers(conn, row["id"])
        if not info:
            continue
        task = store.row_to_task(conn, row)
        task["blockers"] = info
        task["blocked_by"] = [b["id"] for b in info]
        # Дети эпика нужны карточке на доске: «детей открыто N» считается и для
        # заблокированных задач, а не только в карточке задачи.
        task["children_open"] = [c for c in children(conn, row["id"]) if not c["closed"]]
        # «Блокеры стоят»: ни один из них никто не двигает. Открытая задача без держателя
        # тоже стоит — её просто никто не взял, и ждать её молча бессмысленно.
        task["blockers_idle"] = all(b["missing"] or b["stale"] or not b["holder"] for b in info)
        task["blocked_by_stale"] = task["blockers_idle"]
        task["blocked_by_holder"] = next((b["holder_title"] for b in info if b["holder"]), None)
        out.append(task)
        if limit and len(out) >= limit:
            break
    out.sort(key=lambda t: (not t["blocked_by_stale"], t["priority"]))
    return out


def graph(conn: sqlite3.Connection, task_id: str, depth: int = 3) -> dict:
    """Дерево зависимостей вверх (чего ждём) и вниз (кто ждёт нас)."""
    seen_up: set[str] = set()
    seen_down: set[str] = set()

    def up(tid: str, level: int) -> list[dict]:
        if level <= 0 or tid in seen_up:
            return []
        seen_up.add(tid)
        out = []
        for b in blockers(conn, tid):
            out.append({**{k: b.get(k) for k in ("id", "title", "status", "holder_title",
                                                  "idle_age", "dep_type", "missing")},
                        "up": up(b["id"], level - 1)})
        return out

    def down(tid: str, level: int) -> list[dict]:
        if level <= 0 or tid in seen_down:
            return []
        seen_down.add(tid)
        out = []
        for w in waiting_for(conn, tid):
            out.append({**{k: w.get(k) for k in ("id", "title", "status", "holder_title",
                                                  "idle_age", "dep_type", "missing")},
                        "down": down(w["id"], level - 1)})
        return out

    root = _info(conn, task_id, "self")
    return {"task": root, "waits_for": up(task_id, depth), "waited_by": down(task_id, depth),
            "soft_links": soft_links(conn, task_id)}


MENTION_MODES = ("text", "hints")
# id в тексте бывает не упоминанием карточки, а технической ссылкой: компонентом
# файлового пути (`docs/specs/<id>.md`, `/wt/<id>/listik/store.py`) или куском кода
# в кавычках (`x = "<id>"`). Для подсказок `link_hints` такие совпадения — шум
# (listik-0wpx, вердикт grok), для `dep link` остаются рабочими.
# Кортежи, а не строки: `"" in "/\\"` — истина, и id в самом начале текста
# (совпадение с нулевой позиции) ложно считался бы путём.
_PATH_CHARS = ("/", "\\")
_QUOTE_CHARS = ("\"", "'", "`", "«", "»", "“", "”", "„", "‟", "‘", "’")
_MENTION_PAT = re.compile(r"\b([a-z][a-z0-9_]*(?:-[a-z0-9_]+)*)-([a-z0-9]{2,6}(?:\.[0-9]+)?)\b")
_FILE_SUFFIX_PAT = re.compile(r"\.[A-Za-z][A-Za-z0-9_]{0,7}(?![A-Za-z0-9_])")


def _technical_mention(text: str, start: int, end: int) -> bool:
    """Стоит ли id в техническом контексте, а не в прозе.

    Компонент пути — id сразу после разделителя (`/wt/<id>/…`) или сразу перед
    расширением файла (`<id>.md`, `<id>.py`); кавычки и обратные кавычки —
    цитата или фрагмент кода. В обоих случаях это ссылка на файл, а не на
    карточку, и подсказывать по ней `dep link` не нужно."""
    before = text[start - 1] if start else ""
    after = text[end] if end < len(text) else ""
    if before in _PATH_CHARS or after in _PATH_CHARS:
        return True
    if before in _QUOTE_CHARS or after in _QUOTE_CHARS:
        return True
    return after == "." and _FILE_SUFFIX_PAT.match(text, end) is not None


def mentioned(conn: sqlite3.Connection, task_id: str, limit: int = 50, *,
              mode: str = "text") -> list[dict]:
    """Задачи, которые упомянуты в тексте задачи, но не связаны с ней.

    Задачи ссылаются друг на друга ID-ами прямо в описании («упирается в vtt5»,
    «см. zoloto585-search-x3l»). Это не всегда зависимость — поэтому связи
    предлагаются как `relates-to`, а не `blocks`.

    `mode="text"` (по умолчанию) — все совпадения: так работают `dep suggest` и
    `dep link`, где команду запускает человек и сам решает, что связывать.
    `mode="hints"` — для подсказки `link_hints` (`store.link_hints`): id в файловом
    пути и в кавычках за упоминание не считается (listik-0wpx).
    """
    if mode not in MENTION_MODES:
        raise ValueError(f"неизвестный режим упоминаний: {mode}")
    from . import store
    row = _tasks_by_id(conn, [task_id]).get(task_id)
    if row is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    text = " ".join(x or "" for x in (row["title"], row["description"], row["notes"],
                                      row["acceptance"], row["result"]))
    known = {r["id"]: r["id"] for r in _fetch(conn, "SELECT id FROM tasks")}
    linked = {r["depends_on"] for r in _fetch(
        conn, "SELECT depends_on FROM deps WHERE issue_id = ?", (task_id,))}
    linked |= {r["issue_id"] for r in _fetch(
        conn, "SELECT issue_id FROM deps WHERE depends_on = ?", (task_id,))}
    out: list[dict] = []
    seen: set[str] = set()
    for m in _MENTION_PAT.finditer(text):
        ref = f"{m.group(1)}-{m.group(2)}"
        if ref == task_id or ref in seen or ref in linked:
            continue
        if ref not in known:
            continue
        if mode == "hints" and _technical_mention(text, m.start(), m.end()):
            continue
        seen.add(ref)
        info = _info(conn, ref, "relates-to")
        info["reason"] = "упомянута в тексте задачи"
        out.append(info)
        if len(out) >= limit:
            break
    return out


def suggested(conn: sqlite3.Connection, *, project: str | None = None,
             limit: int = 100) -> list[dict]:
    """Предложения агентов (`suggested-blocks`), ожидающие подтверждения человеком.

    Только для зависимых задач, которые ещё живы и не закрыты — предложение по
    архивной или уже завершённой задаче не требует внимания.
    """
    from . import store
    tasks_by_id = _tasks_by_id(conn, [r["id"] for r in _fetch(conn, "SELECT id FROM tasks")])
    rows = _fetch(
        conn,
        "SELECT issue_id, depends_on, created_by, created_at FROM deps "
        "WHERE dep_type='suggested-blocks' ORDER BY created_at ASC",
    )
    out: list[dict] = []
    for r in rows:
        issue_row = tasks_by_id.get(r["issue_id"])
        if issue_row is None:
            continue
        if issue_row["archived"]:
            continue
        if issue_row["status"] not in OPEN_STATUSES:
            continue
        if project and issue_row["project"] != project:
            continue
        dep_row = tasks_by_id.get(r["depends_on"])
        if dep_row is not None:
            dep_task = store.row_to_task(conn, dep_row)
            depends_on_title = dep_task["title"]
            depends_on_status = dep_task["status"]
        else:
            depends_on_title = "(задача не найдена)"
            depends_on_status = "missing"
        out.append({
            "issue_id": r["issue_id"],
            "issue_title": issue_row["title"],
            "issue_stage": issue_row["stage"],
            "project": issue_row["project"],
            "depends_on": r["depends_on"],
            "depends_on_title": depends_on_title,
            "depends_on_status": depends_on_status,
            "created_by": r["created_by"],
            "created_at": r["created_at"],
        })
        if limit and len(out) >= limit:
            break
    return out


def cycles(conn: sqlite3.Connection) -> list[list[str]]:
    """Циклы в жёстких зависимостях: задача, которая ждёт саму себя через других."""
    edges: dict[str, list[str]] = {}
    for r in _fetch(conn, "SELECT issue_id, depends_on FROM deps WHERE dep_type IN "
                          f"({','.join('?' * len(HARD_BLOCKERS))})", tuple(HARD_BLOCKERS)):
        edges.setdefault(r["issue_id"], []).append(r["depends_on"])
    found: list[list[str]] = []
    state: dict[str, int] = {}
    stack: list[str] = []

    def walk(node: str) -> None:
        state[node] = 1
        stack.append(node)
        for nxt in edges.get(node, []):
            if state.get(nxt, 0) == 1:
                idx = stack.index(nxt)
                found.append(stack[idx:] + [nxt])
            elif state.get(nxt, 0) == 0:
                walk(nxt)
        stack.pop()
        state[node] = 2

    for node in list(edges):
        if state.get(node, 0) == 0:
            walk(node)
    return found


def _waves_kahn_layers(
    nodes: set[str],
    ids: list[str],
    base_incoming: dict[str, set[str]],
    resource_incoming: dict[str, set[str]],
) -> tuple[list[list[str]], set[str]]:
    """Слои Кана над `nodes`: базовые рёбра плюс накопленные ресурсные."""
    incoming = {tid: set(base_incoming.get(tid, ())) for tid in nodes}
    for tid, preds in resource_incoming.items():
        if tid in incoming:
            incoming[tid].update(p for p in preds if p in nodes)

    rem = set(nodes)
    layers: list[list[str]] = []
    while rem:
        layer = [tid for tid in ids if tid in rem and not (incoming[tid] & rem)]
        if not layer:
            break
        for tid in layer:
            rem.discard(tid)
        layers.append(layer)
    return layers, rem


def _waves_find_cycles(
    rem: set[str],
    ids: list[str],
    base_incoming: dict[str, set[str]],
    order_index: dict[str, int],
) -> list[list[str]]:
    """Простые циклы в остатке — как `tests/swarm_stand/planner._find_cycles`."""
    adj: dict[str, list[str]] = {tid: [] for tid in rem}
    for tid in ids:
        if tid not in rem:
            continue
        for dep in base_incoming.get(tid, ()):
            if dep in rem:
                adj[dep].append(tid)

    found: set[tuple[str, ...]] = set()

    def canonicalize(cycle: list[str]) -> tuple[str, ...]:
        start = min(range(len(cycle)), key=lambda i: order_index[cycle[i]])
        return tuple(cycle[start:] + cycle[:start])

    def dfs(start: str, node: str, path: list[str], on_path: set[str]) -> None:
        for nxt in adj.get(node, ()):
            if nxt == start:
                found.add(canonicalize(list(path)))
            elif nxt not in on_path:
                path.append(nxt)
                on_path.add(nxt)
                dfs(start, nxt, path, on_path)
                path.pop()
                on_path.discard(nxt)

    for start in ids:
        if start in rem:
            dfs(start, start, [start], {start})

    return [list(c) for c in sorted(found, key=lambda c: tuple(order_index[x] for x in c))]


def find_cycles(ids: list[str], incoming: dict[str, set[str]]) -> list[list[str]]:
    """Простые циклы среди `ids`: чистая функция, форма — как `cycles` у `waves`.

    `ids` — порядок узлов (он же порядок канонизации цикла: наименьший по порядку id
    вперёд, без повтора первого элемента). `incoming[tid]` — множество id, которых
    ждёт `tid`; рёбра на id вне `ids` игнорируются.
    """
    nodes = set(ids)
    _layers, rem = _waves_kahn_layers(nodes, ids, incoming, {})
    if not rem:
        return []
    order_index = {tid: i for i, tid in enumerate(ids)}
    return _waves_find_cycles(rem, ids, incoming, order_index)


def waves(conn: sqlite3.Connection, *, project: str, stage: str | None = None) -> dict:
    """Волны планировщика роя: кто может бежать сейчас, кто ждёт, кто в конфликте.

    Чистая функция расчёта (не пишет в базу — резервирование ресурсных рёбер
    делает `apply_resource_blocks`, следующая порция). Модель алгоритма —
    `tests/swarm_stand/planner.py`: Кан по входящим рёбрам, поиск циклов в
    остатке до арбитража, попарный арбитраж внутри слоя с пересчётом слоёв
    после каждого добавленного ресурсного ребра. Здесь, в отличие от модели:
    вход — рабочее множество проекта из базы (порядок `priority ASC,
    created_at ASC, id ASC`, не «порядок объявления»); уже накопленные
    `resource-blocks` в расчёт не входят — считаются только смысловые жёсткие
    рёбра (`SEMANTIC_HARD`); области — нормализованные пути с покрытием по
    каталогу (`scope.covers`); ключ дерева — только у пишущих этапов
    (`''`/`s3-impl`/`s4-judge`) с непустым `worktree`; пустой `write_scope`
    уводит в `unscoped` только разработку и приёмку (`s3-impl`/`s4-judge`).
    Старт ТЗ (пустой этап и `s1-spec`) и критика (`s2-review`) область не
    пишут и в волну входят без неё. `failed` не моделируется — задача с
    упавшим воркером просто остаётся открытой.
    """
    from . import scope as scope_mod
    from . import store
    from . import store_helpers

    if not (project or "").strip():
        raise errors_mod.BadArgument("нужен проект: волны считаются по одному проекту")

    order_sql = "priority ASC, created_at ASC, id ASC"
    where = ["archived = 0",
             f"status IN ({','.join('?' * len(OPEN_STATUSES))})",
             "project = ?"]
    params: list = [*OPEN_STATUSES, project]
    if stage is not None:
        where.append("stage = ?")
        params.append(stage)
    rows = _fetch(
        conn,
        f"SELECT * FROM tasks WHERE {' AND '.join(where)} ORDER BY {order_sql}",
        tuple(params),
    )

    ids = [r["id"] for r in rows]
    by_id = {r["id"]: r for r in rows}
    order_index = {tid: i for i, tid in enumerate(ids)}

    unroutable: list[str] = []
    unscoped: list[str] = []
    p_ids: list[str] = []
    write_scopes: dict[str, list[str]] = {}
    for tid in ids:
        row = by_id[tid]
        route = (row["launch_route"] or "").strip()
        if not route:
            unroutable.append(tid)
            continue
        ws = store_helpers.json_list(row["write_scope"])
        # ТЗ и критика файлов не правят. Область нужна, чтобы развести
        # одновременную разработку и приёмку.
        stage_v = (row["stage"] or "").strip()
        if stage_v not in ("", "s1-spec", "s2-review") and not ws:
            unscoped.append(tid)
            continue
        write_scopes[tid] = ws
        p_ids.append(tid)

    # Рёбра: смысловые жёсткие, issue_id из P, вместе со статусом depends_on —
    # один запрос (LEFT JOIN, как `blockers()` считает отсутствующую задачу открытой).
    edges: dict[str, list[str]] = {}
    if p_ids:
        marks = ",".join("?" * len(p_ids))
        edge_rows = _fetch(
            conn,
            "SELECT d.issue_id AS issue_id, d.depends_on AS depends_on, "
            "t.status AS dep_status FROM deps d LEFT JOIN tasks t ON t.id = d.depends_on "
            f"WHERE d.dep_type IN ({','.join('?' * len(SEMANTIC_HARD))}) "
            f"AND d.issue_id IN ({marks})",
            (*SEMANTIC_HARD, *p_ids),
        )
        for r in edge_rows:
            if (r["dep_status"] or "") in FINAL_STATUSES:
                continue
            edges.setdefault(r["issue_id"], []).append(r["depends_on"])

    def reason_sort_key(dep: str):
        if dep in order_index:
            return (0, order_index[dep])
        return (1, dep)

    # `blocked`, до устойчивости: причина фиксируется на первом попадании и не
    # пересчитывается; задача, ушедшая в blocked в этом же проходе, сразу видна
    # следующим по O как кандидат (не снимок P на начало прохода).
    working = set(p_ids)
    blocked: dict[str, str] = {}
    changed = True
    while changed:
        changed = False
        for tid in ids:
            if tid not in working:
                continue
            candidates = [dep for dep in edges.get(tid, []) if dep not in working]
            if candidates:
                blocked[tid] = min(candidates, key=reason_sort_key)
                working.discard(tid)
                changed = True

    # Ключи наполняются в порядке проходов «до устойчивости», не по O (задача может
    # заблокироваться на втором проходе, даже если она раньше по O, чем та, что
    # заблокировалась на первом); ответ обязан отдавать ключи по O (см. таблицу
    # ответа), поэтому пересобираем словарь в порядке `ids`.
    blocked = {tid: blocked[tid] for tid in ids if tid in blocked}

    # Базовые входящие рёбра Кана: только между задачами, оставшимися в working.
    base_incoming: dict[str, set[str]] = {}
    for tid in working:
        base_incoming[tid] = {dep for dep in edges.get(tid, []) if dep in working}

    tasks_view = {
        tid: {
            "title": by_id[tid]["title"],
            "priority": by_id[tid]["priority"],
            "status": by_id[tid]["status"],
            "stage": by_id[tid]["stage"],
            "holder": by_id[tid]["holder"],
            "launch_route": by_id[tid]["launch_route"],
            "write_scope": store_helpers.json_list(by_id[tid]["write_scope"]),
            "worktree": by_id[tid]["worktree"],
        }
        for tid in ids
    }

    resource_incoming: dict[str, set[str]] = {}
    layers, rem = _waves_kahn_layers(working, ids, base_incoming, resource_incoming)

    if rem:
        found_cycles = _waves_find_cycles(rem, ids, base_incoming, order_index)
        return {
            "project": project,
            "stage": stage,
            "waves": [],
            "cycles": found_cycles,
            "unroutable": unroutable,
            "unscoped": unscoped,
            "blocked": blocked,
            "resource_blocks": [],
            "tasks": tasks_view,
        }

    resource_blocks: list[list[str]] = []
    resource_pairs: set[tuple[str, str]] = set()
    project_path = store.project_path(conn, project)

    def tree_of(tid: str) -> str | None:
        row = by_id[tid]
        stage_v = (row["stage"] or "").strip()
        if stage_v not in ("", "s3-impl", "s4-judge"):
            return None
        wt = (row["worktree"] or "").strip()
        if not wt:
            return None
        return store.worktree_lock_key(row["worktree"], project_path)

    tree_cache: dict[str, str | None] = {}

    def tree_cached(tid: str) -> str | None:
        if tid not in tree_cache:
            tree_cache[tid] = tree_of(tid)
        return tree_cache[tid]

    while True:
        added = False
        for layer in layers:
            for i, a_id in enumerate(layer):
                for b_id in layer[i + 1:]:
                    conflict = scope_mod.scopes_intersect(
                        write_scopes[a_id], write_scopes[b_id]
                    ) or (tree_cached(a_id) is not None and tree_cached(a_id) == tree_cached(b_id))
                    if conflict and (a_id, b_id) not in resource_pairs:
                        resource_pairs.add((a_id, b_id))
                        resource_blocks.append([a_id, b_id])
                        resource_incoming.setdefault(b_id, set()).add(a_id)
                        added = True
                        break
                if added:
                    break
            if added:
                break
        if not added:
            break
        layers, rem = _waves_kahn_layers(working, ids, base_incoming, resource_incoming)
        if rem:
            raise RuntimeError(
                f"waves: Кан после ресурсного ребра оставил остаток {sorted(rem)} — "
                "ошибка реализации, ресурсное ребро не должно создавать циклы"
            )

    return {
        "project": project,
        "stage": stage,
        "waves": layers,
        "cycles": [],
        "unroutable": unroutable,
        "unscoped": unscoped,
        "blocked": blocked,
        "resource_blocks": resource_blocks,
        "tasks": tasks_view,
    }


def apply_resource_blocks(conn: sqlite3.Connection, *, project: str, stage: str | None = None) -> dict:
    """Записывает в базу ресурсные рёбра под свежий расчёт `waves`.

    Переписывает `resource-blocks` только у задач рабочего множества (`plan["tasks"]`):
    снимает устаревшие, ставит недостающие, смысловые рёбра (`SEMANTIC_HARD`,
    `suggested-blocks`) не трогает. Автор ребра — всегда `RESOURCE_BLOCK_AUTHOR`, у функции
    нет параметра `actor`: подпись машиной нельзя переопределить ни с одного входа. Цикл в
    смысловых рёбрах — отказ (`errors.ListikError`, `code=errors.CONFLICT`), в базу ничего не
    пишется. Один `commit` в конце; исключение по дороге — `rollback`, база как до вызова.
    """
    plan = waves(conn, project=project, stage=stage)
    if plan["cycles"]:
        cycle = plan["cycles"][0]
        raise errors_mod.ListikError(
            "ресурсные рёбра не записаны: в зависимостях цикл "
            + " → ".join(cycle) + " → " + cycle[0],
            code=errors_mod.CONFLICT,
            hint="разорви цикл: listik dep rm <id> <блокер>",
        )

    working = set(plan["tasks"].keys())
    desired = {(later, earlier) for earlier, later in plan["resource_blocks"]}

    existing: set[tuple[str, str]] = set()
    if working:
        marks = ",".join("?" * len(working))
        rows = _fetch(
            conn,
            f"SELECT issue_id, depends_on FROM deps WHERE dep_type = 'resource-blocks' "
            f"AND issue_id IN ({marks})",
            tuple(working),
        )
        existing = {(r["issue_id"], r["depends_on"]) for r in rows}

    to_remove = existing - desired
    to_add = desired - existing
    kept = len(existing & desired)

    try:
        for issue_id, depends_on in to_remove:
            conn.execute(
                "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='resource-blocks'",
                (issue_id, depends_on),
            )
        for issue_id, depends_on in to_add:
            conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?,?,'resource-blocks',?)",
                (issue_id, depends_on, RESOURCE_BLOCK_AUTHOR),
            )
        touched: set[str] = set()
        for issue_id, depends_on in (*to_remove, *to_add):
            touched.add(issue_id)
            touched.add(depends_on)
        for tid in touched:
            refresh_task(conn, tid)
    except Exception:
        conn.rollback()
        raise
    conn.commit()

    order_index = {tid: i for i, tid in enumerate(plan["tasks"].keys())}
    added = [[earlier, later] for earlier, later in plan["resource_blocks"]
             if (later, earlier) in to_add]
    removed = sorted(
        ([earlier, later] for later, earlier in to_remove),
        key=lambda pair: (order_index.get(pair[1], len(order_index)), pair[0]),
    )

    return {
        "project": project,
        "stage": stage,
        "added": added,
        "removed": removed,
        "kept": kept,
        "waves": plan,
    }


#: Автор машинных рёбер `blocks`, поставленных проходом роя (`plan`/`rescope --apply`) —
#: тот же машинный автор, что у ресурсных рёбер: подпись не переопределяется ни с
#: одного входа (у `apply_planned_blocks` нет параметра `actor`).
PLANNED_BLOCK_AUTHOR = RESOURCE_BLOCK_AUTHOR


def apply_planned_blocks(conn: sqlite3.Connection, *, working: list[str],
                         edges: list[list[str]]) -> dict:
    """Записывает граф `blocks`, который вернула модель роя (`plan`/`rescope --apply`).

    `working` — id рабочего множества прохода, в порядке прохода. `edges` — пары
    `[раньше, позже]` («позже ждёт раньше», форма — как `resource_blocks` у `waves`).
    Переписывает только свои строки `blocks` (`dep_type='blocks'`,
    `created_by=PLANNED_BLOCK_AUTHOR`) и только те, у которых **оба** конца в `working`:
    ребро на задачу другого этапа (отфильтрованную `--stage`) не снимается. Смысловые
    жёсткие рёбра любого другого автора (человеческие `blocks`, `waits-for`,
    `conditional-blocks`) не трогает. Цикл в рабочем множестве — отказ до записи
    (`errors.ListikError`, `code=errors.CONFLICT`). Один `commit` в конце; исключение по
    дороге — `rollback`, база как до вызова. Событий в `events` не пишется (как у
    `add_dep`/`apply_resource_blocks`).
    """
    from . import store_helpers  # deps импортируется store на уровне модуля — отложенный импорт

    working_list = list(working)
    working_set = set(working_list)

    validated: list[tuple[str, str]] = []
    for edge in edges:
        if not (isinstance(edge, (list, tuple)) and len(edge) == 2
                and isinstance(edge[0], str) and isinstance(edge[1], str)):
            raise errors_mod.BadArgument(f"edges: элемент должен быть парой строк: {edge!r}")
        earlier, later = edge
        if earlier == later:
            raise errors_mod.BadArgument(f"edges: задача не может ждать сама себя: {earlier}")
        if earlier not in working_set or later not in working_set:
            raise errors_mod.BadArgument(
                f"edges: {earlier} → {later} — обе задачи должны быть в working")
        validated.append((earlier, later))

    for earlier, later in validated:
        for tid in (earlier, later):
            if not store_helpers.task_exists(conn, tid):
                raise errors_mod.NotFound(f"задача не найдена: {tid}")

    if not working_set:
        return {"added": [], "removed": [], "kept": 0, "covered": [], "promoted": 0}

    desired = {(later, earlier) for earlier, later in validated}

    marks_w = ",".join("?" * len(working_list))
    marks_semantic = ",".join("?" * len(SEMANTIC_HARD))
    rows = _fetch(
        conn,
        "SELECT issue_id, depends_on, dep_type, created_by FROM deps "
        f"WHERE dep_type IN ({marks_semantic}) AND issue_id IN ({marks_w}) "
        f"AND depends_on IN ({marks_w})",
        (*SEMANTIC_HARD, *working_list, *working_list),
    )
    own: set[tuple[str, str]] = set()
    other: set[tuple[str, str]] = set()
    for r in rows:
        pair = (r["issue_id"], r["depends_on"])
        if r["dep_type"] == "blocks" and r["created_by"] == PLANNED_BLOCK_AUTHOR:
            own.add(pair)
        else:
            other.add(pair)

    # ponytail: цикл ищется в рабочем множестве; путь через открытую задачу вне
    # множества (фильтр --stage) не виден — расширить до проекта, если такое случится.
    # Рёбра с концом вне множества не удаляем.
    incoming: dict[str, set[str]] = {}
    for issue_id, depends_on in (*other, *desired):
        incoming.setdefault(issue_id, set()).add(depends_on)
    found_cycles = find_cycles(working_list, incoming)
    if found_cycles:
        cycle = found_cycles[0]
        raise errors_mod.ListikError(
            "рёбра не записаны: граф содержит цикл " + " → ".join(cycle) + " → " + cycle[0],
            code=errors_mod.CONFLICT,
            hint="разорви цикл: listik dep rm <id> <блокер>",
        )

    to_add = desired - own - other
    covered = desired & other
    to_remove = own - desired
    kept = len(own & desired)

    suggested_rows = set()
    if to_add:
        marks_add = ",".join("(?,?)" for _ in to_add)
        params: list[str] = []
        for issue_id, depends_on in to_add:
            params.extend((issue_id, depends_on))
        suggested_rows = {
            (r["issue_id"], r["depends_on"]) for r in _fetch(
                conn,
                "SELECT issue_id, depends_on FROM deps WHERE dep_type='suggested-blocks' "
                f"AND (issue_id, depends_on) IN ({marks_add})",
                tuple(params),
            )
        }

    try:
        for issue_id, depends_on in to_remove:
            conn.execute(
                "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='blocks' "
                "AND created_by=?",
                (issue_id, depends_on, PLANNED_BLOCK_AUTHOR),
            )
        for issue_id, depends_on in suggested_rows:
            conn.execute(
                "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                (issue_id, depends_on),
            )
        for issue_id, depends_on in to_add:
            conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) "
                "VALUES(?,?,'blocks',?)",
                (issue_id, depends_on, PLANNED_BLOCK_AUTHOR),
            )
        touched: set[str] = set()
        for issue_id, depends_on in (*to_remove, *to_add):
            touched.add(issue_id)
            touched.add(depends_on)
        for tid in touched:
            refresh_task(conn, tid)
    except Exception:
        conn.rollback()
        raise
    conn.commit()

    order_index = {tid: i for i, tid in enumerate(working_list)}
    added = [[earlier, later] for earlier, later in validated if (later, earlier) in to_add]
    removed = sorted(
        ([earlier, later] for later, earlier in to_remove),
        key=lambda pair: (order_index.get(pair[1], len(order_index)), pair[0]),
    )
    covered_pairs = [[earlier, later] for earlier, later in validated
                     if (later, earlier) in covered]

    return {
        "added": added,
        "removed": removed,
        "kept": kept,
        "covered": covered_pairs,
        "promoted": len(suggested_rows),
    }
