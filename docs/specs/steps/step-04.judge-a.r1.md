# Приёмка шага 04, порция a, заход r1 — вердикт: зелёный

Чек-лист: `docs/specs/steps/step-04.check-a.md`; порция: `docs/specs/steps/step-04.a.md`;
дифф: `.git/feature-pipeline/step-04.diff-a.r1.txt` (совпадает с рабочим деревом: те же 6
изменённых файлов + новый `tests/test_deps.py`, те же числа строк).

Все проверки — на временных базах в `/private/tmp/.../scratchpad`, HTTP — на отдельном сервере
(`LISTIK_CONFIG` и `LISTIK_DB` в scratch, порт 8799, остановлен после проверки). Реальная
`listik.db` только копировалась для замера скорости `deps.suggested`, не изменялась.

## Тесты

1. ✅ `tests/test_deps.py` есть, наследует `TempDbTestCase`, `python3 -m unittest tests.test_deps -v`
   — 28 тестов, OK. Покрыты все сценарии раздела 7 ТЗ: предложение без этапа и на `s1-spec`;
   предложение не блокирует; человек / `confirm` / пустой актор → жёсткая; префикс `agent:` →
   предложение; MCP-диспетчер без актора (`agent:mcp`), с `LISTIK_ACTOR`, с явным `me`;
   подтверждение заменяет предложение; повтор → `created=False`; самосвязь, циклы из 2 и 3 задач;
   `related`/`supersedes` мягкие; `ready_tasks` до/после `done` блокера; `remove_dep` без типа и с
   типом; `deps.suggested` (фильтр проекта, закрытая задача); CLI `dep add`/`confirm`/`suggested`/
   `show`, цикл без трейсбека, висячая мягкая связь в `show` и `dep tree`.
2. ✅ `python3 -m unittest discover tests` — 130 тестов, OK. `git diff -- tests/` пуст (существующие
   тесты не правились).
3. ✅ CLI-тесты (`CliDepTests._run`) — `sys.executable`, абсолютный путь к `bin/listik`, `--local`,
   `env["LISTIK_DB"]` = временная база, `capture_output=True`.

## store / deps

4. ✅ `add_dep(B, A, "blocks", "agent:codex")` на задаче без этапа: одна строка `suggested-blocks`,
   ответ `suggested=True, dep_type="suggested-blocks", requested_dep_type="blocks", created=True`.
5. ✅ `deps.ready(B)["ready"]` истинно, `B` в `ready_tasks`, `tasks.blocked_by == "[]"`.
6. ✅ `add_dep(B, A, "blocks", "me")` после предложения: одна строка `blocks`;
   `promoted=True, confirmed=True, created=True`.
7. ✅ агент с `confirm=True` → `blocks`; `created_by=None` → `blocks`.
7a. ✅ `agent:mcp` (resolve даёт `human`) → `suggested=True`, строка `suggested-blocks`:
   правило префикса в `store.add_dep` (`listik/store.py:1040`).
7b. ✅ `mcp.call_tool("listik_deps", …)` без `actor` и без `LISTIK_ACTOR` → `suggested=True`,
   `created_by == "agent:mcp"`; с `LISTIK_ACTOR=agent:codex` → `agent:codex`; с `"actor": "me"` →
   `blocks`. Тест снимает/ставит `LISTIK_ACTOR` через `mock.patch.dict(os.environ)` и подменяет
   `paths.DB_PATH`.
8. ✅ повтор `add_dep(B, A, "blocks", "me")` → `created=False`, строка одна.
9. ✅ `add_dep(A, A, …)` → `ValueError: связь задачи с самой собой: …`; цикл из двух →
   `ValueError: жёсткая зависимость создаёт цикл: A → B → A`, строка `A→B` не появилась,
   `deps.cycles == []`; цикл из трёх → `… : A → C → B → A` (полный путь).
