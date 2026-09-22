"""Режим роя: на каждый этап свой процесс, карточку ведёт Listik.

Контракт — `docs/specs/swarm-stage-launch.md`. Рой только зовёт `listik launch`.
Первую строку stdout, `claim`, перевод этапа и вердикт пишет сервер.
Режим скила и прямой маршрут сюда не заходят.
"""
from __future__ import annotations

import contextvars
import json

from . import errors as errors_mod
from . import store

DRIVERS = ("skill", "swarm")
STAGE_ORDER = ("s1-spec", "s2-review", "s3-impl", "s4-judge")
ROLE_OF_STAGE = {
    "s1-spec": "spec",
    "s2-review": "critic",
    "s3-impl": "impl",
    "s4-judge": "judge",
}
WORK_ANSWERS = frozenset({"готово", "вопрос", "не смог"})
JUDGE_ANSWERS = frozenset({"зелёный", "красный"})
ANSWER_CAP = 64 * 1024
BODY_CAP = 4000
TAIL_CAP = 1000

ACTOR = "agent:listik"
_CLOSE_CHAIN: contextvars.ContextVar[frozenset[str]] = contextvars.ContextVar(
    "stage_launch_close", default=frozenset())


def card_driver(row, record) -> str:
    """`swarm` / `skill` для конвейера, `direct` для прямого маршрута.

    Снимок `launch_driver` сильнее живого поля маршрута. Пока снимка нет,
    способ читается из маршрута; отсутствие поля — `skill`.
    """
    snap = ""
    keys = row.keys() if hasattr(row, "keys") else ()
    if "launch_driver" in keys:
        snap = (row["launch_driver"] or "").strip()
    if snap in DRIVERS:
        return snap
    if not record or record.get("kind") != "pipeline":
        return "direct"
    driver = (record.get("driver") or "skill").strip()
    return driver if driver in DRIVERS else "skill"


def logical_stage(stage: str | None) -> str:
    text = (stage or "").strip()
    return text or "s1-spec"


def stdout_path(log_path: str) -> str:
    path = str(log_path)
    if path.endswith(".log"):
        return path[:-4] + ".out"
    return path + ".out"


def parse_answer(data: bytes) -> tuple[str, str]:
    """Первая строка и хвост. Строка, которая не кончается в первых 64 КиБ, — пустой ответ."""
    if not data:
        return "", ""
    window = data[:ANSWER_CAP]
    if len(data) > ANSWER_CAP and b"\n" not in window and b"\r" not in window:
        return "", ""
    if window.startswith(b"\xef\xbb\xbf"):
        window = window[3:]
    text = window.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines()
    if not lines:
        return "", ""
    first = lines[0].strip()
    rest = "\n".join(lines[1:]).strip()
    return first, rest


def read_answer(path: str | None) -> tuple[str, str]:
    """Не больше 64 КиБ и одного лишнего байта: по нему видно, что строка обрезана."""
    if not path:
        return "", ""
    try:
        with open(path, "rb") as handle:
            data = handle.read(ANSWER_CAP + 1)
    except OSError:
        return "", ""
    return parse_answer(data)


def _cell_ready(cell) -> bool:
    return isinstance(cell, dict) and bool(cell.get("command")) and bool(cell.get("harness"))


def next_stage_with_role(stage: str | None, roles: dict) -> str | None:
    current = logical_stage(stage)
    if current not in STAGE_ORDER:
        return None
    start = STAGE_ORDER.index(current) + 1
    for nxt in STAGE_ORDER[start:]:
        if _cell_ready(roles.get(ROLE_OF_STAGE[nxt])):
            return nxt
    return None


def first_portion_stage(roles: dict) -> str | None:
    for stage in ("s2-review", "s3-impl", "s4-judge"):
        if _cell_ready(roles.get(ROLE_OF_STAGE[stage])):
            return stage
    return None


def classify(role: str, first: str) -> str:
    if role == "judge":
        return first if first in JUDGE_ANSWERS else "bad"
    if role in ("spec", "critic", "impl"):
        return first if first in WORK_ANSWERS else "bad"
    return "bad"


