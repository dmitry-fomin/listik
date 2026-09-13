# Порция 04.a. Зависимости: предложения агента, подтверждение человеком, циклы, мягкие типы

## Контекст

Listik — трекер задач на stdlib-Python: `bin/listik` (argparse CLI + API-клиент, все команды идут
через `call()` — HTTP при живом сервере, иначе `client.local_call()` прямо в SQLite),
`listik/store.py` (логика задач), `listik/deps.py` (граф связей: что блокирует, что можно брать),
`listik/server.py` (HTTP на `ThreadingHTTPServer`), `listik/client.py` (`local_call`),
`listik/mcp.py` (stdio MCP). Все транспорты зовут одни и те же функции store/deps — иначе
поведение расходится. Тесты — `tests/`, stdlib `unittest`, база на каждый тест временная
(`tests/helpers.py`, `TempDbTestCase`); образец CLI-теста — `tests/test_needs_owner.py`.

Модель связей: строка `deps(issue_id, depends_on, dep_type, created_at, created_by)`, PK по
трём полям — «`issue_id` зависит от `depends_on`». Жёсткие типы `deps.HARD_BLOCKERS`
(`blocks`, `blocked-by`, `waits-for`, `conditional-blocks`) гейтят `claim` и `ready`; мягкие
`deps.SOFT_LINKS` (`parent-child`, `relates-to`, `related`, `discovered-from`, `duplicates`,
`supersedes`, `parent`, `replies-to`, `suggested-blocks`) — только сигнал. «Заблокирована» —
вычисляемое состояние (`deps.blockers`, `deps.ready`, денормализованная колонка
`tasks.blocked_by` через `deps.refresh_task`/`refresh_blocked_column`), не статус.

Требование продукта (спека шага 04, п. 4–5; `docs/specs/self-critique.md` «Зависимости нельзя
полностью доверять модели»): агент на s1 может записать *предложение* зависимости; жёсткая
`blocks` появляется только после подтверждения человека или явной команды; при добавлении
жёсткой связи проверяется цикл.

Что есть сейчас (`store.add_dep`, проверено на временной базе 12.09.2026):

- агент без `confirm` получает `suggested-blocks` **только если зависимая задача на `s1-spec`**;
  порция без этапа (`stage` NULL) от агента `agent:codex` получила жёсткую `blocks` без
  подтверждения — нарушение п. 4;
- подтверждение человеком (`dep add B A --actor me`) добавляет `blocks`, но строка
  `suggested-blocks` остаётся — в `deps` две строки на пару;
- цикл ловится (BFS от `depends_on` по жёстким рёбрам), но текст английский
  (`hard dependency creates cycle: A → B`) и без пути;
- `remove_dep` удаляет ровно один `dep_type` (по умолчанию `blocks`) — отклонить предложение
  можно только зная имя типа;
- ошибки локального пути CLI (`ValueError`/`KeyError` из store) печатаются трейсбеком, HTTP-путь
  печатает `ошибка 400: …` (`client.request`);
- `show` (текст) не печатает ни предложения, ни мягкие связи — их видно только в `show --json`
  → `deps_state.soft_links` и в `dep tree`;
- сервер не имеет маршрута `DELETE /api/tasks/{id}/deps/{dep}` — `dep rm` при поднятом сервере
  получает 404;
- связи `related` и `supersedes` (импорт WriterLLM, шаг 03) — мягкие, `ready` их не учитывает;
  это верно, но не покрыто тестом.

Перед началом прочитай: `listik/deps.py` целиком (короткий); в `listik/store.py` — `add_dep`,
`remove_dep`, `recompute_blocked`, `get_task` (где кладётся `deps_state`), `update_task`;
в `bin/listik` — `main`, `call`, `emit`, `cmd_dep`, `cmd_dep_tree`, `show_task`, парсер `dep`;
в `listik/client.py` — `local_call` (операции `dep_add`, `dep_remove`, `dep_tree`, `mentions`);
в `listik/server.py` — обработчик `POST /api/tasks/{id}/<action>` (ветки `deps`), маршруты
`/api/ready`, `/api/blocked` как образец «список + `generated_at`»; в `listik/mcp.py` —
`listik_deps`; в `listik/actors.py` — `resolve` (kind `human|agent|unknown`).

