# Приёмка порции 03.a. Модуль импорта WriterLLM

Все проверки — на временной базе и на фикстурах `tests/fixtures/writerllm/`. Реальную выгрузку и
`listik.db` репозитория судья не использует. **Каждая команда `./bin/listik` в этом чек-листе
идёт с явным префиксом `LISTIK_DB=…` в той же строке; судья не выполняет ни одной команды
`./bin/listik` без него** — `paths.DB_PATH` читается из окружения при импорте, и команда без
переменной пишет в живую базу. Перед началом: `DB=/tmp/listik-step03a.db; rm -f "$DB" "$DB-wal"
"$DB-shm"; LISTIK_DB=$DB ./bin/listik --local init`; `stat -f %m listik.db` снять до и после всей
приёмки — значение не должно измениться.

## Тесты и фикстуры

1. `python3 -m unittest discover tests` зелёный; `python3 -m unittest tests.test_import_writerllm -v`
   показывает не меньше 14 тестов, и по именам/докстрокам видно, какой пункт 1–14 раздела «Тесты»
   ТЗ какой тест закрывает. «Красные до правки» судья подтверждает без `git stash`: сохранить
   черновик `git show HEAD:listik/import_writerllm.py > /tmp/draft_import_writerllm.py`, временно
   подменить им модуль в отдельной копии (`git worktree add /tmp/lk-draft HEAD`, туда же
   скопировать новые тесты и фикстуры) и запустить тесты там — тесты на даты (п. 2), актора
   (п. 3), связи (п. 4) и строку `projects` (п. 11) должны падать; после проверки
   `git worktree remove /tmp/lk-draft`.
2. Фикстуры `tests/fixtures/writerllm/export.jsonl`, `export.json`, `broken.jsonl` существуют,
   синтетические: `grep -rniE "@|фомин|fomin" tests/fixtures/writerllm` пуст (алиас `dfomin`
   допустим только как значение `assignee/created_by/author` — судья смотрит контекст);
   `export.jsonl` содержит запись `_type=memory`, связи `blocks`, `parent-child`, `relates-to`,
   связь на несуществующую цель, запись с двумя комментариями, запись с комментарием без `id` и
   без `created_at`, запись со статусом `weird`.

## Поведение модуля (живой прогон на фикстурах, `DB=/tmp/listik-step03a.db`)

3. Dry-run не пишет ничего, включая побочные вызовы: сразу после `init` снять
   `sqlite3 $DB "select (select count(*) from actors), (select count(*) from actor_aliases)"`,
   затем `LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl
   --dry-run`; после него `select count(*) from tasks/comments/deps/events/projects` — все `0`,
   а `actors`/`actor_aliases` — те же числа, что после `init` (ловит `actors.remember` и
   `seed_actors`); `blocked_by` у задач проверять нечего (задач нет). Чтением кода: в
   dry-run-ветке нет вызовов `actors.remember`, `store.recompute_blocked`, `store.add_dep`,
   `store.create_task`, `_index_*`, `conn.commit()` (ТЗ §6 перечисляет запрещённое).
4. `LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.jsonl`
   (без `--dry-run`): `created` = число `issue`-записей фикстуры, `ignored=1` (поле видно в
   `--json` отчёте, текстовый вывод его не печатает — это порция b);
   `sqlite3 $DB "select id, external_ref, source, project from tasks"` — `id` равен старому ID,
   `source='writerllm'`, `project='writerllm'`.
5. У закрытой записи: `created_at`, `updated_at`, `started_at`, `closed_at`, `close_reason` равны
   значениям из фикстуры (не сегодняшней дате); `status='done'`; `result` = `close_reason`;
   `priority=1`; в `events` есть `created` с `ts` = `created_at` фикстуры и `import`.
6. `assignee`, `created_by` задачи и `author` её комментариев — `me` (не `dfomin`).
7. `sqlite3 $DB "select issue_id, depends_on, dep_type from deps"` содержит `blocks`,
   `parent-child`, `relates-to` с ID Listik; у открытой задачи, ждущей открытую, `blocked_by`
   непуст (`LISTIK_DB=$DB ./bin/listik --local show <id>` печатает «заблокирована: …»); связь
   на несуществующую цель — в `errors` отчёта с `where="dependency"`, задача-источник создана.
8. Комментарии: у записи с двумя комментариями в `comments` ровно два, `created_at` из фикстуры,
   `kind` — `journal` у текста с `[listik]`, `comment` у другого; в `events` этой задачи нет
   `kind='comment'`. У записи с комментарием без `id`/`created_at` — ровно один комментарий,
   `created_at` равен `created_at` задачи из фикстуры, `id` начинается с `<task_id>:wl:`
   (`sqlite3 $DB "select id, created_at from comments where task_id=…"`).
