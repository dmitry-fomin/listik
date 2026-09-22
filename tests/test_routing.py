"""Tests for project routing overrides (шаг 04, порция b).

Хранение переопределений переходов/окна возврата у проекта (`config.validate_routing`,
`store.update_project(..., routing=...)`), их показ (`routing_effective`/`routing_source`)
и то, что устаревшие ключи (`default_process`, `harnesses`) молча игнорируются.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from listik import config as config_mod
from listik import db as db_mod
from listik import paths
from listik import store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


class RoutingTests(TempDbTestCase):
    def setUp(self) -> None:
        super().setUp()
        # Файл не существует — config.load() падает на DEFAULTS, реальный
        # config.toml в корне репозитория ни разу не читается и не пишется.
        self._config_path = self.tmp_path / "config.toml"
        self._config_patch = mock.patch.object(paths, "CONFIG_PATH", self._config_path)
        self._config_patch.start()
        self.addCleanup(self._config_patch.stop)
        store.upsert_project(self.conn, "demo")

    # -- 1. без переопределений ------------------------------------------------

    def test_no_overrides_uses_defaults(self) -> None:
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertEqual(demo["routing_source"], "default")
        self.assertIsNone(demo["routing"])
        self.assertEqual(demo["routing_effective"]["transitions"],
                         config_mod.DEFAULTS["routing"]["transitions"])

    # -- 2. переопределение из базы ---------------------------------------------

    def test_db_override_is_per_key_merged(self) -> None:
        store.update_project(self.conn, "demo",
                             routing={"transitions": {"s3-impl:s4-judge": "handoff"}})
        effective = config_mod.routing("demo", conn=self.conn)
        self.assertEqual(effective["transitions"]["s3-impl:s4-judge"], "handoff")
        # Остальные переходы не задеты поключевым слиянием.
        self.assertEqual(effective["transitions"]["s1-spec:s2-review"],
                         config_mod.DEFAULTS["routing"]["transitions"]["s1-spec:s2-review"])
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertEqual(demo["routing_source"], "db")

    # -- 3. переопределение из TOML, и приоритет базы над TOML ------------------

    def test_toml_override_and_db_wins_over_toml(self) -> None:
        self._config_path.write_text(
            '[routing.projects.demo.transitions]\n'
            '"s3-impl:s4-judge" = "handoff"\n',
            encoding="utf-8",
        )
        effective = config_mod.routing("demo", conn=self.conn)
        self.assertEqual(effective["transitions"]["s3-impl:s4-judge"], "handoff")
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertEqual(demo["routing_source"], "config")

        store.update_project(self.conn, "demo",
                             routing={"transitions": {"s3-impl:s4-judge": "sticky"}})
        effective2 = config_mod.routing("demo", conn=self.conn)
        self.assertEqual(effective2["transitions"]["s3-impl:s4-judge"], "sticky")
        projects2 = store.list_all_projects(self.conn)
        demo2 = next(p for p in projects2 if p["slug"] == "demo")
        self.assertEqual(demo2["routing_source"], "config+db")

    # -- 4. claim больше не ограничен списком харнессов этапа --------------------

    def test_claim_accepts_any_harness(self) -> None:
        """`routing.harnesses` убран: claim не сверяет исполнителя со списком
        этапа — харнесс решается маршрутом/каталогом, а не конфигом."""
        task = store.create_task(self.conn, title="реализация", project="demo",
                                 stage="s3-impl")
        out = store.claim(self.conn, task["id"], holder="pi-glm", harness="pi-glm")
        self.assertEqual(out["holder"], "pi-glm")

    # -- 7. validate_routing -------------------------------------------------------

    def test_validate_routing_accepts_valid_shapes(self) -> None:
        example = {
            "transitions": {"s1-spec:s2-review": "sticky", "s4-judge:done": "handoff"},
            "return_window_hours": 12,
        }
        out = config_mod.validate_routing(example)
        self.assertEqual(out, example)
        self.assertEqual(config_mod.validate_routing({}), {})

    def test_validate_routing_ignores_legacy_keys(self) -> None:
        """Убранные ключи не должны ломать старые вызовы и старые сохранённые
        значения: `default_process` (listik-sqh6) и `harnesses` молча
        выкидываются, а не превращаются в «неизвестный ключ»."""
        out = config_mod.validate_routing(
            {"default_process": ["s1-spec", "s3-impl"],
             "harnesses": {"s3-impl": ["dsh"]},
             "return_window_hours": 5}
        )
        self.assertEqual(out, {"return_window_hours": 5})
        self.assertEqual(config_mod.validate_routing({"default_process": []}), {})
        self.assertEqual(config_mod.validate_routing({"harnesses": "not-a-dict"}), {})
        # Перезапись проекта со старым ключом проходит и не оставляет его в базе.
        saved = store.update_project(self.conn, "demo",
                                     routing={"harnesses": {"s3-impl": ["dsh"]}})
        self.assertIsNone(saved["routing"])
        self.assertNotIn("harnesses", saved["routing_effective"])
        self.assertEqual(saved["routing_source"], "default")
        # Настоящий неизвестный ключ по-прежнему отвергается.
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"bad": 1})

    def test_legacy_keys_in_db_override_are_ignored(self) -> None:
        """Старое переопределение проекта в базе читается: устаревшие ключи не
        видны ни в `routing`, ни в `routing_effective`, и сами по себе не
        считаются переопределением."""
        self.conn.execute("UPDATE projects SET routing = ? WHERE slug = 'demo'",
                          (json.dumps({"default_process": ["s1-spec"],
                                       "harnesses": {"s3-impl": ["dsh"]}}),))
        self.conn.commit()
        demo = next(p for p in store.list_all_projects(self.conn) if p["slug"] == "demo")
        self.assertIsNone(demo["routing"])
        self.assertEqual(demo["routing_source"], "default")
        self.assertNotIn("default_process", demo["routing_effective"])
        self.assertNotIn("harnesses", demo["routing_effective"])

        # А вместе с настоящим ключом — сохраняется только он.
        self.conn.execute(
            "UPDATE projects SET routing = ? WHERE slug = 'demo'",
            (json.dumps({"default_process": ["s1-spec"],
                         "transitions": {"s3-impl:s4-judge": "handoff"}}),),
        )
        self.conn.commit()
        demo2 = next(p for p in store.list_all_projects(self.conn) if p["slug"] == "demo")
        self.assertEqual(demo2["routing"],
                         {"transitions": {"s3-impl:s4-judge": "handoff"}})
        self.assertEqual(demo2["routing_source"], "db")
        effective = config_mod.routing("demo", conn=self.conn)
        self.assertEqual(effective["transitions"]["s3-impl:s4-judge"], "handoff")

    def test_legacy_default_process_in_toml_project_override_is_ignored(self) -> None:
        """То же для `[routing.projects.<slug>]` в config.toml."""
        self._config_path.write_text(
            '[routing]\ndefault_process = ["s1-spec", "s2-review"]\n'
            'harnesses = {s3-impl = ["dsh"]}\n'
            '[routing.projects.demo]\ndefault_process = ["s3-impl"]\n',
            encoding="utf-8",
        )
        effective = config_mod.routing("demo", conn=self.conn)
        self.assertNotIn("default_process", effective)
        self.assertNotIn("harnesses", effective)
        demo = next(p for p in store.list_all_projects(self.conn) if p["slug"] == "demo")
        self.assertIsNone(demo["routing"])
        self.assertEqual(demo["routing_source"], "default")

    def test_validate_routing_rejects_bad_shapes(self) -> None:
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"bad": 1})
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"return_window_hours": 0})
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"return_window_hours": -1})
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"transitions": {"s1-spec:s2-review": "magic"}})

    def test_update_project_routing_json_and_reset(self) -> None:
        with self.assertRaises(ValueError):
            store.update_project(self.conn, "demo", routing="{не json")
        out = store.update_project(self.conn, "demo", routing={})
        self.assertIsNone(out["routing"])
        row = store.project_row(self.conn, "demo")
        self.assertIsNone(row["routing"])

    # -- 8. форма ответа: routing / routing_effective / routing_source -----------

    def test_response_shape_has_routing_keys(self) -> None:
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertIn(demo["routing"], (None,))
        self.assertIsInstance(demo["routing_effective"], dict)
        for key in ("transitions", "return_window_hours"):
            self.assertIn(key, demo["routing_effective"])
        self.assertNotIn("default_process", demo["routing_effective"])
        self.assertNotIn("harnesses", demo["routing_effective"])

        out = store.update_project(self.conn, "demo",
                                   routing={"transitions": {"s3-impl:s4-judge": "handoff"}})
        self.assertEqual(out["routing_effective"]["transitions"]["s3-impl:s4-judge"],
                         "handoff")
        self.assertEqual(out["routing_source"], "db")

    # -- 9. CLI ------------------------------------------------------------------

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_CONFIG": str(self._config_path)}
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), *args],
            capture_output=True, text=True, env=env, cwd=str(LISTIK_BIN.parent.parent),
        )

    def test_cli_show_projects_and_set_routing(self) -> None:
        p = self._cli("--local", "projects", "demo")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("маршрутизация", p.stdout)
        # listik-sqh6: строки про убранный «процесс по умолчанию» в выводе больше нет.
        self.assertNotIn("процесс по умолчанию", p.stdout)

        p2 = self._cli("--local", "projects", "demo", "--routing",
                       '{"transitions":{"s3-impl:s4-judge":"handoff"}}')
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("s3-impl→s4-judge handoff", p2.stdout)
        self.assertIn("источник: база", p2.stdout)

    def test_cli_json_includes_routing_effective(self) -> None:
        p = self._cli("--local", "projects", "demo", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = json.loads(p.stdout)
        demo = next(r for r in rows if r["slug"] == "demo")
        self.assertIn("routing_effective", demo)

    def test_cli_invalid_routing_key_fails(self) -> None:
        p = self._cli("--local", "projects", "demo", "--routing", '{"bad": 1}')
        self.assertEqual(p.returncode, 1)
        self.assertIn("ошибка:", p.stderr)
        self.assertIn("неизвестный ключ", p.stderr)

    def test_cli_routing_unknown_project_fails(self) -> None:
        p = self._cli("--local", "projects", "nope", "--routing", "{}")
        self.assertEqual(p.returncode, 1)
        self.assertIn("проект не найден", p.stderr)

    def test_local_client_helpers_never_touch_http(self) -> None:
        """`local=True` не должен ходить по HTTP, даже если сервер выглядит поднятым."""
        from listik import client

        with mock.patch.object(db_mod, "init", return_value=self.conn), \
             mock.patch.object(client, "is_up", return_value=True) as is_up_mock, \
             mock.patch.object(client, "request") as request_mock:
            client.list_projects(local=True)
            client.add_project(path=None, slug="another", local=True)
            client.set_project_archived("demo", False, local=True)
            client.set_project_routing("demo", {}, local=True)

        is_up_mock.assert_not_called()
        request_mock.assert_not_called()


class TransitionKindEmptyFromStageTests(unittest.TestCase):
    """A task with no stage yet is not "handing off" to itself: `f"{from_stage}:{to_stage}"`
    with `from_stage=None` used to miss every entry in `transitions` and silently fall back
    to the generic "handoff" default, stripping the holder the moment a task first entered
    the pipeline (or on any `stage --to` from an empty stage)."""

    def test_none_from_stage_is_sticky_not_handoff(self) -> None:
        self.assertEqual(config_mod.transition_kind(None, None, "s1-spec"), "sticky")
        self.assertEqual(config_mod.transition_kind(None, None, "s2-review"), "sticky")

    def test_empty_string_from_stage_is_sticky_not_handoff(self) -> None:
        self.assertEqual(config_mod.transition_kind(None, "", "s1-spec"), "sticky")

    def test_same_stage_is_sticky_not_handoff(self) -> None:
        """Повторная выдача на том же этапе (`stage --to s3-impl --holder dsh`) —
        не передача: держатель остаётся назначенным, а не снимается молча."""
        self.assertEqual(config_mod.transition_kind(None, "s3-impl", "s3-impl"), "sticky")
        self.assertEqual(config_mod.transition_kind(None, "s4-judge", "s4-judge"), "sticky")

    def test_defined_transitions_still_work(self) -> None:
        self.assertEqual(config_mod.transition_kind(None, "s1-spec", "s2-review"), "handoff")
        self.assertEqual(config_mod.transition_kind(None, "s2-review", "s3-impl"), "handoff")
        self.assertEqual(config_mod.transition_kind(None, "s4-judge", "s3-impl"), "sticky-return")


if __name__ == "__main__":
    unittest.main()
