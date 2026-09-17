"""Add the ``routes`` table (step 09, portion b).

Route presets move out of ``routes.json`` into the database.  This revision only
creates the storage: readers (``routes.current``, the API, the launcher) still
read the file until portion b switches them over.  The initial import of the
file is done by ``listik/routes_store.py`` (``import_file``/``ensure_imported``).

``command`` and ``roles`` hold JSON text (argv array and
``{"spec": {provider, label, title}, …}`` respectively); ``visible`` is 0/1.
"""
from __future__ import annotations

from alembic import op

revision = "0007_routes"
down_revision = "0006_task_owner"
branch_labels = None
depends_on = None

CREATE_ROUTES = """
CREATE TABLE IF NOT EXISTS routes (
    key        TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    title      TEXT NOT NULL,
    hint       TEXT NOT NULL DEFAULT '',
    icon       TEXT,
    visible    INTEGER NOT NULL DEFAULT 1,
    position   INTEGER NOT NULL DEFAULT 0,
    harness    TEXT,
    command    TEXT,
    roles      TEXT,
    created_at TEXT,
    updated_at TEXT
)
"""
CREATE_POSITION_INDEX = "CREATE INDEX IF NOT EXISTS idx_routes_position ON routes(position)"


def upgrade() -> None:
    op.execute(CREATE_ROUTES)
    op.execute(CREATE_POSITION_INDEX)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_routes_position")
    op.execute("DROP TABLE IF EXISTS routes")
