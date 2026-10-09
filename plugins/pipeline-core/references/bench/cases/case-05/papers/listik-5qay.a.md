Рабочее дерево: {{TREE}}
Карточка: listik-5qay, порция a

# listik-5qay.a — memory, dep suggested, MCP listik_list: итог по полному числу и limit=0 как у сервера

Весь код и тесты — только в рабочем дереве выше. Запускать CLI как `./bin/listik` из этого
дерева (не установленный `listik`), тесты — `python3 -m ... -t .` из корня дерева.

## Образец, на который равняться

В шаге listik-n1aw то же сделано для `list`/`ready`/`blocked` — этот код уже есть в дереве,
посмотри его:

- `client.local_call("list")` подставляет `limit or 200`, как сервер (`as_int(q1("limit"), 200) or 200`):
  0 — страница по умолчанию, а не `LIMIT 0`;
- `cmd_ready`/`cmd_blocked`: если страница заполнена (`0 < limit <= len(tasks)`), второй
  `call(..., fallback_warning=False)` узнаёт полное число; итог — `показано M из N …`, если
  N > M, иначе прежнее `всего …: M`;
- тесты — `tests/test_cli_page_totals.py` (класс `LocalBypassWarningCase`: живой сервер на
  случайном порту + `run_cli(..., local=True|False)`), каждое поведение проверено на обоих путях.

## Требования

### 1. `listik memory` без запроса (список)

Сейчас: `bin/listik` `cmd_memory` печатает `всего: {len(rows)}`; сервер `/api/memory` берёт
`limit or 20`, а `client.local_call("memory")` и MCP `listik_memory` передают 0 в
`store.list_memories` → `LIMIT 0` → пусто.

1.1. `limit=0` на всех путях (HTTP — уже так, `--local`, MCP `listik_memory`) — страница по
умолчанию 20 заметок. Это же — для режима с запросом (`listik memory "текст" -n 0 --local`,
MCP `listik_memory` с `query` и `limit: 0`): 20, как у сервера, а не пусто.
Рекомендуемое место — одна правка в общих функциях (`store.list_memories` и
`search.search_memories`: `limit` 0/None → 20), тогда local и MCP исправятся разом; других
вызывающих с `limit=0` у этих функций нет (проверено `grep`, `search.py` зовёт
`search_memories` с `max(3, …)`). Правка на местах вызова (client + mcp) тоже допустима.
Поведение при `limit > 0` не меняется.

1.2. Текстовый итог списка (без `--json`): если заметок больше, чем показано, —
`показано M из N`; иначе прежнее `всего: M`. `GET /api/memory` отдаёт голый список без
`total`, форму ответа **не менять**; полное число узнавать вторым вызовом того же `call`
с `fallback_warning=False` и лимитом, заведомо покрывающим всё (например, константа `10**9`:
сервер пропускает любое положительное число, SQLite тоже), и только когда страница
заполнена (`len(rows) == эффективный лимит`). Предупреждение о недоступном сервере
печатается не больше одного раза за команду.

1.3. `listik memory -n 0` (и `--local`, и через сервер) при непустой памяти печатает
заметки, а не «памяти пока нет». «Памяти пока нет» — только когда заметок действительно
нет (с учётом `--project`).

1.4. `--json` у `listik memory` — вывод не меняется (тот же список, что сейчас).

1.5. Итог в режиме поиска (`listik memory "запрос"`) — не трогаем, там печатает
`search_mod.print_memories`.

### 2. `listik dep suggested`

Сейчас: сервер `/api/deps/suggested` — `limit or 100`; `client.local_call("dep_suggested")`
и MCP `listik_deps_suggested` передают 0 в `deps.suggested`, где 0 = без ограничения.

2.1. `limit=0` на всех путях (HTTP, `--local`, MCP `listik_deps_suggested`) — страница по
умолчанию 100. **Саму `deps.suggested` не менять**: `store.lint` вызывает её с `limit=0`
именно как «все», это поведение должно остаться. Правка — на местах вызова (client, mcp).

2.2. В ответ `dep_suggested` добавить поле `total` — число всех предложений, прошедших
фильтры (`project`, живые открытые задачи), без учёта `limit`. Одинаково в
`GET /api/deps/suggested`, `client.local_call("dep_suggested")` и MCP
`listik_deps_suggested`. Остальные поля (`items`, у HTTP и local — `generated_at`) не
меняются. Как посчитать — на усмотрение (например, `deps.suggested(limit=0)` и срез
`[:limit]`); не дублировать один и тот же расчёт в трёх местах, если это легко вынести
в одну функцию в `listik/deps.py`.

2.3. Текстовый итог `listik dep suggested`: `показано M из N предложений`, если N > M, иначе
прежнее `всего предложений: M`. N — из `total` ответа (второго вызова не нужно).

2.4. `listik dep suggested --json` — вывод не меняется (тот же список `items`, как сейчас).
«предложений нет» — только когда `total` = 0.

### 3. MCP `listik_list`

