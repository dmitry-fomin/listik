Рабочее дерево: {{TREE}}

# listik-5qay.check-a — приёмка порции a

Все CLI-пункты проверяются на обоих путях: через живой сервер и с `--local`.

## memory

1. 3 заметки, `listik memory -n 2` → последняя строка `показано 2 из 3`; до правки было `всего: 2`.
2. 3 заметки, `listik memory -n 5` → последняя строка `всего: 3`.
3. 3 заметки, `listik memory -n 0` → выведены все 3 заметки, строки «памяти пока нет» нет,
   итог `всего: 3`; до правки с `--local` печаталось «памяти пока нет».
4. 25 заметок, `listik memory -n 0 --json` → список из 20 элементов на обоих путях.
5. `listik memory -n 2 --json` → JSON-список из 2 заметок, форма как до правки (не объект, без `total`).
6. `GET /api/memory` по-прежнему отдаёт голый список (форма не изменилась).
7. Пустая память → «памяти пока нет»; с `--project` без заметок в проекте — тоже.
8. При лежащем сервере (без `--local`) предупреждение «сервер Listik не отвечает» в stderr
   выводится не больше одного раза, даже если страница заполнена и делается второй запрос.
9. `listik memory "запрос" -n 0 --local` и MCP `listik_memory` `{"query": …, "limit": 0}` не
   возвращают пустой результат там, где есть совпадения (до 20).
10. MCP `listik_memory` `{"limit": 0}` → непустой список (до 20); до правки — пустой.

## dep suggested

11. 3 предложения, `listik dep suggested -n 2` → последняя строка `показано 2 из 3 предложений`.
12. 3 предложения, `listik dep suggested -n 5` → `всего предложений: 3`.
13. `GET /api/deps/suggested?limit=2` → `total == 3`, в `items` 2 элемента, `generated_at` на месте;
    `client.local_call("dep_suggested", limit=2)` → то же.
14. `limit=0`: сервер, `--local` и MCP `listik_deps_suggested` отдают одинаковое число элементов
    (не больше 100); до правки local/MCP отдавали без ограничения.
15. MCP `listik_deps_suggested` `{"limit": 1}` → 1 элемент, `total` — полное число.
16. `listik dep suggested --json` → тот же JSON-список `items`, что до правки.
17. Нет предложений → «предложений нет».
18. `store.lint` по-прежнему учитывает все предложения: тесты `suggested_dep_stale` в
    `tests/test_lint.py` и `tests/test_board_lint.py` проходят без правки; `deps.suggested(limit=0)`
    возвращает все (проверка чтением неизменённого поведения + существующие тесты).

## MCP listik_list

19. `listik_list` `{"limit": 0}` → задачи не пусты, их не больше 200, `total` — полное число;
    до правки `tasks` был пуст.
20. `listik_list` без `limit` → не больше 50 задач (дефолт прежний).
21. В `inputSchema` у `listik_list`, `listik_memory`, `listik_deps_suggested` у `limit` есть
    `description`, где сказано, что значит 0 (200 / 20 / 100).

## Документация и границы

22. `docs/API.md`: у `GET /api/deps/suggested` в ответе указан `total`, сказано про `limit=0` → 100;
    в разделе CLI есть строка про `listik memory -n` и итог/`-n 0` у `dep suggested`.
23. Дефолты `-n` у `memory` (20) и `dep` (50), дефолты MCP не изменились.
24. Существующие тесты не изменены под реализацию (`git diff` по старым тест-методам пуст,
    кроме добавленных); `web/`, `alembic/`, `list`/`ready`/`blocked` не тронуты.
25. Новые тесты на пункты 1, 3, 10, 13, 19 падают на коде до правки (`git stash` не нужен —
    судья может проверить, откатив изменения исходников в копии или по логике теста).

## Проверки порции

```sh
cd {{TREE}}
python3 -m unittest tests.test_cli_page_totals tests.test_memory_list tests.test_deps tests.test_mcp_tools tests.test_lint tests.test_board_lint -v
```
Плюс новый файл тестов, если исполнитель завёл его отдельно (`python3 -m unittest tests.<имя> -v`).
