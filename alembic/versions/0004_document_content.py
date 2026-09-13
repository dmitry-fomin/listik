"""Add document source and uploaded content columns.

Documents indexed from a file (``source='file'``) keep their text on disk and
``content`` stays NULL.  Documents uploaded through the API/MCP (``source='upload'``)
carry their text in ``content``: the server has no project repositories, so a file
path there would only ever be missing.
"""
from __future__ import annotations

from alembic import op

revision = "0004_document_content"
down_revision = "0003_document_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_context().as_sql:
        op.execute("ALTER TABLE documents ADD COLUMN source TEXT NOT NULL DEFAULT 'file'")
        op.execute("ALTER TABLE documents ADD COLUMN content TEXT")
        return
    bind = op.get_bind()
    columns = {row[1] for row in bind.exec_driver_sql("PRAGMA table_info(documents)").fetchall()}
    if "source" not in columns:
        bind.exec_driver_sql("ALTER TABLE documents ADD COLUMN source TEXT NOT NULL DEFAULT 'file'")
    if "content" not in columns:
        bind.exec_driver_sql("ALTER TABLE documents ADD COLUMN content TEXT")


def downgrade() -> None:
    # SQLite cannot drop columns on all supported versions; the columns are optional
    # and retaining them is safer than rebuilding a live table.
    pass
