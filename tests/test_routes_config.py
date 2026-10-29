"""Тесты routes.json (шаг 09, порция a; чек-лист `step-09.check-a.md`).

Настоящий `~/.config/listik/` тесты не трогают: везде явные пути во временном каталоге.
Файл `routes.json` из репозитория только читается: его структура
проверяется по самому файлу, а зашитых таблиц маршрутов в `web/src` быть не должно —
источник данных для доски после первичного ввоза — таблица `routes`.
"""
from __future__ import annotations

import argparse
import contextlib
import http.client
import importlib.machinery
import importlib.util
import io
import json
import pathlib
import re
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

from listik import embed as embed_mod
from listik import launcher
from listik import paths
from listik import routes as routes_mod
from listik import routes_store
from listik import server
from listik import skills as skills_mod
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
ROUTES_JSON = REPO_DIR / "routes.json"
ICONS_TS = REPO_DIR / "web" / "src" / "lib" / "icons.ts"
DICTIONARIES_TS = REPO_DIR / "web" / "src" / "lib" / "dictionaries.ts"
WEB_SRC = REPO_DIR / "web" / "src"
LISTIK_BIN = REPO_DIR / "bin" / "listik"

# Внутри блока `export const ROUTE_ICONS` справочника доски: `value: 'xhigh'` и
# `icon: 'route-xhigh'` — уровни маршрута и имена их иконок.
ROUTE_LEVEL_RE = re.compile(r"value: '([a-z0-9-]+)'")
ROUTE_GLYPH_RE = re.compile(r"icon: '([a-z0-9-]+)'")

# Порядок записей в routes.json — он же порядок строк формы «Новая задача».
EXPECTED_KEYS = ["lp-xhigh", "lp-high", "lp-medium", "lp-low", "lp-xlow"]

# Таблицы маршрутов, зашитые в доске до шага 09, порции c: их заменил ответ API.
REMOVED_TS_TABLES = ("PIPELINES", "DIRECT_HARNESSES")

IMPLEMENTER = [{"harness": "devin", "model": "swe-2", "effort": "max"}]


def lp_record(skill: str = "high", **over) -> dict:
    """Запись файла v2 для `lp-<skill>` с одним исполнителем."""
    record = {"key": f"lp-{skill}", "plugin": "listik-pipeline", "skill": skill,
              "roles": {"implementer": [dict(e) for e in IMPLEMENTER]}}
    record.update(over)
    return record


def document(*records) -> dict:
    return {"version": 2, "routes": list(records) if records else [lp_record()]}


