"""Add task scope-fencing columns (swarm dispatch).

``tasks.read_scope``/``tasks.write_scope`` are JSON lists of relative paths a task
reads/writes; ``write_scope`` is what the swarm scheduler uses to split tasks into
non-conflicting waves. ``tasks.dispatch_id``/``tasks.generation`` identify a worker
launch — ``dispatch_id`` is a unique run id (NULL until the first launch),
``generation`` is a monotonically increasing launch counter. Only the launcher
(swarm-3) writes ``dispatch_id``/``generation``; they never change through
``update_task``/``PATCH``. ``read_scope``/``write_scope`` are storage-only in this
revision (listik-s520 slice a) — write support lands in a later slice. No index:
the columns are small and read together with the rest of the row.
"""
from __future__ import annotations

from alembic import op

revision = "0008_task_scope_fencing"
down_revision = "0007_routes"
branch_labels = None
depends_on = None

COLUMNS = (
    ("read_scope", "TEXT NOT NULL DEFAULT '[]'"),
    ("write_scope", "TEXT NOT NULL DEFAULT '[]'"),
    ("dispatch_id", "TEXT"),
    ("generation", "INTEGER NOT NULL DEFAULT 0"),
)


def upgrade() -> None:
    if op.get_context().as_sql:
        for name, decl in COLUMNS:
            op.execute(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")
        return
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(tasks)").fetchall()}
    for name, decl in COLUMNS:
        if name not in columns:
            bind.exec_driver_sql(f"ALTER TABLE tasks ADD COLUMN {name} {decl}")


def downgrade() -> None:
    # SQLite cannot drop columns on all supported versions; the columns are optional
    # and retaining them is safer than rebuilding a live table.
    pass
