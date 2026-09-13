"""Add document status/error/checked_at columns.

Tracks whether the last read of an indexed file succeeded (``status='ok'``) or
failed (``status='missing'``), the last error text, and when it was last checked.
"""
from __future__ import annotations

from alembic import op

revision = "0003_document_status"
down_revision = "0002_document_paths"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        op.execute("ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'")
        op.execute("ALTER TABLE documents ADD COLUMN error TEXT")
        op.execute("ALTER TABLE documents ADD COLUMN checked_at TEXT")
        return
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(documents)").fetchall()}
    if "status" not in columns:
        bind.exec_driver_sql("ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'ok'")
    if "error" not in columns:
        bind.exec_driver_sql("ALTER TABLE documents ADD COLUMN error TEXT")
    if "checked_at" not in columns:
        bind.exec_driver_sql("ALTER TABLE documents ADD COLUMN checked_at TEXT")


def downgrade() -> None:
    # SQLite cannot drop columns on all supported versions; the columns are optional
    # and retaining them is safer than rebuilding a live table.
    pass