## Что сделать

### 1. `store.add_dep`: предложение от агента, подтверждение заменяет предложение

Сигнатура не меняется:
`add_dep(conn, issue_id, depends_on, dep_type="blocks", created_by=None, confirm=False) -> dict`.
Поведение, в этом порядке:

1. Любой из двух задач нет → `KeyError("задача не найдена: <id>")`.
2. `issue_id == depends_on` → `ValueError("связь задачи с самой собой: <id>")` — для любого типа.
3. `actor_key, actor_kind = actors_mod.resolve(created_by, conn)`; при непустом `created_by`
   — `actors_mod.remember(...)`, как в `create_task`.
4. Агентом считается вызов, у которого `actor_kind == "agent"` **или** сырая строка
   `created_by` после `strip().lower()` начинается с `agent:` (`actors.resolve` для незнакомого
   имени вроде `agent:mcp` возвращает kind `human` — префикс надёжнее). Если `dep_type in
   HARD_BLOCKERS`, `confirm` ложен и вызов агентский → записывается **`suggested-blocks`**,
   независимо от этапа задач. `human` и `unknown` (пустой актор — человек у терминала) пишут
   жёсткую связь сразу; `confirm=True` пишет жёсткую связь при любом акторе.
5. Жёсткая связь (после п. 4): проверка цикла по рёбрам `HARD_BLOCKERS` — есть ли путь от
   `depends_on` до `issue_id`. Если есть → `ValueError` с текстом
   `жёсткая зависимость создаёт цикл: <issue_id> → <depends_on> → … → <issue_id>` (полный путь
   найденного цикла, ID через ` → `). Ничего не пишется. Существующий BFS можно оставить,
   дописав восстановление пути (родительские ссылки).
6. Жёсткая связь пишется `INSERT … ON CONFLICT DO NOTHING`; после вставки удаляется строка
   `suggested-blocks` для той же пары (если была) — подтверждение **заменяет** предложение.
7. Предложение (`suggested-blocks`): если для пары уже есть строка любого жёсткого типа —
   ничего не пишется (жёсткая связь важнее предложения). Иначе `INSERT … ON CONFLICT DO NOTHING`.
   Цикл для предложения не проверяется (оно на готовность не влияет).
8. `deps.refresh_task` для обеих задач, `commit`.
9. Ответ — словарь:
   `issue_id, depends_on` — как переданы; `dep_type` — что реально записано (или что уже лежало);
   `requested_dep_type` — что просили; `suggested` — записано/осталось предложением;
   `confirmed` — после вызова для пары есть жёсткая строка; `promoted` — этим вызовом
   предложение заменено жёсткой связью; `created` — этим вызовом появилась новая строка
   (ложь, если строка уже была); `created_by` — `actor_key or created_by`.

Мягкие типы (`relates-to`, `parent-child`, `related` …) пишутся как раньше, без проверок п. 4–5.

### 2. `store.remove_dep(conn, issue_id, depends_on, dep_type=None)`

`dep_type=None` → удалить строки типов `blocks` **и** `suggested-blocks` для пары (так
человек отклоняет предложение той же командой, что снимает связь). Явный тип — только его.
Ответ: `{"removed": <число удалённых строк>, "dep_types": [<удалённые типы>]}`; `removed` = 0 и
пустой список — не ошибка. `refresh_task` для обеих задач, `commit`.

### 3. `deps.suggested(conn, *, project=None, limit=100) -> list[dict]`

Ожидающие подтверждения предложения: строки `dep_type='suggested-blocks'`, у которых
зависимая задача (`issue_id`) не архивна и её статус в `OPEN_STATUSES`. Фильтр по проекту —
по `tasks.project` зависимой задачи. Порядок — `created_at` предложения, старые первыми.
Элемент: `issue_id, issue_title, issue_stage, project, depends_on, depends_on_title,
depends_on_status, created_by, created_at`. Задача-цель может не существовать (импорт) —
тогда `depends_on_title = "(задача не найдена)"`, `depends_on_status = "missing"`.

### 4. Транспорты

