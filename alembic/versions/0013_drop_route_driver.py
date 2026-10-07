"""Drop the routes.driver column (listik-ujra).

How a route runs is derived from its ``kind`` alone: ``pipeline`` runs as a
skill, ``swarm`` runs in the swarm. The forbidden combination ``kind='pipeline'``
with ``driver='swarm'`` and a non-empty ``roles`` (its cells are already swarm
cells) becomes a swarm route with the same key; with empty roles (``NULL``,
``''``, ``'{}'``) it stays a pipeline — a swarm route without roles is invalid.
Then ``routes.driver`` is dropped. ``tasks.launch_driver`` is untouched.

Online the upgrade runs only while ``routes.driver`` still exists; in ``--sql``
mode it is emitted unconditionally. ``downgrade`` adds back
``driver TEXT NOT NULL DEFAULT 'skill'`` (online only when it is missing) and
sets it to ``'swarm'`` for swarm routes.
"""
from __future__ import annotations

from alembic import op

revision = "0013_drop_route_driver"
down_revision = "0012_event_transition"
branch_labels = None
depends_on = None

STATEMENTS = (
    "UPDATE routes SET kind = 'swarm' WHERE kind = 'pipeline' AND driver = 'swarm' "
    "AND COALESCE(roles, '') NOT IN ('', '{}')",
    "ALTER TABLE routes DROP COLUMN driver",
)


def _has_driver() -> bool:
    bind = op.get_bind()
    columns = {row[1] for row in
               bind.exec_driver_sql("PRAGMA table_info(routes)").fetchall()}
    return "driver" in columns


def upgrade() -> None:
    if not op.get_context().as_sql and not _has_driver():
        return
    for sql in STATEMENTS:
        op.execute(sql)


def downgrade() -> None:
    if not op.get_context().as_sql and _has_driver():
        return
    op.execute("ALTER TABLE routes ADD COLUMN driver TEXT NOT NULL DEFAULT 'skill'")
    op.execute("UPDATE routes SET driver = 'swarm' WHERE kind = 'swarm'")
