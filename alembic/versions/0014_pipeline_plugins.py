"""Pipeline preset plugins: ``routes.plugin`` and new pipeline route keys (listik-d9rj).

Pipeline presets moved from ``feature-pipeline``/``claude-codex`` into three plugins
(``pipeline-full``, ``pipeline-cc``, ``pipeline-claude``). A pipeline route now names
its plugin explicitly in ``routes.plugin`` and its key is ``<plugin without
"pipeline-">-<skill>``. The upgrade adds the column and, for every ``(old, new,
plugin)`` in ``PIPELINE_ROUTE_RENAMES`` (the same table as ``listik.db``, copied here
on purpose — revisions don't import ``listik``):

* the ``routes`` row ``old`` (``kind='pipeline'``, only while ``new`` is free) gets
  ``key = new``, ``plugin``, ``updated_at`` now, and in ``command`` the skill reference
  ``/feature-pipeline:{route}``, ``/feature-pipeline:<old>`` and (``cc-*``)
  ``/claude-codex:<old without "cc-">`` becomes ``/{plugin}:{skill}``;
* only if that row was renamed (``new`` has ``plugin`` set — the column is brand new):
  ``tasks.launch_route``, the ``"process:<old>"`` label and the ``task_fts`` labels.

Swarm routes and the author's own pipelines keep ``plugin = NULL``. No row is deleted,
no event written, ``tasks.updated_at`` untouched. Same text as
``listik.db.pipeline_rename_sql``.

Online the upgrade runs only while ``routes.plugin`` is missing; in ``--sql`` mode it
is emitted unconditionally (as in 0013). ``downgrade`` reverts keys, ``process:``
labels (with ``task_fts``), ``launch_route`` and the skill reference in ``command``,
then drops ``routes.plugin``.

Colons inside string literals are escaped (``\\:``): a plain string passed to
``op.execute`` becomes ``text()``, where ``:name`` is a bind parameter.
"""
from __future__ import annotations

from alembic import op

revision = "0014_pipeline_plugins"
down_revision = "0013_drop_route_driver"
branch_labels = None
depends_on = None

PIPELINE_ROUTE_RENAMES = (
    # old key, new key, plugin
    ("xhigh-pipeline", "full-xhigh", "pipeline-full"),
    ("high-pipeline", "full-high", "pipeline-full"),
    ("medium-pipeline", "full-medium", "pipeline-full"),
    ("low-pipeline", "full-low", "pipeline-full"),
    ("xlow-pipeline", "full-xlow", "pipeline-full"),
    ("nano-pipeline", "full-nano", "pipeline-full"),
    ("cross-pipeline", "full-cross", "pipeline-full"),
    ("sol-pipeline", "cc-sol", "pipeline-cc"),
    ("cc-xhigh-pipeline", "cc-xhigh", "pipeline-cc"),
    ("cc-high-pipeline", "cc-high", "pipeline-cc"),
    ("cc-medium-pipeline", "cc-medium", "pipeline-cc"),
    ("cc-low-pipeline", "cc-low", "pipeline-cc"),
    ("cc-xlow-pipeline", "cc-xlow", "pipeline-cc"),
    ("cc-nano-pipeline", "cc-nano", "pipeline-cc"),
    ("opus-pipeline", "claude-opus", "pipeline-claude"),
    ("claude-pipeline", "claude-high", "pipeline-claude"),
)
SKILL_PLACEHOLDER = "/{plugin}:{skill}"


def _labels_sql(old: str, new: str, guard: str) -> tuple[str, str]:
    """Label ``"process:<old>"`` → ``"process:<new>"`` under ``guard``, then ``task_fts``."""
    return (
        f"UPDATE tasks SET labels = REPLACE(labels, '\"process:{old}\"', '\"process:{new}\"') "
        f"WHERE instr(labels, '\"process:{old}\"') > 0 AND {guard}",
        "UPDATE task_fts SET labels = (SELECT labels FROM tasks WHERE tasks.id = task_fts.task_id) "
        f"WHERE task_id IN (SELECT id FROM tasks WHERE instr(labels, '\"process:{new}\"') > 0)",
    )


def upgrade_sql(old: str, new: str, plugin: str) -> tuple[str, ...]:
    command = (f"REPLACE(REPLACE(command, '/feature-pipeline:{{route}}', '{SKILL_PLACEHOLDER}'), "
               f"'/feature-pipeline:{old}', '{SKILL_PLACEHOLDER}')")
    if old.startswith("cc-"):
        command = f"REPLACE({command}, '/claude-codex:{old[3:]}', '{SKILL_PLACEHOLDER}')"
    renamed = f"EXISTS (SELECT 1 FROM routes WHERE key = '{new}' AND plugin = '{plugin}')"
    return (
        f"UPDATE routes SET key = '{new}', plugin = '{plugin}', "
        f"updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), command = {command} "
        f"WHERE key = '{old}' AND kind = 'pipeline' "
        f"AND NOT EXISTS (SELECT 1 FROM routes WHERE key = '{new}')",
        f"UPDATE tasks SET launch_route = '{new}' WHERE launch_route = '{old}' AND {renamed}",
        *_labels_sql(old, new, renamed),
    )


def downgrade_sql(old: str, new: str, plugin: str) -> tuple[str, ...]:
    if old.startswith("cc-"):
        ref = f"/claude-codex:{old[3:]}"
    else:
        ref = "/feature-pipeline:{route}"
    # `plugin` is still there: the row `old` is ours exactly when it carries the plugin.
    reverted = f"EXISTS (SELECT 1 FROM routes WHERE key = '{old}' AND plugin = '{plugin}')"
    return (
        f"UPDATE routes SET key = '{old}', "
        f"command = REPLACE(command, '{SKILL_PLACEHOLDER}', '{ref}') "
        f"WHERE key = '{new}' AND kind = 'pipeline' AND plugin = '{plugin}' "
        f"AND NOT EXISTS (SELECT 1 FROM routes WHERE key = '{old}')",
        f"UPDATE tasks SET launch_route = '{old}' WHERE launch_route = '{new}' AND {reverted}",
        *_labels_sql(new, old, reverted),
    )


def _execute(sql: str) -> None:
    op.execute(sql.replace(":", "\\:"))


def _has_plugin() -> bool:
    bind = op.get_bind()
    columns = {row[1] for row in
               bind.exec_driver_sql("PRAGMA table_info(routes)").fetchall()}
    return "plugin" in columns


def upgrade() -> None:
    if not op.get_context().as_sql and _has_plugin():
        return
    op.execute("ALTER TABLE routes ADD COLUMN plugin TEXT")
    for old, new, plugin in PIPELINE_ROUTE_RENAMES:
        for sql in upgrade_sql(old, new, plugin):
            _execute(sql)


def downgrade() -> None:
    if not op.get_context().as_sql and not _has_plugin():
        return
    for old, new, plugin in PIPELINE_ROUTE_RENAMES:
        for sql in downgrade_sql(old, new, plugin):
            _execute(sql)
    op.execute("ALTER TABLE routes DROP COLUMN plugin")
