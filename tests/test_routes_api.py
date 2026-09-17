"""API маршрутов: чтение с ролями, сверка со скилами, правка, заведение, удаление,
порядок (шаг listik-8jgz, порция c).

Прогоняется через `server.handle(...)`, как соседние тесты ручек (`test_route_change.py`,
`test_projects_add.py`): `get_conn` подменяется на временное соединение, ошибки — это
`server.ApiError`, пойманный `assertRaises`. Скилы читаются из настоящего
`plugins/feature-pipeline/skills/*` репозитория (там ровно те 10 ключей, что и в образце
`routes.json`) — кроме тестов, которые явно подменяют `skills.SKILLS_DIR` на пустой/чужой
каталог.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
from unittest import mock

from listik import errors
from listik import routes_store
from listik import server
from listik import skills as skills_mod
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
ROUTES_JSON = REPO_DIR / "routes.json"

DIRECT_KEYS = ["dsh", "grok", "codex"]
EXPECTED_KEYS = [
    "xhigh-pipeline", "high-pipeline", "medium-pipeline", "low-pipeline", "xlow-pipeline",
    "nano-pipeline", "inherit-pipeline", "opus-single-pipeline", "opus-sonnet-pipeline",
    "universal-pipeline", "feature-pipeline", *DIRECT_KEYS,
]


def pipeline_record(key: str = "demo-pipeline", **overrides) -> dict:
    record = {
        "key": key,
        "kind": "pipeline",
        "title": "Демо",
        "hint": "подсказка",
        "visible": True,
        "roles": {"impl": {"provider": "claude", "label": "Opus", "title": "Opus · medium"}},
    }
    record.update(overrides)
    return record


def direct_record(key: str = "dsh", **overrides) -> dict:
    record = {"key": key, "kind": "direct", "harness": "dsh", "title": key,
              "hint": "", "visible": True}
    record.update(overrides)
    return record


class RoutesApiBase(TempDbTestCase):
    """Общая обвязка: временная база вместо `get_conn`, тихий ввоз, короткие обёртки handle()."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)

    def import_sample(self) -> dict:
        with contextlib.redirect_stderr(io.StringIO()):
            return routes_store.import_file(self.conn, ROUTES_JSON)

    def write_and_import(self, records, *, replace: bool = False) -> dict:
        path = self.tmp_path / "routes.json"
        path.write_text(json.dumps({"version": 1, "routes": list(records)}, ensure_ascii=False),
                        encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return routes_store.import_file(self.conn, path, replace=replace)

    def get(self, path: str) -> tuple[int, dict]:
        return server.handle("GET", path, {}, {}, authed=True)

    def post(self, path: str, body: dict) -> tuple[int, dict]:
        with mock.patch.object(server, "publish"):
            return server.handle("POST", path, {}, body, authed=True)

    def patch(self, path: str, body: dict) -> tuple[int, dict]:
        with mock.patch.object(server, "publish"):
            return server.handle("PATCH", path, {}, body, authed=True)

    def delete(self, path: str) -> tuple[int, dict]:
        with mock.patch.object(server, "publish"):
            return server.handle("DELETE", path, {}, {}, authed=True)


class GetRoutesFieldsTests(RoutesApiBase):
    """`GET /api/routes` отдаёт `command`, `roles`/`harness`, `position`, порядок по `position`."""

    def test_command_roles_position_are_present_and_ordered(self) -> None:
        self.import_sample()
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual([r["key"] for r in data["routes"]], EXPECTED_KEYS)
        for record in data["routes"]:
            self.assertIn("command", record)
            self.assertIn("position", record)
            if record["kind"] == "pipeline":
                self.assertIsInstance(record["roles"], dict)
                self.assertNotIn("harness", record)
            else:
                self.assertIsInstance(record["harness"], str)
                self.assertNotIn("roles", record)


class SkillMatchTests(RoutesApiBase):
    """Маршрут `kind=pipeline` без каталога скила — скрыт и назван в предупреждениях."""

    def test_route_without_skill_is_hidden_with_warning(self) -> None:
        self.write_and_import([pipeline_record(key="totally-fake-pipeline"), direct_record()])
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        record = next(r for r in data["routes"] if r["key"] == "totally-fake-pipeline")
        self.assertTrue(record["skill_missing"])
        self.assertFalse(record["visible"])
        self.assertIsNone(record["skill_path"])
        self.assertTrue(any("totally-fake-pipeline" in w for w in data["warnings"]))
        # В базе значение не меняется — маршрут скрыт в ответе, а не переписан.
        row = routes_store.get_route(self.conn, "totally-fake-pipeline")
        self.assertTrue(row["visible"])

    def test_route_with_real_skill_has_skill_path_and_is_not_hidden(self) -> None:
        self.write_and_import([pipeline_record(key="high-pipeline"), direct_record()])
        status, data = self.get("/api/routes")
        record = next(r for r in data["routes"] if r["key"] == "high-pipeline")
        self.assertNotIn("skill_missing", record)
        self.assertTrue(record["visible"])
        self.assertEqual(record["skill_path"],
                         "plugins/feature-pipeline/skills/high-pipeline/SKILL.md")


class NoSkillsDirectoryTests(RoutesApiBase):
    """Установленный Listik может быть без каталога `plugins/` вовсе — сверка тогда пустая."""

    def setUp(self) -> None:
        super().setUp()
        self._skills_patch = mock.patch.object(skills_mod, "SKILLS_DIR",
                                               self.tmp_path / "нет-такого-каталога")
        self._skills_patch.start()
        self.addCleanup(self._skills_patch.stop)

    def test_no_route_is_marked_missing_without_skills_dir(self) -> None:
        self.import_sample()
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        self.assertFalse(any(r.get("skill_missing") for r in data["routes"]))
        self.assertEqual(data["warnings"], [])

    def test_sync_reports_unavailable_and_empty_lists(self) -> None:
        self.import_sample()
        status, data = self.get("/api/routes/sync")
        self.assertEqual(status, 200)
        self.assertFalse(data["skills_available"])
        self.assertEqual(data["missing_skill"], [])
        self.assertEqual(data["missing_route"], [])


class SyncEndpointTests(RoutesApiBase):
    def test_sync_lists_both_directions(self) -> None:
        self.write_and_import([pipeline_record(key="totally-fake-pipeline"), direct_record()])
        status, data = self.get("/api/routes/sync")
        self.assertEqual(status, 200)
        self.assertTrue(data["skills_available"])
        self.assertEqual([r["key"] for r in data["missing_skill"]], ["totally-fake-pipeline"])
        missing_route_keys = {r["key"] for r in data["missing_route"]}
        self.assertIn("high-pipeline", missing_route_keys)
        self.assertNotIn("totally-fake-pipeline", missing_route_keys)


class PatchRouteTests(RoutesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_patch_updates_title_hint_icon_visible(self) -> None:
        status, record = self.patch("/api/routes/dsh",
                                    {"title": "Новый", "hint": "h", "icon": "high",
                                     "visible": False})
        self.assertEqual(status, 200)
        self.assertEqual(record["title"], "Новый")
        self.assertEqual(record["hint"], "h")
        self.assertEqual(record["icon"], "high")
        self.assertFalse(record["visible"])

    def test_patch_roles_in_body_is_400_names_the_field(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/high-pipeline", {"roles": {}})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertIn("roles", ctx.exception.message)

    def test_patch_kind_key_harness_position_are_400(self) -> None:
        for field, value in (("kind", "direct"), ("key", "x"), ("harness", "dsh"),
                             ("position", 0)):
            with self.assertRaises(server.ApiError) as ctx:
                self.patch("/api/routes/dsh", {field: value})
            self.assertEqual(ctx.exception.status, 400, field)
            self.assertIn(field, ctx.exception.message)

    def test_patch_command_on_pipeline_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/high-pipeline", {"command": ["x"]})
        self.assertEqual(ctx.exception.status, 400)

    def test_patch_command_unknown_placeholder_names_it(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/dsh", {"command": ["run", "{foo}"]})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("{foo}", ctx.exception.message)

    def test_patch_unknown_field_is_400_names_it(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/dsh", {"foo": 1})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("foo", ctx.exception.message)

    def test_patch_empty_body_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/dsh", {})
        self.assertEqual(ctx.exception.status, 400)

    def test_patch_unknown_key_is_404(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/нет-такого", {"title": "x"})
        self.assertEqual(ctx.exception.status, 404)


class CreateRouteTests(RoutesApiBase):
    def test_post_creates_pipeline_route_without_roles(self) -> None:
        status, record = self.post("/api/routes", {"key": "high-pipeline"})
        self.assertEqual(status, 201)
        self.assertEqual(record["kind"], "pipeline")
        self.assertFalse(record["visible"])
        self.assertEqual(record["roles"], {})
        self.assertIsNone(record["command"])
        self.assertTrue(record["title"])
        self.assertTrue(record["hint"] or record["hint"] == "")

    def test_post_unknown_key_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "не-скил-вообще"})
        self.assertEqual(ctx.exception.status, 400)

    def test_post_extra_field_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "high-pipeline", "visible": True})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("visible", ctx.exception.message)

    def test_post_duplicate_key_is_409(self) -> None:
        self.import_sample()
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "high-pipeline"})
        self.assertEqual(ctx.exception.status, 409)


