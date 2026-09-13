# Порция 01.b. Статус документа, диагностика недоступного файла, `show`, удаление задачи

## Контекст

Listik — трекер на stdlib-Python: `listik/db.py` (схема, `SCHEMA_VERSION`, мягкие миграции
`MIGRATIONS` через `ALTER TABLE ADD COLUMN`), параллельно `alembic/versions/*` (явные ревизии
той же схемы; `0001_initial` уже содержит таблицы `documents`, `document_chunks`,
`document_chunk_fts`; `0002_document_paths` добавляет колонки в `tasks`). Новая колонка
должна появиться **в обоих механизмах**: в `db.SCHEMA` + `db.MIGRATIONS` + `SCHEMA_VERSION`
и в новой alembic-ревизии `0003_…`, по образцу `0002_document_paths.py` (проверка
`PRAGMA table_info` перед `ALTER`, `downgrade` пустой с пояснением).

Индексация файлов задачи — `listik/documents.py`: `index_document(conn, task_id, path, kind)`
читает файл, при ошибке чтения пишет событие `document_error` (через `store.event`) и
возвращает словарь `{"ok": False, "error": …}`; при успехе — `document_json(...)`.
`index_task_documents` вызывается из `store.create_task`/`update_task` при заданных путях и
из `documents.context()` при каждом вызове. Карточку читает `store.get_task` (в ответе уже есть
список `documents` из таблицы), CLI печатает её функцией `show_task` в `bin/listik`.

Проверено на временной базе: если файл удалить, `listik show` работает, но событие
`document_error` пишется **при каждом** вызове `context`, ответ содержит словарь ошибки без
`kind`/`path`, а `show` никак не сообщает, что файл недоступен. Удаление задачи через
`DELETE /api/tasks/{id}` (в `listik/server.py`) чистит `task_fts`/`comment_fts`, но не
`document_chunk_fts` и не `embeddings` чанков (`documents`/`document_chunks` уходят каскадом по
внешнему ключу — `PRAGMA foreign_keys = ON` включён в `db.connect`).

Тесты: каталог `tests/` со stdlib `unittest` и хелпером временной базы уже заведён порцией a —
используй `tests/helpers.py` и фикстуры из `tests/fixtures/`.

## Что сделать

### 1. Колонки статуса документа

В таблицу `documents` добавить:
- `status TEXT NOT NULL DEFAULT 'ok'` — `ok` | `missing`;
- `error TEXT` — текст последней ошибки чтения (или NULL);
- `checked_at TEXT` — ISO-время последней попытки прочитать файл.

Добавить в `db.SCHEMA` (в `CREATE TABLE documents`), в `db.MIGRATIONS` (три записи для
таблицы `documents`), поднять `SCHEMA_VERSION` до 5. Создать `alembic/versions/0003_document_status.py`
с `down_revision = "0002_document_paths"` и теми же тремя `ALTER TABLE documents ADD COLUMN …`.

### 2. Поведение `index_document` при недоступном файле

- Если файл прочитать не удалось и строки в `documents` для `(task_id, kind, path)` **нет**:
  создать строку с `revision = 0`, `content_hash = ''`, `status = 'missing'`, `error`,
  `checked_at`, `title = path`; чанков нет. Событие `document_error` пишется один раз
  (заметка: `<kind> <path>: <ошибка>`).
- Если строка **есть** и была `status = 'ok'`: перевести в `missing`, записать `error` и
  `checked_at`, **чанки предыдущей ревизии не удалять** — они остаются доступными поиску
  (`document_chunk_fts`) и контексту как последняя известная версия. Событие `document_error` —
  один раз, на переходе.
- Чтобы чанки пропавшего файла действительно оставались в контексте, в `documents.context()`
  разрешена **одна точечная правка**: строка, отбирающая документы фильтром `d.get("ok", True)`
  (сейчас `documents.py:308`), должна перестать отбрасывать документы по `ok` — перебираются все
  документы из `index_task_documents`, документ без чанков просто ничего не добавляет. Ничего
  другого в `context()` не менять; `status`/`error` документа виден в блоке `documents` ответа.
- Если строка есть и уже `missing`: обновить `checked_at` и `error`, событие **не** писать.
- Если файл снова читается, а строка была `missing`: обычная переиндексация по хешу (если хеш
  отличается от сохранённого — `revision + 1` и замена чанков; при `content_hash = ''` — тоже),
  `status = 'ok'`, `error = NULL`, событие `document_restored` один раз.
- Успешное чтение неизменённого файла обновляет только `checked_at` (без `updated_at`).
- `checked_at` — служебная колонка: она **не попадает** ни в `document_json`, ни в блок
  `documents` у `store.get_task`, ни, как следствие, в ответ `context`. Причина: ответ `context`
  обязан быть побайтно одинаковым между двумя вызовами подряд (контракт шага), а `checked_at`
  меняется при каждом чтении. Смотреть `checked_at` — только SQL и тесты.
- Возвращаемое значение при любом исходе — `document_json(conn, doc_id)`, дополненный ключами
  `status`, `error`, `ok` (`True` при `status == 'ok'`). Форма ответа не должна зависеть от
  исхода: набор ключей одинаков — `id, task_id, kind, path, revision, content_hash, title,
  created_at, updated_at, chunks, chunk_count, status, error, ok`.
- `document_json` также отдаёт `status`, `error`, `chunk_count`. При переходах `ok → missing` и
  `missing → ok` обновляется `updated_at` (это смена состояния); повторные проверки в том же
  состоянии `updated_at` не трогают, а `error` для одной и той же причины должен быть одной и
  той же строкой — иначе `context` перестанет быть стабильным.