class RepoRoutesFileTests(unittest.TestCase):
    """`routes.json` репозитория — формат v2, пять режимов `listik-pipeline`.

    Сверки с `web/src` больше нет: зашитые таблицы маршрутов оттуда убраны
    (шаг 09, порция c). Здесь проверяются структура образца для ввоза и отсутствие
    зашитых таблиц в исходниках доски.
    """

    def setUp(self) -> None:
        self.raw = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))

    def test_not_ignored_by_git(self) -> None:
        try:
            done = subprocess.run(["git", "check-ignore", "routes.json"], cwd=REPO_DIR,
                                  capture_output=True, text=True)
        except OSError as exc:  # git недоступен — проверять нечего
            self.skipTest(f"git недоступен: {exc}")
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)

    def test_has_five_records_in_order(self) -> None:
        self.assertEqual(self.raw["version"], 2)
        self.assertEqual([r["key"] for r in self.raw["routes"]], EXPECTED_KEYS)

    def test_records_are_exactly_v2_fields(self) -> None:
        for raw in self.raw["routes"]:
            with self.subTest(key=raw["key"]):
                self.assertEqual(list(raw), ["key", "plugin", "skill", "roles"])
                self.assertEqual(raw["plugin"], "listik-pipeline")
                self.assertEqual(raw["key"], "lp-" + raw["skill"])

    def test_validates_into_database_form(self) -> None:
        """Пункт 5 чек-листа: пять записей в форме слоя базы, роли — как в файле."""
        normalized = routes_mod.validate(self.raw)
        self.assertEqual([r["key"] for r in normalized], EXPECTED_KEYS)
        for raw, record in zip(self.raw["routes"], normalized):
            with self.subTest(key=raw["key"]):
                self.assertEqual(record["kind"], "pipeline")
                self.assertEqual(record["plugin"], "listik-pipeline")
                self.assertIs(record["visible"], True)
                self.assertIsNone(record["command"])
                self.assertEqual(record["title"],
                                 skills_mod.skill_info(raw["key"])["title"])
                self.assertEqual(record["roles"], raw["roles"])
                for cell in record["roles"].values():
                    self.assertIsInstance(cell, list)
        by_key = {r["key"]: r for r in normalized}
        self.assertEqual(by_key["lp-high"]["title"], "high")
        self.assertEqual(list(by_key["lp-high"]),
                         ["key", "kind", "plugin", "title", "hint", "visible", "icon",
                          "command", "roles"])

    def test_icons_are_the_route_levels(self) -> None:
        normalized = {r["key"]: r["icon"] for r in routes_mod.validate(self.raw)}
        self.assertEqual(normalized, {"lp-xhigh": "xhigh", "lp-high": "high",
                                      "lp-medium": "medium", "lp-low": "low",
                                      "lp-xlow": "xlow"})

    def test_validates_without_skills_on_disk(self) -> None:
        """Пункт 13a: машина без `plugins/` — файл проходит, title = имя скила, hint пуст."""
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(skills_mod, "PIPELINE_PLUGINS_DIR", pathlib.Path(tmp)):
            normalized = {r["key"]: r for r in routes_mod.validate(self.raw)}
        self.assertEqual(normalized["lp-high"]["title"], "high")
        self.assertEqual(normalized["lp-high"]["hint"], "")
        self.assertEqual(normalized["lp-high"]["icon"], "high")

    def test_web_src_has_no_embedded_route_tables(self) -> None:
        offenders: list[str] = []
        for path in sorted(WEB_SRC.rglob("*")):
            if not path.is_file() or path.suffix not in {".ts", ".vue", ".js", ".mjs"}:
                continue
            text = path.read_text(encoding="utf-8")
            for name in REMOVED_TS_TABLES:
                if name in text:
                    offenders.append(f"{path.relative_to(REPO_DIR)}: {name}")
        self.assertEqual(offenders, [])


