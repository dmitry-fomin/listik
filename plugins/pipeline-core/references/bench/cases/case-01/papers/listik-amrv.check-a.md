# listik-amrv.check-a — приёмка порции a

Рабочее дерево: {{TREE}}

## Пункты

1. Приёмка автора. Блокер `X` открыт в проекте `other`, десять задач `demo` с
   `priority=0` ждут `X`, три свободные задачи `demo` с приоритетами 1, 2, 3 (`F1`, `F2`,
   `F3`). `deps.ready_tasks(conn, project="demo", limit=2)` возвращает id ровно
   `[F1, F2]`. Тест есть в `tests/test_open_hard_edges.py` и проходит. На коде до правки
   он падает: возвращается `[]`. Это видно по отчёту исполнителя, либо судья переносит
   тест на `HEAD` без правки `deps.py`.
2. В том же сценарии `ready_tasks(conn, project="other")` отдаёт `X` с
   `waiting_for_count == 10`, у `F1` `waiting_for_count == 0`. Тест есть и проходит.
3. `limit=0`: в том же сценарии `ready_tasks(conn, project="demo", limit=0)` возвращает
   ровно три задачи `{F1, F2, F3}`, блокированных среди них нет. Регрессия: тест
   проходит и до правки, и после.
4. Живое правило. Ребро `issue_id=A, depends_on=B, dep_type='blocks'` вставлено прямым
   `INSERT` без `refresh_task`, у `A` в колонке `blocked_by` лежит `[]`. `A` нет в
   `ready_tasks(project="demo")`, `deps.ready(conn, A)["claimable"]` — `False`. Тест
   есть и проходит.
5. Для каждого типа из `deps.HARD_BLOCKERS`, включая `resource-blocks`, задача с открытым
   блокером не попадает в `ready_tasks`, а после закрытия блокера (`done`/`cancelled`)
   попадает. Задача, чей блокер удалён прямым `DELETE FROM tasks`, в `ready_tasks` не
   попадает. Тест с `subTest` по типам есть и проходит.
6. В `ready_tasks` нет множителя `* 3`, числа `3000`, вызова `open_hard_edges(conn)`,
   Python-пропуска блокированных строк и обрыва `if limit and len(out) >= limit`.
   `grep -n "3000\|limit \* 3" listik/deps.py` пуст. Параметр `LIMIT` запроса равен
   `limit`, при ложном `limit` лимита нет. Проверяется чтением диффа.
7. Фильтр по блокерам — условие в `WHERE` того же SQL-запроса, что выбирает задачи
   (`NOT EXISTS` или равносильное). Он не использует колонку `t.blocked_by`. Проверяется
   чтением диффа и сценарием п.4.
8. Правило «жёсткое ребро незакрыто» записано в `deps.py` один раз: в приватном
   помощнике, которым пользуются и `open_hard_edges`, и `ready_tasks`. Типы берутся из
   `HARD_BLOCKERS`, финальные статусы из `FINAL_STATUSES`, оба параметрами. Отсутствующий
   блокер считается открытым (`LEFT JOIN` + `coalesce` или равносильное). Докстринг
   `open_hard_edges` соответствует новому устройству. Проверяется чтением диффа.
9. Поведение `open_hard_edges` не изменилось: `tests.test_open_hard_edges` проходит
   целиком, включая `test_filters`, где `issue_ids=[]` не порождает ни одного SQL-запроса.
10. Порядок выдачи прежний: `t.priority ASC, t.updated_at DESC`. Остальные фильтры
    (`archived`, статусы, эпик с детьми, владелец, проект, этап, `include_occupied`)
    и вызов `expire_return_handoffs` на месте. Тесты `test_epic.NotTakenTests`,
    `test_claim.ExpireReturnWindowTests`, `test_owner_store.ServerModeListCase`,
    `test_scope_schema.ScopeCardTests` проходят без правок.
11. `git diff --name-only` — ровно `listik/deps.py` и `tests/test_open_hard_edges.py`.
    Существующие методы в `tests/test_open_hard_edges.py` не изменены: дифф файла только
    добавляет строки.
12. В `deps.py` не изменены `blocked_tasks`, `blockers`, `waiting_for`, `ready`,
    `refresh_task`, `refresh_blocked_column`, `cycles`, `suggested`,
    `not_epic_with_children_sql`, `expire_return_handoffs`, константы `HARD_BLOCKERS`,
    `FINAL_STATUSES`, `SOFT_LINKS`. Проверяется чтением диффа.
13. Инвариант: `ready_tasks` не выдаёт задачу, которую `claim` отвергнет из-за
    незакрытого жёсткого блокера. Сценарий нарушения — п.4: судья проверяет его тестом,
    а не пересказом кода.

## Проверки порции

```sh
cd {{TREE}}
python3 -m unittest tests.test_open_hard_edges tests.test_deps.ReadyTasksTests \
  tests.test_deps.AddDepSuggestionTests tests.test_resource_blocks \
  tests.test_epic.NotTakenTests tests.test_claim.ExpireReturnWindowTests \
  tests.test_scope_schema.ScopeCardTests tests.test_owner_store.ServerModeListCase -v
grep -n "3000\|limit \* 3" listik/deps.py
git diff --name-only
```

Линтера и проверки типов для Python в проекте нет. Полный `unittest discover` не
запускать.