class DeleteRouteTests(RoutesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_delete_clears_route_and_labels_but_keeps_tasks(self) -> None:
        open_task = store.create_task(self.conn, title="open", project="p", route="dsh")
        working = store.create_task(self.conn, title="working", project="p", route="dsh")
        store.claim(self.conn, working["id"], holder="dsh")
        status, data = self.delete("/api/routes/dsh")
        self.assertEqual(status, 200)
        self.assertEqual(data["removed"], "dsh")
        self.assertEqual(data["tasks_cleared"], 2)
        for tid in (open_task["id"], working["id"]):
            row = self.conn.execute(
                "SELECT launch_route, labels, status, holder FROM tasks WHERE id = ?",
                (tid,)).fetchone()
            self.assertIsNone(row["launch_route"])
            self.assertNotIn("harness:dsh", json.loads(row["labels"]))
            self.assertNotIn("process:direct", json.loads(row["labels"]))
        # Держатель и статус задачи в работе — не тронуты.
        self.assertEqual(
            self.conn.execute("SELECT holder FROM tasks WHERE id = ?",
                              (working["id"],)).fetchone()["holder"], "dsh")
        self.assertIsNotNone(store.get_task(self.conn, open_task["id"]))
        self.assertIsNotNone(store.get_task(self.conn, working["id"]))
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "dsh")

    def test_delete_unknown_key_is_404(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.delete("/api/routes/нет-такого")
        self.assertEqual(ctx.exception.status, 404)

    def test_second_delete_of_same_key_is_404_and_noop(self) -> None:
        self.delete("/api/routes/grok")
        with self.assertRaises(server.ApiError) as ctx:
            self.delete("/api/routes/grok")
        self.assertEqual(ctx.exception.status, 404)


class ReorderRouteTests(RoutesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_reorder_changes_get_order(self) -> None:
        new_order = list(reversed(EXPECTED_KEYS))
        status, records = self.post("/api/routes/reorder", {"keys": new_order})
        self.assertEqual(status, 200)
        self.assertEqual([r["key"] for r in records], new_order)
        status, data = self.get("/api/routes")
        self.assertEqual([r["key"] for r in data["routes"]], new_order)

    def test_reorder_unknown_key_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes/reorder", {"keys": ["нет-такого-ключа"]})
        self.assertEqual(ctx.exception.status, 400)


class ExportImportRoundtripTests(RoutesApiBase):
    def test_export_then_import_replace_gives_the_same_table(self) -> None:
        self.import_sample()
        before = routes_store.list_routes(self.conn)
        exported = routes_store.export_records(self.conn)
        self.assertTrue(all("position" not in record for record in exported))
        path = self.tmp_path / "roundtrip.json"
        path.write_text(json.dumps({"version": 1, "routes": exported}, ensure_ascii=False),
                        encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            report = routes_store.import_file(self.conn, path, replace=True)
        self.assertEqual(report["imported"], len(before))
        after = routes_store.list_routes(self.conn)
        self.assertEqual(after, before)
