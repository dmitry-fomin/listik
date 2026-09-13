# Приёмка порции 04.a. Зависимости: предложения, подтверждение, циклы, мягкие типы

Все сценарии — на временной базе (`LISTIK_DB=/tmp/…`, `./bin/listik --local init`), тесты —
`python3 -m unittest`. «Красный до правки» — пункт, который на коммите до порции падает; судья
при необходимости проверяет это, читая тест, а не переключая рабочее дерево.

## Тесты

1. Файл `tests/test_deps.py` существует, наследует `TempDbTestCase`,
   `python3 -m unittest tests.test_deps -v` зелёный; в нём есть тесты на все сценарии из
   раздела 7 ТЗ (предложение от агента на задаче без этапа и на `s1-spec`; предложение не
   блокирует; человек/`confirm`/пустой актор → жёсткая, префикс `agent:` → предложение; MCP без
   актора → предложение с `agent:mcp`; подтверждение заменяет предложение; повтор →
   `created=False`; самосвязь и циклы из 2 и 3 задач; `related`/`supersedes` мягкие; независимые
   порции в `ready`, зависимая после `done` блокера; `remove_dep` без типа и с типом;
   `deps.suggested` с фильтром и без закрытых; CLI `dep add`/`confirm`/`suggested`/`show`, цикл
   без трейсбека, висячая мягкая связь в `show` и `dep tree`). Отсутствие любого — красный пункт.
2. `python3 -m unittest discover tests` зелёный целиком; `git diff` по существующим тестам пуст.
3. CLI-тесты запускают `bin/listik` только с `--local` и `env["LISTIK_DB"]` временной базы;
   тест без этого — красный пункт (рискует рабочей базой).

## store / deps

4. `store.add_dep(conn, B, A, "blocks", "agent:codex")` на задаче `B` **без этапа**: в `deps`
   одна строка с `dep_type = 'suggested-blocks'`, ответ `suggested is True`,
   `dep_type == "suggested-blocks"`, `requested_dep_type == "blocks"`. **Красный до правки**
   (сегодня — жёсткая `blocks`).
5. После п. 4: `deps.ready(conn, B)["ready"]` истинно, `B` в `deps.ready_tasks(conn)`,
   `tasks.blocked_by == '[]'`.
6. `add_dep(conn, B, A, "blocks", "me")` после п. 4: в `deps` для пары ровно одна строка,
   `dep_type = 'blocks'`; ответ `promoted is True`, `confirmed is True`, `created is True`.
   **Красный до правки** (сегодня остаются две строки).
7. `add_dep(conn, B, A, "blocks", "agent:codex", confirm=True)` на чистой паре → `blocks`;
   `add_dep(conn, B, A, "blocks", None)` → `blocks`.
7a. `add_dep(conn, B, A, "blocks", "agent:mcp")` (незнакомое имя с префиксом `agent:`,
   `actors.resolve` даёт kind `human`) → `suggested is True`, строка `suggested-blocks` —
   правило префикса. **Красный до правки.**
7b. Диспетчер инструментов `mcp.py` вызван напрямую в тесте: `listik_deps` с `{"id": B,
   "depends_on": A}` **без** `actor` при отсутствующем `LISTIK_ACTOR` → `suggested is True`,
   `deps.created_by == "agent:mcp"`; при `LISTIK_ACTOR=agent:codex` в окружении →
   `created_by == "agent:codex"`; с явным `"actor": "me"` → `blocks`. **Красный до правки**
   (сегодня без `actor` пишется жёсткая `blocks`). Тест не должен зависеть от реального
   окружения судьи: `LISTIK_ACTOR` явно убирается/ставится через `mock.patch.dict(os.environ)`.
8. Повтор `add_dep(conn, B, A, "blocks", "me")` → `created is False`, строк одна.
9. `add_dep(conn, A, A, "blocks")` → `ValueError`; при существующей `B→A`
   `add_dep(conn, A, B, "blocks", "me")` → `ValueError`, текст содержит `цикл`, `A` и `B`, и
   путь вида `A → B → A`; в `deps` строка `A→B` не появилась; `deps.cycles(conn) == []`.
   Цикл из трёх (`C→B`, `B→A`, затем `A→C`) — тот же отказ.
10. `add_dep(conn, C, A, "related", "me", confirm=True)` и `"supersedes"`: `C` не в
    `deps.blocked_tasks`, `deps.ready(conn, C)["ready"]` истинно, в `soft_links` есть элементы
    с этими `dep_type` — регрессия шага 03 (типы из живой базы).
11. Две задачи без связей обе в `ready_tasks`; после `add_dep(B, A, "blocks", "me")` `B`
    исчезает; после `update_task(conn, A, status="done")` — появляется.
12. `store.remove_dep(conn, B, A)` (без типа) при двух строках (`blocks` + `suggested-blocks`,
    вставленных напрямую) → `{"removed": 2, "dep_types": [...]}` с обоими типами, строк нет.
    **Красный до правки** (сегодня снимается только `blocks`). С `dep_type="suggested-blocks"`
    → `removed == 1`, `blocks` остаётся. Несуществующая пара → `removed == 0`, без исключения.
13. `deps.suggested(conn)` после п. 4 — один элемент с ключами `issue_id, issue_title,
    issue_stage, project, depends_on, depends_on_title, depends_on_status, created_by,
    created_at`; после п. 6 — пуст; `project="другой"` → пуст; если зависимая задача закрыта
    (`status="done"`) — не показывается. **Красный до правки** (функции нет).

## CLI (локальный путь)

