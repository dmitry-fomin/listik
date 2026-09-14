"""Смена маршрута («типа запуска») задачи — store, API и CLI.

Маршрут живёт в колонке `launch_route`: его выбирают при создании, а потом можно
поменять — но только пока задача заведена и работа не началась: нет этапа,
держателя и запущенного процесса. Как только задача пошла в работу, сервер
отказывает понятной ошибкой (400/`conflict`), а доска, глядя на `route_editable`,
даже не показывает выбор. Остальные восемь колонок запуска правкой карточки
по-прежнему не меняются.

Два запрета проверяются отдельно: один вызов не может заодно начать работу и
сменить маршрут (в нём видны новые `status`/`stage`/`holder`), а гонку с `claim`
на втором соединении ловит условный UPDATE — у взятой задачи маршрут не меняется.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

from listik import db as db_mod
from listik import server
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"

# Восемь колонок запуска, которые пишет только create_task/launcher (девятая,
# launch_route, — предмет этих тестов).
FROZEN_LAUNCH_FIELDS = ("autostart", "launched_by", "launch_pid", "launched_at",
                        "launch_log", "launch_exit_code", "launch_finished_at",
                        "launch_error")


class RouteStoreTests(TempDbTestCase):
    """store.update_task: смена маршрута и её запрет после начала работы."""

    def task(self, **fields) -> dict:
        return store.create_task(self.conn, title="проба", project="listik", **fields)

    def row(self, task_id: str):
        return self.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

    def route_events(self, task_id: str) -> list[tuple]:
        rows = self.conn.execute(
            "SELECT from_value, to_value FROM events WHERE task_id = ? AND kind = 'route' "
            "ORDER BY rowid", (task_id,)).fetchall()
        return [(r["from_value"], r["to_value"]) for r in rows]

    def test_open_task_route_is_set_and_changed(self) -> None:
        task = self.task()
        self.assertIsNone(task["launch_route"])
        self.assertIs(task["route_editable"], True)

        updated = store.update_task(self.conn, task["id"], launch_route="low-pipeline")
        self.assertEqual(updated["launch_route"], "low-pipeline")
        self.assertEqual(self.route_events(task["id"]), [(None, "low-pipeline")])

        updated = store.update_task(self.conn, task["id"], launch_route="high-pipeline",
                                    actor="agent:dsh", note="дороже, но надёжнее")
        self.assertEqual(updated["launch_route"], "high-pipeline")
        self.assertIs(updated["route_editable"], True)
        self.assertEqual(self.route_events(task["id"]),
                         [(None, "low-pipeline"), ("low-pipeline", "high-pipeline")])
        event = self.conn.execute(
            "SELECT actor, note FROM events WHERE task_id = ? AND kind = 'route' "
            "ORDER BY rowid DESC LIMIT 1", (task["id"],)).fetchone()
        self.assertEqual(event["actor"], "agent:dsh")
        self.assertEqual(event["note"], "дороже, но надёжнее")

    def test_route_alias_route_is_the_same_field(self) -> None:
        task = self.task(route="low-pipeline")
        updated = store.update_task(self.conn, task["id"], route="dsh")
        self.assertEqual(updated["launch_route"], "dsh")

    def test_empty_route_clears_it(self) -> None:
        task = self.task(route="low-pipeline")
        updated = store.update_task(self.conn, task["id"], launch_route="  ")
        self.assertIsNone(updated["launch_route"])
        self.assertEqual(self.route_events(task["id"]), [("low-pipeline", None)])

    def test_same_route_is_unchanged_and_writes_nothing(self) -> None:
        task = self.task(route="low-pipeline")
        out = store.update_task(self.conn, task["id"], route=" low-pipeline ")
        self.assertIs(out.get("unchanged"), True)
        self.assertEqual(out["launch_route"], "low-pipeline")
        self.assertEqual(out["id"], task["id"])
        self.assertIn("holder_title", out, "no-op set должен отдавать карточку, а не строку таблицы")
        self.assertEqual(self.route_events(task["id"]), [])

    def test_status_in_progress_refuses_and_keeps_route(self) -> None:
        task = self.task(route="low-pipeline")
        store.claim(self.conn, task["id"], holder="dsh")
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], False)

        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], route="grok")
        message = str(ctx.exception)
        self.assertIn("маршрут нельзя менять", message)
        self.assertIn("статус «в работе»", message)
        self.assertEqual(self.row(task["id"])["launch_route"], "low-pipeline")
        self.assertEqual(self.route_events(task["id"]), [])

    def test_stage_refuses(self) -> None:
        task = self.task(route="low-pipeline", stage="s1-spec")
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], False)
        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], route="grok")
        self.assertIn("этап s1-spec", str(ctx.exception))

    def test_holder_refuses(self) -> None:
        task = self.task()
        store.update_task(self.conn, task["id"], holder="dsh")
        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], route="grok")
        self.assertIn("держит dsh", str(ctx.exception))
        self.assertIsNone(self.row(task["id"])["launch_route"])

    def test_launched_task_refuses(self) -> None:
        task = self.task(route="low-pipeline")
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'listik', launch_pid = 42, "
            "launched_at = '2026-01-01T00:00:00Z' WHERE id = ?", (task["id"],))
        self.conn.commit()
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], False)
        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], route="grok")
        self.assertIn("процесс уже запускали", str(ctx.exception))

    def test_same_call_starting_work_cannot_change_route(self) -> None:
        """Один вызов не может начать работу и сменить маршрут: `status`/`stage`/`holder`
        из тех же полей учитываются проверкой, и отказ не применяет ни одного поля."""
        task = self.task(route="low-pipeline")
        calls = (
            (("status", "in_progress"), ("route", "grok")),
            (("route", "grok"), ("status", "in_progress")),
            (("stage", "s3-impl"), ("route", "grok")),
            (("holder", "dsh"), ("route", "grok")),
            (("holder", "dsh"), ("status", "in_progress"), ("route", "grok")),
        )
        for pairs in calls:
            with self.subTest(pairs=pairs):
                with self.assertRaises(ValueError) as ctx:
                    store.update_task(self.conn, task["id"], **dict(pairs))
                message = str(ctx.exception)
                self.assertIn("маршрут нельзя менять", message)
                row = self.row(task["id"])
                self.assertEqual(row["launch_route"], "low-pipeline", "маршрут не менялся")
                self.assertEqual(row["status"], "open", "отказ не применяет status")
                self.assertIsNone(row["stage"], "отказ не применяет stage")
                self.assertIsNone(row["holder"], "отказ не применяет holder")
                self.assertEqual(self.route_events(task["id"]), [])

    def test_same_call_clearing_stage_does_not_bypass_the_ban(self) -> None:
        """Снять этап и сменить маршрут одним вызовом нельзя: на записи задача ещё
        с этапом (условный UPDATE смотрит состояние до запроса), поэтому отказ —
        и ни одно из полей не применяется."""
        task = self.task(route="low-pipeline", stage="s1-spec")
        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], stage="", route="grok")
        self.assertIn("этап s1-spec", str(ctx.exception))
        row = self.row(task["id"])
        self.assertEqual(row["launch_route"], "low-pipeline")
        self.assertEqual(row["stage"], "s1-spec")
        self.assertEqual(self.route_events(task["id"]), [])

    def test_same_call_with_same_route_starts_work(self) -> None:
        """Маршрут не меняется — началу работы это не мешает: смена была бы no-op."""
        task = self.task(route="low-pipeline")
        updated = store.update_task(self.conn, task["id"], status="in_progress",
                                    route="low-pipeline")
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["launch_route"], "low-pipeline")
        self.assertEqual(self.route_events(task["id"]), [])

    def test_non_string_route_is_rejected(self) -> None:
        task = self.task()
        for value in (5, ["low-pipeline"], {"key": "low-pipeline"}):
            with self.assertRaises(ValueError) as ctx:
                store.update_task(self.conn, task["id"], route=value)
            self.assertIn("маршрут должен быть строкой", str(ctx.exception))
        self.assertIsNone(self.row(task["id"])["launch_route"])

    def test_other_launch_fields_are_still_not_updatable(self) -> None:
        task = self.task(route="low-pipeline")
        store.update_task(self.conn, task["id"], autostart=True, launched_by="listik",
                          launch_pid=1, launch_error="подмена", launch_exit_code=42,
                          launch_log="/tmp/подмена.log", launched_at="2020-01-01T00:00:00Z",
                          launch_finished_at="2020-01-01T00:00:00Z")
        row = self.row(task["id"])
        for name in FROZEN_LAUNCH_FIELDS:
            if name == "autostart":
                self.assertEqual(row[name], 0, name)
            else:
                self.assertIsNone(row[name], name)


class RouteRaceTests(TempDbTestCase):
    """Смена маршрута против `claim` на двух соединениях: у взятой задачи маршрут стоит.

    Обе операции читают карточку, а пишут по очереди. Проверка «ещё заведена» стоит
    в WHERE самого UPDATE смены маршрута, поэтому взятую задачу он не задевает и
    отказывает тем же текстом, что и обычная проверка.
    """

    def setUp(self) -> None:
        super().setUp()
        self.second = db_mod.connect(self.db_path)
        self.addCleanup(self.second.close)
        self.task = store.create_task(self.conn, title="проба", project="listik",
                                      route="low-pipeline")

    def route(self, conn=None) -> str | None:
        conn = conn or self.conn
        return conn.execute("SELECT launch_route FROM tasks WHERE id = ?",
                            (self.task["id"],)).fetchone()["launch_route"]

    def route_events(self, task_id: str) -> list[tuple]:
        rows = self.conn.execute(
            "SELECT from_value, to_value FROM events WHERE task_id = ? AND kind = 'route' "
            "ORDER BY rowid", (task_id,)).fetchall()
        return [(r["from_value"], r["to_value"]) for r in rows]

    def test_claim_between_read_and_route_update_keeps_old_route(self) -> None:
        """`claim` влезает ровно между чтением карточки и UPDATE смены маршрута.

        Так гонка и выглядит: оба соединения прочитали задачу заведённой, а записали
        по очереди. Условный UPDATE видит свежего держателя и не трогает маршрут.
        """
        real_now_iso = store.now_iso
        fired: list[bool] = []

        def claim_then_now() -> str:
            if not fired:
                fired.append(True)
                # Второе соединение успело взять задачу — ровно как в жизни.
                store.claim(self.conn, self.task["id"], holder="dsh")
            return real_now_iso()

        with mock.patch.object(store, "now_iso", side_effect=claim_then_now):
            with self.assertRaises(ValueError) as ctx:
                store.update_task(self.second, self.task["id"], route="grok")
        self.assertTrue(fired, "подмена now_iso не сработала — гонка не воспроизвелась")
        message = str(ctx.exception)
        self.assertIn("маршрут нельзя менять", message)
        self.assertIn("держит dsh", message)
        # Маршрут остался прежним на обоих соединениях, следа смены нет.
        self.assertEqual(self.route(), "low-pipeline")
        self.assertEqual(self.route(self.second), "low-pipeline")
        self.assertEqual(self.route_events(self.task["id"]), [])
        self.assertIs(store.get_task(self.second, self.task["id"])["route_editable"], False)

    def test_parallel_claim_and_route_change_never_change_taken_route(self) -> None:
        """Две операции наперегонки на двух соединениях: у взятой задачи маршрут стоит.

        Пока третье соединение держит блокировку записи, и `claim`, и смена маршрута
        успевают прочитать задачу заведённой — а записывают потом по очереди, как в
        жизни. Порядок виден по rowid событий: событие `route` не должно оказаться
        позже события `claim`, иначе маршрут сменили бы у уже взятой задачи.
        """
        for attempt in range(10):
            task = store.create_task(self.conn, title=f"гонка {attempt}", project="listik",
                                     route="low-pipeline")
            lock = db_mod.connect(self.db_path)
            conn_claim = db_mod.connect(self.db_path)
            conn_route = db_mod.connect(self.db_path)
            failures: list[BaseException] = []
            # Читать можно и под блокировкой записи (WAL), писать — нет: оба потока
            # дочитают карточку заведённой и встанут на своей первой записи.
            lock.execute("BEGIN IMMEDIATE")

            def take(task_id=task["id"], conn=conn_claim) -> None:
                try:
                    store.claim(conn, task_id, holder="dsh")
                except BaseException as exc:  # noqa: BLE001 — ждём только успеха
                    failures.append(exc)

            def change(task_id=task["id"], conn=conn_route) -> None:
                try:
                    store.update_task(conn, task_id, route="grok")
                except ValueError:
                    pass  # отказ — законный исход гонки: задачу уже взяли
                except BaseException as exc:  # noqa: BLE001
                    failures.append(exc)

            threads = [threading.Thread(target=take), threading.Thread(target=change)]
            try:
                threads[0].start()
                time.sleep(0.05)  # claim прочитал карточку и встал на записи
                threads[1].start()
                time.sleep(0.05)  # смена маршрута прочитала ту же заведённую карточку
                lock.rollback()   # отпускаем запись — дальше потоки пишут по очереди
                for thread in threads:
                    thread.join()
            finally:
                lock.close()
                conn_claim.close()
                conn_route.close()
            self.assertEqual(failures, [], f"попытка {attempt}")
            kinds = [row["kind"] for row in self.conn.execute(
                "SELECT kind FROM events WHERE task_id = ? ORDER BY rowid",
                (task["id"],)).fetchall()]
            route_at = kinds.index("route") if "route" in kinds else None
            claim_at = kinds.index("claim") if "claim" in kinds else None
            self.assertIsNotNone(claim_at, f"попытка {attempt}: задача не взята")
            if route_at is not None:
                self.assertLess(route_at, claim_at,
                                f"попытка {attempt}: маршрут сменился после взятия")
            store.delete_task(self.conn, task["id"])


class RouteApiTests(TempDbTestCase):
    """PATCH /api/tasks/{id}: `route`/`launch_route` и отказ после начала работы."""

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)
        self.task = store.create_task(self.conn, title="проба", project="listik",
                                      route="low-pipeline")

    def patch(self, body: dict, task_id: str | None = None):
        with mock.patch.object(server, "publish"):
            return server.handle("PATCH", f"/api/tasks/{task_id or self.task['id']}", {}, body,
                                 authed=True)

    def row_route(self) -> str | None:
        return self.conn.execute("SELECT launch_route FROM tasks WHERE id = ?",
                                 (self.task["id"],)).fetchone()["launch_route"]

    def test_patch_route_of_open_task(self) -> None:
        status, task = self.patch({"route": "high-pipeline", "actor": "me"})
        self.assertEqual(status, 200)
        self.assertEqual(task["launch_route"], "high-pipeline")
        self.assertIs(task["route_editable"], True)
        self.assertEqual(self.row_route(), "high-pipeline")

    def test_patch_launch_route_is_the_same_field(self) -> None:
        status, task = self.patch({"launch_route": "medium-pipeline"})
        self.assertEqual(status, 200)
        self.assertEqual(task["launch_route"], "medium-pipeline")

    def test_patch_route_is_400_once_work_started(self) -> None:
        store.claim(self.conn, self.task["id"], holder="dsh")
        with self.assertRaises(server.ApiError) as ctx:
            self.patch({"route": "grok"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("маршрут нельзя менять", ctx.exception.message)
        self.assertIn("статус «в работе»", ctx.exception.message)
        self.assertEqual(self.row_route(), "low-pipeline")

    def test_patch_route_400_on_stage(self) -> None:
        staged = store.create_task(self.conn, title="этап", project="listik", stage="s3-impl")
        with self.assertRaises(server.ApiError) as ctx:
            self.patch({"route": "grok"}, task_id=staged["id"])
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("этап s3-impl", ctx.exception.message)

    def test_patch_route_400_on_non_string(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch({"route": ["grok"]})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("маршрут должен быть строкой", ctx.exception.message)

    def test_patch_same_call_starting_work_and_route_is_400(self) -> None:
        """Один PATCH не может начать работу и сменить маршрут — ни одного поля."""
        for body in ({"status": "in_progress", "route": "grok"},
                     {"route": "grok", "holder": "dsh"},
                     {"stage": "s3-impl", "route": "grok"}):
            with self.subTest(body=body):
                with self.assertRaises(server.ApiError) as ctx:
                    self.patch(dict(body))
                self.assertEqual(ctx.exception.status, 400)
                self.assertIn("маршрут нельзя менять", ctx.exception.message)
                row = self.conn.execute(
                    "SELECT status, stage, holder, launch_route FROM tasks WHERE id = ?",
                    (self.task["id"],)).fetchone()
                self.assertEqual(row["status"], "open")
                self.assertIsNone(row["stage"])
                self.assertIsNone(row["holder"])
                self.assertEqual(row["launch_route"], "low-pipeline")

    def test_get_task_reports_route_editable(self) -> None:
        status, task = server.handle("GET", f"/api/tasks/{self.task['id']}", {}, {}, authed=True)
        self.assertEqual(status, 200)
        self.assertIs(task["route_editable"], True)
        store.update_task(self.conn, self.task["id"], holder="dsh")
        _, held = server.handle("GET", f"/api/tasks/{self.task['id']}", {}, {}, authed=True)
        self.assertIs(held["route_editable"], False)

    def test_patch_still_ignores_other_launch_fields(self) -> None:
        status, task = self.patch({"autostart": True, "launched_by": "listik", "launch_pid": 7,
                                   "launch_error": "подмена"})
        self.assertEqual(status, 200)
        self.assertIs(task["autostart"], False)
        self.assertIsNone(task["launched_by"])
        self.assertIsNone(task["launch_pid"])
        self.assertIsNone(task["launch_error"])


class RouteMcpTests(TempDbTestCase):
    """MCP `listik_update` принимает маршрут и под алиасом `route`, и под колонкой."""

    def test_mcp_update_accepts_route_alias(self) -> None:
        from listik import mcp

        task = store.create_task(self.conn, title="проба", project="listik",
                                 route="low-pipeline")
        out = mcp.call_tool("listik_update",
                            {"id": task["id"], "fields": {"route": "high-pipeline"}},
                            conn=self.conn)
        self.assertEqual(out["launch_route"], "high-pipeline")
        out = mcp.call_tool("listik_update",
                            {"id": task["id"], "fields": {"launch_route": "medium-pipeline"}},
                            conn=self.conn)
        self.assertEqual(out["launch_route"], "medium-pipeline")

    def test_mcp_update_refuses_route_after_work_started(self) -> None:
        from listik import mcp

        task = store.create_task(self.conn, title="проба", project="listik",
                                 route="low-pipeline")
        store.claim(self.conn, task["id"], holder="dsh")
        with self.assertRaises(ValueError) as ctx:
            mcp.call_tool("listik_update",
                          {"id": task["id"], "fields": {"route": "grok"}}, conn=self.conn)
        self.assertIn("маршрут нельзя менять", str(ctx.exception))


class RouteCliTests(TempDbTestCase):
    """`listik set <id> route=…` и показ маршрута в `listik show`."""

    def _run(self, *args):
        env = {**os.environ, "LISTIK_DB": str(self.db_path),
               "LISTIK_LOG": str(self.tmp_path / "cli.log")}
        return subprocess.run(
            [sys.executable, str(LISTIK_BIN), "--local", *args],
            capture_output=True, text=True, env=env, cwd=str(REPO_DIR),
        )

    def _new(self, *extra: str) -> str:
        proc = self._run("new", "проба", "-p", "listik", "--route", "low-pipeline",
                         "--json", *extra)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)["id"]

    def _show(self, task_id: str) -> dict:
        proc = self._run("show", task_id, "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_cli_changes_route_of_open_task(self) -> None:
        task_id = self._new()
        proc = self._run("set", task_id, "route=high-pipeline")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self._show(task_id)["launch_route"], "high-pipeline")

        proc = self._run("set", task_id, "launch_route=")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(self._show(task_id)["launch_route"])

    def test_cli_show_prints_route_without_autostart(self) -> None:
        task_id = self._new()
        proc = self._run("show", task_id)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("маршрут:     low-pipeline (без автостарта)", proc.stdout)

    def test_cli_refuses_route_change_after_claim(self) -> None:
        task_id = self._new()
        proc = self._run("claim", task_id, "--holder", "dsh")
        self.assertEqual(proc.returncode, 0, proc.stderr)

        proc = self._run("set", task_id, "route=high-pipeline")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("маршрут нельзя менять", proc.stderr)
        self.assertIn("статус «в работе»", proc.stderr)
        self.assertEqual(self._show(task_id)["launch_route"], "low-pipeline")

    def test_cli_noop_route_change_does_not_fail(self) -> None:
        task_id = self._new()
        proc = self._run("claim", task_id, "--holder", "dsh")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self._run("set", task_id, "route=low-pipeline")
        self.assertEqual(proc.returncode, 0, proc.stderr)


class RouteCliParserTests(unittest.TestCase):
    """Подсказка `set` упоминает маршрут — им пользуются и агенты, и человек."""

    def test_set_help_mentions_route(self) -> None:
        env = {**os.environ, "LISTIK_LOG": os.devnull}
        proc = subprocess.run([sys.executable, str(LISTIK_BIN), "--help"],
                              capture_output=True, text=True, env=env, cwd=str(REPO_DIR))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("route=low-pipeline", proc.stdout)


if __name__ == "__main__":
    unittest.main()
