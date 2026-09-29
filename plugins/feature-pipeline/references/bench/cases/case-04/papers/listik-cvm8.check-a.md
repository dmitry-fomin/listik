Рабочее дерево: {{TREE}}

# listik-cvm8, порция a — чек-лист приёмки

Пункты 1–16 — тесты в `tests/test_stage_transition_event.py`, если не сказано иное. «Падает до
правки» означает: на коммите `HEAD` тест красный (нет ключа, колонки или значения), после
правки — зелёный. Пункты, помеченные **[по чтению]**, проверяются чтением кода или diff.

Общая подготовка для пунктов 5–12:
- config изолирован (`paths.CONFIG_PATH` → `<tmp>/config.toml`), файла нет, то есть действует
  дефолт;
- `store.upsert_project(conn, "demo")`;
- задача `tid = store.create_task(conn, title="t", project="demo", stage="s1-spec")["id"]`;
- держатель `store.claim(conn, tid, holder="agent:x")`.

## Схема и миграция

1. На свежей базе (`TempDbTestCase`) в `PRAGMA table_info(events)` есть колонка `transition`:
   тип `TEXT`, `notnull = 0`, `dflt_value = NULL`. `db.SCHEMA_VERSION == 14`, в `meta` записано
   `schema_version = '14'`. Падает до правки.
2. Досыпка на старой базе. База собрана своей схемой, где `events` без `transition` (см. ТЗ,
   п. 8). В ней восемь событий:
   - (а) `stage`, `to_value='s2-review'`, `note='этап -> s2-review (handoff)'`;
   - (б) `stage`, `to_value='s4-judge'`, `note='этап -> s4-judge (sticky)'`;
   - (в) `stage`, `to_value='s3-impl'`, `note='этап -> s3-impl (sticky-return)'`;
   - (г) `stage`, `to_value='s3-impl'`, `note='перешёл (sticky) к делу'`;
   - (д) `note`, `note='этап -> s2-review (sticky)'`;
   - (е) `stage`, `to_value='s2-review'`, `note='этап -> s3-impl (sticky)'` — заметка про другой
     этап;
   - (ж) `stage`, `from_value='s4-judge'`, `to_value='done'`, `note='этап -> done (handoff)'` —
     старая запись до listik-rku8;
   - (з) `stage`, `from_value=NULL`, `to_value='s1-spec'`, `note='этап -> s1-spec (sticky)'`.

   После `db.init(path)` колонка есть. Значения: (а) `handoff`, (б) `sticky`, (в)
   `sticky-return`, (з) `sticky`. (г), (д), (е), (ж) остаются `NULL`. Падает до правки.
3. Повторный `db.init` той же базы: `migrate(conn) == []`, ни одна строка `events` не
   меняется (сравнить `SELECT id, transition FROM events` до и после). Досыпка не запускается
   повторно. Проверка: после первой миграции вручную выставить событию (а) `transition = NULL`,
   после повторного `init` оно остаётся `NULL`. Падает, если досыпка идёт на каждом `init`.
4. Проверка через alembic. Команды — в «Проверках порции», CLI `alembic` обязателен.
   - `alembic upgrade head` на пустой временной базе проходит, в `events` есть `transition`.
   - `alembic upgrade 0011_task_orchestrator:0012_event_transition --sql` печатает
     `ALTER TABLE events ADD COLUMN transition TEXT` и UPDATE досыпки с условием
     `to_value <> 'done'`.
   - Тест в `tests/test_stage_transition_event.py` проверяет файл ревизии: `revision =
     "0012_event_transition"`, `down_revision = "0011_task_orchestrator"`, `--sql`, как в
     `tests/test_orchestrator.py` (`AlembicTests`). Без alembic тест делает `skipTest`.
   - **[по чтению]** Текст UPDATE в ревизии совпадает с константой в `db.py`.

## Запись типа перехода

5. Задача из общей подготовки (`s1-spec`, держатель `agent:x`), вызов
   `store.next_stage(conn, tid, note="своя заметка")` без `to_stage` — идёт на следующий этап.
   Последнее событие `stage`:
   - `from_value='s1-spec'`, `to_value='s2-review'`;
   - `transition='handoff'`;
   - `note='своя заметка'`.

   `holder` карточки пуст: handoff снял держателя. Падает до правки: ключа `transition` нет.
