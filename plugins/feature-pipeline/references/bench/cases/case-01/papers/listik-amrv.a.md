# listik-amrv.a — ready_tasks: фильтр блокированных в SQL, LIMIT limit

Рабочее дерево: {{TREE}}

Код правь только в этом дереве. Основное дерево репозитория
не трогай. Команды запускай из корня дерева.

## Контекст

`listik/deps.py`:

- `open_hard_edges(conn, *, issue_ids=None, depends_on=None)` (около строк 96–122) —
  по докстрингу «единственное определение правила в модуле». Ребро незакрыто, если
  `d.dep_type IN HARD_BLOCKERS` и `coalesce(t.status, 'open') NOT IN FINAL_STATUSES`,
  где `t` — задача-блокер из `LEFT JOIN tasks t ON t.id = d.depends_on`. Отсутствующий
  блокер (задачу удалили, ребро осталось) из-за `coalesce` считается открытым. Типы
  и статусы подставляются параметрами (`_HARD_MARKS`, `?`).
- `ready_tasks(conn, *, project, stage, include_occupied, limit=50, as_owner)` (около
  строк 419–461). Условия в `WHERE`: архив, статус, «не эпик с детьми», владелец в
  серверном режиме, проект, этап, держатель. Потом идёт
  `ORDER BY t.priority ASC, t.updated_at DESC LIMIT ?` с параметром
  `limit * 3 if limit else 3000`. Затем в Python строится словарь `blocked_by` по
  `open_hard_edges(conn)` (все рёбра базы), строки с блокером пропускаются, у остальных
  считается `waiting_for_count = len(waiting_for(conn, id))`, а цикл обрывается на
  `if limit and len(out) >= limit`.

В этом и дефект: блокированные отсекаются после `LIMIT`. Если первые `3*limit` строк по
приоритету все блокированы, функция возвращает пустой список, хотя ниже есть свободные
задачи.

`ready_tasks` вызывают `server.py` (`/api/ready`), `client.local_call` (`op == "ready"`),
`mcp.py` (`listik_ready`) и `store.board` (блок `ready`). Их не меняем: исправление в одной
функции чинит все пути.

Колонка `tasks.blocked_by` — денормализация, её обновляет `refresh_task`. Гейт `claim`
(`deps.ready` → `blockers` → `open_hard_edges`) на колонку не смотрит, он считает по живому
правилу. Поэтому и `ready_tasks` должна фильтровать по живому правилу, а не по колонке.

## Требования

1. **Общий фрагмент правила.** Условие «жёсткое ребро незакрыто» вынеси из
   `open_hard_edges` в один приватный помощник модуля `deps.py`. Помощник отдаёт
   SQL-фрагмент и его параметры и позволяет задать алиасы ребра и задачи-блокера. Внутри
   остаются типы из `HARD_BLOCKERS`, финальные статусы из `FINAL_STATUSES` (параметрами,
   не литералами строк) и правило «отсутствующий блокер открыт». Этим помощником
   пользуются `open_hard_edges` и `ready_tasks`, другой записи правила в модуле нет.
   Поведение `open_hard_edges` не меняется: сигнатура, набор и порядок строк, ключи
   `issue_id`/`depends_on`/`dep_type`, пустой `issue_ids` — `[]` без единого SQL-запроса.
   Докстринг `open_hard_edges` поправь так, чтобы он оставался верным: правило записано
   один раз, в помощнике, и им пользуются обе функции.

2. **Фильтр в SQL.** В `WHERE` запроса `ready_tasks` добавь условие: у задачи `t` нет ни
   одного незакрытого жёсткого ребра, где она ждущая (`deps.issue_id = t.id`). Условие
   строится на помощнике из п.1, например через `NOT EXISTS (SELECT 1 FROM deps … LEFT
   JOIN tasks … WHERE <ребро этой задачи> AND <фрагмент>)`. По колонке `t.blocked_by`
   не фильтровать.

3. **`LIMIT limit`.** Параметр `LIMIT` равен `limit`. При ложном `limit` (`0`, `None`)
   лимита нет: выборка возвращает все подходящие задачи, потолка 3000 больше нет. Вид
   записи выбери сам (`LIMIT -1` в SQLite или без `LIMIT`). Множитель `* 3`, число `3000`,
   словарь `blocked_by` из `open_hard_edges(conn)`, пропуск блокированных строк в цикле и
   обрыв `if limit and len(out) >= limit` из `ready_tasks` убрать.

4. **Что не меняется в ответе.** Порядок — `t.priority ASC, t.updated_at DESC`, как
   сейчас. Каждый элемент — `store.row_to_task(conn, row)` плюс `waiting_for_count`,
   посчитанный как сейчас. Остальные фильтры (`archived`, статусы `open`/`in_progress`/
   `review`, эпик с детьми, владелец, проект, этап, `include_occupied`) и вызов
   `expire_return_handoffs` в начале остаются на месте. Чтение через `_fetch`, которое
   на старой базе глотает `OperationalError`, тоже остаётся. Докстринг `ready_tasks`
   дополни одной фразой: `limit=0` — без ограничения.

