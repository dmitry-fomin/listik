Рабочее дерево: {{TREE}}

# listik-ds5l, порция a — `null` в `limit`/`depth`/`priority` MCP-инструментов = умолчание

Весь код пиши в этом дереве, а не в основном
дереве репозитория. Пути ниже даны от корня дерева. Бэкенд — чистый stdlib
Python без сборки. Тесты на `unittest`, запускаются из корня дерева. База — коммит HEAD.

## Контекст (баг)

MCP-клиенты присылают необязательный аргумент как `"limit": null`, а не пропускают ключ.
`mcp.call_tool` в семи ветках делает `int(args.get(key, default))`. Если ключ есть и равен
`None`, `get` возвращает `None`, а `int(None)` бросает `TypeError`. `mcp.handle` превращает
это в ответ `isError: true`, и инструмент не работает.

Шаг listik-r3ak починил так `listik_ready`/`listik_blocked`. В `listik/mcp.py` (строка 525)
появился хелпер `_limit(args, default)`: он читает `args.get("limit")` и при `None` отдаёт
`default`, иначе `int(value)`. В его докстринге сказано «`null` — как отсутствие ключа,
`0` — без ограничения». Зовут его только эти два инструмента (строки 616 и 621).

## Сверено с кодом (коммит HEAD, `listik/mcp.py`)

| Инструмент | Строка | Аргумент | Умолчание | Куда уходит |
| --- | --- | --- | --- | --- |
| `listik_search` | 558 | `limit` | 10 | `search.search(limit=)` |
| `listik_list` | 568 | `limit` | 50 | `store.list_tasks(limit=)`, в ответе есть поле `limit` |
| `listik_create` | 579 | `priority` | 2 | `store.create_task(priority=)` |
| `listik_dep_tree` | 630 | `depth` | 3 | `deps.graph(depth=)` |
| `listik_memory` | 668 | `limit` | 20 | `search.search_memories` (есть `query`) или SQL `LIMIT ?` (нет `query`) |
| `listik_timeline` | 695 | `limit` | 100 | `store.task_timeline(limit=)` |
| `listik_deps_suggested` | 708 | `limit` | 100 | `deps.suggested(limit=)` |
| `listik_ready` | 616 | `limit` | 30 | уже через `_limit` |
| `listik_blocked` | 621 | `limit` | 50 | уже через `_limit` |

Умолчания в таблице — те, что сейчас стоят в коде, и они совпадают с `"default"` в схемах
инструментов (`TOOLS`). Схемы не меняются.

## Требования

1. **Один хелпер на все девять мест.** Обобщи `_limit` до хелпера, который принимает ещё
   и имя ключа, например `(args, key, default) -> int`. Имя выбери сам. Поведение: значение
   `None` или отсутствие ключа дают `default`, любое другое значение — `int(value)`, как
   сейчас. Все девять веток из таблицы, включая `listik_ready`/`listik_blocked`, читают
   свой числовой аргумент только через этот хелпер. Старый `_limit` не оставляй рядом
   с новым: два хелпера для одного правила не нужны.
2. **Докстринг хелпера говорит только про `null`.** Сейчас в докстринге написано
   «`0` — без ограничения». Это верно для `ready`/`blocked`, но не для `list`, `memory`,
   `timeline` и прочих, где `0` уходит в SQL `LIMIT 0`. Не распространяй это утверждение
   на все инструменты. Докстринг описывает одно: `null` — то же, что ключ не передан.
3. **Умолчания не меняются.** `null` даёт то же число, что и отсутствие ключа, по таблице
   выше: 10, 50, 2, 3, 20, 100, 100, 30, 50. У `listik_list` это MCP-умолчание 50, а не
   умолчание `store.list_tasks` (200).
4. **Прочие значения ведут себя как раньше.** `0` передаётся как `0`, строка `"5"` — как
   `5`. Нечисловая строка по-прежнему даёт ошибку инструмента: глушить `ValueError` или
   подставлять вместо неё умолчание нельзя.
