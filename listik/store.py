"""Ядро трекера: создание, правка, переходы этапов, доска, статистика.

Вся запись идёт через эти функции. Если Listik запущен сервером — их вызывает
HTTP-слой; если нет, CLI работает с базой напрямую (WAL выдержит).
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import string
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import actors as actors_mod
from . import config as config_mod
from . import deps as deps_mod
from . import textutil

OPEN_STATUSES = ("open", "in_progress", "blocked", "review")
FINAL_STATUSES = ("done", "cancelled")
PIPELINE_STAGES = ("s1-spec", "s2-review", "s3-impl", "s4-judge")
STAGE_TITLES = {
    "s1-spec": "1. ТЗ и чек-лист",
    "s2-review": "2. Второе мнение",
    "s3-impl": "3. Реализация",
    "s4-judge": "4. Проверка и коммит",
    "done": "готово",
}
STATUS_TITLES = {
    "open": "открыта",
    "in_progress": "в работе",
    "blocked": "заблокирована",
    "review": "на проверке",
    "done": "готова",
    "cancelled": "отменена",
}
PRIORITY_TITLES = {0: "P0 срочно", 1: "P1 высокий", 2: "P2 обычный", 3: "P3 низкий", 4: "P4 потом"}

_SUFFIX_ALPHABET = string.ascii_lowercase + string.digits


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def hours_since(value: str | None) -> float | None:
    ts = parse_ts(value)
    if not ts:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600.0


def human_age(value: str | None) -> str:
    h = hours_since(value)
    if h is None:
        return "—"
    if h < 1:
        return f"{int(h * 60)} мин"
    if h < 48:
        return f"{int(h)} ч"
    return f"{int(h / 24)} дн"


# ------------------------------------------------------------------ задачи

def gen_id(conn: sqlite3.Connection, project: str | None, *, prefix: str | None = None) -> str:
    base = (prefix or project or "lk").strip()
    base = re.sub(r"[^0-9a-zA-Zа-яА-Я_\-]+", "-", base).strip("-").lower() or "lk"
    for _ in range(50):
        cand = f"{base}-{''.join(random.choice(_SUFFIX_ALPHABET) for _ in range(4))}"
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (cand,)).fetchone():
            return cand
    return f"{base}-{int(datetime.now().timestamp())}"


def event(conn: sqlite3.Connection, task_id: str, kind: str, *, from_value=None, to_value=None,
          actor: str | None = None, harness: str | None = None, note: str | None = None,
          duration_s: int | None = None, ts: str | None = None) -> None:
    conn.execute(
        "INSERT INTO events(task_id, ts, kind, from_value, to_value, actor, harness, note, duration_s) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (task_id, ts or now_iso(), kind, from_value, to_value, actor, harness, note, duration_s),
    )


def _index_task(conn: sqlite3.Connection, task_id: str) -> None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        return
    body = "\n\n".join(
        x for x in (
            textutil.clean_markdown(row["description"]),
            textutil.clean_markdown(row["acceptance"]),
            textutil.clean_markdown(row["design"]),
            textutil.clean_markdown(row["notes"]),
            textutil.clean_markdown(row["result"]),
            # Imported records must remain discoverable by their legacy ID.
            textutil.clean_markdown(row["external_ref"]),
        ) if x
    )
    conn.execute("DELETE FROM task_fts WHERE task_id = ?", (task_id,))
    conn.execute(
        "INSERT INTO task_fts(task_id, title, body, labels) VALUES(?,?,?,?)",
        (task_id, row["title"], body, row["labels"] or ""),
    )
    h = textutil.text_hash(row["title"], body, row["labels"] or "")
    conn.execute("UPDATE tasks SET content_hash = ?, indexed_at = ? WHERE id = ?", (h, now_iso(), task_id))


def _index_comment(conn: sqlite3.Connection, comment_id: str) -> None:
    row = conn.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
    if not row:
        return
    clean = textutil.clean_markdown(row["text"])
    conn.execute("DELETE FROM comment_fts WHERE comment_id = ?", (comment_id,))
    if clean:
        conn.execute(
            "INSERT INTO comment_fts(comment_id, task_id, body) VALUES(?,?,?)",
            (comment_id, row["task_id"], clean),
        )


def create_task(
    conn: sqlite3.Connection,
    *,
    title: str,
    project: str | None = None,
    description: str = "",
    acceptance: str = "",
    design: str = "",
    notes: str = "",
    result: str = "",
    issue_type: str = "task",
    status: str = "open",
    priority: int = 2,
    assignee: str | None = None,
    stage: str | None = None,
    labels: list[str] | None = None,
    spec_path: str | None = None,
    checklist_path: str | None = None,
    review_path: str | None = None,
    decision_path: str | None = None,
    journal_path: str | None = None,
    external_ref: str | None = None,
    source: str = "native",
    task_id: str | None = None,
    created_by: str | None = None,
    created_at: str | None = None,
    needs_owner: bool = False,
    harness: str | None = None,
    autostart: bool = False,
    route: str | None = None,
) -> dict:
    """Создать задачу. `autostart`/`route` только сохраняются: процесс запускает
    не эта функция, а `listik/launcher.py` (сервер — сразу после создания, CLI
    в локальном режиме — отказом, потому что сервера нет)."""
    if not title.strip():
        raise ValueError("title не может быть пустым")
    tid = task_id or gen_id(conn, project)
    ts = created_at or now_iso()
    actor_key, kind = actors_mod.resolve(created_by, conn)
    if created_by:
        actors_mod.remember(conn, created_by, actor_key, kind)
    conn.execute(
        """
        INSERT INTO tasks(id, project, title, description, acceptance, design, notes, result, status, stage,
                          stage_at, priority, issue_type, assignee, labels, spec_path, journal_path,
                          source, external_ref, created_at, created_by, updated_at, needs_owner,
                          checklist_path, review_path, decision_path, autostart, launch_route)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title, description=excluded.description, acceptance=excluded.acceptance,
            design=excluded.design, notes=excluded.notes, status=excluded.status,
            priority=excluded.priority, issue_type=excluded.issue_type, labels=excluded.labels,
            updated_at=excluded.updated_at
        """,
        (tid, project, title, description, acceptance, design, notes, result, status, stage,
         ts if stage else None, priority, issue_type, assignee,
         json.dumps(labels or [], ensure_ascii=False), spec_path, journal_path,
         source, external_ref, ts, created_by, ts, 1 if needs_owner else 0,
         checklist_path, review_path, decision_path, 1 if autostart else 0, route),
    )
    event(conn, tid, "created", to_value=status, actor=actor_key, harness=harness,
          note=f"создана: {title[:120]}", ts=ts)
    if stage:
        event(conn, tid, "stage", from_value=None, to_value=stage, actor=actor_key,
              harness=harness, ts=ts)
    _index_task(conn, tid)
    if spec_path or journal_path or checklist_path or review_path or decision_path:
        try:
            from . import documents
            documents.index_task_documents(conn, tid)
        except Exception as exc:  # document availability must not break task creation
            event(conn, tid, "document_error", note=str(exc))
    conn.commit()
    return get_task(conn, tid)


UPDATABLE = {
    "title", "description", "acceptance", "design", "notes", "result", "status", "stage",
    "priority", "issue_type", "assignee", "holder", "holder_note", "project", "labels",
    "spec_path", "checklist_path", "review_path", "decision_path", "journal_path", "worktree", "branch", "close_reason", "needs_owner",
    "external_ref", "archived",
}


def delete_task(conn: sqlite3.Connection, task_id: str) -> None:
    """Remove a task and every derived row: FTS entries, embeddings and indexed
    documents/chunks. ``documents``/``document_chunks`` also cascade via the foreign
    key on ``task_id``, but they are cleared explicitly here so removal does not
    depend on ``PRAGMA foreign_keys`` staying on for a given connection."""
    conn.execute("DELETE FROM task_fts WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM comment_fts WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM document_chunk_fts WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM embeddings WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM documents WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM comments WHERE task_id=?", (task_id,))
    conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()


def update_task(conn: sqlite3.Connection, task_id: str, *, actor: str | None = None,
                harness: str | None = None, note: str | None = None, **fields) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError(f"задача не найдена: {task_id}")
    sets, params = [], []
    changes: list[tuple[str, object, object]] = []
    for key, value in fields.items():
        if key not in UPDATABLE or value is None:
            continue
        if isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        if key == "needs_owner":
            value = 1 if value else 0
        old = row[key]
        if str(old) == str(value):
            continue
        sets.append(f"{key} = ?")
        params.append(value)
        changes.append((key, old, value))

    actor_key, kind = actors_mod.resolve(actor, conn)
    ts = now_iso()

    if not changes:
        ename = actors_mod.display(actor_key) if actor_key else "—"
        return {**dict(row), "unchanged": True, "requested_by": ename}

    for key, old, new in changes:
        if key == "stage":
            stage_old = row["stage"]
            dur = None
            if row["stage_at"]:
                h = hours_since(row["stage_at"])
                dur = int(h * 3600) if h else None
            sets.append("stage_at = ?")
            params.append(ts)
            event(conn, task_id, "stage", from_value=stage_old, to_value=new,
                  actor=actor_key, harness=harness, note=note, duration_s=dur)
            if new in PIPELINE_STAGES:
                # задача в конвейере — держателя не сбрасываем
                pass
        elif key == "status":
            event(conn, task_id, "status", from_value=old, to_value=new,
                  actor=actor_key, harness=harness, note=note)
            if new == "in_progress" and not row["started_at"]:
                sets.append("started_at = ?")
                params.append(ts)
            if new in FINAL_STATUSES and not row["closed_at"]:
                sets.append("closed_at = ?")
                params.append(ts)
                if new == "done" and not row["started_at"]:
                    sets.append("started_at = ?")
                    params.append(ts)
            if new not in FINAL_STATUSES and row["closed_at"]:
                sets.append("closed_at = NULL")
                sets.append("close_reason = NULL")
        elif key == "holder":
            event(conn, task_id, "release" if not new else "claim",
                  from_value=old, to_value=new, actor=actor_key, harness=harness, note=note)
            if new:
                sets.append("holder_at = ?")
                params.append(ts)
        elif key == "needs_owner":
            event(conn, task_id, "question" if new else "answer",
                  from_value=old, to_value=new, actor=actor_key, harness=harness, note=note)
        elif key == "assignee":
            event(conn, task_id, "note", from_value=old, to_value=new, actor=actor_key,
                  harness=harness, note=f"исполнитель: {note or ''}".strip())

    sets.append("updated_at = ?")
    params.append(ts)
    params.append(task_id)
    conn.execute(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", params)
    # статус изменился — пересчитываем флаг блокировки у этой задачи и её ждущих
    deps_mod.refresh_task(conn, task_id)
    _index_task(conn, task_id)
    if any(k in fields for k in ("spec_path", "checklist_path", "review_path", "decision_path", "journal_path")):
        try:
            from . import documents
            documents.index_task_documents(conn, task_id)
        except Exception as exc:
            event(conn, task_id, "document_error", note=str(exc))
    conn.commit()
    return get_task(conn, task_id)


def set_needs_owner(conn: sqlite3.Connection, task_id: str, *, value: bool,
                    text: str | None = None, actor: str | None = None,
                    harness: str | None = None) -> dict:
    """Поднять/снять флаг «нужен человек», всегда фиксируя вопрос/ответ в истории.

    В отличие от `update_task(..., needs_owner=...)`, пишет комментарий и событие
    при каждом вызове — даже если флаг уже стоит в нужном значении, чтобы второй
    вопрос с другим текстом не терялся.
    """
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError(f"задача не найдена: {task_id}")
    old_value = bool(row["needs_owner"])
    new_value = bool(value)

    actor_key, _kind = actors_mod.resolve(actor, conn)
    ts = now_iso()

    clean_text = (text or "").strip()
    if clean_text:
        add_comment(conn, task_id, clean_text, author=actor,
                    kind="question" if new_value else "answer", harness=harness)

    event(conn, task_id, "question" if new_value else "answer",
          from_value=old_value, to_value=new_value, actor=actor_key, harness=harness,
          note=text, ts=ts)

    conn.execute("UPDATE tasks SET needs_owner = ?, updated_at = ? WHERE id = ?",
                 (1 if new_value else 0, ts, task_id))
    deps_mod.refresh_task(conn, task_id)
    _index_task(conn, task_id)
    conn.commit()
    return get_task(conn, task_id)


def claim(conn: sqlite3.Connection, task_id: str, *, holder: str, harness: str | None = None,
          note: str | None = None, force: bool = False) -> dict:
    """Агент берёт задачу: держатель, heartbeat, статус в работе.

    Заблокированную задачу взять нельзя: сначала надо закрыть блокеры.
    Обойти можно только явным force (и это останется в истории). Лок рабочего
    дерева распространяется только на пишущие задачи (`s3-impl`/`s4-judge`/без
    этапа) — держатель на `s1-spec`/`s2-review` дерево не занимает.
    """
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError(f"задача не найдена: {task_id}")
    # Harness must be allowed for the task's project/stage.
    allowed = config_mod.allowed_harnesses(row["project"], row["stage"], conn=conn)
    if harness and allowed and harness not in allowed:
        raise ValueError(f"harness {harness} не разрешён для этапа {row['stage'] or 's1-spec'} проекта {row['project'] or '—'}; разрешены: {', '.join(allowed)}")
    if row["status"] in FINAL_STATUSES:
        raise ValueError(f"задача {task_id} уже {row['status']}")
    # A silent holder past the red-verdict return window loses the task before we
    # even look at who holds it — otherwise this claim would just bounce off "уже
    # удерживается" instead of taking over.  Re-read the row: expiry commits its own
    # UPDATE, which the row fetched above cannot see.
    deps_mod.expire_return_handoffs(conn, task_id=task_id)
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    # Claim is idempotent for the current holder, but must never replace another
    # holder.  This also makes a repeated claim in the same worktree safe.
    current_holder = (row["holder"] or "").strip()
    if current_holder:
        if current_holder == holder:
            # Refresh holder_at only: this both answers the repeated claim and,
            # crucially, extends the red-verdict return window (see
            # deps.expire_return_handoffs) so an agent that resumes with `claim`
            # rather than `heartbeat` is not treated as having gone silent.
            ts = now_iso()
            conn.execute("UPDATE tasks SET holder_at = ?, updated_at = ? WHERE id = ?",
                        (ts, ts, task_id))
            conn.commit()
            return get_task(conn, task_id)
        cur_task = row_to_task(conn, row)
        stale_note = ", молчит — брошена?" if cur_task["stale"] else ""
        raise ValueError(
            f"задача {task_id} уже удерживается {cur_task['holder_title']} "
            f"({cur_task['holder_age']}{stale_note}). Варианты: дождаться, "
            f"listik release {task_id} (если держатель мёртв), или взять другую задачу из listik ready"
        )
    # A repository/worktree is a write lock, but only for writing tasks (s3-impl,
    # s4-judge, or no stage at all — a direct claim -> code -> done task).
    conflict = deps_mod.worktree_conflict(conn, row, holder=holder)
    if conflict is not None:
        conflict_task = row_to_task(conn, conflict)
        wt = (row["worktree"] or "").strip()
        wt_label = wt or "основное"
        stale_note = ", молчит — брошена?" if conflict_task["stale"] else ""
        raise ValueError(
            f"рабочее дерево {wt_label} проекта {row['project'] or '—'} занято задачей "
            f"{conflict['id']} ({conflict_task['title'][:80]}), держит {conflict_task['holder_title']} "
            f"{conflict_task['holder_age']}{stale_note}. Варианты: дождаться или listik release "
            f"{conflict['id']}, указать этой задаче другое дерево (listik set {task_id} worktree=/путь), "
            "или взять другую задачу"
        )
    state = deps_mod.ready(conn, task_id)
    if state["blocked_by"] and not force:
        names = "; ".join(f"{b['id']} — {b['title'][:80]} [{b['status']}"
                          + (f", держит {b['holder_title']} {b['idle_age']}" if b["holder"] else "")
                          + "]" for b in state["blocked_by"][:5])
        raise ValueError(
            f"задача {task_id} заблокирована и брать её нельзя.\n"
            f"Ждём завершения: {names}\n"
            "Варианты: взять сам блокер, поставить блокеру needs-owner, "
            "или осознанно обойти запрет: claim --force (останется в истории)"
        )
    if state["blocked_by"] and force:
        event(conn, task_id, "note", actor=holder, harness=harness,
              note=f"ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ: {', '.join(b['id'] for b in state['blocked_by'])}")
    extra = {}
    if row["status"] == "open":
        extra["status"] = "in_progress"
    out = update_task(conn, task_id, holder=holder, assignee=row["assignee"] or holder,
                      harness=harness, note=note or f"взял в работу: {holder}", **extra)
    return out


def heartbeat(conn: sqlite3.Connection, task_id: str, *, holder: str, note: str | None = None,
              harness: str | None = None, min_interval_min: int = 10) -> dict:
    row = conn.execute("SELECT holder, holder_at, holder_note FROM tasks WHERE id = ?",
                       (task_id,)).fetchone()
    if not row:
        raise KeyError(f"задача не найдена: {task_id}")
    ts = now_iso()
    conn.execute("UPDATE tasks SET holder = ?, holder_at = ?, holder_note = ?, updated_at = ? "
                 "WHERE id = ?", (holder, ts, note or row["holder_note"], ts, task_id))
    last = parse_ts(row["holder_at"])
    if not last or (datetime.now(timezone.utc) - last) > timedelta(minutes=min_interval_min):
        event(conn, task_id, "heartbeat", to_value=holder, note=note, harness=harness)
    conn.commit()
    return get_task(conn, task_id)


def add_comment(conn: sqlite3.Connection, task_id: str, text: str, *, author: str | None = None,
                kind: str = "comment", harness: str | None = None,
                created_at: str | None = None) -> dict:
    if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone():
        raise KeyError(f"задача не найдена: {task_id}")
    failed = parse_verdict(text) if kind == "verdict" else False
    actor_key, a_kind = actors_mod.resolve(author, conn)
    if author:
        actors_mod.remember(conn, author, actor_key, a_kind)
    cid = f"{task_id}:{int(datetime.now().timestamp() * 1000)}:{random.randint(100, 999)}"
    ts = created_at or now_iso()
    conn.execute(
        "INSERT INTO comments(id, task_id, author, kind, text, created_at) VALUES(?,?,?,?,?,?)",
        (cid, task_id, author, kind, text, ts),
    )
    conn.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (ts, task_id))
    event(conn, task_id, "comment", to_value=kind, actor=actor_key, harness=harness,
          note=text[:200], ts=ts)
    # Red verdict at s4 returns work to the implementer (sticky) within a configured window.
    if failed:
        task_row = conn.execute("SELECT stage, project FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if task_row and task_row["stage"] == "s4-judge":
            next_stage(conn, task_id, to_stage="s3-impl", actor=author, harness=harness,
                      note="возврат после красного verdict")
    _index_comment(conn, cid)
    conn.commit()
    return {"id": cid, "task_id": task_id, "author": author, "kind": kind, "text": text,
            "created_at": ts}


VERDICT_FORMAT = ('first line must be exactly "VERDICT: PASS" or "VERDICT: FAIL"; '
                  'after FAIL list the required fixes on the next lines')


def parse_verdict(text: str | None) -> bool:
    """Return True for "VERDICT: FAIL", False for "VERDICT: PASS"; raise ValueError otherwise.
    Only the first line decides; a FAIL must carry the list of fixes below it."""
    head, _, body = (text or "").strip().partition("\n")
    head = head.strip()
    if head == "VERDICT: PASS":
        return False
    if head == "VERDICT: FAIL":
        if not body.strip():
            raise ValueError(f"verdict FAIL without fixes: {VERDICT_FORMAT}")
        return True
    raise ValueError(f"bad verdict format: {VERDICT_FORMAT}")


def next_stage(conn: sqlite3.Connection, task_id: str, *, holder: str | None = None,
               note: str | None = None, harness: str | None = None,
               to_stage: str | None = None, actor: str | None = None) -> dict:
    row = conn.execute("SELECT stage, project FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError(f"задача не найдена: {task_id}")
    cur = row["stage"]
    if to_stage is not None:
        if to_stage != "done" and to_stage not in PIPELINE_STAGES:
            raise ValueError(f"неизвестный этап: {to_stage}")
        nxt = to_stage
    elif cur in PIPELINE_STAGES:
        idx = PIPELINE_STAGES.index(cur)
        nxt = PIPELINE_STAGES[idx + 1] if idx + 1 < len(PIPELINE_STAGES) else "done"
    else:
        nxt = PIPELINE_STAGES[0]
    fields = {"stage": nxt}
    transition = config_mod.transition_kind(row["project"], cur, nxt, conn=conn)
    # Handoff intentionally releases the previous writer so the next harness
    # must claim the stage.  Sticky transitions keep/optionally refresh holder.
    if transition == "handoff":
        fields["holder"] = ""
    elif holder:
        fields["holder"] = holder
    return update_task(conn, task_id, actor=actor, harness=harness,
                       note=note or f"этап -> {nxt} ({transition})", **fields)


# ------------------------------------------------------------------ чтение

def get_task(conn: sqlite3.Connection, task_id: str, *, with_details: bool = True) -> dict:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        raise KeyError(f"задача не найдена: {task_id}")
    out = row_to_task(conn, row)
    try:
        from . import deps as deps_mod
        out["deps_state"] = deps_mod.ready(conn, task_id)
    except Exception:  # noqa: BLE001 — срез зависимостей не должен ломать карточку
        out["deps_state"] = None
    if with_details:
        try:
            out["documents"] = [dict(r) for r in conn.execute(
                "SELECT id, kind, path, revision, content_hash, title, updated_at, status, error, source, "
                "(SELECT count(*) FROM document_chunks WHERE document_chunks.document_id = documents.id) "
                "AS chunk_count FROM documents WHERE task_id=? ORDER BY kind, path",
                (task_id,))]
        except sqlite3.OperationalError:
            out["documents"] = []
        out["comments"] = [
            dict(r) for r in conn.execute(
                "SELECT id, author, kind, text, created_at FROM comments WHERE task_id = ? "
                "ORDER BY created_at", (task_id,))
        ]
        out["dependencies"] = [
            dict(r) for r in conn.execute(
                "SELECT depends_on, dep_type, created_at FROM deps WHERE issue_id = ?", (task_id,))
        ]
        out["dependents"] = [
            dict(r) for r in conn.execute(
                "SELECT issue_id, dep_type FROM deps WHERE depends_on = ?", (task_id,))
        ]
        out["events"] = [
            dict(r) for r in conn.execute(
                "SELECT ts, kind, from_value, to_value, actor, harness, note, duration_s "
                "FROM events WHERE task_id = ? ORDER BY ts DESC LIMIT 100", (task_id,))
        ]
    return out


def row_to_task(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    cfg = config_mod.load()
    warn = float((cfg.get("board") or {}).get("wip_warn_hours", 8))
    stale_h = float((cfg.get("board") or {}).get("stale_hours", 24))
    try:
        labels = json.loads(row["labels"] or "[]")
    except json.JSONDecodeError:
        labels = []
    try:
        blockers = json.loads(row["blocked_by"] or "[]")
    except json.JSONDecodeError:
        blockers = []
    stage_hours = hours_since(row["stage_at"]) if row["stage_at"] else None
    holder_hours = hours_since(row["holder_at"]) if row["holder_at"] else None
    open_now = row["status"] in OPEN_STATUSES
    # Задача считается идущей, если статус «в работе» или «на проверке».
    # Открытая (никем не взятая) и заблокированная — это очередь, а не движение,
    # поэтому в «брошенные» они не попадают: иначе весь бэклог висит в линии «нужен ты».
    running = row["status"] in ("in_progress", "review")
    # «в работе», но держателя нет — типичный след брошенной задачи (и всех
    # импортированных из beads: там статус ставили руками и не снимали).
    orphan = bool(running and not row["holder"])
    missing_heartbeat = bool(running and row["holder"] and holder_hours is None)
    idle_hours = holder_hours if holder_hours is not None else (
        hours_since(row["started_at"]) if running else None)
    stale = bool(running and not orphan and idle_hours is not None and idle_hours > stale_h)
    abandoned = orphan or missing_heartbeat
    return {
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "description": row["description"],
        "acceptance": row["acceptance"],
        "design": row["design"],
        "notes": row["notes"],
        "result": row["result"],
        "status": row["status"],
        "status_title": STATUS_TITLES.get(row["status"], row["status"]),
        "stage": row["stage"],
        "stage_title": STAGE_TITLES.get(row["stage"] or "", row["stage"]),
        "priority": row["priority"],
        "priority_title": PRIORITY_TITLES.get(row["priority"], str(row["priority"])),
        "issue_type": row["issue_type"],
        "assignee": row["assignee"],
        "assignee_title": actors_mod.display(row["assignee"]),
        "holder": row["holder"],
        "holder_title": actors_mod.display(row["holder"]),
        "holder_note": row["holder_note"],
        "holder_at": row["holder_at"],
        "holder_age": human_age(row["holder_at"]),
        "holder_hours": holder_hours,
        # сколько задача стоит без движения: у брошенной — от последнего heartbeat,
        # у задачи «в работе без держателя» — от начала работы, иначе — от обновления
        "idle_hours": idle_hours,
        "idle_age": human_age(row["holder_at"] or row["started_at"] or row["updated_at"]),
        "stage_at": row["stage_at"],
        "stage_age": human_age(row["stage_at"]),
        "stage_hours": stage_hours,
        "stage_warn": bool(stage_hours is not None and stage_hours > warn),
        "needs_owner": bool(row["needs_owner"]),
        "labels": labels,
        "spec_path": row["spec_path"],
        "checklist_path": row["checklist_path"] if "checklist_path" in row.keys() else None,
        "review_path": row["review_path"] if "review_path" in row.keys() else None,
        "decision_path": row["decision_path"] if "decision_path" in row.keys() else None,
        "journal_path": row["journal_path"],
        "worktree": row["worktree"],
        "branch": row["branch"],
        # Автостарт: пишут только create_task и launcher, PATCH их не меняет
        "autostart": bool(row["autostart"]),
        "launch_route": row["launch_route"],
        "launched_by": row["launched_by"],
        "launch_pid": row["launch_pid"],
        "launched_at": row["launched_at"],
        "launch_log": row["launch_log"],
        "launch_exit_code": row["launch_exit_code"],
        "launch_finished_at": row["launch_finished_at"],
        "launch_error": row["launch_error"],
        "blocked_by": blockers,
        "source": row["source"],
        "external_ref": row["external_ref"],
        "created_at": row["created_at"],
        "created_by": row["created_by"],
        "updated_at": row["updated_at"],
        "updated_age": human_age(row["updated_at"]),
        "started_at": row["started_at"],
        "closed_at": row["closed_at"],
        "close_reason": row["close_reason"],
        "archived": bool(row["archived"]),
        "stale": stale,
        "abandoned": abandoned,
    }


def list_tasks(conn: sqlite3.Connection, *, project: str | None = None, status: str | None = None,
               stage: str | None = None, assignee: str | None = None, holder: str | None = None,
               needs_owner: bool = False, issue_type: str | None = None, label: str | None = None,
               text: str | None = None, include_closed: bool = False, include_archived: bool = False,
               limit: int = 200, offset: int = 0, order: str = "updated") -> dict:
    where, params = [], []
    if project:
        where.append("project = ?")
        params.append(project)
    if status:
        where.append("status = ?")
        params.append(status)
    elif not include_closed:
        where.append("status IN ('open','in_progress','blocked','review')")
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if assignee:
        where.append("assignee = ?")
        params.append(assignee)
    if holder:
        where.append("holder = ?")
        params.append(holder)
    if needs_owner:
        where.append("needs_owner = 1")
    if issue_type:
        where.append("issue_type = ?")
        params.append(issue_type)
    if label:
        where.append("labels LIKE ?")
        params.append(f'%"{label}"%')
    if text:
        where.append("(title LIKE ? OR description LIKE ?)")
        params.extend([f"%{text}%", f"%{text}%"])
    if not include_archived:
        where.append("archived = 0")
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    order_sql = {
        "updated": "updated_at DESC",
        "created": "created_at DESC",
        "priority": "priority ASC, updated_at DESC",
        "stage": "stage_at ASC",
    }.get(order, "updated_at DESC")
    total = conn.execute(f"SELECT count(*) FROM tasks {sql_where}", params).fetchone()[0]
    rows = conn.execute(
        f"SELECT * FROM tasks {sql_where} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {"total": total, "limit": limit, "offset": offset,
            "tasks": [row_to_task(conn, r) for r in rows]}


def task_timeline(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """SELECT e.ts, e.kind, e.from_value, e.to_value, e.actor, e.harness, e.note,
                  e.duration_s, e.task_id, t.title, t.project, t.stage, t.status
           FROM events e LEFT JOIN tasks t ON t.id = e.task_id
           ORDER BY e.ts DESC LIMIT ?""", (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["actor_title"] = actors_mod.display(r["actor"])
        d["age"] = human_age(r["ts"])
        out.append(d)
    return out


def board(conn: sqlite3.Connection, *, group_by: str = "status", project: str | None = None,
          include_closed: bool = False, limit_per_column: int = 300,
          ready_limit: int = 15) -> dict:
    """Данные для канбан-доски: колонки с задачами."""
    where, params = [], []
    if project:
        where.append("project = ?")
        params.append(project)
    if not include_closed:
        where.append("status IN ('open','in_progress','blocked','review')")
    where.append("archived = 0")
    sql_where = "WHERE " + " AND ".join(where)
    rows = conn.execute(
        f"SELECT * FROM tasks {sql_where} ORDER BY priority ASC, updated_at DESC", params
    ).fetchall()
    tasks = [row_to_task(conn, r) for r in rows]

    columns: dict[str, dict] = {}
    if group_by == "stage":
        keys = [(s, STAGE_TITLES.get(s, s)) for s in PIPELINE_STAGES]
        keys.append(("none", "без этапа"))
        keys.append(("done", "завершённые"))
        for key, title in keys:
            columns[key] = {"key": key, "title": title, "tasks": []}
        for t in tasks:
            key = t["stage"] if t["stage"] in PIPELINE_STAGES else (
                "done" if t["status"] in FINAL_STATUSES else "none")
            columns[key]["tasks"].append(t)
    elif group_by == "project":
        for t in tasks:
            key = t["project"] or "без проекта"
            col = columns.setdefault(key, {"key": key, "title": key, "tasks": []})
            col["tasks"].append(t)
    elif group_by == "holder":
        for t in tasks:
            key = t["holder"] or "—"
            title = actors_mod.display(t["holder"]) if t["holder"] else "никто не держит"
            col = columns.setdefault(key, {"key": key, "title": title, "tasks": []})
            col["tasks"].append(t)
    else:
        order = ["in_progress", "review", "open", "blocked"]
        if include_closed:
            order += list(FINAL_STATUSES)
        for key in order:
            columns[key] = {"key": key, "title": STATUS_TITLES.get(key, key), "tasks": []}
        for t in tasks:
            key = t["status"] if t["status"] in columns else "open"
            columns[key]["tasks"].append(t)

    for col in columns.values():
        if col["key"] == "done":
            # «Готово» — не активная работа: тут важна свежесть закрытия, а не
            # приоритет, иначе после среза limit_per_column в колонке остаются
            # старые высокоприоритетные задачи вместо недавно закрытых (доска
            # и рельса «Готово · 7 дн» тогда расходятся в числах).
            col["tasks"].sort(key=lambda t: t["updated_at"] or "", reverse=True)
        else:
            col["tasks"].sort(key=lambda t: (not t["needs_owner"], t["priority"], t["updated_at"] or ""))
        col["count"] = len(col["tasks"])
        col["wip"] = sum(1 for t in col["tasks"] if t["status"] == "in_progress")
        col["needs_owner"] = sum(1 for t in col["tasks"] if t["needs_owner"])
        col["stale"] = sum(1 for t in col["tasks"] if t["stale"])
        col["tasks"] = col["tasks"][:limit_per_column]

    needs_you = [t for t in tasks if t["needs_owner"] or t["stale"] or t["abandoned"]]
    # Самое запущенное — наверх: сначала ждущие человека, потом по времени без движения
    needs_you.sort(key=lambda t: (not t["needs_owner"], -(t.get("idle_hours") or 0)))

    # Что можно взять прямо сейчас: без незакрытых блокеров и без держателя.
    # Считается по графу зависимостей, поэтому ограничено сверху.
    ready_list: list[dict] = []
    blocked_count = 0
    if ready_limit:
        try:
            ready_list = deps_mod.ready_tasks(conn, project=project, limit=ready_limit)
        except Exception:  # noqa: BLE001 — доска не должна падать из-за графа
            ready_list = []
    try:
        statuses = {t["id"]: t["status"] for t in tasks}
        blocked_ids = set()
        for r in conn.execute(
                "SELECT issue_id, depends_on FROM deps WHERE dep_type IN "
                "('blocks','blocked-by','waits-for','conditional-blocks')"):
            if statuses.get(r["depends_on"], "open") not in FINAL_STATUSES:
                blocked_ids.add(r["issue_id"])
        blocked_count = len(blocked_ids & set(statuses))
    except Exception:  # noqa: BLE001
        blocked_count = 0

    return {
        "group_by": group_by,
        "columns": list(columns.values()),
        "total": len(tasks),
        "needs_you": needs_you[:100],
        "ready": ready_list,
        "blocked_count": blocked_count,
        "cycles": deps_mod.cycles(conn) if ready_limit else [],
        "generated_at": now_iso(),
    }


def stats(conn: sqlite3.Connection, project: str | None = None) -> dict:
    where = "WHERE archived = 0"
    params: list = []
    if project:
        where += " AND project = ?"
        params.append(project)
    by_status = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT status, count(*) n FROM tasks {where} GROUP BY status", params)}
    by_stage = {r["stage"] or "none": r["n"] for r in conn.execute(
        f"SELECT stage, count(*) n FROM tasks {where} AND status IN "
        "('open','in_progress','blocked','review') GROUP BY stage", params)}
    by_project_rows = list(conn.execute(
        f"SELECT coalesce(project,'—') project, count(*) n, "
        f"sum(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) wip, "
        f"sum(CASE WHEN needs_owner=1 THEN 1 ELSE 0 END) waiting "
        f"FROM tasks {where} AND status IN ('open','in_progress','blocked','review') "
        "GROUP BY project ORDER BY n DESC", params))
    by_holder = list(conn.execute(
        f"SELECT coalesce(holder,'—') holder, count(*) n FROM tasks {where} AND "
        "status IN ('open','in_progress','blocked','review') GROUP BY holder ORDER BY n DESC",
        params))
    by_actor = list(conn.execute(
        f"SELECT coalesce(assignee,'—') actor, count(*) n FROM tasks {where} AND "
        "status IN ('open','in_progress','blocked','review') GROUP BY assignee ORDER BY n DESC",
        params))
    stale = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status IN ('in_progress','review') "
        "AND ((holder IS NULL AND (started_at IS NULL OR "
        "      julianday('now') - julianday(started_at) > 1)) "
        "  OR (holder IS NOT NULL AND holder_at IS NOT NULL "
        "      AND julianday('now') - julianday(holder_at) > 1))", params).fetchone()[0]
    closed_7d = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status = 'done' AND closed_at IS NOT NULL "
        "AND julianday('now') - julianday(closed_at) <= 7", params).fetchone()[0]
    closed_prev_7d = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status = 'done' AND closed_at IS NOT NULL "
        "AND julianday('now') - julianday(closed_at) > 7 "
        "AND julianday('now') - julianday(closed_at) <= 14", params).fetchone()[0]
    # Закрытия по дням за 14 суток (старые → свежие) — спарклайн «Закрыто за 7 дней».
    closed_days = {r["d"]: r["n"] for r in conn.execute(
        f"SELECT date(closed_at) d, count(*) n FROM tasks {where} AND status = 'done' "
        "AND closed_at IS NOT NULL AND date(closed_at) > date('now', '-14 days') "
        "GROUP BY d", params)}
    today = datetime.now(timezone.utc).date()
    closed_by_day = [
        {"date": (d := (today - timedelta(days=offset)).isoformat()), "count": closed_days.get(d, 0)}
        for offset in range(13, -1, -1)]
    long_stage = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND status IN ('in_progress','review') "
        "AND stage_at IS NOT NULL AND julianday('now') - julianday(stage_at) > 0.33",
        params).fetchone()[0]
    needs_owner = conn.execute(
        f"SELECT count(*) FROM tasks {where} AND needs_owner = 1", params).fetchone()[0]
    running = list(conn.execute(
        f"SELECT * FROM tasks {where} AND status = 'in_progress' "
        "ORDER BY stage_at ASC", params))
    return {
        "by_status": by_status,
        "by_stage": by_stage,
        "by_project": [
            {"project": r["project"], "total": r["n"], "in_progress": r["wip"],
             "waiting": r["waiting"]} for r in by_project_rows],
        "by_holder": [{"holder": r["holder"], "title": actors_mod.display(
            None if r["holder"] == "—" else r["holder"]), "count": r["n"]} for r in by_holder],
        "by_actor": [{"actor": r["actor"], "title": actors_mod.display(
            None if r["actor"] == "—" else r["actor"]), "count": r["n"]} for r in by_actor],
        "stale": stale,
        "needs_owner": needs_owner,
        "closed_7d": closed_7d,
        "closed_prev_7d": closed_prev_7d,
        "closed_delta": closed_7d - closed_prev_7d,
        "closed_by_day": closed_by_day,
        "long_stage": long_stage,
        "running": [row_to_task(conn, r) for r in running],
        "generated_at": now_iso(),
    }


# ------------------------------------------------------------------ проекты, акторы, связи

def upsert_project(conn: sqlite3.Connection, slug: str, **fields) -> dict:
    allowed = {"title", "kind", "path", "git_remote", "git_branch", "color", "archived",
               "imported_at", "import_note", "routing"}
    conn.execute(
        "INSERT INTO projects(slug, title, kind) VALUES(?,?,?) "
        "ON CONFLICT(slug) DO UPDATE SET title=coalesce(excluded.title, projects.title)",
        (slug, fields.get("title") or slug, fields.get("kind", "native")),
    )
    sets, params = [], []
    for k, v in fields.items():
        if k in allowed and v is not None:
            sets.append(f"{k} = ?")
            params.append(v)
    if sets:
        params.append(slug)
        conn.execute(f"UPDATE projects SET {', '.join(sets)} WHERE slug = ?", params)
    conn.commit()
    return dict(conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone())


def _project_with_routing(conn: sqlite3.Connection, row: dict) -> dict:
    """Дописать к строке проекта действующую маршрутизацию: `routing` (переопределение
    из базы, распарсенное, или None), `routing_effective` (слитый словарь: то, чем
    реально пользуются `allowed_harnesses`/`transition_kind`) и `routing_source` —
    откуда взято переопределение (`default`/`config`/`db`/`config+db`)."""
    slug = row.get("slug")
    raw = row.get("routing")
    parsed = None
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            parsed = None
    out = dict(row)
    out["routing"] = parsed if isinstance(parsed, dict) else None
    out["routing_effective"] = config_mod.routing(slug, conn=conn)
    has_db = bool(out["routing"])
    has_config = False
    try:
        cfg = config_mod.load()
        projects_cfg = (cfg.get("routing") or {}).get("projects") or {}
        has_config = bool(slug and isinstance(projects_cfg, dict) and projects_cfg.get(slug))
    except Exception:  # noqa: BLE001 — конфиг не должен ронять показ проекта
        has_config = False
    if has_config and has_db:
        out["routing_source"] = "config+db"
    elif has_db:
        out["routing_source"] = "db"
    elif has_config:
        out["routing_source"] = "config"
    else:
        out["routing_source"] = "default"
    return out


def list_projects(conn: sqlite3.Connection, include_archived: bool = False) -> list[dict]:
    where = "" if include_archived else "WHERE archived = 0"
    rows = conn.execute(
        f"""SELECT p.*,
              (SELECT count(*) FROM tasks t WHERE t.project = p.slug AND t.archived = 0) n_tasks,
              (SELECT count(*) FROM tasks t WHERE t.project = p.slug AND t.archived = 0
                 AND t.status IN ('open','in_progress','blocked','review')) n_open,
              (SELECT count(*) FROM tasks t WHERE t.project = p.slug AND t.archived = 0
                 AND t.status = 'in_progress') n_wip
            FROM projects p {where} ORDER BY n_open DESC, p.slug""").fetchall()
    return [_project_with_routing(conn, dict(r)) for r in rows]


def list_all_projects(conn: sqlite3.Connection) -> list[dict]:
    """Все проекты (включая скрытые) со счётчиками и признаком «каталог ещё жив».

    Для настроек доски: там нужно видеть и скрытые проекты, чтобы вернуть их обратно,
    и понимать, какие каталоги исчезли с диска.
    """
    rows = list_projects(conn, include_archived=True)
    for row in rows:
        row["path_exists"] = bool(row.get("path")) and Path(row["path"]).is_dir()
    return rows


def norm_slug(value: str) -> str:
    """Slug проекта из произвольной строки.

    Буквы (в том числе кириллица) и «/» сохраняются: проекты лежат категориями
    (`Zoloto585/zoloto585-search`), и такие slug'и уже есть в базе с импорта beads.
    """
    slug = re.sub(r"[^0-9a-zA-Zа-яА-Я_\-/]+", "-", str(value).strip()).strip("-/")
    return re.sub(r"/{2,}", "/", slug)


def _git_value(path: Path, *args: str) -> str | None:
    """Значение из git, если каталог — репозиторий. Ошибки не пробрасываются."""
    try:
        out = subprocess.run(["git", "-C", str(path), *args], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (out.stdout or "").strip()
    return value or None


def repo_info(path: str | Path) -> dict:
    """Что можно узнать о каталоге-репозитории: абсолютный путь, git remote и ветка.

    Каталог может быть и не git-репозиторием — это не ошибка: путь проекту нужен
    в основном для `init-projects` (прописать правила Listik в AGENTS.md).
    """
    resolved = Path(path).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        pass
    info = {"path": str(resolved), "exists": resolved.is_dir(), "git": False,
            "git_remote": None, "git_branch": None}
    if not info["exists"]:
        return info
    if (resolved / ".git").exists() or _git_value(resolved, "rev-parse", "--git-dir"):
        info["git"] = True
        info["git_remote"] = _git_value(resolved, "remote", "get-url", "origin")
        info["git_branch"] = _git_value(resolved, "rev-parse", "--abbrev-ref", "HEAD")
    return info


def project_row(conn: sqlite3.Connection, slug: str) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE slug = ?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"проект не найден: {slug}")
    return dict(row)


def add_project(conn: sqlite3.Connection, *, path: str | None = None, slug: str | None = None,
                title: str | None = None, kind: str = "native") -> dict:
    """Добавить репозиторий (каталог) на доску.

    Slug по умолчанию — имя каталога; можно передать свой, чтобы лечь в категорию
    (`Zoloto585/my-repo`). Если проект с таким slug уже есть — он возвращается
    на доску и обновляется, а не падает с ошибкой.
    """
    info = (repo_info(path) if path
            else {"path": None, "exists": False, "git": False,
                  "git_remote": None, "git_branch": None})
    if path and not info["exists"]:
        raise ValueError(f"каталога нет: {info['path']}")
    base = slug or (Path(info["path"]).name if info["path"] else "")
    clean = norm_slug(base)
    if not clean:
        raise ValueError("нужен slug проекта или путь к каталогу")
    existed = conn.execute("SELECT 1 FROM projects WHERE slug = ?", (clean,)).fetchone() is not None
    project = upsert_project(conn, clean, title=title or Path(clean).name, kind=kind,
                             path=info["path"], git_remote=info["git_remote"],
                             git_branch=info["git_branch"], archived=0)
    project["created"] = not existed
    project["git"] = info["git"]
    project["path_exists"] = info["exists"]
    return project


def update_project(conn: sqlite3.Connection, slug: str, **fields) -> dict:
    """Правка проекта: название, путь, цвет, скрытие с доски (`archived`), маршрутизация.

    `routing`: словарь — валидируется (`config.validate_routing`) и пишется как JSON;
    пустой словарь сбрасывает переопределение (колонка становится NULL); строка
    разбирается как JSON и дальше обрабатывается как словарь (невалидный JSON —
    ``ValueError``).
    """
    project_row(conn, slug)
    routing_value = fields.pop("routing", None)
    changes: dict[str, object] = {
        k: (int(v) if k == "archived" else v) for k, v in fields.items()
        if v is not None and k in ("title", "path", "git_remote", "git_branch",
                                    "color", "archived", "kind")
    }
    if routing_value is not None:
        parsed = routing_value
        if isinstance(parsed, str):
            try:
                parsed = json.loads(parsed)
            except json.JSONDecodeError as exc:
                raise ValueError(f"routing: невалидный JSON: {exc}") from exc
        validated = config_mod.validate_routing(parsed)
        changes["routing"] = json.dumps(validated, ensure_ascii=False) if validated else None
    if changes:
        sets = ", ".join(f"{k} = ?" for k in changes)
        conn.execute(f"UPDATE projects SET {sets} WHERE slug = ?", (*changes.values(), slug))
        conn.commit()
    out = _project_with_routing(conn, project_row(conn, slug))
    out["path_exists"] = bool(out.get("path")) and Path(out["path"]).is_dir()
    return out


def remove_project(conn: sqlite3.Connection, slug: str, *, force: bool = False) -> dict:
    """Убрать проект из Listik.

    Проект с задачами по умолчанию не удаляется: задачи — это история, по которой
    потом ищут. Такой проект скрывают с доски (`archived`), а настоящее удаление —
    только с `force`, и оно уносит задачи вместе с проектом.
    """
    project_row(conn, slug)
    n_tasks = conn.execute("SELECT count(*) FROM tasks WHERE project = ?", (slug,)).fetchone()[0]
    if n_tasks and not force:
        raise ValueError(f"у проекта {slug} {n_tasks} задач — сначала скройте его с доски, "
                         f"либо удалите вместе с задачами (force)")
    removed_tasks = 0
    if n_tasks:
        ids = [r[0] for r in conn.execute("SELECT id FROM tasks WHERE project = ?", (slug,))]
        for task_id in ids:
            conn.execute("DELETE FROM task_fts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM comment_fts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM document_chunk_fts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM embeddings WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM documents WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM comments WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM deps WHERE issue_id = ? OR depends_on = ?",
                         (task_id, task_id))
            conn.execute("DELETE FROM events WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            removed_tasks += 1
    conn.execute("DELETE FROM projects WHERE slug = ?", (slug,))
    conn.commit()
    return {"slug": slug, "removed": True, "removed_tasks": removed_tasks}


def list_actors(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT a.*, (SELECT count(*) FROM tasks t WHERE t.assignee = a.key
                        AND t.status IN ('open','in_progress','blocked','review')) n_tasks,
                         (SELECT count(*) FROM tasks t WHERE t.holder = a.key
                        AND t.status IN ('open','in_progress','blocked','review')) n_held
           FROM actors a ORDER BY n_tasks DESC""").fetchall()
    return [dict(r) for r in rows]


