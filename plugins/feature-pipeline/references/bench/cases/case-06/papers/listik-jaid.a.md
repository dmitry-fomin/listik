Рабочее дерево: {{TREE}}

# listik-jaid, порция a — done и release через store с проверкой владельца во всех путях

Весь код пиши в этом дереве; основное дерево репозитория не трогать.
Бэкенд — чистый stdlib Python, сборки нет. Перед правкой прочитай в docs/API.md вступление
«Серверный режим (владелец-человек)» (строки ~15-30) и строки таблицы действий карточки
`/stage`, `/release`, `/done` (~1553-1559).

## Контекст: как устроено сейчас

**Серверный режим.** В `config.toml` `[server] mode = "server"` и `users = [...]`. У задачи есть
колонка `owner`. Идентичность вызова в store — аргумент `as_owner` (не путать с полем `owner`):

- HTTP — только заголовок `X-Listik-Owner` (переменная `owner` в `server.handle`);
- MCP по HTTP (`POST /mcp`) — тот же заголовок; MCP stdio — `LISTIK_OWNER`
  (`mcp.call_tool(..., owner=FROM_ENV)`);
- CLI — `--owner` > `LISTIK_OWNER` > `[auth] owner`. `bin/listik: call()` кладёт его в
  `local_kwargs["as_owner"]` только для op из `OWNER_OPS` (bin/listik:199), а
  `client.local_call` выбрасывает `as_owner` у op вне `client.OWNER_LOCAL_OPS` (listik/client.py:67).

В локальном режиме владелец игнорируется целиком: ни проверок, ни записи.

**Правило PATCH** (`store.update_task`, listik/store.py:729-749): в серверном режиме
`config.check_owner(as_owner)` (имя не из `users` → `errors.BadArgument`, HTTP 400); если
представившийся не совпал с непустым `owner` задачи и в полях есть что-то кроме `owner` —
`errors.Forbidden` («задача … принадлежит <владелец>: чужую задачу править нельзя») до любой
записи. Без представления (`as_owner` пуст) — проходит. `errors.Forbidden` → HTTP 403 `forbidden`
(`server.error_response`), в CLI — ошибка с кодом `forbidden` и подсказкой с `owner=`, в MCP —
`isError: true`.

**Что сломано.**

- `listik/server.py:1323-1331` — `/release` и `/done` зовут `store.update_task` без `as_owner`:
  в серверном режиме любой закрывает и освобождает чужую карточку. `body.harness` не передаётся.
  Здесь же своя копия правил «заметка `release` по умолчанию — `освободил`» и
  «`close_reason` = `reason` или `result`».
- `listik/mcp.py:659-666` — `listik_done`/`listik_release`: то же, без `as_owner`, свои копии правил.
- `bin/listik:1169-1187` — `cmd_release`/`cmd_done` вызывают `call("update", …)`: по HTTP идут на
  `/release`/`/done`, а локально эмулируют их op `update` с `as_owner` — одна и та же команда по
  HTTP проходит, а локально получает Forbidden. У `done` в `local_kwargs` нет `note` и `harness`
  (заметка без сервера теряется), у `release` нет `harness`. Свои копии правил.
  `bin/listik:1796-1801` — `_SwarmCards.release` тоже `call("update", …)` с `holder=""`.
- `listik/store.py:1283-1318` `next_stage`: владелец проверяется только при непустом `holder`
  (`check_task_owner`); ветка перехода в `done` (~1313-1317) зовёт
  `update_task(stage="done", status="done", holder="")` без `as_owner` — `stage --to done` закрывает
  чужую задачу в обход будущего 403 у `/done`.
- `listik/client.py:177` `FENCED_LOCAL_OPS` (op → ключ id для `fence.guard`), `:186-207` начало
  `local_call` (ограждение, отбрасывание `as_owner`), `:246` op `update`.
- `bin/listik:206` `WRITE_OPS` — пишущие op: для них `--local` при живом сервере печатает в stderr
  предупреждение «доска не получит событие».

**Что уже работает и в этой порции не меняется** (сверено с кодом, коммит `HEAD`):

