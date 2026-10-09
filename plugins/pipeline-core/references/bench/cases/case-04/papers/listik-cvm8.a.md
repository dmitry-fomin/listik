Рабочее дерево: {{TREE}}

# listik-cvm8, порция a — тип перехода в событии `stage` и таблица маршрутизации в `/api/meta`

Весь код пиши в этом дереве, а не в основном
дереве репозитория. Все пути ниже даны от корня дерева.

Бэкенд — чистый stdlib Python без сборки. Тесты на `unittest`. У каждого теста своя временная
sqlite-база (`tests/helpers.py: TempDbTestCase`), а `tests/__init__.py` уводит `LISTIK_HOME` во
временный каталог.

## Контекст (баг)

1. **Тип перехода живёт только в тексте заметки.** `store.next_stage` вычисляет тип перехода
   (`sticky` / `handoff` / `sticky-return`) через `config.transition_kind` и от него решает, снимать
   ли держателя. В событие `stage` тип попадает только внутри заметки по умолчанию
   `этап -> <этап> (<тип>)`. Если агент передал `--note`, заметка пользовательская, и тип потерян.
   Возврат после красного вердикта всегда идёт с заметкой «возврат после красного verdict», так
   что его `sticky-return` не сохраняется никогда. Доска пытается вытащить тип из заметки
   регуляркой. Эту регулярку уберёт порция b, а данные для неё даёт эта порция.
2. **Доске неоткуда взять общую таблицу переходов.** `/api/meta` отдаёт `projects[]` с
   `routing_effective` (действующая таблица каждого проекта), но общей таблицы config.toml без
   переопределений проекта в ответе нет. Её нужно показывать на доске «все проекты» и для задач,
   проект которых не пришёл в `projects[]`, например скрытый.

## Сверено с кодом (коммит HEAD, это дерево)

- `listik/db.py`:
  - `SCHEMA_VERSION = 13` (строка 14);
  - таблица `events` в `SCHEMA` (строки 133–146) с колонками `id, task_id, ts, kind, from_value,
    to_value, actor, harness, note, duration_s`;
  - список мягких миграций `MIGRATIONS` (строка 303), кортежи `(table, column, decl)`;
  - `migrate(conn)` (строка 374) добавляет недостающие колонки и возвращает список
    `"table.column"` добавленных;
  - `init()` (строка 385) вызывает `migrate`, пишет `schema_version` в `meta`, затем
    `drop_direct_routes`.
  - Образец «SQL переноса данных в константе, тот же текст в alembic-ревизии» — `DROP_DIRECT_SQL`
    с `drop_direct_routes` и `RENAME_ORCHESTRATOR_SQL`.
- `alembic/versions/0011_task_orchestrator.py` — последняя ревизия, `revision =
  "0011_task_orchestrator"`. Образец ревизии с добавлением колонки —
  `alembic/versions/0006_task_owner.py`: онлайн колонка добавляется, только если её нет, в режиме
  `--sql` ALTER печатается безусловно. `downgrade` — `pass` с комментарием, как в
  `0008_task_scope_fencing.py`.
- `listik/store.py`:
  - `event(conn, task_id, kind, *, from_value, to_value, actor, harness, note, duration_s, ts)`
    (строка 210) — единственная точка вставки в `events`;
  - `update_task(conn, task_id, *, actor, harness, note, as_owner, **fields)` (строка 768).
    Событие `stage` пишется на строке 890, при смене поля `stage`;
  - `next_stage` (строка 1353):
    - `transition = config_mod.transition_kind(...)` (строка 1388);
    - handoff без `holder` снимает держателя;
    - вызывает `update_task(..., note=note or f"этап -> {nxt} ({transition})", **fields)`
      (строка 1400);
    - переход в `done` идёт через `close_task` (строка 1386), и `transition_kind` там не
      вычисляется;
    - тот же этап уходит в `stage_unchanged`, и события `stage` нет;
  - возврат после красного вердикта: `add_comment` (строка 1250) вызывает
    `next_stage(conn, task_id, to_stage="s3-impl", ..., note="возврат после красного verdict")`;
  - `create_task` с этапом пишет событие `stage` на строке 411 (`from_value=None`);
  - какие заметки у событий `stage` пишет код сейчас (важно для досыпки, п. 2):
    - `create_task` (строка 411) заметку не передаёт, `note` равен `NULL`;
    - переход в `done` через `next_stage` пишет `этап -> done (закрыта из <этап>)` (строка
      1387). `listik done`, `/done` и `listik_done` передают в `close_task` заметку
      пользователя или `None`. До listik-rku8 (2026-09-14) переход в `done`
      шёл обычной веткой `next_stage`, поэтому в старых базах бывает `этап -> done (handoff)`;
    - `update_task`/`PATCH` с полем `stage` пишет заметку из запроса или `NULL`;
    - `next_stage` из задачи без этапа в `s1-spec` пишет `этап -> s1-spec (sticky)`:
      `transition_kind` без исходного этапа даёт `sticky`. После правки это событие получит
      `transition='sticky'`;
  - `get_task` (строки 1434–1437) выбирает события явным списком
    `ts, kind, from_value, to_value, actor, harness, note, duration_s`;
  - `check_update_fields` (строка 480) отвечает `bad_argument` на ключ правки вне `UPDATABLE`.