10. ✅ `related`/`supersedes` с `confirm=True`: задача не в `blocked_tasks`, `ready` истинно, оба
    типа в `deps_state.soft_links` — регрессия шага 03 держится.
11. ✅ две задачи в `ready_tasks`; после `blocks` зависимая исчезает; после `done` блокера —
    возвращается.
12. ✅ `remove_dep(B, A)` при `blocks` + `suggested-blocks` → `{"removed": 2, "dep_types":
    ["blocks", "suggested-blocks"]}`, строк нет; с `dep_type="suggested-blocks"` → `removed == 1`,
    `blocks` остаётся; несуществующая пара → `{"removed": 0, "dep_types": []}` без исключения.
13. ✅ `deps.suggested(conn)` — ровно 9 ключей из ТЗ; после подтверждения пуст; `project="другой"`
    пуст; закрытая зависимая задача не показывается.

## CLI (локальный путь)

14. ✅ `dep add B A --actor agent:codex` → rc 0, `предложение зависимости записано: … ; подтвердить:
    listik dep confirm B A`.
15. ✅ цикл → rc 1, stdout пуст, stderr — одна строка `ошибка: жёсткая зависимость создаёт цикл:
    …`, `Traceback` нет нигде (grep -c = 0 в обоих потоках).
16. ✅ `claim nosuch-id --holder x` → rc 1, `ошибка: задача не найдена: nosuch-id` (без кавычек
    repr, без трейсбека) — общая обёртка `main()` (`bin/listik:1266`).
17. ✅ `dep confirm B A --actor me --json` → валидный JSON, `confirmed: true`, `dep_type: "blocks"`,
    `promoted: true`.
18. ✅ `dep suggested` — строка с B, A и `всего предложений: 1`; `--json` — список; после
    подтверждения — `предложений нет`; `--project other` — `предложений нет`.
19. ✅ `dep rm B A` без типа снимает обе строки и печатает `связь снята: B → A (blocks,
    suggested-blocks)`; повтор — `связи не было`, rc 0.
20. ✅ `show B`: при предложении — `предложены блокеры (не подтверждены): A`; при `relates-to` —
    `связано (не блокирует): A (связана)`; при подтверждённой `blocks` — `заблокирована: A` и
    строки про предложение нет.
20a. ✅ висячая мягкая связь: `show B` → rc 0, `связано (не блокирует): X (relates-to, задача не
    найдена)`, без трейсбека; `dep tree B` → rc 0, без трейсбека. Отличие от буквы чек-листа:
    в `dep tree` печатается тот же расширенный ярлык `X (relates-to, задача не найдена)`, а не
    `X (relates-to)` — общий помощник `_soft_link_label` (`bin/listik:104`). Суть пункта (тип
    виден, `KeyError('dep_title')` нет) выполнена, ярлык — надмножество ожидаемой строки.
21. ✅ `dep tree` (живые связи), `dep suggest`, `dep link`, `cycles` работают: ветки в `cmd_dep`
    не тронуты (прочитано), в `cmd_dep_tree` изменена ровно одна строка; прогнаны по разу.

## HTTP (сервер на временной базе, порт 8799)

22. ✅ `POST /api/tasks/{B}/deps {"depends_on": A, "actor": "agent:codex"}` → 200,
    `data.suggested == true`; с `"actor": "me"` → `confirmed == true`, `promoted == true`;
    цикл → 400 `жёсткая зависимость создаёт цикл: …`.
23. ✅ `DELETE /api/tasks/{B}/deps/{A}?dep_type=suggested-blocks` → 200,
    `{"removed": 1, "dep_types": ["suggested-blocks"]}`; без параметра — `removed`/`dep_types` по
    паре; `./bin/listik --port 8799 dep rm B A` (без `--local`) → rc 0, 404 больше нет;
    `dep rm --dep-type blocks` через HTTP тоже rc 0.
