"""Исполнители `pi` в ролях конвейера v2: сверка каналов при ввозе и `used_by` (listik-906l).

Раньше здесь жили `members` ячейки v1 и разовое заполнение `fill_members`; в формате v2
канал — поле исполнителя `{"harness": "pi", "channel": …}`, а заполнения больше нет.
"""
from __future__ import annotations

import contextlib
import io
from unittest import mock

from listik import harnesses_store
from listik import paths
from listik import routes as routes_mod
from listik import routes_store
from tests.helpers import TempDbTestCase


def drop_pi_channel(conn, name: str) -> None:
    channels = [ch for ch in harnesses_store.get(conn, "pi")["channels"] if ch["name"] != name]
    harnesses_store.update(conn, "pi", {"channels": channels})


def snapshot(conn) -> list[tuple]:
    return [tuple(row) for row in conn.execute(
        "SELECT key, position, roles, updated_at FROM routes ORDER BY key")]


def pi(channel: str) -> dict:
    return {"harness": "pi", "channel": channel, "effort": "high"}


class ReimportChannelsTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch.object(paths, "DATA_DIR", self.tmp_path / "data")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reimport_with_stale_channel_rolls_back(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.import_file(self.conn)
        before = snapshot(self.conn)
        drop_pi_channel(self.conn, "glm")
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_store.reimport(self.conn)
        self.assertEqual(str(ctx.exception),
                         "roles.critic[2].channel: у харнесса 'pi' нет канала 'glm'")
        self.assertEqual(snapshot(self.conn), before)


class UsedByPipelineTests(TempDbTestCase):
    def test_order_and_shape(self) -> None:
        routes_store.upsert_route(self.conn, {
            "key": "lp-b", "kind": "pipeline", "plugin": "listik-pipeline", "title": "b",
            "visible": True, "roles": {"critic": [pi("deepseek"), pi("glm")],
                                       "judge": [pi("glm")]},
        }, position=1)
        routes_store.upsert_route(self.conn, {
            "key": "lp-a", "kind": "pipeline", "plugin": "listik-pipeline", "title": "a",
            "visible": True, "roles": {"critic": [pi("glm")]},
        }, position=5)
        routes_store.upsert_route(self.conn, {
            "key": "swarm-pi", "kind": "swarm", "title": "s", "visible": True,
            "roles": {"impl": {"harness": "pi", "argv": ["pi"]}},
        }, position=3)
        self.assertEqual(harnesses_store.used_by(self.conn, "pi"), [
            {"route": "lp-b", "kind": "pipeline", "role": "critic", "channel": "deepseek"},
            {"route": "lp-b", "kind": "pipeline", "role": "critic", "channel": "glm"},
            {"route": "lp-b", "kind": "pipeline", "role": "judge", "channel": "glm"},
            {"route": "swarm-pi", "kind": "swarm", "role": "impl"},
            {"route": "lp-a", "kind": "pipeline", "role": "critic", "channel": "glm"},
        ])

    def test_sample_routes(self) -> None:
        """Пункт 26: после ввоза `routes.json` — пары (маршрут, роль, канал) по порядку."""
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.import_file(self.conn)
        used = harnesses_store.used_by(self.conn, "pi")
        self.assertEqual(used[:2], [
            {"route": "lp-xhigh", "kind": "pipeline", "role": "critic", "channel": "deepseek"},
            {"route": "lp-xhigh", "kind": "pipeline", "role": "critic", "channel": "glm"},
        ])
        self.assertEqual(used[-1], {"route": "lp-nano", "kind": "pipeline", "role": "judge",
                                    "channel": "glm"})
        self.assertIn({"route": "lp-xhigh", "kind": "pipeline", "role": "judge", "channel": None},
                      harnesses_store.used_by(self.conn, "grok"))
        self.assertIn({"route": "lp-low", "kind": "pipeline", "role": "implementer",
                       "channel": None}, harnesses_store.used_by(self.conn, "devin"))


class EnsureImportedTests(TempDbTestCase):
    """`fill_members` удалён: ввоз не трогает флаг `route_members_v1`."""

    def flag(self):
        return self.conn.execute(
            "SELECT value FROM meta WHERE key = 'route_members_v1'").fetchone()

    def test_fresh_install_sets_no_flag(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            result = routes_store.ensure_imported(self.conn)
        self.assertNotIn("error", result)
        self.assertIsNone(self.flag())
        self.assertEqual(routes_store.get_route(self.conn, "lp-nano")["roles"]["judge"],
                         [pi("glm")])

    def test_old_flag_left_alone(self) -> None:
        self.conn.execute("INSERT INTO meta(key, value) VALUES('route_members_v1', '1')")
        self.conn.commit()
        with contextlib.redirect_stderr(io.StringIO()):
            routes_store.ensure_imported(self.conn)
        self.assertEqual(self.flag()[0], "1")