- Все вызовы `next_stage` проходят через одну функцию: `server.py` (`POST /api/tasks/{id}/stage`,
  строка 1270), `client.local_call("stage")`, `mcp.py` (`listik_stage`), `stage_launch.py`,
  `launcher.py`, `add_comment`. Правка в `next_stage`/`update_task` покрывает их все.
- `listik/config.py`:
  - `DEFAULTS["routing"]["transitions"]` (строки 50–58): `s1-spec:s2-review` handoff,
    `s2-review:s3-impl` handoff, `s3-impl:s4-judge` sticky, `s4-judge:s3-impl` sticky-return,
    `s4-judge:done` handoff;
  - `return_window_hours` 24;
  - `routing(project=None, conn=None)` (строка 380) без проекта возвращает общую таблицу без
    ключа `projects` и без устаревших ключей. Проверено запуском на пустом `LISTIK_HOME`:
    `{'transitions': {'s1-spec:s2-review': 'handoff', 's2-review:s3-impl': 'handoff',
    's3-impl:s4-judge': 'sticky', 's4-judge:s3-impl': 'sticky-return', 's4-judge:done':
    'handoff'}, 'return_window_hours': 24}`. Глобальное `[routing.transitions]` из config.toml
    попадает сюда через `load()`;
  - `transition_kind` (строка 454): нет этапа или тот же этап — `sticky`, ключа нет в таблице —
    `handoff`.
- `listik/server.py`, `/api/meta` (строки 907–915). `listik/client.py`, `local_call`, ветка `meta`
  (строки 214–222), — тот же словарь для работы без сервера. `/api/events` (строка 1447) отдаёт
  `SELECT *` и новую колонку подхватит сам.
- `server.error_response` возвращает четыре поля: `(статус, сообщение, код, подсказка)`. Если
  тест его распаковывает, распаковывай четыре. Тесты через `server.handle(...)` получают
  `server.ApiError` с полями `.status` и `.code`, образец — `tests/test_routing.py`, `_patch`.
- Сигнатуры для тестов (сверено, все параметры после `*` необязательны, кроме отмеченных):
  - `create_task(conn, *, title, project=None, ..., stage=None, ...)` — `title` обязателен;
  - `claim(conn, task_id, *, holder, harness=None, note=None, force=False, actor=None,
    as_owner=None)` — `holder` обязателен;
  - `next_stage(conn, task_id, *, holder=None, note=None, harness=None, to_stage=None,
    actor=None, as_owner=None)` — без `to_stage` идёт на следующий этап конвейера;
  - `update_task(conn, task_id, *, actor=None, harness=None, note=None, as_owner=None,
    **fields)`;
  - `update_project(conn, slug, **fields)` — `routing` словарём;
  - `add_comment(conn, task_id, text, *, author=None, kind="comment", harness=None,
    created_at=None)`. Вердикт с автором-человеком (`author="me"`) принимается. Красный вердикт
    на `s4-judge` сам вызывает возврат в `s3-impl`.
- Номер схемы 13 зашит в тестах: `tests/test_autostart.py` (строки 198, 199),
  `tests/test_documents_upload.py` (76), `tests/test_orchestrator.py` (93),
  `tests/test_owner_store.py` (134, 137), `tests/test_routes_direct_migration.py` (108),
  `tests/test_routes_db.py` (628), `tests/test_scope_schema.py` (75, 76).

## Требования

### 1. Колонка `events.transition`

- В `db.SCHEMA` таблица `events` получает колонку `transition TEXT`: nullable, без значения по
  умолчанию, после `duration_s`. Рядом нужен комментарий: «тип перехода, применённый `stage`:
  sticky | handoff | sticky-return; NULL — не переход конвейера».
- В `MIGRATIONS` добавляется `("events", "transition", "TEXT")`.
- `SCHEMA_VERSION` становится 14.

### 2. Досыпка старых событий при добавлении колонки

Досыпка нужна, чтобы история, записанная до этой версии, не потеряла тип перехода.

- Досыпка выполняется, **только когда `migrate` в этом же `init` добавил `events.transition`**.
  Повторный `init` и свежая база её не запускают.
- Для каждого `k` из `sticky`, `handoff`, `sticky-return` пишется `transition = k` в строки, где
  выполнены все условия:
  - `kind = 'stage'`;
  - `transition IS NULL`;
  - `to_value <> 'done'`;
  - `note` в точности равна `'этап -> ' || to_value || ' (' || k || ')'`.
