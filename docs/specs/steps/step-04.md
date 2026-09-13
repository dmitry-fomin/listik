# Шаг 04. Маршрутизация и зависимости — план порций

Спека шага: `docs/specs/steps/step-04-routing-and-dependencies.md`. Продукт:
`docs/specs/listik-product.md` (разделы 4 «Процесс и гранулярность», 6.2 «Маршрутизация и
зависимости», 9 «`ready` не отдаёт задачу с незакрытым hard blocker или чужим holder»). Дух и объём —
`docs/listik-vision.md`: ничего сверх спеки. Самокритика (`docs/specs/self-critique.md`):
«suggestion и `relates-to` создаются автоматически, а `blocks` требует подтверждения»;
«worktree-lock с распределённым менеджером» — отложено, здесь только лок в одной базе.

## Цель

Выбор harness и порядок порций — данные проекта, готовность задачи считает сервер механически:
`ready --harness X` отдаёт только то, что можно взять этим harness прямо сейчас; `claim` отказывает
с внятной причиной (блокер, чужой держатель, занятое рабочее дерево); жёсткая связь `blocks` от
агента — только предложение, пока человек не подтвердил; циклы не проходят; возврат после
красного вердикта живёт в журнале событий и по истечении окна корректно снимает держателя.
Claude остаётся диспетчером, Listik — не оркестратор.

## Что уже есть в коде (после шага 03, коммит 1aa4128)

Черновик всех семи требований лежит в коммите 3452012 (Codex MVP), тестов на него нет. Прогон на
временной базе 12.09.2026 показал, что работает и что нет:

- `listik/config.py`: `DEFAULTS["routing"]` (`default_process`, `harnesses` по этапам,
  `transitions` sticky/handoff/sticky-return, `return_window_hours = 24`, `projects = {}`),
  `routing(project, conn)` — слияние defaults ← `config.toml [routing]` ← `[routing.projects.<slug>]`
  ← колонка `projects.routing` (JSON); `allowed_harnesses(project, stage, conn)`;
  `transition_kind(...)`. `_dump` умеет вложенные таблицы, `ensure_token` конфиг не портит.
  **Нет** ни CLI, ни вывода routing проекта; `PATCH /api/projects/{slug}` принимает `routing`
  без валидации; `GET /api/projects` отдаёт колонку сырой строкой. `default_process` нигде не
  читается. Задача **без этапа** проверяется по списку `s1-spec` (`harnesses.get(stage or "s1-spec")`).
- `listik/deps.py`: `HARD_BLOCKERS = (blocks, blocked-by, waits-for, conditional-blocks)`,
  `SOFT_LINKS` включает `related`, `supersedes`, `suggested-blocks`; `ready_tasks(harness=)`
  фильтрует по `allowed_harnesses` — **работает**; `expire_return_handoffs` снимает держателя
  на `s3-impl`, если последнее событие `stage s4-judge→s3-impl` старше окна — **снимает даже
  при heartbeat минуту назад** (не смотрит на активность после возврата), вызывается только из
  `ready_tasks`; `cycles()` находит циклы по всей базе. Связи `related`/`supersedes` (импорт
  WriterLLM, шаг 03) — мягкие: не попадают в `blocked_by`, задача с ними «можно брать» —
  проверено, нужен только регрессионный тест.
- `listik/store.py`: `claim` — guard по harness (только если передан `--harness`), идемпотентен
  для того же держателя, отказывает чужому, лочит `(project, worktree)` **для любого этапа**
  (держатель карточки шага на `s1-spec` в основном дереве не даёт взять порцию на `s3-impl`),
  отказ по блокеру с текстом «Ждём завершения: … Варианты: …» — **работает**. `add_dep` —
  агент без `confirm` получает `suggested-blocks` **только если зависимая задача на `s1-spec`**
  (порция без этапа → жёсткая связь без подтверждения); подтверждение человеком **не удаляет**
  строку предложения (в `deps` остаются обе); проверка цикла есть (BFS), текст английский;
  `remove_dep` удаляет ровно один тип. `add_comment` при красном вердикте на `s4-judge` пишет
  `UPDATE tasks SET stage='s3-impl'` напрямую — событие `stage` без `duration_s`, мимо
  `update_task`. `next_stage`: handoff снимает держателя, sticky оставляет (шаг 02).
- `bin/listik`: `ready --harness` (общий флаг), `dep add|rm|tree|cycles|suggest|link`,
  `--confirm`; ошибки локального пути (`ValueError`/`KeyError` из store) вываливаются
  **трейсбеком**, HTTP-путь печатает `ошибка 400: …`. `show` не печатает мягкие связи и
  предложения (`dep tree` печатает). `projects <slug>` — таблица без routing.
- `listik/server.py`: `GET /api/ready?harness=`, `POST /api/tasks/{id}/deps` с `confirm`;
  **нет** маршрута `DELETE /api/tasks/{id}/deps/{dep}` (CLI `dep rm` при поднятом сервере
  упадёт в 404). `listik/mcp.py`: `listik_ready(harness)`, `listik_deps(action, confirm)`.
- `listik/documents.py`: `context` на `s1`/`s2` отдаёт `dependencies.suggested[]` (шаг 01) —
  читает строки `dep_type='suggested-blocks'`; форма не меняется.
- `docs/harness-protocol.md` (шаг 02): «жёсткую `dep add` без подтверждения человека не
  ставить»; блок в `AGENTS.md` побайтно равен протоколу (`tests/test_migrate.py`), обновляется
  `./bin/listik init-projects --only listik`.
- Тесты: `tests/helpers.py` (`TempDbTestCase`), образец CLI-теста — `tests/test_needs_owner.py`
  (`--local`, `LISTIK_DB` временной базы, `sys.executable`).

