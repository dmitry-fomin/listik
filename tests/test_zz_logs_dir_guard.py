"""Страж каталога логов запуска: прогон тестов не пишет в настоящий `paths.LOGS_DIR`.

Логи автостарта лежат в `paths.LOGS_DIR` (`<DATA_DIR>/logs`), каталог в `.gitignore`,
поэтому мусор из тестов никто не замечал: за полгода в нём накопилось 376 пустых файлов
проекта `proj` от прогонов, и настоящий лог запуска стало не найти глазами (listik-fcel).
Источник — тесты, которые зовут `launcher.start` без `log_dir` (запуск через сервер:
`POST /api/tasks` с `autostart`, `POST /api/tasks/{id}/launch`): `paths.LOGS_DIR` посчитан
на импорте модуля, и подмена `paths.ROOT_DIR` на него не действует — подменять надо
`paths.LOGS_DIR` (это делает `tests.test_autostart.AutostartTestCase`).

Имя модуля начинается с `test_zz`, чтобы при `python3 -m unittest discover -s tests -t .`
(модули берутся в алфавитном порядке) страж отработал последним, уже после всех
запускающих тестов. Снимок каталога берётся на импорте модуля: discover импортирует все
тестовые модули до того, как запустит хоть один тест, поэтому снимок — это состояние
каталога до прогона.

Каталог логов теперь временный: `tests/__init__.py` уводит `LISTIK_HOME` в свежий
`listik-tests-*` до первого импорта `listik`, так что боевой рой, пишущий в
`~/.listik/logs`, на этот прогон не влияет. Страж по-прежнему ловит тест, который
насорил в `paths.LOGS_DIR` без подмены.
"""
from __future__ import annotations

import unittest
from pathlib import Path

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


class DataDirIsolationTests(unittest.TestCase):
    def test_data_dir_is_temporary_and_config_lives_inside(self) -> None:
        """`tests/__init__.py` уводит прогон в свежий временный каталог: `DATA_DIR` —
        ни боевой `~/.listik`, ни корень репозитория, а `CONFIG_PATH` лежит внутри него."""
        self.assertNotEqual(paths.DATA_DIR, Path.home() / ".listik")
        self.assertNotEqual(paths.DATA_DIR, paths.ROOT_DIR)
        self.assertIn(paths.DATA_DIR, paths.CONFIG_PATH.parents)
