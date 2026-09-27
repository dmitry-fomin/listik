"""Наборы статусов задач — единственное объявление для Python-логики и SQL-выборок.

`store`, `deps`, `documents` и `swarm_watch` берут наборы отсюда. Модуль листовой, ничего
не импортирует из пакета, поэтому его можно подключить из любого модуля без цикла импортов.
Рой держит тот же открытый набор в `swarm/decide.mjs` (`OPEN_STATUSES`) — это Node, общего
объявления с Python у него нет.
"""
from __future__ import annotations

OPEN_STATUSES = ("open", "in_progress", "blocked", "review")
FINAL_STATUSES = ("done", "cancelled")

#: Открытый набор как список SQL-литералов без скобок: `status IN ({OPEN_STATUSES_SQL})`.
#: Литералы, а не плейсхолдеры: значения — константы модуля, а не ввод, и фрагмент встаёт
#: в запросы, где позиции `?` уже заняты другими параметрами.
OPEN_STATUSES_SQL = ", ".join(f"'{s}'" for s in OPEN_STATUSES)
