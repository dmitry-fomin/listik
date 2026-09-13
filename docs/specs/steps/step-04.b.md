# Порция 04.b. Маршрутизация: routing проекта — хранение, валидация, показ, `ready --harness`

## Контекст

Listik — трекер задач на stdlib-Python: `bin/listik` (CLI + API-клиент; команды идут через
`call()` — HTTP при живом сервере, иначе `client.local_call()`; команда `projects` использует
отдельные хелперы `client.list_projects`/`add_project`/`set_project_archived`/`remove_project`,
у каждого свой локальный фолбэк), `listik/config.py` (TOML-конфиг `config.toml` в корне,
`DEFAULTS`, `load`, `save`/`_dump`, `routing`, `allowed_harnesses`, `transition_kind`),
`listik/deps.py` (`ready_tasks(harness=)`), `listik/store.py` (`claim` — guard по harness,
`upsert_project`/`update_project`/`list_projects`/`list_all_projects`), `listik/server.py`
(`GET/POST /api/projects`, `PATCH /api/projects/{slug}`, `GET /api/ready`), `listik/mcp.py`
(`listik_ready(harness)`). Тесты — `tests/`, `unittest`, `TempDbTestCase`.

Требование продукта (спека шага 04, п. 1–2; продукт 6.2): хранить в проекте или его конфиге
разрешённых harnesses по этапам и default process; `ready --harness <name>` и API-фильтр с тем
же результатом; критерий приёмки — «`ready --harness codex` не отдаёт задачу, разрешённую только
dsh». Решение автора (12.09.2026): `claim` проверяет таблицу только при явном `--harness`
(необязательный guard), harness из держателя не выводится — это уже так, не менять.

Что есть сейчас (проверено 12.09.2026):

- `config.DEFAULTS["routing"]`: `default_process = [s1-spec, s2-review, s3-impl, s4-judge]`,
  `harnesses = {этап: [claude, dsh, codex, grok]}` (для `s3-impl` — `[codex, dsh, claude, grok]`),
  `transitions = {"s1-spec:s2-review": sticky, "s2-review:s3-impl": handoff, "s3-impl:s4-judge":
  sticky, "s4-judge:s3-impl": sticky-return, "s4-judge:done": handoff}`, `return_window_hours = 24`,
  `projects = {}`.
- `config.routing(project, conn)`: берёт `load()["routing"]`, вынимает `projects`, накладывает
  `[routing.projects.<slug>]` из TOML, затем JSON из колонки `projects.routing` (если строка
  проекта есть), слияние по ключам верхнего уровня; для вложенных словарей (`harnesses`,
  `transitions`) — поключевое (`dict.update`), т. е. переопределение одного этапа не стирает
  остальные. Это поведение сохранить.
- `allowed_harnesses(project, stage, conn)`: `harnesses.get(stage or "s1-spec")` — задача **без
  этапа** (прямая задача, `stage` NULL) и задача на `done` проверяются по списку `s1-spec`.
- `deps.ready_tasks(..., harness=)`: для каждой отобранной задачи зовёт `allowed_harnesses`
  (каждый вызов — `config.load()`, чтение TOML с диска); `allowed` пустой → не фильтруется.
  Работает: с ограничением `s3-impl: [dsh]` `ready --harness codex` задачу не отдаёт.
- `store.claim`: `if harness and allowed and harness not in allowed: raise ValueError(...)`.
- `store.update_project(conn, slug, routing=...)` пишет JSON в колонку без проверки формы;
  `PATCH /api/projects/{slug}` передаёт `routing` из тела. `list_projects`/`list_all_projects`
  отдают `SELECT p.*` — `routing` сырой строкой (или NULL). Ни CLI, ни `GET /api/projects` не
  показывают действующий routing. `default_process` нигде не читается.
- `config.toml` в корне сейчас без секции `[routing]` (работают DEFAULTS); `_dump` умеет
  вложенные таблицы, `ensure_token` при сохранении их не портит.
- `web/src/api/client.ts` шлёт `PATCH /api/projects/{slug}` с `title/path/color/kind/archived`
  — `routing` не трогает, форма ответа для него — как была плюс новые ключи.

Перед началом прочитай: `listik/config.py` целиком; в `listik/deps.py` — `ready_tasks`; в
`listik/store.py` — `claim` (первые строки, guard), `upsert_project`, `update_project`,
`list_projects`, `list_all_projects`, `project_row`, `add_project`, `PIPELINE_STAGES`; в
`bin/listik` — `cmd_projects`, `_projects_table`, парсер `projects`, `cmd_ready`; в
`listik/client.py` — хелперы проектов; в `listik/server.py` — маршруты `/api/projects*`,
`/api/ready`; `listik/paths.py` — `CONFIG_PATH`.