def release_capture(conn, task_id: str) -> None:
    conn.execute(
        "UPDATE tasks SET launched_by = NULL, dispatch_id = NULL, updated_at = ? WHERE id = ?",
        (store.now_iso(), task_id))
    conn.commit()


def drop_holder(conn, task_id: str) -> None:
    row = conn.execute("SELECT holder FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row and (row["holder"] or "").strip():
        store.update_task(conn, task_id, actor=ACTOR, holder="")


def _ask(conn, task_id: str, text: str) -> None:
    """Вопрос протокола — не ошибка автостарта: `launch_error` показал бы «ОШИБКА»."""
    drop_holder(conn, task_id)
    release_capture(conn, task_id)
    conn.execute("UPDATE tasks SET launch_error = NULL WHERE id = ?", (task_id,))
    store.set_needs_owner(conn, task_id, value=True, text=text, actor=ACTOR)


def _journal(conn, task_id: str, text: str) -> None:
    store.add_comment(conn, task_id, text, author=ACTOR, kind="journal")


def portion_counts(conn, task_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT t.status AS status FROM deps d JOIN tasks t ON t.id = d.issue_id "
        "WHERE d.depends_on = ? AND d.dep_type = 'parent-child'",
        (task_id,)).fetchall()
    total = len(rows)
    cancelled = sum(1 for r in rows if r["status"] == "cancelled")
    done = sum(1 for r in rows if r["status"] == "done")
    live = total - cancelled
    return {"total": total, "live": live, "done": done, "cancelled": cancelled}


def _flags_from_statuses(statuses: list[str], stage: str) -> tuple[bool, bool]:
    total = len(statuses)
    cancelled = sum(1 for item in statuses if item == "cancelled")
    done = sum(1 for item in statuses if item == "done")
    live = total - cancelled
    cancelled_only = (total > 0 and live == 0 and done == 0
                      and stage in ("", "s1-spec"))
    return live > 0, cancelled_only


def portion_flags(conn, row) -> tuple[bool, bool]:
    """`(has_portions, portions_cancelled_only)` для выдачи карточки и для тика роя."""
    return portion_flags_many(conn, [row])[row["id"]]


def portion_flags_many(conn, rows) -> dict[str, tuple[bool, bool]]:
    """Те же флаги одним запросом на пачку карточек. Ошибку запроса не глотает."""
    ids = [row["id"] for row in rows]
    stages = {row["id"]: (row["stage"] or "").strip() for row in rows}
    grouped: dict[str, list[str]] = {task_id: [] for task_id in ids}
    if ids:
        marks = ",".join("?" * len(ids))
        found = conn.execute(
            "SELECT d.depends_on AS parent, t.status AS status FROM deps d "
            "JOIN tasks t ON t.id = d.issue_id "
            "WHERE d.dep_type = 'parent-child' AND d.depends_on IN (" + marks + ")",
            tuple(ids)).fetchall()
        for item in found:
            grouped.setdefault(item["parent"], []).append(item["status"])
    return {task_id: _flags_from_statuses(statuses, stages.get(task_id, ""))
            for task_id, statuses in grouped.items()}


def _swarm_route(conn, row) -> bool:
    keys = row.keys() if hasattr(row, "keys") else ()
    snap = (row["launch_driver"] or "").strip() if "launch_driver" in keys else ""
    if snap == "swarm":
        return True
    if snap == "skill":
        return False
    key = (row["launch_route"] or "").strip()
    if not key:
        return False
    route = conn.execute(
        "SELECT kind, driver FROM routes WHERE key = ?", (key,)).fetchone()
    if route is None or route["kind"] != "pipeline":
        return False
    return (route["driver"] or "skill") == "swarm"


def plan_start(conn, task_id: str, row, record) -> dict:
    """До `Popen`: пропуск пустой роли, claim или вопрос. Захват уже стоит."""
    stage = (row["stage"] or "").strip()
    counts = portion_counts(conn, task_id)
    # Живые порции прячут родителя на любом этапе: ручной `stage` вперёд
    # не должен снова поднять процесс родителя.
    if counts["live"] > 0:
        release_capture(conn, task_id)
        _journal(conn, task_id, "рой: родитель нарезан, запускаются порции")
        return {"action": "skip", "stage_skipped": stage or "s1-spec"}
    if (stage in ("", "s1-spec") and counts["total"] > 0 and counts["live"] == 0
            and counts["done"] == 0):
        release_capture(conn, task_id)
        if not row["needs_owner"]:
            _ask(conn, task_id, "рой: все порции отменены, родитель не закрыт")
            return {"action": "ask",
                    "message": "рой: все порции отменены, родитель не закрыт"}
        return {"action": "skip", "stage_skipped": stage or "s1-spec"}

    roles = record.get("roles") or {}
    key = record.get("key") or (row["launch_route"] or "")
    if not any(_cell_ready(cell) for cell in roles.values()):
        text = f"рой: у маршрута {key} нет роли с командой — маршрут не выбираю"
        _ask(conn, task_id, text)
        return {"action": "ask", "message": text}

    logical = logical_stage(stage)
    role = ROLE_OF_STAGE.get(logical)
    if role not in roles:
        nxt = next_stage_with_role(logical, roles)
        if nxt is None:
            text = f"рой: после {logical} роли нет, карточку не закрываю"
            _ask(conn, task_id, text)
            return {"action": "ask", "message": text}
        note = f"рой: роли {role} нет, этап {logical} → {nxt}"
        store.next_stage(conn, task_id, to_stage=nxt, actor=ACTOR, note=note)
        _journal(conn, task_id, note)
        release_capture(conn, task_id)
        return {"action": "skip", "stage_skipped": nxt}

    cell = roles.get(role) or {}
    if not _cell_ready(cell):
        text = f"рой: у роли {role} маршрута {key} нет command"
        _ask(conn, task_id, text)
        return {"action": "ask", "message": text}

    if not stage:
        store.update_task(conn, task_id, actor=ACTOR, stage="s1-spec")
        stage = "s1-spec"
    harness = cell["harness"]
    try:
        store.claim(conn, task_id, holder=harness, actor=f"agent:{harness}",
                    harness=harness, note=f"рой: взял за {harness}")
    except Exception as exc:
        # `Forbidden` — PermissionError, `NotFound` — KeyError: оба не ListikError.
        # Иначе захват `launched_by` остаётся при пустом pid, и recover карточку не видит.
        text = errors_mod.message_of(exc)
        drop_holder(conn, task_id)
        release_capture(conn, task_id)
        store.set_needs_owner(conn, task_id, value=True, text=text, actor=ACTOR)
        return {"action": "ask", "message": text}
    return {"action": "run", "command": list(cell["command"]), "role": role,
            "harness": harness, "stage": stage}


def _cap(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text[:limit]


def _bad_line(first: str) -> str:
    return "«пусто»" if not first else first


def finish_swarm(conn, task_id: str, *, exit_code: int | None, dispatch_id: str | None,
                 pid: int | None, log_path: str | None, generation: int | None = None) -> None:
    """Разобрать stdout и сдвинуть карточку. Чужое поколение только журналит."""
    ts = store.now_iso()
    cur = conn.execute(
        "UPDATE tasks SET launch_exit_code = ?, launch_finished_at = ?, "
        "launched_by = NULL, dispatch_id = NULL, updated_at = ? "
        "WHERE id = ? AND dispatch_id IS ?",
        (exit_code, ts, ts, task_id, dispatch_id))
    if cur.rowcount == 0:
        conn.commit()
        gen = generation if generation is not None else "неизвестно"
        _journal(conn, task_id,
                 f"автостарт: процесс {pid} поколения {gen} завершился с кодом "
                 f"{exit_code} после отзыва — карточка не менялась")
        return
    try:
        _finish_body(conn, task_id, log_path)
    except Exception as exc:
        _ask(conn, task_id,
             f"рой: не разобрал исход этапа: {errors_mod.message_of(exc)}")


def _finish_body(conn, task_id: str, log_path: str | None) -> None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        conn.commit()
        return
    from . import routes as routes_mod
    record = routes_mod.state(conn).by_key.get(row["launch_route"] or "")
    roles = (record or {}).get("roles") or {}
    stage = logical_stage(row["stage"])
    role = ROLE_OF_STAGE.get(stage, "")
    first, rest = read_answer(stdout_path(log_path) if log_path else None)
    kind = classify(role, first)
    log_text = log_path or ""
    if kind == "готово" and role == "spec" and stage == "s1-spec":
        if _apply_slice(conn, task_id, row, roles):
            return
    if kind == "готово":
        _apply_ready(conn, task_id, stage, roles)
        return
    if kind == "вопрос":
        body = _cap(rest, BODY_CAP)
        text = body or f"рой: этап {stage} ({role}) ответил «вопрос» без текста. Лог: {log_text}"
        _ask(conn, task_id, text)
        return
    if kind == "зелёный":
        _apply_green(conn, task_id)
        return
    if kind == "красный":
        _apply_red(conn, task_id, rest, log_text)
        return
    text = (f"рой: этап {stage} ({role}) не сдал работу "
            f"(первая строка: {_bad_line(first)}). Лог: {log_text}")
    tail = _cap(rest, TAIL_CAP)
    if tail:
        text = text + "\n" + tail
    _ask(conn, task_id, text)


def _apply_ready(conn, task_id: str, stage: str, roles: dict) -> None:
    nxt = next_stage_with_role(stage, roles)
    if nxt is None:
        _ask(conn, task_id, f"рой: после {stage} роли нет, карточку не закрываю")
        return
    note = f"рой: ответ «готово», этап {stage} → {nxt}, держатель снят"
    store.next_stage(conn, task_id, to_stage=nxt, actor=ACTOR, note=note)
    drop_holder(conn, task_id)
    _journal(conn, task_id, note)


def _apply_green(conn, task_id: str) -> None:
    out = store.add_comment(conn, task_id, "VERDICT: PASS", author=ACTOR, kind="verdict")
    if out.get("verdict_accepted") is False:
        _ask(conn, task_id, out.get("message") or "рой: вердикт не принят")
        return
    store.next_stage(conn, task_id, to_stage="done", actor=ACTOR,
                     note="рой: ответ «зелёный», карточка закрыта")
    _journal(conn, task_id, "рой: ответ «зелёный», карточка закрыта")


def _apply_red(conn, task_id: str, rest: str, log_text: str) -> None:
    body = _cap(rest, BODY_CAP)
    if not body:
        body = (f"1. приёмка ответила «красный» без списка правок — лог {log_text} "
                f"— разбери лог и поправь")
    out = store.add_comment(conn, task_id, "VERDICT: FAIL\n" + body,
                            author=ACTOR, kind="verdict")
    if out.get("verdict_accepted") is False:
        _ask(conn, task_id, out.get("message") or "рой: вердикт не принят")
        return
    drop_holder(conn, task_id)
    _journal(conn, task_id, "рой: ответ «красный», возврат на s3-impl, держатель снят")


def _portion_rows(conn, parent_id: str) -> list:
    return conn.execute(
        "SELECT t.* FROM deps d JOIN tasks t ON t.id = d.issue_id "
        "WHERE d.depends_on = ? AND d.dep_type = 'parent-child' ORDER BY t.id",
        (parent_id,)).fetchall()


def _apply_slice(conn, task_id: str, row, roles: dict) -> bool:
    """True — порции были, родителя дальше не ведём."""
    children = [c for c in _portion_rows(conn, task_id) if c["status"] != "cancelled"]
    if not children:
        return False
    ids = [c["id"] for c in children]
    nxt = first_portion_stage(roles)
    if nxt is None:
        _ask(conn, task_id, "рой: порции заведены, но после s1-spec роли нет")
        return True
    parent_route = (row["launch_route"] or "").strip()
    for child in children:
        _assign_portion(conn, child, parent_route, nxt)
    _promote_portion_blocks(conn, set(ids))
    drop_holder(conn, task_id)
    _journal(conn, task_id,
             "рой: нарезано на " + ", ".join(ids) + ", родитель дальше по ролям не идёт")
    return True


def _assign_portion(conn, child, parent_route: str, stage: str) -> None:
    started = bool((child["holder"] or "").strip() or (child["launched_by"] or "").strip())
    child_stage = (child["stage"] or "").strip()
    if child["status"] != "open" or started or child_stage not in ("", "s1-spec"):
        return
    if parent_route and not (child["launch_route"] or "").strip():
        labels = store.route_labels_from_row(child)
        fresh = store.labels_after_route_change(conn, labels, parent_route)
        if fresh is None:
            fresh = labels
        conn.execute(
            "UPDATE tasks SET launch_route = ?, labels = ?, updated_at = ? WHERE id = ?",
            (parent_route, json.dumps(fresh, ensure_ascii=False), store.now_iso(), child["id"]))
    conn.execute(
        "UPDATE tasks SET launch_driver = 'swarm', updated_at = ? WHERE id = ?",
        (store.now_iso(), child["id"]))
    store.update_task(conn, child["id"], actor=ACTOR, stage=stage,
                      note=f"рой: порция на этапе {stage}")


def _promote_portion_blocks(conn, ids: set[str]) -> None:
    if len(ids) < 2:
        return
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT issue_id, depends_on FROM deps WHERE dep_type = 'suggested-blocks' "
        f"AND issue_id IN ({marks}) AND depends_on IN ({marks})",
        (*ids, *ids)).fetchall()
    for edge in rows:
        store.add_dep(conn, edge["issue_id"], edge["depends_on"], "blocks",
                      created_by=ACTOR, confirm=True)


def on_task_done(conn, task_id: str) -> None:
    chain = _CLOSE_CHAIN.get()
    if task_id in chain:
        return
    parent = _parent_row(conn, task_id)
    if parent is None or not _swarm_route(conn, parent):
        return
    if parent["status"] in store.FINAL_STATUSES:
        return
    counts = portion_counts(conn, parent["id"])
    if counts["done"] < 1 or counts["done"] + counts["cancelled"] != counts["total"]:
        return
    token = _CLOSE_CHAIN.set(chain | {task_id})
    try:
        store.update_task(
            conn, parent["id"], actor=ACTOR, status="done", stage="done", holder="",
            close_reason="порции закрыты",
            note="рой: все порции закрыты, родитель закрыт")
        _journal(conn, parent["id"], "рой: все порции закрыты, родитель закрыт")
    finally:
        _CLOSE_CHAIN.reset(token)


def on_task_cancelled(conn, task_id: str) -> None:
    parent = _parent_row(conn, task_id)
    if parent is None or not _swarm_route(conn, parent):
        return
    if parent["status"] in store.FINAL_STATUSES:
        return
    counts = portion_counts(conn, parent["id"])
    if counts["done"] >= 1 and counts["done"] + counts["cancelled"] == counts["total"]:
        on_task_done(conn, task_id)
        return
    if parent["needs_owner"]:
        return
    stage = (parent["stage"] or "").strip()
    if stage not in ("", "s1-spec"):
        return
    if counts["total"] < 1 or counts["live"] != 0 or counts["done"] != 0:
        return
    store.set_needs_owner(conn, parent["id"], value=True,
                          text="рой: все порции отменены, родитель не закрыт", actor=ACTOR)


def _parent_row(conn, task_id: str):
    row = conn.execute(
        "SELECT t.* FROM deps d JOIN tasks t ON t.id = d.depends_on "
        "WHERE d.issue_id = ? AND d.dep_type IN ('parent-child', 'parent') "
        "ORDER BY d.rowid LIMIT 1",
        (task_id,)).fetchone()
    return row