class ValidateTests(unittest.TestCase):
    """Правила записи файла v2: каждое — отдельный случай, текст ошибки целиком."""

    def error(self, document_obj) -> str:
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate(document_obj)
        self.assertIsInstance(ctx.exception, ValueError)
        return str(ctx.exception)

    # -- version и корень ---------------------------------------------------

    def test_version_must_be_exactly_two(self) -> None:
        for bad in (1, "2", True, 2.0, None):
            with self.subTest(version=bad):
                self.assertEqual(self.error({"version": bad, "routes": []}),
                                 "version: должна быть ровно целая 2")

    def test_version_missing(self) -> None:
        self.assertEqual(self.error({"routes": []}), "version: обязательное поле отсутствует")

    def test_root_extra_field(self) -> None:
        self.assertEqual(self.error({"version": 2, "routes": [], "visble": True}),
                         "visble: лишнее поле")

    def test_root_not_object(self) -> None:
        self.assertIn("routes.json", self.error(["routes"]))

    def test_routes_not_list(self) -> None:
        self.assertIn("routes", self.error({"version": 2, "routes": {}}))

    def test_empty_routes_ok(self) -> None:
        self.assertEqual(routes_mod.validate({"version": 2, "routes": []}), [])

    # -- поля записи ---------------------------------------------------------

    def test_v1_fields_are_extra(self) -> None:
        """Пункт 9: поля записи v1 и старых бэкапов — `лишнее поле`."""
        for name, value in (("kind", "pipeline"), ("title", "x"), ("hint", ""),
                            ("visible", True), ("icon", "high"), ("command", ["true"]),
                            ("driver", "skill"), ("strip", {}), ("visble", True)):
            with self.subTest(field=name):
                self.assertEqual(self.error(document(lp_record(**{name: value}))),
                                 f"routes[0].{name}: лишнее поле")

    def test_extra_field_goes_before_other_errors(self) -> None:
        record = lp_record(key="Bad", plugin=None, kind="pipeline")
        self.assertEqual(self.error(document(record)), "routes[0].kind: лишнее поле")

    def test_required_fields(self) -> None:
        for name in ("key", "plugin", "skill", "roles"):
            record = lp_record()
            del record[name]
            with self.subTest(field=name):
                self.assertEqual(self.error(document(record)),
                                 f"routes[0].{name}: обязательное поле отсутствует")

    def test_key_format(self) -> None:
        for bad in ("Lp-high", "-x", "", 5):
            with self.subTest(key=bad):
                self.assertEqual(self.error(document(lp_record(key=bad))),
                                 "routes[0].key: ключ должен подходить под ^[a-z0-9][a-z0-9-]*$")

    def test_plugin_not_listed(self) -> None:
        """Пункт 7: `pipeline-full`, `null` и прочее — один текст."""
        for bad in ("pipeline-full", None, "pipeline-cc", "", 1):
            with self.subTest(plugin=bad):
                self.assertEqual(self.error(document(lp_record(plugin=bad))),
                                 "routes[0].plugin: допустимы listik-pipeline")

    def test_skill_format(self) -> None:
        for bad in ("High", "", "a/b", None, 5):
            with self.subTest(skill=bad):
                self.assertEqual(
                    self.error(document(lp_record(skill="high") | {"skill": bad})),
                    "routes[0].skill: имя каталога скила под ^[a-z0-9][a-z0-9-]*$")

    def test_role_skill_is_not_a_mode(self) -> None:
        for skill in ("writer", "critic", "implementer", "lens", "judge"):
            with self.subTest(skill=skill):
                self.assertEqual(self.error(document(lp_record(skill))),
                                 f"routes[0].skill: '{skill}' — скил роли, а не режим")

    def test_key_must_be_lp_skill(self) -> None:
        """Пункт 8: ключ не `lp-<skill>` — один текст, без текста `check_plugin`."""
        expected = ("routes[0].key: у плагина listik-pipeline ключ — lp-<skill> "
                    "(ожидается 'lp-high')")
        for key in ("full-high", "lp-low", "high", "lp-high-x"):
            with self.subTest(key=key):
                message = self.error(document(lp_record("high", key=key)))
                self.assertEqual(message, expected)
                self.assertNotIn("начинается с", message)

    def test_plugin_error_goes_before_key_mismatch(self) -> None:
        record = lp_record("high", key="full-high", plugin="pipeline-full")
        self.assertEqual(self.error(document(record)),
                         "routes[0].plugin: допустимы listik-pipeline")

    def test_key_duplicate(self) -> None:
        self.assertEqual(self.error(document(lp_record(), lp_record())),
                         "routes[1].key: дубликат ключа 'lp-high'")

    def test_record_not_object(self) -> None:
        self.assertEqual(self.error(document("lp-high")),
                         "routes[0]: запись должна быть объектом")

    # -- положительные случаи -----------------------------------------------

    def test_record_in_database_form(self) -> None:
        info = skills_mod.skill_info("lp-high")
        self.assertEqual(routes_mod.validate(document(lp_record()))[0], {
            "key": "lp-high", "kind": "pipeline", "plugin": "listik-pipeline",
            "title": info["title"], "hint": info["hint"], "visible": True, "icon": "high",
            "command": None, "roles": {"implementer": IMPLEMENTER}})

    def test_unknown_skill_on_disk_gets_skill_as_title(self) -> None:
        record = routes_mod.validate(document(lp_record("nope")))[0]
        self.assertEqual((record["title"], record["hint"], record["icon"]), ("nope", "", None))

    def test_empty_roles_in_file_ok(self) -> None:
        """Пункт 12: `{}` в файле допустим — так бэкап пишет конвейер без ролей."""
        self.assertEqual(routes_mod.validate(document(lp_record(roles={})))[0]["roles"], {})

    def test_warnings_stay_empty(self) -> None:
        warnings: list[str] = []
        routes_mod.validate(json.loads(ROUTES_JSON.read_text(encoding="utf-8")), warnings)
        self.assertEqual(warnings, [])

    # -- command (записи базы) и подстановки ---------------------------------

    def command_error(self, value) -> str:
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate_command(value, "command")
        return str(ctx.exception)

    def test_command_empty(self) -> None:
        self.assertIn("command", self.command_error([]))

    def test_command_empty_element(self) -> None:
        self.assertIn("command[1]", self.command_error(["x", ""]))

    def test_command_unknown_placeholder(self) -> None:
        message = self.command_error(["{taskid}"])
        self.assertIn("command[0]", message)
        self.assertIn("{taskid}", message)

    def test_command_double_braces(self) -> None:
        self.assertIn("command[0]", self.command_error(["{{task_id}}"]))

    def test_command_stray_brace(self) -> None:
        self.assertIn("command[0]", self.command_error(["a}"]))

    def test_title_is_unknown_and_lists_all_placeholders(self) -> None:
        message = self.command_error(["{title}"])
        self.assertIn("неизвестная подстановка", message)
        self.assertEqual(routes_mod.PLACEHOLDERS,
                         ("task_id", "project", "route", "cwd", "worktree", "branch",
                          "stage", "role", "harness", "plugin", "skill"))
        for name in routes_mod.PLACEHOLDERS:
            self.assertIn("{" + name + "}", message)

    def test_valid_command_with_placeholders(self) -> None:
        command = ["run", "{task_id}", "--p={project}", "{route}", "{cwd}",
                   "{worktree}", "{branch}"]
        self.assertEqual(routes_mod.validate_command(command, "command"), command)

    # -- уровень по ключу ------------------------------------------------------

    def test_icon_fallback_ignores_kind(self) -> None:
        """Первое правило — уровень из префикса ключа, у обоих видов (listik-ar8v)."""
        cases = (("xhigh-pipeline", "xhigh"), ("low-pipeline", "low"),
                 ("opus-pipeline", None), ("pidi", None))
        for key, level in cases:
            with self.subTest(key=key):
                self.assertEqual(routes_mod.fallback_icon(key), level)

    def test_icon_fallback_second_rule_only_for_pipeline(self) -> None:
        cases = (("lp-high", "high"), ("lp-xlow", "xlow"), ("lp-nano", None),
                 ("xhigh-pipeline", "xhigh"), ("pidi", None))
        for key, level in cases:
            with self.subTest(key=key):
                self.assertEqual(routes_mod.fallback_icon(key), level)
        self.assertIsNone(routes_mod.fallback_icon("x-high", "swarm"))
        self.assertEqual(routes_mod.fallback_icon("x-high", "pipeline"), "high")


