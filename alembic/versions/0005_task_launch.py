"""Add task autostart and launch tracking columns.

``autostart`` is the checkbox set when the task is created; the remaining columns
describe the process Listik started for that task (``launched_by``/``launch_pid``/
``launched_at``/``launch_log``), how it ended (``launch_exit_code``/
``launch_finished_at``) and why it was not started at all (``launch_error``).
Only ``listik/store.create_task`` and ``listik/launcher.py`` write them; they are
deliberately absent from ``PATCH /api/tasks/{id}``.
"""
from __future__ import annotations

from alembic import op

revision = "0005_task_launch"
down_revision = "0004_document_content"
branch_labels = None
depends_on = None

COLUMNS = (
    ("autostart", "INTEGER NOT NULL DEFAULT 0"),
    ("launch_route", "TEXT"),
    ("launched_by", "TEXT"),
    ("launch_pid", "INTEGER"),
    ("launched_at", "TEXT"),
    ("launch_log", "TEXT"),
    ("launch_exit_code", "INTEGER"),
    ("launch_finished_at", "TEXT"),
    ("launch_error", "TEXT"),
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