5. **Тесты — в `tests/test_open_hard_edges.py`**, новыми методами класса
   `OpenHardEdgesTests`. Хелперы `_new`, `_hard`, `_blocked_by` в классе уже есть; у
   `store.create_task` есть аргумент `priority`. Сначала напиши тесты и прогони их на
   текущем коде, потом правь `deps.py`. `git stash` не использовать.

   - **Приёмка (красный до правки).** Блокер `X` — открытая задача в проекте `other`.
     Десять задач проекта `demo` с `priority=0`, каждая ждёт `X` (`self._hard(task, X)`). Три
     свободные задачи `demo` с разными приоритетами 1, 2, 3 (`F1`, `F2`, `F3`).
     `deps.ready_tasks(conn, project="demo", limit=2)` возвращает id ровно `[F1, F2]`,
     в этом порядке. На текущем коде возвращается `[]`.
   - **`waiting_for_count` на месте.** В том же сценарии `ready_tasks(conn,
     project="other")` возвращает `X` с `waiting_for_count == 10`, а у `F1` из прошлого
     пункта `waiting_for_count == 0`.
   - **`limit=0` без лимита (регрессия).** В том же сценарии
     `ready_tasks(conn, project="demo", limit=0)` возвращает ровно `{F1, F2, F3}`: три
     элемента, блокированных нет.
   - **Живое правило, а не колонка (регрессия).** Задачи `A`, `B` в `demo`. Ребро
     «`A` ждёт `B`» (`issue_id=A`, `depends_on=B`, `dep_type='blocks'`) вставь прямым
     `INSERT INTO deps(issue_id, depends_on, dep_type, created_by)` с `commit`, но без
     `refresh_task`. Предусловие: `_blocked_by(A) == []`.
     Проверки: `A` нет в `ready_tasks(conn, project="demo")`,
     `deps.ready(conn, A)["claimable"]` — `False`.
   - **Каждый жёсткий тип и отсутствующий блокер (регрессия).** Для каждого типа из
     `deps.HARD_BLOCKERS` (`subTest`, `_hard` умеет и `resource-blocks`) проверь: пока
     блокер открыт, ждущей задачи нет в `ready_tasks(project="demo")`, после закрытия
     блокера (`done`, для `waits-for` — `cancelled`, как в
     `test_every_hard_type_blocks_until_blocker_closes`) она там есть. Отдельно: у задачи
     с ребром `blocks` на блокер, удалённый прямым `DELETE FROM tasks`, в `ready_tasks`
     её нет.

   Существующие тесты не менять.

## Критичные инварианты

- `ready_tasks` не выдаёт задачу, которую `claim` отвергнет из-за незакрытого жёсткого
  блокера, даже если денормализованный `blocked_by` у неё отстал. Сценарий нарушения:
  ребро `blocks` вставлено в `deps` мимо `refresh_task` (колонка `'[]'`), а задача
  попала в выдачу `ready_tasks`. Это ровно то, что сделал бы фильтр по `t.blocked_by`.
- При `limit=N > 0` выдача содержит `min(N, число свободных подходящих задач)` элементов,
  сколько бы блокированных задач ни стояло выше по приоритету.

## Границы правки

- Меняются только `listik/deps.py` (новый приватный помощник, `open_hard_edges`,
  `ready_tasks`) и `tests/test_open_hard_edges.py` (новые методы). Больше ни одного файла.
- Не трогать `server.py`, `client.py`, `mcp.py`, `store.py` (в том числе `store.board`),
  `bin/listik`, `docs/API.md`. Обработка `limit` у вызывающих (`or 50`, `int(...)`,
  CLI `-n`) остаётся как есть.
- В `deps.py` не менять `blocked_tasks`, `blockers`, `waiting_for`, `ready`,
  `refresh_task`, `refresh_blocked_column`, `cycles`, `suggested`,
  `not_epic_with_children_sql`, `expire_return_handoffs`. Меняется только то, что
  пользуется новым помощником.
- Не менять `HARD_BLOCKERS`, `FINAL_STATUSES`, `SOFT_LINKS`, схему базы, индексы, не
  добавлять миграций.
- Не фильтровать по колонке `tasks.blocked_by` и не вызывать `refresh_*` из `ready_tasks`.
- Существующие тесты не править и не ослаблять их ассерты.
- Ничего «заодно»: не переименовывать, не форматировать соседний код, не чинить
  расхождение `limit=0` между HTTP и локальным путём.

## Как проверить

```sh
cd {{TREE}}
python3 -m unittest tests.test_open_hard_edges tests.test_deps.ReadyTasksTests \
  tests.test_deps.AddDepSuggestionTests tests.test_resource_blocks \
  tests.test_epic.NotTakenTests tests.test_claim.ExpireReturnWindowTests \
  tests.test_scope_schema.ScopeCardTests tests.test_owner_store.ServerModeListCase -v
grep -n "3000\|limit \* 3" listik/deps.py
```

Полный `unittest discover` не запускать. `grep` не должен ничего найти. Живую базу
Listik не трогать, `listik ready` на ней не запускать: всё проверяется тестами на
временной базе.

В отчёте исполнителя: вывод прогона тестов и для каждого нового теста одна строка — падал
ли он на старом коде и чем.