def facet_values(conn: sqlite3.Connection) -> dict:
    out: dict[str, list] = {}
    for name, sql in {
        "projects": "SELECT DISTINCT coalesce(project,'—') v FROM tasks WHERE archived=0 ORDER BY v",
        "assignees": "SELECT DISTINCT coalesce(assignee,'—') v FROM tasks WHERE archived=0 ORDER BY v",
        "holders": "SELECT DISTINCT coalesce(holder,'—') v FROM tasks WHERE archived=0 ORDER BY v",
        "statuses": "SELECT DISTINCT status v FROM tasks ORDER BY v",
        "stages": "SELECT DISTINCT coalesce(stage,'—') v FROM tasks ORDER BY v",
        "types": "SELECT DISTINCT issue_type v FROM tasks ORDER BY v",
    }.items():
        out[name] = [r["v"] for r in conn.execute(sql)]
    out["actors"] = {"key": "actors", "values": [dict(r) for r in conn.execute(
        "SELECT key, title, kind FROM actors ORDER BY key")]}
    return out


def add_dep(conn: sqlite3.Connection, issue_id: str, depends_on: str, dep_type: str = "blocks",
            created_by: str | None = None, confirm: bool = False) -> dict:
    for tid in (issue_id, depends_on):
        if not conn.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,)).fetchone():
            raise KeyError(f"задача не найдена: {tid}")
    if issue_id == depends_on:
        raise ValueError(f"связь задачи с самой собой: {issue_id}")
    actor_key, actor_kind = actors_mod.resolve(created_by, conn)
    if created_by:
        actors_mod.remember(conn, created_by, actor_key, actor_kind)
    # Правило префикса надёжнее actors.resolve: незнакомое имя вида agent:mcp
    # тот вернёт как kind human, а вызов всё равно от агента.
    is_agent = actor_kind == "agent" or (created_by or "").strip().lower().startswith("agent:")

    requested_dep_type = dep_type
    suggested = False
    confirmed = False
    promoted = False
    created = False

    if dep_type in deps_mod.HARD_BLOCKERS:
        if is_agent and not confirm:
            # Агент может записать гипотезу, но не может сам сделать её жёсткой:
            # это меняло бы готовность задач без участия человека.
            requested_dep_type = dep_type
            actual_type = "suggested-blocks"
            existing_hard = conn.execute(
                "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (%s)"
                % ",".join("?" * len(deps_mod.HARD_BLOCKERS)),
                (issue_id, depends_on, *deps_mod.HARD_BLOCKERS),
            ).fetchone()
            if existing_hard:
                # Жёсткая связь важнее предложения — уже подтверждено, ничего не пишем.
                actual_type = existing_hard["dep_type"]
                confirmed = True
            else:
                suggested = True
                exists = conn.execute(
                    "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                    (issue_id, depends_on),
                ).fetchone()
                created = not exists
                conn.execute(
                    "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?) "
                    "ON CONFLICT(issue_id, depends_on, dep_type) DO NOTHING",
                    (issue_id, depends_on, "suggested-blocks", actor_key or created_by),
                )
            deps_mod.refresh_task(conn, issue_id)
            deps_mod.refresh_task(conn, depends_on)
            conn.commit()
            return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": actual_type,
                    "requested_dep_type": requested_dep_type, "suggested": suggested,
                    "confirmed": confirmed, "promoted": False, "created": created,
                    "created_by": actor_key or created_by}

        # Жёсткая связь: человек, unknown, агент с --confirm.
        already_hard = conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (%s)"
            % ",".join("?" * len(deps_mod.HARD_BLOCKERS)),
            (issue_id, depends_on, *deps_mod.HARD_BLOCKERS),
        ).fetchone()
        had_suggestion = conn.execute(
            "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
            (issue_id, depends_on),
        ).fetchone()
        if not already_hard:
            # Проверка цикла: есть ли путь от depends_on обратно к issue_id по жёстким рёбрам.
            parents: dict[str, str | None] = {depends_on: None}
            frontier = [depends_on]
            cycle_path: list[str] | None = None
            marks = ",".join("?" * len(deps_mod.HARD_BLOCKERS))
            while frontier and cycle_path is None:
                cur = frontier.pop()
                for r in conn.execute(
                    f"SELECT depends_on FROM deps WHERE issue_id = ? AND dep_type IN ({marks})",
                    (cur, *deps_mod.HARD_BLOCKERS),
                ):
                    nxt = r["depends_on"]
                    if nxt == issue_id:
                        # cur замыкается на issue_id — восстанавливаем путь от
                        # depends_on до cur, добавляем issue_id с обеих сторон.
                        chain = [cur]
                        node = cur
                        while parents.get(node) is not None:
                            node = parents[node]
                            chain.append(node)
                        chain.reverse()
                        cycle_path = [issue_id] + chain + [issue_id]
                        break
                    if nxt not in parents:
                        parents[nxt] = cur
                        frontier.append(nxt)
            if cycle_path is not None:
                raise ValueError(
                    "жёсткая зависимость создаёт цикл: " + " → ".join(cycle_path))
            conn.execute(
                "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?) "
                "ON CONFLICT(issue_id, depends_on, dep_type) DO NOTHING",
                (issue_id, depends_on, dep_type, actor_key or created_by),
            )
            created = True
            promoted = bool(had_suggestion)
            if had_suggestion:
                conn.execute(
                    "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                    (issue_id, depends_on),
                )
            actual_type = dep_type
        else:
            actual_type = already_hard["dep_type"]
            created = False
            if had_suggestion:
                conn.execute(
                    "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type='suggested-blocks'",
                    (issue_id, depends_on),
                )
        confirmed = True
        deps_mod.refresh_task(conn, issue_id)
        deps_mod.refresh_task(conn, depends_on)
        conn.commit()
        return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": actual_type,
                "requested_dep_type": requested_dep_type, "suggested": False,
                "confirmed": confirmed, "promoted": promoted, "created": created,
                "created_by": actor_key or created_by}

    # Мягкие типы: пишутся как раньше, без проверок предложений/циклов.
    exists = conn.execute(
        "SELECT 1 FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
        (issue_id, depends_on, dep_type),
    ).fetchone()
    conn.execute(
        "INSERT INTO deps(issue_id, depends_on, dep_type, created_by) VALUES(?,?,?,?) "
        "ON CONFLICT(issue_id, depends_on, dep_type) DO NOTHING",
        (issue_id, depends_on, dep_type, actor_key or created_by),
    )
    deps_mod.refresh_task(conn, issue_id)
    deps_mod.refresh_task(conn, depends_on)
    conn.commit()
    return {"issue_id": issue_id, "depends_on": depends_on, "dep_type": dep_type,
            "requested_dep_type": dep_type, "suggested": False, "confirmed": False,
            "promoted": False, "created": not exists, "created_by": actor_key or created_by}