## Что сделать

### 1. `config.validate_routing(obj) -> dict`

Новая функция. Принимает словарь переопределений проекта, возвращает нормализованную копию
или поднимает `ValueError` с русским текстом, что именно не так. Допустимые ключи и формы:

- `harnesses`: словарь `этап → список строк`; этап — из `store.PIPELINE_STAGES`
  (`s1-spec`, `s2-review`, `s3-impl`, `s4-judge`); строки непустые, без дублей (дубли можно
  молча схлопнуть). Пустой список для этапа допустим и означает «нет ограничения» (как
  `allowed_harnesses` трактует пустой список сегодня).
- `default_process`: список этапов из `PIPELINE_STAGES` в любом порядке, без дублей; допустим
  пустой (прямые задачи).
- `transitions`: словарь `"<этап>:<этап|done>" → sticky|handoff|sticky-return`; обе части
  ключа — этапы из `PIPELINE_STAGES` или `done` справа.
- `return_window_hours`: число > 0 (int или float).
- Любой другой ключ → `ValueError("routing: неизвестный ключ <k>")`; не-словарь на входе →
  `ValueError`. `{}` — валидно.

`store.update_project(conn, slug, routing=...)`: `dict` → `validate_routing` → JSON в колонку;
`{}` → колонка `NULL` (сброс); строка → распарсить как JSON и дальше как dict, невалидный JSON
→ `ValueError`. `upsert_project` (импортёры) не трогать.

### 2. Действующий routing в ответах о проекте

- Один общий хелпер в `store.py`, например `_project_with_routing(conn, row: dict) -> dict`:
  берёт строку проекта и дописывает три ключа — `routing` (распарсенный словарь или `None`,
  не сырая строка), `routing_effective` (результат `config.routing(slug, conn)`: полный слитый
  словарь `default_process`, `harnesses`, `transitions`, `return_window_hours`) и
  `routing_source` — одна из строк `default` (нет переопределений), `config` (есть
  `[routing.projects.<slug>]` в TOML), `db` (есть колонка), `config+db` (обе).
- Хелпер зовут **все** места, откуда наружу уходит проект: `list_projects` и
  `list_all_projects` (→ `GET /api/projects`, `/api/meta`; для доски это лишние ключи, она их
  игнорирует), `update_project` (→ ответ `PATCH /api/projects/{slug}` и локальный путь CLI),
  `add_project` не обязателен. Так ответ `PATCH` и то, что CLI печатает после `--routing`,
  содержат `routing_effective` без повторного чтения.
- `PATCH /api/projects/{slug}` с невалидным `routing` → 400 с текстом `ValueError`
  (сейчас `ValueError` в этой ветке не перехватывается — добавить, по образцу `POST`).

### 3. CLI `projects`

- `listik projects <slug>` (показ одного проекта) после таблицы печатает блок:

  ```
  маршрутизация (источник: по умолчанию | config.toml | база | config.toml+база):
    процесс по умолчанию: s1-spec → s2-review → s3-impl → s4-judge
    s1-spec:   claude, dsh, codex, grok
    s2-review: claude, dsh, codex, grok
    s3-impl:   dsh
    s4-judge:  claude, dsh, codex, grok
    переходы:  s1-spec→s2-review sticky, s2-review→s3-impl handoff, …
    окно возврата после красного вердикта: 24 ч
  ```

  Этап без ограничения (пустой список) печатается как `любой`. `--json` — строки проектов с
  `routing`, `routing_effective`, `routing_source`.
- **новый флаг** `--routing '<json>'` вместе с позиционным `<slug>`: установить переопределение
  проекта; `'{}'` — сбросить. Идёт через новый хелпер `client.set_project_routing(slug,
  routing, *, local=False, host=None, port=None)` — HTTP `PATCH /api/projects/{slug}`
  `{"routing": {...}}` при живом сервере **и `local` ложном**, иначе `store.update_project`
  напрямую; возвращает словарь проекта из п. 2 (с `routing_effective`). Невалидный JSON или
  `ValueError` валидации → stderr `ошибка: …`, код 1 (через `SystemExit`, как соседние хелперы).
  Проект без строки в `projects` → `ошибка: проект не найден: <slug>; добавьте его: listik
  projects --add <путь> [--slug …]`. После установки печатается тот же блок «маршрутизация»
  из **возвращённого** словаря — повторного чтения не нужно.
