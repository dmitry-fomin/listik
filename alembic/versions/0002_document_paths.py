"""Add explicit review and decision document paths.

The original schema exposed ``journal_path`` only.  Keep it as a compatibility
alias while allowing context to index review and decision files separately.
"""
from __future__ import annotations

from alembic import op

revision = "0002_document_paths"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        op.execute("ALTER TABLE tasks ADD COLUMN review_path TEXT")
        op.execute("ALTER TABLE tasks ADD COLUMN decision_path TEXT")
        return
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(tasks)").fetchall()}
    if "review_path" not in columns:
        bind.exec_driver_sql("ALTER TABLE tasks ADD COLUMN review_path TEXT")
    if "decision_path" not in columns:
        bind.exec_driver_sql("ALTER TABLE tasks ADD COLUMN decision_path TEXT")


def downgrade() -> None:
    # SQLite cannot drop columns on all supported versions; paths are optional and
    # retaining them is safer than rebuilding a live task table.
    pass
