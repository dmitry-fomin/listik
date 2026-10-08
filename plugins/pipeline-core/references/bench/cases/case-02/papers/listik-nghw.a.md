Рабочее дерево: {{TREE}}

# listik-nghw.a — blocked_count доски по blocked_by; LINK_TYPES знает все типы связей

Весь код пишется в этом дереве. Основное дерево репозитория не трогать.
Ничего не коммитить без команды оркестратора.

## Контекст

**Как сейчас считается `blocked_count`.** Функция `store.board` (`listik/store.py`, `def board`)
считает его в блоке после `ready_list`. Сначала собирается словарь
`statuses = {t["id"]: t["status"] for t in tasks}`. Потом отдельный SQL-запрос по `deps`
перебирает рёбра с литералом типов `('blocks','blocked-by','waits-for','conditional-blocks')`,
и ребро считается открытым, если `statuses.get(depends_on, "open") not in FINAL_STATUSES`.
Весь блок обёрнут в `try/except` и при ошибке даёт 0. Здесь три ошибки:

1. В `tasks` лежат только карточки самой доски: без `include_closed` это открытые
   неархивные задачи, а с `project` — ещё и только этого проекта. Закрытого блокера в словаре
   нет, `get(..., "open")` считает его открытым, и задача с уже закрытым блокером попадает
   в «Ждёт».
2. С блокером из другого проекта то же самое: он считается открытым, даже если закрыт.
3. В литерале нет `resource-blocks`, поэтому задача, которую держит только ресурсный
   блокер, не считается.

**Эталон — `/api/blocked`** (`deps.blocked_tasks`). Он отдаёт неархивные задачи со статусом
из `OPEN_STATUSES` (`open`, `in_progress`, `blocked`, `review`), у которых непуст
`deps.blockers()`. `blockers()` — это рёбра типов `deps.HARD_BLOCKERS` (пять типов, включая
`resource-blocks`), у которых задача-блокер не в `FINAL_STATUSES` (`done`, `cancelled`).
Отсутствующая задача-блокер считается открытой.

**`blocked_by` уже есть у каждой карточки доски.** Это денормализованный столбец
`tasks.blocked_by`: JSON-список id незакрытых жёстких блокеров. Карточка получает его через
`row_to_task`. Столбец пересчитывает `deps.refresh_task` на каждой правке связи и смене
статуса (`store.update_task`, `add_dep`, `remove_dep`, `deps.apply_resource_blocks`…), по тому
же правилу, что `blockers()`.

**Словарь доски.** `LINK_TYPES` лежит в `web/src/lib/dictionaries.ts`. По его комментарию
подписи в нём совпадают с `DEP_TITLES` из `listik/deps.py`. Но двух типов из `DEP_TITLES`
в словаре нет: `conditional-blocks` («блокирует условно») и `resource-blocks` («ресурсный
блокер»). Незнакомый тип доска показывает сырым ключом.

## Предпосылки (проверены при написании ТЗ)

Всё ниже есть в коде на момент написания ТЗ. Перед правкой исполнитель подтверждает это
командами из конца раздела и кладёт их вывод в отчёт. Если чего-то нет или оно устроено
иначе, код не писать: остановиться и сообщить.

- `OPEN_STATUSES` есть в `listik/store.py` на уровне модуля, значение
  `("open", "in_progress", "blocked", "review")`. Оно совпадает с `deps.OPEN_STATUSES` и
  со статусами, которые отдаёт `deps.blocked_tasks`. Внутри `store.py` константа доступна
  под этим именем.
- `row_to_task` кладёт в карточку `"blocked_by": blockers`: список, разобранный из
  `tasks.blocked_by`.
- Фильтр владельца стоит в `board` до сборки `tasks`. В начале функции
  `owner_cfg = server_cfg()`, потом `config_mod.check_owner(as_owner, owner_cfg)`, потом
  условие `(owner = ? OR owner IS NULL)` в `WHERE` запроса, из строк которого строится
  `tasks`. Сервер передаёт в `board` `as_owner=owner` из заголовка `X-Listik-Owner`
  (`listik/server.py`, ветка `/api/board`). Поэтому `tasks` к моменту подсчёта уже
  отфильтрован по владельцу, по проекту и (без `include_closed`) по статусу. В локальном
  режиме (`server_cfg()` → `None`) фильтра владельца нет.
