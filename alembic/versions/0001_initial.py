"""Create the current Listik schema.

This baseline mirrors the schema used by ``listik.db``.  The regular Listik
startup path still calls ``db.init`` (including its soft migration fallback),
while Alembic can now create or upgrade a database explicitly.
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

# Frozen at revision creation time.  Future schema changes belong in a new
# revision rather than changing this baseline.
INITIAL_SCHEMA = '\n-- Проекты и рабочие области\nCREATE TABLE IF NOT EXISTS projects (\n    slug         TEXT PRIMARY KEY,       -- короткое имя: zoloto585-search, personal, ...\n    title        TEXT,\n    kind         TEXT NOT NULL DEFAULT \'native\',   -- native | beads\n    path         TEXT,                   -- путь к каталогу проекта (если есть)\n    git_remote   TEXT,\n    git_branch   TEXT,\n    color        TEXT,\n    archived     INTEGER NOT NULL DEFAULT 0,\n    imported_at  TEXT,\n    import_note  TEXT,\n    created_at   TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\'))\n);\n\n-- Задачи: сначала в Listik, потом в голове\nCREATE TABLE IF NOT EXISTS tasks (\n    id           TEXT PRIMARY KEY,       -- человекочитаемый ID: zoloto585-search-a1b2 или lk-000123\n    project      TEXT,\n    title        TEXT NOT NULL DEFAULT \'\',\n    description  TEXT NOT NULL DEFAULT \'\',\n    acceptance   TEXT NOT NULL DEFAULT \'\',\n    design       TEXT NOT NULL DEFAULT \'\',\n    notes        TEXT NOT NULL DEFAULT \'\',\n    result       TEXT NOT NULL DEFAULT \'\',  -- чем кончилось\n    status       TEXT NOT NULL DEFAULT \'open\',   -- open|in_progress|blocked|review|done|cancelled\n    stage        TEXT,                   -- s1-spec|s2-review|s3-impl|s4-judge|done (этап конвейера)\n    priority     INTEGER NOT NULL DEFAULT 2,\n    issue_type   TEXT NOT NULL DEFAULT \'task\',   -- task|bug|feature|epic|chore|decision|question\n    assignee     TEXT,                   -- actor_key: me | agent:claude | agent:dsh | ...\n    holder       TEXT,                   -- кто держит прямо сейчас (может отличаться от assignee)\n    holder_at    TEXT,                   -- heartbeat держащего\n    holder_note  TEXT,                   -- что именно он делает сейчас\n    stage_at     TEXT,                   -- когда вошёл в текущий этап\n    needs_owner  INTEGER NOT NULL DEFAULT 0,  -- 1 = ждёт человека\n    labels       TEXT NOT NULL DEFAULT \'[]\',\n    spec_path    TEXT,                   -- путь к ТЗ/спеке\n    checklist_path TEXT,                 -- путь к чек-листу приёмки\n    journal_path TEXT,                   -- путь к журналу конвейера\n    worktree     TEXT,                   -- рабочее дерево, если работа идёт в отдельном\n    branch       TEXT,\n    blocked_by   TEXT NOT NULL DEFAULT \'[]\',   -- JSON: незакрытые блокеры\n    source       TEXT NOT NULL DEFAULT \'native\',  -- native | beads\n    external_ref TEXT,                   -- старое ID в beads/github и т.п.\n    created_at   TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),\n    created_by   TEXT,\n    updated_at   TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),\n    started_at   TEXT,\n    closed_at    TEXT,\n    close_reason TEXT,\n    archived     INTEGER NOT NULL DEFAULT 0,\n    content_hash TEXT,\n    indexed_at   TEXT\n);\nCREATE INDEX IF NOT EXISTS idx_tasks_status   ON tasks(status);\nCREATE INDEX IF NOT EXISTS idx_tasks_stage    ON tasks(stage);\nCREATE INDEX IF NOT EXISTS idx_tasks_project  ON tasks(project);\nCREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);\nCREATE INDEX IF NOT EXISTS idx_tasks_holder   ON tasks(holder);\nCREATE INDEX IF NOT EXISTS idx_tasks_updated  ON tasks(updated_at DESC);\n\n-- Рёбра зависимостей\nCREATE TABLE IF NOT EXISTS deps (\n    issue_id   TEXT NOT NULL,\n    depends_on TEXT NOT NULL,\n    dep_type   TEXT NOT NULL DEFAULT \'blocks\',\n    created_at TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),\n    created_by TEXT,\n    PRIMARY KEY (issue_id, depends_on, dep_type)\n);\nCREATE INDEX IF NOT EXISTS idx_deps_depends_on ON deps(depends_on);\n\n-- Комментарии и журнал работы\nCREATE TABLE IF NOT EXISTS comments (\n    id         TEXT PRIMARY KEY,\n    task_id    TEXT NOT NULL,\n    author     TEXT,\n    kind       TEXT NOT NULL DEFAULT \'comment\',  -- comment|journal|question|answer|review|verdict\n    text       TEXT NOT NULL DEFAULT \'\',\n    created_at TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\'))\n);\nCREATE INDEX IF NOT EXISTS idx_comments_task ON comments(task_id, created_at);\n\n-- События: история переходов, из неё же считается «сколько времени на этапе»\nCREATE TABLE IF NOT EXISTS events (\n    id         INTEGER PRIMARY KEY AUTOINCREMENT,\n    task_id    TEXT NOT NULL,\n    ts         TEXT NOT NULL,\n    kind       TEXT NOT NULL,   -- created|stage|claim|release|heartbeat|note|question|answer|status|close|reopen|comment|import\n    from_value TEXT,\n    to_value   TEXT,\n    actor      TEXT,\n    harness    TEXT,\n    note       TEXT,\n    duration_s INTEGER          -- сколько секунд занял закрытый этап/отрезок\n);\nCREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id, ts DESC);\nCREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts DESC);\n\n-- Нормализация исполнителей\nCREATE TABLE IF NOT EXISTS actors (\n    key        TEXT PRIMARY KEY,   -- me | agent:claude | ...\n    title      TEXT,\n    kind       TEXT NOT NULL DEFAULT \'human\',   -- human|agent\n    kind_hint  TEXT,                             -- claude|dsh|grok|codex|gemini|human\n    created_at TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\'))\n);\n\nCREATE TABLE IF NOT EXISTS actor_aliases (\n    raw   TEXT PRIMARY KEY,\n    actor TEXT NOT NULL\n);\n\n-- Полнотекстовый поиск (BM25)\nCREATE VIRTUAL TABLE IF NOT EXISTS task_fts USING fts5(\n    task_id UNINDEXED,\n    title,\n    body,\n    labels,\n    tokenize = "unicode61 remove_diacritics 2 tokenchars \'-_.\'"\n);\n\nCREATE VIRTUAL TABLE IF NOT EXISTS comment_fts USING fts5(\n    comment_id UNINDEXED,\n    task_id UNINDEXED,\n    body,\n    tokenize = "unicode61 remove_diacritics 2 tokenchars \'-_.\'"\n);\n\n-- Векторы\nCREATE TABLE IF NOT EXISTS embeddings (\n    doc_id      TEXT PRIMARY KEY,     -- task_id или comment_id\n    doc_kind    TEXT NOT NULL,        -- task|comment\n    task_id     TEXT NOT NULL,\n    project     TEXT,\n    model       TEXT NOT NULL,\n    dim         INTEGER NOT NULL,\n    vec         BLOB NOT NULL,\n    text_hash   TEXT NOT NULL,\n    embedded_at TEXT NOT NULL\n);\nCREATE INDEX IF NOT EXISTS idx_embeddings_kind ON embeddings(doc_kind);\n\nCREATE TABLE IF NOT EXISTS meta (\n    key   TEXT PRIMARY KEY,\n    value TEXT\n);\n\n-- Долговременная память: заметки, не привязанные к задаче (аналог bd remember)\nCREATE TABLE IF NOT EXISTS memories (\n    key        TEXT PRIMARY KEY,\n    project    TEXT,\n    body       TEXT NOT NULL,\n    source     TEXT NOT NULL DEFAULT \'native\',  -- native | beads\n    created_at TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),\n    updated_at TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\'))\n);\n\nCREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(\n    memory_key UNINDEXED,\n    body,\n    tokenize = "unicode61 remove_diacritics 2 tokenchars \'-_.\'"\n);\n\nCREATE TABLE IF NOT EXISTS memory_embeddings (\n    memory_key  TEXT PRIMARY KEY,\n    model       TEXT NOT NULL,\n    dim         INTEGER NOT NULL,\n    vec         BLOB NOT NULL,\n    text_hash   TEXT NOT NULL,\n    embedded_at TEXT NOT NULL\n);\n\n-- Индексируемые файлы ТЗ и их структурные части\nCREATE TABLE IF NOT EXISTS documents (\n    id           TEXT PRIMARY KEY,\n    task_id      TEXT NOT NULL,\n    kind         TEXT NOT NULL DEFAULT \'spec\',\n    path         TEXT NOT NULL,\n    revision     INTEGER NOT NULL DEFAULT 1,\n    content_hash TEXT NOT NULL,\n    title        TEXT,\n    created_at   TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),\n    updated_at   TEXT NOT NULL DEFAULT (strftime(\'%Y-%m-%dT%H:%M:%SZ\',\'now\')),\n    UNIQUE(task_id, kind, path),\n    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE\n);\nCREATE INDEX IF NOT EXISTS idx_documents_task ON documents(task_id);\nCREATE TABLE IF NOT EXISTS document_chunks (\n    id           TEXT PRIMARY KEY,\n    document_id  TEXT NOT NULL,\n    ordinal      INTEGER NOT NULL,\n    heading      TEXT,\n    breadcrumb   TEXT,\n    text         TEXT NOT NULL,\n    start_line   INTEGER,\n    end_line     INTEGER,\n    token_count  INTEGER NOT NULL DEFAULT 0,\n    content_hash TEXT NOT NULL,\n    FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE,\n    UNIQUE(document_id, ordinal)\n);\nCREATE INDEX IF NOT EXISTS idx_document_chunks_document ON document_chunks(document_id, ordinal);\nCREATE VIRTUAL TABLE IF NOT EXISTS document_chunk_fts USING fts5(\n    chunk_id UNINDEXED, document_id UNINDEXED, task_id UNINDEXED,\n    heading, breadcrumb, body,\n    tokenize = "unicode61 remove_diacritics 2 tokenchars \'-_.\'"\n);\n'
LEGACY_COLUMNS = [('projects', 'kind', "TEXT NOT NULL DEFAULT 'beads'"), ('projects', 'title', 'TEXT'), ('projects', 'color', 'TEXT'), ('projects', 'archived', 'INTEGER NOT NULL DEFAULT 0'), ('projects', 'imported_at', 'TEXT'), ('projects', 'import_note', 'TEXT'), ('tasks', 'result', "TEXT NOT NULL DEFAULT ''"), ('tasks', 'holder', 'TEXT'), ('tasks', 'holder_at', 'TEXT'), ('tasks', 'holder_note', 'TEXT'), ('tasks', 'stage_at', 'TEXT'), ('tasks', 'needs_owner', 'INTEGER NOT NULL DEFAULT 0'), ('tasks', 'spec_path', 'TEXT'), ('tasks', 'checklist_path', 'TEXT'), ('tasks', 'journal_path', 'TEXT'), ('tasks', 'worktree', 'TEXT'), ('tasks', 'branch', 'TEXT'), ('tasks', 'source', "TEXT NOT NULL DEFAULT 'beads'"), ('tasks', 'archived', 'INTEGER NOT NULL DEFAULT 0'), ('tasks', 'blocked_by', "TEXT NOT NULL DEFAULT '[]'"), ('comments', 'kind', "TEXT NOT NULL DEFAULT 'comment'"), ('deps', 'created_by', 'TEXT')]
DROP_TABLES = ['document_chunk_fts', 'memory_fts', 'comment_fts', 'task_fts', 'memory_embeddings', 'document_chunks', 'documents', 'embeddings', 'memories', 'meta', 'actor_aliases', 'actors', 'events', 'comments', 'deps', 'tasks', 'projects']


def _statements(script: str) -> Iterable[str]:
    """Yield complete SQLite statements without splitting quoted semicolons."""
    buffer: list[str] = []
    for line in script.splitlines():
        buffer.append(line)
        text = "\n".join(buffer)
        if sqlite3.complete_statement(text):
            if text.strip().rstrip(";").strip():
                yield text
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        yield tail


def _apply_legacy_columns(bind) -> None:
    # Existing databases may predate one or more columns.  Keep this compatible
    # with db.init's non-destructive fallback while allowing ``upgrade`` to run
    # against a database that already contains tasks.
    # Offline SQL generation has no database to inspect.  The baseline DDL
    # already contains every current column, so there is nothing to add for a
    # new database; callers upgrading an old database should run online.
    if op.get_context().as_sql:
        return
    for table, column, declaration in LEGACY_COLUMNS:
        rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        if rows and column not in {row[1] for row in rows}:
            bind.exec_driver_sql(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )


def upgrade() -> None:
    bind = op.get_bind()
    # Add columns to pre-Alembic tables first; the baseline then creates
    # indexes that reference them.  On a fresh database this is a no-op.
    _apply_legacy_columns(bind)
    for statement in _statements(INITIAL_SCHEMA):
        if op.get_context().as_sql:
            op.execute(statement)
        else:
            bind.exec_driver_sql(statement)


def downgrade() -> None:
    bind = op.get_bind()
    # Drop virtual FTS tables before their source tables and then unwind the
    # foreign-key graph from chunks/tasks back to projects.
    for table in DROP_TABLES:
        statement = f"DROP TABLE IF EXISTS {table}"
        if op.get_context().as_sql:
            op.execute(statement)
        else:
            bind.exec_driver_sql(statement)