def board_icon_names() -> set[str]:
    """Ключи объекта icons.ts для проверки справочника доски, не валидатор бэкенда."""
    text = ICONS_TS.read_text(encoding="utf-8")
    return {quoted or bare for quoted, bare in
            re.findall(r"^\s*(?:'([a-z0-9-]+)'|([a-z][a-z0-9-]*)):\s*\{", text, re.M)}


class PluginFieldTests(unittest.TestCase):
    """`check_plugin` — поле `plugin` записи в базе."""

    def check_error(self, kind, key, value) -> str:
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.check_plugin(kind, key, value, "")
        return str(ctx.exception)

    def test_none_is_none(self) -> None:
        self.assertIsNone(routes_mod.check_plugin("pipeline", "my-flow", None, ""))
        self.assertIsNone(routes_mod.check_plugin("swarm", "dsh", None, ""))

    def test_good_plugin_kept(self) -> None:
        self.assertEqual(routes_mod.check_plugin("pipeline", "lp-high", "listik-pipeline", ""),
                         "listik-pipeline")

    def test_only_for_pipeline(self) -> None:
        self.assertEqual(self.check_error("swarm", "lp-high", "listik-pipeline"),
                         "plugin: поле только у kind=pipeline")

    def test_unknown_plugin(self) -> None:
        self.assertEqual(self.check_error("pipeline", "full-high", "pipeline-full"),
                         "plugin: допустимы listik-pipeline")

    def test_key_must_start_with_lp(self) -> None:
        for key in ("full-high", "lp-"):
            with self.subTest(key=key):
                self.assertEqual(self.check_error("pipeline", key, "listik-pipeline"),
                                 "key: у плагина listik-pipeline ключ начинается с 'lp-'")

    def test_where_prefixes_path(self) -> None:
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.check_plugin("swarm", "x", "listik-pipeline", "routes[2]")
        self.assertEqual(str(ctx.exception), "routes[2].plugin: поле только у kind=pipeline")

    def test_record_fields(self) -> None:
        self.assertEqual(routes_mod.RECORD_FIELDS, ("key", "plugin", "skill", "roles"))

    def test_substitute_plugin_and_skill(self) -> None:
        self.assertEqual(launcher._substitute("/{plugin}:{skill}",
                                              {"plugin": "listik-pipeline", "skill": "high"}),
                         "/listik-pipeline:high")
        # Без значений (запуск роя, запись без плагина) — пустые строки.
        self.assertEqual(launcher._substitute("/{plugin}:{skill}", {}), "/:")


