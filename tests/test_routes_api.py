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
    "nano-pipeline", "devin-pipeline", "inherit-pipeline", "opus-single-pipeline", "opus-sonnet-pipeline",
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


class CreateDirectRouteTests(RoutesApiBase):
    """`POST /api/routes` с `kind="direct"` заводит прямой маршрут (listik-sjx3, порция a)."""

    def body(self, **overrides) -> dict:
        payload = {"kind": "direct", "key": "probe-direct", "title": "Проба",
                   "harness": "codex", "command": ["codex", "exec", "{task_id}"]}
        payload.update(overrides)
        return payload

    def test_post_creates_direct_route_and_get_shows_it(self) -> None:
        status, record = self.post("/api/routes", self.body())
        self.assertEqual(status, 201)
        self.assertEqual(set(record), {"key", "kind", "title", "hint", "visible",
                                       "icon", "position", "command", "harness"})
        self.assertEqual(record["key"], "probe-direct")
        self.assertEqual(record["kind"], "direct")
        self.assertEqual(record["title"], "Проба")
        self.assertEqual(record["hint"], "")
        self.assertFalse(record["visible"])
        self.assertEqual(record["icon"], "direct")
        self.assertEqual(record["command"], ["codex", "exec", "{task_id}"])
        self.assertEqual(record["harness"], "codex")
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        stored = next(r for r in data["routes"] if r["key"] == "probe-direct")
        self.assertEqual(record, stored)

    def test_post_direct_position_is_after_existing(self) -> None:
        self.import_sample()
        _, before = self.get("/api/routes")
        top = max(r["position"] for r in before["routes"])
        status, record = self.post("/api/routes", self.body())
        self.assertEqual(status, 201)
        self.assertGreater(record["position"], top)

    def test_post_duplicate_direct_key_is_409(self) -> None:
        self.post("/api/routes", self.body())
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", self.body(title="Другое"))
        self.assertEqual(ctx.exception.status, 409)
        self.assertEqual(ctx.exception.code, errors.CONFLICT)
        self.assertIn("probe-direct", ctx.exception.message)
        _, data = self.get("/api/routes")
        matches = [r for r in data["routes"] if r["key"] == "probe-direct"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["title"], "Проба")
        self.assertEqual(matches[0]["command"], ["codex", "exec", "{task_id}"])

    def test_hint_and_visible_defaults_and_overrides(self) -> None:
        _, base = self.post("/api/routes", self.body(key="probe-a"))
        self.assertEqual(base["hint"], "")
        self.assertFalse(base["visible"])
        _, custom = self.post("/api/routes",
                              self.body(key="probe-b", hint="проба", visible=True))
        self.assertEqual(custom["hint"], "проба")
        self.assertTrue(custom["visible"])

    def test_icon_default_null_and_override(self) -> None:
        _, default = self.post("/api/routes", self.body(key="probe-a"))
        self.assertEqual(default["icon"], "direct")
        _, no_icon = self.post("/api/routes", self.body(key="probe-b", icon=None))
        self.assertIsNone(no_icon["icon"])
        _, xlow = self.post("/api/routes", self.body(key="probe-c", icon="xlow"))
        self.assertEqual(xlow["icon"], "xlow")

    def test_missing_required_fields_are_400_naming_field(self) -> None:
        for field in ("key", "title", "harness", "command"):
            with self.subTest(field=field):
                payload = self.body()
                del payload[field]
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", payload)
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
                self.assertIn(field, ctx.exception.message)
        _, data = self.get("/api/routes")
        self.assertFalse(any(r["key"] == "probe-direct" for r in data["routes"]))

    def test_bad_command_is_400_and_not_created(self) -> None:
        cases = [[], "codex exec", ["codex", ""], ["codex", "{foo}"]]
        for i, command in enumerate(cases):
            with self.subTest(command=command):
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", self.body(key=f"probe-{i}", command=command))
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
                if command == ["codex", "{foo}"]:
                    self.assertIn("{foo}", ctx.exception.message)
        _, data = self.get("/api/routes")
        self.assertFalse(any(r["key"].startswith("probe-") for r in data["routes"]))

    def test_bad_key_is_400(self) -> None:
        for key in ("Прямой", "-abc"):
            with self.subTest(key=key):
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", self.body(key=key))
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
                self.assertIn("key", ctx.exception.message)

    def test_unknown_harness_and_icon_are_400_with_allowed(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", self.body(harness="opencode"))
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        for name in ("claude", "dsh", "codex", "grok", "gemini"):
            self.assertIn(name, ctx.exception.message)
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", self.body(icon="turbo"))
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        for name in ("xhigh", "high", "medium", "low", "xlow", "direct"):
            self.assertIn(name, ctx.exception.message)

    def test_extra_fields_are_400_with_field_name(self) -> None:
        for field in ("roles", "position"):
            with self.subTest(field=field):
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", self.body(**{field: {}}))
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
                self.assertIn(field, ctx.exception.message)

    def test_bad_kind_is_400_naming_kind_and_allowed(self) -> None:
        for kind in ("conveyor", 5, []):
            with self.subTest(kind=kind):
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", self.body(kind=kind))
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
                self.assertIn("kind", ctx.exception.message)
                self.assertIn("pipeline", ctx.exception.message)
                self.assertIn("direct", ctx.exception.message)
        payload = self.body()
        payload["kind"] = None
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", payload)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)

    def test_pipeline_without_kind_and_explicit_kind_are_same(self) -> None:
        status_a, rec_a = self.post("/api/routes", {"key": "high-pipeline"})
        self.assertEqual(status_a, 201)
        status_b, rec_b = self.post("/api/routes", {"key": "low-pipeline", "kind": "pipeline"})
        self.assertEqual(status_b, 201)
        for record in (rec_a, rec_b):
            self.assertEqual(record["kind"], "pipeline")
            self.assertFalse(record["visible"])
            self.assertIsNone(record["command"])
            self.assertTrue(record["title"])

    def test_pipeline_unknown_key_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "нет-такого-скила"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)

