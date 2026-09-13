# Приёмка порции 03.d. Импорт WriterLLM в живую базу

Судья работает с **живой** базой `listik.db` только чтением (`sqlite3` `select`, `--local show/
search/list`) и с файлами исполнителя из его `$WORK` (путь — в отчёте исполнителя). Ничего не
импортировать повторно, `--update` не запускать, `bd` не вызывать, `projects --remove` и
`.restore` не выполнять. В вердикт не копировать тексты задач и e-mail — только ID, счётчики,
даты, ключи акторов.

## Предусловие

0. Порции a–c закоммичены (`README.md` содержит раздел «Импорт WriterLLM», `python3 -m unittest
   discover tests` зелёный); отчёт исполнителя содержит `$WORK`, паспорт выгрузки (§1 ТЗ:
   записей, разбивка по `_type`, `N`, `D`, `C`), пути к `dryrun.json`, `import.json`, `rerun.json`,
   `project-after-import.txt`, `counts-after-import.txt`, к снимку базы и результат проверки
   акторов (2б).

## Выгрузка, dry-run, акторы, снимок (по файлам исполнителя)

1. Файл выгрузки существует в `$WORK` (вне `~/Projects/Listik`), оканчивается на `.jsonl`; судья
   сам считает паспорт скриптом из §1 ТЗ: число записей, разбивку по `_type`, `N` = число
   issue-записей (`N ≥ 777`), `D`, `C`, число записей с путями документов — и сверяет с отчётом
   исполнителя. `wc -l` — справочно, не эталон.
1а. Пути документов: число записей с ключами `spec_path|spec`, `journal_path|journal|log_path`,
   `checklist_path|checklist` в паспорте исполнителя равно тому, что насчитал судья. Если оно `0`
   — `select count(*) from documents d join tasks t on t.id=d.task_id where t.source='writerllm'`
   и такой же `count(*)` по `document_chunks` (через `documents.task_id`) в живой базе → `0`, и
   `select count(*) from tasks where source='writerllm' and (spec_path is not null or journal_path
   is not null or checklist_path is not null)` → `0`. Если больше нуля — импорт **не должен был
   запускаться**: `select count(*) from tasks where source='writerllm'` → `0`, пп. 5–15 не
   применяются, порция ждёт решения автора; задачи в базе при ненулевом числе — красный пункт.
2. `dryrun.json`: `dry_run=true`, `created=N`, `skipped=0`, `ignored = total − N` (равно числу
   не-issue записей из паспорта; может быть 0), нет ошибок с `where` `record`/`source`; ошибки
   `dependency` (если есть) перечислены в отчёте исполнителя парами `external_ref → target_ref`.
3. Акторы до записи (2б): в отчёте есть число различных сырых значений, ключи `me`/`agent:*` с
   числом значений и строка «несведённых акторов: 0» — либо список несведённых ключей и
   **остановка без импорта** (тогда пп. 5–13 не применяются, порция ждёт решения автора). Судья
   повторяет скрипт 2б сам (он read-only) и сверяет результат.
4. Снимок `listik-before-writerllm.db` существует в `$WORK`, вне репозитория;
   `sqlite3 <снимок> "pragma integrity_check"` → `ok`; `select count(*) from tasks where
   source='writerllm'` в снимке → `0`; `select source, count(*) from tasks group by source` в
   снимке совпадает с числами «до импорта» из отчёта исполнителя (на 12.09.2026 — `beads` 3077,
   `native` 10). Дата снимка раньше `imported_at` проекта `writerllm`.

## Живая база после импорта и повтора

5. `sqlite3 listik.db "select count(*) from tasks where source='writerllm' and project='writerllm'"`
   = `N`; `select id, external_ref from tasks where source='writerllm' and id != external_ref` пуст
   (в живой базе ID вида `WriterLLM-*` до импорта не было; если не пуст — пары перечислены в отчёте
   исполнителя с объяснением).