- `as_owner` уже доходит до `next_stage` по всем путям: HTTP `/stage`
  (listik/server.py:1292-1295, `as_owner=owner`), MCP `listik_stage` (listik/mcp.py:636-643,
  `as_owner=owner`), local — op `stage` есть и в `OWNER_OPS`, и в `OWNER_LOCAL_OPS`. Поэтому для
  проверки владельца при закрытии через `stage` достаточно правки store (требование 2); обработчики
  `stage` в server.py, mcp.py, client.py, bin/listik не трогать.
- `bin/listik: call(op, args, path, *, query, body, method, local_kwargs, …)`: HTTP-запрос уходит
  по явным `method` и `path`; `op` только решает членство в `OWNER_OPS` (класть ли `as_owner`),
  в `WRITE_OPS` (предупреждение `--local`) и выбирает операцию `client.local_call`. Замена
  `call("update", args, "/api/tasks/{id}/done", …)` на `call("done", …)` с тем же `path`
  HTTP-маршрут не меняет.
- В `update_task` проверка владельца (listik/store.py:743-749) стоит до разбора изменений и до
  раннего возврата «ничего не изменилось» (`unchanged`): отказ чужому получается и на no-op —
  `release` задачи без держателя, `done` уже закрытой.
- У `/done` (server.py), `listik_done` (mcp.py), `cmd_done` (bin/listik) своей заметки по умолчанию
  нет — `note` уходит как есть; умолчание `освободил` есть только у release.
- Схемы MCP (listik/mcp.py): `listik_done` принимает `id`, `result`, `reason`, `note`, `actor`;
  `listik_release` — `id`, `actor`, `note`. Их хватает, схемы не меняются.
- `_SwarmCards.release(task_id, note)` вызывается из listik/swarm_watch.py:259 с
  `note="рой: держатель снят при заморозке"`.
- AST-тест `tests/test_board_events.py::WriteToolsCoverage.test_every_mutating_tool_is_registered`
  считает MCP-инструмент пишущим, если в его ветке `call_tool` есть вызов, чьё имя (атрибут, как у
  `store.close_task`) входит в `MUTATING_CALLS`.

## Требования

1. **store: две функции** в listik/store.py.
   - `close_task(conn, task_id, *, actor=None, harness=None, note=None, result=None, reason=None,
     as_owner=None) -> dict` — закрыть задачу: `status=done`, `stage=done`, держатель снимается
     (событие `release` пишет сам `update_task`, как сейчас у `/done`). `result` записывается,
     только если не `None`. `close_reason` — `reason`, если он непустой, иначе `result`, если он
     непустой, иначе поле не трогается. `note` уходит в события как есть, своего умолчания нет.
   - `release_task(conn, task_id, *, actor=None, harness=None, note=None, as_owner=None) -> dict` —
     снять держателя (`holder=""`); заметка события — `note`, а пустая или `None` — `"освободил"`.
   - Обе делают запись через `update_task(..., as_owner=as_owner)`: проверка владельца — ровно
     правило PATCH (см. выше), отказ — до любой записи. Обе возвращают то же, что `update_task`
     (карточку; при no-op — с `unchanged: True`).
   - Правила «`освободил` по умолчанию» и «`close_reason` = `reason` или `result`» после порции
     записаны только в этих функциях.
2. **`next_stage`**: ветка перехода в `done` (явный `to_stage="done"` и неявный после `s4-judge`)
   зовёт `close_task` с `as_owner`; заметка по умолчанию прежняя —
   `f"этап -> done (закрыта из {cur or '—'})"`. Проверка `check_task_owner` при непустом `holder`
   остаётся как есть. Переходы не в `done` без `holder` владельца не смотрят — не менять.
   Ветка «`to` совпал с текущим этапом» (`stage_unchanged`) не меняется.
3. **server.py**: `/release` → `store.release_task(conn, tid, actor=…, note=…, harness=…,
   as_owner=owner)`; `/done` → `store.close_task(conn, tid, actor=…, note=…, harness=…,
   result=body.get("result"), reason=body.get("reason"), as_owner=owner)`. Ограждение (`_guard_op`),
   `publish`, разбор ошибок — не менять.