- **HTTP** (`listik/server.py`):
  - `POST /api/tasks/{id}/deps` с `depends_on` — как сейчас (`dep_type`, `confirm`, `actor`);
    ответ — словарь п. 1.9. `ValueError` → 400 с текстом (уже так через общий `except`).
  - **новый** `DELETE /api/tasks/{id}/deps/{depends_on}` (пять сегментов пути), необязательный
    параметр строки запроса `dep_type`; ответ — словарь п. 2. Задача не обязана существовать
    (удаление по паре ID); `publish("task", {"id": tid, "action": "deps"})`, как у POST.
  - **новый** `GET /api/deps/suggested?project=&limit=` → `{"items": [...], "generated_at": …}`.
    Маршрут ставится **до** общего разбора `/api/tasks/...`, рядом с `/api/ready`/`/api/blocked`.
- **Локальный путь** (`listik/client.py` `local_call`): `dep_remove` передаёт `dep_type` из
  kwargs как есть (в т. ч. `None`); новая операция `dep_suggested(project, limit)`.
- **CLI** (`bin/listik`, парсер `dep`):
  - `dep add <id> <dep> [--dep-type T] [--confirm]` — как сейчас; человекочитаемый вывод
    различает результат: `… (blocks)` для жёсткой, `предложение зависимости записано: <id> ждёт
    <dep>; подтвердить: listik dep confirm <id> <dep>` для `suggested=True`, `… (подтверждена,
    предложение заменено)` при `promoted=True`, `связь уже была` при `created=False`.
  - **новое действие** `dep confirm <id> <dep>` — то же, что `dep add <id> <dep> --dep-type
    blocks --confirm` (через тот же `call("dep_add", …)`).
  - **новое действие** `dep suggested [--project SLUG] [-n N]` — таблица предложений:
    `<issue_id>  ждёт  <depends_on> [<status>]  предложил <created_by> <created_at>` и итог
    `всего предложений: N`, при пустом списке — `предложений нет`; `--json` — список п. 3.
    Флаг `--project` добавить в парсер `dep`. Идёт через `call("dep_suggested", …,
    "/api/deps/suggested", query=…)`.
  - `dep rm <id> <dep> [--dep-type T]` — без `--dep-type` шлёт `dep_type=None` (в HTTP-путь —
    без параметра); человекочитаемый вывод перечисляет снятые типы или `связи не было`.
    Сегодня `cmd_dep` подставляет `blocks` по умолчанию для всех действий — для `rm` не
    подставлять.
  - `dep suggest`/`dep link`/`dep tree`/`dep cycles` не меняются.
- **MCP** (`listik/mcp.py`): `listik_deps` остаётся; `action: "rm"` передаёт `dep_type` из
  аргументов или `None`. MCP — транспорт, которым ходят только агенты, поэтому вызов без
  `actor` не должен превращаться в «человека»: в ветке `listik_deps` актор берётся как
  `args.get("actor") or os.environ.get("LISTIK_ACTOR") or "agent:mcp"` и передаётся в
  `add_dep` (`agent:mcp` попадает под правило префикса из п. 1.4 → предложение). Схема
  `inputSchema` не меняется (`actor` остаётся необязательным); описание инструмента
  дополняется фразой: жёсткая связь от агента без `confirm` записывается как предложение,
  без `actor` вызов считается агентским. Нового инструмента не добавлять; другие ветки
  `call_tool` не трогать.

### 5. CLI: ошибки store без трейсбека

В `bin/listik` `main()` вызов `args.func(args)` оборачивается: `ValueError` и `KeyError`
(что поднимает store/deps на локальном пути) → в stderr одна строка `ошибка: <текст>` (для
`KeyError` — `exc.args[0]`, без кавычек `repr`), код возврата 1; `SystemExit` и остальные
исключения не перехватывать. Это делает локальный путь симметричным HTTP-пути
(`client.request` уже печатает `ошибка 400: <текст>` через `SystemExit`). При `--json` —
то же, JSON ошибки не печатается.

### 6. `show` печатает предложения и мягкие связи

