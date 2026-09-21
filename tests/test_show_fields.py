"""Выбор полей карточки: `--fields` в CLI, `?fields=` в HTTP, `fields` в MCP.

Фильтрует та сторона, которая собрала карточку: сервер (`store.select_task_fields`
в `GET /api/tasks/{id}`), `local_call` (локальный режим — «сервера нет») и MCP
(`listik_show`). Логика одна и та же, поэтому и ошибка неизвестного поля везде
одинаковая — `bad_argument` с перечнем доступных полей, а не молчаливый `None`
(так было у клиентского прототипа `--field`).

Покрыты все четыре пути приёмки: CLI `--json`, CLI `--local`, HTTP-эндпоинт,
MCP-инструмент.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest
from unittest import mock

from listik import client, db as db_mod, errors, mcp, paths, server, store
from tests.helpers import TempDbTestCase

LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"


class SelectFieldsTests(TempDbTestCase):
    """Общий хелпер `store.select_task_fields` — и фильтр, и отказ."""

    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Задача", project="p")["id"]

    def test_keeps_only_requested_keys_in_request_order(self) -> None:
        picked = store.select_task_fields(
            {"id": "x", "title": "Т", "launch_route": "x-pipeline", "labels": ["a"]},
            ["launch_route", "labels"])
        self.assertEqual(list(picked), ["launch_route", "labels"])
        self.assertEqual(picked["launch_route"], "x-pipeline")

    def test_comma_string_and_repeated_tokens_are_the_same(self) -> None:
        task = {"id": "x", "title": "Т", "labels": ["a"]}
        self.assertEqual(store.select_task_fields(task, "labels,id"),
                         store.select_task_fields(task, ["labels", "id"]))

    def test_no_fields_returns_task_as_is(self) -> None:
        task = {"id": "x"}
        self.assertIs(store.select_task_fields(task, None), task)
        self.assertIs(store.select_task_fields(task, []), task)
        self.assertIs(store.select_task_fields(task, ["  "]), task)

    def test_unknown_field_raises_bad_argument_with_available(self) -> None:
        with self.assertRaises(errors.BadArgument) as ctx:
            store.select_task_fields({"id": "x", "title": "Т"}, ["launch_route", "nope"])
        self.assertIn("nope", str(ctx.exception))
        self.assertIn("launch_route", str(ctx.exception))  # доступные поля в подсказке

    def test_local_call_show_filters_fields(self) -> None:
        with mock.patch.object(db_mod, "init", return_value=self.conn), \
                mock.patch.object(paths, "DB_PATH", self.db_path):
            task = client.local_call("show", task_id=self.task_id,
                                     fields=["launch_route", "labels"])
        self.assertEqual(sorted(task), ["labels", "launch_route"])

    def test_local_call_show_unknown_field_is_bad_argument(self) -> None:
        with mock.patch.object(db_mod, "init", return_value=self.conn), \
                mock.patch.object(paths, "DB_PATH", self.db_path), \
                self.assertRaises(errors.BadArgument):
            client.local_call("show", task_id=self.task_id, fields=["nope"])


class ServerFieldsTests(TempDbTestCase):
    """`GET /api/tasks/{id}?fields=…` — фильтр на стороне сервера."""

    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Задача", project="p",
                                         labels=["process:x-pipeline"])["id"]
        patcher = mock.patch.object(server, "get_conn", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_fields_query_returns_only_requested_keys(self) -> None:
        status, task = server.handle("GET", f"/api/tasks/{self.task_id}", {}, {},
                                     authed=True)
        self.assertEqual(status, 200)
        self.assertIn("title", task)
        status, task = server.handle("GET", f"/api/tasks/{self.task_id}",
                                     {"fields": ["launch_route,labels"]}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(sorted(task), ["labels", "launch_route"])

    def test_fields_query_unknown_field_is_400_bad_argument(self) -> None:
        # handle() отдаёт BadArgument, 400/`bad_argument` из него делает
        # error_response() на уровне HTTP-слоя — проверяем оба шага.
        with self.assertRaises(errors.BadArgument):
            server.handle("GET", f"/api/tasks/{self.task_id}",
                          {"fields": ["nope"]}, {}, authed=True)
        status, message, code = server.error_response(
            errors.BadArgument("неизвестное поле карточки: nope"))
        self.assertEqual((status, code), (400, errors.BAD_ARGUMENT))

    def test_fields_can_pick_deps_state(self) -> None:
        status, task = server.handle("GET", f"/api/tasks/{self.task_id}",
                                     {"fields": ["deps_state"]}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertEqual(list(task), ["deps_state"])
        self.assertIn("blocked_by", task["deps_state"])


class McpShowFieldsTests(TempDbTestCase):
    """`listik_show` с параметром `fields`."""

    def setUp(self) -> None:
        super().setUp()
        self.task_id = store.create_task(self.conn, title="Задача", project="p")["id"]

    def test_schema_has_fields_param(self) -> None:
        resp = mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, conn=self.conn)
        tool = next(t for t in resp["result"]["tools"] if t["name"] == "listik_show")
        self.assertIn("fields", tool["inputSchema"]["properties"])
        self.assertEqual(tool["inputSchema"]["required"], ["id"])

    def test_show_with_fields_returns_only_requested_keys(self) -> None:
        task = mcp.call_tool("listik_show", {"id": self.task_id,
                                             "fields": ["launch_route,labels"]},
                             conn=self.conn)
        self.assertEqual(sorted(task), ["labels", "launch_route"])

    def test_show_unknown_field_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            mcp.call_tool("listik_show", {"id": self.task_id, "fields": ["nope"]},
                          conn=self.conn)


class CliShowFieldsTests(TempDbTestCase):
    """CLI `show --fields`: и через сервер, и в локальном режиме."""

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "listik.log")}
        return subprocess.run([sys.executable, str(LISTIK_BIN), "--local", *args],
                              capture_output=True, text=True, env=env,
                              cwd=str(LISTIK_BIN.parent.parent))

    def setUp(self) -> None:
        super().setUp()
        proc = self.run_cli("new", "Задача", "--project", "p",
                            "--json", "--actor", "agent:test", "--harness", "test")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.task_id = json.loads(proc.stdout)["id"]

    def test_show_fields_json_gives_only_requested_keys(self) -> None:
        proc = self.run_cli("show", self.task_id, "--fields", "launch_route,labels", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        task = json.loads(proc.stdout)
        self.assertEqual(sorted(task), ["labels", "launch_route"])

    def test_show_fields_repeated_flag(self) -> None:
        proc = self.run_cli("show", self.task_id, "--fields", "launch_route",
                            "--fields", "labels", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(json.loads(proc.stdout)), ["labels", "launch_route"])

    def test_show_fields_text_prints_values(self) -> None:
        proc = self.run_cli("show", self.task_id, "--fields", "title")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "Задача")

    def test_show_unknown_field_is_bad_argument_json(self) -> None:
        proc = self.run_cli("show", self.task_id, "--fields", "nope", "--json")
        self.assertNotEqual(proc.returncode, 0)
        err = json.loads(proc.stdout)["error"]
        self.assertEqual(err["code"], "bad_argument")
        self.assertIn("nope", err["message"])
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