- `store.add_dep(conn, x, y, "blocks", created_by="agent:codex")` без `confirm` пишет
  `suggested-blocks`. Это проверяет `tests/test_deps.py`,
  `AddDepSuggestionTests.test_agent_without_confirm_records_suggestion_regardless_of_stage`.
  С `created_by="me"` жёсткая связь пишется сразу (`ReadyTasksTests` там же).
- В тестах есть: функция `board_icon_names()` на уровне модуля
  `tests/test_routes_config.py`; функция `_insert_resource_block(conn, issue_id, depends_on)`
  на уровне модуля `tests/test_resource_blocks.py`; класс `OwnerStoreCase` в
  `tests/test_owner_store.py` (метод `server_mode()` подменяет `config.toml` серверным
  режимом с пользователями `ann` и `bob`, `tearDown` возвращает конфиг).
- `node` (в окружении v26) импортирует `web/src/lib/dictionaries.ts` напрямую: в файле
  только `import type`, его node вырезает. Сейчас в `LINK_TYPES` 12 записей.

```sh
cd {{TREE}}
grep -n "^OPEN_STATUSES" listik/store.py listik/deps.py
grep -n '"blocked_by": blockers' listik/store.py
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -n "server_cfg()\|check_owner\|owner = ? OR owner IS NULL\|tasks = \[row_to_task"
grep -n "as_owner=owner" listik/server.py
grep -n "^def board_icon_names\|^def _insert_resource_block\|^class OwnerStoreCase\|def server_mode" tests/test_routes_config.py tests/test_resource_blocks.py tests/test_owner_store.py
node --input-type=module -e "const m = await import('./web/src/lib/dictionaries.ts'); console.log(m.LINK_TYPES.length)"
```

## Порядок работы

1. Проверить предпосылки (раздел выше).
2. Написать `tests/test_board_blocked_count.py` (п.4) и прогнать его **до правки** кода:
   `python3 -m unittest tests.test_board_blocked_count`. Ожидаемые падения:
   - основной сценарий — `AssertionError: 4 != 3`;
   - сценарий `include_closed` — `AssertionError: 1 != 0`;
   - тест словаря — нет ключей `conditional-blocks` и `resource-blocks`;
   - тест серверного режима проходит: он страхует от регрессии и до правки не падает.

   Вывод прогона дословно (строки `FAIL:`/`AssertionError` с числами) идёт в отчёт.
   Падения другого вида, или если тест прошёл, — это повод остановиться и сообщить, а не
   подгонять тест.
3. Сделать правки п.1–3 и прогнать проверки из «Как проверить».

## Требования

1. **`store.board`.** `blocked_count` — это число карточек из уже собранного списка `tasks`,
   у которых непуст `blocked_by` и `status` входит в `OPEN_STATUSES` (константа модуля
   `store.py`). Считается одним выражением над `tasks`, и в `board` это единственное место,
   где он считается. Выражение стоит там же, где сейчас старый блок: после сборки `tasks`
   и после `ready_list`. Удалить целиком: словарь `statuses`, множество `blocked_ids`,
   отдельный SQL-запрос по `deps` с литералом типов, `try/except` вокруг них и начальное
   `blocked_count = 0`. Новое выражение **не** оборачивать в `try/except`: ему нечем падать.
   Над выражением поставить комментарий в одну-две строки: правило то же, что у
   `/api/blocked`, считается по денормализованному `blocked_by`.
   - Зачем фильтр статуса. При `include_closed=true` в `tasks` попадают и закрытые карточки,
     а у отменённой может остаться непустой `blocked_by`. `/api/blocked` закрытые задачи не
     отдаёт. Без `include_closed` фильтр ничего не меняет.
   - Блок `ready_list` вместе со своим `try/except` не трогать. Ключи и форма ответа
     `board` остаются прежними.
