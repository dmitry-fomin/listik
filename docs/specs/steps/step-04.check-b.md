# Приёмка порции 04.b. Маршрутизация: routing проекта и `ready --harness`

Все сценарии — на временной базе (`LISTIK_DB=/tmp/…`, `./bin/listik --local init`) и с
`config.toml` в корне репозитория **без изменений** (`git diff config.toml` пуст до и после;
файл содержит токен — в отчёт не копировать). «Красный до правки» — падает на коммите до
порции; судья проверяет чтением теста.

## Тесты

1. Файл `tests/test_routing.py` существует, наследует `TempDbTestCase`,
   `python3 -m unittest tests.test_routing -v` зелёный; покрыты девять сценариев раздела 6 ТЗ
   (defaults; переопределение из базы поключевое; из TOML и приоритет базы; фильтр `ready_tasks`
   по harness; задача без этапа не фильтруется; guard `claim` только при `harness=`;
   `validate_routing` принимает/отвергает; форма `list_all_projects`; CLI `projects --routing`,
   `--json`, ошибки, `ready --harness`). Отсутствие любого — красный пункт.
2. Каждый тест, читающий routing, подменяет `listik.paths.CONFIG_PATH` на временный путь
   (`mock.patch.object`), а CLI-тесты передают подпроцессу `env["LISTIK_CONFIG"]` с временным
   путём; тест без подмены, который зависит от содержимого реального `config.toml` или пишет в
   него, — красный пункт.
3. `python3 -m unittest discover tests` зелёный целиком; существующие тесты не изменены.

## config / store / deps

4. `config.validate_routing({"harnesses": {"s3-impl": ["dsh"]}, "default_process": ["s3-impl",
   "s4-judge"], "transitions": {"s3-impl:s4-judge": "sticky"}, "return_window_hours": 2})`
   возвращает словарь; `validate_routing({})` → `{}`. **Красный до правки** (функции нет).
5. `validate_routing` поднимает `ValueError` с русским текстом на: `{"bad": 1}` (текст содержит
   `неизвестный ключ`), `{"harnesses": {"s9-x": ["dsh"]}}`, `{"harnesses": {"s3-impl": "dsh"}}`,
   `{"return_window_hours": 0}`, `{"transitions": {"s1-spec:s2-review": "magic"}}`,
   `{"default_process": ["s3-impl", "s3-impl"]}`.
6. `store.update_project(conn, "demo", routing={"harnesses": {"s3-impl": ["dsh"]}})` пишет в
   колонку JSON; `routing={}` → колонка `NULL`; `routing="{не json"` → `ValueError`;
   `routing={"bad": 1}` → `ValueError`, колонка не изменилась. **Красный до правки**
   (сегодня пишется что угодно).
7. `config.allowed_harnesses("demo", "s3-impl", conn)` == `["dsh"]` после п. 6, а
   `("demo", "s1-spec", conn)` == список из `DEFAULTS` — поключевое слияние сохранено.
8. `config.allowed_harnesses("demo", None, conn)` == `[]` и `("demo", "done", conn)` == `[]`
   при любом переопределении. **Красный до правки** (сегодня — список `s1-spec`).
9. Временный TOML с `[routing.projects.demo.harnesses]` `s3-impl = ["codex"]` и подменённым
   `CONFIG_PATH`: `allowed_harnesses("demo", "s3-impl", conn)` == `["codex"]`; при
   одновременной колонке в базе `["dsh"]` — `["dsh"]`.
10. `deps.ready_tasks(conn, harness="codex")` при ограничении `s3-impl: ["dsh"]` не содержит
    задачу на `s3-impl`, содержит задачу на `s1-spec` и задачу без этапа; `harness="dsh"` и
    вызов без `harness` содержат все три. Задачи с блокером или держателем не появляются
    независимо от `harness` (как раньше).
11. На **одной** задаче `T` (`s3-impl`, проект с `s3-impl: ["dsh"]`), строго по порядку:
    `store.claim(conn, T, holder="codex", harness="codex")` → `ValueError`, текст содержит
    `codex`, `s3-impl`, `dsh`, держателя у `T` нет; `claim(conn, T, holder="codex")` без
    `harness` → взята, `holder == "codex"`; `store.update_task(conn, T, holder="")` (release);
    `claim(conn, T, holder="dsh", harness="dsh")` → взята, `holder == "dsh"`. Тест, который
    делает третий `claim` без release или на второй задаче того же проекта (упрётся в
    держателя/лок worktree), — красный пункт как невыполнимый; `claim` в `store.py` при этом
    не изменён (проверяется `git diff`).
12. `store.list_all_projects(conn)` → у `demo`: `routing` — `dict` (после п. 6) или `None`
    (после сброса), `routing_effective` — словарь с ключами `default_process, harnesses,
    transitions, return_window_hours`, `routing_source` ∈ {`default`, `config`, `db`,
    `config+db`} и соответствует источнику. **Красный до правки** (сегодня `routing` — строка,
    остальных ключей нет). Те же три ключа — в словаре, который возвращает
    `store.update_project(conn, "demo", routing={"harnesses": {"s3-impl": ["dsh"]}})`:
    `routing_effective["harnesses"]["s3-impl"] == ["dsh"]`, `routing_source == "db"`; после
    `routing={}` — `routing is None`, `routing_source == "default"`. **Красный до правки**
    (сегодня `update_project` → `project_row`, ключей нет). Оба места зовут один хелпер
    (проверяется чтением `store.py`: `list_projects` и `update_project` не дублируют логику).
