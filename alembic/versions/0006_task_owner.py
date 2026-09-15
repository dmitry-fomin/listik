"""Add the task owner column (server mode).

``tasks.owner`` is the human who owns the card in server mode — a name from the
closed ``[server] users`` list in ``config.toml``. It is unrelated to ``assignee``
and ``holder``, which describe the agent working the task. Only
``listik/store.create_task`` and ``listik/store.update_task`` write it; in local
mode the column stays NULL. No index: the column is tiny and the board filters it
together with the rest of the row.
"""
from __future__ import annotations

from alembic import op

revision = "0006_task_owner"
down_revision = "0005_task_launch"
branch_labels = None
depends_on = None

COLUMNS = (
    ("owner", "TEXT"),
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
