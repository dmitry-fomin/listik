"""Страж каталога логов запуска: прогон тестов не пишет в настоящий `paths.LOGS_DIR`.

Логи автостарта лежат в `paths.LOGS_DIR` (`<DATA_DIR>/logs`), каталог в `.gitignore`,
поэтому мусор из тестов никто не замечал: за полгода в нём накопилось 376 пустых файлов
проекта `proj` от прогонов, и настоящий лог запуска стало не найти глазами (listik-fcel).
Источник — тесты, которые зовут `launcher.start` без `log_dir` (запуск через сервер:
`POST /api/tasks` с `autostart`, `POST /api/tasks/{id}/launch`): `paths.LOGS_DIR` посчитан
на импорте модуля, и подмена `paths.ROOT_DIR` на него не действует — подменять надо
`paths.LOGS_DIR` (это делает `tests.test_autostart.AutostartTestCase`).

Имя модуля начинается с `test_zz`, чтобы при `python3 -m unittest discover tests`
(модули берутся в алфавитном порядке) страж отработал последним, уже после всех
запускающих тестов. Снимок каталога берётся на импорте модуля: discover импортирует все
тестовые модули до того, как запустит хоть один тест, поэтому снимок — это состояние
каталога до прогона.
"""
from __future__ import annotations

import unittest

from listik import paths


def _snapshot() -> set[str]:
    real = paths.LOGS_DIR
    if not real.is_dir():
        return set()
    return {entry.name for entry in real.iterdir()}


# Состояние настоящего каталога логов до прогона (см. докстроку модуля).
BEFORE = _snapshot()


class LogsDirGuardTests(unittest.TestCase):
    def test_test_run_leaves_no_files_in_real_logs_dir(self) -> None:
        appeared = sorted(_snapshot() - BEFORE)
        self.assertEqual(
            appeared, [],
            "прогон тестов насорил в настоящем каталоге логов "
            f"{paths.LOGS_DIR}: {appeared}. Тест, который зовёт launcher.start "
            "(в том числе через сервер), должен подменять paths.LOGS_DIR — "
            "см. tests.test_autostart.AutostartTestCase.setUp")
