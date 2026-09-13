"""Схема и подключение к базе Listik.

Listik — самостоятельный трекер: база живёт в Listik/listik.db и является единственным
источником истины. Старые .beads-проекты можно разово импортировать (kind='beads'),
но пишет в базу только Listik.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import paths

SCHEMA_VERSION = 6

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Проекты и рабочие области
CREATE TABLE IF NOT EXISTS projects (
    slug         TEXT PRIMARY KEY,       -- короткое имя: zoloto585-search, personal, ...
    title        TEXT,
    kind         TEXT NOT NULL DEFAULT 'native',   -- native | beads
    path         TEXT,                   -- путь к каталогу проекта (если есть)
    git_remote   TEXT,
    git_branch   TEXT,
    color        TEXT,
    archived     INTEGER NOT NULL DEFAULT 0,
    imported_at  TEXT,
    import_note  TEXT,
    routing      TEXT,                   -- optional project routing JSON
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- Задачи: сначала в Listik, потом в голове
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,       -- человекочитаемый ID: zoloto585-search-a1b2 или lk-000123
    project      TEXT,
    title        TEXT NOT NULL DEFAULT '',
    description  TEXT NOT NULL DEFAULT '',
    acceptance   TEXT NOT NULL DEFAULT '',
    design       TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    result       TEXT NOT NULL DEFAULT '',  -- чем кончилось
    status       TEXT NOT NULL DEFAULT 'open',   -- open|in_progress|blocked|review|done|cancelled
    stage        TEXT,                   -- s1-spec|s2-review|s3-impl|s4-judge|done (этап конвейера)
    priority     INTEGER NOT NULL DEFAULT 2,
    issue_type   TEXT NOT NULL DEFAULT 'task',   -- task|bug|feature|epic|chore|decision|question
    assignee     TEXT,                   -- actor_key: me | agent:claude | agent:dsh | ...
    holder       TEXT,                   -- кто держит прямо сейчас (может отличаться от assignee)
    holder_at    TEXT,                   -- heartbeat держащего
    holder_note  TEXT,                   -- что именно он делает сейчас
    stage_at     TEXT,                   -- когда вошёл в текущий этап
    needs_owner  INTEGER NOT NULL DEFAULT 0,  -- 1 = ждёт человека
    labels       TEXT NOT NULL DEFAULT '[]',
    spec_path    TEXT,                   -- путь к ТЗ/спеке
    checklist_path TEXT,                 -- путь к чек-листу приёмки
    review_path  TEXT,                   -- файл предыдущего ревью (если есть)
    decision_path TEXT,                  -- файл решения/ADR (если есть)
    journal_path TEXT,                   -- путь к журналу конвейера
    worktree     TEXT,                   -- рабочее дерево, если работа идёт в отдельном
    branch       TEXT,
    blocked_by   TEXT NOT NULL DEFAULT '[]',   -- JSON: незакрытые блокеры
    source       TEXT NOT NULL DEFAULT 'native',  -- native | beads
    external_ref TEXT,                   -- старое ID в beads/github и т.п.
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by   TEXT,
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    started_at   TEXT,
    closed_at    TEXT,
    close_reason TEXT,
    archived     INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT,
    indexed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_stage    ON tasks(stage);
CREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks(project);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_tasks_holder   ON tasks(holder);
CREATE INDEX IF NOT EXISTS idx_tasks_updated  ON tasks(updated_at DESC);

-- Рёбра зависимостей
CREATE TABLE IF NOT EXISTS deps (
    issue_id   TEXT NOT NULL,
    depends_on TEXT NOT NULL,
    dep_type   TEXT NOT NULL DEFAULT 'blocks',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by TEXT,
    PRIMARY KEY (issue_id, depends_on, dep_type)
);
CREATE INDEX IF NOT EXISTS idx_deps_depends_on ON deps(depends_on);

-- Комментарии и журнал работы
CREATE TABLE IF NOT EXISTS comments (
    id         TEXT PRIMARY KEY,
    task_id    TEXT NOT NULL,
    author     TEXT,
    kind       TEXT NOT NULL DEFAULT 'comment',  -- comment|journal|question|answer|review|verdict
    text       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON comments(task_id, created_at);

-- События: история переходов, из неё же считается «сколько времени на этапе»
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    kind       TEXT NOT NULL,   -- created|stage|claim|release|heartbeat|note|question|answer|status|close|reopen|comment|import
    from_value TEXT,
    to_value   TEXT,
    actor      TEXT,
    harness    TEXT,
    note       TEXT,
    duration_s INTEGER          -- сколько секунд занял закрытый этап/отрезок
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts DESC);

-- Нормализация исполнителей
CREATE TABLE IF NOT EXISTS actors (
    key        TEXT PRIMARY KEY,   -- me | agent:claude | ...
    title      TEXT,
    kind       TEXT NOT NULL DEFAULT 'human',   -- human|agent
    kind_hint  TEXT,                             -- claude|dsh|grok|codex|gemini|human
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE IF NOT EXISTS actor_aliases (
    raw   TEXT PRIMARY KEY,
    actor TEXT NOT NULL
);

-- Полнотекстовый поиск (BM25)
CREATE VIRTUAL TABLE IF NOT EXISTS task_fts USING fts5(
    task_id UNINDEXED,
    title,
    body,
    labels,
    tokenize = "unicode61 remove_diacritics 2 tokenchars '-_.'"
);

CREATE VIRTUAL TABLE IF NOT EXISTS comment_fts USING fts5(
    comment_id UNINDEXED,
    task_id UNINDEXED,
    body,
    tokenize = "unicode61 remove_diacritics 2 tokenchars '-_.'"
);

-- Векторы
CREATE TABLE IF NOT EXISTS embeddings (
    doc_id      TEXT PRIMARY KEY,     -- task_id или comment_id
    doc_kind    TEXT NOT NULL,        -- task|comment
    task_id     TEXT NOT NULL,
    project     TEXT,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vec         BLOB NOT NULL,
    text_hash   TEXT NOT NULL,
    embedded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_kind ON embeddings(doc_kind);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Долговременная память: заметки, не привязанные к задаче (аналог bd remember)
CREATE TABLE IF NOT EXISTS memories (
    key        TEXT PRIMARY KEY,
    project    TEXT,
    body       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'native',  -- native | beads
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    memory_key UNINDEXED,
    body,
    tokenize = "unicode61 remove_diacritics 2 tokenchars '-_.'"
);

CREATE TABLE IF NOT EXISTS memory_embeddings (
    memory_key  TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    vec         BLOB NOT NULL,
    text_hash   TEXT NOT NULL,
    embedded_at TEXT NOT NULL
);

-- Индексируемые файлы ТЗ и их структурные части
CREATE TABLE IF NOT EXISTS documents (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'spec',
    path         TEXT NOT NULL,
    revision     INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    title        TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    status       TEXT NOT NULL DEFAULT 'ok',   -- ok | missing
    error        TEXT,                          -- текст последней ошибки чтения
    checked_at   TEXT,                          -- служебное: последняя попытка прочитать файл
    source       TEXT NOT NULL DEFAULT 'file',  -- file | upload (текст пришёл через API/MCP)
    content      TEXT,                          -- текст загруженного документа; у file всегда NULL
    UNIQUE(task_id, kind, path),
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_documents_task ON documents(task_id);
CREATE TABLE IF NOT EXISTS document_chunks (
    id           TEXT PRIMARY KEY,
    document_id  TEXT NOT NULL,
    ordinal      INTEGER NOT NULL,
    heading      TEXT,
    breadcrumb   TEXT,
    text         TEXT NOT NULL,
    start_line   INTEGER,
    end_line     INTEGER,
    token_count  INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT NOT NULL,
    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,
    UNIQUE(document_id, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id, ordinal);
CREATE VIRTUAL TABLE IF NOT EXISTS document_chunk_fts USING fts5(
    chunk_id UNINDEXED, document_id UNINDEXED, task_id UNINDEXED,
    heading, breadcrumb, body,
    tokenize = "unicode61 remove_diacritics 2 tokenchars '-_.'"
);
"""

