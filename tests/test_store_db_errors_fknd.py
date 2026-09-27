"""Сбой базы не глотается в `board` и индексации документов (listik-fknd, порция a).

`sqlite3.DatabaseError` (и подклассы) выходит из `board()`/`create_task()`/
`update_task()` наружу — до 503 в `server.error_response`; прочие исключения
обрабатываются как раньше (пустой lint/ready, событие `document_error`).
Образец — `GetTaskDepsStateDbError*` в `tests/test_deps.py` (listik-88ef).
"""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from listik import store
from tests.helpers import TempDbTestCase
from tests.test_owner_http import LOCAL_CONFIG, OwnerHttpCase


def _event_count(conn, task_id: str, kind: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM events WHERE task_id=? AND kind=?", (task_id, kind)
    ).fetchone()[0]


class BoardLintDbErrorTests(TempDbTestCase):
    def test_lint_operational_error_propagates(self) -> None:
        store.create_task(self.conn, title="A", project="p")
        with mock.patch("listik.store.lint",
                        side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                store.board(self.conn, project="p")

    def test_lint_plain_database_error_propagates(self) -> None:
        # Сам DatabaseError, не подкласс: ловит слишком узкий `except OperationalError`.
        with mock.patch("listik.store.lint",
                        side_effect=sqlite3.DatabaseError("plain")):
            with self.assertRaises(sqlite3.DatabaseError):
                store.board(self.conn, project="p")

    def test_lint_other_error_still_swallowed(self) -> None:
        store.create_task(self.conn, title="A", project="p")
        with mock.patch("listik.store.lint", side_effect=ValueError("bad")):
            board = store.board(self.conn, project="p")
        self.assertEqual(board["lint"], {"count": 0, "items": []})


class BoardReadyDbErrorTests(TempDbTestCase):
    def test_ready_tasks_operational_error_propagates(self) -> None:
        store.create_task(self.conn, title="A", project="p")
        with mock.patch("listik.deps.ready_tasks",
                        side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                store.board(self.conn, project="p", ready_limit=5)

    def test_ready_tasks_other_error_still_swallowed(self) -> None:
        store.create_task(self.conn, title="A", project="p")
        with mock.patch("listik.deps.ready_tasks", side_effect=ValueError("bad")):
            board = store.board(self.conn, project="p", ready_limit=5)
        self.assertEqual(board["ready"], [])


class IndexDocumentsDbErrorTests(TempDbTestCase):
    def test_create_task_operational_error_propagates(self) -> None:
        with mock.patch("listik.documents.index_task_documents",
                        side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                store.create_task(self.conn, title="A", project="p",
                                  spec_path=str(self.tmp_path / "s.md"))

    def test_update_task_operational_error_propagates(self) -> None:
        tid = store.create_task(self.conn, title="A", project="p")["id"]
        with mock.patch("listik.documents.index_task_documents",
                        side_effect=sqlite3.OperationalError("boom")):
            with self.assertRaises(sqlite3.OperationalError):
                store.update_task(self.conn, tid, spec_path=str(self.tmp_path / "s.md"))
        self.assertEqual(_event_count(self.conn, tid, "document_error"), 0)

    def test_update_task_other_error_writes_document_error(self) -> None:
        spec = self.tmp_path / "s.md"
        tid = store.create_task(self.conn, title="A", project="p")["id"]
        with mock.patch("listik.documents.index_task_documents",
                        side_effect=ValueError("bad")):
            task = store.update_task(self.conn, tid, spec_path=str(spec))
        self.assertEqual(task["spec_path"], str(spec))
        self.assertEqual(_event_count(self.conn, tid, "document_error"), 1)


class BoardDbErrorHttpTests(OwnerHttpCase):
    """Сбой базы в ready_tasks на `GET /api/board` — 503 server_error, а не 200
    с пустым «можно взять»; 503 даёт именно подменённый вызов (listik-fknd)."""

    config_text = LOCAL_CONFIG

    def test_board_ready_tasks_db_error_is_503(self) -> None:
        self.make_task(title="A")
        with mock.patch("listik.deps.ready_tasks",
                        side_effect=sqlite3.OperationalError("boom")):
            status, payload = self.api("GET", "/api/board?project=demo")
        self.assertEqual(status, 503, payload)
        self.assertEqual(payload["code"], "server_error")
        self.assertIn("OperationalError", payload["error"])


if __name__ == "__main__":
    unittest.main()