9. Повторный запуск `LISTIK_DB=$DB ./bin/listik import-writerllm --source
   tests/fixtures/writerllm/export.jsonl`: `create=0`, `skip` = число issue-записей;
   `sqlite3 $DB "select count(*) from comments"` снят до и после повтора и **равен** (включая
   комментарий без `created_at` — именно он дублировался бы при `id`/дате от времени прогона);
   счётчики `tasks/deps/events` не изменились. Судья запускает импорт третий раз и снова
   сверяет `count(*) from comments`.
10. `LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm/broken.jsonl
    --json`: две валидные записи созданы; запись без `id` получила `external_ref` с префиксом
    `writerllm:`; второй такой же запуск её не дублирует; в отчёте — ошибка с `line` и `raw` для
    битой строки и ошибка `where="record"` для записи без заголовка; `total=5`; код возврата —
    `1` (уже так в черновике CLI).
11. Коллизия ID: на отдельной пустой базе `DB2=/tmp/listik-step03a-collision.db`:
    `python3 -c 'from pathlib import Path; from listik import db, store;
    c=db.init(Path("/tmp/listik-step03a-collision.db")); store.create_task(c, task_id="WL-a1",
    title="native", project="demo")'`, затем `LISTIK_DB=$DB2 ./bin/listik import-writerllm
    --source tests/fixtures/writerllm/export.jsonl --json`: задача с `external_ref='WL-a1'`
    получила другой `id` (не `WL-a1`), в `examples.create` отчёта у неё `remapped: true`;
    нативная `WL-a1` не изменилась (`title='native'`, `source='native'`).
12. `export.json` (массив, только `issue`-записи без `_type`) на **отдельной пустой базе**
    `DB3=/tmp/listik-step03a-json.db` (`LISTIK_DB=$DB3 ./bin/listik --local init`, затем
    `LISTIK_DB=$DB3 ./bin/listik import-writerllm --source tests/fixtures/writerllm/export.json
    --json`) даёт то же число задач и рёбер, что `export.jsonl` в `$DB`, `ignored=0`,
    комментариев 0; множества `select id from tasks` в `$DB` и `$DB3` совпадают. В фикстуре
    `export.json` записи-памяти нет (`grep -c memory tests/fixtures/writerllm/export.json` → `0`).
13. `sqlite3 $DB "select slug, kind, import_note from projects"` содержит
    `writerllm | writerllm | …<N> задач…`.
14. `LISTIK_DB=$DB ./bin/listik import-writerllm --source tests/fixtures/writerllm` (каталог) и
    `LISTIK_DB=$DB ./bin/listik import-writerllm --source README.md`: `total=0`, одна ошибка
    `where="source"`, число задач в `$DB` не изменилось;
    `grep -n "_markdown_record\|rglob\|def _scalar" listik/import_writerllm.py` пуст.
15. `LISTIK_DB=$DB ./bin/listik --local show WL-a1` показывает карточку с `external_ref`;
    `LISTIK_DB=$DB ./bin/listik --local search WL-a1 --mode text` находит её первой.
16. Запись со статусом `weird`/приоритетом `x` создана как `open`/`2`; `warnings` в `--json`
    отчёте содержит два элемента с `field` `status` и `priority`.

## Границы

17. `git diff --stat HEAD -- . ':!docs/specs'` показывает только `listik/import_writerllm.py`,
    `tests/test_import_writerllm.py`, `tests/fixtures/writerllm/*`; `git status --porcelain -- bin
    listik/store.py listik/deps.py listik/actors.py listik/db.py listik/import_beads.py
    listik/server.py listik/mcp.py listik/client.py README.md API.md CLAUDE.md alembic` пуст.
18. `--update` в этой порции не реализован: `LISTIK_DB=$DB ./bin/listik import-writerllm
    --source tests/fixtures/writerllm/export.jsonl --update` даёт `update=0`, `skip=N`, не падает.
19. В диффе нет имён людей, e-mail, токенов, содержимого `config.toml`; тесты не обращаются к
    `~/Agents`, `listik.db` и путям вне `tmp_path`/фикстур (чтение тестового файла).
20. Изоляция базы — по тексту, не по памяти: в `tests/test_import_writerllm.py` нет вызова
    `db.init()`/`db_mod.init()` без явного пути и нет `bin/listik`/`subprocess` без
    `LISTIK_DB` в `env` (`grep -n "init()\|subprocess\|bin/listik" tests/test_import_writerllm.py`
    — каждое совпадение с явным путём/переменной или пусто); в разделе «Как проверить» ТЗ и в
    отчёте исполнителя каждая строка с `./bin/listik` начинается с `LISTIK_DB=`
    (`grep -n "bin/listik" docs/specs/steps/step-03.a.md | grep -v "LISTIK_DB="` показывает только
    строки-прозу без команд); `stat -f %m listik.db` до и после приёмки совпадает.
