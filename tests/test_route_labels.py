"""Метки маршрута (`harness:`/`process:`) на карточке — одна логика на сервере.

Требование listik-7ztx: `listik new --route` (CLI), `POST /api/tasks` с `route` и MCP
`listik_create` ставят те же метки, что форма «Новая задача» на доске, и не дублируют
уже заданные вручную. Правило живёт в `routes.labels_for`, применяет его `store`
(`labels_with_route` при создании, `labels_after_route_change` при смене маршрута);
доска метки не считает — только показывает пришедшие с сервера.

Тесты герметичны: маршруты ввезены во временную базу (`TempDbTestCase`),
рабочая копия `~/.config/listik/` не читается.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

from listik import errors
from listik import mcp
from listik import routes as routes_mod
from listik import routes_store
from listik import server, store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"
WEB_SRC = REPO_DIR / "web" / "src"

PIPELINE = "low-pipeline"
DIRECT = "dsh"
#: У конвейера исполнитель-оркестратор claude, у прямого маршрута — харнесс записи.
PIPELINE_LABELS = ["harness:claude", "process:low-pipeline"]
DIRECT_LABELS = ["harness:dsh", "process:direct"]

#: Образец таблицы маршрутов: конвейер с чужим провайдером роли impl (правило меток от
#: него не зависит) и прямой маршрут. Ключ `нет-такого` намеренно отсутствует.
ROUTES_DOC = {
    "version": 1,
    "routes": [
        {
            "key": PIPELINE,
            "kind": "pipeline",
            "title": "low",
            "hint": "",
            "visible": True,
            "roles": {
                "spec": {"provider": "claude", "label": "low", "title": "Fable · low"},
                "critic": {"provider": "glm", "label": "GLM", "title": "GLM · flash"},
                "impl": {"provider": "deepseek", "label": "flash", "title": "DeepSeek · flash"},
                "judge": {"provider": "grok", "label": "xhigh", "title": "Grok · xhigh"},
            },
        },
        {"key": DIRECT, "kind": "direct", "title": "dsh", "hint": "", "visible": True,
         "harness": "dsh"},
    ],
}


def write_routes(path: pathlib.Path) -> pathlib.Path:
    path.write_text(json.dumps(ROUTES_DOC, ensure_ascii=False), encoding="utf-8")
    return path


class RoutesStateMixin:
    """Временный файл ввозится в таблицу временной базы."""

    def setUp(self) -> None:
        super().setUp()
        self.routes_path = write_routes(self.tmp_path / "routes.json")
        report = routes_store.import_file(self.conn, self.routes_path)
        self.assertEqual(report["imported"], 2)


class LabelRuleTests(RoutesStateMixin, TempDbTestCase):
    """`routes.labels_for` — то самое одно место, где правило и живёт."""

    def test_pipeline_gets_claude_and_its_key(self) -> None:
        self.assertEqual(routes_mod.labels_for(self.conn, PIPELINE), PIPELINE_LABELS)

    def test_direct_gets_its_harness_and_direct_process(self) -> None:
        self.assertEqual(routes_mod.labels_for(self.conn, DIRECT), DIRECT_LABELS)

    def test_empty_unknown_or_whitespace_key_gets_nothing(self) -> None:
        for key in (None, "", "   ", "нет-такого"):
            with self.subTest(key=key):
                self.assertEqual(routes_mod.labels_for(self.conn, key), [])

    def test_database_error_gets_nothing_and_reports_reason(self) -> None:
        with mock.patch.object(routes_store, "get_route",
                               side_effect=sqlite3.DatabaseError("база недоступна")), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(routes_mod.labels_for(self.conn, PIPELINE), [])
        self.assertIn("база недоступна", err.getvalue())


class StoreLabelTests(RoutesStateMixin, TempDbTestCase):
    """store: создание задачи с маршрутом и смена маршрута у заведённой."""

    def task(self, **fields) -> dict:
        return store.create_task(self.conn, title="проба", project="demo", **fields)

    def test_create_with_pipeline_route_labels_the_card(self) -> None:
        task = self.task(route=PIPELINE)
        self.assertEqual(task["launch_route"], PIPELINE)
        self.assertEqual(task["labels"], PIPELINE_LABELS)

    def test_create_with_direct_route_labels_the_card(self) -> None:
        task = self.task(route=DIRECT)
        self.assertEqual(task["labels"], DIRECT_LABELS)

    def test_manual_label_is_not_duplicated(self) -> None:
        task = self.task(route=PIPELINE, labels=["harness:claude", "срочно"])
        self.assertEqual(task["labels"], ["harness:claude", "срочно", "process:low-pipeline"])
        self.assertEqual(task["labels"].count("harness:claude"), 1)

    def test_route_labels_do_not_duplicate_each_other(self) -> None:
        task = self.task(route=DIRECT, labels=[*DIRECT_LABELS])
        self.assertEqual(task["labels"], DIRECT_LABELS)

    def test_without_route_labels_are_untouched(self) -> None:
        task = self.task(labels=["frontend"])
        self.assertEqual(task["labels"], ["frontend"])

    def test_unknown_route_key_gets_no_labels(self) -> None:
        task = self.task(route="нет-такого", labels=["frontend"])
        self.assertEqual(task["labels"], ["frontend"])
        self.assertEqual(task["launch_route"], "нет-такого")

    def test_route_change_replaces_route_labels_and_keeps_others(self) -> None:
        task = self.task(route=PIPELINE, labels=["frontend"])
        updated = store.update_task(self.conn, task["id"], route=DIRECT)
        self.assertEqual(updated["labels"], ["frontend", *DIRECT_LABELS])

    def test_route_change_back_to_a_pipeline(self) -> None:
        task = self.task(route=DIRECT)
        updated = store.update_task(self.conn, task["id"], route=PIPELINE)
        self.assertEqual(updated["labels"], PIPELINE_LABELS)

    def test_clearing_route_removes_route_labels_only(self) -> None:
        task = self.task(route=PIPELINE, labels=["frontend"])
        updated = store.update_task(self.conn, task["id"], launch_route="")
        self.assertIsNone(updated["launch_route"])
        self.assertEqual(updated["labels"], ["frontend"])

    def test_unknown_route_on_change_keeps_labels(self) -> None:
        # Маршрута нет в таблице — смена отклоняется (`BadArgument`, listik-zr05):
        # ни маршрут, ни метки карточки не меняются.
        task = self.task(route=PIPELINE, labels=["frontend"])
        with self.assertRaises(errors.BadArgument) as ctx:
            store.update_task(self.conn, task["id"], route="нет-такого")
        self.assertIn("маршрута нет-такого нет в базе", str(ctx.exception))
        after = store.get_task(self.conn, task["id"])
        self.assertEqual(after["launch_route"], PIPELINE)
        self.assertEqual(after["labels"], ["frontend", *PIPELINE_LABELS])

    def test_same_route_does_not_touch_labels(self) -> None:
        task = self.task(route=PIPELINE)
        self.conn.execute("UPDATE tasks SET labels = '[\"frontend\"]' WHERE id = ?",
                          (task["id"],))
        self.conn.commit()
        out = store.update_task(self.conn, task["id"], route=PIPELINE)
        self.assertIs(out.get("unchanged"), True)
        self.assertEqual(out["labels"], ["frontend"])

    def test_imported_route_labels_the_card(self) -> None:
        self.assertEqual(self.task(route=PIPELINE)["labels"], PIPELINE_LABELS)

    def test_labels_passed_in_the_same_call_are_the_base(self) -> None:
        # Явные метки того же вызова — основа; метки маршрута сервер всё равно
        # нормализует сам (снимает старые `harness:`/`process:` и ставит новые).
        task = self.task(route=PIPELINE)
        updated = store.update_task(self.conn, task["id"], route=DIRECT,
                                    labels=["harness:dsh", "ручное"])
        self.assertEqual(updated["labels"], ["ручное", "harness:dsh", "process:direct"])


class ApiLabelTests(RoutesStateMixin, TempDbTestCase):
    """`POST /api/tasks` — тот же путь, которым ходит форма «Новая задача»."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)
        self._publish_patch = mock.patch.object(server, "publish")
        self._publish_patch.start()
        self.addCleanup(self._publish_patch.stop)

    def post(self, body: dict):
        return server.handle("POST", "/api/tasks", {}, body, authed=True)

    def test_post_with_route_labels_like_the_board_form(self) -> None:
        status, task = self.post({"title": "проба", "project": "demo", "route": PIPELINE,
                                  "actor": "me"})
        self.assertEqual(status, 201)
        self.assertEqual(task["labels"], PIPELINE_LABELS)
        self.assertEqual(task["launch_route"], PIPELINE)

    def test_post_does_not_duplicate_manual_labels(self) -> None:
        _, task = self.post({"title": "проба", "project": "demo", "route": DIRECT,
                             "labels": ["harness:dsh"], "actor": "me"})
        self.assertEqual(task["labels"], DIRECT_LABELS)

    def test_post_without_route_leaves_labels_empty(self) -> None:
        _, task = self.post({"title": "проба", "project": "demo", "actor": "me"})
        self.assertEqual(task["labels"], [])

    def test_patch_route_rewrites_labels(self) -> None:
        _, task = self.post({"title": "проба", "project": "demo", "route": PIPELINE,
                             "labels": ["frontend"], "actor": "me"})
        status, updated = server.handle("PATCH", f"/api/tasks/{task['id']}", {},
                                        {"route": DIRECT, "actor": "me"}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(updated["labels"], ["frontend", *DIRECT_LABELS])


class McpLabelTests(RoutesStateMixin, TempDbTestCase):
    """MCP `listik_create`: маршрут в схеме инструмента и метки на карточке."""

    def test_tool_schema_accepts_route(self) -> None:
        tool = next(item for item in mcp.TOOLS if item["name"] == "listik_create")
        self.assertIn("route", tool["inputSchema"]["properties"])
        self.assertEqual(tool["inputSchema"]["required"], ["title"])

    def test_create_with_route_labels_the_card(self) -> None:
        task = mcp.call_tool("listik_create",
                             {"title": "проба", "project": "demo", "route": PIPELINE},
                             conn=self.conn)
        self.assertEqual(task["launch_route"], PIPELINE)
        self.assertEqual(task["labels"], PIPELINE_LABELS)

    def test_create_without_route_does_not_label(self) -> None:
        task = mcp.call_tool("listik_create", {"title": "проба", "project": "demo"},
                             conn=self.conn)
        self.assertEqual(task["labels"], [])


class StdioMcpLabelTests(RoutesStateMixin, TempDbTestCase):
    """Тот же MCP, но настоящим stdio-процессом: маршруты берёт из базы."""

    def test_stdio_create_with_route_labels_the_card(self) -> None:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "mcp.log")}
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "listik_create",
                              "arguments": {"title": "проба", "project": "demo",
                                            "route": PIPELINE}}}
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), "mcp"],
                              input=json.dumps(request) + "\n", capture_output=True,
                              text=True, env=env, cwd=str(REPO_DIR), timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        response = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertNotIn("isError", response.get("result", {}), proc.stdout)
        task = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(task["launch_route"], PIPELINE)
        self.assertEqual(task["labels"], PIPELINE_LABELS)