# Колонки, добавленные после первой версии — для мягкой миграции существующей базы
MIGRATIONS: list[tuple[str, str, str]] = [
    ("projects", "kind", "TEXT NOT NULL DEFAULT 'beads'"),
    ("projects", "title", "TEXT"),
    ("projects", "color", "TEXT"),
    ("projects", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("projects", "imported_at", "TEXT"),
    ("projects", "import_note", "TEXT"),
    ("projects", "routing", "TEXT"),
    ("tasks", "result", "TEXT NOT NULL DEFAULT ''"),
    ("tasks", "holder", "TEXT"),
    ("tasks", "holder_at", "TEXT"),
    ("tasks", "holder_note", "TEXT"),
    ("tasks", "stage_at", "TEXT"),
    ("tasks", "needs_owner", "INTEGER NOT NULL DEFAULT 0"),
    ("tasks", "spec_path", "TEXT"),
    ("tasks", "checklist_path", "TEXT"),
    ("tasks", "review_path", "TEXT"),
    ("tasks", "decision_path", "TEXT"),
    ("tasks", "journal_path", "TEXT"),
    ("tasks", "worktree", "TEXT"),
    ("tasks", "branch", "TEXT"),
    ("tasks", "source", "TEXT NOT NULL DEFAULT 'beads'"),
    ("tasks", "archived", "INTEGER NOT NULL DEFAULT 0"),
    ("tasks", "blocked_by", "TEXT NOT NULL DEFAULT '[]'"),
    ("comments", "kind", "TEXT NOT NULL DEFAULT 'comment'"),
    ("deps", "created_by", "TEXT"),
    ("documents", "status", "TEXT NOT NULL DEFAULT 'ok'"),
    ("documents", "error", "TEXT"),
    ("documents", "checked_at", "TEXT"),
    ("documents", "source", "TEXT NOT NULL DEFAULT 'file'"),
    ("documents", "content", "TEXT"),
]


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or paths.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: сервер Listik многопоточный (ThreadingHTTPServer),
    # а доступ к базе сериализуется блокировкой вокруг соединения.
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.OperationalError:
        return set()


def migrate(conn: sqlite3.Connection) -> list[str]:
    applied: list[str] = []
    for table, column, decl in MIGRATIONS:
        cols = _existing_columns(conn, table)
        if not cols or column in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        applied.append(f"{table}.{column}")
    return applied


def init(db_path: Path | None = None, *, verbose: bool = False) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    applied = migrate(conn)
    if verbose and applied:
        print(f"миграции: {', '.join(applied)}")
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    seed_actors(conn)
    conn.commit()
    return conn


def seed_actors(conn: sqlite3.Connection) -> None:
    from . import actors as actors_mod
    for key, title in actors_mod.CANONICAL.items():
        kind = "agent" if key.startswith("agent:") else "human"
        hint = key.split(":", 1)[1] if ":" in key else "human"
        conn.execute(
            "INSERT INTO actors(key, title, kind, kind_hint) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET title=excluded.title, kind=excluded.kind",
            (key, title, kind, hint),
        )
    for raw, actor in actors_mod.ALIASES.items():
        conn.execute(
            "INSERT INTO actor_aliases(raw, actor) VALUES(?,?) "
            "ON CONFLICT(raw) DO UPDATE SET actor=excluded.actor",
            (raw, actor),
        )


def counts(conn: sqlite3.Connection) -> dict:
    out = {}
    for table in ("projects", "tasks", "comments", "deps", "events", "embeddings",
                  "memories", "actors"):
        try:
            out[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            out[table] = -1
    return out
