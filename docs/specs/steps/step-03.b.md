# Порция 03.b. `--update` с журналом diff, отчёт для CI, вывод CLI, подпись источника в `show`

## Контекст

Listik — трекер задач на stdlib-Python, одна база SQLite; CLI `bin/listik` (argparse, общие флаги
`--json`, `--local` навешиваются на каждую подкоманду через `common`). Порция a переписала
`listik/import_writerllm.py`: `import_file(conn, path, *, project=None, dry_run=False,
update=False) -> dict` читает JSON/JSONL из `bd export`/`bd list --json`, создаёт задачи со
старыми ID, датами, актором, комментариями и связями, пропускает уже импортированные по ключу
`(source='writerllm', project, external_ref)`, возвращает отчёт с ключами `source, project,
dry_run, update, total, created, updated, skipped, ignored, dependencies, comments, errors[],
warnings[], examples{create,update,skip,error}`. Флаг `update` пока ведёт себя как skip.
Тесты — `tests/test_import_writerllm.py`, фикстуры — `tests/fixtures/writerllm/`.

**Предусловие: порция a закоммичена.** Проверка: `python3 -m unittest tests.test_import_writerllm`
зелёный и `grep -n "_markdown_record" listik/import_writerllm.py` пуст. Иначе порцию не
начинать и вернуть задачу оркестратору.

Требования спеки, которые закрывает эта порция: «при повторном импорте существующая карточка не
перезаписывается молча: default `skip`, явный `--update` создаёт diff/журнал»; «итоговый отчёт
пригоден для CI и содержит ненормализованные записи»; «показывает количество
create/update/skip/error и первые примеры»; «импорт не стирает уже существующий ручной
комментарий».

`store.update_task(conn, id, *, actor, harness, note, **fields)` меняет только поля из
`store.UPDATABLE`, пропускает `None`, сравнивает строково, пишет события для `status`/`stage`/
`holder`/`assignee`, при `status` → `done` ставит `closed_at=now`, если он пуст; возвращает
`{"unchanged": True, …}`, если менять нечего. `store.add_comment(conn, id, text, author=, kind=,
created_at=)` пишет комментарий и событие `comment`. `bin/listik show` (`show_task`) печатает
`было в beads: <external_ref>` для любого источника.

Перед началом прочитай: `listik/import_writerllm.py` в редакции порции a (коммит 4fce07a):
`import_file` (ветка `if existing:` — сейчас любая существующая запись безусловно уходит в
`skipped`, флаг `update` только кладётся в отчёт), `_build_record`, `_first_nonempty`,
`_map_status`/`_map_priority`, `_write_comments`/`_count_new_comments`, `_push_error`,
`_add_example`; `store.update_task`, `store.UPDATABLE`, `store.add_comment`; `bin/listik` —
`cmd_import_writerllm`, `cmd_import` (образец текстового вывода), `show_task`, блок
`add("import-writerllm", …)` и `common`-флаги; `tests/test_import_writerllm.py` и фикстуры
`tests/fixtures/writerllm/` (`export.jsonl` — записи `WL-a1`, `WL-a2`, `WL-a3`, `WL-a4`, `WL-a5`,
`WL-a1.1`, `WL-a9`, память `WL-mem-1`; `WL-a1.1` ссылается на несуществующую `WL-missing`).

**`export.jsonl` — «грязная» фикстура по построению.** Связь `WL-a1.1 → WL-missing` даёт ошибку
`where="dependency"`, `error="target task not found"` на каждом прогоне, включая повторный и
`--dry-run` (это закреплено тестом порции a, `test_4_…`). Поэтому на `export.jsonl` `ok` всегда
`False`, а код возврата — всегда `1`, ровно с одной ошибкой. Сценарии «без ошибок → `ok=True`,
код `0`» проверяются **только** на новой чистой фикстуре `export-v2.jsonl` (§5). Тест порции a на
эту ошибку не трогать и «target task not found» в предупреждение не переводить.

## Что сделать

### 1. `--update` в `import_file`

Для записи, у которой задача по ключу идемпотентности уже есть:

- `update=False` — как в порции a: `skipped`; комментарии и связи, которых ещё нет, всё равно
  дописываются (это не «перезапись», а дозаполнение — ручные комментарии не трогаются).
- `update=True` — сравнить **только** поля содержания `title, description, acceptance, design,
  notes, result, status, priority, issue_type, assignee, labels, close_reason`, и только те, для
  которых в сырой записи есть хотя бы один исходный ключ (PATCH: отсутствующий в выгрузке ключ
  не стирает значение в Listik; `labels` сравниваются как списки, `priority` как целые, остальное
  строково после нормализации порции a). Если различий нет — `skipped`, никаких записей. Если
  есть — `store.update_task(conn, id, actor=<ключ актора из created_by записи>, note="импорт
  WriterLLM --update", **изменённые_поля)`, затем `store.add_comment(conn, id, text,
  author=<тот же актор>, kind="journal")`, где `text` начинается с фиксированного префикса
  `[import-writerllm] update:` и дальше по строке на поле: `<поле>: <было> → <стало>` (длинные
  значения обрезать до 200 символов с `…`). Это `updated`; в `examples["update"]` — `{id,
  external_ref, title, fields: [имена изменённых полей]}`.
- Импортёр **не передаёт** в `update_task` ничего, кроме изменённых полей содержания: даты
  `created_at/started_at/closed_at`, `holder*`, `external_ref`, `project`, `source`,
  `spec_path/journal_path/checklist_path` в diff не входят и напрямую не пишутся. При этом
  `store.update_task` при смене `status` сам выставляет даты (store.py, ветка `elif key ==
  "status"`): `in_progress` при пустом `started_at` → `started_at = now`; финальный статус при
  пустом `closed_at` → `closed_at = now` (для `done` ещё и `started_at = now`, если пуст); уход из
  финального статуса → `closed_at = NULL` и `close_reason = NULL`. Это **норма, не дефект**:
  импортёр эти побочные изменения не откатывает, в журнальный diff не пишет (там только поля, что
  передал сам) и никак не обходит. У записи, чей статус при `--update` не менялся, `started_at`,
  `closed_at`, `close_reason` остаются прежними. Ручные комментарии (любые, не совпадающие по
  `id` или `(text, created_at)` с комментариями выгрузки) не удаляются и не изменяются ни в одном
  режиме.
- `dry_run=True, update=True` — `updated` считается по тому же сравнению, но ни `update_task`,
  ни `add_comment` не вызываются.
- Повторный `--update` без изменений в выгрузке: `updated=0`, второго журнального комментария
  нет.

### 2. Отчёт и код возврата

- `errors[*].raw` — сырая запись или сырая строка целиком (это и есть «ненормализованные
  записи»); в `examples["error"]` — `{external_ref, where, error}`. `warnings` остаются как в а.
- `import_file` дополнительно возвращает `"ok": len(errors) == 0`.
- CLI (`cmd_import_writerllm`): код возврата `0`, если `ok`, иначе `1` — и при `--dry-run` тоже.
  Исключение уровня всего запуска (нечитаемый источник) — тоже через отчёт (`where="source"`),
  не traceback.

### 3. Вывод CLI

`--json` — весь отчёт `json.dumps(report, ensure_ascii=False, indent=2)` и больше ничего в stdout.

Без `--json` — на русском, по образцу `cmd_import`:

```text
WriterLLM → проект writerllm (источник: <путь>)
записей: 9   создано: 7   обновлено: 0   пропущено: 0   игнорировано: 1   связей: 5   комментариев: 2   ошибок: 1
  create  WL-a1  «Заголовок…»
  create  WL-a2  «…»  (id: writerllm-x9k2 — старый занят)
  skip    WL-a3  «…»
  update  WL-a4  «…»  (title, status)
  ! строка 4: invalid JSON: Expecting value
  ! запись WL-a7, связь → WL-zzz: target task not found
(dry-run: ничего не записано)
```

Примеры — из `examples` (по действию, не более 5 на действие), ошибки — первые 10 из `errors`
(`where`, `line`/`external_ref`, `target_ref`, `error`; **без** `raw` — сырые записи только в
`--json`). Строка `(dry-run: ничего не записано)` — только при `--dry-run`. Предупреждения —
одной строкой `предупреждений: N (подробности в --json)`, если есть.