5. **Тесты — новый модуль `tests/test_mcp_null_args.py`** на `TempDbTestCase` из
   `tests/helpers.py`. Инструменты вызываются через `mcp.call_tool(name, args, conn=self.conn)`,
   так же как в `tests/test_blocked_limit.py`. Каждый тест ниже на базе HEAD падает
   с `TypeError`, после правки проходит.
   - `listik_search`: задача с уникальным словом в заголовке. Вызов с `"mode": "text"`,
     потому что режим `hybrid` ходит в Ollama. `hits` при `"limit": None` не пусты и равны
     `hits` без ключа `limit`. Сравнивай только `hits`: в ответе есть поля со временем.
   - `listik_list`: 3 задачи проекта `demo`. При `"limit": None` в ответе `limit == 50`,
     id задач совпадают с вызовом без ключа, их 3.
   - `listik_create`: `"priority": None` → в ответе и в `store.get_task` приоритет `2`.
   - `listik_dep_tree`: цепочка из пяти задач, E ждёт D, D ждёт C, C ждёт B, B ждёт A
     (`store.add_dep(conn, E, D, "blocks", created_by=...)` и так далее). У `dep_tree` для E
     при `"depth": None` цепочка id по `waits_for` → `up` ровно D → C → B, а `up` у B пуст:
     сработала глубина 3. То же без ключа `depth`. Сравнивай id, а не словари целиком:
     поле `idle_age` зависит от времени.
   - `listik_memory`: две заметки через `store.remember`. При `"limit": None` обе ветки,
     без `query` и с `query`, находящим заметку, дают непустой `items`, равный вызову без
     ключа `limit`.
   - `listik_timeline`: после создания пары задач `items` при `"limit": None` не пусты
     и равны вызову без ключа.
   - `listik_deps_suggested`: предложенная связь, как в
     `tests/test_mcp_tools.py::test_deps_suggested_lists_agent_proposal` (`listik_deps`
     с `"actor": "agent:codex"`). `items` при `"limit": None` не пусты и равны вызову без
     ключа.
   - Одна проверка через `mcp.handle` (`tools/call`, как это делают другие тесты
     `tests/test_mcp_tools.py`) для любого из семи инструментов с `null`. Ответ без
     `isError`.
6. **Существующие тесты не меняются.** `tests/test_ready_limit.py`,
   `tests/test_blocked_limit.py` и `tests/test_mcp_tools.py` проходят без правок.

## Границы правки

- Меняются только `listik/mcp.py` (хелпер и девять веток `call_tool`) и новый файл
  `tests/test_mcp_null_args.py`. Никаких других файлов.
- Не трогать `store.py`, `search.py`, `deps.py`, `documents.py`, `server.py`, `bin/listik`
  и сигнатуры/умолчания функций, которые зовёт `call_tool`. Правка чинит разбор аргументов
  MCP, а не нижний слой.
- Не менять схемы инструментов (`TOOLS`), их набор и описания.
- Не трогать нечисловые аргументы с `args.get(key, default)` (`type`, `order`, `mode`,
  `kind`, `dep_type`, `group_by`, `description`, `acceptance`, `value` у
  `listik_needs_owner`), даже если там та же проблема с `null`. Это вне порции.
- Не трогать `int(cfg['server']['port'])` (строка 855) и `max_chars` у `listik_context`:
  первое — не аргумент инструмента, второе уже понимает `None` ниже по стеку.
- Не менять смысл `0` и обработку нечисловых строк. Не ловить исключения вокруг `int()`.
- Не править существующие тесты под реализацию.
- Документацию (`docs/API.md`, `README.md`) не трогать.
- Полный `discover` не запускать: только модули из «Как проверить».

## Как проверить

Из корня дерева:

```
python3 -m unittest tests.test_mcp_null_args tests.test_ready_limit tests.test_blocked_limit tests.test_mcp_tools
```

Все четыре модуля зелёные. Новый модуль на базе HEAD (с откатом `listik/mcp.py`) краснеет
с `TypeError` в каждом тесте, кроме проверки через `handle`: там падает утверждение про
`isError`.
