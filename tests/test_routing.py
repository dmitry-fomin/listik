"""Tests for project routing overrides (шаг 04, порция b).

Хранение переопределений harness/этапов/переходов у проекта (`config.validate_routing`,
`store.update_project(..., routing=...)`), их показ (`routing_effective`/`routing_source`)
и то, что `ready --harness`/`claim` действительно фильтруют по ним.
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
from listik import deps as deps_mod
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
        allowed = config_mod.allowed_harnesses("demo", "s3-impl", conn=self.conn)
        self.assertEqual(allowed, config_mod.DEFAULTS["routing"]["harnesses"]["s3-impl"])
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertEqual(demo["routing_source"], "default")
        self.assertIsNone(demo["routing"])

    # -- 2. переопределение из базы ---------------------------------------------

    def test_db_override_is_per_stage_merged(self) -> None:
        store.update_project(self.conn, "demo",
                             routing={"harnesses": {"s3-impl": ["dsh"]}})
        allowed = config_mod.allowed_harnesses("demo", "s3-impl", conn=self.conn)
        self.assertEqual(allowed, ["dsh"])
        # Другой этап не задет поключевым слиянием.
        allowed_s1 = config_mod.allowed_harnesses("demo", "s1-spec", conn=self.conn)
        self.assertEqual(allowed_s1, config_mod.DEFAULTS["routing"]["harnesses"]["s1-spec"])
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertEqual(demo["routing_source"], "db")

    # -- 3. переопределение из TOML, и приоритет базы над TOML ------------------

    def test_toml_override_and_db_wins_over_toml(self) -> None:
        self._config_path.write_text(
            '[routing.projects.demo.harnesses]\n'
            's3-impl = ["codex"]\n',
            encoding="utf-8",
        )
        allowed = config_mod.allowed_harnesses("demo", "s3-impl", conn=self.conn)
        self.assertEqual(allowed, ["codex"])
        projects = store.list_all_projects(self.conn)
        demo = next(p for p in projects if p["slug"] == "demo")
        self.assertEqual(demo["routing_source"], "config")

        store.update_project(self.conn, "demo",
                             routing={"harnesses": {"s3-impl": ["dsh"]}})
        allowed2 = config_mod.allowed_harnesses("demo", "s3-impl", conn=self.conn)
        self.assertEqual(allowed2, ["dsh"])
        projects2 = store.list_all_projects(self.conn)
        demo2 = next(p for p in projects2 if p["slug"] == "demo")
        self.assertEqual(demo2["routing_source"], "config+db")

    # -- 4. ready_tasks фильтрует по harness --------------------------------------

    def test_ready_tasks_filters_by_harness(self) -> None:
        store.update_project(self.conn, "demo",
                             routing={"harnesses": {"s3-impl": ["dsh"]}})
        impl_task = store.create_task(self.conn, title="реализация", project="demo",
                                      stage="s3-impl")
        spec_task = store.create_task(self.conn, title="тз", project="demo", stage="s1-spec")

        codex_ids = {t["id"] for t in deps_mod.ready_tasks(self.conn, harness="codex")}
        self.assertNotIn(impl_task["id"], codex_ids)
        self.assertIn(spec_task["id"], codex_ids)

        dsh_ids = {t["id"] for t in deps_mod.ready_tasks(self.conn, harness="dsh")}
        self.assertIn(impl_task["id"], dsh_ids)
        self.assertIn(spec_task["id"], dsh_ids)

        any_ids = {t["id"] for t in deps_mod.ready_tasks(self.conn)}
        self.assertIn(impl_task["id"], any_ids)
        self.assertIn(spec_task["id"], any_ids)

    # -- 5. задача без этапа не фильтруется по s1-spec (красный до правки) -------

    def test_task_without_stage_is_not_filtered_by_s1_spec_override(self) -> None:
        store.update_project(self.conn, "demo",
                             routing={"harnesses": {"s1-spec": ["dsh"]}})
        direct_task = store.create_task(self.conn, title="прямая", project="demo")
        self.assertIsNone(direct_task["stage"])

        codex_ids = {t["id"] for t in deps_mod.ready_tasks(self.conn, harness="codex")}
        self.assertIn(direct_task["id"], codex_ids)

    # -- 6. claim guard: порядок из ТЗ --------------------------------------------

    def test_claim_guard_order(self) -> None:
        store.update_project(self.conn, "demo",
                             routing={"harnesses": {"s3-impl": ["dsh"]}})
        task = store.create_task(self.conn, title="реализация", project="demo",
                                 stage="s3-impl")
        task_id = task["id"]

        with self.assertRaises(ValueError) as ctx:
            store.claim(self.conn, task_id, holder="codex", harness="codex")
        message = str(ctx.exception)
        self.assertIn("codex", message)
        self.assertIn("s3-impl", message)
        self.assertIn("dsh", message)
        current = store.get_task(self.conn, task_id)
        self.assertFalse((current["holder"] or "").strip())

        out = store.claim(self.conn, task_id, holder="codex")
        self.assertEqual(out["holder"], "codex")

        store.update_task(self.conn, task_id, holder="")

        out2 = store.claim(self.conn, task_id, holder="dsh", harness="dsh")
        self.assertEqual(out2["holder"], "dsh")

    # -- 7. validate_routing -------------------------------------------------------

    def test_validate_routing_accepts_valid_shapes(self) -> None:
        example = {
            "harnesses": {"s3-impl": ["dsh", "codex"]},
            "default_process": ["s1-spec", "s3-impl"],
            "transitions": {"s1-spec:s2-review": "sticky", "s4-judge:done": "handoff"},
            "return_window_hours": 12,
        }
        out = config_mod.validate_routing(example)
        self.assertEqual(out["harnesses"]["s3-impl"], ["dsh", "codex"])
        self.assertEqual(config_mod.validate_routing({}), {})

    def test_validate_routing_rejects_bad_shapes(self) -> None:
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"bad": 1})
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"harnesses": {"s9-x": ["dsh"]}})
        with self.assertRaises(ValueError):
            config_mod.validate_routing({"harnesses": "not-a-dict"})
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
        for key in ("default_process", "harnesses", "transitions", "return_window_hours"):
            self.assertIn(key, demo["routing_effective"])

        out = store.update_project(self.conn, "demo",
                                   routing={"harnesses": {"s3-impl": ["dsh"]}})
        self.assertEqual(out["routing_effective"]["harnesses"]["s3-impl"], ["dsh"])
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

        p2 = self._cli("--local", "projects", "demo", "--routing",
                       '{"harnesses":{"s3-impl":["dsh"]}}')
        self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertIn("s3-impl:   dsh", p2.stdout)
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

    def test_cli_ready_harness_after_override(self) -> None:
        task = store.create_task(self.conn, title="реализация", project="demo",
                                 stage="s3-impl")
        p_set = self._cli("--local", "projects", "demo", "--routing",
                          '{"harnesses":{"s3-impl":["dsh"]}}')
        self.assertEqual(p_set.returncode, 0, p_set.stderr)

        p_codex = self._cli("--local", "ready", "--harness", "codex")
        self.assertEqual(p_codex.returncode, 0, p_codex.stderr)
        self.assertNotIn(task["id"], p_codex.stdout)

        p_dsh = self._cli("--local", "ready", "--harness", "dsh")
        self.assertEqual(p_dsh.returncode, 0, p_dsh.stderr)
        self.assertIn(task["id"], p_dsh.stdout)

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

    def test_defined_transitions_still_work(self) -> None:
        self.assertEqual(config_mod.transition_kind(None, "s1-spec", "s2-review"), "sticky")
        self.assertEqual(config_mod.transition_kind(None, "s2-review", "s3-impl"), "handoff")
        self.assertEqual(config_mod.transition_kind(None, "s4-judge", "s3-impl"), "sticky-return")


if __name__ == "__main__":
    unittest.main()
