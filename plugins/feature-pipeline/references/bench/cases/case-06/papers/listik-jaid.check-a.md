Рабочее дерево: {{TREE}}

# Чек-лист приёмки listik-jaid, порция a

Серверный режим в пунктах ниже — `[server] mode = "server"`, `users = ["ann", "bob"]`; задача
заведена от `ann` (`owner = "ann"`). «Не изменилась» — в строке `tasks` не изменились `status`,
`stage`, `holder`, `closed_at`, `close_reason`, `result`, `updated_at`, и число событий задачи
то же. Все сценарии закреплены тестами в `tests/test_done_release.py`; до правки тесты на отказ
чужому по HTTP, MCP и CLI через живой сервер, на закрытие чужой через `stage` и на заметку `done`
без сервера падают, после — проходят.

## store

- [ ] `store.close_task(conn, id, as_owner="bob")` → `errors.Forbidden`, в тексте `ann`; задача не изменилась.
- [ ] `store.release_task(conn, id, as_owner="bob")` на задаче с держателем → `errors.Forbidden`; держатель на месте, задача не изменилась.
- [ ] No-op тоже отказ: `release_task(..., as_owner="bob")` на задаче `ann` без держателя → `Forbidden`; `close_task(..., as_owner="bob")` на задаче `ann`, уже закрытой владельцем, → `Forbidden`; в обоих случаях задача не изменилась.
- [ ] `as_owner="ann"`: `close_task` даёт `status=done`, `stage=done`, `closed_at` заполнен, держатель пуст; `release_task` снимает держателя.
- [ ] Без представления (`as_owner=None`) в серверном режиме `close_task` и `release_task` проходят.
- [ ] `as_owner="carol"` (нет в `users`) → `errors.BadArgument`, задача не изменилась.
- [ ] Задачу без владельца (`owner` NULL) `bob` закрывает.
- [ ] `release_task` без `note` пишет событие `release` с заметкой `освободил`; с `note="ухожу"` — `ухожу`.
- [ ] `close_reason`: при `reason="R"`, `result="X"` — `R`; только `result="X"` — `X`; без обоих — `close_reason` остаётся `NULL`.
- [ ] `next_stage(conn, id, to_stage="done", as_owner="bob")` → `Forbidden`, задача не изменилась; то же для задачи на `s4-judge` и `next_stage` без `to_stage`.
- [ ] `next_stage(..., to_stage="done", as_owner="ann")` и `as_owner=None` — закрывают (статус `done`, держатель снят); заметка события по умолчанию прежняя — `этап -> done (закрыта из …)`.
- [ ] Локальный режим: задача с `owner='ann'` (прямым SQL) закрывается `close_task`, освобождается `release_task` и закрывается `next_stage(to_stage="done")` при `as_owner="bob"`.

## HTTP (живой сервер в процессе, заголовок `X-Listik-Owner`)

- [ ] `POST /api/tasks/{id}/done` с заголовком `bob` → 403, `code = "forbidden"`, в `error` есть `ann`; `GET` задачи: не изменилась.
- [ ] `POST /api/tasks/{id}/release` с `bob` на задаче с держателем → 403, держатель на месте.
- [ ] `POST /api/tasks/{id}/stage` с телом `{"to": "done"}` и заголовком `bob` → 403, задача не изменилась.
- [ ] `/done` с заголовком `bob` и телом `{"owner": "ann"}`, затем с `{"as_owner": "ann"}` → оба 403.
- [ ] Заголовок `ann`: `/done` → 200, `status = "done"`; `/release` → 200, держатель пуст.
- [ ] Без заголовка `/done` → 200 (как `PATCH`).
- [ ] Заголовок `carol` → 400, `code = "bad_argument"`.
- [ ] `/done` от `ann` с телом `{"note": "N", "harness": "grok"}` → у события `status` этой задачи `note = "N"`, `harness = "grok"`; `/release` с `{"harness": "grok"}` → у события `release` `harness = "grok"`.
- [ ] `POST /api/tasks/{id}/stage` без `to` и без `holder` от `bob` на задаче `ann` на `s1-spec` → 200, этап `s2-review` (переходы не в `done` не проверяются).

## MCP

- [ ] `POST /mcp`, `tools/call` `listik_done` с заголовком `bob` → `result.isError = true`, в тексте `ann`; задача не изменилась. То же для `listik_release`.
- [ ] `POST /mcp` `listik_done` от `bob` с `{"owner": "ann"}` в аргументах → `isError`.
- [ ] stdio: `mcp.call_tool("listik_done", {"id": …}, conn=…)` при `LISTIK_OWNER=bob` → `errors.Forbidden`, задача не изменилась; при `LISTIK_OWNER=ann` → карточка `status = "done"`.
- [ ] stdio: `mcp.call_tool("listik_release", {"id": …}, conn=…)` на задаче с держателем при `LISTIK_OWNER=bob` → `errors.Forbidden`, держатель на месте; при `LISTIK_OWNER=ann` → держатель пуст.
- [ ] Существующие `tests/test_mcp_tools.py` (заметка `listik_done`, заметка и умолчание `освободил` у `listik_release`) проходят без правок.

## CLI `bin/listik --local` (временные база и конфиг)

