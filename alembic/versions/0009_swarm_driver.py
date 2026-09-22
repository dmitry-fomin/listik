"""Route driver and the per-card launch_driver snapshot (listik-09c5).

``routes.driver`` is ``skill`` (one Claude process, the previous behaviour) or
``swarm`` (Listik launches each pipeline role itself). Existing rows default to
``skill``. ``tasks.launch_driver`` is copied from the route on the first capture
and is not writable through ``update_task``. Direct routes keep ``skill`` in the
column and do not expose it.
"""
from __future__ import annotations

from alembic import op

revision = "0009_swarm_driver"
down_revision = "0008_task_scope_fencing"
branch_labels = None
depends_on = None

COLUMNS = (
    ("routes", "driver", "TEXT NOT NULL DEFAULT 'skill'"),
    ("tasks", "launch_driver", "TEXT"),
)


def upgrade() -> None:
    if op.get_context().as_sql:
        for table, name, decl in COLUMNS:
            op.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        return
    bind = op.get_bind()
    for table, name, decl in COLUMNS:
        columns = {row[1] for row in bind.exec_driver_sql(
            f"PRAGMA table_info({table})").fetchall()}
        if name not in columns:
            bind.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def downgrade() -> None:
    pass