### 3. `show`

- `store.get_task(..., with_details=True)`: в `documents` добавить `status`, `error`,
  `chunk_count` (подзапрос по `document_chunks`); `checked_at` не отдавать. Файлы при `show` **не читаются**
  и переиндексация не запускается: `show` только показывает состояние индекса.
- `show_task` в `bin/listik` печатает раздел `документы:` — по строке на документ:
  `kind`, путь, `rev N`, `чанков M`, и пометку `ФАЙЛ НЕДОСТУПЕН: <error>` для `missing`.
  Блок `ТЗ:`/`журнал:` можно оставить, но пути `checklist`/`review`/`decision` должны быть видны
  в этом же новом разделе.

### 4. Удаление задачи

В обработчике `DELETE /api/tasks/{id}` в `listik/server.py` перед удалением строки задачи
дополнительно удалить: строки `document_chunk_fts` по `task_id`, строки `embeddings` по
`task_id` (все виды — `task`, `comment`, `chunk`), и явно `documents` по `task_id` (не полагаясь
только на каскад). Вынести это в функцию `store.delete_task(conn, task_id)` и использовать её
в сервере; локальный фолбэк удаления в `client.local_call` не требуется (его сейчас нет).

### 5. Тесты — `tests/test_document_status.py` (временная база)

- Задача с `spec_path` на несуществующий файл: `documents` содержит строку `status='missing'`,
  `revision=0`, ровно одно событие `document_error`; `store.get_task` работает и в
  `documents[0]` видны `status`, `error`.
- Повторный `index_task_documents` (три раза подряд): событие `document_error` по-прежнему
  одно.
- Файл появился: `status='ok'`, `revision=1`, чанки есть, одно событие `document_restored`.
- Файл проиндексирован, затем удалён, затем `index_task_documents`: `status='missing'`,
  чанки предыдущей ревизии на месте (число строк `document_chunks` не изменилось), одно
  событие `document_error`; `documents.context(conn, task_id, "s1-spec")` после этого
  по-прежнему содержит чанки этого документа (их `document_id` есть в `chunks[]`), а в
  `documents[]` ответа у него `status == "missing"`.
- Стабильность: два вызова `documents.context(...)` подряд для одной задачи дают одинаковый
  `json.dumps(..., sort_keys=True)` — и когда файл на месте (между вызовами `checked_at` в
  таблице меняется, а ответ нет), и когда файл отсутствует.
- `checked_at` отсутствует среди ключей `document_json(...)` и элементов `get_task(...)["documents"]`,
  но заполнен в таблице (`SELECT checked_at FROM documents` не NULL после индексации).
- `store.delete_task`: после удаления задачи с документами нет строк в `documents`,
  `document_chunks`, `document_chunk_fts`, `embeddings` с этим `task_id` (для `embeddings`
  вставить фиктивную строку заранее, без Ollama).
- Миграция: во временной базе вручную создать таблицу `documents` старым `CREATE TABLE`
  (без трёх новых колонок), затем вызвать `db.init(db_path=<этот путь>)`; после этого
  `PRAGMA table_info(documents)` содержит `status`, `error`, `checked_at`, а существующие
  строки получили `status = 'ok'`.

## Границы правки

- Правятся: `listik/db.py`, новый `alembic/versions/0003_document_status.py`,
  `listik/documents.py` (`index_document`, `document_json`, `index_task_documents` и одна
  строка фильтра документов в `context()` — см. §2), `listik/store.py` (`get_task` — блок
  `documents`; новая `delete_task`), `listik/server.py` (только обработчик DELETE),
  `bin/listik` (только `show_task`), `tests/test_document_status.py`.
- В `documents.context()` менять только строку фильтра `d.get("ok", True)`; функцию `stable`,
  лимиты, reasons, набор ключей ответа не трогать — это порция d. Не трогать `split_markdown`,
  `listik/search.py`, `listik/embed.py`, `listik/mcp.py`, `client.py`, документацию
  (`API.md`, `README.md`) — это порции c, d, e.
- Не добавлять в ответы `document_json`/`get_task`/`context` полей, меняющихся от вызова к
  вызову (`checked_at`, текущее время).
- Не менять `0001_initial.py` и `0002_document_paths.py`.
- Не удалять чанки при недоступности файла и не менять схему идентификаторов чанков.
- Не ослаблять тесты порции a; они должны остаться зелёными.
- Не трогать рабочую `listik.db`; alembic на рабочей базе не запускать.

## Как проверить

```sh
python3 -m unittest discover tests -v
export LISTIK_DB=/tmp/listik-step01b.db && ./bin/listik --local init
./bin/listik --local --json new "проверка b" --project listik --spec /tmp/nope.md   # id → $TID
./bin/listik --local show $TID            # работает, раздел «документы:» с пометкой ФАЙЛ НЕДОСТУПЕН
./bin/listik --local context $TID --stage s1-spec --format json >/dev/null   # повторить 3 раза
sqlite3 $LISTIK_DB "select kind,count(*) from events where task_id='$TID' group by kind"  # document_error = 1
printf '# Есть\n\nтекст\n' > /tmp/nope.md && ./bin/listik --local context $TID --stage s1-spec --format json >/dev/null
sqlite3 $LISTIK_DB "select status,revision from documents"                   # ok | 1
LISTIK_DB=/tmp/listik-step01b.db alembic upgrade head                        # если alembic установлен; иначе пропустить
```
