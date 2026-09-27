"""Наборы статусов задач — единственное объявление для Python-логики и SQL-выборок.

`store`, `deps`, `documents` и `swarm_watch` берут наборы отсюда. Модуль листовой, ничего
не импортирует из пакета, поэтому его можно подключить из любого модуля без цикла импортов.
Рой держит тот же открытый набор в `swarm/decide.mjs` (`OPEN_STATUSES`) — это Node, общего
объявления с Python у него нет.
"""
from __future__ import annotations

OPEN_STATUSES = ("open", "in_progress", "blocked", "review")
FINAL_STATUSES = ("done", "cancelled")

#: Полный набор статусов: сначала открытые, затем финальные — порядок именно такой.
ALL_STATUSES = OPEN_STATUSES + FINAL_STATUSES

#: «Задача в работе у исполнителя/судьи» — подмножество OPEN_STATUSES.
RUNNING_STATUSES = ("in_progress", "review")

#: Одиночное значение для SQL-сравнений `status = '…'`; строка из OPEN_STATUSES.
IN_PROGRESS = "in_progress"
#: Одиночное значение для SQL-сравнений `status = '…'`; строка из FINAL_STATUSES.
DONE = "done"

#: Открытый набор как список SQL-литералов без скобок: `status IN ({OPEN_STATUSES_SQL})`.
#: Литералы, а не плейсхолдеры: значения — константы модуля, а не ввод, и фрагмент встаёт
#: в запросы, где позиции `?` уже заняты другими параметрами.
OPEN_STATUSES_SQL = ", ".join(f"'{s}'" for s in OPEN_STATUSES)
#: Рабочий набор тем же способом, что OPEN_STATUSES_SQL: `status IN ({RUNNING_STATUSES_SQL})`.
RUNNING_STATUSES_SQL = ", ".join(f"'{s}'" for s in RUNNING_STATUSES)
