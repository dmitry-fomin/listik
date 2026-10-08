судья: sonnet-5.5-high
модель: sonnet (feature-pipeline:pipeline-judge, model: sonnet в вызове, effort high из frontmatter)
начало: 2026-09-29T12:12:48Z
конец: 2026-09-29T12:14:34Z
секунд: 74
---
зелёный
8bdf9ad

Пункты чек-листа:
0. Предпосылки: OPEN_STATUSES store.py:29 и deps.py:47 совпадают; "blocked_by": blockers store.py:1728; в board server_cfg/check_owner/owner-фильтр стоят выше tasks = [row_to_task (строки 13/15/17/29 тела board); server.py as_owner=owner; три тестовых символа на месте; node печатает 14 (после правки) — закрыт.
1. Основной сценарий (3 == 3, {A1,A4,A5}) — закрыт, тест проходит.
2. include_closed (0 и 0, предусловие blocked_by == [B]) — закрыт.
3. Серверный режим (1, 1, 2) — закрыт.
4. LinkTypesDictionaryTests (уникальность, DEP_TITLES, иконки) — закрыт.
5. Негативный контроль на снимке до порции (git archive merge-base + новый тест): Ran 6 tests, FAILED (failures=3): "AssertionError: 4 != 3", "AssertionError: 1 != 0", словарь != DEP_TITLES (нет conditional-blocks/resource-blocks); серверный тест ok — закрыт.
6. dictionaries.ts: +2 строки, 0 удалено, порядок как в ТЗ; node печатает 14 — закрыт.
7. Дифф board: удалён старый блок (statuses/blocked_ids/SQL/try/except/начальный blocked_count = 0), добавлено одно sum(...) с комментарием, на прежнем месте — закрыт.
8. grep "blocked_ids|statuses = |FROM deps" — пусто; grep -c "try:" — 2; grep -c "blocked_count =" — 1 — закрыт.
9. Остальная доска: ready_list и его try/except не тронуты, ключи ответа прежние; tests.test_board_lint, test_board_stage_columns, test_assigned_not_taken, test_owner_store проходят без правок — закрыт.
10. docs/API.md: одна строка таблицы /api/board, blocked_count в перечне + определение — закрыт.
11. git status до коммита — ровно 4 ожидаемые строки; deps.py не тронут — закрыт.

Проверки: unittest шести модулей из «Проверок порции» — Ran 164 tests, OK. Полный discover не запускал: дифф ограничен блоком board, словарём и докой, чек-лист запрещает полный прогон.
Срезанных углов нет: ассерты не ослаблены, скипов/xfail нет, существующие тесты не менялись, границы правки соблюдены, дубликатов существующих хелперов нет. Секретов в диффе нет.
Критичных инвариантов в чек-листе нет; сценарии отказа (закрытый блокер своего и чужого проекта, resource-blocks, suggested-blocks, отменённая при include_closed, фильтр владельца) покрыты тестами и прогнаны выше.
Перенесено: нет. typecheck не запускал (в дереве нет web/node_modules, по чек-листу не нужен).
Коммит: один, 8bdf9ad; пути: docs/API.md, listik/store.py, tests/test_board_blocked_count.py, web/src/lib/dictionaries.ts.
Карточка: карточки нет.