4. **mcp.py**: `listik_done` → `store.close_task(…, as_owner=owner)` с `actor`, `note`, `result`,
   `reason` из аргументов; `listik_release` → `store.release_task(…, as_owner=owner)` с `actor`,
   `note`. Схемы и описания инструментов не менять.
5. **client.py**: новые op `done` и `release` в `local_call` → `store.close_task` /
   `store.release_task` (`task_id` из kwargs, остальное — именованными аргументами). Оба op — в
   `OWNER_LOCAL_OPS` и в `FENCED_LOCAL_OPS` с ключом `task_id`. Op `update` остаётся как есть.
6. **bin/listik**:
   - `cmd_done` → `call("done", …)`; тело HTTP — как сейчас; `local_kwargs`: `task_id`, `actor`,
     `note`, `harness`, `result`, `reason` — сырые значения аргументов, без своих умолчаний.
   - `cmd_release` → `call("release", …)`; тело HTTP и `local_kwargs`: `actor`, `note`, `harness`
     (+ `task_id` локально); ни `holder`, ни `"освободил"` в CLI не остаётся.
   - `_SwarmCards.release` → `call("release", …)` через тот же `_guard`; `actor=self.ACTOR`,
     `note` — аргумент метода как есть (от swarm_watch приходит «рой: держатель снят при
     заморозке», он и должен оказаться в событии `release`; умолчание `освободил` срабатывает
     только на пустой заметке).
   - `done`, `release` — в `OWNER_OPS` и `WRITE_OPS`.
   - Вывод команд (`emit`, тексты «закрыта …», «освобождена: …») не меняется.
7. **Документация**:
   - docs/API.md, строка `POST /api/tasks/{id}/release` (~1556): в колонку «Тело» добавить
     `harness`; в «Смысл» — `note` по умолчанию «освободил»; в серверном режиме чужая задача
     (заголовок `X-Listik-Owner` не совпал с владельцем) — 403 `forbidden`, карточка не меняется;
     имя не из `server.users` — 400 `bad_argument`; без заголовка — проходит, как `PATCH`.
   - строка `POST /api/tasks/{id}/done` (~1559): `harness` в теле; `close_reason` — `reason`, иначе
     `result`; тот же абзац про владельца.
   - строка `POST /api/tasks/{id}/stage` (~1553): фразу «Владелец в серверном режиме проверяется
     **только при переданном `holder`** …» дополнить: и при переходе в `done` — как у `/done`
     (403 на чужой, 400 на имя не из списка, без заголовка проходит); прочие переходы без `holder`
     заголовок не смотрят.
   - вступление (~строка 23): «чужую задачу взять или править нельзя» — дописать, что закрыть и
     освободить (`/done`, `/release`, `stage` в `done`) тоже нельзя.
   - docs/usage.md:50: «`claim`/`heartbeat` чужой задачи отказывают» — добавить `done`, `release`
     и `stage --to done`.
8. **Тесты** — новый файл `tests/test_done_release.py`, все сценарии чек-листа
   `listik-jaid.check-a.md`: store, HTTP, MCP (`POST /mcp` с заголовком и stdio `mcp.call_tool`
   с `LISTIK_OWNER`), CLI `--local` и CLI через живой сервер. Готовые фикстуры (сверено: у трёх
   базовых классов нет ни одного `test_*`-метода, загрузчик unittest собирает из них 0 тестов,
   поэтому импорт в новый файл ничего не запускает повторно):
   - `OwnerStoreCase` (tests/test_owner_store.py) — база и подмена `paths.CONFIG_PATH`,
     `server_mode()`/`local_mode()`, `snapshot(task_id)`, `make(...)`;
   - `OwnerHttpCase` (tests/test_owner_http.py) — живой сервер в процессе с `SERVER_CONFIG`
     (users `ann`, `bob`, токен `test-token`), `api(method, path, owner=, body=)`, `make_task(header)`,
     `get_task`, `self.port`, `self.db_path`, конфиг — `paths.CONFIG_PATH`;
   - `OwnerCliCase` и константы `LOCAL_CONFIG`, `SERVER_CONFIG` (tests/test_owner_cli.py) —
     `bin/listik --local` подпроцессом, `run_cli(*args, config=None, **env)`, `json_out`,
     `error_json`, `new_task`;
   - образец `client.local_call` с токеном ограждения — `LocalFallbackTests` (tests/test_fencing.py);
   - образец CLI против живого сервера — `run_cli` в tests/test_local_bypass_warning.py: подпроцесс
     `bin/listik --port <порт сервера> …` **без** `--local`, с `LISTIK_CONFIG` и `LISTIK_DB` тех же
     файлов, что у сервера. Для сценариев CLI по HTTP в серверном режиме — подкласс
     `OwnerHttpCase` и такой подпроцесс (`LISTIK_CONFIG=paths.CONFIG_PATH`,
     `LISTIK_DB=self.db_path`, `--port self.port`, `LISTIK_OWNER` убрать из окружения).
   Импортируй **только** эти базовые классы и константы — импорт классов с тестами запустил бы их
   тесты второй раз.
   - tests/test_board_events.py: в `MUTATING_CALLS` добавить `close_task` и `release_task` — иначе
     AST-тест перестанет считать `listik_done`/`listik_release` пишущими.
   - tests/test_local_bypass_warning.py: можно добавить случаи `("done", task)` и
     `("release", task)` в `test_write_ops_warn_not_only_create`.

