# Приёмка порции 03.b. `--update`, отчёт, вывод CLI, подпись в `show`

## Предусловие

0. Порция a закоммичена (4fce07a): `python3 -m unittest tests.test_import_writerllm` зелёный на
   `HEAD`, `grep -n "_markdown_record" listik/import_writerllm.py` пуст. Иначе приёмка не
   проводится.

Все проверки — на временных базах и фикстурах `tests/fixtures/writerllm/`. **Каждая команда
`./bin/listik` — с явным `LISTIK_DB=…` в той же строке**; без переменной команда пишет в
`listik.db` репозитория. Подготовка: `DB=/tmp/listik-step03b.db; rm -f "$DB" "$DB-wal"
"$DB-shm"; LISTIK_DB=$DB ./bin/listik --local init`. Помнить: `export.jsonl` — «грязная»
фикстура (связь `WL-a1.1 → WL-missing`), на ней код возврата всегда `1` с ровно одной ошибкой
`dependency`; чистые сценарии — на `export-v2.jsonl`.

## `--update` и сохранность ручных данных

1. Поведение до порции b — `skip`, не исключение: на `HEAD` (порция a) в `import_file` ветка
   `if existing:` безусловно считает запись в `skipped`, флаг `update` только попадает в отчёт
   (`git show HEAD:listik/import_writerllm.py | grep -n "skipped"`). Судья убеждается, что после
   порции b на той же последовательности (`export.jsonl`, затем `export-v2.jsonl --update`)
   изменённые записи `WL-a2` и `WL-a5` уходят в `updated` (`обновлено: 2`), а не в `skipped`, и
   что на `HEAD` (через `git stash` **не** проверять — достаточно чтения кода порции a) они шли
   бы в `skipped`.
2. Ручной комментарий цел: после `LISTIK_DB=$DB ./bin/listik import-writerllm --source
   tests/fixtures/writerllm/export.jsonl` (код `1`, задачи созданы), `LISTIK_DB=$DB ./bin/listik
   --local comment WL-a1 "ручной" -k comment` и двух повторных импортов `export-v2.jsonl` (без
   флага и с `--update`) `LISTIK_DB=$DB ./bin/listik --local show WL-a1` показывает «ручной», а
   число комментариев выгрузки у `WL-a1` не выросло (два).
3. Без `--update` `export-v2.jsonl` не меняет `title` `WL-a2` и `status` `WL-a5`
   (`sqlite3 $DB "select id,title,status from tasks where id in ('WL-a2','WL-a5')"` прежние;
   отчёт — `skip`), но новый комментарий `WL-a3` дописан (`комментариев: 1`); код возврата `0`.
4. С `--update`: `обновлено: 2`; у `WL-a2` новый `title`; у `WL-a5` `status='in_progress'`;
   у обеих есть комментарий `kind='journal'` с префиксом `[import-writerllm] update:` — у `WL-a2`
   только строка `title: … → …`, у `WL-a5` только `status: open → in_progress`; `created_at`
   обеих прежний; `WL-a4` в `skip`, журнала нет; `examples.update` в `--json` содержит обе
   записи с `fields` `["title"]` и `["status"]`; код возврата `0`.
5. Повторный `--update` той же выгрузки: `обновлено: 0`, `select count(*) from comments where
   text like '[import-writerllm] update:%'` = `2` (по одному на задачу, второго нет).
6. `--dry-run --update`: на отдельной базе `DB3=/tmp/listik-step03b-dry.db` (`init`, затем импорт
   `export.jsonl`) выполнить `LISTIK_DB=$DB3 ./bin/listik import-writerllm --source
   tests/fixtures/writerllm/export-v2.jsonl --dry-run --update`: отчёт показывает
   `обновлено: 2`, а `select id,title,status,started_at from tasks where id in ('WL-a2','WL-a5')`
   и `select count(*) from comments` в `$DB3` до и после равны.
7. Побочные даты — только от `update_task` и только при смене статуса: у `WL-a2` (статус не
   менялся) `started_at`, `closed_at`, `close_reason`, `holder`, `external_ref`, `project` после
   `--update` равны значениям до него (сверка `select` до/после); у `WL-a5` `started_at` из
   `NULL` стал непустым (дата приёмки), `closed_at` остался `NULL`, `holder` пуст, `external_ref`
   и `project` прежние; в журнальном комментарии `WL-a5` нет упоминания `started_at`. В диффе
   `import_writerllm.py` нет прямого `UPDATE tasks SET started_at/closed_at` в ветке update и нет
   передачи дат в `update_task` (чтение диффа).