6. То же без заметки, `store.next_stage(conn, tid)`: `note == 'этап -> s2-review (handoff)'`
   (формат не изменился), `transition == 'handoff'`.
7. Переопределение проекта до перехода:
   - `store.update_project(conn, "demo", routing={"transitions": {"s1-spec:s2-review":
     "sticky"}})`;
   - затем `store.next_stage(conn, tid, note="моё")`;
   - событие получает `transition='sticky'`, держатель `agent:x` остаётся.
8. Красный вердикт:
   - задачу довести до `s4-judge` вызовом `store.next_stage(conn, tid, to_stage="s4-judge")`;
   - затем `store.add_comment(conn, tid, "VERDICT: FAIL\n1. починить", author="me",
     kind="verdict")`;
   - появляется событие `stage` `s4-judge → s3-impl` с `transition='sticky-return'` и
     `note='возврат после красного verdict'`. Падает до правки.
9. `transition is None` у событий `stage` без перехода конвейера, в трёх случаях:
   - задача доведена до `s4-judge`, как в пункте 8, затем
     `store.next_stage(conn, tid, to_stage="done")` — у события `s4-judge → done`;
   - `tid2 = store.create_task(conn, title="t2", project="demo", stage="s1-spec")["id"]` — у её
     единственного события `stage` (`from_value` `None`);
   - затем `store.update_task(conn, tid2, stage="s3-impl")` (остальные kwargs по умолчанию) — у
     события `s1-spec → s3-impl`.
10. `store.get_task(conn, tid)["events"]`: у каждого элемента есть ключ `transition`, у не-`stage`
    событий (`created`, `claim`, `release`) он `None`. Падает до правки.
11. HTTP через `server.handle`, по образцу `tests/test_routing.py`:
    - задача на `s1-spec` из общей подготовки, дефолтный config;
    - `POST /api/tasks/{tid}/stage` с телом `{"note": "своя"}`, затем `GET /api/tasks/{tid}`;
    - в `events` есть событие `stage` `s1-spec → s2-review` с `note == 'своя'` и
      `transition == 'handoff'`. Падает до правки.
12. Страж регрессии, зелёный и до правки, и после:
    - `PATCH /api/tasks/{tid}` с телом `{"transition": "sticky"}` бросает `server.ApiError` со
      `status == 400` и `code == errors.BAD_ARGUMENT`;
    - `stage`, `holder` и число событий карточки после этого не изменились.
13. `POST /api/tasks/{tid}/stage` с телом `{"transition": "sticky"}` на задаче `s1-spec` при
    дефолтном config: ключ из тела игнорируется, событие получает `transition == 'handoff'`,
    держатель снят. Падает до правки: ключа нет.

## `/api/meta`

14. Дефолтный config: `server.handle("GET", "/api/meta", {}, {}, authed=True)`. В ответе есть
    ключ `routing`:
    - `routing["transitions"] == config.DEFAULTS["routing"]["transitions"]`;
    - `routing["transitions"]["s1-spec:s2-review"] == "handoff"`;
    - `routing["return_window_hours"] == 24`;
    - `"projects" not in routing`.

    Строка проекта `demo` в `projects[]` по-прежнему несёт `routing_effective`. Падает до правки.
15. config.toml с глобальным и проектным переопределением сразу:

    ```toml
    [routing.transitions]
    "s3-impl:s4-judge" = "handoff"

    [routing.projects.demo.transitions]
    "s1-spec:s2-review" = "sticky"
    ```

    Ожидается:
    - `meta["routing"]["transitions"]["s3-impl:s4-judge"] == "handoff"`: общая таблица читает
      config, а не зашитый `DEFAULTS`;
    - `meta["routing"]["transitions"]["s1-spec:s2-review"] == "handoff"`: проектное
      переопределение в общую таблицу не попало;
    - у `demo` в `projects[]` значение `routing_effective.transitions["s1-spec:s2-review"] ==
      "sticky"`.