## Критичные инварианты

- Представившийся не владелец (`bob`) не закрывает и не освобождает задачу `ann` ни одним путём:
  HTTP `/done`, `/release`, `/stage` с `to=done` и `/stage` без `to` на `s4-judge`; MCP
  `listik_done`, `listik_release` (по `/mcp` и stdio); CLI `done`, `release`, `stage --to done`
  (`--local` и через живой сервер). Отказ `forbidden` — и когда запись была бы no-op (release
  задачи без держателя, done уже закрытой); `status`, `stage`, `holder`, `closed_at`,
  `close_reason`, `result`, `updated_at` и число событий задачи не меняются.
- Идентичность не берётся из тела и аргументов: `/done` с заголовком `bob` и телом
  `{"owner": "ann"}` или `{"as_owner": "ann"}` — 403; MCP `listik_done` от `bob` с
  `{"owner": "ann"}` в аргументах — `isError`.
- Локальный режим не отказывает никогда: задача с `owner='ann'` (прямым SQL) закрывается и
  освобождается при `as_owner="bob"`.
- Переходы этапа не в `done` без `holder` владельца не проверяют: `bob` переводит задачу `ann`
  `s1-spec → s2-review`.

## Границы правки

Меняются только: `listik/store.py`, `listik/server.py`, `listik/mcp.py`, `listik/client.py`,
`bin/listik`, `docs/API.md`, `docs/usage.md`, `tests/test_done_release.py` (новый),
`tests/test_board_events.py` (только `MUTATING_CALLS`), `tests/test_local_bypass_warning.py`
(только новые случаи).

Нельзя:

- менять семантику `update_task`, `check_task_owner`, `config.check_owner`, `errors.py`,
  `server.error_response`;
- трогать обработчики `stage` (server.py, mcp.py, `client.local_call`, `cmd_stage`) — `as_owner`
  они уже передают;
- трогать ограждение сверх добавления `done`/`release` в `FENCED_LOCAL_OPS`: `_guard_op` в
  server.py, `FENCED_TOOLS` в mcp.py и `listik/fence.py` — это порция b;
- менять схемы и описания MCP-инструментов, `web/`, `swarm/`, `routes.json`, alembic, `db.SCHEMA`;
- переводить на `close_task` внутренние закрытия (`listik/stage_launch.py`, автозакрытие эпика в
  store) — они без представления и не про этот баг;
- менять или ослаблять существующие тесты, кроме двух названных правок; удалять `OWNER_OPS`
  (это listik-pf6i); трогать строку docs/API.md про ограждение `listik_deps` (~782, это listik-9s7e);
- брать владельца из тела запроса или аргументов инструмента;
- расширять порцию «заодно» (harness у `new`/`set`, автор `listik_comment`, ветки `listik_stage` —
  отдельные карточки).

## Как проверить

См. `listik-jaid.check-a.md`, раздел «Проверки порции». Полный `discover` не гонять.
