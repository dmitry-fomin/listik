"""Markdown documents, structural chunks and compact stage context."""
from __future__ import annotations

import hashlib
import re
import sqlite3

from . import deps as deps_mod
from . import errors as errors_mod
from . import paths, search, store, textutil, util

MAX_CHARS = 6000
OVERLAP = 240
# Вид документа -> поле задачи, в котором лежит путь к нему.
DOC_FIELDS = {"spec": "spec_path", "checklist": "checklist_path", "review": "review_path",
              "decision": "decision_path"}
# Потолок на текст, присланный через API/MCP: документ едет в sqlite целиком.
MAX_UPLOAD_CHARS = 1_000_000
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*```")
_LIST_ITEM = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\S")

# ------------------------------------------------------------------ context: per-stage contract

# По этапам конвейера: этап -> дефолтный лимит символов на суммарный текст выбранных чанков.
STAGE_MAX_CHARS = {"s1-spec": 150000, "s2-review": 150000, "s3-impl": 24000, "s4-judge": 24000}
# Порядок документов в chunks[]/documents[] на s1/s2 и как основа сортировки слоёв s3/s4.
_DOC_KIND_ORDER = {"spec": 0, "checklist": 1, "review": 2, "decision": 3}
_CARD_KEYS = ("id", "project", "title", "status", "stage", "priority", "holder", "worktree",
              "branch", "spec_path", "checklist_path", "review_path", "decision_path",
              "issue_type", "labels")
_DEPENDENCIES_DEFAULTS = {
    "task_id": None, "title": None, "status": None, "stage": None, "ready": None,
    "claimable": None, "can_finish": None, "blocked_by": [], "waiting_for": [],
    "children_open": [], "parent": None, "soft_links": [], "holder": None,
    "holder_title": None, "verdict": None, "worktree_busy": None,
}


def _resolve(conn: sqlite3.Connection, task: sqlite3.Row, raw: str):
    """Существующий файл документа (pathlib.Path) либо исходный путь."""
    p = util.expanduser(raw)
    candidates = [p]
    if not p.is_absolute():
        project = conn.execute("SELECT path FROM projects WHERE slug = ?", (task["project"],)).fetchone()
        if project and project[0]:
            candidates.insert(0, util.path(project[0]) / p)
        candidates.append(paths.ROOT_DIR / p)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return util.resolved(candidate)
        except OSError:
            continue
    return p


def _hard_split(text: str, line_start: int, line_end: int, chunk_limit: int) -> list[tuple[str, int, int]]:
    """Char-level split of a single over-long paragraph/item, with overlap between
    consecutive pieces of the same unit (policy §2, bullet "б")."""
    pieces: list[tuple[str, int, int]] = []
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + chunk_limit)
        piece = text[pos:end].strip()
        rel_start = text[:pos].count("\n")
        rel_end = text[:end].count("\n")
        pieces.append((piece, line_start + rel_start, min(line_end, line_start + rel_end)))
        if end >= len(text):
            break
        pos = max(pos + 1, end - OVERLAP)
    return pieces


def _group_list_items(items: list[tuple[str, int]], chunk_limit: int) -> list[tuple[str, int, int]]:
    """Group consecutive list-item lines into chunks not exceeding chunk_limit, without
    splitting an item, and without overlap between groups (policy §2)."""
    groups: list[tuple[str, int, int]] = []
    current_lines: list[str] = []
    current_start = current_end = None
    for line_text, lineno in items:
        if len(line_text) > chunk_limit:
            if current_lines:
                groups.append(("\n".join(current_lines), current_start, current_end))
                current_lines = []
                current_start = current_end = None
            # Longer than chunk_limit — a single-item group; the caller hard-splits it.
            groups.append((line_text, lineno, lineno))
            continue
        candidate_lines = current_lines + [line_text]
        candidate_text = "\n".join(candidate_lines)
        if current_lines and len(candidate_text) > chunk_limit:
            groups.append(("\n".join(current_lines), current_start, current_end))
            current_lines = [line_text]
            current_start = lineno
            current_end = lineno
        else:
            current_lines = candidate_lines
            current_start = lineno if current_start is None else current_start
            current_end = lineno
    if current_lines:
        groups.append(("\n".join(current_lines), current_start, current_end))
    return groups


def split_markdown(content: str, limit: int = MAX_CHARS) -> list[dict]:
    """Split Markdown into deterministic heading sections.

    A chunk always carries its breadcrumb in the text as well as in metadata.  Keeping
    source line offsets while splitting (instead of searching the resulting text) avoids
    incorrect offsets when overlap repeats a paragraph or a phrase occurs twice.
    """
    lines = content.splitlines()
    if not lines:
        return []
    sections: list[tuple[int, int, str, str, list[str]]] = []
    stack: list[str] = []
    section_start = 1
    section_heading = ""
    section_breadcrumb = ""

    def flush(end_line: int) -> None:
        nonlocal section_start
        if end_line >= section_start:
            sections.append((section_start, end_line, section_heading,
                             section_breadcrumb, lines[section_start - 1:end_line]))

    in_fence = False
    for line_no, line in enumerate(lines, 1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if not match:
            continue
        flush(line_no - 1)
        level = len(match.group(1))
        title = match.group(2).strip()
        stack = stack[: level - 1] + [title]
        section_start = line_no
        section_heading = title
        section_breadcrumb = " / ".join(stack)
    flush(len(lines))

    out: list[dict] = []
    for source_start, source_end, heading, breadcrumb, raw_lines in sections:
        prefix = (breadcrumb + "\n\n") if breadcrumb else ""
        chunk_limit = max(1, limit - len(prefix))
        # Do not index the Markdown heading twice; the canonical breadcrumb prefix below
        # is what makes every chunk self-describing in FTS and embeddings.
        has_heading_line = bool(raw_lines) and _HEADING.match(raw_lines[0])
        body_lines = raw_lines[1:] if has_heading_line else raw_lines
        body_start_line = source_start + (1 if has_heading_line else 0)
        raw = "\n".join(body_lines).strip()
        if not raw:
            raw = breadcrumb or heading

        # Build pieces together with their source line ranges.  Paragraphs are preferred;
        # a single very long paragraph is hard-split so MAX_CHARS remains a real bound;
        # a single very long list (every line is a list item) is split by whole items.
        units: list[tuple] = []
        para_start = 0
        while para_start < len(body_lines):
            while para_start < len(body_lines) and not body_lines[para_start].strip():
                para_start += 1
            if para_start >= len(body_lines):
                break
            para_end = para_start
            while para_end + 1 < len(body_lines) and body_lines[para_end + 1].strip():
                para_end += 1
            para_lines = body_lines[para_start:para_end + 1]
            abs_start = body_start_line + para_start
            abs_end = body_start_line + para_end
            text = "\n".join(para_lines).strip()
            if text:
                is_list = bool(para_lines) and all(_LIST_ITEM.match(l) for l in para_lines)
                if len(text) > chunk_limit and is_list:
                    items = [(body_lines[para_start + i], body_start_line + para_start + i)
                             for i in range(len(para_lines))]
                    units.append(("list", items, abs_start, abs_end))
                else:
                    units.append(("para", text, abs_start, abs_end))
            para_start = para_end + 1
        if not units and raw:
            units = [("para", raw, source_start, source_end)]

        pieces: list[tuple[str, int, int]] = []
        current = ""
        current_start = current_end = 0
        for unit in units:
            if unit[0] == "list":
                _, items, line_start, line_end = unit
                # A list group never carries an overlap tail forward or backward.
                if current:
                    pieces.append((current.strip(), current_start, current_end))
                    current = ""
                for grp_text, grp_start, grp_end in _group_list_items(items, chunk_limit):
                    if len(grp_text) > chunk_limit:
                        pieces.extend(_hard_split(grp_text, grp_start, grp_end, chunk_limit))
                    else:
                        pieces.append((grp_text, grp_start, grp_end))
                continue
            _, text, line_start, line_end = unit
            if len(text) > chunk_limit:
                if current:
                    pieces.append((current.strip(), current_start, current_end))
                    current = ""
                pieces.extend(_hard_split(text, line_start, line_end, chunk_limit))
                continue
            candidate = (current + "\n\n" + text).strip() if current else text
            if current and len(candidate) > chunk_limit:
                pieces.append((current.strip(), current_start, current_end))
                tail = current[-OVERLAP:]
                # Budget accounts for the carried-over tail: shrink it so that
                # tail + "\n\n" + text still fits chunk_limit (down to no tail at all).
                max_tail = max(0, chunk_limit - len(text) - 2)
                if len(tail) > max_tail:
                    tail = tail[-max_tail:] if max_tail > 0 else ""
                current = (tail + "\n\n" + text) if tail else text
                current_start = line_start
                current_end = line_end
            else:
                current = candidate
                current_start = current_start or line_start
                current_end = line_end
        if current.strip():
            pieces.append((current.strip(), current_start or source_start, current_end or source_end))

        for piece, start_line, end_line in pieces:
            text = prefix + piece
            out.append({"heading": heading, "breadcrumb": breadcrumb, "text": text,
                        "start_line": start_line, "end_line": end_line,
                        "token_count": len(re.findall(r"\S+", text))})
    return out


# Backwards-compatible alias: existing callers used the private name.
_split_markdown = split_markdown


def _document_title(content: str, path: str) -> str:
    return content.splitlines()[0].lstrip("# ") if content else path


def _insert_chunks(conn: sqlite3.Connection, task_id: str, doc_id: str, content: str) -> None:
    """Нарезать `content` в `document_chunks` и их строки FTS для готовой строки документа."""
    for ordinal, chunk in enumerate(split_markdown(content), 1):
        chunk_id = f"{doc_id}:{ordinal}"
        chash = textutil.text_hash(chunk["heading"] or "", chunk["breadcrumb"] or "", chunk["text"])
        conn.execute("INSERT INTO document_chunks(id,document_id,ordinal,heading,breadcrumb,text,start_line,end_line,token_count,content_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (chunk_id, doc_id, ordinal, chunk["heading"], chunk["breadcrumb"], chunk["text"],
                      chunk["start_line"], chunk["end_line"], chunk["token_count"], chash))
        conn.execute("INSERT INTO document_chunk_fts(chunk_id,document_id,task_id,heading,breadcrumb,body) VALUES(?,?,?,?,?,?)",
                     (chunk_id, doc_id, task_id, chunk["heading"], chunk["breadcrumb"], chunk["text"]))


def _drop_chunks(conn: sqlite3.Connection, doc_id: str) -> None:
    """Снести чанки документа вместе с их строками FTS и векторами (как при переиндексации)."""
    old_chunk_ids = [r[0] for r in conn.execute(
        "SELECT id FROM document_chunks WHERE document_id=?", (doc_id,)).fetchall()]
    conn.execute("DELETE FROM document_chunks WHERE document_id=?", (doc_id,))
    conn.execute("DELETE FROM document_chunk_fts WHERE document_id=?", (doc_id,))
    if old_chunk_ids:
        marks = ",".join("?" * len(old_chunk_ids))
        conn.execute(
            f"DELETE FROM embeddings WHERE doc_kind='chunk' AND doc_id IN ({marks})",
            old_chunk_ids)
    search.invalidate_vectors()


def index_document(conn: sqlite3.Connection, task_id: str, path: str, *, kind: str = "spec") -> dict:
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    row = conn.execute("SELECT * FROM documents WHERE task_id=? AND kind=? AND path=?",
                       (task_id, kind, path)).fetchone()
    now = store.now_iso()
    prev_status = row["status"] if row is not None and "status" in row.keys() else "ok"
    if row is not None and "source" in row.keys() and row["source"] == "upload":
        # Текст загруженного документа лежит в базе: на диск не ходим вообще.
        content = row["content"] or ""
    else:
        resolved = _resolve(conn, task, path)
        try:
            content = util.read_text(resolved)
        except (OSError, UnicodeError) as exc:
            error_text = str(exc)
            if row is None:
                doc_id = hashlib.sha256(f"{task_id}:{kind}:{path}".encode()).hexdigest()[:24]
                conn.execute(
                    "INSERT INTO documents(id,task_id,kind,path,revision,content_hash,title,"
                    "created_at,updated_at,status,error,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (doc_id, task_id, kind, path, 0, "", path, now, now, "missing", error_text, now))
                store.event(conn, task_id, "document_error", note=f"{kind} {path}: {error_text}")
            else:
                doc_id = row["id"]
                if prev_status == "missing":
                    conn.execute("UPDATE documents SET error=?, checked_at=? WHERE id=?",
                                 (error_text, now, doc_id))
                else:
                    conn.execute(
                        "UPDATE documents SET status='missing', error=?, checked_at=?, updated_at=? WHERE id=?",
                        (error_text, now, now, doc_id))
                    store.event(conn, task_id, "document_error", note=f"{kind} {path}: {error_text}")
            conn.commit()
            return document_json(conn, doc_id)

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
    was_missing = row is not None and prev_status == "missing"
    if row and row["content_hash"] == digest and not was_missing:
        # Unchanged file, already ok: only the last-checked timestamp moves.
        conn.execute("UPDATE documents SET checked_at=? WHERE id=?", (now, row["id"]))
        conn.commit()
        return document_json(conn, row["id"])

    title = _document_title(content, path)
    if row and row["content_hash"] == digest:
        # Was missing, file reappeared unchanged: no revision bump, chunks kept as-is.
        doc_id = row["id"]
        conn.execute(
            "UPDATE documents SET status='ok', error=NULL, checked_at=?, updated_at=? WHERE id=?",
            (now, now, doc_id))
    elif row:
        revision = int(row["revision"] or 0) + 1
        doc_id = row["id"]
        conn.execute(
            "UPDATE documents SET revision=?, content_hash=?, title=?, status='ok', error=NULL, "
            "checked_at=?, updated_at=? WHERE id=?",
            (revision, digest, title, now, now, doc_id))
        _drop_chunks(conn, doc_id)
        _insert_chunks(conn, task_id, doc_id, content)
    else:
        doc_id = hashlib.sha256(f"{task_id}:{kind}:{path}".encode()).hexdigest()[:24]
        conn.execute(
            "INSERT INTO documents(id,task_id,kind,path,revision,content_hash,title,created_at,"
            "updated_at,status,error,checked_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, task_id, kind, path, 1, digest, title, now, now, "ok", None, now))
        _insert_chunks(conn, task_id, doc_id, content)

    if was_missing:
        store.event(conn, task_id, "document_restored", note=f"{kind} {path}")
    conn.commit()
    return document_json(conn, doc_id)


def _check_kind(kind: str) -> None:
    if kind not in DOC_FIELDS:
        raise ValueError(f"неизвестный вид документа: {kind}; "
                         f"допустимо: {', '.join(DOC_FIELDS)}")


def put_document(conn: sqlite3.Connection, task_id: str, kind: str, content: str, *,
                 path: str | None = None, actor: str | None = None) -> dict:
    """Принять текст документа через API/MCP и держать его в базе.

    На удалённом сервере репозиториев проектов нет, поэтому файл по `spec_path` там не
    прочитается. Документ, однажды загруженный для `(task, kind, path)`, дальше читается
    из `documents.content` и на диск не ходит; повтор с тем же текстом ревизию не меняет.
    """
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    _check_kind(kind)
    if not isinstance(content, str):
        raise ValueError("content должен быть строкой")
    if len(content) > MAX_UPLOAD_CHARS:
        raise ValueError(f"документ длиннее {MAX_UPLOAD_CHARS} символов")
    if path is not None and not isinstance(path, str):
        raise ValueError("path должен быть строкой")

    field = DOC_FIELDS[kind]
    current = task[field] if field in task.keys() else None
    journal = task["journal_path"] if "journal_path" in task.keys() else None
    # explicit: путь выбран явным параметром (4.1) или синтетическим fallback (4.4) —
    # только тогда он дописывается в карточку задачи.
    if path is not None and path.strip():
        eff_path, explicit = path.strip(), True
    elif current and str(current).strip():
        eff_path, explicit = current, False
    elif kind == "decision" and journal and str(journal).strip():
        eff_path, explicit = journal, False
    else:
        eff_path, explicit = f"listik://{task_id}/{kind}.md", True

    now = store.now_iso()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
    row = conn.execute("SELECT * FROM documents WHERE task_id=? AND kind=? AND path=?",
                       (task_id, kind, eff_path)).fetchone()
    if row is None:
        doc_id = hashlib.sha256(f"{task_id}:{kind}:{eff_path}".encode()).hexdigest()[:24]
        revision = 1
        conn.execute(
            "INSERT INTO documents(id,task_id,kind,path,revision,content_hash,title,created_at,"
            "updated_at,status,error,checked_at,source,content) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, task_id, kind, eff_path, revision, digest, _document_title(content, eff_path),
             now, now, "ok", None, now, "upload", content))
        _insert_chunks(conn, task_id, doc_id, content)
        store.event(conn, task_id, "document_uploaded",
                    note=f"{kind} {eff_path} r{revision}", actor=actor)
    else:
        doc_id = row["id"]
        changed = row["content_hash"] != digest
        revision = int(row["revision"] or 0) + (1 if changed else 0)
        if changed:
            conn.execute(
                "UPDATE documents SET revision=?, content_hash=?, title=?, status='ok', "
                "error=NULL, checked_at=?, updated_at=?, source='upload', content=? WHERE id=?",
                (revision, digest, _document_title(content, eff_path), now, now, content, doc_id))
            _drop_chunks(conn, doc_id)
            _insert_chunks(conn, task_id, doc_id, content)
            store.event(conn, task_id, "document_uploaded",
                        note=f"{kind} {eff_path} r{revision}", actor=actor)
        else:
            conn.execute(
                "UPDATE documents SET status='ok', error=NULL, checked_at=?, updated_at=?, "
                "source='upload', content=? WHERE id=?",
                (now, now, content, doc_id))

    # Путь из 4.1/4.4 дописываем в карточку только после записи строки: переиндексация
    # внутри update_task должна найти строку `upload` и не пойти на диск.
    if explicit and (current or "") != eff_path:
        store.update_task(conn, task_id, actor=actor, **{field: eff_path})
    conn.commit()
    return document_json(conn, doc_id)


def get_document(conn: sqlite3.Connection, task_id: str, kind: str) -> dict:
    """Прочитать документ задачи: загруженный — из базы, файловый — с диска. Базу не пишет."""
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    _check_kind(kind)
    field = DOC_FIELDS[kind]
    path = task[field] if field in task.keys() else None
    if not path and kind == "decision" and "journal_path" in task.keys():
        path = task["journal_path"]
    if not path:
        raise errors_mod.NotFound(f"у задачи {task_id} нет документа {kind}")
    row = conn.execute("SELECT * FROM documents WHERE task_id=? AND kind=? AND path=?",
                       (task_id, kind, path)).fetchone()
    if row is not None and "source" in row.keys() and row["source"] == "upload":
        return {"task_id": task_id, "kind": kind, "path": path, "source": "upload",
                "revision": row["revision"], "content_hash": row["content_hash"],
                "status": "ok", "error": None, "content": row["content"] or ""}
    try:
        content = util.read_text(_resolve(conn, task, path))
        status, error = "ok", None
    except (OSError, UnicodeError) as exc:
        content, status, error = None, "missing", str(exc)
    return {"task_id": task_id, "kind": kind, "path": path, "source": "file",
            "revision": row["revision"] if row is not None else None,
            "content_hash": row["content_hash"] if row is not None else None,
            "status": status, "error": error, "content": content}


def index_task_documents(conn: sqlite3.Connection, task_id: str) -> list[dict]:
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise errors_mod.NotFound(f"задача не найдена: {task_id}")
    paths_to_index: list[tuple[str, str]] = []
    if task["spec_path"]:
        paths_to_index.append(("spec", task["spec_path"]))
    if "checklist_path" in task.keys() and task["checklist_path"]:
        paths_to_index.append(("checklist", task["checklist_path"]))
    # A journal path is a first-class document when explicitly supplied.
    # Explicit review/decision files are first class.  journal_path predates these
    # fields and remains an alias for a decision document.
    for key, kind in (("review_path", "review"), ("decision_path", "decision"),
                      ("journal_path", "decision")):
        value = task[key] if key in task.keys() else None
        if value and (kind, value) not in paths_to_index:
            paths_to_index.append((kind, value))
    return [index_document(conn, task_id, path, kind=kind) for kind, path in paths_to_index]


def refresh_all(conn: sqlite3.Connection) -> dict:
    """Re-check every task with at least one document path for on-disk changes.

    Called by the background worker (`server.start_embed_worker`) so edits to spec/checklist/
    review/decision/journal files are picked up without an explicit `context`/`update` call.
    Closed tasks are skipped: their files no longer change, and re-reading hundreds of them
    every cycle would be wasted work.
    """
    checked = reindexed = missing = 0
    task_ids = [r["id"] for r in conn.execute(
        """
        SELECT id FROM tasks
        WHERE status NOT IN ('done','cancelled','closed')
          AND (
                (spec_path IS NOT NULL AND spec_path != '') OR
                (checklist_path IS NOT NULL AND checklist_path != '') OR
                (review_path IS NOT NULL AND review_path != '') OR
                (decision_path IS NOT NULL AND decision_path != '') OR
                (journal_path IS NOT NULL AND journal_path != '')
              )
        """
    ).fetchall()]
    for task_id in task_ids:
        checked += 1
        try:
            before = {r["id"]: r["revision"] for r in conn.execute(
                "SELECT id, revision FROM documents WHERE task_id=?", (task_id,))}
            docs = index_task_documents(conn, task_id)
            for doc in docs:
                if doc.get("status") == "missing":
                    missing += 1
                elif before.get(doc["id"]) != doc.get("revision"):
                    reindexed += 1
        except Exception as exc:  # noqa: BLE001 — one task must not stop the sweep
            store.event(conn, task_id, "document_error",
                       note=f"refresh_all: {type(exc).__name__}: {exc}")
    return {"checked": checked, "reindexed": reindexed, "missing": missing}


def document_json(conn: sqlite3.Connection, doc_id: str) -> dict:
    row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise errors_mod.NotFound(f"документ не найден: {doc_id}")
    chunks = [dict(r) for r in conn.execute("SELECT * FROM document_chunks WHERE document_id=? ORDER BY ordinal", (doc_id,))]
    status = row["status"] if "status" in row.keys() else "ok"
    error = row["error"] if "error" in row.keys() else None
    return {"id": row["id"], "task_id": row["task_id"], "kind": row["kind"], "path": row["path"],
            "source": row["source"] if "source" in row.keys() else "file",
            "revision": row["revision"], "content_hash": row["content_hash"], "title": row["title"],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "chunks": chunks,
            "chunk_count": len(chunks), "status": status, "error": error, "ok": status == "ok"}


def _stable(value):
    """Strip wall-clock/derived fields so repeated `context` calls stay byte-stable."""
    drop_keys = {"stale", "abandoned", "stale_holder"}
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items()
                if not (k.endswith("_age") or k.endswith("_hours") or k in drop_keys)}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _dependencies(conn: sqlite3.Connection, task: dict, task_id: str, stage: str) -> dict:
    deps_state = task.get("deps_state")
    if deps_state is None:
        out = dict(_DEPENDENCIES_DEFAULTS)
        out["task_id"] = task_id
    else:
        out = _stable(dict(deps_state))
        out.pop("reasons", None)
    marks = ",".join("?" * len(deps_mod.HARD_BLOCKERS))
    hard = [dict(r) for r in conn.execute(
        f"SELECT d.depends_on AS depends_on, d.dep_type AS dep_type, t.status AS status, "
        f"t.title AS title FROM deps d LEFT JOIN tasks t ON t.id = d.depends_on "
        f"WHERE d.issue_id = ? AND d.dep_type IN ({marks}) ORDER BY d.depends_on",
        (task_id, *deps_mod.HARD_BLOCKERS))]
    suggested = [dict(r) for r in conn.execute(
        "SELECT d.depends_on AS depends_on, d.dep_type AS dep_type, t.status AS status, "
        "t.title AS title FROM deps d LEFT JOIN tasks t ON t.id = d.depends_on "
        "WHERE d.issue_id = ? AND d.dep_type = 'suggested-blocks' ORDER BY d.depends_on",
        (task_id,))]
    out["hard"] = hard
    out["suggested"] = suggested if stage in ("s1-spec", "s2-review") else []
    return out


def _worktree(task: dict, conn: sqlite3.Connection | None = None) -> dict:
    """Что происходит в рабочем дереве задачи — блок контекста судьи (`s4-judge`).

    `worktree=main`/`master` — маркер «работа идёт в основной ветке репозитория
    проекта, отдельного дерева нет» (`store.main_worktree`), а не каталог:
    проверяем репозиторий проекта (`projects.path`), а не значение поля.
    """
    raw = (task.get("worktree") or "").strip()
    if not raw:
        return {"exists": False, "reason": "worktree не задан в карточке"}
    marker = store.main_worktree(raw)
    base = {"value": raw, "mode": "main" if marker else "worktree",
            "title": store.main_worktree_title(raw)}
    if marker:
        project_path = ""
        if conn is not None and task.get("project"):
            prow = conn.execute("SELECT path FROM projects WHERE slug = ?",
                                (task["project"],)).fetchone()
            project_path = ((prow["path"] if prow else None) or "").strip()
        if not project_path:
            return {**base, "exists": False,
                    "reason": f"{base['title']}, но у проекта {task.get('project') or '—'} не указан путь"}
        raw = project_path
    path = util.expanduser(raw)
    try:
        resolved = util.resolved(path)
    except OSError:
        resolved = path
    if not resolved.is_dir():
        return {**base, "path": str(resolved), "exists": False, "reason": "каталог не найден"}
    is_git = (resolved / ".git").exists() or bool(store._git_value(resolved, "rev-parse", "--git-dir"))
    if not is_git:
        return {**base, "path": str(resolved), "exists": True, "git": False,
                "reason": "не git-репозиторий"}
    branch_raw = store._git_value(resolved, "rev-parse", "--abbrev-ref", "HEAD")
    # In a repository with no commits `git rev-parse HEAD` fails but still echoes the
    # unresolved argument to stdout, so a raw string check would misread that as a hash.
    branch = None if branch_raw == "HEAD" else branch_raw
    head_raw = store._git_value(resolved, "rev-parse", "HEAD")
    head = head_raw if head_raw and re.fullmatch(r"[0-9a-f]{40}", head_raw) else None
    status_out = store._git_value(resolved, "status", "--porcelain") or ""
    changed_files = status_out.splitlines()
    files_clipped = len(changed_files) > 200
    if files_clipped:
        changed_files = changed_files[:200]
    result = {**base, "path": str(resolved), "exists": True, "git": True, "branch": branch,
              "head": head, "changed_files": changed_files, "card_branch": task.get("branch")}
    reason_bits = []
    if head is None:
        result["diff_stat"] = ""
        reason_bits.append("нет коммитов")
    else:
        diff_stat = store._git_value(resolved, "diff", "--stat", "HEAD") or ""
        diff_clipped = len(diff_stat) > 4000
        if diff_clipped:
            diff_stat = diff_stat[:4000]
        result["diff_stat"] = diff_stat
        if diff_clipped:
            reason_bits.append("diff обрезан до 4000 символов")
    if files_clipped:
        reason_bits.append("список изменённых файлов обрезан до 200 строк")
    if reason_bits:
        result["reason"] = "; ".join(reason_bits)
    return result


def _journal_items(conn: sqlite3.Connection, task_id: str, journals: list[dict]) -> list[dict]:
    items = []
    for c in journals:
        items.append({"ts": c["created_at"], "kind": "journal", "actor": c["author"],
                      "text": c["text"], "id": c["id"], "from": None, "to": None})
    for e in conn.execute(
        "SELECT id, ts, actor, note, from_value, to_value FROM events WHERE task_id=? "
        "AND kind='stage' ORDER BY ts, id", (task_id,)):
        items.append({"ts": e["ts"], "kind": "stage", "actor": e["actor"],
                      "text": e["note"] or "", "id": f"event:{e['id']}",
                      "from": e["from_value"], "to": e["to_value"]})
    items.sort(key=lambda j: (j["ts"], j["kind"], j["id"]))
    return items


def _full_doc_chunks(docs: list[dict], stage: str, kinds: tuple[str, ...]) -> list[dict]:
    selected = []
    for doc in sorted((d for d in docs if d["kind"] in kinds),
                      key=lambda d: (_DOC_KIND_ORDER.get(d["kind"], 9), d.get("path", ""))):
        chunks = sorted(doc.get("chunks", []), key=lambda c: (c.get("ordinal", 0), c.get("id", "")))
        for chunk in chunks:
            reason = f"полный документ {doc['kind']} для этапа {stage}"
            selected.append({**chunk, "document_id": doc["id"], "kind": doc["kind"],
                             "path": doc["path"], "reason": reason})
    return selected


_PORTION_SPLIT = re.compile(r"[^0-9a-zа-яё]+")


def _portion_rank(child: dict, needle: str, word: re.Pattern) -> int | None:
    """Насколько карточка похожа на названную порцию: меньше — точнее."""
    if needle == (child.get("id") or "").lower():
        return 0
    if needle == (child.get("title") or "").lower():
        return 1
    if word.search((child.get("title") or "").lower()):
        return 2
    for doc in child.get("documents") or []:
        if word.search((doc.get("title") or "").lower()):
            return 2
        tokens = [t for t in _PORTION_SPLIT.split((doc.get("path") or "").lower()) if t]
        if needle in tokens:
            return 3
    return None


def match_portion_child(children: list[dict], portion: str | None) -> dict | None:
    """Дочерняя карточка-порция по названию `portion` (s3/s4).

    Совпадение ищется по id, точному заголовку, слову в заголовке карточки или
    документа и по токену пути документа (`step-09.check-a.md` → «a»). Если лучшее
    совпадение делят несколько карточек, порция не разрешается: молча вернуть
    чужую хуже, чем показать `children[]` и не угадывать."""
    needle = (portion or "").strip().lower()
    if not needle:
        return None
    word = re.compile(rf"(?<![0-9a-zа-яё_]){re.escape(needle)}(?![0-9a-zа-яё_])")
    best_rank: int | None = None
    matches: list[dict] = []
    for child in children:
        rank = _portion_rank(child, needle, word)
        if rank is None:
            continue
        if best_rank is None or rank < best_rank:
            best_rank, matches = rank, [child]
        elif rank == best_rank:
            matches.append(child)
    return matches[0] if len(matches) == 1 else None


def _layered_chunks(conn: sqlite3.Connection, task_id: str, task: dict, docs: list[dict],
                    portion: str | None) -> list[dict]:
    """s3/s4 chunk selection: checklist in full, then portion match or lexical match,
    then a fallback to the beginning of the spec if nothing else touched it (author decision)."""
    selected: list[dict] = []
    selected_ids: set[str] = set()

    def add(chunk: dict, doc: dict, reason: str) -> None:
        if chunk["id"] in selected_ids:
            return
        selected_ids.add(chunk["id"])
        selected.append({**chunk, "document_id": doc["id"], "kind": doc["kind"],
                         "path": doc["path"], "reason": reason})

    by_kind: dict[str, list[dict]] = {}
    for doc in docs:
        by_kind.setdefault(doc["kind"], []).append(doc)

    # Layer 1: checklist, whole.
    for doc in sorted(by_kind.get("checklist", []), key=lambda d: d.get("path", "")):
        for chunk in sorted(doc.get("chunks", []), key=lambda c: (c.get("ordinal", 0), c.get("id", ""))):
            add(chunk, doc, "чек-лист целиком")

    spec_decision_docs = sorted(
        [d for d in docs if d["kind"] in ("spec", "decision")],
        key=lambda d: (_DOC_KIND_ORDER.get(d["kind"], 9), d.get("path", "")))

    # Layer 2: explicit portion match on heading/breadcrumb.
    portion_hits = 0
    needle = (portion or "").strip().lower()
    if needle:
        for doc in spec_decision_docs:
            for chunk in sorted(doc.get("chunks", []), key=lambda c: (c.get("ordinal", 0), c.get("id", ""))):
                heading = (chunk.get("heading") or "").lower()
                breadcrumb = (chunk.get("breadcrumb") or "").lower()
                if needle in heading or needle in breadcrumb:
                    add(chunk, doc, f"совпадение с порцией «{portion}»")
                    portion_hits += 1

    # Layer 3: lexical fallback via FTS on title + acceptance, when no portion was given
    # or the portion layer found nothing.
    if not needle or portion_hits == 0:
        query_text = f"{task.get('title') or ''} {task.get('acceptance') or ''}".strip()
        fts_expr = textutil.fts_query_or(query_text)
        if fts_expr:
            chunk_lookup = {c["id"]: (doc, c) for doc in spec_decision_docs for c in doc.get("chunks", [])}
            rank = 0
            try:
                rows = conn.execute(
                    "SELECT f.chunk_id AS chunk_id, bm25(document_chunk_fts) AS score "
                    "FROM document_chunk_fts f JOIN documents d ON d.id = f.document_id "
                    "WHERE f.task_id = ? AND d.kind IN ('spec','decision') "
                    "AND document_chunk_fts MATCH ? ORDER BY score", (task_id, fts_expr))
            except sqlite3.OperationalError:
                rows = []
            for row in rows:
                hit = chunk_lookup.get(row["chunk_id"])
                if not hit:
                    continue
                doc, chunk = hit
                if chunk["id"] in selected_ids:
                    continue
                rank += 1
                add(chunk, doc, f"лексическое совпадение с заголовком и acceptance, ранг {rank}")

    # Layer 4: if no spec chunk made it in at all, fall back to the beginning of the spec.
    has_spec_chunk = any(c["kind"] == "spec" for c in selected)
    if not has_spec_chunk:
        for doc in sorted(by_kind.get("spec", []), key=lambda d: d.get("path", "")):
            for chunk in sorted(doc.get("chunks", []), key=lambda c: (c.get("ordinal", 0), c.get("id", ""))):
                add(chunk, doc, "начало документа: совпадений по порции и лексике нет")

    return selected


def context(conn: sqlite3.Connection, task_id: str, stage: str, *, portion: str | None = None,
            max_chars: int | None = None) -> dict:
    requested_task = store.get_task(conn, task_id, with_details=False)
    # Порции шага — дочерние карточки (решение listik-9gsh). На s3/s4 `portion`
    # сначала ищет дочернюю карточку: нашлась — контекст строится по ней (её
    # spec/checklist/review), а `portion_card` и `parent` показывают, куда он
    # разрешился. Не нашлась — работает прежний отбор по заголовку раздела ТЗ.
    children = store.child_cards(conn, task_id)
    portion_card = (match_portion_child(children, portion)
                    if stage in ("s3-impl", "s4-judge") else None)
    if portion_card is not None:
        task_id = portion_card["id"]
        task = store.get_task(conn, task_id, with_details=False)
    else:
        task = requested_task
    parent = store.parent_card(conn, task_id)
    docs = index_task_documents(conn, task_id)
    # Comments are the canonical review/journal/verdict stream.  They are kept separate
    # from document chunks so callers can distinguish a decision from source material.
    comments = [dict(r) for r in conn.execute(
        "SELECT id, author, kind, text, created_at FROM comments WHERE task_id=? "
        "AND kind IN ('review','verdict','journal') ORDER BY created_at, id", (task_id,))]
    reviews_all = [c for c in comments if c["kind"] == "review"]
    verdicts_all = [c for c in comments if c["kind"] == "verdict"]
    journals_all = [c for c in comments if c["kind"] == "journal"]

    default_for_stage = STAGE_MAX_CHARS.get(stage, 24000)
    effective_max = max_chars if max_chars is not None else default_for_stage

    if stage in ("s1-spec", "s2-review"):
        selected = _full_doc_chunks(docs, stage, ("spec", "checklist", "review", "decision"))
    else:
        selected = _layered_chunks(conn, task_id, task, docs, portion)

    # Deterministic clipping: preserve order and explain truncation.
    used = 0
    clipped: list[dict] = []
    dropped_chunks = 0
    truncated_chunks: list[dict] = []
    cut_at: dict | None = None
    for chunk in selected:
        text = chunk["text"]
        remain = effective_max - used
        if remain <= 0:
            dropped_chunks += 1
            if cut_at is None:
                cut_at = chunk
            continue
        if len(text) > remain:
            clipped_text = textutil.clip(text, remain)
            chunk = {**chunk, "text": clipped_text, "truncated": True}
            truncated_chunks.append({"chunk_id": chunk["id"], "path": chunk["path"],
                                     "start_line": chunk.get("start_line")})
            if cut_at is None:
                cut_at = chunk
        clipped.append(chunk)
        used += len(chunk["text"])
    truncated = dropped_chunks > 0 or bool(truncated_chunks)
    if truncated:
        if stage in ("s1-spec", "s2-review") and cut_at is not None:
            reason = (f"документ {cut_at['kind']} «{cut_at['path']}» не поместился целиком: "
                      f"обрезан начиная со строки {cut_at.get('start_line')}")
        else:
            reason = "лимит контекста исчерпан, часть блоков не поместилась"
    else:
        reason = "все выбранные блоки помещены"

    reviews = [] if stage == "s1-spec" else (
        reviews_all if stage == "s2-review" else reviews_all[-1:])
    verdict = verdicts_all[-1] if stage == "s4-judge" and verdicts_all else None
    journal = _journal_items(conn, task_id, journals_all) if stage == "s4-judge" else []
    worktree = _worktree(task, conn) if stage == "s4-judge" else None

    stable_task = _stable(task)
    stable_task.pop("deps_state", None)

    card = {k: task.get(k) for k in _CARD_KEYS}
    docs_out = [{k: v for k, v in doc.items() if k != "chunks"} for doc in docs]
    dependencies = _dependencies(conn, task, task_id, stage)

    reasons = [{"block": "card", "reason": "карточка задачи"},
               {"block": "acceptance", "reason": "критерии приёмки"},
               {"block": "dependencies", "reason": "зависимости и блокеры"}]
    if children:
        reasons.append({"block": "children",
                        "reason": "дочерние карточки порций с их документами"})
    if portion_card is not None:
        reasons.append({"block": "portion_card",
                        "reason": f"порция «{portion}» разрешилась в карточку {portion_card['id']}"})
    if parent is not None:
        reasons.append({"block": "parent", "reason": f"родительская карточка {parent['id']}"})
    if reviews:
        reasons.append({"block": "review", "reason": "ревью этапа"})
    if verdict is not None:
        reasons.append({"block": "verdict", "reason": "последний вердикт судьи"})
    if journal:
        reasons.append({"block": "journal", "reason": "журнал решений и переходов"})
    if worktree is not None:
        reasons.append({"block": "worktree", "reason": "состояние рабочего дерева"})
    reasons.extend({"block": "chunk", "chunk_id": c["id"], "reason": c["reason"]} for c in clipped)

    return {
        "task": stable_task,
        "card": card,
        "stage": stage,
        "portion": portion,
        "documents": docs_out,
        "chunks": clipped,
        "acceptance": task.get("acceptance", ""),
        "dependencies": dependencies,
        # Поля холодного старта карточки-порции: все дочерние карточки шага со своими
        # документами (даже закрытые), карточка, в которую разрешилась `portion`, и
        # родитель карточки, по которой построен контекст.
        "children": children,
        "portion_card": portion_card,
        "parent": parent,
        "reviews": reviews,
        "verdict": verdict,
        "journal": journal,
        "worktree": worktree,
        "limits": {"max_chars": effective_max, "default_for_stage": default_for_stage,
                   "used_chars": used, "truncated": truncated, "applies_to": "chunks",
                   "truncated_chunks": truncated_chunks, "dropped_chunks": dropped_chunks,
                   "reason": reason},
        "reasons": reasons,
        # Kept for API shape compatibility; this is the task revision timestamp,
        # not wall-clock generation time, so identical inputs serialize identically.
        "generated_at": task.get("updated_at"),
    }
