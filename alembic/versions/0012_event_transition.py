"""Add the stage-event transition column (listik-cvm8).

``events.transition`` is the pipeline transition kind a ``stage`` event applied:
``sticky``, ``handoff`` or ``sticky-return``; NULL when the event is not a
pipeline transition (closing into ``done``, creating a task on a stage, editing
``stage`` directly). Only ``listik/store.next_stage`` writes it, whatever the
event note says.

The column is added and older history is backfilled from the default note
``этап -> <to_value> (<kind>)`` that ``next_stage`` used to write; transitions
into ``done`` stay NULL. Online the upgrade runs only while ``events`` has no
``transition`` column; in ``--sql`` mode it is emitted unconditionally.
"""
from __future__ import annotations

from alembic import op

revision = "0012_event_transition"
down_revision = "0011_task_orchestrator"
branch_labels = None
depends_on = None

ADD_COLUMN = "ALTER TABLE events ADD COLUMN transition TEXT"
# Same text as `listik.db.BACKFILL_TRANSITION_SQL`.
BACKFILL_TRANSITION_SQL = tuple(
    f"UPDATE events SET transition = '{kind}' "
    "WHERE kind = 'stage' AND transition IS NULL AND to_value <> 'done' "
    f"AND note = 'этап -> ' || to_value || ' ({kind})'"
    for kind in ("sticky", "handoff", "sticky-return")
)


def upgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        columns = {row[1] for row in
                   bind.exec_driver_sql("PRAGMA table_info(events)").fetchall()}
        if "transition" in columns:
            return
    op.execute(ADD_COLUMN)
    for sql in BACKFILL_TRANSITION_SQL:
        op.execute(sql)


def downgrade() -> None:
    # SQLite cannot drop columns on all supported versions; the column is optional
    # and retaining it is safer than rebuilding a live table.
    pass
