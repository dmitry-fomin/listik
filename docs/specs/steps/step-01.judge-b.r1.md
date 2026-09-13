# Приёмка порции 01.b, заход r1 — вердикт: зелёный

Проверка велась на временных базах в скретч-каталоге сессии (`LISTIK_DB=<scratch>/b.db`,
`--local`); рабочая `listik.db` не открывалась на запись (`git status listik.db` чист,
mtime не изменился). Для проверки «красноты до правки» поднималось отдельное
`git worktree` на `HEAD` (3ab8a7a), после проверки удалено.

## Тесты

`python3 -m unittest discover tests -v` — **35 тестов, OK** (21 порции a + 9 новых + 5 из
`test_index_documents`).

Тот же `tests/test_document_status.py`, положенный в worktree на `HEAD`:
`Ran 9 tests — FAILED (failures=4, errors=5)`, то есть **все девять новых тестов красные до
правки**, в том числе:
- `test_migrate_adds_document_status_columns` — `'status' not found in {...}` (п. 2);
- `test_repeated_indexing_of_missing_file_writes_event_once` — `4 != 1` (п. 6);
- `test_deleted_file_keeps_previous_chunks_and_flags_missing` — ошибка на `row["status"]` (п. 8, 8а);
- `test_delete_task_removes_all_derived_rows` — `store.delete_task` отсутствует (п. 13);
- `test_context_stable_while_file_missing` — ответы двух `context` расходились (п. 8б).

Ни одного ослабленного теста порции a не обнаружено: `tests/test_chunking.py` и
`tests/test_index_documents.py` в диффе отсутствуют.

## Схема и миграции (пп. 1–4)

1. **зелёный.** `listik/db.py:207-209` — `status TEXT NOT NULL DEFAULT 'ok'`, `error TEXT`,
   `checked_at TEXT` в `CREATE TABLE documents`; `listik/db.py:263-265` — три записи
   `("documents", …)` в `MIGRATIONS`; `listik/db.py:14` — `SCHEMA_VERSION = 5`.
2. **зелёный.** Тест миграции со старым `CREATE TABLE documents` зелёный на правке, красный на `HEAD`;
   строка `d1` после `db.init` имеет `status='ok'`.
3. **зелёный.** `alembic/versions/0003_document_status.py:10-11` — `revision = "0003_document_status"`,
   `down_revision = "0002_document_paths"`; проверка `PRAGMA table_info` перед каждым `ALTER`,
   `downgrade` — пустой с пояснением. Alembic установлен (1.18.4):
   `LISTIK_DB=<scratch>/alem.db alembic upgrade head` прошёл, `PRAGMA table_info(documents)` →
   `[… updated_at, status, error, checked_at]`, `alembic_version = 0003_document_status`.
   Offline-ветка (`as_sql`) предусмотрена отдельно.
4. **зелёный.** `git diff --stat HEAD -- alembic/versions/0001_initial.py alembic/versions/0002_document_paths.py`
   пуст; файлы не в списке изменённых.

## Недоступный файл (пп. 5–9)

5. **зелёный.** `listik show <id>` на задаче со `--spec <несуществующий>` печатает карточку,
   `exit=0`, раздел `документы:` со строкой
   `spec <путь> rev 0 чанков 0 — ФАЙЛ НЕДОСТУПЕН: [Errno 2] No such file or directory: …`.
   `show --json` → `documents[0].status == "missing"`, `revision == 0`.
6. **зелёный.** Три `context … --format json` подряд: `select kind,count(*) from events` →
   `[('created',1), ('document_error',1)]`. На `HEAD` тот же сценарий давал 4 события.
7. **зелёный.** После создания файла: `documents` → `status='ok'`, `revision=1`,
   `document_chunks = 2`, `document_chunk_fts = 2`, ровно одно `document_restored`.
8. **зелёный.** Файл удалён, `context` вызван: `status='missing'`, `revision` остался 1,
   число строк `document_chunks` не изменилось (2 → 2), добавилось ровно одно новое
   `document_error` (итого 2 за всю историю задачи — первое было на исходном отсутствии файла);
   `show` работает.
8а. **зелёный.** В том же состоянии `context … --stage s1-spec --format json` содержит
   `chunks[]` с `document_id` пропавшего документа, а `documents[]` у него `status == "missing"`,
   `chunk_count == 2`. Правка — ровно одна строка `documents.py:355` (`for doc in sorted(docs, …)`
   вместо `sorted((d for d in docs if d.get("ok", True)), …)`).
8б. **зелёный.** `cmp` двух подряд выданных `context` молчит и при существующем файле,
   и при отсутствующем; в тестах — равенство `json.dumps(..., sort_keys=True)` в обоих случаях,
   при этом `checked_at` в таблице между вызовами меняется.