16. `client.local_call("meta")` на базе теста (`db_mod.init` подменён) **с config.toml из
    пункта 15** отдаёт `routing`, равный `routing` из ответа сервера. В нём, в частности,
    `transitions["s3-impl:s4-judge"] == "handoff"`. Падает до правки. Падает и на реализации, где
    локальная ветка вернула бы зашитый `DEFAULTS`.

## Документация и границы

17. **[по чтению]** В `docs/API.md` изменены четыре места:
    - `events[]` содержит `transition` с пояснением о `null`;
    - есть фраза о `SCHEMA_VERSION` 14 и `0012_event_transition`;
    - в строке `GET /api/meta` упомянут `routing{transitions, return_window_hours}`;
    - в строке `POST /api/tasks/{id}/stage` есть фраза про `transition` события.
18. **[по чтению]** Не изменились:
    - заметка по умолчанию `f"этап -> {nxt} ({transition})"` и заметка закрытия в `next_stage`;
    - `config.transition_kind` и `config.DEFAULTS`;
    - `UPDATABLE`;
    - обработчик `POST /stage` в `server.py`;
    - `store.task_timeline`.
19. **[по чтению]** В семи тестовых файлах со старым номером схемы diff — только замена `13` →
    `14` в перечисленных строках ТЗ.
20. `git diff --name-only` в дереве — ровно:
    - `docs/API.md`;
    - `listik/client.py`;
    - `listik/db.py`;
    - `listik/server.py`;
    - `listik/store.py`;
    - `tests/test_autostart.py`, `tests/test_documents_upload.py`, `tests/test_orchestrator.py`,
      `tests/test_owner_store.py`, `tests/test_routes_db.py`,
      `tests/test_routes_direct_migration.py`, `tests/test_scope_schema.py`.

    Новые файлы по `git status --porcelain` (строки `??`) — только
    `alembic/versions/0012_event_transition.py` и `tests/test_stage_transition_event.py`. `web/`
    не тронут.

## Проверки порции

Затронутые пути и где их гоняют:
- новые требования — `test_stage_transition_event`;
- маршрутизация и routing проектов — `test_routing`;
- `next_stage`, красный вердикт, возврат — `test_claim`, `test_comment_author`,
  `test_release_grace`, `test_stage_launch`;
- тот же этап и `done` — `test_stage_same`, `test_stage_done_closes`;
- `store.event` — `test_worked_by`, `test_release_grace`;
- `/api/events` и fenced `stage` — `test_fencing`;
- stage через MCP — `test_mcp_tools`, `test_mcp_http`;
- CLI stage — `test_stage_same` (CLI-кейсы);
- номер схемы и старые базы — `test_autostart`, `test_documents_upload`, `test_orchestrator`,
  `test_owner_store`, `test_routes_db`, `test_routes_direct_migration`, `test_scope_schema`.

Отдельных тестов на остальные ключи `/api/meta` (`facets`, `actors`) в `tests/` нет, их
неизменность — пункт 18 по чтению. Ветка правится только добавлением ключа.

```sh
cd {{TREE}}
which alembic   # обязателен; пусто — остановка с отчётом
python3 -m unittest tests.test_stage_transition_event tests.test_routing tests.test_claim \
  tests.test_comment_author tests.test_release_grace tests.test_stage_launch \
  tests.test_stage_same tests.test_stage_done_closes tests.test_worked_by tests.test_fencing \
  tests.test_mcp_tools tests.test_mcp_http tests.test_autostart tests.test_documents_upload \
  tests.test_orchestrator tests.test_owner_store tests.test_routes_db \
  tests.test_routes_direct_migration tests.test_scope_schema
T=$(mktemp -d) && LISTIK_DB="$T/a.db" alembic upgrade head \
  && sqlite3 "$T/a.db" "PRAGMA table_info(events)" | grep transition
LISTIK_DB="$T/sql.db" alembic upgrade 0011_task_orchestrator:0012_event_transition --sql \
  | grep -E "ALTER TABLE events ADD COLUMN transition|UPDATE events"
git diff --name-only && git status --porcelain
```

Отдельного линтера в проекте нет. Прогон выше — всё, что затрагивает порция. Полный
`python3 -m unittest discover` не запускает никто: ни исполнитель, ни судья.
