судья: sonnet-5.5-high
модель: sonnet (pipeline-core:pipeline-judge, model: sonnet в вызове, effort high из frontmatter)
начало: 2026-09-29T12:12:48Z
конец: 2026-09-29T12:14:07Z
секунд: 47
---
красный

1. Критичный инвариант «при limit=N > 0 выдача содержит min(N, число свободных)» и п.3 ТЗ (`LIMIT` равен `limit`) нарушены: listik/deps.py:464 — `limit if limit > 1 else -1`; при `limit=1` параметр становится `-1`, то есть без ограничения. Ломающий сценарий (три свободные задачи demo, `deps.ready_tasks(conn, project="demo", limit=...)`): limit 1 -> 3 (ожидалось 1), limit 2 -> 2, limit 3 -> 3, limit 0 -> 3. Ни один новый тест не проверяет `limit=1`, поэтому набор зелёный.
2. listik/deps.py:464 — `limit=None` падает: `TypeError: '>' not supported between instances of 'NoneType' and 'int'`. Требование п.3: при ложном `limit` (`0`, `None`) лимита нет; до правки `None` работал (`limit * 3 if limit else 3000`).
3. tests/test_open_hard_edges.py — нет теста на границу `limit=1` (и на `limit=None`); пункт 1 и инвариант «min(N, свободные)» этим не закрыты.

Остальное по чек-листу (для справки): п.1-2, 4-5 — тесты есть и проходят; п.6-8, 11-12 — по диффу выполнены (`grep "3000\|limit \* 3" listik/deps.py` пуст, фильтр — `NOT EXISTS` в `WHERE` на `_open_hard_sql`, правило записано один раз, `git diff --name-only` = `listik/deps.py`, `tests/test_open_hard_edges.py`); п.9-10 — проходят. Инвариант про `claim`/живое правило — тестом п.4 подтверждён.
Проверки: чек-листовая команда unittest — Ran 64 tests, OK. Полный discover не запускал: дифф ограничен `ready_tasks`, узкий набор покрывает вызывающих.
Негативный контроль на старом коде (красность приёмочного теста до правки) не переносил на HEAD: вердикт красный по пунктам выше.
Коммита нет. Карточки нет, в Listik ничего не писал.