В `show_task` (`bin/listik`) после строки `заблокирована: …` — если в `t["deps_state"]`
(`get_task` кладёт его при `with_details`) в `soft_links` есть элементы с
`dep_type == "suggested-blocks"`: строка `  предложены блокеры (не подтверждены): A, B`;
остальные мягкие связи: `  связано (не блокирует): A (родитель), C (связана)`. Подпись типа —
`x.get("dep_title") or x["dep_type"]`, **не** `x["dep_title"]`: для связи с несуществующей
задачей `deps._info` возвращает укороченный словарь (`id, dep_type, missing, title, status`)
без `dep_title`, а висячие связи в базе реальны (`store.delete_task` строки `deps` не чистит,
`import_beads` не проверяет цель; в живой базе их 16). Для такой связи печатать
`<id> (<dep_type>, задача не найдена)`. Без связей строк нет. `deps_state` может быть `None` —
не падать. Ту же ошибку содержит `cmd_dep_tree` (`bin/listik`, строка `связано (не
блокирует): …` с `x['dep_title']`) — заменить на тот же fallback; больше в `cmd_dep_tree`
ничего не менять.

### 7. Тесты: `tests/test_deps.py`

Класс(ы) на `TempDbTestCase`; задачи создаются через `store.create_task`, статус блокера
закрывается через `store.update_task(conn, id, status="done")`. Обязательные тесты (имена —
на усмотрение, суть — как ниже):

1. агент (`created_by="agent:codex"`) + `blocks` на задаче **без этапа** → в `deps` одна строка
   `suggested-blocks`, ответ `suggested=True`, `dep_type="suggested-blocks"`,
   `requested_dep_type="blocks"`; то же для задачи на `s1-spec`;
2. предложение не блокирует: `deps.ready(conn, B)["ready"]` истинно, `B` есть в
   `deps.ready_tasks(conn)`, `tasks.blocked_by == "[]"`;
3. человек (`created_by="me"`) → сразу `blocks`; агент с `confirm=True` → `blocks`; пустой
   `created_by` → `blocks`; незнакомое имя с префиксом `agent:` (`created_by="agent:mcp"`,
   `resolve` даёт kind `human`) → предложение — правило префикса;
3a. MCP без актора: `mcp.call_tool("listik_deps", {"id": B, "depends_on": A})` (диспетчер
   инструментов в `mcp.py`; вызывать напрямую, без stdio; соединение он берёт через `_conn()`
   → `db.init()` по `paths.DB_PATH`, поэтому в тесте подменить `listik.paths.DB_PATH` на
   временную базу через `mock.patch.object`, иначе тест пойдёт в реальную `listik.db`) при
   `LISTIK_ACTOR`, убранном из окружения (`mock.patch.dict(os.environ, …, clear=…)` или
   `os.environ.pop`), → `suggested is True`, в `deps` строка `suggested-blocks` с
   `created_by == "agent:mcp"`; при `LISTIK_ACTOR=agent:codex` → `created_by == "agent:codex"`;
   с явным `actor: "me"` → `blocks`;
4. подтверждение после предложения: `add_dep(B, A, "blocks", "me")` → в `deps` для пары ровно
   одна строка, тип `blocks`; ответ `promoted=True`, `confirmed=True`; `B` заблокирована
   (`deps.blockers` содержит `A`, `ready_tasks` не содержит `B`);
5. повтор той же жёсткой связи → `created=False`, строк по-прежнему одна;
6. `issue_id == depends_on` → `ValueError`; цикл из двух (`A→B`, затем `B→A`) и из трёх задач
   → `ValueError`, текст содержит слово `цикл` и оба ID; после отказа `deps.cycles(conn)` пуст
   и `blocked_by` у задач не изменился;
7. `related` и `supersedes` (как импортирует шаг 03, `confirm=True`) — не в `blocked_by`, есть в
   `deps_state.soft_links` с `dep_type`, `deps.ready(...)["ready"]` истинно — регрессия шага 03;
8. две независимые порции обе в `ready_tasks`; зависимая (`blocks` на первую) отсутствует, а
   после `update_task(status="done")` блокера появляется;
9. `remove_dep(B, A)` без типа после предложения + жёсткой (создать обе через прямой INSERT)
   → `removed == 2`, `dep_types` содержит оба; с явным `dep_type="suggested-blocks"` → только
   предложение снято, `blocks` остаётся;
10. `deps.suggested(conn)` после п. 1 содержит один элемент с `issue_id`, `depends_on`,
    `created_by`; после подтверждения — пуст; фильтр `project=` отсекает чужой проект; закрытая
    зависимая задача не показывается;