class CliLabelTests(RoutesStateMixin, TempDbTestCase):
    """`listik new --route` и `listik set <id> route=` в локальном режиме."""

    def _run(self, *args) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "cli.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(REPO_DIR), timeout=120)

    def _new(self, *extra: str) -> dict:
        proc = self._run("new", "проба", "-p", "demo", "--route", PIPELINE, "--json", *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_cli_new_with_route_labels_the_card(self) -> None:
        task = self._new()
        self.assertEqual(task["launch_route"], PIPELINE)
        self.assertEqual(task["labels"], PIPELINE_LABELS)

    def test_cli_manual_label_is_not_duplicated(self) -> None:
        task = self._new("--label", "harness:claude")
        self.assertEqual(task["labels"], ["harness:claude", "process:low-pipeline"])

    def test_cli_and_the_board_form_give_the_same_labels(self) -> None:
        # Форма на доске шлёт в POST /api/tasks только `route` — ровно то же, что CLI.
        cli_task = self._new()
        with mock.patch.object(server, "get_conn", return_value=self.conn), \
                mock.patch.object(server, "publish"):
            _, api_task = server.handle("POST", "/api/tasks", {},
                                        {"title": "проба", "project": "demo",
                                         "route": PIPELINE}, authed=True)
        self.assertEqual(cli_task["labels"], api_task["labels"])

    def test_cli_route_change_rewrites_labels(self) -> None:
        task = self._new("--label", "frontend")
        proc = self._run("set", task["id"], f"route={DIRECT}", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        updated = json.loads(proc.stdout)
        self.assertEqual(updated["launch_route"], DIRECT)
        self.assertEqual(updated["labels"], ["frontend", *DIRECT_LABELS])

    def test_cli_new_without_route_does_not_label(self) -> None:
        proc = self._run("new", "проба", "-p", "demo", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["labels"], [])


class WebRuleMovedTests(unittest.TestCase):
    """Правило меток живёт на сервере: в доске его больше нет (listik-7ztx)."""

    def test_web_does_not_compute_route_labels(self) -> None:
        self.assertTrue(WEB_SRC.is_dir(), WEB_SRC)
        offenders = [str(path.relative_to(REPO_DIR)) for path in WEB_SRC.rglob("*")
                     if path.suffix in (".ts", ".vue")
                     and "routeLabels" in path.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [], "метки маршрута должна считать только серверная сторона")


if __name__ == "__main__":
    unittest.main()