## Решения автора (12.09.2026)

1. Worktree-лок держат только **пишущие** задачи: держатель на `s3-impl`, `s4-judge` или задачи
   без этапа (прямая задача «claim → код → done»). Держатели на `s1-spec`/`s2-review` дерево не
   занимают, и их собственный `claim` о дерево не спотыкается.
2. `claim` проверяет таблицу harness только при явном `--harness X` (необязательный guard);
   фильтр по harness — `ready --harness`. Harness из держателя/`LISTIK_ACTOR` не выводится.

## Допущения планировщика (автор не спрашивался; менять при несогласии)

- Жёсткая связь от **агента** (`actors.resolve(...)` → kind `agent`) без `--confirm` всегда
  записывается как `suggested-blocks`, независимо от этапа зависимой задачи. Человек
  (`me`, неизвестное имя, пустой актор) или любой вызов с `--confirm` пишет жёсткую связь сразу —
  «явная команда» = `--confirm`/`dep confirm`; протокол запрещает агентам её использовать.
- Подтверждение (`dep confirm <id> <dep>` = `dep add … --confirm`) заменяет строку
  `suggested-blocks` строкой `blocks`; отклонение — `dep rm <id> <dep>` (без типа снимает и
  `blocks`, и `suggested-blocks` для пары). Список ожидающих предложений — `dep suggested
  [--project]` и `GET /api/deps/suggested`; в MCP отдельного инструмента нет.
- Routing проекта задаётся `listik projects <slug> --routing '<json>'` (колонка
  `projects.routing`) или руками в `config.toml` `[routing.projects.<slug>]`; `projects <slug>`
  показывает действующий (слитый) routing. `default_process` хранится и показывается, но
  на цепочку `next_stage` (жёстко `s1→s2→s3→s4→done`) не влияет — это подсказка диспетчеру.
- Задача без этапа (`stage` NULL или `done`) под фильтр `--harness` не попадает — отдаётся
  любому harness.
- Окно возврата по умолчанию остаётся `return_window_hours = 24` (совпадает с порогом
  «брошена» на доске), переопределяется на проекте. Истечение проверяется лениво: при `ready`
  и при `claim` этой задачи; отдельного фонового потока нет. Держатель, проявивший активность
  после возврата (`holder_at` новее события возврата), не снимается.
- `--force` у `claim` обходит только блокеры (как сегодня); чужого держателя и занятое дерево
  он не обходит — для этого есть `release`.
- Событие на `dep add`/`dep confirm` не пишется: кто и когда поставил связь — `deps.created_by`,
  `deps.created_at`.

## Порции (по порядку, каждая опирается на закоммиченные предыдущие)

| Порция | Суть | Файлы |
| --- | --- | --- |
| a | Зависимости: предложение от агента всегда, подтверждение заменяет предложение, `dep confirm`/`dep suggested`, `dep rm` без типа, русский текст цикла с путём, `DELETE …/deps/{dep}` и `GET /api/deps/suggested`, чистые ошибки CLI вместо трейсбека, `show` печатает предложения и мягкие связи, регрессия `related`/`supersedes`; тесты `tests/test_deps.py` | `step-04.a.md`, `step-04.check-a.md` |
| b | Маршрутизация: валидация routing, `projects <slug> --routing`, вывод действующего routing в CLI и `GET /api/projects`, задача без этапа не фильтруется, кэш `allowed_harnesses` в `ready_tasks`; тесты `tests/test_routing.py` | `step-04.b.md`, `step-04.check-b.md` |
| c | `claim` и окно возврата: лок дерева только для пишущих этапов, сообщения об отказе с держателем и вариантами, возврат по красному вердикту через `next_stage` (событие с длительностью), истечение окна с учётом активности и в `claim`; тесты `tests/test_claim.py` | `step-04.c.md`, `step-04.check-c.md` |
| d | Документация: `README.md` (routing, связи, лок, окно), `API.md`, `docs/harness-protocol.md` + `AGENTS.md`, `CLAUDE.md`, описания MCP-инструментов | `step-04.d.md`, `step-04.check-d.md` |

## Вне шага

- Доска `web/` (шаги 05–06): показ предложений, routing и лока в UI; `web/src/api/client.ts`
  не меняется — `addDep` шлёт `{depends_on, dep_type, actor}`, как раньше.
- Крон-диспетчер, автозапуск harness, оценщик DeepSeek, выбор процесса по `default_process`
  сервером (`next_stage` идёт по жёсткой цепочке).
- Изменение детектора красного вердикта (подстроки «красн»/«red»/«fail»/«не прой»/«❌» —
  шаг 02) и контракта `context` (шаг 01, `dependencies.suggested[]` остаётся как есть).
- Запуск Codex из Claude (шаг 07), `project-skills/…/SKILL.md`, `bin/listik-codex`.
- Правки `import_beads.py`/`import_writerllm.py`: они зовут `add_dep(..., confirm=True)` и
  продолжают писать жёсткие связи из выгрузки.
- Фоновая проверка окна возврата в `server.py`; распределённый worktree-lock.

## Как проверять любую порцию

```sh
export LISTIK_DB=/tmp/listik-step04.db   # любой путь вне репозитория
./bin/listik --local init
python3 -m unittest discover tests        # без сервера и Ollama
```

`--local` заставляет CLI ходить в SQLite напрямую. HTTP-путь проверяется сервером на той же
временной базе: `LISTIK_DB=… ./bin/listik serve` в отдельном терминале (токен — `./bin/listik
token`; в отчёты и чек-листы токен не копировать). Реальную `listik.db` и `config.toml` в корне
репозитория ни один тест не трогает: путь к базе всегда явный, `paths.CONFIG_PATH` в тестах
подменяется на временный файл.