2. **`LINK_TYPES`** (`web/src/lib/dictionaries.ts`). Добавить две записи той же формы
   `{ value, label, icon }`, по одной строке каждая:
   - `conditional-blocks`, подпись `блокирует условно`, иконка `lock` — сразу после записи
     `waits-for`;
   - `resource-blocks`, подпись `ресурсный блокер`, иконка `lock` — последней в массиве.
   Больше в файле ничего не менять.
3. **`docs/API.md`**, строка таблицы `GET /api/board`. Сейчас в ней перечень
   «`group_by, columns[], total, needs_you[], lint{count, items[]}, generated_at`», за ним
   пояснения. В перечень добавить `blocked_count`, а к пояснениям — одно предложение:
   `blocked_count` — число незакрытых карточек доски с непустым `blocked_by` (незакрытый
   жёсткий блокер), то же правило, что у `/api/blocked`; в серверном режиме считаются только
   карточки, которые пропустил фильтр `X-Listik-Owner`. Остальной текст строки не менять.
4. **Новый файл тестов `tests/test_board_blocked_count.py`** (`unittest`, база —
   `TempDbTestCase` из `tests.helpers`, как в `tests/test_board_lint.py`). В нём три класса.

   **`BoardBlockedCountTests`**
   - Основной сценарий (проекты `demo` и `other`):
     - `A1` (demo) ждёт открытую `B` (demo) связью `blocks`. Связь ставится через
       `store.add_dep(conn, A1, B, "blocks", created_by="me")`: человеческий актор пишет
       жёсткую связь сразу.
     - `A2` (demo) ждёт `C` (demo) через `blocks`, потом `C` закрыта:
       `store.update_task(conn, C, status="done")`.
     - `A3` (demo) ждёт `D` (other) через `blocks`, потом `D` закрыта так же.
     - `A4` (demo) ждёт открытую `E` (demo) только ребром `resource-blocks`. Через `add_dep`
       этот тип не ставится, поэтому нужен прямой `INSERT` в `deps` с `dep_type =
       'resource-blocks'`, затем `deps.refresh_task(conn, A4)` и `commit`. Образец —
       `_insert_resource_block` в `tests/test_resource_blocks.py`.
     - `A5` (demo) ждёт открытую `F` (other) через `blocks`.
     - `A6` (demo) — только предложенный блокер:
       `store.add_dep(conn, A6, B, "blocks", created_by="agent:codex")` пишет
       `suggested-blocks`, а такая связь не блокирует.

     Проверки, каждая отдельным утверждением:
     - `store.board(conn, project="demo")["blocked_count"] == 3`;
     - `len(deps.blocked_tasks(conn, project="demo")) == 3`;
     - `{t["id"] for t in deps.blocked_tasks(conn, project="demo")} == {A1, A4, A5}`.

     Числа и множество выведены из сценария, а не из кода, поэтому оба пути не могут
     ошибиться одинаково. До правки доска даёт `4` (A1, A2, A3, A5).
   - Сценарий `include_closed`:
     - `X` (demo) ждёт открытую `B` (demo) через `blocks`, потом `X` отменена:
       `store.update_task(conn, X, status="cancelled")`.
     - Предусловие проверить в самом тесте: после отмены
       `store.get_task(conn, X)["blocked_by"] == [B]`. Без этого тест ничего не проверяет.
     - Проверки: `store.board(conn, project="demo", include_closed=True)["blocked_count"]`
       равен `0`, и `len(deps.blocked_tasks(conn, project="demo")) == 0` — отдельными
       утверждениями. До правки доска даёт `1`.

   **`BoardBlockedCountOwnerTests(OwnerStoreCase)`** — серверный режим. Класс
   `OwnerStoreCase` импортируется из `tests.test_owner_store`, в `setUp` зовётся
   `self.server_mode()`. Это страховка от регрессии: тест проходит и до правки, и после.
   - Сценарий (проект `demo`): `B` (владелец `ann`) открыта; `Aa` (владелец `ann`) и
     `Ab` (владелец `bob`) ждут `B` через `blocks`. Задачи заводятся через
     `store.create_task(..., as_owner="ann"|"bob")`, связи — через
     `store.add_dep(..., created_by="me")`.
   - Проверки:
     - `store.board(conn, project="demo", as_owner="ann")["blocked_count"] == 1`;
     - то же с `as_owner="bob"` — тоже `1`;
     - `len(deps.blocked_tasks(conn, project="demo")) == 2`: `/api/blocked` фильтр владельца
       не применяет, это задуманное расхождение.

     Сценарий проверен в песочнице на текущем коде: `1`, `1`, `2`.

   **`LinkTypesDictionaryTests`** — читает `web/src/lib/dictionaries.ts` как текст, без сборки
   доски. Образец — `RouteIconDictionaryTests` в `tests/test_routes_config.py`.
   - Блок — текст от `export const LINK_TYPES` до первого `\n]` после него. Из блока
     извлекаются все тройки `value: '…', label: '…', icon: '…'`.
   - Проверки:
     - значения `value` не повторяются;
     - словарь `{value: label}` равен `deps.DEP_TITLES` (те же ключи и те же подписи);
     - каждая `icon` есть среди имён иконок доски — функция `board_icon_names` из
       `tests.test_routes_config`. Импортировать только функцию, не классы тестов.
   - До правки тест падает: в словаре нет двух ключей.

