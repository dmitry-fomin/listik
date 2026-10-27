"""Правка расклада ролей v2 по HTTP и справочник для редактора ролей (listik-syu8; listik-906l).

Как в `test_routes_api.py`: через `server.handle(...)` на временной базе. Маршруты
готовятся ввозом `routes.json` репозитория (`import_sample`, формат v2) и
`routes_store.create_route` — свои хелперы здесь, без записей v1 из `test_routes_api`.
"""
from __future__ import annotations

import json
from unittest import mock

from listik import errors
from listik import routes as routes_mod
from listik import routes_store
from listik import server
from listik import skills as skills_mod
from tests.test_routes_api import ROUTES_JSON, RoutesApiBase

CATALOGUE = ["grok:delegate", "pi:pi-delegate"]

#: Раскладки из `routes.json` репозитория: принимаются и файлом, и HTTP.
SHIPPED = {r["key"]: r["roles"]
           for r in json.loads(ROUTES_JSON.read_text(encoding="utf-8"))["routes"]}
ROLES = SHIPPED["lp-low"]
OLD_CELL = {"provider": "glm", "label": "x", "title": "y"}
NOT_OBJECT = "roles: должен быть объектом с ролями writer/critic/implementer/lens/judge"


class RolesApiBase(RoutesApiBase):
    def route(self, key: str = "lp-high") -> dict:
        return routes_store.get_route(self.conn, key)

    def add_swarm(self, key: str = "dsh") -> dict:
        return routes_store.create_route(self.conn, key=key, kind="swarm", title=key,
                                         roles={"impl": {"harness": "dsh"}})

    def rejected(self, method: str, path: str, body: dict) -> server.ApiError:
        with mock.patch.object(server, "publish") as publish:
            with self.assertRaises(server.ApiError) as ctx:
                server.handle(method, path, {}, body, authed=True)
        publish.assert_not_called()
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        return ctx.exception