6. `import.json`: `created=N`, `ignored = total − N`, `skipped=0`, `ok` соответствует пустоте
   `errors`; код возврата в отчёте исполнителя `0` при пустых ошибках либо `1` при ошибках
   `dependency`. `rerun.json`: `created=0`, `updated=0`, `skipped=N`, `ignored = total − N`,
   `dependencies=0`, `comments=0`; ошибки повтора той же природы, что у импорта (те же пары
   `external_ref → target_ref`), код возврата тот же.
7. `select count(*) from deps d join tasks t on t.id=d.issue_id where t.source='writerllm'`
   = `D − (число ошибок dependency в import.json)` и равен значению в `counts-after-import.txt`;
   среди `dep_type` есть `parent-child` и `blocks` (`select dep_type, count(*) … group by dep_type`).
8. `select count(*) from comments c join tasks t on t.id=c.task_id where t.source='writerllm'`
   = `C` и равен значению в `counts-after-import.txt`; `select count(*) from events e join tasks t
   on t.id=e.task_id where t.source='writerllm' and e.kind='comment'` = `0` (комментарии
   импортированы без событий).
9. `select count(*) from events e join tasks t on t.id=e.task_id where t.source='writerllm' and
   e.kind='import'` = `N`; `select count(*) from tasks where source='writerllm' and created_at >=
   date('now')` = `0`; `select min(created_at), max(created_at) from tasks where source='writerllm'`
   — даты 2026 года до дня импорта.
10. `select status, count(*) from tasks where source='writerllm' group by status` — статусы только
    из `open|in_progress|blocked|review|done|cancelled`, `done` ≥ 776.
11. Акторы после записи согласуются с 2б: `select distinct assignee from tasks where
    source='writerllm' and assignee is not null` ∪ `select distinct created_by …` ∪ `select
    distinct author from comments c join tasks t on t.id=c.task_id where t.source='writerllm' and
    author is not null` ⊆ `{me} ∪ {agent:*} ∪ <список ключей, явно согласованный автором в
    отчёте 2б>`. При «несведённых акторов: 0» из 2б множество ⊆ `{me, agent:*}`.
12. Строка проекта: `project-after-import.txt` содержит `writerllm | writerllm | <imported_at> |
    N задач из WriterLLM` (снята исполнителем между импортом и повтором); сейчас в живой базе
    `select slug, kind, import_note from projects where slug='writerllm'` → `kind='writerllm'`,
    `import_note = '0 задач из WriterLLM'` — это ожидаемое следствие повтора (UPSERT на каждом
    запуске), не дефект. Любое другое `import_note` (например, `N задач…` после повтора) означает,
    что повтор не выполнялся, — красный пункт.
13. `./bin/listik --local show WriterLLM-yti4`: статус «готова», начата `2026-09-05…`, закрыта
    `2026-09-06…`, есть причина закрытия, строка `старый ID (writerllm): WriterLLM-yti4`;
    `./bin/listik --local show WriterLLM-dyaw.4` открывается и показывает связь `parent-child`
    на `WriterLLM-dyaw` (в `dependencies` JSON-вывода `--json`).
14. `./bin/listik --local search WriterLLM-yti4 --mode text -n 3` — карточка первой в выдаче.
15. Если сервер поднят: `./bin/listik status` показывает число задач ≥ прежнего + `N`
    (прежнее — по снимку из п. 4), `./bin/listik show WriterLLM-yti4` без `--local` даёт ту же
    карточку. Если не поднят — пункт не применяется, поднимать не нужно.

## Границы

16. `git status --porcelain -- . ':!docs/specs'` пуст: в репозитории нет выгрузки, снимка,
    отчётов, правок кода/доков; `git log -1` — коммит порции c (кода в этой порции нет).
17. В отчёте исполнителя нет сырых записей выгрузки и e-mail; есть только ID, счётчики, даты,
    ключи акторов из 2б, ошибки `dependency` парами ID; нет следов `projects --remove`, `.restore`,
    `--update`, `import-beads`, остановки/перезапуска сервера.
18. Число задач `source='beads'` и `source='native'` в живой базе равно числам в снимке из п. 4
    (`select source, count(*) from tasks group by source`).
19. Ни один существующий проект в `projects`, кроме `writerllm`, не получил `imported_at` сегодня
    (`select slug from projects where date(imported_at)=date('now')` → только `writerllm`).