3.1. `listik_list` с `limit: 0` возвращает страницу по умолчанию 200 задач (как
`GET /api/tasks` с `limit=0`), `total` — полное число, как и сейчас. Без `limit` — прежние 50.
Место правки — `listik/mcp.py` (`_int_arg(args, "limit", 50) or 200`) или
`store.list_tasks` (`limit` 0 → 200) — на усмотрение; при правке в store убедись, что
`limit` в ответе `list_tasks` показывает реально применённое значение.

3.2. В `inputSchema.properties.limit` у `listik_list`, `listik_memory`,
`listik_deps_suggested` добавить `description`: что значит 0 (страница по умолчанию:
200 / 20 / 100 соответственно).

### 4. Документация

4.1. `docs/API.md`, строка таблицы `GET /api/deps/suggested` (~стр. 1435): в ответе
появляется `total` (число всех предложений без учёта `limit`); `limit=0` — 100.

4.2. `docs/API.md`, раздел команд CLI (рядом со строкой `listik dep suggested [--project]`,
~стр. 2094 и строкой `listik list [-n N] [--offset K]`): одна строка про `listik memory [-n N]`
и дополнение к `dep suggested`: итог «всего …»/«показано M из N …» по полному числу; `-n 0` —
страница по умолчанию (20 / 100) и через сервер, и с `--local`.

### 5. Тесты

Новый класс(ы) в `tests/test_cli_page_totals.py` (или новый файл `tests/test_page_totals_memory_deps.py`
на той же базе `LocalBypassWarningCase`), каждое CLI-поведение — на обоих путях
(`local=True` и `local=False`, как в образце):

- memory: 3 заметки, `-n 2` → последняя строка `показано 2 из 3`; `-n 5` → `всего: 3`;
  `-n 0` → заметки выведены, нет «памяти пока нет», итог `всего: 3`; 25 заметок, `-n 0 --json`
  → 20 элементов на обоих путях; `--json` при `-n 2` — список из 2 элементов (форма прежняя);
- dep suggested: 3 предложения (`store.add_dep(..., "blocks", "agent", confirm=False)` даёт
  `suggested-blocks` — см. `tests/test_deps.py`), `-n 2` → `показано 2 из 3 предложений`;
  `-n 5` → `всего предложений: 3`; `-n 0 --json` на обоих путях — одинаковое число элементов;
  HTTP `GET /api/deps/suggested?limit=2` и `client.local_call("dep_suggested", limit=2)` —
  `total == 3`, `len(items) == 2`;
- MCP (`mcp.call_tool(..., conn=self.conn)`): `listik_list` `{"limit": 0}` — задачи не пусты
  и `total` верен; `listik_memory` `{"limit": 0}` — не пусто; `listik_deps_suggested`
  `{"limit": 0}` — `len(items) == total`, `{"limit": 1}` — 1 элемент и `total` полный;
- `store.lint` по-прежнему видит все предложения (существующие тесты lint `suggested_dep_stale`
  проходят без правки).

Тесты, которые до правки должны падать: memory `-n 0 --local`, итоги `показано M из N`,
`total` у dep suggested, MCP `listik_list`/`listik_memory` с `limit: 0`.

## Границы правки

- Меняются только: `bin/listik` (`cmd_memory`, ветка `suggested` в `cmd_dep`), `listik/client.py`
  (ветки `memory`, `dep_suggested`, при необходимости), `listik/server.py` (только ответ
  `/api/deps/suggested` — добавить `total`), `listik/mcp.py` (`listik_list`, `listik_memory`,
  `listik_deps_suggested` и их схемы), `listik/store.py` (только `list_memories`, при выборе
  этого места — `list_tasks` в части `limit` 0), `listik/search.py` (только `search_memories`
  в части `limit` 0), `listik/deps.py` (только добавление подсчёта `total`, семантику
  `suggested(limit=0)` = «все» не менять), `docs/API.md`, тесты.
- Не менять форму ответа `GET /api/memory` и JSON-вывод `listik memory --json` /
  `listik dep suggested --json`.
- Не менять дефолты `-n` у CLI (`memory` 20, `dep` 50) и у MCP (`listik_list` 50,
  `listik_memory` 20, `listik_deps_suggested` 100).
- Не трогать `list`/`ready`/`blocked` (сделано в listik-n1aw), `web/`, `alembic/`, схему БД.
- Существующие тесты не править под реализацию; если какой-то падает — это ошибка в правке.
- Ничего «заодно»: другие команды с похожим итогом — не в этой порции (найдёшь — запиши
  в отчёте, карточку не заводи).
- Полный `python3 -m unittest discover` не гонять — только команды из «Как проверить».

## Как проверить

```sh
python3 -m unittest tests.test_cli_page_totals tests.test_memory_list tests.test_deps tests.test_mcp_tools tests.test_lint tests.test_board_lint -v
```
(плюс новый файл тестов, если заведён отдельно.)

Руками (из корня дерева, `--local`, временная база через `LISTIK_HOME`/`LISTIK_DB` —
не реальная база Listik): `./bin/listik --local memory -n 0`, `./bin/listik --local dep suggested -n 1`.