- Флаг `--routing` без `<slug>` → `SystemExit("нужен slug проекта")`.
- **`--local` в `projects`.** Сегодня `cmd_projects` собирает `list_kwargs = {host, port}` и
  не передаёт `local`, а хелперы `client.list_projects`/`add_project`/`set_project_archived`/
  `remove_project` сами решают по `is_up()` — при поднятом `listik serve --daemon` команда
  `./bin/listik --local projects …` уйдёт по HTTP в **реальную** базу (и с временным
  `LISTIK_CONFIG` упадёт 401 без токена). Исправить: у всех четырёх существующих хелперов и у
  нового `set_project_routing` — keyword-параметр `local: bool = False`, при истинном `local`
  HTTP не пробуется вовсе (`is_up` не вызывается); `cmd_projects` кладёт в `list_kwargs`
  `local=getattr(args, "local", False)`. Форма ответов хелперов не меняется.

### 4. `allowed_harnesses`: задача без этапа не фильтруется

`config.allowed_harnesses(project, stage, conn)`: если `stage` пустой/`None` или `done` —
вернуть `[]` (нет ограничения). Для этапов из `PIPELINE_STAGES` — как сейчас. `claim` и
`ready_tasks` менять не нужно — они трактуют `[]` как «разрешено всем».

### 5. `deps.ready_tasks`: один вызов routing на пару (project, stage)

Внутри одного вызова `ready_tasks` кэшировать результат `allowed_harnesses` по ключу
`(project, stage)` (словарь на время вызова), чтобы `ready --harness` на 40 задачах не читал
`config.toml` 40 раз. Результат и порядок выдачи не меняются.

### 6. Тесты: `tests/test_routing.py`

На `TempDbTestCase`. Чтобы не трогать реальный `config.toml`, каждый тест подменяет
`listik.paths.CONFIG_PATH` на файл во временном каталоге (`unittest.mock.patch.object(paths,
"CONFIG_PATH", tmp)`); если файла нет — работают `DEFAULTS`. Строка проекта создаётся
`store.upsert_project(conn, "demo")`, переопределение — `store.update_project(conn, "demo",
routing={...})`. Обязательные тесты:

1. без переопределений `allowed_harnesses("demo", "s3-impl", conn)` == список из `DEFAULTS`;
   `routing_source == "default"` в `list_all_projects`;
2. переопределение из базы `{"harnesses": {"s3-impl": ["dsh"]}}`: `allowed_harnesses("demo",
   "s3-impl")` == `["dsh"]`, а `("demo", "s1-spec")` — по-прежнему список по умолчанию
   (поключевое слияние); `routing_source == "db"`;
3. переопределение из TOML (`[routing.projects.demo.harnesses]` `s3-impl = ["codex"]` во
   временном `CONFIG_PATH`): `allowed_harnesses` == `["codex"]`, `routing_source == "config"`;
   при одновременной колонке в базе с `["dsh"]` побеждает база, `routing_source ==
   "config+db"`;
4. `ready_tasks(conn, harness="codex")` при `s3-impl: ["dsh"]` **не содержит** задачу на
   `s3-impl`, `harness="dsh"` содержит, без `harness` содержит; задача на `s1-spec` (без
   ограничения) отдаётся любому;
5. задача **без этапа** при `{"harnesses": {"s1-spec": ["dsh"]}}` отдаётся
   `harness="codex"` — **красный до правки** (сегодня проверяется по `s1-spec`);
6. guard `claim` — **строго в таком порядке на одной задаче `T` (`s3-impl`, проект `demo`
   с `s3-impl: ["dsh"]`)**, потому что `claim` отказывает чужому держателю и лочит пару
   `(project, worktree)` для соседних задач проекта: (1) `store.claim(conn, T, holder="codex",
   harness="codex")` → `ValueError`, текст содержит `codex`, `s3-impl` и `dsh`, держателя у `T`
   нет; (2) `claim(conn, T, holder="codex")` без `harness` → проходит, `holder == "codex"`;
   (3) снять держателя: `store.update_task(conn, T, holder="")` (это то, что делает
   `release`); (4) `claim(conn, T, holder="dsh", harness="dsh")` → проходит, `holder == "dsh"`.
   Второй задачи того же проекта в этом тесте не заводить (лок worktree);
7. `validate_routing`: принимает пример из п. 2 и `{}`; отвергает неизвестный ключ, этап
   `s9-x` в `harnesses`, не-список в `harnesses`, `return_window_hours = 0` и `-1`, переход
   с неизвестным видом (`"s1-spec:s2-review": "magic"`); `update_project(routing="{не json")`
   → `ValueError`; `update_project(routing={})` → колонка `NULL`;
8. `list_all_projects` → у строки `routing` — `dict`/`None`, `routing_effective` содержит
   четыре ключа `default_process, harnesses, transitions, return_window_hours`; то же —
   в словаре, который возвращает `update_project(conn, "demo", routing={...})`
   (`routing_effective["harnesses"]["s3-impl"] == ["dsh"]`, `routing_source == "db"`);
