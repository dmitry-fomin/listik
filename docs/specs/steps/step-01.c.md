# Порция 01.c. Поиск и embeddings на уровне чанков

## Контекст

Гибридный поиск Listik — `listik/search.py`: лексическая ветка `_lexical_run` бьёт по трём
FTS5-таблицам (`task_fts`, `comment_fts`, `document_chunk_fts`), векторная `vector` считает
косинус по таблице `embeddings` (там `doc_kind` ∈ `task|comment|chunk`, `doc_id` — id задачи,
комментария или чанка `<doc_id>:<ordinal>`), результаты сливаются RRF (`RRF_K=60`) и
агрегируются к задаче: `results[]` с `hits[]` (`kind, doc_id, rrf, snippet, author`).
Текстовый вывод — `print_results`. Векторы считает `listik/embed.py` (`pending`, `embed_pending`,
kind `chunk` уже поддержан, дефолт `kinds="task,comment,chunk"`); фоновый воркер
`start_embed_worker` в `listik/server.py` раз в 45 с вызывает `embed_pending` и
`search.invalidate_vectors()`. Ollama может быть недоступна — тогда векторная ветка молча
возвращает пусто; это поведение сохранить.

Документы индексирует `listik/documents.py` (`index_document`, `index_task_documents`; после
порции b у документа есть `status`). Переиндексация по изменившемуся `content_hash` удаляет и
заново вставляет чанки с теми же id `<doc_id>:<ordinal>`.

Проверено на временной базе: `search "<фраза из середины длинного раздела>"` находит задачу и
чанк, но в `hits` нет `heading`/`breadcrumb`, хотя `_lexical_run` их читает; текстовый вывод
заголовок раздела не показывает. `POST /api/embed` по умолчанию берёт `kinds="task,comment"`
(чанки не считаются), тогда как воркер и CLI `listik embed` считают чанки. При переиндексации
с уменьшением числа чанков в `embeddings` остаются строки старых `chunk`-id — их вектора
продолжают попадать в выдачу. Файл, изменённый на диске, переиндексируется только при
`context`/`update`; больше никто хеш не перепроверяет.

Тесты: `tests/` со stdlib `unittest`, хелпер временной базы `tests/helpers.py`, фикстуры
`tests/fixtures/` (порция a).

## Что сделать

### 1. Результаты поиска показывают раздел

- В `search.search` для хитов `kind == "chunk"` добавлять в элемент `hits[]` ключи
  `heading`, `breadcrumb`, `document_id`, `document_kind`, `path`, `start_line`, `end_line`.
  **Откуда берутся поля.** В `document_chunk_fts` есть только `chunk_id, document_id, task_id,
  heading, breadcrumb, body` — ни `path`, ни `start_line`/`end_line`, ни вида документа там нет,
  а `chunk_sql` в `_lexical_run` (`search.py:110-121`) выбирает только колонки FTS. Поэтому
  обогащение делается **в одном месте и для всех чанк-хитов сразу, независимо от ветки**:
  после того как собраны `lex` и `vec` и до агрегации по задаче (`per_task`), взять множество
  всех `chunk_id` из обоих списков и одним запросом
  `SELECT c.id, c.document_id, c.heading, c.breadcrumb, c.start_line, c.end_line, d.kind, d.path
  FROM document_chunks c JOIN documents d ON d.id = c.document_id WHERE c.id IN (…)`
  получить метаданные. `chunk_sql` и схему `document_chunk_fts` не расширять.
- **Сироты отбрасываются до агрегации.** `chunk_id`, которого нет в ответе этого запроса
  (чанк удалён при переиндексации, строка в `embeddings` осталась), исключается из `lex`/`vec`
  **до** построения `ranks`/`per_task`: его `rrf` не входит в `score` задачи, а задача, у которой
  после отсева не осталось ни одного хита, в `results[]` не попадает. Это и есть основная защита
  от исчезнувших чанков — кеш векторов процессный (`_VEC_CACHE`, TTL 300 с) и живёт в процессе
  сервера, а индексация в `--local` идёт в процессе CLI, так что `invalidate_vectors()` из §2
  сервер не увидит; фильтр — не перестраховка.
- **Политика слияния хитов из двух веток.** Сейчас `info = {(kind, doc_id): i for i in lex + vec}`
  (`search.py:286`) перетирает лексический элемент векторным, и у чанка, найденного обеими
  ветками (нормальный случай `mode="hybrid"`), пропадают `snippet`/`heading`/`row`. Заменить
  на слияние `{**vec_item, **lex_item}`: лексический элемент главнее по всем общим ключам
  (`snippet`, `row`, `heading`, `breadcrumb`), векторный лишь добавляет то, чего в лексическом
  нет. `rrf` при этом по-прежнему суммируется из рангов обеих веток — формулу RRF не менять.
  Метаданные из запроса выше подставляются в хит после слияния и перекрывают `heading`/`breadcrumb`
  из FTS-строки (источник истины — `document_chunks`).