- Условие `to_value <> 'done'` обязательно. Новый код переход в `done` не помечает (п. 4), а в
  старых базах бывают заметки `этап -> done (handoff)` времён до listik-rku8 (см. «Сверено»). Без
  условия досыпка разошлась бы с п. 4.
- Условия на `from_value` нет. Переход из «без этапа» в `s1-spec` через `next_stage` и после
  правки пишет `sticky`, так что досыпка с этим согласна. События создания с этапом имеют
  `note = NULL` и под условие не попадают.
- Остальные строки остаются `NULL`: пользовательские заметки, события других видов, заметка про
  другой этап, чем `to_value`, переходы в `done`. Пользователь мог вручную написать через PATCH
  заметку ровно в формате по умолчанию. Такую запись от настоящей не отличить, она получит тип
  из заметки. Это принято.
- SQL лежит константой в `db.py` рядом с функцией досыпки. В alembic-ревизии тот же текст, как
  у `DROP_DIRECT_SQL`/`RENAME_ORCHESTRATOR_SQL`.

### 3. Alembic-ревизия `alembic/versions/0012_event_transition.py`

- Ревизия: `revision = "0012_event_transition"`, `down_revision = "0011_task_orchestrator"`. В
  докстринге (по-английски, как у соседних ревизий) сказано, что колонка добавляется и
  досыпается по заметке по умолчанию.
- `upgrade` онлайн: если колонка уже есть, ничего не делает. Если колонки нет, выполняет
  `ALTER TABLE events ADD COLUMN transition TEXT` и затем UPDATE досыпки из п. 2.
- `upgrade` в режиме `--sql`: печатает ALTER и UPDATE безусловно.
- `downgrade` — `pass` с комментарием, по образцу `0008_task_scope_fencing.py`.

### 4. Запись типа перехода

- `store.event()` получает keyword-параметр `transition=None` и пишет его в колонку.
- `store.update_task()` получает keyword-only параметр `transition: str | None = None`. Это
  служебный параметр, а не поле задачи: в `UPDATABLE` его нет, в `**fields` он не попадает. Он
  передаётся только в событие `stage`, и только когда в этом вызове меняется `stage`.
- `PATCH /api/tasks/{id}` с ключом `transition` в теле по-прежнему отвечает `400 bad_argument`
  через `check_update_fields`, как на любой неизвестный ключ. Это уже так, менять ничего не нужно,
  но проверить нужно.
- `POST /api/tasks/{id}/stage` читает из тела только свои ключи (`holder`, `note`, `harness`,
  `actor`, `to`/`stage`, строка 1270 `server.py`). Ключ `transition` в теле игнорируется: тип
  события всегда берётся из таблицы маршрутизации. Обработчик не меняется, это тоже нужно
  проверить.
- `next_stage` передаёт вычисленный `transition` в `update_task` на каждом переходе, кроме
  перехода в `done`. Держатель снимается и сохраняется по тем же правилам, что сейчас. Заметка по
  умолчанию `этап -> {nxt} ({transition})` не меняется. Пользовательская заметка пишется как есть,
  а `transition` при ней всё равно заполнен.
- Событие `stage` без перехода конвейера оставляет `transition` пустым (`NULL`). Таких случаев
  три:
  - переход в `done` через `close_task`;
  - создание задачи сразу с этапом;
  - правка поля `stage` через `update_task`/`PATCH` без `next_stage`.

### 5. Чтение

- `get_task` добавляет `transition` в список колонок событий, после `duration_s`. Ключ
  `transition` есть у каждого события в `events[]`, у не-`stage` он `None`.
- `/api/timeline` (`store.task_timeline`) не меняется.

### 6. `routing` в `/api/meta`

- `server.py`, `/api/meta`: в ответе появляется ключ `routing` со значением
  `config_mod.routing()` без проекта: `{"transitions": {...}, "return_window_hours": N}`. Ключа
  `projects` внутри нет, переопределения проектов не подмешаны.
- `client.local_call("meta")` отдаёт тот же ключ `routing`: без сервера ответ совпадает по форме.
- Остальные ключи ответа (`projects, actors, facets, statuses, stages, priorities`) не меняются.

### 7. `docs/API.md`

- Строки 115–116: список полей `events[]` дополняется `transition` с пояснением: тип перехода,
  применённый `stage` (`sticky`/`handoff`/`sticky-return`); `null` — переход в `done`, создание
  с этапом, правка `stage` через PATCH, а также события старше этой версии, заметку которых
  досыпка не распознала.
- Рядом, по образцу абзаца про `orchestrator` (строки 109–110), одна фраза: «колонка
  `events.transition`, `SCHEMA_VERSION` 14, alembic `0012_event_transition`; старые события
  досыпаны по заметке по умолчанию».