class RouteIconDictionaryTests(unittest.TestCase):
    """Уровни `icon` и их иконки: сервер и справочник доски не разъезжаются.

    Проверка читает `web/src/lib/dictionaries.ts` и `icons.ts` без сборки доски.
    Уровень, который сервер разрешает в `routes.json`,
    но которого нет в `ROUTE_ICONS`, доска молча показала бы без иконки.
    """

    def setUp(self) -> None:
        text = DICTIONARIES_TS.read_text(encoding="utf-8")
        self.assertTrue("export const ROUTE_ICONS" in text, "в dictionaries.ts нет ROUTE_ICONS")
        self.block = text.split("export const ROUTE_ICONS", 1)[1].split("\n]", 1)[0]

    def test_levels_match_the_backend(self) -> None:
        self.assertEqual(ROUTE_LEVEL_RE.findall(self.block), list(routes_mod.ROUTE_ICONS))

    def test_glyphs_are_distinct_and_known_to_icons_ts(self) -> None:
        glyphs = ROUTE_GLYPH_RE.findall(self.block)
        self.assertEqual(len(glyphs), len(routes_mod.ROUTE_ICONS))
        self.assertEqual(len(set(glyphs)), len(glyphs))
        known = board_icon_names()
        self.assertTrue(known)
        for glyph in glyphs:
            self.assertIn(glyph, known)


