"""Drop the direct route kind and the routes.harness column (listik-ar8v).

Every ``kind='direct'`` route becomes a swarm route with a single ``impl`` role,
keeping its key: the role's harness is the route's ``harness``, its argv is
``command`` without the last element (the direct hand-off prompt is dropped on
purpose — in the swarm Listik itself does claim/stage/done). A harness missing
from the ``harnesses`` catalogue gets a new ``exec`` record with that argv.
Then ``routes.harness`` is dropped. ``tasks.launch_route`` is untouched: it keeps
pointing at the same key, now a swarm route.

Online the upgrade runs only while ``routes.harness`` still exists; in ``--sql``
mode it is emitted unconditionally. ``downgrade`` only adds back an empty
``harness TEXT`` column — the direct routes and their harness values are not
restored.
"""
from __future__ import annotations

from alembic import op

revision = "0010_drop_direct_routes"
down_revision = "0009_harnesses_swarm"
branch_labels = None
depends_on = None

_ARGV = ("CASE WHEN json_valid(command) THEN CASE WHEN json_array_length(command) >= 2 "
         "THEN json_remove(command, '$[#-1]') END END")
STATEMENTS = (
    "INSERT INTO harnesses(key, label, hint, icon, argv, prompt, kind, builtin, enabled, "
    "position, created_at, updated_at) "
    f"SELECT harness, harness, '', NULL, {_ARGV}, NULL, 'exec', 0, 1, "
    "(SELECT COALESCE(MAX(position), -1) FROM harnesses) "
    "+ ROW_NUMBER() OVER (ORDER BY position, key), "
    "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now') "
    "FROM routes WHERE kind = 'direct' AND harness IS NOT NULL "
    "AND harness NOT IN (SELECT key FROM harnesses) "
    "ORDER BY position, key "
    "ON CONFLICT(key) DO NOTHING",
    "UPDATE routes SET roles = json_object('impl', json_object('harness', harness)) "
    "WHERE kind = 'direct'",
    f"UPDATE routes SET roles = json_set(roles, '$.impl.argv', {_ARGV}) "
    f"WHERE kind = 'direct' AND ({_ARGV}) IS NOT NULL",
    "UPDATE routes SET kind = 'swarm', driver = 'swarm', command = NULL "
    "WHERE kind = 'direct'",
    "ALTER TABLE routes DROP COLUMN harness",
)


def upgrade() -> None:
    if not op.get_context().as_sql:
        bind = op.get_bind()
        columns = {row[1] for row in
                   bind.exec_driver_sql("PRAGMA table_info(routes)").fetchall()}
        if "harness" not in columns:
            return
    for sql in STATEMENTS:
        op.execute(sql)


def downgrade() -> None:
    op.execute("ALTER TABLE routes ADD COLUMN harness TEXT")