9. **зелёный.** Проверено напрямую: множества ключей `index_document` в успешном и в ошибочном
   исходе равны эталонному `{id, task_id, kind, path, revision, content_hash, title, created_at,
   updated_at, chunks, chunk_count, status, error, ok}` (симметрическая разность пуста),
   `checked_at` отсутствует в обоих.

## `show` (пп. 10–12)

10. **зелёный.** `documents[*]` в `get_task` содержат `status`, `error`, `chunk_count` и не содержат
    `checked_at` (`listik/store.py:460-462` — подзапрос `count(*) FROM document_chunks`);
    на задаче с четырьмя документами `chunk_count` совпал с `select count(*) from document_chunks`
    для каждого, `select checked_at from documents` — не NULL.
11. **зелёный.** `store.get_task` не вызывает `index_task_documents`; после `rm` файла и вызова
    `show` (до `context`) число событий не изменилось: `[('created',1), ('document_error',1)]`,
    `status` в таблице остался прежним.
12. **зелёный.** Задача с `--spec/--checklist/--review/--decision`: в текстовом `show` раздел
    `документы:` содержит все четыре строки (`checklist`, `decision`, `review`, `spec`)
    с путями, `rev 1`, `чанков 1`.

## Удаление (пп. 13–14)

13. **зелёный.** `test_delete_task_removes_all_derived_rows` зелёный: после `store.delete_task`
    нет строк в `documents`, `document_chunks`, `document_chunk_fts`, `embeddings` (вставлялась
    фиктивная строка без Ollama) и в `tasks`. На `HEAD` тест падает (функции нет).
14. **зелёный.** `listik/server.py:312` — обработчик DELETE целиком заменён на
    `store.delete_task(conn, tid)`; логика вынесена в `listik/store.py:216-229`, где к прежним
    `task_fts`/`comment_fts`/`comments`/`tasks` добавлены `document_chunk_fts`, `embeddings`
    и явный `documents`. Больше в `server.py` ничего не изменено (один хунк).

## Границы (п. 15)

**зелёный.** `git diff --stat HEAD` для `listik/search.py`, `listik/embed.py`, `listik/mcp.py`,
`listik/client.py`, `API.md`, `README.md` пуст. `git diff -U0 HEAD` даёт хунки только в
разрешённых местах:

- `bin/listik` — один хунк `@@ -132,0 +133,7 @@ def show_task`;
- `listik/db.py` — `SCHEMA_VERSION`, `CREATE TABLE documents`, `MIGRATIONS`;
- `listik/documents.py` — хунки внутри `index_document` (со старой строки 224), два в
  `document_json` и **один однострочный** в `context()` (старая 308 → новая 355);
  `split_markdown` (строки выше 220) не затронут; `stable`, лимиты, `reasons`, набор ключей
  ответа `context` (`['acceptance','card','chunks','dependencies','documents','generated_at',
  'journal','limits','portion','reasons','reviews','stage','task','verdicts']`) не менялись;
- `listik/server.py` — только DELETE;
- `listik/store.py` — `delete_task` + блок `documents` в `get_task`.

Тесты порции a зелёные.

## Срезанные углы: не найдено

- Хардкода под тест нет: тесты дёргают `store`/`documents` через публичные функции и SQL,
  сценарии воспроизведены руками через CLI с теми же результатами.
- Дублирование цикла вставки чанков в двух ветках `index_document` — не срезанный угол, а
  следствие требования «не удалять чанки при недоступности файла»: ветка «был missing,
  файл вернулся с тем же хешем» осознанно не трогает чанки.
- `checked_at` действительно нигде не протекает в JSON (проверено и тестом, и глазами по
  трём местам: `document_json`, `get_task`, ответ `context`).
- Секретов в диффе нет: затронуты только `.py`, `bin/listik` и новый тест.

## Наблюдение (не красное, вне границ порции)

`store.delete_project` (`listik/store.py:955-962`) при удалении проекта с задачами чистит
`task_fts`/`comment_fts`/`comments`/`deps`/`events`, но не `document_chunk_fts`, не `embeddings`
и не `documents` — то есть повторяет ровно тот дефект, который порция b починила для удаления
задачи. Границы порции b явно ограничены обработчиком `DELETE /api/tasks/{id}`, и в чек-листе
шага этого пункта нет, поэтому вердикт это не меняет; стоит завести отдельной задачей
(кандидат — переиспользовать `store.delete_task` в цикле `delete_project`).

## Вердикт

Все 15 пунктов чек-листа (с 8а и 8б — 17 проверок) зелёные, перенесённых нет.
Коммит: см. первую строку отчёта оркестратору.
