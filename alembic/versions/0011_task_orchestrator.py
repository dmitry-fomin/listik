"""Rename tasks.assignee to tasks.orchestrator (listik-6g0q).

``tasks.orchestrator`` is who drives the card along its route: ``listik`` (the
server or the swarm launched it) or ``claude`` (a Claude Code session runs the
pipeline). Only the system writes it. The column is renamed in place and its
values are carried over unchanged — ``me``, imported names, harness names and
NULL stay as they were; there is no ``UPDATE``. The index ``idx_tasks_assignee``
is replaced by ``idx_tasks_orchestrator``.

Online the upgrade runs only while ``tasks`` has ``assignee`` and no
``orchestrator``; in ``--sql`` mode it is emitted unconditionally. ``downgrade``
renames the column and swaps the index back.
"""
from __future__ import annotations

from alembic import op

revision = "0011_task_orchestrator"
down_revision = "0010_drop_direct_routes"
branch_labels = None
depends_on = None

UPGRADE = (
    "DROP INDEX IF EXISTS idx_tasks_assignee",
    "ALTER TABLE tasks RENAME COLUMN assignee TO orchestrator",
    "CREATE INDEX IF NOT EXISTS idx_tasks_orchestrator ON tasks(orchestrator)",
)
DOWNGRADE = (
    "DROP INDEX IF EXISTS idx_tasks_orchestrator",
    "ALTER TABLE tasks RENAME COLUMN orchestrator TO assignee",
    "CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee)",
)


def _columns() -> set[str]:
    bind = op.get_bind()
    return {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(tasks)").fetchall()}


def upgrade() -> None:
    if not op.get_context().as_sql:
        columns = _columns()
        if "assignee" not in columns or "orchestrator" in columns:
            return
    for sql in UPGRADE:
        op.execute(sql)


def downgrade() -> None:
    if not op.get_context().as_sql:
        columns = _columns()
        if "orchestrator" not in columns or "assignee" in columns:
            return
    for sql in DOWNGRADE:
        op.execute(sql)
