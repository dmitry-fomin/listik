"""Смена маршрута («типа запуска») задачи — store, API и CLI.

Маршрут живёт в колонке `launch_route`: его выбирают при создании, а потом можно
поменять у любой незакрытой задачи без держателя и без живого запуска (listik-zr05).
Этап не мешает и сохраняется; `launch_driver` (снимок режима старого маршрута)
сбрасывается. Иначе сервер отказывает понятной ошибкой (400), а доска, глядя на
`route_editable`, не показывает выбор. Новый ключ должен быть в таблице `routes`:
неизвестный — `bad_argument` раньше любых отказов по состоянию. Остальные колонки
запуска правкой карточки по-прежнему не меняются.

Два запрета проверяются отдельно: один вызов не может заодно закрыть задачу или
назначить держателя и сменить маршрут (в нём видны новые `status`/`holder`), а
гонку с `claim` на втором соединении ловит условный UPDATE.
"""
from __future__ import annotations

import contextlib
import io
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
from listik import errors
from listik import launcher
from listik import routes_store
from listik import server
from listik import store
from tests.helpers import TempDbTestCase

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
LISTIK_BIN = REPO_DIR / "bin" / "listik"


class RoutesSeeded(TempDbTestCase):
    """Временная база с маршрутами из `routes.json` репозитория: ключ смены
    маршрута обязан быть в таблице."""

    def setUp(self) -> None:
        super().setUp()
        routes_store.import_file(self.conn, REPO_DIR / "routes.json")

# Восемь колонок запуска, которые пишет только create_task/launcher (девятая,
# launch_route, — предмет этих тестов).
FROZEN_LAUNCH_FIELDS = ("autostart", "launched_by", "launch_pid", "launched_at",
                        "launch_log", "launch_exit_code", "launch_finished_at",
                        "launch_error")


