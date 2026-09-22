"""Harness catalog, route driver, task launch_driver (listik-2gry).

``harnesses`` — the harness catalogue: who can execute a task or a swarm-stage
role and which argv/prompt launches it by default.  ``routes.driver`` picks the
execution mode of a route — ``skill`` (the one-process pipeline launch) or
``swarm`` (per-stage launch driven by ``listik/stage_launch.py``); ``kind`` now
also allows ``swarm``.  ``tasks.launch_driver`` is a snapshot of that mode taken
by the launcher at the first launch — editing ``routes.driver`` afterwards does
not retarget a run in flight; NULL means the task was never launched (a direct
route leaves it NULL, there is no mode to snapshot).
"""
from __future__ import annotations

from alembic import op

revision = "0009_harnesses_swarm"
down_revision = "0008_task_scope_fencing"
branch_labels = None
depends_on = None

COLUMNS = (
    ("tasks", "launch_driver", "TEXT"),
    ("routes", "driver", "TEXT NOT NULL DEFAULT 'skill'"),
)

CREATE_HARNESSES = """
CREATE TABLE IF NOT EXISTS harnesses (
    key        TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    hint       TEXT NOT NULL DEFAULT '',
    icon       TEXT,
    argv       TEXT,
    prompt     TEXT,
    kind       TEXT NOT NULL DEFAULT 'exec',
    builtin    INTEGER NOT NULL DEFAULT 0,
    enabled    INTEGER NOT NULL DEFAULT 1,
    position   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
)
"""
CREATE_POSITION_INDEX = ("CREATE INDEX IF NOT EXISTS idx_harnesses_position "
                         "ON harnesses(position)")


def upgrade() -> None:
    op.execute(CREATE_HARNESSES)
    op.execute(CREATE_POSITION_INDEX)
    if op.get_context().as_sql:
        for table, name, decl in COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        return
    bind = op.get_bind()
    for table, name, decl in COLUMNS:
        columns = {row[1] for row in
                   bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()}
        if name not in columns:
            bind.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_harnesses_position")
    op.execute("DROP TABLE IF EXISTS harnesses")
    # SQLite до 3.35 не умеет DROP COLUMN: колонки launch_driver/driver
    # при откате остаются — безвредны (NOT NULL у driver с дефолтом).
