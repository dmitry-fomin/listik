# Приёмка порции 01.b. Статус документа, диагностика, `show`, удаление

Сценарии CLI — на временной базе (`LISTIK_DB=/tmp/…`, `--local`). Тесты — `python3 -m unittest discover tests -v`.

## Схема и миграции

1. `listik/db.py`: в `CREATE TABLE documents` есть `status`, `error`, `checked_at`;
   в `MIGRATIONS` три записи `("documents", …)`; `SCHEMA_VERSION == 5`.
2. На базе, где `documents` создана без этих колонок, `db.init` добавляет их
   (тест миграции в `test_document_status` зелёный; до правки — красный или отсутствует).
3. Существует `alembic/versions/0003_document_status.py` с `down_revision = "0002_document_paths"`,
   добавляющий те же три колонки с проверкой `PRAGMA table_info`. Если `alembic` установлен:
   `LISTIK_DB=/tmp/x.db alembic upgrade head` на свежей базе проходит, и `PRAGMA table_info(documents)`
   содержит три колонки. Если не установлен — проверяется чтением файла.
4. `0001_initial.py`, `0002_document_paths.py` не изменены (`git diff`).

## Недоступный файл

5. Задача с `--spec /tmp/nope.md` (файла нет): `listik show` печатает карточку с кодом 0
   и раздел `документы:` со строкой `spec … ФАЙЛ НЕДОСТУПЕН: …`; `show --json` даёт
   `documents[0].status == "missing"`, `revision == 0`.
6. Три вызова `context … --format json` подряд: в `events` ровно одно `document_error`
   (до правки — по одному на каждый вызов; тест «событие один раз» красный до правки).
7. Файл создан, следующий `context`: `documents.status == "ok"`, `revision == 1`, чанки есть,
   ровно одно событие `document_restored`.
8. Файл проиндексирован (rev 1), удалён, `context` вызван: `status == "missing"`, число строк
   `document_chunks` для документа не изменилось, одно `document_error`; `show` работает.
8а. Чанки пропавшего файла остаются в контексте: в том же сценарии
   `context … --stage s1-spec --format json` содержит в `chunks[]` элементы с `document_id`
   этого документа, а в `documents[]` у него `status == "missing"`. До правки фильтр
   `d.get("ok", True)` в `context()` отбрасывал документ целиком — тест красный до правки.
8б. Стабильность `context`: два вызова подряд `context … --format json > /tmp/b1.json` и
   `> /tmp/b2.json` — `cmp /tmp/b1.json /tmp/b2.json` молчит и при существующем файле, и при
   отсутствующем; в тесте — равенство `json.dumps(..., sort_keys=True)`.
9. Форма ответа `index_document` одинакова в обоих исходах: множество ключей в обоих случаях
   равно `{id, task_id, kind, path, revision, content_hash, title, created_at, updated_at,
   chunks, chunk_count, status, error, ok}`; ключа `checked_at` нет (тест).

## `show`

10. `show --json` → `documents[*]` содержит `status`, `error`, `chunk_count` и не содержит
    `checked_at`; `chunk_count` совпадает с `select count(*) from document_chunks where document_id=…`;
    при этом `select checked_at from documents` для того же документа не NULL.
11. `show` не читает файлы: удалить файл, вызвать `show` — `status` остаётся `ok` и событий
    не прибавилось (проверяется до вызова `context`).
12. В текстовом `show` видны пути всех заданных документов (`spec`, `checklist`, `review`,
    `decision`) в разделе `документы:`.

## Удаление

13. Тест `delete_task`: после удаления нет строк `documents`, `document_chunks`,
    `document_chunk_fts`, `embeddings` с этим `task_id` (до правки — строки
    `document_chunk_fts`/`embeddings` остаются: тест красный).
14. `server.py` в обработчике DELETE вызывает `store.delete_task` (проверяется чтением диффа).

## Границы

15. `git diff --stat HEAD` не содержит `listik/search.py`, `listik/embed.py`, `listik/mcp.py`,
    `listik/client.py`, `API.md`, `README.md`; в `listik/documents.py` `split_markdown` не
    изменена, а в `context()` дифф ограничен строкой фильтра документов (`d.get("ok", True)`):
    функция `stable`, лимиты, `reasons`, набор ключей ответа не тронуты (чтение диффа);
    тесты порции a зелёные.