class PatchRolesTests(RolesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.import_sample()
        self.add_swarm()

    def test_patch_roles_written_and_published(self) -> None:
        before = self.route()
        with mock.patch.object(server, "publish") as publish:
            status, data = server.handle("PATCH", "/api/routes/lp-high", {},
                                         {"roles": ROLES}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(data["roles"], ROLES)
        self.assertEqual(data["skill"], "high")
        publish.assert_called_once_with("route", {"key": "lp-high", "action": "updated"})
        _, listing = self.get("/api/routes")
        record = next(r for r in listing["routes"] if r["key"] == "lp-high")
        self.assertEqual(record["roles"], ROLES)
        self.assertNotEqual(self.route(), before)

    def test_shipped_roles_patch_back(self) -> None:
        """Пункт 24: раскладка из `routes.json` проходит PATCH как есть."""
        for key, roles in SHIPPED.items():
            with self.subTest(key=key):
                status, data = self.patch("/api/routes/lp-high", {"roles": roles})
                self.assertEqual(status, 200)
                self.assertEqual(data["roles"], roles)

    def test_swarm_route_rejects_pipeline_roles(self) -> None:
        before = self.route("dsh")
        error = self.rejected("PATCH", "/api/routes/dsh", {"roles": ROLES})
        self.assertIn("roles.", error.message)
        self.assertEqual(self.route("dsh"), before)

    def test_invalid_roles_rejected_without_write(self) -> None:
        bad = {
            "old cell": ({"writer": OLD_CELL}, "roles.writer: непустой массив исполнителей"),
            "old role": ({"spec": [{"harness": "devin", "effort": "max"}]},
                         "roles.spec: неизвестная роль"),
            "claude without agent": ({"writer": [{"harness": "claude", "model": "opus",
                                                  "effort": "high"}]},
                                     "roles.writer[0].agent: обязательное поле отсутствует"),
            "pi channel outside catalog": ({"critic": [{"harness": "pi", "channel": "nope",
                                                        "effort": "high"}]},
                                           "roles.critic[0].channel: у харнесса 'pi' нет канала"),
            "empty": ({}, "roles: нужна хотя бы одна роль"),
            "list": ([], NOT_OBJECT),
            "null": (None, NOT_OBJECT),
        }
        before = self.route()
        for name, (roles, text) in bad.items():
            with self.subTest(case=name):
                error = self.rejected("PATCH", "/api/routes/lp-high", {"roles": roles})
                self.assertIn(text, error.message)
                self.assertEqual(self.route(), before)

    def test_roles_not_object_message(self) -> None:
        before = self.route()
        error = self.rejected("PATCH", "/api/routes/lp-high", {"roles": "x"})
        self.assertEqual(error.message, NOT_OBJECT)
        self.assertEqual(self.route(), before)

    def test_patched_roles_pass_file_validation(self) -> None:
        """Роли, принятые HTTP-путём, принимает и проверка файла поставки."""
        self.patch("/api/routes/lp-high", {"roles": ROLES})
        document = {"version": 2, "routes": [
            {"key": "lp-high", "plugin": "listik-pipeline", "skill": "high",
             "roles": self.route()["roles"]}]}
        self.assertEqual(routes_mod.validate(document)[0]["roles"], ROLES)

    def test_no_launcher_check_left(self) -> None:
        self.assertFalse(hasattr(server, "_check_role_launchers"))


class PostRolesTests(RolesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.add_swarm()

    def assert_no_row(self, key: str = "lp-high") -> None:
        self.assertIsNone(self.conn.execute(
            "SELECT 1 FROM routes WHERE key = ?", (key,)).fetchone())

    def test_post_without_roles(self) -> None:
        """Пункт 22: конвейер заводится скрытым, без команды и без ролей."""
        status, data = self.post("/api/routes", {"key": "lp-high"})
        self.assertEqual(status, 201)
        self.assertEqual(data["plugin"], "listik-pipeline")
        self.assertEqual(data["skill"], "high")
        self.assertEqual(data["title"], skills_mod.skill_info("lp-high")["title"])
        self.assertEqual(data["icon"], "high")
        self.assertIs(data["visible"], False)
        self.assertIsNone(data["command"])
        self.assertEqual(data["roles"], {})

    def test_post_with_null_roles(self) -> None:
        status, data = self.post("/api/routes", {"key": "lp-low", "roles": None})
        self.assertEqual(status, 201)
        self.assertEqual(data["roles"], {})

    def test_post_with_roles(self) -> None:
        with mock.patch.object(server, "publish") as publish:
            status, data = server.handle("POST", "/api/routes", {},
                                         {"key": "lp-high", "roles": ROLES}, authed=True)
        self.assertEqual(status, 201)
        self.assertEqual(data["roles"], ROLES)
        publish.assert_called_once_with("route", {"key": "lp-high", "action": "created"})

    def test_post_existing_key_is_conflict(self) -> None:
        self.post("/api/routes", {"key": "lp-high"})
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "lp-high"})
        self.assertEqual(ctx.exception.status, 409)

    def test_post_unknown_skill_rejected(self) -> None:
        """Пункт 23: только скилы-режимы `listik-pipeline`."""
        for key in ("full-high", "lp-writer", "lp-nope"):
            with self.subTest(key=key):
                error = self.rejected("POST", "/api/routes", {"key": key})
                self.assertEqual(error.message,
                                 f"скила {key!r} нет среди plugins/listik-pipeline/skills "
                                 "(ключ lp-<скил>: lp-high → plugins/listik-pipeline/skills/high)")
                self.assert_no_row(key)

    def test_post_with_empty_roles_rejected(self) -> None:
        error = self.rejected("POST", "/api/routes", {"key": "lp-high", "roles": {}})
        self.assertEqual(error.message, "roles: нужна хотя бы одна роль")
        self.assert_no_row()

    def test_post_with_invalid_roles_rejected(self) -> None:
        """Пункт 24: путь до поля исполнителя в тексте, записи нет."""
        roles = {"writer": [{"harness": "claude", "effort": "high"}]}
        error = self.rejected("POST", "/api/routes", {"key": "lp-high", "roles": roles})
        self.assertIn("roles.writer[0].agent", error.message)
        self.assert_no_row()

    def test_post_roles_not_object(self) -> None:
        error = self.rejected("POST", "/api/routes", {"key": "lp-high", "roles": "x"})
        self.assertEqual(error.message, NOT_OBJECT)
        self.assert_no_row()

    def test_post_unknown_channel_rejected(self) -> None:
        roles = {"critic": [{"harness": "pi", "channel": "nope", "effort": "high"}]}
        error = self.rejected("POST", "/api/routes", {"key": "lp-high", "roles": roles})
        self.assertEqual(error.message, "roles.critic[0].channel: у харнесса 'pi' нет канала 'nope'")
        self.assert_no_row()

    def test_post_with_unknown_field_still_rejected(self) -> None:
        error = self.rejected("POST", "/api/routes", {"key": "lp-high", "kind": "direct"})
        self.assertIn("kind", error.message)


class LaunchersEndpointTests(RolesApiBase):
    def catalogue(self, keys: list[str]) -> None:
        """Подменить каталог запускаторов на заданный список ключей."""
        for name, value in (("launcher_keys", lambda keys=keys: list(keys)),
                            ("launchers_available", lambda keys=keys: bool(keys))):
            patch = mock.patch.object(skills_mod, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_get_launchers(self) -> None:
        """Пункт 25: харнессы и роли конвейера v2, поля `providers` нет."""
        self.catalogue(CATALOGUE)
        infos = [{"key": key, "plugin": key.split(":")[0], "skill": key.split(":")[1],
                  "title": key, "hint": "", "provider": "glm", "skill_path": None}
                 for key in CATALOGUE]
        with mock.patch.object(skills_mod, "launchers", return_value=infos):
            status, data = self.get("/api/routes/launchers")
        self.assertEqual(status, 200)
        self.assertEqual(list(data), ["skills_available", "launchers", "harnesses", "roles"])
        self.assertTrue(data["skills_available"])
        self.assertEqual([item["key"] for item in data["launchers"]], CATALOGUE)
        self.assertEqual(data["harnesses"], ["claude", "pi", "devin", "codex", "grok"])
        self.assertEqual(data["roles"], ["writer", "critic", "implementer", "lens", "judge"])

    def test_get_launchers_without_catalogue(self) -> None:
        self.catalogue([])
        with mock.patch.object(skills_mod, "launchers", return_value=[]):
            status, data = self.get("/api/routes/launchers")
        self.assertEqual(status, 200)
        self.assertFalse(data["skills_available"])
        self.assertEqual(data["launchers"], [])

    def test_post_launchers_not_allowed(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes/launchers", {})
        self.assertEqual(ctx.exception.status, 405)