14. `./bin/listik --local dep add <B> <A> --actor agent:codex` → код 0, stdout содержит
    `предложение` и подсказку `dep confirm`. **Красный до правки.**
15. `./bin/listik --local dep add <A> <B> --actor me` при существующей `B→A` → код 1, stderr —
    одна строка `ошибка: … цикл …`, слова `Traceback` нет ни в stdout, ни в stderr.
    **Красный до правки** (сегодня трейсбек).
16. `./bin/listik --local claim <несуществующий-id> --holder x` → код 1, stderr
    `ошибка: задача не найдена: …` без кавычек `repr` и без трейсбека — та же обёртка `main()`
    работает для `KeyError`.
17. `./bin/listik --local dep confirm <B> <A> --actor me --json` → stdout парсится как JSON,
    `confirmed == true`, `dep_type == "blocks"`. **Красный до правки** (действия нет).
18. `./bin/listik --local dep suggested` печатает строку с `<B>`, `<A>` и `всего предложений: 1`;
    с `--json` — список; после подтверждения — `предложений нет`. **Красный до правки.**
19. `./bin/listik --local dep rm <B> <A>` без типа снимает и `blocks`, и `suggested-blocks`,
    печатает снятые типы; повтор печатает `связи не было`, код 0.
20. `./bin/listik --local show <B>` при предложении печатает `предложены блокеры (не
    подтверждены): <A>`; при `relates-to` — `связано (не блокирует): <A> (связана)`; при
    подтверждённой `blocks` — `заблокирована: <A>` и строки про предложение нет.
    **Красный до правки** (сегодня строк нет).
20a. Висячая мягкая связь: `add_dep(B, X, "relates-to", "me")`, затем `store.delete_task(conn,
    X)` (строка `deps` остаётся — так устроен `delete_task`); `./bin/listik --local show <B>`
    → код 0, stdout содержит `связано (не блокирует): <X> (relates-to, задача не найдена)`,
    без `Traceback` и без `ошибка: dep_title`; `./bin/listik --local dep tree <B>` → код 0,
    строка `связано (не блокирует): <X> (relates-to)`, без `Traceback`. **Красный до правки**
    для `dep tree` (сегодня `KeyError('dep_title')`); для `show` — красный, если реализовать
    п. 6 через `x["dep_title"]`.
21. `./bin/listik --local dep tree <B>` (живые связи), `dep suggest`, `dep link` работают как
    раньше (проверяется чтением кода `cmd_dep` для этих веток — в `cmd_dep_tree` изменена
    только подпись типа мягкой связи — и одним запуском).

## HTTP

22. При сервере на временной базе: `POST /api/tasks/{B}/deps` `{"depends_on": A, "actor":
    "agent:codex"}` → 200, `data.suggested == true`; с `"confirm": true` → `data.confirmed == true`,
    `data.promoted == true`; цикл → 400, текст с `цикл`.
23. `DELETE /api/tasks/{B}/deps/{A}` → 200, `data.removed` и `data.dep_types`;
    `?dep_type=suggested-blocks` снимает только предложение. **Красный до правки** (маршрута нет,
    сегодня 404). `./bin/listik dep rm <B> <A>` без `--local` при поднятом сервере — код 0.
24. `GET /api/deps/suggested?project=demo` → 200, `data.items[]` и `data.generated_at`;
    без `project` — все проекты. **Красный до правки.**
25. `GET /api/tasks/{B}` → `deps_state.soft_links` содержит предложение с
    `dep_type == "suggested-blocks"`, `dep_title == "предложенный блокер"`; форма `deps_state`
    (набор ключей) не изменилась относительно `API.md` «Зависимости: можно ли брать задачу».

## MCP

26. `tools/call listik_deps {"id": B, "depends_on": A, "actor": "agent:codex"}` →
    `suggested: true`; `tools/call listik_deps {"id": B, "depends_on": A}` **без `actor`** (MCP
    запущен без `LISTIK_ACTOR` в окружении) → `suggested: true`, в `deps` `created_by ==
    "agent:mcp"` — **красный до правки** (сегодня жёсткая `blocks`); с `LISTIK_ACTOR=agent:dsh`
    в окружении MCP-процесса → `created_by == "agent:dsh"`; `{"action": "rm", "id": B,
    "depends_on": A}` без `dep_type` снимает и предложение, и жёсткую (тот же
    `remove_dep(None)`); `tools/list` показывает у `listik_deps` прежнюю схему (`id, depends_on,
    dep_type, action, actor, confirm`, `actor` по-прежнему необязателен), в описании — фразы про
    предложение от агента и про то, что вызов без `actor` считается агентским. Новых
    инструментов в `tools/list` нет.

## Границы

27. `git diff --stat` не содержит `web/`, `listik/actors.py`, `listik/documents.py`,
    `listik/import_*.py`, `listik/db.py`, `listik/config.py`, `README.md`, `API.md`,
    `docs/harness-protocol.md`, `AGENTS.md`; в `listik/store.py` изменены только `add_dep` и
    `remove_dep`; в `listik/deps.py` константы `HARD_BLOCKERS`/`SOFT_LINKS`/`DEP_TITLES` не
    изменены; в `listik/mcp.py` diff ограничен веткой `listik_deps` в диспетчере и строкой
    описания (`inputSchema` без изменений); в `cmd_dep_tree` изменена одна строка.
28. `python3 -m unittest tests.test_context` зелёный — `dependencies.suggested[]` в `context`
    на `s1-spec` по-прежнему показывает строки `suggested-blocks` (проверить один вызов
    `documents.context(conn, B, "s1-spec")` после п. 4: в `dependencies.suggested` один элемент).
