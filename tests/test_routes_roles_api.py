"""Правка расклада ролей по HTTP и каталог запускаторов (listik-syu8, порция b).

Как в `test_routes_api.py`: через `server.handle(...)` на временной базе. Каталог
запускаторов везде подменяется (`skills.launcher_keys`/`launchers_available`) — состав
`plugins/` конкретной машины тестом не проверяется.
"""
from __future__ import annotations

from unittest import mock

from listik import errors
from listik import routes as routes_mod
from listik import routes_store
from listik import server
from listik import skills as skills_mod
from tests.test_routes_api import RoutesApiBase, direct_record, pipeline_record

CATALOGUE = ["grok:delegate", "pi:pi-delegate"]

ROLES = {
    "impl": {"provider": "glm", "label": "GLM", "title": "GLM 5.3 Flash",
             "skill": "pi:pi-delegate", "params": {"channel": "glm", "thinking": "high"}},
    "judge": {"provider": "grok", "label": "high", "title": "Grok 4.6 · high",
              "skill": "grok:delegate"},
}


class RolesApiBase(RoutesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.catalogue(CATALOGUE)

    def catalogue(self, keys: list[str]) -> None:
        """Подменить каталог запускаторов на заданный список ключей."""
        for name, value in (("launcher_keys", lambda keys=keys: list(keys)),
                            ("launchers_available", lambda keys=keys: bool(keys))):
            patch = mock.patch.object(skills_mod, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def route(self, key: str = "demo-pipeline") -> dict:
        return routes_store.get_route(self.conn, key)


class PatchRolesTests(RolesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.write_and_import([pipeline_record(key="high-pipeline"), direct_record()])

    def test_patch_roles_written_and_published(self) -> None:
        before = self.route("high-pipeline")
        with mock.patch.object(server, "publish") as publish:
            status, data = server.handle("PATCH", "/api/routes/high-pipeline", {},
                                         {"roles": ROLES}, authed=True)
        self.assertEqual(status, 200)
        self.assertIsInstance(data["roles"], dict)
        self.assertEqual(data["roles"], ROLES)
        publish.assert_called_once_with("route", {"key": "high-pipeline", "action": "updated"})
        # то же самое видно в общем чтении маршрутов, и updated_at сдвинулся
        _, listing = self.get("/api/routes")
        record = next(r for r in listing["routes"] if r["key"] == "high-pipeline")
        self.assertEqual(record["roles"], ROLES)
        self.assertNotEqual(self.route("high-pipeline"), before)

    def test_params_types_survive_roundtrip(self) -> None:
        roles = {"impl": {"provider": "glm", "label": "GLM", "title": "GLM",
                          "skill": "pi:pi-delegate",
                          "params": {"channel": "glm", "web": False, "tokens": 12}}}
        _, data = self.patch("/api/routes/high-pipeline", {"roles": roles})
        params = data["roles"]["impl"]["params"]
        self.assertIs(params["web"], False)
        self.assertEqual(params["tokens"], 12)
        self.assertEqual(params["channel"], "glm")

    def test_direct_route_rejects_roles(self) -> None:
        before = self.route("dsh")
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/dsh", {"roles": ROLES})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertIn("pipeline", ctx.exception.message)
        self.assertEqual(self.route("dsh"), before)

    def test_invalid_roles_rejected_without_write(self) -> None:
        bad = {
            "unknown role": {"reviewer": {"provider": "glm", "label": "x", "title": "y"}},
            "no label": {"impl": {"provider": "glm", "title": "y"}},
            "bad provider": {"impl": {"provider": "нет", "label": "x", "title": "y"}},
            "bad skill": {"impl": {"provider": "glm", "label": "x", "title": "y",
                                   "skill": "Плохой Ключ"}},
            "params list": {"impl": {"provider": "glm", "label": "x", "title": "y",
                                     "skill": "pi:pi-delegate", "params": {"a": [1]}}},
            "params without skill": {"impl": {"provider": "glm", "label": "x", "title": "y",
                                              "params": {"a": "b"}}},
            "empty": {},
            "list": [],
            "string": "x",
            "null": None,
        }
        before = self.route("high-pipeline")
        for name, roles in bad.items():
            with self.subTest(case=name):
                with mock.patch.object(server, "publish") as publish:
                    with self.assertRaises(server.ApiError) as ctx:
                        server.handle("PATCH", "/api/routes/high-pipeline", {},
                                      {"roles": roles}, authed=True)
                self.assertEqual(ctx.exception.status, 400)
                self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
                publish.assert_not_called()
                self.assertEqual(self.route("high-pipeline"), before)

    def test_unknown_launcher_rejected(self) -> None:
        roles = {"impl": {"provider": "glm", "label": "x", "title": "y",
                          "skill": "no-such:launcher"}}
        with self.assertRaises(server.ApiError) as ctx:
            self.patch("/api/routes/high-pipeline", {"roles": roles})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("roles.impl.skill", ctx.exception.message)
        for key in CATALOGUE:
            self.assertIn(key, ctx.exception.message)

    def test_no_catalogue_no_launcher_check(self) -> None:
        """Установка без plugins/: наличие скила не проверяется, правка проходит."""
        self.catalogue([])
        roles = {"impl": {"provider": "glm", "label": "x", "title": "y",
                          "skill": "any:launcher"}}
        status, data = self.patch("/api/routes/high-pipeline", {"roles": roles})
        self.assertEqual(status, 200)
        self.assertEqual(data["roles"]["impl"]["skill"], "any:launcher")

    def test_patched_roles_pass_file_validation(self) -> None:
        """Роли, принятые HTTP-путём, принимает и проверка файла поставки."""
        self.patch("/api/routes/high-pipeline", {"roles": ROLES})
        self.assertEqual(self.route("high-pipeline")["roles"], ROLES)
        document = {"version": 1, "routes": [
            {"key": "high-pipeline", "kind": "pipeline", "title": "Демо", "hint": "подсказка",
             "visible": True, "roles": ROLES}]}
        self.assertEqual(routes_mod.validate(document)[0]["roles"], ROLES)


class PostRolesTests(RolesApiBase):
    def setUp(self) -> None:
        super().setUp()
        self.write_and_import([direct_record()])

    def test_post_with_roles(self) -> None:
        with mock.patch.object(server, "publish") as publish:
            status, data = server.handle("POST", "/api/routes", {},
                                         {"key": "high-pipeline", "roles": ROLES}, authed=True)
        self.assertEqual(status, 201)
        self.assertIsInstance(data["roles"], dict)
        self.assertEqual(data["roles"], ROLES)
        publish.assert_called_once_with("route", {"key": "high-pipeline", "action": "created"})

    def test_post_without_roles_and_with_null(self) -> None:
        status, data = self.post("/api/routes", {"key": "high-pipeline"})
        self.assertEqual(status, 201)
        self.assertEqual(data["roles"], {})
        status, data = self.post("/api/routes", {"key": "low-pipeline", "roles": None})
        self.assertEqual(status, 201)
        self.assertEqual(data["roles"], {})

    def test_post_with_empty_roles_rejected(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "high-pipeline", "roles": {}})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)

    def test_post_with_unknown_launcher_rejected(self) -> None:
        roles = {"impl": {"provider": "glm", "label": "x", "title": "y",
                          "skill": "no-such:launcher"}}
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "high-pipeline", "roles": roles})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("roles.impl.skill", ctx.exception.message)

    def test_post_with_unknown_field_still_rejected(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "high-pipeline", "kind": "direct"})
        self.assertEqual(ctx.exception.status, 400)

    def test_post_with_invalid_roles_rejected(self) -> None:
        roles = {"impl": {"provider": "нет", "label": "x", "title": "y"}}
        with self.assertRaises(server.ApiError) as ctx:
            self.post("/api/routes", {"key": "high-pipeline", "roles": roles})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)


class LaunchersEndpointTests(RolesApiBase):
    def test_get_launchers(self) -> None:
        infos = [{"key": key, "plugin": key.split(":")[0], "skill": key.split(":")[1],
                  "title": key, "hint": "", "provider": "glm", "skill_path": None}
                 for key in CATALOGUE]
        with mock.patch.object(skills_mod, "launchers", return_value=infos):
            status, data = self.get("/api/routes/launchers")
        self.assertEqual(status, 200)
        self.assertTrue(data["skills_available"])
        self.assertEqual([item["key"] for item in data["launchers"]], CATALOGUE)
        self.assertEqual(data["providers"], list(routes_mod.PROVIDERS))
        self.assertEqual(data["roles"], list(routes_mod.ROLE_KEYS))
        for item in data["launchers"]:
            for field in ("key", "title", "hint", "provider", "skill_path"):
                self.assertIn(field, item)

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
