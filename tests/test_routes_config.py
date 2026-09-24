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
import threading
import unittest
from unittest import mock

from listik import embed as embed_mod
from listik import paths
from listik import routes as routes_mod
from listik import routes_store
from listik import server
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

DIRECT_KEYS = ["pi-deepseek", "grok", "codex"]

# Порядок записей в routes.json — он же порядок строк формы «Новая задача».
EXPECTED_KEYS = [
    "xhigh-pipeline", "high-pipeline", "medium-pipeline", "low-pipeline", "xlow-pipeline",
    "nano-pipeline", "devin-pipeline", "cross-pipeline", "inherit-pipeline", "opus-single-pipeline", "opus-sonnet-pipeline",
    "universal-pipeline", "feature-pipeline",
    *DIRECT_KEYS,
]

# Таблицы маршрутов, зашитые в доске до шага 09, порции c: их заменил ответ API.
REMOVED_TS_TABLES = ("PIPELINES", "DIRECT_HARNESSES")


def pipeline_record() -> dict:
    return {
        "key": "demo-pipeline",
        "kind": "pipeline",
        "title": "Демо",
        "hint": "подсказка",
        "visible": True,
        "roles": {"impl": {"provider": "claude", "label": "Opus", "title": "Opus · medium"}},
    }


def direct_record() -> dict:
    return {"key": "dsh", "kind": "direct", "harness": "dsh", "title": "dsh",
            "hint": "", "visible": True}


def document(*records) -> dict:
    return {"version": 1, "routes": list(records) if records else [pipeline_record()]}


