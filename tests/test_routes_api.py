"""API маршрутов: чтение с ролями, сверка со скилами, правка, заведение, удаление,
порядок (шаг listik-8jgz, порция c).

Прогоняется через `server.handle(...)`, как соседние тесты ручек (`test_route_change.py`,
`test_projects_add.py`): `get_conn` подменяется на временное соединение, ошибки — это
`server.ApiError`, пойманный `assertRaises`. Скилы читаются из настоящих
`plugins/pipeline-full|pipeline-cc|pipeline-claude/skills/*` репозитория (вместе — ровно те
16 ключей, что и в образце `routes.json`, ключ `<плагин без pipeline->-<скил>`) — кроме
тестов, которые явно подменяют `skills.PIPELINE_PLUGINS_DIR` на пустой/чужой каталог.
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
from unittest import mock

from listik import errors
from listik import harnesses_store
from listik import routes_store
from listik import server
from listik import skills as skills_mod
from listik import stage_launch
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
ROUTES_JSON = REPO_DIR / "routes.json"

EXPECTED_KEYS = [
    "full-xhigh", "full-high", "full-medium", "cc-sol", "full-low",
    "full-xlow", "full-nano", "full-cross", "claude-high", "claude-opus",
    "cc-xhigh", "cc-high", "cc-medium", "cc-low",
    "cc-xlow", "cc-nano",
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


def swarm_record(key: str = "dsh", **overrides) -> dict:
    record = {"key": key, "kind": "swarm", "title": key, "hint": "", "visible": True,
              "roles": {"impl": {"harness": "dsh"}}}
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
    """`GET /api/routes` отдаёт `command`, `roles`, `position`, порядок по `position`."""

    def test_command_roles_position_are_present_and_ordered(self) -> None:
        self.import_sample()
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual([r["key"] for r in data["routes"]], EXPECTED_KEYS)
        for record in data["routes"]:
            self.assertIn("command", record)
            self.assertIn("position", record)
            self.assertIsInstance(record["roles"], dict)
            self.assertNotIn("harness", record)


class SkillMatchTests(RoutesApiBase):
    """Маршрут `kind=pipeline` без каталога скила — скрыт и назван в предупреждениях."""

    def test_route_without_skill_is_hidden_with_warning(self) -> None:
        self.write_and_import([pipeline_record(key="totally-fake-pipeline"), swarm_record()])
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
        self.write_and_import([pipeline_record(key="full-high", plugin="pipeline-full"),
                               swarm_record()])
        status, data = self.get("/api/routes")
        record = next(r for r in data["routes"] if r["key"] == "full-high")
        self.assertNotIn("skill_missing", record)
        self.assertTrue(record["visible"])
        self.assertEqual(record["skill_path"],
                         "plugins/pipeline-full/skills/high/SKILL.md")


class ClaudeCodexSkillTests(RoutesApiBase):
    """Маршрут `cc-<имя>` ↔ скил `plugins/pipeline-cc/skills/<имя>/SKILL.md`."""

    def test_cc_route_has_pipeline_cc_skill_path(self) -> None:
        self.import_sample()
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        record = next(r for r in data["routes"] if r["key"] == "cc-high")
        self.assertNotIn("skill_missing", record)
        self.assertTrue(record["visible"])
        self.assertEqual(record["skill_path"],
                         "plugins/pipeline-cc/skills/high/SKILL.md")

    def test_cc_route_without_skill_is_hidden_with_pipeline_cc_warning(self) -> None:
        self.write_and_import([pipeline_record(key="cc-fake", plugin="pipeline-cc"), swarm_record()])
        status, data = self.get("/api/routes")
        record = next(r for r in data["routes"] if r["key"] == "cc-fake")
        self.assertTrue(record["skill_missing"])
        self.assertFalse(record["visible"])
        self.assertTrue(any("/pipeline-cc:fake" in w for w in data["warnings"]))
        self.assertFalse(any("/pipeline-full:cc-fake" in w for w in data["warnings"]))

    def test_sync_on_sample_is_clean(self) -> None:
        self.import_sample()
        status, data = self.get("/api/routes/sync")
        self.assertEqual(status, 200)
        self.assertTrue(data["skills_available"])
        self.assertEqual(data["missing_skill"], [])
        self.assertEqual(data["missing_route"], [])

    def test_no_plugins_dir_means_no_skills(self) -> None:
        with mock.patch.object(skills_mod, "PIPELINE_PLUGINS_DIR", self.tmp_path / "нет-такого-каталога"):
            self.assertTrue((skills_mod.paths.ROOT_DIR / "plugins" / "pipeline-cc" / "skills").is_dir())
            self.assertFalse(skills_mod.skills_available())
            self.import_sample()
            status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        self.assertFalse(any(r.get("skill_missing") for r in data["routes"]))

    def test_post_unknown_cc_key_is_400_naming_plugins(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "cc-fake"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertIn("pipeline-cc", ctx.exception.message)
        self.assertIn("plugins/pipeline-full", ctx.exception.message)
        self.assertIn("full-high", ctx.exception.message)

    def test_post_deleted_cc_route_is_201(self) -> None:
        self.import_sample()
        routes_store.delete_route(self.conn, "cc-high")
        status, record = self.post("/api/routes", {"key": "cc-high"})
        self.assertEqual(status, 201)
        self.assertEqual(record["kind"], "pipeline")
        # Заголовок — короткий `name` из frontmatter, без имени плагина; уровень — по ключу.
        self.assertEqual(record["title"], "high")
        self.assertEqual(record["icon"], "high")


class PluginFieldApiTests(RoutesApiBase):
    """Поле `plugin` в `GET`/`POST`/`PATCH /api/routes` (listik-d9rj, порция d)."""

    def test_get_sample_has_plugin_and_skill_path(self) -> None:
        self.import_sample()
        routes_store.create_route(self.conn, key="roy", kind="swarm", title="Рой",
                                  roles={"impl": {"harness": "dsh"}})
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        records = {r["key"]: r for r in data["routes"]}
        for record in data["routes"]:
            self.assertIn("plugin", record, record["key"])
        self.assertEqual(records["full-high"]["plugin"], "pipeline-full")
        self.assertEqual(records["full-high"]["skill_path"],
                         "plugins/pipeline-full/skills/high/SKILL.md")
        self.assertEqual(records["cc-sol"]["plugin"], "pipeline-cc")
        self.assertEqual(records["claude-opus"]["plugin"], "pipeline-claude")
        self.assertIsNone(records["roy"]["plugin"])
        self.assertEqual(data["warnings"], [])

    def test_pipeline_without_plugin_is_hidden_with_warning(self) -> None:
        self.write_and_import([pipeline_record(key="old-flow"), swarm_record()])
        status, data = self.get("/api/routes")
        record = next(r for r in data["routes"] if r["key"] == "old-flow")
        self.assertTrue(record["skill_missing"])
        self.assertFalse(record["visible"])
        self.assertIsNone(record["skill_path"])
        self.assertIn("маршрута 'old-flow': не задан плагин, маршрут скрыт", data["warnings"])
        self.assertTrue(routes_store.get_route(self.conn, "old-flow")["visible"])
        status, sync = self.get("/api/routes/sync")
        self.assertIn("old-flow", [r["key"] for r in sync["missing_skill"]])

    def test_plugin_without_skill_dir_is_hidden_with_warning(self) -> None:
        self.write_and_import([pipeline_record(key="full-nope", plugin="pipeline-full")])
        status, data = self.get("/api/routes")
        record = next(r for r in data["routes"] if r["key"] == "full-nope")
        self.assertTrue(record["skill_missing"])
        self.assertFalse(record["visible"])
        self.assertIn("маршрута 'full-nope': скила /pipeline-full:nope нет, маршрут скрыт",
                      data["warnings"])
        status, sync = self.get("/api/routes/sync")
        self.assertEqual([r["key"] for r in sync["missing_skill"]], ["full-nope"])

    def test_post_takes_plugin_from_catalogue(self) -> None:
        self.import_sample()
        routes_store.delete_route(self.conn, "cc-high")
        status, record = self.post("/api/routes", {"key": "cc-high"})
        self.assertEqual(status, 201)
        self.assertEqual(record["plugin"], "pipeline-cc")
        self.assertEqual(routes_store.get_route(self.conn, "cc-high")["plugin"], "pipeline-cc")

    def test_patch_plugin_is_400_and_unchanged(self) -> None:
        self.import_sample()
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/full-high", {"plugin": "pipeline-cc"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertEqual(routes_store.get_route(self.conn, "full-high")["plugin"],
                         "pipeline-full")


class SkillCatalogTests(RoutesApiBase):
    """Ключ скила `<плагин без pipeline->-<скил>` (listik-d9rj, порция b): справочник и имя для текстов."""

    def test_skill_info_by_plugin_prefix(self) -> None:
        info = skills_mod.skill_info("full-high")
        self.assertEqual(info["plugin"], "pipeline-full")
        self.assertEqual(info["skill"], "high")
        self.assertEqual(info["title"], "high")
        self.assertEqual(info["skill_path"], "plugins/pipeline-full/skills/high/SKILL.md")
        self.assertEqual(skills_mod.skill_info("cc-sol")["skill"], "sol")
        self.assertEqual(skills_mod.skill_info("cc-sol")["skill_path"],
                         "plugins/pipeline-cc/skills/sol/SKILL.md")
        for key in ("xhigh-pipeline", "nope-high", "full-nope"):
            with self.subTest(key=key):
                self.assertIsNone(skills_mod.skill_info(key))

    def test_skill_ref_is_pure_string(self) -> None:
        cases = (("full-high", "/pipeline-full:high"), ("full-nope", "/pipeline-full:nope"),
                 ("cc-fake", "/pipeline-cc:fake"), ("claude-opus", "/pipeline-claude:opus"),
                 ("xhigh-pipeline", None), ("pidi", None), ("nope-high", None), ("full-", None))
        for key, ref in cases:
            with self.subTest(key=key):
                self.assertEqual(skills_mod.skill_ref(key), ref)

    def test_skill_keys_match_sample(self) -> None:
        self.assertEqual(skills_mod.skill_keys(), sorted(EXPECTED_KEYS))

    def test_sync_missing_route_names_plugin_and_short_title(self) -> None:
        self.import_sample()
        routes_store.delete_route(self.conn, "cc-high")
        status, data = self.get("/api/routes/sync")
        self.assertEqual(status, 200)
        self.assertEqual(data["missing_skill"], [])
        self.assertEqual([r["key"] for r in data["missing_route"]], ["cc-high"])
        self.assertEqual(data["missing_route"][0]["plugin"], "pipeline-cc")
        self.assertEqual(data["missing_route"][0]["title"], "high")


class NoSkillsDirectoryTests(RoutesApiBase):
    """Установленный Listik может быть без каталога `plugins/` вовсе — сверка тогда пустая."""

    def setUp(self) -> None:
        super().setUp()
        self._skills_patch = mock.patch.object(skills_mod, "PIPELINE_PLUGINS_DIR",
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
        self.write_and_import([pipeline_record(key="totally-fake-pipeline"), swarm_record()])
        status, data = self.get("/api/routes/sync")
        self.assertEqual(status, 200)
        self.assertTrue(data["skills_available"])
        self.assertEqual([r["key"] for r in data["missing_skill"]], ["totally-fake-pipeline"])
        missing_route_keys = {r["key"] for r in data["missing_route"]}
        self.assertIn("full-high", missing_route_keys)
        self.assertNotIn("totally-fake-pipeline", missing_route_keys)


class PatchRouteTests(RoutesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_patch_updates_title_hint_icon_visible(self) -> None:
        status, record = self.patch("/api/routes/full-cross",
                                    {"title": "Новый", "hint": "h", "icon": "high",
                                     "visible": False})
        self.assertEqual(status, 200)
        self.assertEqual(record["title"], "Новый")
        self.assertEqual(record["hint"], "h")
        self.assertEqual(record["icon"], "high")
        self.assertFalse(record["visible"])

    def test_patch_roles_in_body_is_400_names_the_field(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/full-high", {"roles": {}})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertIn("roles", ctx.exception.message)

    def test_patch_kind_key_harness_position_command_are_400(self) -> None:
        for field, value in (("kind", "swarm"), ("key", "x"), ("harness", "dsh"),
                             ("position", 0), ("command", ["run", "{task_id}"])):
            with self.assertRaises(server.ApiError) as ctx:
                self.patch("/api/routes/full-cross", {field: value})
            self.assertEqual(ctx.exception.status, 400, field)
            self.assertIn(field, ctx.exception.message)
            self.assertIn("нельзя менять", ctx.exception.message)

    def test_patch_command_on_pipeline_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/full-high", {"command": ["x"]})
        self.assertEqual(ctx.exception.status, 400)

    def test_patch_unknown_field_is_400_names_it(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/full-cross", {"foo": 1})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("foo", ctx.exception.message)

    def test_patch_empty_body_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/full-cross", {})
        self.assertEqual(ctx.exception.status, 400)

    def test_patch_unknown_key_is_404(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/нет-такого", {"title": "x"})
        self.assertEqual(ctx.exception.status, 404)


class CreateRouteTests(RoutesApiBase):
    def test_post_creates_pipeline_route_without_roles(self) -> None:
        status, record = self.post("/api/routes", {"key": "full-high"})
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
            self.post("/api/routes", {"key": "full-high", "visible": True})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("visible", ctx.exception.message)

    def test_post_duplicate_key_is_409(self) -> None:
        self.import_sample()
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "full-high"})
        self.assertEqual(ctx.exception.status, 409)


class DeleteRouteTests(RoutesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()

    def test_delete_clears_route_and_labels_but_keeps_tasks(self) -> None:
        open_task = store.create_task(self.conn, title="open", project="p", route="full-cross")
        working = store.create_task(self.conn, title="working", project="p", route="full-cross")
        store.claim(self.conn, working["id"], holder="dsh")
        status, data = self.delete("/api/routes/full-cross")
        self.assertEqual(status, 200)
        self.assertEqual(data["removed"], "full-cross")
        self.assertEqual(data["tasks_cleared"], 2)
        for tid in (open_task["id"], working["id"]):
            row = self.conn.execute(
                "SELECT launch_route, labels, status, holder FROM tasks WHERE id = ?",
                (tid,)).fetchone()
            self.assertIsNone(row["launch_route"])
            self.assertNotIn("harness:claude", json.loads(row["labels"]))
            self.assertNotIn("process:full-cross", json.loads(row["labels"]))
        # Держатель и статус задачи в работе — не тронуты.
        self.assertEqual(
            self.conn.execute("SELECT holder FROM tasks WHERE id = ?",
                              (working["id"],)).fetchone()["holder"], "dsh")
        self.assertIsNotNone(store.get_task(self.conn, open_task["id"]))
        self.assertIsNotNone(store.get_task(self.conn, working["id"]))
        with self.assertRaises(errors.NotFound):
            routes_store.get_route(self.conn, "full-cross")

    def test_delete_unknown_key_is_404(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.delete("/api/routes/нет-такого")
        self.assertEqual(ctx.exception.status, 404)

    def test_second_delete_of_same_key_is_404_and_noop(self) -> None:
        self.delete("/api/routes/full-nano")
        with self.assertRaises(server.ApiError) as ctx:
            self.delete("/api/routes/full-nano")
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


class ReimportRoutesTests(RoutesApiBase):
    """`POST /api/routes/reimport` — HTTP-путь `listik routes --reimport` (listik-ttjm)."""

    def test_reimport_rewrites_table(self) -> None:
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "full-cross")["title"]
        routes_store.update_route(self.conn, "full-cross", title="Моя правка")
        with mock.patch.object(server, "publish") as publish:
            status, report = server.handle("POST", "/api/routes/reimport", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertTrue(report["replaced"])
        self.assertEqual(report["imported"], len(EXPECTED_KEYS))
        self.assertEqual(report["orphans"], {})
        self.assertEqual(routes_store.get_route(self.conn, "full-cross")["title"], shipped)
        publish.assert_called_once_with("route", {"key": None, "action": "reimported"})

    def test_reimport_from_backup_path(self) -> None:
        from listik import paths
        self.import_sample()
        shipped = routes_store.get_route(self.conn, "full-cross")["title"]
        routes_store.update_route(self.conn, "full-cross", title="Моя правка")
        with mock.patch.object(paths, "DATA_DIR", self.tmp_path / "data"), \
                mock.patch.object(server, "publish"), \
                contextlib.redirect_stderr(io.StringIO()):
            _, first = server.handle("POST", "/api/routes/reimport", {}, {}, authed=True)
            self.assertEqual(routes_store.get_route(self.conn, "full-cross")["title"],
                             shipped)
            status, report = server.handle("POST", "/api/routes/reimport", {},
                                           {"path": first["backup"]}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(report["source"], first["backup"])
        self.assertEqual(routes_store.get_route(self.conn, "full-cross")["title"],
                         "Моя правка")

    def test_reimport_bad_path_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            server.handle("POST", "/api/routes/reimport", {}, {"path": 5}, authed=True)
        self.assertEqual(ctx.exception.status, 400)

    def test_reimport_get_is_405(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.get("/api/routes/reimport")
        self.assertEqual(ctx.exception.status, 405)


class CreateRouteKindTests(RoutesApiBase):
    """`POST /api/routes`: проверка `kind` — первая; вида `direct` нет (listik-ar8v)."""

    def assert_kind_rejected(self, body: dict) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", body)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        message = ctx.exception.message
        self.assertIn("kind", message)
        self.assertIn('"pipeline"', message)
        self.assertIn('"swarm"', message)
        self.assertNotIn("direct", message)

    def test_direct_kind_is_400_and_not_created(self) -> None:
        self.assert_kind_rejected({"kind": "direct", "key": "x", "title": "x"})
        # Проверка `kind` идёт до лишних полей: `harness` не меняет ответ.
        self.assert_kind_rejected({"kind": "direct", "key": "x", "title": "x",
                                   "harness": "grok"})
        _, data = self.get("/api/routes")
        self.assertFalse(any(r["key"] == "x" for r in data["routes"]))

    def test_bad_kind_is_400_naming_kind_and_allowed(self) -> None:
        for kind in ("conveyor", 5, [], None):
            with self.subTest(kind=kind):
                self.assert_kind_rejected({"kind": kind, "key": "x", "title": "x"})

    def test_pipeline_without_kind_and_explicit_kind_are_same(self) -> None:
        status_a, rec_a = self.post("/api/routes", {"key": "full-high"})
        self.assertEqual(status_a, 201)
        status_b, rec_b = self.post("/api/routes", {"key": "full-low", "kind": "pipeline"})
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


class HarnessesApiTests(RoutesApiBase):
    """`GET/POST /api/harnesses`, `GET/PATCH /api/harnesses/{key}` (listik-2gry)."""

    def test_list_returns_seeds_with_used_by(self) -> None:
        status, data = self.get("/api/harnesses")
        self.assertEqual(status, 200)
        keys = {h["key"] for h in data["harnesses"]}
        for key in ("claude", "codex", "me"):
            self.assertIn(key, keys)
        for record in data["harnesses"]:
            self.assertIn("used_by", record)
            self.assertIsInstance(record["used_by"], list)
        me = next(h for h in data["harnesses"] if h["key"] == "me")
        self.assertEqual(me["kind"], "manual")
        # Протокол роя приходит с каталогом — доска показывает его в
        # предпросмотре команды роли при пустом `prompt` ячейки.
        self.assertEqual(data["swarm_prompt"], harnesses_store.SWARM_PROMPT)

    def test_list_returns_swarm_role_tails(self) -> None:
        status, data = self.get("/api/harnesses")
        self.assertEqual(status, 200)
        self.assertEqual(data["swarm_prompt"], harnesses_store.SWARM_PROMPT)
        self.assertEqual(data["swarm_role_errors"], {})
        self.assertEqual(data["swarm_role_tails"], {
            "spec": stage_launch.role_tail("spec"),
            "critic": stage_launch.role_tail("critic"),
            "impl": stage_launch.role_tail("impl"),
            "impl_last": stage_launch.role_tail("impl", last=True),
            "judge": stage_launch.role_tail("judge"),
        })

    def test_list_without_agent_files_reports_role_errors(self) -> None:
        agents = self.tmp_path / "agents"
        agents.mkdir()
        with mock.patch.object(stage_launch, "AGENTS_DIR", agents):
            status, data = self.get("/api/harnesses")
        self.assertEqual(status, 200)
        self.assertEqual(data["swarm_prompt"], harnesses_store.SWARM_PROMPT)
        self.assertEqual(data["swarm_role_tails"], {})
        self.assertEqual(set(data["swarm_role_errors"]),
                         {"spec", "critic", "impl", "impl_last", "judge"})
        self.assertEqual(data["swarm_role_errors"]["judge"],
                         f"нет файла {agents}/pipeline-judge.md")

    def test_create_get_patch(self) -> None:
        status, record = self.post("/api/harnesses", {
            "key": "mini", "label": "mini", "hint": "локальный",
            "argv": ["mini", "run"], "prompt": "задача {task_id}"})
        self.assertEqual(status, 201)
        self.assertEqual(record["key"], "mini")
        self.assertFalse(record["builtin"])

        status, detail = self.get("/api/harnesses/mini")
        self.assertEqual(status, 200)
        self.assertEqual(detail["argv"], ["mini", "run"])
        self.assertEqual(detail["used_by"], [])

        status, patched = self.patch("/api/harnesses/mini",
                                     {"hint": "новая", "enabled": False})
        self.assertEqual(status, 200)
        self.assertEqual(patched["hint"], "новая")
        self.assertFalse(patched["enabled"])

    def test_create_bad_key_and_duplicate(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/harnesses", {"key": "Bad Key"})
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(errors.ListikError) as ctx:
            self.post("/api/harnesses", {"key": "claude"})
        self.assertEqual(ctx.exception.status, 409)

    def test_patch_unknown_key_is_404_and_guards(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/harnesses/nope", {"hint": "x"})
        self.assertEqual(ctx.exception.status, 404)
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/harnesses/claude", {"key": "other"})
        self.assertEqual(ctx.exception.status, 400)

    def test_get_detail_lists_used_by(self) -> None:
        self.post("/api/harnesses", {"key": "mini", "argv": ["mini", "run"]})
        self.post("/api/routes", {"kind": "swarm", "key": "roy", "title": "рой",
                                  "roles": {"impl": {"harness": "mini"}}})
        status, detail = self.get("/api/harnesses/mini")
        self.assertEqual(status, 200)
        self.assertEqual(detail["used_by"], [{"route": "roy", "kind": "swarm", "role": "impl"}])

    def test_get_unknown_key_is_404(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.get("/api/harnesses/nope")
        self.assertEqual(ctx.exception.status, 404)


class CreateSwarmRouteTests(RoutesApiBase):
    """`POST /api/routes` с `kind="swarm"` — маршрут роя с ролями (listik-2gry)."""

    def body(self, **overrides) -> dict:
        payload = {"kind": "swarm", "key": "roy", "title": "Рой",
                   "roles": {"impl": {"harness": "codex"},
                             "judge": {"harness": "claude"}}}
        payload.update(overrides)
        return payload

    def test_post_creates_swarm_route(self) -> None:
        status, record = self.post("/api/routes", self.body())
        self.assertEqual(status, 201)
        self.assertEqual(record["kind"], "swarm")
        self.assertEqual(record["roles"]["impl"]["harness"], "codex")
        self.assertIsNone(record["command"])
        self.assertNotIn("harness", record)
        self.assertEqual(routes_store.get_route(self.conn, "roy")["kind"], "swarm")

    def test_post_swarm_requires_roles(self) -> None:
        for bad in (None, {}, "x"):
            with self.subTest(roles=bad):
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", self.body(roles=bad))
                self.assertEqual(ctx.exception.status, 400)
                self.assertIn("roles", ctx.exception.message)

    def test_post_swarm_rejects_command_and_harness(self) -> None:
        for field, value in (("command", ["x"]), ("harness", "codex")):
            with self.subTest(field=field):
                with self.assertRaises(server.ApiError) as ctx:
                    self.post("/api/routes", self.body(**{field: value}))
                self.assertEqual(ctx.exception.status, 400)
                self.assertIn(field, ctx.exception.message)

    def test_post_swarm_unknown_harness_is_400(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes",
                      self.body(roles={"impl": {"harness": "nope"}}))
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("nope", ctx.exception.message)

    def test_post_swarm_role_without_command_is_400(self) -> None:
        # `me` — manual-харнесс без команды; у роли своего argv тоже нет.
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", self.body(roles={"impl": {"harness": "me"}}))
        self.assertEqual(ctx.exception.status, 400)

    def test_post_swarm_role_argv_and_prompt(self) -> None:
        status, record = self.post("/api/routes", self.body(roles={
            "spec": None,
            "impl": {"harness": "codex", "argv": ["codex", "exec", "{task_id}"],
                     "prompt": "сделай {task_id}"}}))
        self.assertEqual(status, 201)
        # `null`-ячейки в расклад не пишутся — пропуск хранится отсутствием ключа.
        self.assertNotIn("spec", record["roles"])
        self.assertEqual(record["roles"]["impl"]["argv"],
                         ["codex", "exec", "{task_id}"])
        self.assertEqual(record["roles"]["impl"]["prompt"], "сделай {task_id}")

    def test_patch_swarm_roles(self) -> None:
        self.post("/api/routes", self.body())
        status, record = self.patch("/api/routes/roy",
                                    {"roles": {"judge": {"harness": "codex"}}})
        self.assertEqual(status, 200)
        self.assertEqual(record["roles"]["judge"]["harness"], "codex")

    def test_used_by_counts_swarm_route(self) -> None:
        """`used_by` харнесса видит роли маршрута `kind=swarm`."""
        self.post("/api/harnesses", {"key": "mini", "argv": ["mini", "run"]})
        self.post("/api/routes", self.body(roles={"impl": {"harness": "mini"}}))
        status, detail = self.get("/api/harnesses/mini")
        self.assertEqual(status, 200)
        self.assertIn({"route": "roy", "kind": "swarm", "role": "impl"}, detail["used_by"])


class RouteDriverGoneTests(RoutesApiBase):
    """listik-ujra: поля `driver` у маршрута нет — его нельзя ни передать, ни получить."""

    SWARM = {"kind": "swarm", "key": "roy", "title": "Рой",
             "roles": {"impl": {"harness": "codex"}}}

    def assert_bad_driver(self, method, path, body) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            method(path, body)
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertIn("driver", ctx.exception.message)

    def test_patch_with_driver_is_400_and_changes_nothing(self) -> None:
        self.write_and_import([pipeline_record()])
        self.post("/api/routes", self.SWARM)
        for key, roles in (("demo-pipeline", {"impl": {"harness": "codex"}}),
                           ("roy", {"judge": {"harness": "codex"}})):
            before = self.conn.execute("SELECT * FROM routes WHERE key = ?", (key,)).fetchone()
            for body in ({"driver": "swarm"}, {"driver": "skill"},
                         {"driver": "swarm", "roles": roles}):
                with self.subTest(key=key, body=body):
                    self.assert_bad_driver(self.patch, f"/api/routes/{key}", body)
                    after = self.conn.execute(
                        "SELECT * FROM routes WHERE key = ?", (key,)).fetchone()
                    self.assertEqual(dict(after), dict(before))

    def test_post_with_driver_is_400_and_creates_nothing(self) -> None:
        for body in ({"key": "full-xhigh", "driver": "skill"},
                     {**self.SWARM, "driver": "swarm"}):
            with self.subTest(body=body):
                self.assert_bad_driver(self.post, "/api/routes", body)
                with self.assertRaises(errors.NotFound):
                    routes_store.get_route(self.conn, body["key"])

    def test_update_route_rejects_driver(self) -> None:
        self.write_and_import([pipeline_record()])
        with self.assertRaises(ValueError) as ctx:
            routes_store.update_route(self.conn, "demo-pipeline", driver="skill")
        self.assertIn("driver", str(ctx.exception))

    def test_records_have_no_driver(self) -> None:
        self.write_and_import([pipeline_record()])
        self.post("/api/routes", self.SWARM)
        status, data = self.get("/api/routes")
        self.assertEqual(status, 200)
        self.assertTrue(data["routes"])
        for record in data["routes"]:
            self.assertNotIn("driver", record)
        for record in routes_store.list_routes(self.conn):
            self.assertNotIn("driver", record)
            self.assertNotIn("driver", routes_store.get_route(self.conn, record["key"]))


if __name__ == "__main__":
    import unittest
    unittest.main()

