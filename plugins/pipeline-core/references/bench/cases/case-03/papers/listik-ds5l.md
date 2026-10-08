Рабочее дерево: {{TREE}}

# listik-ds5l — MCP: `null` в числовых аргументах = аргумент не передан

## Цель

Семь веток `mcp.call_tool` делают `int(args.get(key, default))`. Если клиент прислал ключ
со значением `null`, получается `int(None)` → `TypeError`, и инструмент отвечает
`isError: true` вместо результата. Нужно, чтобы `null` в `limit`/`depth`/`priority` значил
то же, что отсутствие ключа: подставляется значение по умолчанию этого инструмента.
Шаг listik-r3ak уже сделал так для `listik_ready`/`listik_blocked` через
хелпер `_limit` в `listik/mcp.py`. Этот шаг распространяет то же правило на остальные места.

## Что уже есть (разведка, коммит HEAD)

`listik/mcp.py`:
- `_limit(args, default)` (строка 525): `None` → `default`, иначе `int(value)`. Его зовут
  только `listik_ready` (строка 616, умолчание 30) и `listik_blocked` (621, умолчание 50).
- Места с голым `int(args.get(...))`:
  - `listik_search`, строка 558: `limit`, умолчание 10;
  - `listik_list`, 568: `limit`, 50;
  - `listik_create`, 579: `priority`, 2;
  - `listik_dep_tree`, 630: `depth`, 3;
  - `listik_memory`, 668: `limit`, 20 (обе ветки, с `query` и без);
  - `listik_timeline`, 695: `limit`, 100;
  - `listik_deps_suggested`, 708: `limit`, 100.
- `int(cfg['server']['port'])` (строка 855) — не аргумент инструмента, шаг его не касается.
- `listik_context` передаёт `max_chars` без `int()`, а `documents.context` сам понимает
  `None` как «по умолчанию этапа». Правка не нужна.

## Порции

- `a` — `listik-ds5l.a.md`, чек-лист `listik-ds5l.check-a.md`. Один хелпер для
  всех девяти мест, новый тестовый модуль `tests/test_mcp_null_args.py`.

## Вне шага

- Нечисловые аргументы с `args.get(key, default)`: `null` там даёт `None`, а не умолчание
  (`type`, `order`, `mode`, `kind`, `dep_type`, `group_by`, `description`, `acceptance`,
  `value` у `listik_needs_owner`). Это отдельный класс ошибок, в эту карточку он не входит.
- Нечисловые строки (`"limit": "abc"`) по-прежнему дают ошибку инструмента.
- Смысл `0` у каждого инструмента не меняется.
- Документация не меняется: в `docs/API.md` нет раздела с семантикой `null` для MCP.