class RouteStoreTests(RoutesSeeded):
    """store.update_task: смена маршрута и её запреты."""

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
        updated = store.update_task(self.conn, task["id"], route="grok")
        self.assertEqual(updated["launch_route"], "grok")

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

    def test_in_progress_without_holder_changes_route(self) -> None:
        task = self.task(route="low-pipeline")
        store.update_task(self.conn, task["id"], status="in_progress")
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], True)
        updated = store.update_task(self.conn, task["id"], route="grok")
        self.assertEqual(updated["launch_route"], "grok")
        self.assertEqual(updated["status"], "in_progress")

    def test_stage_is_kept_and_launch_driver_reset(self) -> None:
        for stage in ("s1-spec", "s3-impl"):
            with self.subTest(stage=stage):
                task = self.task(route="low-pipeline", stage=stage)
                self.conn.execute(
                    "UPDATE tasks SET launch_driver = 'swarm', launched_by = 'x', "
                    "launch_finished_at = '2026-01-01T00:00:00Z', generation = 7 "
                    "WHERE id = ?", (task["id"],))
                self.conn.commit()
                self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], True)
                labels_before = store.route_labels_from_row(self.row(task["id"]))

                updated = store.update_task(self.conn, task["id"], route="high-pipeline")
                self.assertEqual(updated["launch_route"], "high-pipeline")
                row = self.row(task["id"])
                self.assertEqual(row["stage"], stage)
                self.assertIsNone(row["launch_driver"])
                self.assertEqual(row["generation"], 7)
                self.assertEqual(row["launched_by"], "x")
                self.assertEqual(row["launch_finished_at"], "2026-01-01T00:00:00Z")
                self.assertEqual(
                    store.route_labels_from_row(row),
                    store.labels_after_route_change(self.conn, labels_before, "high-pipeline"))

    def test_clearing_route_resets_launch_driver(self) -> None:
        task = self.task(route="low-pipeline", stage="s3-impl")
        self.conn.execute("UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?",
                          (task["id"],))
        self.conn.commit()
        store.update_task(self.conn, task["id"], route="")
        self.assertIsNone(self.row(task["id"])["launch_driver"])

    def test_same_route_keeps_launch_driver(self) -> None:
        task = self.task(route="low-pipeline", stage="s3-impl")
        self.conn.execute("UPDATE tasks SET launch_driver = 'swarm' WHERE id = ?",
                          (task["id"],))
        self.conn.commit()
        out = store.update_task(self.conn, task["id"], route="low-pipeline")
        self.assertIs(out.get("unchanged"), True)
        self.assertEqual(self.row(task["id"])["launch_driver"], "swarm")
        self.assertEqual(self.route_events(task["id"]), [])

    def test_holder_refuses(self) -> None:
        task = self.task()
        store.update_task(self.conn, task["id"], holder="dsh")
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], False)
        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], route="grok")
        self.assertIn("маршрут нельзя менять", str(ctx.exception))
        self.assertIn("держит dsh", str(ctx.exception))
        self.assertIsNone(self.row(task["id"])["launch_route"])

    def test_live_launch_refuses(self) -> None:
        task = self.task(route="low-pipeline")
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'listik', launch_pid = 42, "
            "launched_at = '2026-01-01T00:00:00Z' WHERE id = ?", (task["id"],))
        self.conn.commit()
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], False)
        with self.assertRaises(ValueError) as ctx:
            store.update_task(self.conn, task["id"], route="grok")
        self.assertIn("маршрут нельзя менять", str(ctx.exception))
        self.assertIn("процесс запущен", str(ctx.exception))
        self.assertEqual(self.row(task["id"])["launch_route"], "low-pipeline")

    def test_finished_launch_allows_change(self) -> None:
        task = self.task(route="low-pipeline")
        self.conn.execute(
            "UPDATE tasks SET launched_by = 'listik', launch_pid = 42, "
            "launched_at = '2026-01-01T00:00:00Z', "
            "launch_finished_at = '2026-01-01T01:00:00Z' WHERE id = ?", (task["id"],))
        self.conn.commit()
        self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], True)
        updated = store.update_task(self.conn, task["id"], route="grok")
        self.assertEqual(updated["launch_route"], "grok")

    def test_final_status_refuses(self) -> None:
        for status in ("done", "cancelled"):
            with self.subTest(status=status):
                task = self.task(route="low-pipeline")
                store.update_task(self.conn, task["id"], status=status)
                self.assertIs(store.get_task(self.conn, task["id"])["route_editable"], False)
                with self.assertRaises(ValueError) as ctx:
                    store.update_task(self.conn, task["id"], route="grok")
                self.assertIn("маршрут нельзя менять", str(ctx.exception))
                self.assertIn("статус «", str(ctx.exception))
                self.assertEqual(self.row(task["id"])["launch_route"], "low-pipeline")

    def test_same_call_holder_or_close_cannot_change_route(self) -> None:
        """Один вызов не может назначить держателя или закрыть задачу и сменить
        маршрут: `status`/`holder` из тех же полей учитываются проверкой, и отказ
        не применяет ни одного поля."""
        task = self.task(route="low-pipeline")
        calls = (
            (("holder", "dsh"), ("route", "grok")),
            (("route", "grok"), ("holder", "dsh")),
            (("status", "done"), ("route", "grok")),
            (("status", "cancelled"), ("route", "grok")),
            (("holder", "dsh"), ("status", "in_progress"), ("route", "grok")),
        )
        for pairs in calls:
            with self.subTest(pairs=pairs):
                with self.assertRaises(ValueError) as ctx:
                    store.update_task(self.conn, task["id"], **dict(pairs))
                self.assertIn("маршрут нельзя менять", str(ctx.exception))
                row = self.row(task["id"])
                self.assertEqual(row["launch_route"], "low-pipeline", "маршрут не менялся")
                self.assertEqual(row["status"], "open", "отказ не применяет status")
                self.assertIsNone(row["holder"], "отказ не применяет holder")
                self.assertEqual(self.route_events(task["id"]), [])

    def test_same_call_clearing_stage_changes_route(self) -> None:
        """Этап смене не мешает — и снятие этапа в том же вызове тоже."""
        task = self.task(route="low-pipeline", stage="s1-spec")
        updated = store.update_task(self.conn, task["id"], stage="", route="grok")
        self.assertEqual(updated["launch_route"], "grok")
        self.assertFalse(self.row(task["id"])["stage"])
        self.assertEqual(self.route_events(task["id"]), [("low-pipeline", "grok")])

    def test_unknown_route_is_bad_argument_before_state_checks(self) -> None:
        """Неизвестный ключ — `BadArgument` «нет в базе» у любой карточки: и у
        открытой, и у закрытой, и у взятой (не «маршрут нельзя менять»)."""
        open_task = self.task(route="low-pipeline")
        done_task = self.task(route="low-pipeline")
        store.update_task(self.conn, done_task["id"], status="done")
        held_task = self.task(route="low-pipeline")
        store.update_task(self.conn, held_task["id"], holder="dsh")
        for task in (open_task, done_task, held_task):
            with self.subTest(task=task["id"]):
                with self.assertRaises(errors.BadArgument) as ctx:
                    store.update_task(self.conn, task["id"], route="нет-такого")
                message = str(ctx.exception)
                self.assertIn("маршрута нет-такого нет в базе; есть: ", message)
                self.assertIn("low-pipeline", message)
                self.assertNotIn("маршрут нельзя менять", message)
                self.assertEqual(self.row(task["id"])["launch_route"], "low-pipeline")
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


