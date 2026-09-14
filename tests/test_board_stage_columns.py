"""Столбец «Второе мнение»: как карточка попадает в колонку s2-review.

Требование listik-06ho: когда конвейер включает второе мнение (критик ТЗ), задача
переводится на этап `s2-review`, и доска показывает её в колонке «2. Второе мнение».
Колонку выбирает поле `stage`; метки `harness:<x>`/`process:<y>` (их выводит сервер из
маршрута — `routes.labels_for`) колонку не выбирают — они только для человека и поиска.
"""
from __future__ import annotations

import unittest
from unittest import mock

from listik import server, store
from tests.helpers import TempDbTestCase

S2 = "s2-review"
S2_TITLE = "2. Второе мнение"


def column(board: dict, key: str) -> dict:
    return next((item for item in board["columns"] if item["key"] == key), {})


def ids(board: dict, key: str) -> list[str]:
    return [task["id"] for task in column(board, key).get("tasks", [])]


class BoardStageColumnTests(TempDbTestCase):
    """store.board(group_by="stage") — то, что отдаёт GET /api/board."""

    def test_s2_review_task_lands_in_second_opinion_column(self) -> None:
        task = store.create_task(self.conn, title="Критик ТЗ", project="demo", stage=S2)

        board = store.board(self.conn, group_by="stage")

        self.assertEqual(column(board, S2)["title"], S2_TITLE)
        self.assertEqual(ids(board, S2), [task["id"]])
        self.assertEqual(ids(board, "s1-spec"), [])

    def test_critic_stage_after_spec_is_s2_and_keeps_holder(self) -> None:
        # Ровно то, что делает пайплайн при запуске критика: ТЗ написано на s1,
        # `stage` переводит задачу на s2; переход s1→s2 sticky — держатель остаётся.
        task = store.create_task(self.conn, title="ТЗ и критик", project="demo")
        store.claim(self.conn, task["id"], holder="claude", harness="claude")
        spec = store.next_stage(self.conn, task["id"], holder="claude", harness="claude")
        review = store.next_stage(self.conn, task["id"], holder="claude", harness="claude")

        self.assertEqual(spec["stage"], "s1-spec")
        self.assertEqual(review["stage"], S2)
        self.assertEqual(review["holder"], "claude")
        self.assertIn(task["id"], ids(store.board(self.conn, group_by="stage"), S2))

    def test_explicit_to_s2_from_scratch(self) -> None:
        # Пайплайн может выставить этап явно (`stage <id> --to s2-review`), не проходя s1.
        task = store.create_task(self.conn, title="Сразу критик", project="demo")

        moved = store.next_stage(self.conn, task["id"], to_stage=S2)

        self.assertEqual(moved["stage"], S2)
        self.assertIn(task["id"], ids(store.board(self.conn, group_by="stage"), S2))

    def test_route_labels_alone_do_not_choose_the_column(self) -> None:
        # Метки маршрута столбец не выбирают: без этапа карточка в «Заведена»,
        # а не в «Втором мнении» — туда её переводит только этап.
        task = store.create_task(self.conn, title="Только метки", project="demo",
                                 labels=["harness:claude", "process:feature-pipeline"])

        board = store.board(self.conn, group_by="stage")

        self.assertEqual(ids(board, "none"), [task["id"]])
        self.assertEqual(ids(board, S2), [])


class BoardApiTests(TempDbTestCase):
    """Тот же срез через HTTP-слой: GET /api/board?group_by=stage."""

    def setUp(self) -> None:
        super().setUp()
        patch = mock.patch.object(server, "get_conn", return_value=self.conn)
        patch.start()
        self.addCleanup(patch.stop)

    def test_board_api_returns_second_opinion_column_with_card(self) -> None:
        task = store.create_task(self.conn, title="Критик ТЗ", project="demo", stage=S2)

        status, board = server.handle("GET", "/api/board", {"group_by": ["stage"]}, {},
                                      authed=True)

        self.assertEqual(status, 200)
        self.assertEqual(column(board, S2)["title"], S2_TITLE)
        self.assertEqual(ids(board, S2), [task["id"]])

    def test_board_api_keeps_column_when_empty(self) -> None:
        # Колонка «Второе мнение» есть в ряду доски всегда — даже пустая,
        # иначе сетка колонок и рельса переходов разъезжаются.
        _, board = server.handle("GET", "/api/board", {"group_by": ["stage"]}, {}, authed=True)

        self.assertEqual(column(board, S2)["title"], S2_TITLE)
        self.assertEqual(column(board, S2)["tasks"], [])


if __name__ == "__main__":
    unittest.main()