class RouteBoardWarningsTests(unittest.TestCase):
    """`warnings` ответа `GET /api/routes` доезжают до доски (listik-itg8).

    Читаются исходники доски без сборки — так же, как в `RouteIconDictionaryTests`.
    """

    def setUp(self) -> None:
        self.types = (REPO_DIR / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
        self.store = (REPO_DIR / "web" / "src" / "store" / "listik.ts").read_text(encoding="utf-8")
        self.modal = (REPO_DIR / "web" / "src" / "components"
                      / "NewTaskModal.vue").read_text(encoding="utf-8")

    def test_board_shows_file_warnings(self) -> None:
        """`warnings` ответа доезжают до формы «Новая задача»."""
        self.assertIn("warnings", self.types)
        self.assertIn("routesWarnings", self.store)
        self.assertIn("routesWarnings", self.modal)


class FileLoadTests(TempDbTestCase):
    """Проверка и разбор файла только для ввоза, без состояния процесса."""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.tmp_path / "source-routes.json"
        self.source.write_text(ROUTES_JSON.read_text(encoding="utf-8"), encoding="utf-8")

    # -- load ----------------------------------------------------------------

    def test_load_missing_file(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            state = routes_mod.load(self.tmp_path / "нет.json")
        self.assertFalse(state.ok)
        self.assertTrue(state.error)
        self.assertEqual(state.routes, [])
        self.assertIn("нужен ты", err.getvalue())

    def test_load_directory(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            state = routes_mod.load(self.tmp_path)
        self.assertFalse(state.ok)
        self.assertTrue(state.error)
        self.assertEqual(state.routes, [])
        self.assertIn("нужен ты", err.getvalue())

    def test_load_broken_json(self) -> None:
        broken = self.tmp_path / "broken.json"
        broken.write_text("{", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            state = routes_mod.load(broken)
        self.assertFalse(state.ok)
        self.assertTrue(state.error)
        self.assertEqual(state.routes, [])
        self.assertIn("нужен ты", err.getvalue())

    def test_load_schema_error(self) -> None:
        bad = self.tmp_path / "bad-schema.json"
        bad.write_text(json.dumps({"version": 1, "routes": []}), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(bad)
        self.assertFalse(state.ok)
        self.assertEqual(state.error, "version: должна быть ровно целая 2")

    def test_load_valid_file(self) -> None:
        state = routes_mod.load(self.source)
        self.assertTrue(state.ok)
        self.assertIsNone(state.error)
        self.assertEqual(state.path, str(self.source))
        self.assertEqual(len(state.routes), len(EXPECTED_KEYS))
        self.assertEqual(sorted(state.by_key), sorted(r["key"] for r in state.routes))
        self.assertEqual(state.warnings, [])

    def test_file_change_after_import_does_not_change_state(self) -> None:
        routes_store.import_file(self.conn, self.source)
        self.source.write_text("{ битый", encoding="utf-8")
        current = routes_mod.state(self.conn)
        self.assertTrue(current.ok)
        self.assertEqual(len(current.routes), len(EXPECTED_KEYS))
        self.assertEqual(current.path, str(paths.DB_PATH))
        self.assertEqual(current.warnings, [])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(routes_store.ensure_imported(self.conn).get("skipped"))
        self.assertEqual(len(routes_mod.state(self.conn).routes), len(EXPECTED_KEYS))

    def test_database_update_is_visible_immediately(self) -> None:
        routes_store.import_file(self.conn, self.source)
        self.conn.execute("UPDATE routes SET title = ? WHERE key = 'lp-xlow'", ("Проверка",))
        self.conn.commit()
        self.assertEqual(routes_mod.state(self.conn).by_key["lp-xlow"]["title"], "Проверка")

    def test_database_error_is_reported(self) -> None:
        with mock.patch.object(routes_store, "list_routes",
                               side_effect=sqlite3.DatabaseError("база недоступна")):
            result = routes_mod.state(self.conn)
        self.assertFalse(result.ok)
        self.assertIn("база недоступна", result.error)
        self.assertEqual(result.routes, [])
        self.assertEqual(result.path, str(paths.DB_PATH))


class RoutesApiTests(TempDbTestCase):
    """Пункты 11–14 чек-листа: GET /api/routes и routes в /api/health."""

    TOKEN = "routes-test-token"

    def setUp(self) -> None:
        super().setUp()
        self._config_path = self.tmp_path / "config.toml"
        self._config_path.write_text(f'[auth]\ntoken = "{self.TOKEN}"\n', encoding="utf-8")
        self._config_patch = mock.patch.object(paths, "CONFIG_PATH", self._config_path)
        self._config_patch.start()
        self.addCleanup(self._config_patch.stop)
        # База — временная: handle() в начале зовёт get_conn().
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)

        self.target = self.tmp_path / "runtime" / "routes.json"
        self.srv = server.make_server("127.0.0.1", 0, quiet=True)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self._stop_server)

    def _stop_server(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()

    def _get(self, path: str, token: str | None = None) -> tuple[int, dict]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def _init_from_repo(self):
        return routes_store.import_file(self.conn, ROUTES_JSON)

    # -- handle() ------------------------------------------------------------

    def test_handle_routes_includes_command(self) -> None:
        """Порция c (listik-8jgz): `command` больше не вырезается из ответа."""
        self._init_from_repo()
        status, data = server.handle("GET", "/api/routes", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIsNone(data["error"])
        self.assertEqual(data["path"], str(paths.DB_PATH))
        self.assertEqual(len(data["routes"]), len(EXPECTED_KEYS))
        for record in data["routes"]:
            self.assertIn("command", record)
            self.assertNotIn("strip", record)
            self.assertIn("icon", record)
        icons = {record["key"]: record["icon"] for record in data["routes"]}
        self.assertEqual(icons["lp-xhigh"], "xhigh")
        self.assertEqual(icons["lp-medium"], "medium")
        self.assertEqual({record["key"]: record["skill"] for record in data["routes"]}["lp-high"],
                         "high")

    def test_handle_routes_has_empty_warnings_for_valid_file(self) -> None:
        self._init_from_repo()
        status, data = server.handle("GET", "/api/routes", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(data["warnings"], [])

    def test_handle_routes_reports_database_error(self) -> None:
        with mock.patch.object(routes_store, "list_routes",
                               side_effect=sqlite3.DatabaseError("ошибка чтения")):
            status, data = server.handle("GET", "/api/routes", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertFalse(data["ok"])
        self.assertIn("ошибка чтения", data["error"])
        self.assertEqual(data["routes"], [])

    # -- живой сервер --------------------------------------------------------

    def test_routes_without_token_is_401(self) -> None:
        self._init_from_repo()
        status, payload = self._get("/api/routes")
        self.assertEqual(status, 401)
        self.assertFalse(payload.get("ok"))

    def test_routes_with_token_returns_all_records(self) -> None:
        self._init_from_repo()
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["routes"]), len(EXPECTED_KEYS))
        self.assertEqual([r["key"] for r in data["routes"]], EXPECTED_KEYS)
        self.assertTrue(all("command" in r for r in data["routes"]))

    def test_invisible_records_are_returned(self) -> None:
        routes_store.upsert_route(self.conn, {
            "key": "hidden-pipeline", "kind": "pipeline", "title": "Демо", "visible": False,
            "roles": {}, "command": ["run", "{task_id}"]})
        routes_store.create_route(self.conn, key="dsh", kind="swarm", title="dsh",
                                  roles={"impl": {"harness": "dsh"}})
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        records = payload["data"]["routes"]
        self.assertEqual([r["key"] for r in records], ["hidden-pipeline", "dsh"])
        self.assertFalse(records[0]["visible"])
        self.assertEqual(records[0]["command"], ["run", "{task_id}"])

    def test_file_change_after_import_is_not_reflected(self) -> None:
        self._init_from_repo()
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text("{ битый", encoding="utf-8")
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertTrue(payload["data"]["ok"])
        self.assertEqual(len(payload["data"]["routes"]), len(EXPECTED_KEYS))

    def test_database_change_reaches_live_http_without_restart(self) -> None:
        self._init_from_repo()
        self.conn.execute("UPDATE routes SET title = ? WHERE key = ?", ("Проверка", "lp-xlow"))
        self.conn.commit()
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(next(r for r in payload["data"]["routes"] if r["key"] == "lp-xlow")
                         ["title"], "Проверка")

    def test_health_reports_routes(self) -> None:
        self._init_from_repo()
        with mock.patch.object(embed_mod, "health", return_value={"ok": False, "model": "тест"}):
            status, payload = self._get("/api/health", token=self.TOKEN)
        self.assertEqual(status, 200)
        state = payload["data"]["routes"]
        self.assertTrue(state["ok"])
        self.assertIsNone(state["error"])
        self.assertEqual(state["path"], str(paths.DB_PATH))
        self.assertEqual(state["count"], len(EXPECTED_KEYS))

    def test_health_database_error_is_reported(self) -> None:
        with mock.patch.object(routes_store, "list_routes",
                               side_effect=sqlite3.DatabaseError("ошибка чтения")), \
             mock.patch.object(embed_mod, "health", return_value={"ok": False, "model": "тест"}):
            status, payload = self._get("/api/health", token=self.TOKEN)
        self.assertEqual(status, 200)
        state = payload["data"]["routes"]
        self.assertFalse(state["ok"])
        self.assertIn("ошибка чтения", state["error"])
        self.assertEqual(state["count"], 0)

    def test_health_without_token_has_no_routes(self) -> None:
        self._init_from_repo()
        status, payload = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertNotIn("routes", payload["data"])


def _load_cli():
    """Загрузить `bin/listik` как модуль (у файла нет расширения .py)."""
    loader = importlib.machinery.SourceFileLoader("listik_cli_under_test", str(LISTIK_BIN))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class StatusCommandTests(TempDbTestCase):
    """Пункт 15 чек-листа: `listik status` берёт маршруты из /api/health."""

    def setUp(self) -> None:
        super().setUp()
        self._config_path = self.tmp_path / "config.toml"
        self._config_path.write_text('[auth]\ntoken = "status-test"\n', encoding="utf-8")
        self._config_patch = mock.patch.object(paths, "CONFIG_PATH", self._config_path)
        self._config_patch.start()
        self.addCleanup(self._config_patch.stop)
        self.cli = _load_cli()

    def _run_status(self, health) -> tuple[int, str]:
        args = argparse.Namespace(host=None, port=None, json=False)
        out = io.StringIO()
        with mock.patch.object(self.cli.client, "health", return_value=health), \
             mock.patch.object(server, "read_pid", return_value=None), \
             contextlib.redirect_stdout(out):
            code = self.cli.cmd_status(args)
        return code, out.getvalue()

    def test_ok_line(self) -> None:
        code, text = self._run_status({
            "status": "ok", "authed": True, "counts": {"tasks": 0, "comments": 0,
                                                       "events": 0, "embeddings": 0},
            "db": "/tmp/listik.db", "embed": {"model": "bge-m3", "ok": False},
            "routes": {"ok": True, "error": None, "path": "/tmp/listik.db", "count": 11},
        })
        self.assertEqual(code, 0)
        self.assertIn("маршруты: 11 (таблица routes)", text)

    def test_error_line(self) -> None:
        code, text = self._run_status({
            "status": "ok", "authed": True, "counts": {"tasks": 0, "comments": 0,
                                                       "events": 0, "embeddings": 0},
            "db": "/tmp/listik.db", "embed": {"model": "bge-m3", "ok": False},
            "routes": {"ok": False, "error": "битый JSON", "path": "/tmp/r.json", "count": 0},
        })
        self.assertEqual(code, 0)
        self.assertIn("маршруты: ОШИБКА битый JSON — автостарт выключен", text)

    def test_no_line_when_token_rejected(self) -> None:
        code, text = self._run_status({"status": "ok", "authed": False})
        self.assertEqual(code, 0)
        self.assertNotIn("маршруты", text)

    def test_no_line_when_server_down(self) -> None:
        code, text = self._run_status(None)
        self.assertEqual(code, 1)
        self.assertNotIn("маршруты", text)

    def test_cmd_status_does_not_read_routes_file(self) -> None:
        text = LISTIK_BIN.read_text(encoding="utf-8")
        start = text.index("def cmd_status")
        end = text.index("\ndef ", start + 1)
        block = text[start:end]
        for forbidden in ("listik.routes", "import routes", "read_text", "json.load", "open("):
            self.assertNotIn(forbidden, block)


if __name__ == "__main__":
    unittest.main()