class RouteRaceTests(RoutesSeeded):
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


class RouteAutostartResetTests(RoutesSeeded):
    """Смена (и снятие) маршрута снимает прошлый отказ автостарта.

    `launch_error` и флаг «нужен человек» ставит отказ запуска (`launcher.refuse`
    одной транзакцией). Новый «тип запуска» к прежнему отказу не относится,
    поэтому маршрут уносит с собой и ошибку, и — если флаг поднял именно отказ —
    сам флаг; чужой вопрос человека остаётся. След — событие `route` с пояснением.
    """

    def setUp(self) -> None:
        super().setUp()
        self._conn_patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        self._conn_patch.start()
        self.addCleanup(self._conn_patch.stop)

    def refused_task(self, **fields) -> dict:
        """Задача, у которой автостарт отказал: `launch_error` + вопрос и флаг."""
        task = store.create_task(self.conn, title="проба", project="listik",
                                 route="low-pipeline", **fields)
        with contextlib.redirect_stderr(io.StringIO()):
            launcher.refuse(self.conn, task["id"], "маршрута low-pipeline нет в routes.json")
        return task

    def row(self, task_id: str):
        return self.conn.execute(
            "SELECT launch_error, needs_owner, launch_route FROM tasks WHERE id = ?",
            (task_id,)).fetchone()

    def route_note(self, task_id: str) -> str | None:
        return self.conn.execute(
            "SELECT note FROM events WHERE task_id = ? AND kind = 'route' "
            "ORDER BY rowid DESC LIMIT 1", (task_id,)).fetchone()["note"]

    def test_change_clears_error_and_flag_with_event_note(self) -> None:
        task = self.refused_task()
        self.assertEqual(self.row(task["id"])["needs_owner"], 1)

        updated = store.update_task(self.conn, task["id"], route="high-pipeline",
                                    actor="agent:dsh", harness="dsh")
        self.assertEqual(updated["launch_route"], "high-pipeline")
        self.assertIsNone(updated["launch_error"])
        self.assertIs(updated["needs_owner"], False)
        self.assertIsNone(self.row(task["id"])["launch_error"])
        self.assertEqual(self.row(task["id"])["needs_owner"], 0)

        note = self.route_note(task["id"])
        self.assertIn("снята ошибка автостарта", note)
        self.assertIn("маршрута low-pipeline нет в routes.json", note)
        self.assertIn("снят флаг «нужен человек»", note)
        # Вопрос автостарта остаётся в истории: снятие флага не переписывает прошлое.
        question = self.conn.execute(
            "SELECT text, kind FROM comments WHERE task_id = ? AND kind = 'question'",
            (task["id"],)).fetchall()
        self.assertEqual([c["text"] for c in question],
                         ["автостарт не выполнен: маршрута low-pipeline нет в routes.json — "
                          "нужен ты"])

    def test_clearing_route_also_clears_error_and_flag(self) -> None:
        task = self.refused_task()
        updated = store.update_task(self.conn, task["id"], launch_route="", actor="me")
        self.assertIsNone(updated["launch_route"])
        self.assertIsNone(updated["launch_error"])
        self.assertIs(updated["needs_owner"], False)
        self.assertIn("снят флаг «нужен человек»", self.route_note(task["id"]))

    def test_error_is_cleared_even_without_flag(self) -> None:
        """Флаг уже снял человек: смена маршрута всё равно убирает ошибку."""
        task = self.refused_task()
        store.set_needs_owner(self.conn, task["id"], value=False, text="беру на себя",
                              actor="me")
        updated = store.update_task(self.conn, task["id"], route="medium-pipeline")
        self.assertIsNone(updated["launch_error"])
        self.assertIs(updated["needs_owner"], False)
        note = self.route_note(task["id"])
        self.assertIn("снята ошибка автостарта", note)
        self.assertNotIn("флаг", note)

    def test_foreign_question_after_refusal_keeps_flag(self) -> None:
        """Вопрос, заданный уже после отказа, — не автостартный: флаг остаётся."""
        task = self.refused_task()
        store.set_needs_owner(self.conn, task["id"], value=True,
                              text="и ещё: какую ветку брать?", actor="agent:dsh")
        updated = store.update_task(self.conn, task["id"], route="high-pipeline")
        self.assertIsNone(updated["launch_error"])
        self.assertIs(updated["needs_owner"], True)
        note = self.route_note(task["id"])
        self.assertIn("снята ошибка автостарта", note)
        self.assertNotIn("флаг", note)

    def test_question_without_launch_error_is_untouched(self) -> None:
        """Флаг без ошибки автостарта смена маршрута не трогает (и пояснений нет)."""
        task = store.create_task(self.conn, title="проба", project="listik",
                                 route="low-pipeline")
        store.set_needs_owner(self.conn, task["id"], value=True,
                              text="нужен ответ человека", actor="me")
        updated = store.update_task(self.conn, task["id"], route="high-pipeline")
        self.assertIs(updated["needs_owner"], True)
        self.assertIsNone(self.route_note(task["id"]))

    def test_same_route_does_not_touch_error(self) -> None:
        """Смены не было — отказ автостарта остаётся как есть."""
        task = self.refused_task()
        out = store.update_task(self.conn, task["id"], route="low-pipeline")
        self.assertIs(out.get("unchanged"), True)
        row = self.row(task["id"])
        self.assertEqual(row["launch_error"], "маршрута low-pipeline нет в routes.json")
        self.assertEqual(row["needs_owner"], 1)

    def test_refused_route_change_keeps_error(self) -> None:
        """Отказ смены (есть держатель) не снимает ни ошибку, ни флаг."""
        task = self.refused_task()
        store.update_task(self.conn, task["id"], holder="dsh")
        with self.assertRaises(ValueError):
            store.update_task(self.conn, task["id"], route="high-pipeline")
        row = self.row(task["id"])
        self.assertEqual(row["launch_route"], "low-pipeline")
        self.assertEqual(row["launch_error"], "маршрута low-pipeline нет в routes.json")
        self.assertEqual(row["needs_owner"], 1)

    def test_error_without_question_history_is_cleared(self) -> None:
        """Отказ, пришедший без вопроса в истории (импорт/фикстура): флаг тоже снимаем."""
        task = store.create_task(self.conn, title="проба", project="listik",
                                 route="low-pipeline")
        self.conn.execute("UPDATE tasks SET launch_error = ?, needs_owner = 1 WHERE id = ?",
                          ("подмена", task["id"]))
        self.conn.commit()
        updated = store.update_task(self.conn, task["id"], route="high-pipeline")
        self.assertIsNone(updated["launch_error"])
        self.assertIs(updated["needs_owner"], False)