- [ ] Серверный конфиг: `--owner bob done <id> --json` → код выхода ≠ 0, `error.code = "forbidden"`, в `error.hint` есть `owner=`; задача не изменилась. То же для `release <id>` и `stage <id> --to done`.
- [ ] `--owner ann done <id> --json` → код 0, `status = "done"`.
- [ ] Локальный конфиг: `done <id> --note "заметка X" --harness grok --json` → в `show <id> --json` у события `status` `note = "заметка X"` и `harness = "grok"` (приёмка карточки).
- [ ] Локальный конфиг: `release <id>` без `--note` → у события `release` заметка `освободил`; с `--note "ухожу" --harness grok` → `ухожу` и `grok`.
- [ ] `client.local_call("done", fence={"task_id": id, "generation": 1, "dispatch_id": "old"}, task_id=id, …)` на задаче с поколением 2 → `errors.Revoked`, одно событие `rejected` с `note.op = "done"`, задача не закрыта. То же для `release` → `note.op = "release"`.
- [ ] `--local done <id>` и `--local release <id>` при живом сервере печатают в stderr «доска не получит событие».

## CLI через живой сервер (без `--local`, `--port` сервера из `OwnerHttpCase`, те же конфиг и база)

- [ ] `--owner bob done <id> --json` → код выхода ≠ 0, `error.code = "forbidden"`; задача не изменилась (запрос ушёл на `/api/tasks/{id}/done`, а не в локальный фолбэк: в stderr нет «сервер Listik не отвечает»).
- [ ] `--owner bob release <id> --json` на задаче с держателем → `forbidden`, держатель на месте.
- [ ] `--owner ann done <id> --json` → код 0, `status = "done"`; `--owner ann release <id> --json` на другой задаче с держателем → код 0, держатель пуст.

## Критичные инварианты

- [ ] Представившийся не владелец не закрывает и не освобождает чужую задачу ни одним путём (store; HTTP `/done`, `/release`, `/stage` в `done` явно и неявно; MCP `listik_done` и `listik_release` по `/mcp` и stdio; CLI `--local` и через живой сервер), в том числе когда запись была бы no-op: отказ `forbidden`, задача не изменилась.
- [ ] Идентичность не берётся из тела или аргументов (`owner`/`as_owner` в теле `/done`, `owner` в аргументах `listik_done`) — отказ остаётся.
- [ ] Локальный режим не отказывает никому.
- [ ] Переходы этапа не в `done` без `holder` владельца не проверяют.

## Код и документация (проверяются чтением изменённого кода и дифа)

- [ ] В `listik/store.py` есть `close_task` и `release_task` с аргументами `actor`, `harness`, `note`, `as_owner` (у `close_task` ещё `result`, `reason`); ветка перехода в `done` в `next_stage` зовёт `close_task` с `as_owner`.
- [ ] server.py (`/done`, `/release`), mcp.py (`listik_done`, `listik_release`), `client.local_call` (op `done`, `release`) зовут эти функции и передают `as_owner`; server передаёт `harness` из тела.
- [ ] Литерал `освободил` и выбор «`reason`, иначе `result`» для закрытия есть только в store: `grep -rn "освободил" listik bin` находит только `listik/store.py`.
- [ ] `bin/listik`: `cmd_done`, `cmd_release`, `_SwarmCards.release` зовут `call("done"|"release", …)` с прежними `path` (`/api/tasks/{id}/done`, `/api/tasks/{id}/release`) и `method="POST"`; эмуляции закрытия/освобождения через `call("update", …)` со `status="done"` или `holder=""` не осталось.
- [ ] `_SwarmCards.release` передаёт в тело и `local_kwargs` заметку-аргумент метода без замены и без своего умолчания (swarm_watch.py:259 шлёт «рой: держатель снят при заморозке» — она и попадает в событие `release`).
- [ ] Обработчики `stage` (server.py, mcp.py, `client.local_call`, `cmd_stage`) не изменены — проверка закрытия через `stage` целиком в store.
- [ ] `done` и `release` есть в `OWNER_OPS` и `WRITE_OPS` (bin/listik), в `OWNER_LOCAL_OPS` и `FENCED_LOCAL_OPS` (client.py).
- [ ] `tests/test_board_events.py`: `MUTATING_CALLS` содержит `close_task`, `release_task`; других правок в существующих тестах нет (кроме новых случаев в `test_write_ops_warn_not_only_create`).
- [ ] docs/API.md: строки `/release` и `/done` — `harness` в теле, 403/400/без заголовка проходит; `/release` — заметка по умолчанию; `/done` — правило `close_reason`; строка `/stage` — проверка владельца при переходе в `done`; вступление — закрыть/освободить чужую нельзя. docs/usage.md:50 — `done`, `release`, `stage --to done`.
- [ ] `git diff --stat` — только файлы из «Границ правки» порции; `_guard_op` (server.py), `FENCED_TOOLS` (mcp.py), `listik/fence.py`, `web/`, `swarm/`, `listik/stage_launch.py` не тронуты.

## Проверки порции

Линтера и тайпчекера для Python в проекте нет. Все модули ниже, кроме нового
`tests.test_done_release`, существуют; до правки они зелёные (191 тест, прогон при написании ТЗ).

```sh
cd {{TREE}}
python3 -m unittest tests.test_done_release tests.test_owner_store tests.test_owner_http \
  tests.test_owner_cli tests.test_done_releases_holder tests.test_stage_done_closes \
  tests.test_stage_same tests.test_mcp_tools tests.test_board_events tests.test_fencing \
  tests.test_local_bypass_warning
```