## Границы правки

- Меняются ровно четыре файла: `listik/store.py` (только блок `blocked_count` внутри
  `board`), `web/src/lib/dictionaries.ts` (две добавленные строки), `docs/API.md` (одна
  строка таблицы `GET /api/board`) и новый `tests/test_board_blocked_count.py`.
- `listik/deps.py` не трогать совсем: общий помощник незакрытых рёбер делает порция `b`.
  Не добавлять в `deps.py` функций «заодно» и не звать из `board` функции `deps` для
  подсчёта.
- Не менять `deps.blocked_tasks`, `deps.ready_tasks`, `/api/blocked`, `/api/ready`, фильтр
  `X-Listik-Owner`, форму ответа `board` и остальные поля доски (`ready`, `cycles`, `lint`,
  `needs_you`, колонки).
- Не трогать `web/scripts/mock-api.mjs`, компоненты доски и `icons.ts`. Новых иконок нет:
  `lock` уже существует.
- Существующие тесты не править. Реализацию под тест не подгонять: тест описывает
  поведение `/api/blocked`, а не текущий код доски.
- Никаких переименований, переформатирования и рефакторинга соседнего кода.

## Как проверить

```sh
cd {{TREE}}
python3 -m unittest tests.test_board_blocked_count tests.test_board_lint tests.test_board_stage_columns tests.test_assigned_not_taken tests.test_routes_config tests.test_owner_store
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -n "blocked_ids\|statuses = \|FROM deps"
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -c "try:"
sed -n '/^def board(/,/^def stats(/p' listik/store.py | grep -c "blocked_count ="
node --input-type=module -e "const m = await import('./web/src/lib/dictionaries.ts'); console.log(m.LINK_TYPES.length)"
git status --short
```

Что должны показать команды:
- `sed … | grep -n "blocked_ids…"` — пусто;
- `grep -c "try:"` — `2`: остаются только `try` у `lint` и у `ready_list`, до правки было `3`;
- `grep -c "blocked_count ="` — `1`: одно присваивание, то есть новое выражение;
- `node … import` — `14`. Это заодно проверка, что синтаксис `dictionaries.ts` не сломан:
  node разбирает файл целиком;
- `git status --short` — ровно четыре строки: ` M docs/API.md`, ` M listik/store.py`,
  ` M web/src/lib/dictionaries.ts`, `?? tests/test_board_blocked_count.py`. Каталог
  `docs/specs/` в git не виден, и так и должно быть.

Полный `discover` не запускать. Полную проверку типов доски (`cd web && npm run typecheck`)
запускать, только если в дереве уже есть `web/node_modules`; ставить их не нужно.

Что написать в отчёте исполнителя:
- вывод команд из «Предпосылок»;
- дословный вывод прогона `tests.test_board_blocked_count` **до правки кода**
  (`4 != 3`, `1 != 0`, отсутствующие ключи словаря; тест серверного режима — ok);
- вывод всех команд этого раздела после правки.