def remove_dep(conn: sqlite3.Connection, issue_id: str, depends_on: str,
               dep_type: str | None = None) -> dict:
    if dep_type is None:
        types = ("blocks", "suggested-blocks")
        rows = conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (?,?)",
            (issue_id, depends_on, *types),
        ).fetchall()
        removed_types = [r["dep_type"] for r in rows]
        conn.execute(
            "DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type IN (?,?)",
            (issue_id, depends_on, *types),
        )
    else:
        rows = conn.execute(
            "SELECT dep_type FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
            (issue_id, depends_on, dep_type),
        ).fetchall()
        removed_types = [r["dep_type"] for r in rows]
        conn.execute("DELETE FROM deps WHERE issue_id=? AND depends_on=? AND dep_type=?",
                     (issue_id, depends_on, dep_type))
    deps_mod.refresh_task(conn, issue_id)
    deps_mod.refresh_task(conn, depends_on)
    conn.commit()
    return {"removed": len(removed_types), "dep_types": removed_types}


def recompute_blocked(conn: sqlite3.Connection) -> int:
    """Обновляет денормализованный blocked_by. Статусы задач не переписывает:
    «заблокирована кем-то» — вычисляемое состояние (см. deps.ready)."""
    changed = deps_mod.refresh_blocked_column(conn)
    conn.commit()
    return changed


# ------------------------------------------------------------------ память

def remember(conn: sqlite3.Connection, text: str, *, key: str | None = None,
             project: str | None = None) -> dict:
    """Записать заметку в долговременную память (аналог `bd remember`).

    Ключ по умолчанию — `note/<unix-время>`, проект — `personal`. Повторная запись
    с тем же ключом перезаписывает тело заметки и её строку в полнотекстовом индексе.
    """
    if not text or not text.strip():
        raise ValueError("пустой текст заметки")
    key = key or f"note/{int(time.time())}"
    project = project or "personal"
    conn.execute(
        "INSERT INTO memories(key, project, body, source) VALUES(?,?,?, 'native') "
        "ON CONFLICT(key) DO UPDATE SET body=excluded.body, updated_at=?",
        (key, project, text, now_iso()))
    conn.execute("DELETE FROM memory_fts WHERE memory_key = ?", (key,))
    conn.execute("INSERT INTO memory_fts(memory_key, body) VALUES(?,?)", (key, text))
    conn.commit()
    return {"key": key, "project": project}