class RouteApiTests(RoutesSeeded):
    """PATCH /api/tasks/{id}: `route`/`launch_route`, отказы и неизвестный ключ."""

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

    def test_patch_route_is_400_once_taken(self) -> None:
        store.claim(self.conn, self.task["id"], holder="dsh")
        with self.assertRaises(server.ApiError) as ctx:
            self.patch({"route": "grok"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("маршрут нельзя менять", ctx.exception.message)
        self.assertIn("держит dsh", ctx.exception.message)
        self.assertEqual(self.row_route(), "low-pipeline")

    def test_patch_route_on_stage_passes(self) -> None:
        staged = store.create_task(self.conn, title="этап", project="listik", stage="s3-impl")
        status, task = self.patch({"route": "grok"}, task_id=staged["id"])
        self.assertEqual(status, 200)
        self.assertEqual(task["launch_route"], "grok")
        self.assertEqual(task["stage"], "s3-impl")

    def test_patch_unknown_route_is_400_bad_argument(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch({"route": "нет-такого"})
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("маршрута нет-такого нет в базе", ctx.exception.message)
        self.assertEqual(self.row_route(), "low-pipeline")

    def test_patch_route_400_on_non_string(self) -> None:
        with self.assertRaises(server.ApiError) as ctx:
            self.patch({"route": ["grok"]})
        self.assertEqual(ctx.exception.status, 400)
        self.assertIn("маршрут должен быть строкой", ctx.exception.message)

    def test_patch_same_call_holder_or_close_and_route_is_400(self) -> None:
        """Один PATCH не может закрыть задачу или назначить держателя и сменить
        маршрут — ни одного поля."""
        for body in ({"status": "done", "route": "grok"},
                     {"route": "grok", "holder": "dsh"}):
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

    def test_patch_route_clears_autostart_error_and_flag(self) -> None:
        """Доска шлёт тот же PATCH: смена маршрута снимает отказ автостарта."""
        with contextlib.redirect_stderr(io.StringIO()):
            launcher.refuse(self.conn, self.task["id"],
                            "маршрута low-pipeline нет в routes.json")
        status, task = self.patch({"route": "high-pipeline", "actor": "me"})
        self.assertEqual(status, 200)
        self.assertEqual(task["launch_route"], "high-pipeline")
        self.assertIsNone(task["launch_error"])
        self.assertIs(task["needs_owner"], False)
        row = self.conn.execute("SELECT launch_error, needs_owner FROM tasks WHERE id = ?",
                                (self.task["id"],)).fetchone()
        self.assertIsNone(row["launch_error"])
        self.assertEqual(row["needs_owner"], 0)

    def test_patch_empty_route_clears_autostart_error_and_flag(self) -> None:
        """Пункт «без маршрута» доски: пустая строка снимает маршрут и отказ."""
        with contextlib.redirect_stderr(io.StringIO()):
            launcher.refuse(self.conn, self.task["id"], "нет рабочего каталога")
        status, task = self.patch({"route": "", "actor": "me"})
        self.assertEqual(status, 200)
        self.assertIsNone(task["launch_route"])
        self.assertIsNone(task["launch_error"])
        self.assertIs(task["needs_owner"], False)


class RouteMcpTests(RoutesSeeded):
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


class RouteCliTests(RoutesSeeded):
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
        self.assertIn("держит dsh", proc.stderr)
        self.assertEqual(self._show(task_id)["launch_route"], "low-pipeline")

    def test_cli_changes_route_of_staged_task(self) -> None:
        task_id = self._new("--stage", "s3-impl")
        proc = self._run("set", task_id, "route=high-pipeline")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        shown = self._show(task_id)
        self.assertEqual(shown["launch_route"], "high-pipeline")
        self.assertEqual(shown["stage"], "s3-impl")

    def test_cli_unknown_route_is_bad_argument_json(self) -> None:
        task_id = self._new()
        proc = self._run("set", task_id, "route=нет-такого", "--json")
        self.assertNotEqual(proc.returncode, 0)
        error = json.loads(proc.stdout)["error"]
        self.assertEqual(error["code"], "bad_argument")
        self.assertIn("маршрута нет-такого нет в базе", error["message"])
        self.assertEqual(self._show(task_id)["launch_route"], "low-pipeline")

    def test_cli_noop_route_change_does_not_fail(self) -> None:
        task_id = self._new()
        proc = self._run("claim", task_id, "--holder", "dsh")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self._run("set", task_id, "route=low-pipeline")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_cli_route_change_clears_launch_error(self) -> None:
        """Смена маршрута снимает ошибку запуска и флаг «нужен человек»,
        если флаг поднял сам отказ (`launcher.refuse`)."""
        from listik import launcher

        task_id = self._new()
        launcher.refuse(self.conn, task_id, "сервер Listik не запущен")
        shown = self._show(task_id)
        self.assertEqual(shown["launch_error"], "сервер Listik не запущен")
        self.assertIs(shown["needs_owner"], True)

        proc = self._run("set", task_id, "route=high-pipeline")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        shown = self._show(task_id)
        self.assertEqual(shown["launch_route"], "high-pipeline")
        self.assertIsNone(shown["launch_error"])
        self.assertIs(shown["needs_owner"], False)

        proc = self._run("set", task_id, "launch_route=")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(self._show(task_id)["launch_route"])


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
