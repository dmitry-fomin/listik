"""Изоляция прогона тестов: свежий временный каталог данных на процесс.

`listik.paths` считает `DATA_DIR`/`CONFIG_PATH`/`DB_PATH`/`LOGS_DIR`/`PID_PATH` на импорте
из `LISTIK_HOME` (иначе корень репозитория) и `LISTIK_CONFIG`/`LISTIK_DB`. `discover`
импортирует пакет `tests` раньше любого тестового модуля, поэтому здесь — до первого импорта
`listik` — выставляется `LISTIK_HOME` на свежий `tempfile.mkdtemp`, а `LISTIK_CONFIG` и
`LISTIK_DB` убираются из окружения, чтобы они не перебивали временный каталог боевыми
путями (listik-wksg). Подпроцессы (`bin/listik`, рой, e2e) наследуют то же окружение.
Каталог удаляется по завершении процесса.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# Свежий каталог данных на процесс тестов; создаётся до импорта listik.
TEST_HOME = tempfile.mkdtemp(prefix="listik-tests-")
os.environ["LISTIK_HOME"] = TEST_HOME
# Боевые переопределения путей не должны уводить прогон мимо временного каталога.
os.environ.pop("LISTIK_CONFIG", None)
os.environ.pop("LISTIK_DB", None)


def _remove_test_home() -> None:
    shutil.rmtree(TEST_HOME, ignore_errors=True)


atexit.register(_remove_test_home)