11. CLI `--local dep add <B> <A> --actor agent:codex` — код 0, stdout содержит `предложение`;
    `--local dep add <A> <B> --actor me` при уже существующей `B→A` — код 1, stderr содержит
    `цикл` и **не содержит** `Traceback`; `--local dep confirm <B> <A> --actor me --json` —
    stdout парсится как JSON с `confirmed == true`; `--local dep suggested --json` — JSON-список;
    `--local show <B>` после предложения печатает строку `предложены блокеры`; висячая мягкая
    связь (`add_dep(B, X, "relates-to", "me")`, затем `store.delete_task(conn, X)`) →
    `--local show <B>` код 0, stdout содержит `связано (не блокирует): <X> (relates-to, задача
    не найдена)`, и `--local dep tree <B>` код 0 — оба без `Traceback`. CLI-тесты — по
    образцу `tests/test_needs_owner.py` (`sys.executable`, абсолютный путь, `--local`,
    `env["LISTIK_DB"]` = временная база, `capture_output=True`).

## Границы правки

- Файлы: `listik/store.py` (только `add_dep`, `remove_dep`), `listik/deps.py` (новая
  `suggested`; `HARD_BLOCKERS`/`SOFT_LINKS`/`DEP_TITLES` не менять), `listik/server.py` (два
  новых маршрута, ветка `deps`), `listik/client.py` (`local_call`: `dep_remove`,
  `dep_suggested`), `bin/listik` (`main`, `cmd_dep`, `show_task`, парсер `dep`, в
  `cmd_dep_tree` — только fallback подписи типа), `listik/mcp.py` (только ветка `listik_deps`
  в `call_tool`: умолчание актора `args.get("actor") or LISTIK_ACTOR or "agent:mcp"`, передача
  `dep_type` в rm — и фраза в описании; `inputSchema` и другие инструменты не трогать),
  `tests/test_deps.py` (новый).
- Не трогать: `actors.py` (`resolve` не расширять — правило префикса `agent:` живёт в
  `add_dep`), `documents.py` (контракт `context`, `dependencies.suggested[]` читает те же
  строки и продолжит работать), `import_beads.py`, `import_writerllm.py` (зовут
  `add_dep(..., confirm=True)` — жёсткие связи из выгрузки остаются жёсткими), `web/`,
  `db.py` (схема не меняется: `suggested-blocks` — значение `dep_type`, не колонка),
  `config.py`, `claim`/`next_stage`/`add_comment` в store (порция c), routing (порция b),
  документацию `README.md`/`API.md`/`docs/harness-protocol.md`/`AGENTS.md` (порция d).
- Событий на `dep add`/`confirm`/`rm` не писать; нового MCP-инструмента не добавлять.
- Не менять форму `deps_state` (`deps.ready`) и ответы `GET /api/tasks/{id}`, `/api/ready`,
  `/api/blocked`; не менять `dep link`/`dep suggest`.
- Тесты не подгонять под реализацию: если тест из п. 7 не проходит, чинится код. Существующие
  тесты (`python3 -m unittest discover tests`) должны остаться зелёными; их не править.
- Реальную `listik.db` и `config.toml` не трогать; тесты — только на временной базе.

## Как проверить

```sh
export LISTIK_DB=/tmp/listik-step04a.db && ./bin/listik --local init
A=$(./bin/listik --local new "A" -p demo --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
B=$(./bin/listik --local new "B" -p demo --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
./bin/listik --local dep add $B $A --actor agent:codex      # предложение
./bin/listik --local dep suggested                          # одна строка
./bin/listik --local ready                                  # A и B — обе можно брать
./bin/listik --local dep confirm $B $A --actor me           # жёсткая, предложение заменено
./bin/listik --local ready                                  # только A
./bin/listik --local dep add $A $B --actor me; echo rc=$?   # ошибка: … цикл …, rc=1, без трейсбека
./bin/listik --local show $B                                # заблокирована: A
./bin/listik --local dep rm $B $A                           # снята blocks
python3 -m unittest tests.test_deps -v && python3 -m unittest discover tests
```

HTTP-путь: поднять `LISTIK_DB=… ./bin/listik serve` и повторить `dep add`/`dep rm`/`dep
suggested` без `--local` — результат тот же, `dep rm` не даёт 404.