class RepoRoutesFileTests(unittest.TestCase):
    """Пункты 1–3 чек-листа: файл в репозитории, перенос данных, проверка.

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

    def test_has_sixteen_records_in_order(self) -> None:
        self.assertEqual(self.raw["version"], 1)
        self.assertEqual(len(self.raw["routes"]), 16)
        self.assertEqual([r["key"] for r in self.raw["routes"]], EXPECTED_KEYS)

    def test_validates_and_every_record_is_visible(self) -> None:
        normalized = routes_mod.validate(self.raw)
        self.assertEqual([r["key"] for r in normalized], EXPECTED_KEYS)
        self.assertTrue(all(r["visible"] is True for r in normalized))
        self.assertTrue(all(r["visible"] is True for r in self.raw["routes"]))

    def test_kinds_match_the_table(self) -> None:
        kinds = [r["kind"] for r in self.raw["routes"]]
        self.assertEqual(kinds[:13], ["pipeline"] * 13)
        self.assertEqual(kinds[13:], ["direct"] * 3)

    def test_direct_records_are_exact(self) -> None:
        normalized = routes_mod.validate(self.raw)
        raw_by_key = {record["key"]: record for record in self.raw["routes"]}
        for key, got in zip(DIRECT_KEYS, normalized[13:]):
            self.assertEqual(got["title"], key)
            self.assertEqual(got, {"key": key, "kind": "direct", "title": key, "hint": "",
                                   "visible": True, "icon": "direct", "harness": key,
                                   "command": raw_by_key[key]["command"]})

    def test_icons_are_the_route_levels(self) -> None:
        normalized = {r["key"]: r["icon"] for r in routes_mod.validate(self.raw)}
        self.assertEqual(normalized["xhigh-pipeline"], "xhigh")
        self.assertEqual(normalized["high-pipeline"], "high")
        self.assertEqual(normalized["medium-pipeline"], "medium")
        self.assertEqual(normalized["low-pipeline"], "low")
        self.assertEqual(normalized["xlow-pipeline"], "xlow")
        # Уровень nano из ключа не выводится — стоит явно, на ступени xlow.
        self.assertEqual(normalized["nano-pipeline"], "xlow")
        for key in DIRECT_KEYS:
            self.assertEqual(normalized[key], "direct")
        # У пресетов без уровня поля нет — иконка не выдумывается.
        for key in ("inherit-pipeline", "opus-single-pipeline", "opus-sonnet-pipeline",
                    "feature-pipeline"):
            self.assertIsNone(normalized[key])

    def test_repo_every_record_has_command(self) -> None:
        """В образце у каждой записи есть argv автостарта: конвейер — claude, прямой — свой харнесс."""
        normalized = {record["key"]: record for record in routes_mod.validate(self.raw)}
        pipeline_cmd = None
        for raw in self.raw["routes"]:
            self.assertIn("command", raw, raw["key"])
            self.assertEqual(normalized[raw["key"]]["command"], raw["command"])
            if raw["kind"] == "pipeline":
                self.assertEqual(raw["command"][:3],
                                 ["claude", "--dangerously-skip-permissions", "-p"], raw["key"])
                self.assertIn("/feature-pipeline:{route}", raw["command"][-1], raw["key"])
                if pipeline_cmd is None:
                    pipeline_cmd = raw["command"]
                else:
                    self.assertEqual(raw["command"], pipeline_cmd, raw["key"])
            else:
                self.assertNotEqual(raw["command"][0], "claude", raw["key"])

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
    """Пункт 4–5 чек-листа: каждое правило формата — отдельный случай."""

    def check_error(self, document_obj, *path_parts) -> str:
        with self.assertRaises(routes_mod.RoutesError) as ctx:
            routes_mod.validate(document_obj)
        self.assertIsInstance(ctx.exception, ValueError)
        message = str(ctx.exception)
        for part in path_parts:
            self.assertIn(part, message)
        return message

    # -- key ---------------------------------------------------------------

    def test_key_duplicate(self) -> None:
        second = {**direct_record(), "key": "demo-pipeline"}
        self.check_error(document(pipeline_record(), second), "routes[1].key", "дубликат")

    def test_key_uppercase(self) -> None:
        self.check_error(document({**pipeline_record(), "key": "Demo"}), "routes[0].key")

    # -- kind --------------------------------------------------------------

    def test_kind_unknown(self) -> None:
        self.check_error(document({**pipeline_record(), "kind": "foo"}), "routes[0].kind")

    # -- visible -----------------------------------------------------------

    def test_visible_string(self) -> None:
        self.check_error(document({**pipeline_record(), "visible": "true"}), "routes[0].visible")

    def test_visible_number(self) -> None:
        self.check_error(document({**pipeline_record(), "visible": 1}), "routes[0].visible")

    def test_visible_missing(self) -> None:
        record = pipeline_record()
        del record["visible"]
        self.check_error(document(record), "routes[0].visible")

    # -- icon --------------------------------------------------------------

    def validate_with_warnings(self, document_obj) -> tuple[list[dict], list[str]]:
        """`validate` вместе с собранными предупреждениями (listik-itg8)."""
        warnings: list[str] = []
        return routes_mod.validate(document_obj, warnings), warnings

    def test_icon_unknown_level_warns_and_falls_back_to_key(self) -> None:
        """Опечатка в `icon` не отменяет файл: уровень берётся из ключа, а не из поля."""
        record = {**pipeline_record(), "key": "xhigh-pipeline", "icon": "xhihg"}
        normalized, warnings = self.validate_with_warnings(document(record))
        self.assertEqual(normalized[0]["icon"], "xhigh")
        self.assertEqual(len(warnings), 1)
        self.assertIn("routes[0].icon", warnings[0])
        self.assertIn("'xhihg'", warnings[0])
        self.assertIn("'xhigh'", warnings[0])
        self.assertEqual(normalized[0]["icon_error"], warnings[0])

    def test_icon_unknown_level_without_fallback_has_no_icon(self) -> None:
        normalized, warnings = self.validate_with_warnings(
            document({**pipeline_record(), "icon": "xhihg"}))
        self.assertIsNone(normalized[0]["icon"])
        self.assertIn("routes[0].icon", warnings[0])
        self.assertEqual(normalized[0]["icon_error"], warnings[0])

    def test_icon_unknown_level_keeps_other_records(self) -> None:
        """Битая запись не мешает остальным: файл валиден целиком (приёмка 1)."""
        bad = {**pipeline_record(), "key": "bad-pipeline", "icon": "xhihg"}
        good = {**direct_record(), "icon": "direct"}
        normalized, warnings = self.validate_with_warnings(document(bad, good))
        self.assertEqual([r["key"] for r in normalized], ["bad-pipeline", "dsh"])
        self.assertEqual(normalized[1], {"key": "dsh", "kind": "direct", "title": "dsh",
                                         "hint": "", "visible": True, "icon": "direct",
                                         "harness": "dsh", "command": None})
        self.assertEqual(len(warnings), 1)
        self.assertIn("routes[0].icon", warnings[0])

    def test_icon_unknown_levels_accumulate_warnings(self) -> None:
        first = {**pipeline_record(), "key": "xhigh-pipeline", "icon": "xhihg"}
        second = {**direct_record(), "icon": "dirct"}
        normalized, warnings = self.validate_with_warnings(document(first, second))
        self.assertEqual([r["icon"] for r in normalized], ["xhigh", "direct"])
        self.assertEqual(len(warnings), 2)
        self.assertIn("routes[0].icon", warnings[0])
        self.assertIn("routes[1].icon", warnings[1])

    def test_icon_not_string_warns(self) -> None:
        normalized, warnings = self.validate_with_warnings(
            document({**pipeline_record(), "key": "high-pipeline", "icon": 4}))
        self.assertEqual(normalized[0]["icon"], "high")
        self.assertIn("routes[0].icon", warnings[0])
        self.assertIn("4", warnings[0])

    def test_icon_null_is_not_a_level(self) -> None:
        normalized, warnings = self.validate_with_warnings(
            document({**pipeline_record(), "key": "high-pipeline", "icon": None}))
        self.assertEqual(normalized[0]["icon"], "high")
        self.assertIn("routes[0].icon", warnings[0])
        self.assertIn("None", warnings[0])

    def test_icon_explicit_level_passes(self) -> None:
        record = {**pipeline_record(), "key": "feature-pipeline", "icon": "high"}
        normalized, warnings = self.validate_with_warnings(document(record))
        self.assertEqual(normalized[0]["icon"], "high")
        self.assertEqual(warnings, [])
        self.assertNotIn("icon_error", normalized[0])

    def test_icon_falls_back_to_key_prefix(self) -> None:
        for key, level in (("xhigh-pipeline", "xhigh"), ("high-pipeline", "high"),
                           ("medium-pipeline", "medium"), ("low-pipeline", "low"),
                           ("xlow-pipeline", "xlow")):
            with self.subTest(key=key):
                normalized = routes_mod.validate(document({**pipeline_record(), "key": key}))
                self.assertEqual(normalized[0]["icon"], level)

    def test_icon_falls_back_for_direct_by_kind(self) -> None:
        self.assertEqual(routes_mod.validate(document(direct_record()))[0]["icon"], "direct")

    def test_icon_fallback_has_nothing_for_unknown_prefix(self) -> None:
        for key in ("feature-pipeline", "inherit-pipeline", "demo-pipeline"):
            with self.subTest(key=key):
                normalized = routes_mod.validate(document({**pipeline_record(), "key": key}))
                self.assertIsNone(normalized[0]["icon"])

    # -- title -------------------------------------------------------------

    def test_title_empty(self) -> None:
        self.check_error(document({**pipeline_record(), "title": ""}), "routes[0].title")

    # -- version -----------------------------------------------------------

    def test_version_missing(self) -> None:
        self.check_error({"routes": [pipeline_record()]}, "version")

    def test_version_string(self) -> None:
        self.check_error({"version": "1", "routes": [pipeline_record()]}, "version")

    def test_version_two(self) -> None:
        self.check_error({"version": 2, "routes": [pipeline_record()]}, "version")

    def test_version_bool_is_not_one(self) -> None:
        self.check_error({"version": True, "routes": [pipeline_record()]}, "version")

    # -- roles -------------------------------------------------------------

    def test_pipeline_without_roles(self) -> None:
        record = pipeline_record()
        del record["roles"]
        self.check_error(document(record), "routes[0].roles")

    def test_roles_empty(self) -> None:
        self.check_error(document({**pipeline_record(), "roles": {}}), "routes[0].roles")

    def test_role_unknown(self) -> None:
        record = pipeline_record()
        record["roles"] = {**record["roles"], "reviewer": {"provider": "claude",
                                                          "label": "x", "title": "y"}}
        self.check_error(document(record), "routes[0].roles.reviewer")

    def test_role_without_label(self) -> None:
        cell = {"provider": "claude", "title": "Opus"}
        self.check_error(document({**pipeline_record(), "roles": {"impl": cell}}),
                         "routes[0].roles.impl.label")

    def test_provider_unknown(self) -> None:
        cell = {"provider": "gpt", "label": "x", "title": "y"}
        self.check_error(document({**pipeline_record(), "roles": {"impl": cell}}),
                         "routes[0].roles.impl.provider")

    # -- harness -----------------------------------------------------------

    def test_pipeline_with_harness(self) -> None:
        self.check_error(document({**pipeline_record(), "harness": "dsh"}), "routes[0].harness")

    def test_direct_with_roles(self) -> None:
        self.check_error(document({**direct_record(), "roles": pipeline_record()["roles"]}),
                         "routes[0].roles")

    def test_direct_without_harness(self) -> None:
        record = direct_record()
        del record["harness"]
        self.check_error(document(record), "routes[0].harness")

    def test_harness_any_key(self) -> None:
        # listik-2gry: harness — любой ключ по форме (держатель agent:<key>),
        # списком HARNESSES прямой маршрут больше не ограничен.
        record = routes_mod.validate(document({**direct_record(), "harness": "human"}))[0]
        self.assertEqual(record["harness"], "human")
        self.check_error(document({**direct_record(), "harness": "Not A Key"}),
                         "routes[0].harness")

    # -- лишние поля --------------------------------------------------------

    def test_extra_field_typo(self) -> None:
        self.check_error(document({**pipeline_record(), "visble": True}), "routes[0].visble")

    def test_root_extra_field(self) -> None:
        self.check_error({"version": 1, "routes": [], "visble": True}, "visble")

    def test_root_not_object(self) -> None:
        self.check_error(["routes"], "routes.json")

    def test_routes_not_list(self) -> None:
        self.check_error({"version": 1, "routes": {}}, "routes")

    # -- command -----------------------------------------------------------

    def test_command_empty(self) -> None:
        self.check_error(document({**pipeline_record(), "command": []}), "routes[0].command")

    def test_command_empty_element(self) -> None:
        self.check_error(document({**pipeline_record(), "command": ["x", ""]}),
                         "routes[0].command[1]")

    def test_command_unknown_placeholder(self) -> None:
        self.check_error(document({**pipeline_record(), "command": ["{taskid}"]}),
                         "routes[0].command[0]")

    def test_command_double_braces(self) -> None:
        self.check_error(document({**pipeline_record(), "command": ["{{task_id}}"]}),
                         "routes[0].command[0]")

    def test_command_stray_brace(self) -> None:
        self.check_error(document({**pipeline_record(), "command": ["a}"]}),
                         "routes[0].command[0]")

    def test_command_unknown_placeholder_message_names_it(self) -> None:
        message = self.check_error(document({**pipeline_record(), "command": ["{taskid}"]}),
                                   "routes[0].command[0]")
        self.assertIn("{taskid}", message)

    def test_title_is_unknown_and_lists_all_placeholders(self) -> None:
        message = self.check_error(document({**pipeline_record(), "command": ["{title}"]}),
                                   "routes[0].command[0]", "{title}")
        self.assertIn("неизвестная подстановка", message)
        self.assertEqual(routes_mod.PLACEHOLDERS,
                         ("task_id", "project", "route", "cwd", "worktree", "branch",
                          "stage", "role", "harness"))
        for name in routes_mod.PLACEHOLDERS:
            self.assertIn("{" + name + "}", message)

    # -- положительные случаи -----------------------------------------------

    def test_valid_command_with_placeholders(self) -> None:
        record = {**pipeline_record(),
                  "command": ["run", "{task_id}", "--p={project}", "{route}", "{cwd}",
                              "{worktree}", "{branch}"]}
        normalized = routes_mod.validate(document(record))
        self.assertEqual(normalized[0]["command"], record["command"])

    def test_legacy_strip_is_ignored_without_validation(self) -> None:
        record = {**pipeline_record(), "strip": {"glyph": "Invalid Glyph", "label": 123}}
        warnings: list[str] = []
        normalized = routes_mod.validate(document(record), warnings)
        self.assertNotIn("strip", normalized[0])
        self.assertEqual(warnings, [])

    def test_valid_direct_record(self) -> None:
        normalized = routes_mod.validate(document(direct_record()))
        self.assertEqual(normalized[0]["command"], None)
        self.assertNotIn("roles", normalized[0])
        self.assertNotIn("strip", normalized[0])

    def test_hint_defaults_to_empty_string(self) -> None:
        record = pipeline_record()
        del record["hint"]
        self.assertEqual(routes_mod.validate(document(record))[0]["hint"], "")

    def test_all_four_roles_pass(self) -> None:
        record = pipeline_record()
        record["roles"] = {
            "spec": {"provider": "claude", "label": "Fable", "title": "Fable · low"},
            "critic": {"provider": "glm", "label": "GLM", "title": "GLM 5.3 Flash по HTTP"},
            "impl": {"provider": "deepseek", "label": "dsh", "title": "DeepSeek Harness"},
            "judge": {"provider": "grok", "label": "low", "title": "Grok 4.6 · low"},
        }
        normalized = routes_mod.validate(document(record))
        self.assertEqual(list(normalized[0]["roles"]), ["spec", "critic", "impl", "judge"])


def board_icon_names() -> set[str]:
    """Ключи объекта icons.ts для проверки справочника доски, не валидатор бэкенда."""
    text = ICONS_TS.read_text(encoding="utf-8")
    return {quoted or bare for quoted, bare in
            re.findall(r"^\s*(?:'([a-z0-9-]+)'|([a-z][a-z0-9-]*)):\s*\{", text, re.M)}


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


class RouteIconUnknownBoardTests(unittest.TestCase):
    """Маркер недоступной иконки: сервер и доска не разъезжаются (listik-itg8).

    Неизвестный `icon` записи сервер отдаёт полем `icon_error` в `GET /api/routes`,
    а доска рисует по нему серый кружок с крестиком из `ROUTE_ICON_UNKNOWN`. Здесь
    читаются исходники доски — так же, как в `RouteIconDictionaryTests`, без сборки.
    """

    def setUp(self) -> None:
        self.dictionaries_path = DICTIONARIES_TS
        text = DICTIONARIES_TS.read_text(encoding="utf-8")
        marker = "export const ROUTE_ICON_UNKNOWN"
        self.assertTrue(marker in text, "в dictionaries.ts нет ROUTE_ICON_UNKNOWN")
        self.unknown_block = text.split(marker, 1)[1].split("\n}", 1)[0]
        self.route_icon = (REPO_DIR / "web" / "src" / "components" / "marks"
                           / "RouteIcon.vue").read_text(encoding="utf-8")
        self.types = (REPO_DIR / "web" / "src" / "api" / "types.ts").read_text(encoding="utf-8")
        self.store = (REPO_DIR / "web" / "src" / "store" / "listik.ts").read_text(encoding="utf-8")
        self.modal = (REPO_DIR / "web" / "src" / "components"
                      / "NewTaskModal.vue").read_text(encoding="utf-8")

    def test_marker_glyph_exists_in_icons_ts(self) -> None:
        glyphs = ROUTE_GLYPH_RE.findall(self.unknown_block)
        self.assertEqual(len(glyphs), 1, self.unknown_block)
        self.assertIn(glyphs[0], board_icon_names())

    def test_marker_is_not_a_route_level(self) -> None:
        """Крестик не уровень маршрута: в `ROUTE_ICONS` его быть не должно."""
        self.assertEqual(ROUTE_LEVEL_RE.findall(self.unknown_block), [])
        block = (self.dictionaries_path.read_text(encoding="utf-8")
                 .split("export const ROUTE_ICONS", 1)[1].split("\n]", 1)[0])
        self.assertEqual(ROUTE_LEVEL_RE.findall(block), list(routes_mod.ROUTE_ICONS))
        marker_glyphs = ROUTE_GLYPH_RE.findall(self.unknown_block)
        for glyph in ROUTE_GLYPH_RE.findall(block):
            self.assertNotIn(glyph, marker_glyphs)

    def test_component_marks_the_record_by_icon_error(self) -> None:
        """Крестик рисуется по `icon_error` ответа, а не по отсутствию уровня вообще."""
        self.assertIn("ROUTE_ICON_UNKNOWN", self.route_icon)
        self.assertIn("icon_error", self.route_icon)
        self.assertIn("icon_error", self.types)

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
        bad.write_text(json.dumps({"version": 2, "routes": []}), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            state = routes_mod.load(bad)
        self.assertFalse(state.ok)
        self.assertIn("version", state.error)

    def test_load_valid_file(self) -> None:
        state = routes_mod.load(self.source)
        self.assertTrue(state.ok)
        self.assertIsNone(state.error)
        self.assertEqual(state.path, str(self.source))
        self.assertEqual(len(state.routes), 16)
        self.assertEqual(sorted(state.by_key), sorted(r["key"] for r in state.routes))
        self.assertEqual(state.warnings, [])

    def _write_routes(self, records, name: str = "custom-routes.json") -> pathlib.Path:
        path = self.tmp_path / name
        path.write_text(json.dumps({"version": 1, "routes": list(records)}, ensure_ascii=False),
                        encoding="utf-8")
        return path

    def test_load_unknown_icon_warns_but_keeps_the_file(self) -> None:
        """Опечатка в `icon` — предупреждение, а не ошибка файла (приёмка 1).

        Автостарт остаётся включённым, остальные записи работают, у битой записи
        посчитан фолбэк по ключу и есть `icon_error` для доски.
        """
        bad = {**pipeline_record(), "key": "xhigh-pipeline", "icon": "xhihg"}
        path = self._write_routes([bad, direct_record()])
        with contextlib.redirect_stderr(io.StringIO()) as err:
            state = routes_mod.load(path)
        self.assertTrue(state.ok)
        self.assertIsNone(state.error)
        self.assertEqual([r["key"] for r in state.routes], ["xhigh-pipeline", "dsh"])
        self.assertEqual([r["icon"] for r in state.routes], ["xhigh", "direct"])
        self.assertEqual(sorted(state.by_key), ["dsh", "xhigh-pipeline"])
        self.assertEqual(len(state.warnings), 1)
        self.assertIn("routes[0].icon", state.warnings[0])
        self.assertEqual(state.routes[0]["icon_error"], state.warnings[0])
        self.assertNotIn("icon_error", state.routes[1])
        text = err.getvalue()
        self.assertIn("предупреждение", text)
        self.assertIn("routes[0].icon", text)
        # В отличие от ошибки файла: автостарт не выключен и человек не нужен.
        self.assertNotIn("нужен ты", text)
        self.assertNotIn("автостарт выключен", text)

    def test_file_change_after_import_does_not_change_state(self) -> None:
        routes_store.import_file(self.conn, self.source)
        self.source.write_text("{ битый", encoding="utf-8")
        current = routes_mod.state(self.conn)
        self.assertTrue(current.ok)
        self.assertEqual(len(current.routes), 16)
        self.assertEqual(current.path, str(paths.DB_PATH))
        self.assertEqual(current.warnings, [])
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(routes_store.ensure_imported(self.conn).get("skipped"))
        self.assertEqual(len(routes_mod.state(self.conn).routes), 16)

    def test_database_update_is_visible_immediately(self) -> None:
        routes_store.import_file(self.conn, self.source)
        self.conn.execute("UPDATE routes SET title = ? WHERE key = 'pi-deepseek'", ("Проверка",))
        self.conn.commit()
        self.assertEqual(routes_mod.state(self.conn).by_key["pi-deepseek"]["title"], "Проверка")

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
        self.assertEqual(len(data["routes"]), 16)
        for record in data["routes"]:
            self.assertIn("command", record)
            self.assertNotIn("strip", record)
            self.assertIn("icon", record)
        icons = {record["key"]: record["icon"] for record in data["routes"]}
        self.assertEqual(icons["xhigh-pipeline"], "xhigh")
        self.assertEqual(icons["medium-pipeline"], "medium")
        self.assertEqual(icons["pi-deepseek"], "direct")
        self.assertIsNone(icons["feature-pipeline"])

    def test_routes_with_bad_icon_import_fallback_and_keep_all_records(self) -> None:
        """Предупреждение при ввозе; база хранит фолбэк, без файловых предупреждений."""
        bad = {**pipeline_record(), "key": "xhigh-pipeline", "icon": "xhihg"}
        plain = {**pipeline_record(), "key": "feature-pipeline"}
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps({"version": 1, "routes": [bad, plain, direct_record()]},
                                          ensure_ascii=False), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            report = routes_store.import_file(self.conn, self.target)
        self.assertEqual(report["imported"], 3)
        self.assertIn("предупреждение", err.getvalue())
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        data = payload["data"]
        self.assertTrue(data["ok"])
        self.assertIsNone(data["error"])
        self.assertEqual(data["warnings"], [])
        records = {record["key"]: record for record in data["routes"]}
        self.assertEqual(sorted(records), ["dsh", "feature-pipeline", "xhigh-pipeline"])
        self.assertEqual(records["xhigh-pipeline"]["icon"], "xhigh")
        self.assertNotIn("icon_error", records["xhigh-pipeline"])
        self.assertIsNone(records["feature-pipeline"]["icon"])
        self.assertNotIn("icon_error", records["feature-pipeline"])
        self.assertEqual(records["dsh"]["icon"], "direct")
        for record in records.values():
            self.assertIn("command", record)
            self.assertIsNone(record["command"])

    def test_handle_routes_has_empty_warnings_for_valid_file(self) -> None:
        self._init_from_repo()
        status, data = server.handle("GET", "/api/routes", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(data["warnings"], [])

    def test_icon_falls_back_to_key_for_old_runtime_copy(self) -> None:
        """Рабочая копия без поля `icon` (создана до его появления) — уровни по ключу."""
        records = [
            {**pipeline_record(), "key": "low-pipeline"},
            {**pipeline_record(), "key": "feature-pipeline"},
            direct_record(),
        ]
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps({"version": 1, "routes": records}, ensure_ascii=False),
                               encoding="utf-8")
        routes_store.import_file(self.conn, self.target)
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        icons = {record["key"]: record["icon"] for record in payload["data"]["routes"]}
        self.assertEqual(icons, {"low-pipeline": "low", "feature-pipeline": None, "dsh": "direct"})

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
        self.assertEqual(len(data["routes"]), 16)
        self.assertEqual([r["key"] for r in data["routes"]][-3:], DIRECT_KEYS)
        self.assertTrue(all("command" in r for r in data["routes"]))

    def test_invisible_records_are_returned(self) -> None:
        hidden = {**pipeline_record(), "key": "hidden-pipeline", "visible": False,
                  "command": ["run", "{task_id}"]}
        self.target.parent.mkdir(parents=True)
        self.target.write_text(json.dumps({"version": 1, "routes": [hidden, direct_record()]},
                                          ensure_ascii=False), encoding="utf-8")
        routes_store.import_file(self.conn, self.target)
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
        self.assertEqual(len(payload["data"]["routes"]), 16)

    def test_database_change_reaches_live_http_without_restart(self) -> None:
        self._init_from_repo()
        self.conn.execute("UPDATE routes SET title = ? WHERE key = ?", ("Проверка", "pi-deepseek"))
        self.conn.commit()
        status, payload = self._get("/api/routes", token=self.TOKEN)
        self.assertEqual(status, 200)
        self.assertEqual(next(r for r in payload["data"]["routes"] if r["key"] == "pi-deepseek")
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
        self.assertEqual(state["count"], 16)

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
