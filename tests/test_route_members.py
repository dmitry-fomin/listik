"""`members` ролей конвейера: ввоз, `used_by` и разовое заполнение (listik-46q6, порция c)."""
from __future__ import annotations

import contextlib
import io
import json
from unittest import mock

from listik import harnesses_store
from listik import routes as routes_mod
from listik import routes_store
from tests.helpers import TempDbTestCase

OLD = "2000-01-01T00:00:00Z"


def drop_pi_channel(conn, name: str) -> None:
    channels = [ch for ch in harnesses_store.get(conn, "pi")["channels"] if ch["name"] != name]
    harnesses_store.update(conn, "pi", {"channels": channels})


def snapshot(conn) -> list[tuple]:
    return [tuple(row) for row in conn.execute(
        "SELECT key, position, roles, updated_at FROM routes ORDER BY key")]


class ReimportMembersTests(TempDbTestCase):
    def test_reimport_with_stale_channel_rolls_back(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.import_file(self.conn)
        before = snapshot(self.conn)
        drop_pi_channel(self.conn, "glm")
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_store.reimport(self.conn)
        self.assertIn("members", str(ctx.exception))
        self.assertEqual(snapshot(self.conn), before)


class UsedByPipelineTests(TempDbTestCase):
    def test_order_and_shape(self) -> None:
        def cell(*channels):
            return {"provider": "claude", "label": "x", "title": "x",
                    "members": [{"harness": "pi", "channel": ch} for ch in channels]}

        routes_store.upsert_route(self.conn, {
            "key": "full-b", "kind": "pipeline", "plugin": "pipeline-full", "title": "b",
            "visible": True, "roles": {"critic": cell("deepseek", "glm"), "judge": cell("glm")},
        }, position=1)
        routes_store.upsert_route(self.conn, {
            "key": "full-a", "kind": "pipeline", "plugin": "pipeline-full", "title": "a",
            "visible": True, "roles": {"critic": cell("glm")},
        }, position=5)
        routes_store.upsert_route(self.conn, {
            "key": "swarm-pi", "kind": "swarm", "title": "s", "visible": True,
            "roles": {"impl": {"harness": "pi", "argv": ["pi"]}},
        }, position=3)
        self.assertEqual(harnesses_store.used_by(self.conn, "pi"), [
            {"route": "full-b", "kind": "pipeline", "role": "critic", "channel": "deepseek"},
            {"route": "full-b", "kind": "pipeline", "role": "critic", "channel": "glm"},
            {"route": "full-b", "kind": "pipeline", "role": "judge", "channel": "glm"},
            {"route": "swarm-pi", "kind": "swarm", "role": "impl"},
            {"route": "full-a", "kind": "pipeline", "role": "critic", "channel": "glm"},
        ])


class FillMembersTests(TempDbTestCase):
    def insert(self, key: str, roles, *, kind="pipeline", plugin="pipeline-full",
               position=0) -> None:
        text = roles if isinstance(roles, str) else json.dumps(roles, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO routes(key, kind, plugin, title, hint, icon, visible, position, "
            "command, roles, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (key, kind, plugin, key, "", None, 1, position, None, text, OLD, OLD))
        self.conn.commit()

    def roles(self, key: str) -> dict:
        return json.loads(self.conn.execute(
            "SELECT roles FROM routes WHERE key = ?", (key,)).fetchone()[0])

    def updated(self, key: str) -> str:
        return self.conn.execute(
            "SELECT updated_at FROM routes WHERE key = ?", (key,)).fetchone()[0]

    def flag(self):
        return self.conn.execute(
            "SELECT value FROM meta WHERE key = 'route_members_v1'").fetchone()

    def seed_rows(self) -> None:
        self.insert("full-medium", {
            "critic": {"provider": "claude", "label": "S+DS",
                       "title": "Sonnet 5.5 · high + DeepSeek Flash · high"},
            "impl": {"provider": "devin", "label": "max", "title": "GLM-ish SWE-2",
                     "skill": "devin:devin-delegate"},
        }, position=0)
        self.insert("full-low", {
            "critic": {"provider": "glm", "label": "GLM+DS+H", "skill": "pi:pi-delegate",
                       "title": "GLM 5.3 Flash · high + DeepSeek Flash · high + Haiku 5.5 · high"},
            "judge": {"provider": "glm", "label": "GLM", "title": "GLM 5.3 Flash",
                      "members": [{"harness": "pi", "channel": "deepseek"}]},
        }, position=1)
        self.insert("full-broken", "{не json", position=2)
        self.insert("cc-high", {"critic": {"provider": "claude", "label": "x",
                                           "title": "DeepSeek + GLM"}},
                    plugin="pipeline-cc", position=3)
        self.insert("dsh", {"impl": {"harness": "dsh", "argv": ["dsh"]}},
                    kind="swarm", plugin=None, position=4)

    def test_fill_rules(self) -> None:
        self.seed_rows()
        changed = routes_store.fill_members(self.conn)
        self.assertEqual(changed, ["full-medium", "full-low"])
        medium = self.roles("full-medium")
        self.assertEqual(medium["critic"]["members"], [{"harness": "pi", "channel": "deepseek"}])
        self.assertNotIn("members", medium["impl"])  # чужой skill не трогаем
        low = self.roles("full-low")
        self.assertEqual(low["critic"]["members"], [{"harness": "pi", "channel": "glm"},
                                                    {"harness": "pi", "channel": "deepseek"}])
        self.assertEqual(low["critic"]["skill"], "pi:pi-delegate")
        self.assertEqual(low["judge"]["members"], [{"harness": "pi", "channel": "deepseek"}])
        self.assertNotEqual(self.updated("full-medium"), OLD)
        for key in ("full-broken", "cc-high", "dsh"):
            self.assertEqual(self.updated(key), OLD)
        self.assertNotIn("members", self.roles("cc-high")["critic"])
        self.assertIsNotNone(self.flag())

    def test_missing_channel_skipped(self) -> None:
        drop_pi_channel(self.conn, "glm")
        self.seed_rows()
        routes_store.fill_members(self.conn)
        self.assertEqual(self.roles("full-low")["critic"]["members"],
                         [{"harness": "pi", "channel": "deepseek"}])

    def test_runs_once(self) -> None:
        self.seed_rows()
        routes_store.fill_members(self.conn)
        roles = self.roles("full-medium")
        roles["critic"].pop("members")
        self.conn.execute("UPDATE routes SET roles = ?, updated_at = ? WHERE key = 'full-medium'",
                          (json.dumps(roles), OLD))
        self.conn.commit()
        self.assertEqual(routes_store.fill_members(self.conn), [])
        self.assertNotIn("members", self.roles("full-medium")["critic"])
        self.assertEqual(self.updated("full-medium"), OLD)

    def test_error_in_ensure_imported_leaves_no_flag(self) -> None:
        self.seed_rows()
        # Дописки поставки уже отмечены: в ensure_imported падает только заполнение.
        self.conn.execute("INSERT INTO meta(key, value) VALUES('routes_additions', ?)",
                          (str(max(n for n, _ in routes_store.ROUTE_ADDITIONS)),))
        self.conn.commit()
        before = snapshot(self.conn)
        # Первая строка записана, на второй — сбой: откатывается и первая.
        with mock.patch.object(routes_store.store, "now_iso",
                               side_effect=["2026-10-11T00:00:00Z", RuntimeError("сбой")]), \
                contextlib.redirect_stderr(io.StringIO()):
            result = routes_store.ensure_imported(self.conn)
        self.assertIn("error", result)
        self.assertIsNone(self.flag())
        self.assertEqual(snapshot(self.conn), before)

    def test_ensure_imported_fresh_install_only_sets_flag(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            result = routes_store.ensure_imported(self.conn)
        self.assertNotIn("error", result)
        self.assertIsNotNone(self.flag())
        self.assertEqual(routes_store.get_route(self.conn, "full-nano")["roles"]["judge"]["members"],
                         [{"harness": "pi", "channel": "glm"}])
