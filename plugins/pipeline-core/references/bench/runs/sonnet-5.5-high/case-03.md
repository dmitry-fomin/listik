судья: sonnet-5.5-high
модель: sonnet (pipeline-core:pipeline-judge, model: sonnet в вызове, effort high из frontmatter)
начало: 2026-09-29T12:12:48Z
конец: 2026-09-29T12:14:08Z
секунд: 48
---
красный

1. listik/mcp.py:568 — умолчание `limit` у `listik_list` изменено с 50 на 200: `_int_arg(args, "limit", 200)` вместо `int(args.get("limit", 50))` на базе HEAD (listik/mcp.py:568). Это нарушает требование 3 порции (умолчания не меняются, у `listik_list` MCP-умолчание 50, а не 200 от `store.list_tasks`) и расходится со схемой `"limit": {"type": "integer", "default": 50}` (listik/mcp.py:92). Поведение вызова без ключа `limit` тоже изменилось. Проверено: на 60 задачах проекта `demo` `mcp.call_tool("listik_list", {"project": "demo"})` даёт `limit == 200` и `limit == 200` при `"limit": None`, ожидалось 50. Вернуть 50.
2. tests/test_mcp_null_args.py:41-47 (`test_list_null_limit`) — тест не проверяет `limit == 50`, он только сравнивает `null["limit"]` с `absent["limit"]`, то есть обе ветки могут быть одинаково неверны. Из-за этого дефект п.1 остался незамеченным при зелёном тесте. Пункт чек-листа и требование 5 говорят «в ответе `limit == 50`». Добавить явное утверждение.

Пункты чек-листа:
- зелёные: search, create, dep_tree, memory, timeline, deps_suggested, handle без isError; ready/blocked/mcp_tools без правок; нечисловое `"abc"` даёт `ValueError`; один хелпер `_int_arg`, `_limit` удалён; `grep -n "int(args" listik/mcp.py` пуст; докстринг про `0` не говорит; try/except вокруг `int()` нет; схемы TOOLS и нечисловые `args.get` не менялись; изменены только `listik/mcp.py` и `tests/test_mcp_null_args.py`.
- красные: `listik_list` (пп.1-2 выше).
- Умолчания остальных восьми мест по диффу: 10, 2, 3, 20, 100, 100, 30, 50 совпадают с таблицей.

Проверки:
- `python3 -m unittest tests.test_mcp_null_args tests.test_ready_limit tests.test_blocked_limit tests.test_mcp_tools` — Ran 33 tests, OK (зелёно при дефекте п.1).
- ручной сценарий с `LISTIK_HOME` во временном каталоге — вывод `absent 200`, `null 200 60`, `ValueError`.
- Полный набор не запускал: порция ограничена четырьмя модулями по ТЗ.
- Негативный контроль на базе HEAD не гонял: `mcp.py` не откатывал (правки в рабочем дереве, откат запрещён).
- Коммита нет.
Перенесено: ничего.