24. ✅ `GET /api/deps/suggested?project=demo` → 200, `data.items[]` + `data.generated_at`; без
    `project` — все проекты; маршрут стоит до разбора `/api/tasks/...` (`listik/server.py:250`).
25. ✅ `GET /api/tasks/{B}` → `deps_state.soft_links` содержит `dep_type: "suggested-blocks"`,
    `dep_title: "предложенный блокер"`; набор ключей `deps_state` совпадает с прежним (функция
    `deps.ready` диффом не тронута) и покрывает таблицу API.md «Зависимости: можно ли брать задачу».

## MCP (реальный stdio-процесс)

26. ✅ `listik_deps` с `actor: agent:codex` → `suggested: true`; без `actor` и без `LISTIK_ACTOR` →
    `suggested: true`, `created_by == "agent:mcp"`; с `LISTIK_ACTOR=agent:dsh` → `created_by ==
    "agent:dsh"`; `{"action": "rm"}` без `dep_type` снял обе строки (`removed: 2`). `tools/list`:
    19 инструментов (новых нет), схема `listik_deps` прежняя (`id, depends_on, dep_type, action,
    actor, confirm`, `actor` необязателен), в описании — фразы про предложение от агента и про
    агентский вызов без `actor`.

## Границы

27. ✅ Дифф — только `bin/listik`, `listik/client.py`, `listik/deps.py`, `listik/mcp.py`,
    `listik/server.py`, `listik/store.py`, `tests/test_deps.py`. Нет `web/`, `actors.py`,
    `documents.py`, `import_*.py`, `db.py`, `config.py`, `README.md`, `API.md`,
    `docs/harness-protocol.md`, `AGENTS.md`. В `store.py` изменены только `add_dep`/`remove_dep`;
    в `deps.py` константы не тронуты, добавлена только `suggested`; в `mcp.py` — ветка
    `listik_deps`, строка описания и `import os` (необходим для новой ветки), `inputSchema` без
    изменений; в `cmd_dep_tree` — одна строка. Секретов (`.env`, ключи, токены) в диффе нет.
28. ✅ `tests.test_context` зелёный; `documents.context(conn, B, "s1-spec")` после предложения даёт
    `dependencies.suggested` = один элемент с `dep_type: "suggested-blocks"`.

## Наблюдения (не блокируют, в чек-листе и ТЗ порции их нет)

- `store.add_dep` (`listik/store.py:1069`): перед вставкой жёсткой связи проверяется наличие
  **любой** жёсткой строки для пары (`already_hard`), и при её наличии новая строка другого
  жёсткого типа не пишется. `add_dep(B, A, "waits-for", "me")` поверх существующей `blocks`
  возвращает `dep_type="blocks", created=False` («связь уже была»), строки `waits-for` в базе не
  появляется. ТЗ говорило про `INSERT … ON CONFLICT DO NOTHING` (конфликт — по тройке), так что
  формально это сужение; на готовность задач не влияет (оба типа жёсткие), контракт ответа
  (`dep_type` — «что реально записано или что уже лежало») выдержан.
- Пре-существующий дефект, не внесён порцией: `dep tree` для задачи с **висячей жёсткой** связью
  падает в `deps.graph`/`up` (`listik/deps.py:357`, `KeyError: 'holder_title'`; код диффом не
  тронут, тот же в `HEAD`). Благодаря новой обёртке `main()` теперь это одна строка
  `ошибка: holder_title` и rc 1 вместо трейсбека. Кандидат в следующую порцию/шаг.
- `deps.suggested` читает всю таблицу `tasks` (`_tasks_by_id` по всем id). На живой базе (3864
  задачи, 3637 связей) вызов занимает ~50 мс — приемлемо, но растёт линейно.

## Вердикт

Зелёный. Все 28 пунктов чек-листа выполнены, срезанных углов (хардкода под тест, подгонки тестов
под реализацию, заглушек) не найдено. Порция закоммичена.