Аргументы подкоманды: `--source` (обязательный), `--project` (по умолчанию `writerllm`, справка
говорит это), `--dry-run`, `--update` (справка: «обновлять поля уже импортированных задач и писать
diff в журнал»). `--local` для этой команды не нужен: она всегда работает с базой напрямую
(`db.init()`), как `import-beads`; это отметить в `help` команды одной фразой.

### 4. `show`: подпись источника

В `show_task` строку `было в beads: …` заменить на `старый ID (<source>): <external_ref>`,
где `<source>` — `t["source"]` (`beads`, `writerllm`, …). Печатать, как и раньше, только при
непустом `external_ref`. Другие строки `show_task` не менять.

### 5. Тесты — дополнить `tests/test_import_writerllm.py`

Новая фикстура `tests/fixtures/writerllm/export-v2.jsonl` (обязательна), синтетическая, без
имён и e-mail, **чистая** — ни одной висячей связи и ни одной записи-памяти, чтобы на ней
достигались `ok=True` и код `0`. Состав — те же `issue`-записи, что в `export.jsonl`, с такими
отличиями:

- `WL-a2` — другой `title`, статус прежний (`open`) — запись «изменение без смены статуса»;
- `WL-a5` — `status` изменён `open → in_progress`, остальное прежнее — запись «смена статуса»;
- `WL-a3` — добавлен ещё один комментарий (с `id` и `created_at`);
- `WL-a1.1` — связь только `parent-child → WL-a1`, ссылки на `WL-missing` **нет**;
- `WL-a4`, `WL-a1`, `WL-a9` — без изменений; записи `WL-mem-1` нет.

`export-v2.jsonl` на **пустой** базе даёт `ok=True`, ошибок 0, код `0`; поверх базы с
`export.jsonl` — тоже без ошибок (второй проход по связям находит все цели).

1. Ручной комментарий цел: импорт `export.jsonl` → `store.add_comment(conn, "WL-a1", "ручной",
   author="me")` → импорт `export.jsonl` без флага и с `--update`: комментарий «ручной» на месте,
   комментариев выгрузки не стало больше.
2. Без `--update` изменённая выгрузка (`export-v2.jsonl`) не меняет `title/status` в базе
   (`skipped`), но новый комментарий дописывается (`report["comments"]=1`).
3. С `--update` (`export.jsonl`, затем `export-v2.jsonl --update`): `updated=2` (`WL-a2` и
   `WL-a5`); у `WL-a2` новый `title`, а `started_at`, `closed_at`, `close_reason`, `created_at`,
   `holder` прежние (статус не менялся); у `WL-a5` `status='in_progress'`, `started_at` стал
   непустым (его выставил `update_task`, это ожидаемо), `closed_at` по-прежнему пуст, `created_at`
   прежний; у обеих появился комментарий `kind='journal'`, начинающийся с
   `[import-writerllm] update:` — у `WL-a2` в нём только `title: … → …`, у `WL-a5` только
   `status: open → in_progress` (даты в журнале не упоминаются); `examples["update"]` содержит
   обе записи с `fields` `["title"]` и `["status"]`; `WL-a4` — `skipped`, журнала нет;
   `report["ok"]` — `True`, `errors` пуст.
4. Повторный `--update` той же `export-v2.jsonl`: `updated=0`, `ok=True`, второго журнального
   комментария ни у `WL-a2`, ни у `WL-a5` нет.
5. `--dry-run --update` (`export.jsonl`, затем `export-v2.jsonl --dry-run --update`): `updated=2`
   в отчёте, в базе `title` `WL-a2`, `status`/`started_at` `WL-a5` и число комментариев прежние.
6. `report["ok"]`: `True` на `export-v2.jsonl` (пустая база, `errors == []`); `False` на
   `export.jsonl` — ровно одна ошибка, `where="dependency"`, `target_ref="WL-missing"`; `False` на
   `broken.jsonl`; каждый элемент `errors` имеет ключи `where`, `error`, `raw`.