13. В одном вызове `ready_tasks(conn, harness="dsh")` на 30 задачах одного проекта и этапа
    `config.load` вызывается не более одного раза на пару `(project, stage)` — проверяется
    `mock.patch.object(config_mod, "load", wraps=config_mod.load)` и `call_count` (или чтением
    кода `ready_tasks`, если тест не написан — судья помечает как «проверено чтением»).

## CLI

13a. `--local` в `projects` уважается: при **поднятом** сервере (`./bin/listik serve --daemon`
    на реальной базе — как обычно на машине) команды `LISTIK_DB=/tmp/… LISTIK_CONFIG=/tmp/…
    ./bin/listik --local projects demo --routing '{"harnesses":{"s3-impl":["dsh"]}}'`,
    `--local projects demo`, `--local projects --add …`, `--local projects --archive demo`
    выполняются на временной базе (код 0, без `ошибка 401`), а `./bin/listik projects demo`
    без `--local` на реальной базе не показывает проекта `demo` и `git diff config.toml` пуст.
    **Красный до правки** (сегодня `cmd_projects` не передаёт `local`, хелперы `client.py`
    решают по `is_up`). В `tests/test_routing.py` это же покрыто: `mock.patch.object(client,
    "is_up", return_value=True)` + `mock.patch.object(client, "request")` →
    `client.set_project_routing("demo", {...}, local=True)` и `client.list_projects(local=True)`
    работают через store, `request.assert_not_called()`.
14. `./bin/listik --local projects demo` печатает блок `маршрутизация (источник: по умолчанию)`
    с четырьмя этапами, `процесс по умолчанию: s1-spec → s2-review → s3-impl → s4-judge`,
    строкой `переходы:` и `окно возврата после красного вердикта: 24 ч`. **Красный до правки.**
15. `./bin/listik --local projects demo --routing '{"harnesses":{"s3-impl":["dsh"]}}'` → код 0,
    stdout содержит `s3-impl:   dsh` и `источник: база` — блок печатается из словаря,
    возвращённого `set_project_routing` (в `cmd_projects` после установки нет повторного
    `list_projects`; проверяется чтением); `--routing '{}'` возвращает `источник: по
    умолчанию`. **Красный до правки** (флага нет). Пункты 15–18 выполняются с
    `LISTIK_DB` и `LISTIK_CONFIG` на временные пути и дают тот же результат при поднятом
    сервере (см. 13a).
16. `--routing '{"bad":1}'` → код 1, stderr `ошибка: … неизвестный ключ …`, без трейсбека;
    `--routing 'не json'` → код 1, `ошибка:`; `projects nope --routing '{}'` → код 1, stderr
    содержит `проект не найден` и подсказку `projects --add`.
17. `./bin/listik --local projects demo --json` → JSON-список с одним элементом, в нём
    `routing`, `routing_effective`, `routing_source`.
18. `./bin/listik --local ready --harness codex` после п. 15 не печатает задачу `s3-impl`, а
    `--harness dsh` печатает; `./bin/listik --local claim <T> --holder codex --harness codex` →
    код 1, stderr `ошибка: harness codex не разрешён …`; без `--harness` — код 0.
19. `listik projects` (список без slug), `--add`, `--archive`, `--remove` работают как раньше
    (проверяется чтением неизменённых веток `cmd_projects` и одним запуском `projects`).

## HTTP

20. При сервере на временной базе: `GET /api/projects` → у `demo` есть `routing` (объект или
    null), `routing_effective`, `routing_source`; `PATCH /api/projects/demo` `{"routing":
    {"harnesses": {"s3-impl": ["dsh"]}}}` → 200, и **в ответе `PATCH`** (это результат
    `update_project`, обогащённый общим хелпером) `data.routing_effective.harnesses["s3-impl"]
    == ["dsh"]`, `data.routing_source == "db"`; последующий `GET /api/projects` показывает то
    же; `{"routing": {"bad": 1}}` → 400 с текстом. **Красный до правки** (сегодня в ответе
    `PATCH` ключей нет, невалидный routing — 500 или молча записан).
21. `GET /api/ready?harness=codex` после п. 20 не содержит задачу `s3-impl`; `?harness=dsh`
    содержит — совпадает с CLI.
22. `GET /api/meta` по-прежнему отвечает 200 и содержит `projects` (доска не ломается от новых
    ключей): `cd web && npm run typecheck` зелёный (типы `Project` не сужают ответ).

## Границы

23. `git diff --stat` не содержит `web/`, `listik/db.py`, `listik/mcp.py`, `listik/import_*.py`,
    `README.md`, `API.md`, `docs/harness-protocol.md`, `AGENTS.md`, `config.toml`; в
    `listik/store.py` не изменены `claim`, `next_stage`, `add_comment`, `add_dep`,
    `project_row`, `add_project`, `upsert_project`; в `listik/config.py` `DEFAULTS` и `_dump`
    без изменений; `next_stage` по-прежнему идёт по `PIPELINE_STAGES` (проверяется чтением);
    в `listik/client.py` у хелперов проектов добавлен только параметр `local` (форма ответов
    и тела HTTP-запросов прежние — проверяется `git diff listik/client.py`).
24. Нигде в diff нет вывода harness из `holder`/`actor` (`grep -n "resolve" listik/deps.py
    listik/config.py` не показывает новых вызовов; `claim` в `store.py` без изменений).
