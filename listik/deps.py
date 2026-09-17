"""Зависимости задач: что реально блокирует, что можно брать, кого ждём.

Две вещи, которые нужны агенту перед тем, как взять задачу:

1. **Жёсткая блокировка** (`dep_type='blocks'`, а также `blocked-by`) — задача ждёт
   завершения другой. Пока блокер открыт, брать нельзя.
2. **Мягкая зависимость** (`parent-child`, `relates-to`, `discovered-from`, `supersedes`) —
   не запрет, но сигнал «сначала почитай».

Тип `parent-child` при этом несёт ещё один смысл: родитель-эпик закрывается,
когда закрыты его дети. Поэтому «могу ли я закрыть эпик» — это отдельная проверка
(`finishable`), не то же самое, что «могу ли я взять задачу» (`ready`).
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
HARD_BLOCKERS = ("blocks", "blocked-by", "waits-for", "conditional-blocks")
# Типы связей, которые стоит прочитать, но они не запрещают работу
SOFT_LINKS = ("parent-child", "relates-to", "related", "discovered-from", "duplicates",
              "supersedes", "parent", "replies-to", "suggested-blocks")

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
    # Return windows may be overridden per project, just like harness routing.
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
                stage: str | None = None, harness: str | None = None,
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
    # Один вызов config.routing (чтение config.toml) на пару (project, stage), а не на
    # каждую задачу — ready --harness на десятках задач не должен читать диск столько же раз.
    harness_cache: dict[tuple, list[str]] = {}
    for row in rows:
        if blocked_by.get(row["id"]):
            continue
        task = store.row_to_task(conn, row)
        if harness:
            key = (row["project"], row["stage"])
            if key not in harness_cache:
                harness_cache[key] = util.allowed_harnesses(row["project"], row["stage"], conn=conn)
            allowed = harness_cache[key]
            if allowed and harness not in allowed:
                continue
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
