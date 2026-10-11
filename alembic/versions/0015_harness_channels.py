"""Add the harness channels column (listik-46q6).

``harnesses.channels`` is a JSON array of ``{name, model, effort, default}``
channels or NULL (no channels); the bridges (pi, opencode) read their models from
it. Seeds are not written here: they belong to ``listik.db.init``. Online the
upgrade runs only while ``harnesses`` has no ``channels`` column; in ``--sql`` mode
it is emitted unconditionally.
"""
from __future__ import annotations

from alembic import op

revision = "0015_harness_channels"
down_revision = "0014_pipeline_plugins"
branch_labels = None
depends_on = None

ADD_COLUMN = "ALTER TABLE harnesses ADD COLUMN channels TEXT"


def upgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        columns = {row[1] for row in
                   bind.exec_driver_sql("PRAGMA table_info(harnesses)").fetchall()}
        if "channels" in columns:
            return
    op.execute(ADD_COLUMN)


def downgrade() -> None:
    # SQLite cannot drop columns on all supported versions; the column is optional
    # and retaining it is safer than rebuilding a live table.
    pass
