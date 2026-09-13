"""Эмбеддинги через локальный ollama (bge-m3, 1024 измерения).

Считаем векторы для задач и комментариев. Документ = размеченный текст:
  task:    [project] title  /  очищенное тело
  comment: [[project]] title  /  текст комментария
Пересчёт — по хешу текста: изменился текст, значит нужен новый вектор.
"""
from __future__ import annotations

import array
import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone

from . import paths, textutil


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def vec_to_blob(vec: list[float]) -> bytes:
    a = array.array("f", vec)
    return a.tobytes()


def blob_to_vec(blob: bytes) -> array.array:
    a = array.array("f")
    a.frombytes(blob)
    return a


def ollama_embed(texts: list[str], model: str = paths.EMBED_MODEL) -> list[list[float]]:
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urllib.request.Request(
        f"{paths.OLLAMA_URL}/api/embed", data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    embs = data.get("embeddings")
    if not embs:
        raise RuntimeError(f"ollama вернул пустой ответ: {str(data)[:200]}")
    return embs


def health(model: str = paths.EMBED_MODEL) -> dict:
    try:
        with urllib.request.urlopen(f"{paths.OLLAMA_URL}/api/tags", timeout=5) as resp:
            tags = json.loads(resp.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        ok = any(n.split(":")[0] == model.split(":")[0] for n in names)
        return {"ok": ok, "models": names, "model": model}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "model": model}


def task_doc(row: sqlite3.Row) -> str:
    body = textutil.clip("\n".join(
        x for x in (
            textutil.clean_markdown(row["description"]),
            textutil.clean_markdown(row["acceptance"]),
            textutil.clean_markdown(row["notes"]),
        ) if x
    ), paths.EMBED_MAX_CHARS - 500)
    head = f"[{row['project']}] {row['title']}"
    if row["issue_type"]:
        head = f"{head} ({row['issue_type']})"
    return f"{head}\n\n{body}".strip()


def comment_doc(row: sqlite3.Row) -> str:
    body = textutil.clip(textutil.clean_markdown(row["text"]), paths.EMBED_MAX_CHARS - 500)
    return f"[{row['project']}] {row['title']} — комментарий {row['author'] or '?'}:\n{body}"


def pending(conn: sqlite3.Connection, kinds: tuple[str, ...] = ("task", "comment")) -> list[dict]:
    """Документы, требующие эмбеддинга (нет вектора или текст изменился)."""
    out: list[dict] = []
    if "task" in kinds:
        for row in conn.execute(
            """
            SELECT t.id, t.project, t.title, t.description, t.acceptance, t.notes, t.issue_type,
                   e.text_hash AS old_hash
            FROM tasks t
            LEFT JOIN embeddings e ON e.doc_id = t.id AND e.doc_kind = 'task'
            """
        ):
            doc = task_doc(row)
            h = textutil.text_hash(doc)
            if row["old_hash"] != h:
                out.append({"doc_id": row["id"], "kind": "task", "task_id": row["id"],
                            "project": row["project"], "text": doc, "hash": h})
    if "comment" in kinds:
        for row in conn.execute(
            """
            SELECT c.id, c.task_id, t.project AS project, c.text, c.author, t.title,
                   e.text_hash AS old_hash
            FROM comments c
            JOIN tasks t ON t.id = c.task_id
            LEFT JOIN embeddings e ON e.doc_id = c.id AND e.doc_kind = 'comment'
            """
        ):
            doc = comment_doc(row)
            h = textutil.text_hash(doc)
            if row["old_hash"] != h:
                out.append({"doc_id": row["id"], "kind": "comment", "task_id": row["task_id"],
                            "project": row["project"], "text": doc, "hash": h})
    if "chunk" in kinds:
        try:
            rows = conn.execute(
                """SELECT c.id, c.task_id, t.project, t.title, c.heading, c.breadcrumb, c.text,
                          e.text_hash AS old_hash
                   FROM document_chunks c JOIN documents d ON d.id=c.document_id
                   JOIN tasks t ON t.id=d.task_id
                   LEFT JOIN embeddings e ON e.doc_id=c.id AND e.doc_kind='chunk'"""
            )
            for row in rows:
                doc = f"[{row['project']}] {row['title']} — {row['breadcrumb'] or row['heading'] or ''}\n{row['text']}"
                h = textutil.text_hash(doc)
                if row["old_hash"] != h:
                    out.append({"doc_id": row["id"], "kind": "chunk", "task_id": row["task_id"],
                                "project": row["project"], "text": textutil.clip(doc, paths.EMBED_MAX_CHARS - 100), "hash": h})
        except sqlite3.OperationalError:
            pass
    if "memory" in kinds:
        for row in conn.execute(
            """
            SELECT m.key, m.project, m.body, e.text_hash AS old_hash
            FROM memories m
            LEFT JOIN memory_embeddings e ON e.memory_key = m.key
            """
        ):
            doc = f"[{row['project'] or 'память'}] {row['body']}".strip()
            h = textutil.text_hash(doc)
            if row["old_hash"] != h:
                out.append({"doc_id": row["key"], "kind": "memory", "task_id": row["key"],
                            "project": row["project"], "text": doc, "hash": h})
    return out


def embed_pending(
    conn: sqlite3.Connection,
    *,
    limit: int = 0,
    kinds: str = "task,comment,chunk",
    model: str = paths.EMBED_MODEL,
    verbose: bool = True,
) -> dict:
    kind_tuple = tuple(k.strip() for k in kinds.split(",") if k.strip())
    work = pending(conn, kind_tuple)
    if limit:
        work = work[:limit]
    if not work:
        return {"embedded": 0, "pending": 0, "model": model}

    done = 0
    total = len(work)
    batch = max(1, paths.EMBED_BATCH)
    ts = now_iso()
    for start in range(0, total, batch):
        chunk = work[start:start + batch]
        try:
            vecs = ollama_embed([c["text"] for c in chunk], model=model)
        except urllib.error.URLError as exc:
            return {"embedded": done, "pending": total - done, "model": model,
                    "error": f"ollama недоступен: {exc}"}
        for c, vec in zip(chunk, vecs):
            if c["kind"] == "memory":
                conn.execute(
                    "INSERT INTO memory_embeddings(memory_key, model, dim, vec, text_hash, "
                    "embedded_at) VALUES(?,?,?,?,?,?) ON CONFLICT(memory_key) DO UPDATE SET "
                    "model=excluded.model, dim=excluded.dim, vec=excluded.vec, "
                    "text_hash=excluded.text_hash, embedded_at=excluded.embedded_at",
                    (c["doc_id"], model, len(vec), vec_to_blob(vec), c["hash"], ts),
                )
                continue
            conn.execute(
                "INSERT INTO embeddings(doc_id, doc_kind, task_id, project, model, dim, vec, "
                "text_hash, embedded_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, "
                "vec=excluded.vec, text_hash=excluded.text_hash, embedded_at=excluded.embedded_at",
                (c["doc_id"], c["kind"], c["task_id"], c["project"], model, len(vec),
                 vec_to_blob(vec), c["hash"], ts),
            )
        done += len(chunk)
        conn.commit()
        if verbose:
            print(f"  эмбеддинги {done}/{total}", flush=True)
    return {"embedded": done, "pending": total - done, "model": model}


def cosine(a, b, na: float | None = None, nb: float | None = None) -> float:
    if na is None:
        na = sum(x * x for x in a) ** 0.5
    if nb is None:
        nb = sum(x * x for x in b) ** 0.5
    if not na or not nb:
        return 0.0
    dot = 0.0
    for x, y in zip(a, b):
        dot += x * y
    return dot / (na * nb)
