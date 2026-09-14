"""Гибридный поиск по всем задачам: BM25 (FTS5) + векторы (bge-m3), слияние через RRF.

Рецепт тот же, что в боевом zoloto585-search (Rrf.php): два независимых списка
ранжирования, скор = сумма 1/(k + rank). Ничего не «взвешиваем» вручную — ранги
устойчивее скоров, а k=60 гасит влияние хвоста.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time

from . import actors, embed, paths, textutil

RRF_K = 60
SNIPPET_CHARS = 220

# Токен, похожий на id задачи: `<слово>-<хвост>` (`listik-b3j0`, `zoloto585-search-x3l`).
ID_TOKEN_RE = re.compile(r"[\w.-]+-[0-9a-z]{3,}", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-яё]", re.IGNORECASE)


def _filters_sql(project: str | None, status: str | None) -> tuple[str, list]:
    where, params = [], []
    if project:
        where.append("t.project LIKE ?")
        params.append(f"%{project}%")
    if status:
        where.append("t.status = ?")
        params.append(status)
    return (" AND " + " AND ".join(where) if where else ""), params


def _row_filter(row: sqlite3.Row, stage: str | None, actor: str | None, needs_owner: bool) -> bool:
    if stage:
        st = (row["stage"] or "")
        if not (st == stage or st.startswith(stage) or stage in st):
            return False
    if actor and (row["assignee"] or "") != actor and (row["holder"] or "") != actor:
        return False
    if needs_owner and not row["needs_owner"]:
        return False
    return True


def _task_matches(row: sqlite3.Row, project: str | None, status: str | None, stage: str | None,
                  actor: str | None, needs_owner: bool) -> bool:
    """Фильтры поиска на уровне задачи — одни и те же для RRF- и id-совпадений."""
    if project and project.lower() not in (row["project"] or "").lower():
        return False
    if status and row["status"] != status:
        return False
    return _row_filter(row, stage, actor, needs_owner)


def _snippet(text: str, terms: list[str]) -> str:
    clean = " ".join((text or "").split())
    if not clean:
        return ""
    low = clean.lower()
    pos = -1
    for term in terms:
        p = low.find(term.lower())
        if p >= 0 and (pos < 0 or p < pos):
            pos = p
    if pos < 0:
        return clean[:SNIPPET_CHARS]
    start = max(0, pos - SNIPPET_CHARS // 3)
    end = min(len(clean), start + SNIPPET_CHARS)
    return ("…" if start else "") + clean[start:end] + ("…" if end < len(clean) else "")


def lexical(conn: sqlite3.Connection, query: str, project: str | None, status: str | None,
            limit: int) -> list[dict]:
    """BM25 по задачам и комментариям. Сначала строгий AND, при пустом — мягкий OR."""
    fsql, fparams = _filters_sql(project, status)
    expr = textutil.fts_query(query)
    out = _lexical_run(conn, expr, fsql, fparams, limit) if expr else []
    if not out:
        soft = textutil.fts_query_or(query)
        if soft and soft != expr:
            out = _lexical_run(conn, soft, fsql, fparams, limit)
    return out[:limit]


def _lexical_run(conn: sqlite3.Connection, expr: str, fsql: str, fparams: list,
                 limit: int) -> list[dict]:
    out: list[dict] = []

    task_sql = f"""
        SELECT t.id, t.title, t.description, t.notes, t.acceptance, t.status, t.project,
               t.stage, t.holder, t.priority, t.issue_type, t.updated_at,
               bm25(task_fts, 8.0, 1.0, 2.0) AS score,
               snippet(task_fts, 2, '[[', ']]', '…', 24) AS snip
        FROM task_fts
        JOIN tasks t ON t.id = task_fts.task_id
        WHERE task_fts MATCH ?{fsql}
        ORDER BY score
        LIMIT ?
    """
    for row in conn.execute(task_sql, [expr, *fparams, limit]):
        out.append({"kind": "task", "doc_id": row["id"], "task_id": row["id"],
                    "score": -float(row["score"] or 0), "snippet": row["snip"] or "",
                    "row": row})

    comment_sql = f"""
        SELECT c.id AS cid, c.task_id, t.title, t.status, t.project, t.stage, t.holder,
               c.author, c.text, c.created_at,
               bm25(comment_fts) AS score,
               snippet(comment_fts, 2, '[[', ']]', '…', 24) AS snip
        FROM comment_fts
        JOIN comments c ON c.id = comment_fts.comment_id
        JOIN tasks t ON t.id = c.task_id
        WHERE comment_fts MATCH ?{fsql}
        ORDER BY score
        LIMIT ?
    """
    for row in conn.execute(comment_sql, [expr, *fparams, limit]):
        out.append({"kind": "comment", "doc_id": row["cid"], "task_id": row["task_id"],
                    "score": -float(row["score"] or 0), "snippet": row["snip"] or "",
                    "row": row})
    # Documents are indexed at chunk level; aggregate back to the owning task.
    try:
        chunk_sql = f"""
            SELECT f.chunk_id, f.document_id, f.task_id, f.heading, f.breadcrumb,
                   bm25(document_chunk_fts) AS score,
                   snippet(document_chunk_fts, 5, '[[', ']]', '…', 24) AS snip
            FROM document_chunk_fts f JOIN tasks t ON t.id = f.task_id
            WHERE document_chunk_fts MATCH ?{fsql}
            ORDER BY score LIMIT ?
        """
        for row in conn.execute(chunk_sql, [expr, *fparams, limit]):
            out.append({"kind": "chunk", "doc_id": row["chunk_id"], "task_id": row["task_id"],
                        "score": -float(row["score"] or 0), "snippet": row["snip"] or "",
                        "heading": row["heading"], "breadcrumb": row["breadcrumb"], "row": row})
    except sqlite3.OperationalError:
        # Existing databases are upgraded lazily; task/comment search remains usable.
        pass
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


# Векторы держим в памяти: на 3-4 тысячах документов это единицы мегабайт,
# а повторный поиск перестаёт ждать выгрузку блобов из базы.
_VEC_CACHE: dict[str, tuple[float, list]] = {}
_VEC_TTL = 300.0


def _vectors(conn: sqlite3.Connection, model: str) -> list:
    import time as _time
    cached = _VEC_CACHE.get(model)
    now = _time.monotonic()
    if cached and now - cached[0] < _VEC_TTL:
        return cached[1]
    rows = conn.execute(
        "SELECT doc_id, doc_kind, task_id, vec FROM embeddings WHERE model = ?",
        (model,)).fetchall()
    loaded = [(r["doc_id"], r["doc_kind"], r["task_id"], embed.blob_to_vec(r["vec"]))
              for r in rows]
    _VEC_CACHE[model] = (now, loaded)
    return loaded


def invalidate_vectors() -> None:
    _VEC_CACHE.clear()


def vector(conn: sqlite3.Connection, query: str, limit: int,
           model: str = paths.EMBED_MODEL) -> list[dict]:
    """Косинусная близость по всем векторам (индекс маленький — считаем в памяти)."""
    rows = _vectors(conn, model)
    if not rows:
        return []
    try:
        qvec = embed.ollama_embed([query], model=model)[0]
    except Exception:  # noqa: BLE001 — векторная ветка не должна ломать поиск
        return []
    qnorm = sum(x * x for x in qvec) ** 0.5
    if not qnorm:
        return []
    scored = []
    for doc_id, doc_kind, task_id, vec in rows:
        if len(vec) != len(qvec):
            continue
        dot = 0.0
        nv = 0.0
        for x, y in zip(qvec, vec):
            dot += x * y
            nv += y * y
        nv = nv ** 0.5
        if not nv:
            continue
        scored.append((dot / (qnorm * nv), doc_id, doc_kind, task_id))
    scored.sort(reverse=True)
    return [{"kind": k, "doc_id": d, "task_id": t, "score": s} for s, d, k, t in scored[:limit]]


def search_memories(conn: sqlite3.Connection, query: str, *, limit: int = 10,
                    project: str | None = None, mode: str = "hybrid") -> list[dict]:
    """Поиск по долговременной памяти: заметки, не привязанные к задаче."""
    scores: dict[str, float] = {}
    expr = textutil.fts_query(query)
    rows: list = []
    if mode in ("hybrid", "text") and expr:
        fsql = " AND m.project LIKE ?" if project else ""
        params = [expr] + ([f"%{project}%"] if project else []) + [limit * 4]
        rows = conn.execute(
            f"""SELECT m.key, m.project, m.body, bm25(memory_fts) AS score
                FROM memory_fts JOIN memories m ON m.key = memory_fts.memory_key
                WHERE memory_fts MATCH ?{fsql} ORDER BY score LIMIT ?""", params).fetchall()
    for rank, r in enumerate(rows, start=1):
        scores[r["key"]] = scores.get(r["key"], 0.0) + 1.0 / (RRF_K + rank)

    if mode in ("hybrid", "vector"):
        try:
            qvec = embed.ollama_embed([query])[0]
        except Exception:  # noqa: BLE001 — векторная ветка не должна ломать поиск
            qvec = None
        if qvec:
            qnorm = sum(x * x for x in qvec) ** 0.5
            vec_rows = []
            for r in conn.execute("SELECT * FROM memory_embeddings"):
                vec = embed.blob_to_vec(r["vec"])
                if len(vec) != len(qvec):
                    continue
                dot = sum(x * y for x, y in zip(qvec, vec))
                nv = sum(y * y for y in vec) ** 0.5
                if qnorm and nv:
                    vec_rows.append((dot / (qnorm * nv), r["memory_key"]))
            vec_rows.sort(reverse=True)
            for rank, (_, key) in enumerate(vec_rows[: limit * 4], start=1):
                scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)

    if not scores:
        return []
    keys = sorted(scores, key=lambda k: -scores[k])[:limit]
    marks = ",".join("?" * len(keys))
    found = {r["key"]: r for r in conn.execute(
        f"SELECT * FROM memories WHERE key IN ({marks})", keys)}
    out = []
    for key in keys:
        row = found.get(key)
        if not row or (project and project.lower() not in (row["project"] or "").lower()):
            continue
        out.append({"key": row["key"], "project": row["project"], "body": row["body"],
                    "source": row["source"], "updated_at": row["updated_at"],
                    "score": round(scores[key], 6)})
    return out


def print_memories(items: list[dict]) -> None:
    if not items:
        print("  памяти по такому запросу нет")
        return
    for m in items:
        print(f"● {m['key']}  [{m['project'] or '—'}]  {m['updated_at']}")
        for line in (m["body"] or "").splitlines()[:12]:
            print(f"    {line[:150]}")
        print()


def load_task_rows(conn: sqlite3.Connection, ids: list[str]) -> dict[str, sqlite3.Row]:
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    rows = conn.execute(f"SELECT * FROM tasks WHERE id IN ({marks})", ids).fetchall()
    return {r["id"]: r for r in rows}


def _like_literal(tok: str) -> str:
    """Экранирует `%`/`_`/`\\`, чтобы хвост id искался буквально, а не как LIKE-шаблон."""
    return tok.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _id_candidates(query: str) -> list[str]:
    """Кандидаты в id задачи: токены вида `slug-b3j0` и одинокий хвост (`b3j0`)."""
    q = (query or "").strip()
    out: list[str] = []
    for m in ID_TOKEN_RE.finditer(q):
        tok = m.group(0)
        if tok not in out:
            out.append(tok)
    # Одинокое слово без кириллицы — пробуем как хвост id: `b3j0` → `%-b3j0`.
    if len(q.split()) == 1 and len(q) >= 3 and not _CYRILLIC_RE.search(q) and q not in out:
        out.append(q)
    return out


def _id_tasks(conn: sqlite3.Connection, query: str) -> list[tuple[sqlite3.Row, bool]]:
    """Задачи по точному id или по хвосту id (`b3j0` → `%-b3j0`), без учёта регистра.

    Возвращает пары `(строка задачи, точное ли совпадение)`; точные совпадения идут
    раньше хвостовых, дублей нет. Фильтры здесь не применяются — их накладывает `search()`.
    """
    found: dict[str, tuple[sqlite3.Row, bool]] = {}
    for tok in _id_candidates(query):
        rows = conn.execute(
            "SELECT * FROM tasks WHERE lower(id) = lower(?) ORDER BY updated_at DESC",
            (tok,)).fetchall()
        is_exact = bool(rows)
        if not rows:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE lower(id) LIKE lower(?) ESCAPE '\\' "
                "ORDER BY updated_at DESC", ("%-" + _like_literal(tok),)).fetchall()
        for row in rows:
            if row["id"] not in found:
                found[row["id"]] = (row, is_exact)
    return sorted(found.values(), key=lambda pair: not pair[1])


def _id_hit(row: sqlite3.Row, score: float) -> dict:
    """Хит по id: та же форма, что у остальных `hits[]`, но `kind=id` и заголовок в snippet."""
    return {
        "kind": "id",
        "doc_id": row["id"],
        "rrf": round(score, 6),
        "snippet": row["title"] or "",
        "author": None,
        "heading": None,
        "breadcrumb": None,
        "document_id": None,
        "document_kind": None,
        "path": None,
        "start_line": None,
        "end_line": None,
    }


def _card(row: sqlite3.Row, *, snippet: str, score: float, hits: list[dict],
          best_hit: dict | None) -> dict:
    """Карточка задачи в ответе поиска — общая для RRF- и id-совпадений."""
    try:
        labels = json.loads(row["labels"] or "[]")
    except json.JSONDecodeError:
        labels = []
    try:
        blocked_by = json.loads(row["blocked_by"] or "[]")
    except (json.JSONDecodeError, IndexError, KeyError):
        blocked_by = []
    return {
        "blocked_by": blocked_by,
        "blocked_count": len(blocked_by),
        "id": row["id"],
        "project": row["project"],
        "title": row["title"],
        "status": row["status"],
        "stage": row["stage"],
        "holder": row["holder"],
        "assignee": row["assignee"],
        "assignee_title": actors.display(row["assignee"]),
        "priority": row["priority"],
        "issue_type": row["issue_type"],
        "updated_at": row["updated_at"],
        "labels": labels,
        "snippet": snippet,
        "score": score,
        "hits": hits,
        "best_hit": best_hit,
        "needs_owner": bool(row["needs_owner"]),
    }


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 15,
    project: str | None = None,
    status: str | None = None,
    stage: str | None = None,
    actor: str | None = None,
    needs_owner: bool = False,
    mode: str = "hybrid",
    pool: int = 200,
) -> dict:
    """Гибридный поиск по задачам, комментариям, чанкам документов и памяти.

    Совпадения по точному id задачи (а в `text`/`hybrid` — и по хвосту id) идут первыми,
    с `hits[].kind = "id"` и score выше любого RRF-совпадения; в режиме `vector` id
    не подмешивается.
    """
    t0 = time.time()
    lex: list[dict] = []
    vec: list[dict] = []
    if mode in ("hybrid", "text"):
        lex = lexical(conn, query, project, status, pool)
    if mode in ("hybrid", "vector"):
        vec = vector(conn, query, pool)

    # Точное совпадение по id: `task_fts.task_id` объявлен UNINDEXED, поэтому id ищется
    # отдельным запросом по `tasks` и ставится выше любых RRF-совпадений. В `vector`
    # режиме id не подмешивается.
    id_matches: list[tuple[sqlite3.Row, bool]] = []
    if mode in ("hybrid", "text"):
        id_matches = [pair for pair in _id_tasks(conn, query)
                      if _task_matches(pair[0], project, status, stage, actor, needs_owner)]

    # Обогащение чанк-хитов метаданными раздела: document_chunk_fts не хранит path/строки/
    # вид документа, поэтому дочитываем их одним запросом для всех chunk_id из обеих веток.
    # Заодно отбрасываем "сирот" — id чанков, удалённых при переиндексации, но всё ещё
    # присутствующих в embeddings (кеш векторов процессный, invalidate_vectors может не
    # долетать до этого процесса) — их не должно быть ни в ranks, ни в score задачи.
    chunk_ids = {item["doc_id"] for item in lex + vec if item["kind"] == "chunk"}
    chunk_meta: dict[str, sqlite3.Row] = {}
    if chunk_ids:
        marks = ",".join("?" * len(chunk_ids))
        chunk_meta = {r["id"]: r for r in conn.execute(
            f"""SELECT c.id, c.document_id, c.heading, c.breadcrumb, c.start_line, c.end_line,
                       d.kind AS document_kind, d.path
                FROM document_chunks c JOIN documents d ON d.id = c.document_id
                WHERE c.id IN ({marks})""", list(chunk_ids))}
    lex = [item for item in lex if item["kind"] != "chunk" or item["doc_id"] in chunk_meta]
    vec = [item for item in vec if item["kind"] != "chunk" or item["doc_id"] in chunk_meta]

    # RRF по документам
    ranks: dict[tuple[str, str], float] = {}
    for lst in (lex, vec):
        for rank, item in enumerate(lst, start=1):
            key = (item["kind"], item["doc_id"])
            ranks[key] = ranks.get(key, 0.0) + 1.0 / (RRF_K + rank)

    # Собираем на уровне задачи: лучший документ задачи + все совпавшие документы.
    # Векторный элемент кладём первым, лексический перезаписывает общие ключи (snippet,
    # row, heading, breadcrumb) — так у чанка, найденного обеими ветками, не пропадает
    # лексический snippet, а векторная запись лишь добавляет то, чего в лексической нет.
    per_task: dict[str, dict] = {}
    info: dict[tuple[str, str], dict] = {}
    for i in vec:
        info[(i["kind"], i["doc_id"])] = i
    for i in lex:
        key = (i["kind"], i["doc_id"])
        info[key] = {**info[key], **i} if key in info else i
    for (kind, doc_id), score in ranks.items():
        item = info[(kind, doc_id)]
        tid = item["task_id"]
        entry = per_task.setdefault(tid, {"task_id": tid, "score": 0.0, "hits": []})
        entry["score"] += score
        meta = chunk_meta.get(doc_id) if kind == "chunk" else None
        entry["hits"].append({
            "kind": kind,
            "doc_id": doc_id,
            "rrf": round(score, 6),
            "snippet": item.get("snippet") or "",
            "author": (item.get("row")["author"] if kind == "comment" and item.get("row") else None),
            "heading": meta["heading"] if meta else None,
            "breadcrumb": meta["breadcrumb"] if meta else None,
            "document_id": meta["document_id"] if meta else None,
            "document_kind": meta["document_kind"] if meta else None,
            "path": meta["path"] if meta else None,
            "start_line": meta["start_line"] if meta else None,
            "end_line": meta["end_line"] if meta else None,
        })

    rows = load_task_rows(conn, list(per_task))
    rrf_results = []
    for tid, entry in per_task.items():
        row = rows.get(tid)
        if row is None:
            continue
        if not _task_matches(row, project, status, stage, actor, needs_owner):
            continue
        sorted_hits = sorted(entry["hits"], key=lambda h: -h["rrf"])
        best_hit = dict(sorted_hits[0]) if sorted_hits else None
        snippet = ""
        for hit in sorted_hits:
            if hit["snippet"]:
                snippet = hit["snippet"]
                break
        if not snippet:
            snippet = _snippet(row["description"] or row["title"], query.split())
        rrf_results.append(_card(row, snippet=snippet, score=round(entry["score"], 6),
                                 hits=entry["hits"][:4], best_hit=best_hit))
    rrf_results.sort(key=lambda r: -r["score"])

    # id-совпадения — строго первыми и с score выше любого RRF: RRF задачи это сумма
    # 1/(K+rank) по её попаданиям, поэтому max+1 заведомо больше любой такой суммы.
    id_score = round(max((r["score"] for r in rrf_results), default=0.0) + 1.0, 6)
    id_results = []
    id_seen: set[str] = set()
    for row, _exact in id_matches[:limit]:
        tid = row["id"]
        if tid in id_seen:
            continue
        id_seen.add(tid)
        hit = _id_hit(row, id_score)
        id_results.append(_card(row, snippet=row["title"] or "", score=id_score,
                                hits=[hit], best_hit=dict(hit)))
    results = (id_results + [r for r in rrf_results if r["id"] not in id_seen])[:limit]
    # Память ищем тем же запросом: заметка без задачи часто и есть ответ
    try:
        memories = search_memories(conn, query, limit=max(3, limit // 3), project=project,
                                   mode=mode)
    except Exception:  # noqa: BLE001
        memories = []
    return {
        "memories": memories,
        "query": query,
        "mode": mode,
        "took_ms": int((time.time() - t0) * 1000),
        "lexical_docs": len(lex),
        "vector_docs": len(vec),
        "count": len(results),
        "results": results,
    }


STATUS_ICON = {
    "open": "○", "in_progress": "▶", "blocked": "■", "deferred": "◌", "closed": "✓",
}
STAGE_ICON = {
    "s1-spec": "ТЗ", "s2-review": "крит", "s3-impl": "код", "s4-judge": "судья", "done": "готово",
}


def print_results(res: dict) -> None:
    print(f"запрос: {res['query']!r}  режим: {res['mode']}  "
          f"найдено: {res['count']}  за {res['took_ms']} мс "
          f"(лексика {res['lexical_docs']}, векторы {res['vector_docs']})")
    if not res["results"] and not res.get("memories"):
        print("  ничего не найдено")
        return
    if res.get("memories"):
        print(f"память ({len(res['memories'])}):")
        for m in res["memories"]:
            body = " ".join((m["body"] or "").split())
            print(f"  ● {m['key']}  [{m['project'] or '—'}]  {body[:160]}")
        print()
    for i, r in enumerate(res["results"], start=1):
        st = STATUS_ICON.get(r["status"], "?")
        stage = STAGE_ICON.get(r["stage"] or "", r["stage"] or "")
        flags = []
        if r["needs_owner"]:
            flags.append("НУЖЕН ТЫ")
        if r.get("blocked_count"):
            flags.append(f"ждёт {r['blocked_count']}")
        if r["holder"]:
            flags.append(f"держит {r['holder']}")
        head = f"{i:2d}. {st} {r['id']}  [{r['project']}]"
        if stage:
            head += f"  этап: {stage}"
        if flags:
            head += "  «" + ", ".join(flags) + "»"
        print(head)
        print(f"    {r['title'][:150]}")
        best_hit = r.get("best_hit")
        if best_hit and best_hit.get("kind") == "chunk":
            section = best_hit.get("breadcrumb") or best_hit.get("heading") or "?"
            print(f"    раздел: {section} ({best_hit.get('document_kind')} "
                  f"{best_hit.get('path')}:{best_hit.get('start_line')})")
        if r.get("assignee_title"):
            print(f"    исполнитель: {r['assignee_title']}   обновлено: {r['updated_at']}")
        if r["snippet"]:
            print(f"    …{r['snippet'][:220]}")
        print()