- Для хитов `task`/`comment` эти ключи присутствуют со значением `null`, чтобы форма
  элемента `hits[]` была одинаковой.
- В `results[]` добавить `best_hit`: копию хита с максимальным `rrf` (так harness и доска
  видят, откуда совпадение, не разбирая `hits`).
- `print_results`: если лучший хит — чанк, печатать под заголовком строку
  `раздел: <breadcrumb или heading> (<kind> <path>:<start_line>)`.
- `snippet` чанка не должен начинаться с breadcrumb-префикса, если совпадение ниже: snippet
  по-прежнему берётся из FTS `snippet()` по колонке `body`; ничего в его вычислении не менять.

### 2. Embeddings по чанкам

- `POST /api/embed` в `server.py`: дефолт `kinds` — `"task,comment,chunk"` (как у воркера и CLI).
- `documents.index_document`: при замене чанков (изменился хеш) удалять из `embeddings`
  строки с `doc_kind='chunk'` и `doc_id` из списка **старых** id чанков документа, затем
  вызывать `search.invalidate_vectors()`. Новые чанки с теми же id и новым текстом воркер
  пересчитает сам по несовпадению `text_hash` (уже работает).
- `embed.pending(..., kinds)` для `chunk`: пропускать чанки документов со `status='missing'`?
  **Нет** — чанки последней известной ревизии остаются в поиске; ничего не менять.

### 3. Фоновая перепроверка файлов

- В `listik/documents.py` добавить `refresh_all(conn) -> dict` : для каждой задачи, у которой
  задан хотя бы один из путей документов, вызвать `index_task_documents`; вернуть
  `{"checked": N, "reindexed": M, "missing": K}` (`reindexed` — документы, у которых сменилась
  `revision`; `missing` — со `status='missing'` после прохода). Исключения одной задачи ловить,
  логировать в событие `document_error` через существующий механизм и идти дальше.
- В `start_embed_worker` (`server.py`) перед `embed_pending` вызывать `documents.refresh_all`;
  если `reindexed > 0` — печатать строку `[documents] переиндексировано: M` и `publish("documents", res)`.
  Ошибка `refresh_all` не должна ронять цикл воркера (тот же `try/except`, что и вокруг embed).
- Задача с закрытым статусом (`status in ('done','cancelled','closed')`) в `refresh_all` не
  перепроверяется: её файлы больше не меняются, а чтение сотен файлов каждые 45 с не нужно.

### 4. Тесты — `tests/test_search_chunks.py` (временная база, без Ollama)

**Изоляция от реальной Ollama и от кеша векторов — обязательные правила для всех тестов
этого модуля.**

- Единственный способ заглушить Ollama — `unittest.mock.patch.object(embed, "ollama_embed", …)`
  (в `search.vector` вызов идёт через модуль `embed`, патч срабатывает). Переменная окружения
  `LISTIK_OLLAMA_URL` **не годится**: `paths.OLLAMA_URL` читается один раз при импорте
  (`paths.py:21`), и установка внутри теста ничего не меняет — тест уйдёт в реальную Ollama на
  машине разработчика. Патч ставится в `setUp` модуля на заглушку, которая по умолчанию бросает
  `RuntimeError("ollama недоступна в тестах")`; отдельные тесты переопределяют её своей
  заглушкой с нужными векторами. Заглушка должна считать вызовы (`unittest.mock.Mock` с
  `side_effect`), чтобы тест мог утверждать, что векторная ветка действительно дошла до вызова.
- `search._VEC_CACHE` — глобальный на процесс, ключ только по имени модели, TTL 300 с; он
  делится между всеми временными базами одного прогона `unittest discover`. Поэтому
  `search.invalidate_vectors()` вызывается в `setUp` и **после каждой** прямой записи в
  `embeddings`; тесты не должны зависеть от порядка.
- Векторная ветка в пустой базе выходит до вызова Ollama (`if not rows: return []`,
  `search.py:158-159`), поэтому любой тест про векторы обязан **заранее** вставить в
  `embeddings` строки с фиктивными векторами: `doc_kind='chunk'`, `doc_id` = реальный id чанка
  (из `document_chunks`), `task_id`, `project`, `model=paths.EMBED_MODEL`, `dim` = длина вектора,
  `vec=embed.vec_to_blob([...])`, `text_hash` (любой), `embedded_at`. Векторы подбираются
  вручную (например, ортогональные единичные из 4 компонент), а заглушка `ollama_embed`
  возвращает вектор запроса, равный вектору нужного чанка — так косинус детерминирован и равен 1.

Сценарии:

- Задача A с `spec_path` = копия `long-spec.md`; задача B без документов. Поиск
  `search.search(conn, "<уникальная фраза>", mode="text")`: `results[0].id == A`,
  `best_hit.kind == "chunk"`, `best_hit.heading` равен заголовку длинного раздела,
  `best_hit.breadcrumb` содержит корневой заголовок, `path` равен пути документа,
  `start_line`/`end_line` — целые, совпадающие со строкой `document_chunks`.
- Поиск по слову из заголовка задачи B (`mode="text"`): B находится, `best_hit.kind == "task"`,
  у хита `heading is None` (форма одинаковая).
- Деградация: фиктивные векторы для чанков A вставлены (база непуста), заглушка
  `ollama_embed` бросает исключение; `mode="hybrid"` даёт тот же список `id`, что `mode="text"`,
  исключение наружу не выходит, **и заглушка была вызвана ровно один раз** — иначе тест
  проверил не деградацию, а ранний выход.
- Векторные хиты обогащены: фиктивные векторы вставлены, заглушка возвращает вектор чанка с
  уникальной фразой; `mode="vector"` находит A, `best_hit.kind == "chunk"`, `heading`,
  `breadcrumb`, `path`, `start_line` непустые (данные дочитаны из `document_chunks`, в
  векторной ветке их нет).
- Слияние веток: тот же чанк находится и лексически (запрос — уникальная фраза), и векторно
  (заглушка возвращает его вектор); `mode="hybrid"`: у `best_hit` этого чанка `snippet`
  содержит `[[`…`]]`-подсветку из FTS (лексический snippet не потерян), `heading`/`breadcrumb`
  непустые, а `rrf` больше, чем у того же чанка в `mode="text"` (сумма двух рангов). До правки
  `info` векторный элемент перетирал лексический — `snippet` пустой, тест красный.
- Сироты: вставить в `embeddings` строку `doc_kind='chunk'` с `doc_id` несуществующего чанка
  (`task_id` = A) и, кроме неё, строку реального чанка A; заглушка возвращает вектор сироты.
  `mode="vector"`: не падает, A **не** попадает в выдачу по сироте (если реальный чанк не
  совпал — `results` пуст); затем заглушка возвращает вектор реального чанка, и сирота с тем же
  вектором не увеличивает `score` A по сравнению с прогоном без сироты.
- Переиндексация после укорачивания файла удаляет из `embeddings` строки старых чанков
  (заранее вставить фиктивные строки `embeddings` для всех id чанков документа).
- `refresh_all`: изменить файл на диске, вызвать `refresh_all` — `reindexed == 1`, `revision`
  выросла; повторный вызов — `reindexed == 0`; задача со статусом `done` не перечитывается
  (изменить её файл — `revision` не растёт).

## Границы правки

- Правятся: `listik/search.py` (`_lexical_run`, `search`, `print_results`), `listik/embed.py`
  (только если понадобится вспомогательная функция; логику `pending` не менять),
  `listik/documents.py` (уборка embeddings в `index_document`, новая `refresh_all`),
  `listik/server.py` (дефолт `kinds` в `/api/embed`, вызов `refresh_all` в воркере),
  `tests/test_search_chunks.py`.
- **Не менять RRF**: `RRF_K`, формулу `1/(k+rank)`, суммирование по задаче, `pool`, порядок
  сортировки. Не менять `_snippet`, `fts_query`, `fts_query_or`, лимиты `SNIPPET_CHARS`.
- Не менять таблицы и id чанков; не расширять `document_chunk_fts` и `chunk_sql` — метаданные
  чанков дочитываются одним запросом из `document_chunks JOIN documents` (§1). Не трогать
  `documents.context()` — это порция d.
- В тестах не обращаться к реальной Ollama и не полагаться на переменные окружения
  `LISTIK_OLLAMA_URL`/`LISTIK_EMBED_MODEL`: только `patch.object(embed, "ollama_embed", …)`.
- Не трогать `bin/listik` (текстовый вывод поиска живёт в `search.print_results`), `mcp.py`,
  `client.py`, документацию.
- Не заглушать деградацию векторной ветки: `except Exception → []` остаётся.
- Тесты порций a и b остаются зелёными.

## Как проверить

```sh
python3 -m unittest discover tests -v
export LISTIK_DB=/tmp/listik-step01c.db && ./bin/listik --local init
cp tests/fixtures/long-spec.md /tmp/spec-c.md
./bin/listik --local --json new "поиск по чанкам" --project listik --spec /tmp/spec-c.md   # → $TID
./bin/listik --local search "<уникальная фраза из long-spec.md>" --mode text               # строка «раздел: …»
./bin/listik --local --json search "<та же фраза>" --mode text | python3 -c "import json,sys; r=json.load(sys.stdin)['results'][0]; print(r['best_hit']['heading'], r['best_hit']['breadcrumb'])"
./bin/listik --local --json search "поиск по чанкам" --mode text                             # задача без документов находится
```