- Строка таблицы `GET /api/meta` (строка 1398): в ответе появляется
  `routing{transitions, return_window_hours}` — общая таблица config.toml без переопределений
  проектов. Действующая таблица проекта — `projects[].routing_effective`.
- Строка `POST /api/tasks/{id}/stage` (строка 1568): одна фраза о том, что событие `stage`
  несёт `transition` — вид применённого перехода, независимо от `note`.

### 8. Тесты

- Новый файл `tests/test_stage_transition_event.py` с кейсами из чек-листа. Конфиг изолируй так
  же, как `tests/test_routing.py` (`RoutingTests.setUp`: `mock.patch.object(paths,
  "CONFIG_PATH", <tmp>/config.toml)`). Для `client.local_call` подменяй `db_mod.init` на
  соединение теста, как в `tests/test_autostart.py` (около строки 700).
- Старую базу для теста досыпки собирай своей схемой. Основной способ, по образцу
  `tests/test_scope_schema.py` (строки 43–49): `sqlite3.connect` плюс `executescript` схемы, где
  таблица `events` без `transition`; остальные таблицы берутся из `db.SCHEMA` с этой одной
  заменой. `ALTER TABLE events DROP COLUMN transition` на инициализированной базе допустим как
  запасной путь: нужен SQLite не ниже 3.35, на машине автора 3.53.
- В семи файлах со старым номером схемы (список в «Сверено с кодом») меняются только эти
  утверждения: `13` → `14` и `"13"` → `"14"`. Больше в этих файлах ничего не правится.

## Критичные инварианты

- **Держатель на переходе ведёт себя как раньше.** Без `holder` handoff снимает держателя
  (событие `release`), sticky и sticky-return держателя сохраняют. Меняется только то, что пишется
  в событие. Сценарий нарушения: при дефолтном config задача на `s1-spec` с держателем `agent:x`
  после `next_stage` без `holder` остаётся с держателем. Или: у проекта переопределено
  `s1-spec:s2-review = sticky`, а держатель после `next_stage` снят.
- **Досыпка необратимо меняет данные и должна быть точной.** Она трогает только события
  `kind='stage'`, у которых заметка в точности совпадает с форматом по умолчанию для их
  `to_value`. Сценарии нарушения:
  - событие `stage` с пользовательской заметкой `перешёл (sticky) к делу` получило `transition`;
  - событие `note` с текстом `этап -> s2-review (sticky)` получило `transition`;
  - событие `stage` в `done` с заметкой `этап -> done (handoff)` получило `transition`;
  - повторный `init` на уже мигрированной базе изменил хоть одну строку `events`.
- **`transition` нельзя записать извне.** Сценарии нарушения:
  - `PATCH /api/tasks/{id}` с `{"transition": "sticky"}` не отвечает `400 bad_argument` или
    меняет карточку;
  - `POST /api/tasks/{id}/stage` с `{"transition": "sticky"}` на задаче `s1-spec` при
    дефолтном config пишет событию что-то кроме `handoff`.

## Границы правки

Меняются только:
- `listik/db.py`, `listik/store.py`, `listik/server.py` (только ветка `/api/meta`),
  `listik/client.py` (только ветка `meta` в `local_call`);
- новый `alembic/versions/0012_event_transition.py`;
- `docs/API.md` (только места из п. 7);
- новый `tests/test_stage_transition_event.py`;
- строки с номером схемы в семи тестовых файлах из п. 8.

Запрещено:
- трогать `web/` — это порция b;
- менять `config.transition_kind`, `config.validate_routing`, `config.DEFAULTS` и правила
  снятия держателя в `next_stage`;
- менять формат заметки по умолчанию `этап -> X (kind)` и заметку закрытия;
- добавлять `transition` в `UPDATABLE`, в тело `POST /stage`, в аргументы MCP или CLI;
- менять вывод CLI (`listik show`, `listik timeline`), `store.task_timeline`, `/api/timeline`;
- править существующие тесты сверх строк с номером схемы, глушить или пропускать тесты;
- гонять полный `python3 -m unittest discover`, ни целиком, ни «для верности».

## Как проверить

Команды — в разделе «Проверки порции» чек-листа `listik-cvm8.check-a.md`. Коротко:
- узкий прогон затронутых тестовых модулей;
- `alembic upgrade head` на временной базе и `--sql` для диапазона `0011:0012`;
- `git diff --name-only`.

Требование к среде: CLI `alembic` должен быть установлен у исполнителя и у судьи. На машине
автора он лежит в `/opt/homebrew/bin/alembic`. Если `which alembic` пуст, это остановка с
отчётом, а не пропуск проверки. Тест файла ревизии в `unittest` при этом по-прежнему делает
`skipTest` без alembic, по образцу `tests/test_orchestrator.py`.