7. CLI через `subprocess` с `env={"LISTIK_DB": <временный файл>}` (иначе команда пишет в
   `listik.db` репозитория): `import-writerllm --source broken.jsonl --dry-run` → код `1`, в
   stdout есть `ошибок:` и строка, начинающаяся с `  ! `; `--json` → stdout разбирается
   `json.loads`, ключи `created/updated/skipped/errors/ok` есть; `export.jsonl` без `--dry-run` →
   код `1` (одна известная ошибка `dependency`, задачи при этом созданы); `export-v2.jsonl` на
   **отдельной пустой** базе → код `0`; затем `show WL-a1 --local` на любой из баз печатает
   `старый ID (writerllm): WL-a1`.

## Границы правки

- Правятся: `listik/import_writerllm.py`, `bin/listik` (только `cmd_import_writerllm`, блок
  `add("import-writerllm", …)` и одна строка в `show_task`), `tests/test_import_writerllm.py`,
  новая фикстура `tests/fixtures/writerllm/export-v2.jsonl`. Фикстуры порции a (`export.jsonl`,
  `export.json`, `broken.jsonl`) не менять — в том числе не «чинить» связь на `WL-missing`.
- `store.py`, `deps.py`, `actors.py`, `db.py`, `import_beads.py`, `server.py`, `mcp.py`,
  `client.py`, `alembic/`, `web/` не трогать. HTTP/MCP-обёртку для импорта не заводить.
- Семантику `update_task`/`add_comment` не менять и не обходить «чтобы diff был красивее»:
  побочные события (`status`, `assignee`, `comment`) от них — норма.
- `--update` не расширять до синхронизации: ничего не удалять (ни комментарии, ни связи, ни
  задачи, отсутствующие в новой выгрузке).
- Другие строки вывода `show`, `cmd_import` (beads) и общие флаги CLI не менять.
- Тесты порции a не переписывать под новое поведение — только дополнять; если тест порции a
  противоречит требованию b, это строка в отчёте исполнителя, а не правка теста.
- Реальную выгрузку, `~/Agents/WriterLLM`, `listik.db` репозитория не использовать; в фикстурах
  и диффе — ни имён, ни e-mail, ни токенов.

## Как проверить

Каждая команда `./bin/listik` — с явным `LISTIK_DB=…` в той же строке (без `export`): без
переменной команда пишет в `listik.db` репозитория.

```sh
python3 -m unittest discover tests
DB=/tmp/listik-step03b.db; rm -f "$DB" "$DB-wal" "$DB-shm"; LISTIK_DB=$DB ./bin/listik --local init
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl; echo $?   # 1: одна известная ошибка dependency (WL-missing), задачи созданы
LISTIK_DB=$DB ./bin/listik --local comment WL-a1 "ручной" -k comment
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export-v2.jsonl; echo $?            # 0; skip, но +1 комментарий у WL-a3
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export-v2.jsonl --update; echo $?   # 0; обновлено: 2 (WL-a2 title, WL-a5 status)
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export-v2.jsonl --update; echo $?   # 0; обновлено: 0
LISTIK_DB=$DB ./bin/listik --local show WL-a1            # «ручной» на месте, «старый ID (writerllm): WL-a1»
LISTIK_DB=$DB ./bin/listik --local show WL-a2            # новый title, журнал [import-writerllm] update: title …; started_at/closed_at пустые
LISTIK_DB=$DB ./bin/listik --local show WL-a5            # статус «в работе», «начата: …» (выставил update_task), журнал только про status
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/broken.jsonl --dry-run; echo $?   # 1
LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/broken.jsonl --dry-run --json | python3 -m json.tool >/dev/null
DB2=/tmp/listik-step03b-clean.db; rm -f "$DB2" "$DB2-wal" "$DB2-shm"; LISTIK_DB=$DB2 ./bin/listik --local init
LISTIK_DB=$DB2 ./bin/listik import-writerllm --source tests/fixtures/writerllm/export-v2.jsonl; echo $?          # 0: чистая фикстура на пустой базе
git diff --stat HEAD -- . ':!docs/specs'   # только import_writerllm.py, bin/listik, тесты, export-v2.jsonl
```