9. CLI: `--local projects demo --routing '{"harnesses":{"s3-impl":["dsh"]}}'` → код 0, stdout
   содержит `s3-impl:   dsh` и `источник: база`; `--local projects demo --json` → JSON с
   `routing_effective`; `--local projects demo --routing '{"bad": 1}'` → код 1, stderr
   `ошибка: …неизвестный ключ…`; `--local projects nope --routing '{}'` → код 1, `проект не
   найден`; `--local ready --harness codex` после ограничения не печатает задачу `s3-impl`, а
   `--harness dsh` печатает. CLI-тесты по образцу `tests/test_needs_owner.py` (`--local`,
   `env["LISTIK_DB"]`) и **обязательно** с `env["LISTIK_CONFIG"]` = путь к временному (можно
   несуществующему) файлу — `paths.CONFIG_PATH` читает эту переменную, иначе подпроцесс CLI
   прочитает реальный `config.toml`. Все CLI-вызовы `projects` в тестах — с `--local`; это
   работает только после правки п. 3 («`--local` в `projects`»), и тест должен проходить при
   поднятом на машине сервере (`listik serve --daemon` на реальной базе) — т. е. в тесте
   можно дополнительно замокать `client.is_up` на «поднят» и убедиться, что HTTP не вызывался
   (`mock.patch.object(client, "request")` с `assert_not_called`).

## Границы правки

- Файлы: `listik/config.py` (`validate_routing`, `allowed_harnesses`; `routing()` и `_dump`
  не переписывать, `DEFAULTS` не менять), `listik/store.py` (`update_project`,
  `list_projects`/`list_all_projects`, новый хелпер обогащения; `claim` не трогать — guard уже
  такой, как решил автор), `listik/deps.py` (только кэш в `ready_tasks`), `listik/client.py`
  (новый `set_project_routing`; у четырёх хелперов проектов — добавить keyword-параметр
  `local=False`, остальную логику и форму ответа не менять), `listik/server.py` (только ветка
  `PATCH /api/projects/{slug}` — перехват `ValueError` → 400), `bin/listik` (`cmd_projects` —
  в т. ч. передача `local`, `_projects_table` или новая функция печати routing, парсер
  `projects`), `tests/test_routing.py` (новый).
- Не трогать: `next_stage`/`transition_kind` (цепочка этапов жёсткая, `default_process` — только
  хранение и показ), `add_comment`, `claim` (порция c; если тест из п. 6 красный — чинится
  тест по описанному порядку, не `claim`), `add_dep` (порция a, коммит 40e4322 — опираться на
  неё), `project_row`/`add_project`/`upsert_project` в store, `web/`, `db.py` (колонка
  `projects.routing` уже есть), `mcp.py` (`listik_ready(harness)` уже есть), `import_*.py`,
  документацию (порция d).
- Не выводить harness из `holder`/`LISTIK_ACTOR`/`--actor` ни в `claim`, ни в `ready` —
  решение автора.
- Реальный `config.toml` в корне не редактировать и не сохранять через `config.save` из
  тестов или CLI этой порции; `ensure_token` не трогать. Тесты — только на временной базе и
  временном `CONFIG_PATH`; тест, пишущий в `paths.CONFIG_PATH` без подмены, — красный пункт.
- Форму `GET /api/ready`, `deps_state`, `task_line`/`show` не менять.
- Существующие тесты не править.

## Как проверить

```sh
export LISTIK_DB=/tmp/listik-step04b.db && ./bin/listik --local init
./bin/listik --local projects --add /tmp/listik-step04b-repo --slug demo   # каталог создать заранее: mkdir -p
T=$(./bin/listik --local new "порция" -p demo --stage s3-impl --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
D=$(./bin/listik --local new "прямая" -p demo --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
./bin/listik --local projects demo                          # маршрутизация: источник по умолчанию
./bin/listik --local projects demo --routing '{"harnesses":{"s3-impl":["dsh"]}}'
./bin/listik --local ready --harness codex                  # только «прямая»
./bin/listik --local ready --harness dsh                    # обе
./bin/listik --local claim $T --holder codex --harness codex; echo rc=$?   # ошибка: … не разрешён …
./bin/listik --local claim $T --holder codex                # проходит (guard только при --harness)
./bin/listik --local release $T                             # иначе следующий claim упрётся в держателя
./bin/listik --local claim $T --holder dsh --harness dsh    # проходит
./bin/listik --local projects demo --routing '{}'           # сброс
# при поднятом listik serve --daemon повторить projects-команды с --local: реальная база не меняется
python3 -m unittest tests.test_routing -v && python3 -m unittest discover tests
```

HTTP-путь: при поднятом сервере `GET /api/projects` → у `demo` есть `routing_effective`;
`PATCH /api/projects/demo` `{"routing": {"bad": 1}}` → 400.