## Отчёт и код возврата

8. Коды возврата: `LISTIK_DB=$DB ./bin/listik import-writerllm --source
   tests/fixtures/writerllm/broken.jsonl --dry-run; echo $?` → `1`; `--source
   tests/fixtures/writerllm/export.jsonl` (на любой базе) → `1` с ровно одной ошибкой
   `dependency` `WL-a1.1 → WL-missing`; `export-v2.jsonl` на **отдельной пустой** базе
   (`DB2=/tmp/listik-step03b-clean.db`, `LISTIK_DB=$DB2 ./bin/listik --local init`) → `0`.
9. `… --json` даёт валидный JSON и только его в stdout (`python3 -m json.tool`), с ключами
   `ok, total, created, updated, skipped, ignored, dependencies, comments, errors, warnings,
   examples`; `ok` равен `errors == []`; каждый элемент `errors` содержит `where`, `error`, `raw`
   (`raw` — сырая запись/строка; `None` допустим только при `where="source"`).
10. Текстовый вывод содержит строку счётчиков (`записей: … создано: … обновлено: … пропущено: …
    ошибок: …`), примеры по действиям (не более 5 на действие) с ID и заголовком, строки `  ! `
    для ошибок (не более 10, без сырых записей), `(dry-run: ничего не записано)` только при
    `--dry-run`.
11. `./bin/listik import-writerllm --help` показывает `--project` со значением по умолчанию
    `writerllm`, `--update` с описанием про diff в журнал, фразу, что команда работает с базой
    напрямую.

## `show`

12. `LISTIK_DB=$DB ./bin/listik --local show WL-a1` печатает `старый ID (writerllm): WL-a1`;
    строки `было в beads` в `bin/listik` нет (`grep -n "было в beads" bin/listik` пуст); у задачи
    без `external_ref` строки со «старый ID» нет.

## Тесты и границы

13. `python3 -m unittest discover tests` зелёный; в `tests/test_import_writerllm.py` есть тесты
    на пункты 1–7 раздела «Тесты» ТЗ (ручной комментарий; skip без флага + дозапись комментария;
    update с журналом для `WL-a2`/`WL-a5` и датами по правилу Б3; повторный update без журнала;
    dry-run+update; `ok` `True`/`False` на `export-v2.jsonl`/`export.jsonl`/`broken.jsonl`; CLI
    через `subprocess` с `env={"LISTIK_DB": …}` и кодами `1`/`1`/`0`); `git diff HEAD --
    tests/test_import_writerllm.py` содержит только добавления, тесты порции a не изменены.
14. Фикстура `tests/fixtures/writerllm/export-v2.jsonl` существует, без `_type=memory`, без
    ссылок на `WL-missing` (`grep -c "WL-missing\|memory" tests/fixtures/writerllm/export-v2.jsonl`
    → `0`), содержит `WL-a2` с новым `title`, `WL-a5` с `in_progress`, `WL-a3` с добавленным
    комментарием; `grep -rniE "@|фомин|(^|[^d])fomin" tests/fixtures/writerllm/export-v2.jsonl`
    пуст. Фикстуры порции a не изменены (`git diff HEAD --stat -- tests/fixtures/writerllm/
    export.jsonl tests/fixtures/writerllm/export.json tests/fixtures/writerllm/broken.jsonl` пуст).
15. `git diff --stat HEAD -- . ':!docs/specs'` и `git status --porcelain -- . ':!docs/specs'`
    показывают только `listik/import_writerllm.py`, `bin/listik`, `tests/test_import_writerllm.py`,
    `tests/fixtures/writerllm/export-v2.jsonl`; диф `bin/listik` затрагивает только
    `cmd_import_writerllm`, блок `add("import-writerllm", …)` и одну строку `show_task` (чтение
    диффа); `git status --porcelain -- listik/store.py listik/deps.py listik/db.py
    listik/import_beads.py listik/server.py listik/mcp.py listik/client.py web README.md API.md`
    пуст.
16. `--update` ничего не удаляет: после `--update` число связей и комментариев у задач не
    уменьшилось (сверка `select count(*) from deps/comments` до и после).
17. В диффе и фикстурах нет имён людей, e-mail, токенов; тесты не читают `~/Agents` и
    `listik.db`; в тестах нет `subprocess` без `LISTIK_DB` в `env` и нет `db.init()` без явного
    пути (`grep -n "subprocess\|init()" tests/test_import_writerllm.py`); `stat -f %m listik.db`
    до и после приёмки совпадает.
