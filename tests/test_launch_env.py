"""Тесты `env` в запуске (listik-9hcc, порция a): `check_env`, `RESERVED_ENV`,
`launcher.start(env=...)`, `POST …/launch`, `listik launch --env`.

Обвязка — `tests.test_autostart.AutostartTestCase`/`pipeline_record`,
`tests.test_fencing_revoke.RevokeTestCase`/`CliRevokeLaunchTests` (импортированы, не
скопированы).
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import re
import sys
from unittest import mock

from listik import errors
from listik import launcher as launcher_mod
from listik import server
from tests.test_autostart import AutostartTestCase, pipeline_record
from tests.test_fencing_revoke import CliRevokeLaunchTests, RevokeTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent

# Пишет argv-маркер (LAUNCH_ENV_MARK, унаследованный от сервера) и все LISTIK_* из
# окружения писателя в JSON-файл.
WRITER_PY = """\
import json, os, sys
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"mark": os.environ.get("LAUNCH_ENV_MARK"),
               "listik_env": {k: v for k, v in os.environ.items()
                              if k.startswith("LISTIK_")}}, fh, ensure_ascii=False)
"""


class CheckEnvTests(AutostartTestCase):
    """Пункт 1 требований: `check_env` — одна функция, ровно эти правила."""

    def test_none_is_empty_dict(self) -> None:
        self.assertEqual(launcher_mod.check_env(None), {})

    def test_number_value_becomes_string(self) -> None:
        self.assertEqual(launcher_mod.check_env({"LISTIK_DEV_PORT": 5173}),
                         {"LISTIK_DEV_PORT": "5173"})

    def test_not_a_dict_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env("строка")

    def test_key_without_prefix_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({"PATH": "x"})

    def test_lowercase_key_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({"listik_x": "x"})

    def test_non_string_key_is_bad_argument_not_type_error(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({5: "x"})

    def test_every_reserved_key_is_rejected(self) -> None:
        for name in sorted(launcher_mod.RESERVED_ENV):
            with self.subTest(name=name):
                with self.assertRaises(errors.BadArgument):
                    launcher_mod.check_env({name: "x"})

    def test_none_value_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({"LISTIK_X": None})

    def test_bool_value_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({"LISTIK_X": True})

    def test_list_value_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({"LISTIK_X": ["a"]})

    def test_value_over_512_chars_is_bad_argument(self) -> None:
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env({"LISTIK_X": "a" * 513})

    def test_more_than_20_keys_is_bad_argument(self) -> None:
        env = {f"LISTIK_K{i}": "v" for i in range(21)}
        with self.assertRaises(errors.BadArgument):
            launcher_mod.check_env(env)


class ReservedEnvGuardTests(AutostartTestCase):
    """RESERVED_ENV не должен отставать от того, что Listik реально читает/выдаёт."""

    def test_reserved_env_covers_every_listik_var_in_source(self) -> None:
        pattern = re.compile(r"LISTIK_[A-Z0-9_]+")
        names: set[str] = set()
        for path in (REPO_DIR / "listik").glob("*.py"):
            names |= set(pattern.findall(path.read_text(encoding="utf-8")))
        names |= set(pattern.findall((REPO_DIR / "bin" / "listik").read_text(encoding="utf-8")))
        allowed = launcher_mod.RESERVED_ENV | {
            "LISTIK_DEV_PORT", "LISTIK_SWARM_API_KEY", "LISTIK_SWARM_BASE_URL",
            "LISTIK_SWARM_MODEL",
        }
        self.assertLessEqual(names, allowed, names - allowed)


class StartEnvTests(AutostartTestCase):
    """Пункты 2, 3 требований: `start(env=...)` подмешивает окружение, хвост журнала."""

    def writer_command(self, out_path) -> list[str]:
        script = self.tmp_path / "writer_env.py"
        script.write_text(WRITER_PY, encoding="utf-8")
        return [sys.executable, str(script), str(out_path)]

    def prepare(self, command, *, project="proj", route="low-pipeline"):
        proj_dir = self.tmp_path / project
        proj_dir.mkdir(exist_ok=True)
        self.make_project(project, path=proj_dir)
        self.set_routes(pipeline_record(route, command=command))
        return self.make_task(project=project, autostart=True, route=route)

    def test_extra_env_reaches_process_alongside_inherited_and_standard(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        with mock.patch.dict(os.environ, {"LAUNCH_ENV_MARK": "родитель"}):
            reason = launcher_mod.start(self.conn, task["id"], notify=self.notify_cb,
                                        log_dir=self.log_dir,
                                        env={"LISTIK_Z": "1", "LISTIK_A": "2"})
        self.assertIsNone(reason)
        self.join_tracker(task["id"])
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["mark"], "родитель")
        listik_env = data["listik_env"]
        self.assertEqual(listik_env["LISTIK_A"], "2")
        self.assertEqual(listik_env["LISTIK_Z"], "1")
        for name in ("LISTIK_TASK_ID", "LISTIK_ROUTE", "LISTIK_LAUNCHED_BY",
                     "LISTIK_GENERATION", "LISTIK_DISPATCH_ID"):
            self.assertIn(name, listik_env, listik_env)

        journal = [c for c in self.comments(task["id"], "journal")
                  if c["author"] == "agent:listik"][0]
        self.assertIn("окружение LISTIK_A=2, LISTIK_Z=1", journal["text"])

    def test_without_env_journal_comment_has_no_environment_tail(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        reason = launcher_mod.start(self.conn, task["id"], notify=self.notify_cb,
                                    log_dir=self.log_dir)
        self.assertIsNone(reason)
        self.join_tracker(task["id"])
        journal = [c for c in self.comments(task["id"], "journal")
                  if c["author"] == "agent:listik"][0]
        self.assertIn(", запуск ", journal["text"])
        self.assertNotIn("окружение", journal["text"])

    def _counts(self, task_id: str) -> tuple[int, int]:
        comments = self.conn.execute(
            "SELECT COUNT(*) FROM comments WHERE task_id = ?", (task_id,)).fetchone()[0]
        events = self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE task_id = ?", (task_id,)).fetchone()[0]
        return comments, events

    def test_bad_env_on_unstarted_task_leaves_it_untouched(self) -> None:
        task = self.make_task(project=None, autostart=False, route=None)
        before = self._counts(task["id"])
        with self.assertRaises(errors.BadArgument):
            launcher_mod.start(self.conn, task["id"], env={"PATH": "x"})
        row = self.row(task["id"])
        self.assertEqual(row["generation"], 0)
        self.assertIsNone(row["launched_by"])
        self.assertIsNone(row["launch_error"])
        self.assertEqual(row["needs_owner"], 0)
        after = self._counts(task["id"])
        self.assertEqual(before, after)
        events = [dict(r) for r in self.conn.execute(
            "SELECT * FROM events WHERE task_id = ?", (task["id"],)).fetchall()]
        self.assertEqual([e["kind"] for e in events], ["created"])


class HttpLaunchEnvTests(RevokeTestCase):
    """Пункт 4 требований: `POST …/launch` с `env`."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)

    def post(self, path, body=None):
        return server.handle("POST", path, {}, body or {}, authed=True)

    def writer_command(self, out_path) -> list[str]:
        script = self.tmp_path / "writer_env.py"
        script.write_text(WRITER_PY, encoding="utf-8")
        return [sys.executable, str(script), str(out_path)]

    def prepare(self, command, *, project="proj", route="low-pipeline"):
        proj_dir = self.tmp_path / project
        proj_dir.mkdir(exist_ok=True)
        self.make_project(project, path=proj_dir)
        self.set_routes(pipeline_record(route, command=command))
        return self.make_task(project=project, autostart=True, route=route)

    def test_valid_env_launches_and_reaches_process(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        status, data = self.post(f"/api/tasks/{task['id']}/launch",
                                 body={"env": {"LISTIK_DEV_PORT": "5173"}})
        self.assertEqual(status, 200)
        self.assertTrue(data["launched"])
        self.join_tracker(task["id"])
        listik_env = json.loads(out.read_text(encoding="utf-8"))["listik_env"]
        self.assertEqual(listik_env["LISTIK_DEV_PORT"], "5173")

    def test_reserved_key_is_400_and_task_untouched(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        tid = task["id"]
        before_cols = dict(self.row(tid))
        before_comments = len(self.comments(tid))
        with self.assertRaises(server.ApiError) as ctx:
            self.post(f"/api/tasks/{tid}/launch", body={"env": {"LISTIK_PORT": "1"}})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, errors.BAD_ARGUMENT)
        self.assertEqual(dict(self.row(tid)), before_cols)
        self.assertEqual(len(self.comments(tid)), before_comments)

    def test_env_not_a_dict_is_400(self) -> None:
        out = self.tmp_path / "out.json"
        task = self.prepare(self.writer_command(out))
        with self.assertRaises(server.ApiError) as ctx:
            self.post(f"/api/tasks/{task['id']}/launch", body={"env": "строка"})
        self.assertEqual(ctx.exception.status, 400)


class CliLaunchEnvTests(CliRevokeLaunchTests):
    """Пункт 5 требований: `listik launch --env` — разбор и тело запроса."""

    def test_env_pairs_are_split_and_last_wins(self) -> None:
        with mock.patch.object(self.cli.client, "is_up", return_value=True), \
             mock.patch.object(self.cli.client, "request",
                               return_value={"id": "t1", "generation": 1}) as req:
            code, _out, _err = self.run_cli([
                "launch", "t1",
                "--env", "LISTIK_DEV_PORT=5173",
                "--env", "LISTIK_X=a=b",
                "--env", "LISTIK_Y=",
                "--env", "LISTIK_X=2",
            ])
        self.assertEqual(code, 0)
        self.assertEqual(req.call_args.kwargs["body"]["env"],
                         {"LISTIK_DEV_PORT": "5173", "LISTIK_X": "2", "LISTIK_Y": ""})

    def test_env_pair_without_equals_is_bad_argument_before_request(self) -> None:
        with mock.patch.object(self.cli.client, "is_up", return_value=True), \
             mock.patch.object(self.cli.client, "request") as req:
            code, _out, err = self.run_cli(["launch", "t1", "--env", "без-равно"])
        self.assertNotEqual(code, 0)
        self.assertIn("K=V", err)
        req.assert_not_called()

    def test_env_pair_without_equals_reports_bad_argument_json(self) -> None:
        with mock.patch.object(self.cli.client, "is_up", return_value=True), \
             mock.patch.object(self.cli.client, "request") as req:
            code, out, _err = self.run_cli(
                ["launch", "t1", "--env", "без-равно", "--json"])
        self.assertNotEqual(code, 0)
        self.assertEqual(json.loads(out)["error"]["code"], errors.BAD_ARGUMENT)
        req.assert_not_called()

    def test_without_env_flag_body_has_no_env_key(self) -> None:
        with mock.patch.object(self.cli.client, "is_up", return_value=True), \
             mock.patch.object(self.cli.client, "request",
                               return_value={"id": "t1", "generation": 1}) as req:
            code, _out, _err = self.run_cli(["launch", "t1"])
        self.assertEqual(code, 0)
        self.assertNotIn("env", req.call_args.kwargs["body"])


if __name__ == "__main__":
    import unittest
    unittest.main()
