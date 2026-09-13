# Приёмка порции 01.c. Поиск и embeddings на уровне чанков

Тесты — `python3 -m unittest discover tests -v` (без Ollama и сервера). Сценарии CLI — на
временной базе с `--local`.

## Результаты поиска

Правила изоляции, без которых пункты 4–5б не считаются проверенными: в
`tests/test_search_chunks.py` Ollama заглушена только через
`unittest.mock.patch.object(embed, "ollama_embed", …)` (не через переменные окружения — судья
проверяет чтением теста, что `LISTIK_OLLAMA_URL`/`os.environ` в модуле не используются);
`search.invalidate_vectors()` вызывается в `setUp` и после каждой прямой записи в
`embeddings`; во всех векторных тестах таблица `embeddings` непуста до вызова `search`.

1. Тест: поиск уникальной фразы из середины длинного раздела `long-spec.md` (`mode="text"`)
   возвращает задачу-владельца первой, `best_hit.kind == "chunk"`, `best_hit.heading` —
   заголовок этого раздела, `best_hit.breadcrumb` содержит корневой заголовок, `path` — путь
   документа, `start_line`/`end_line` совпадают со строкой `document_chunks`. До правки ключей
   `heading`/`breadcrumb`/`best_hit` в ответе нет — тест красный. Поля берутся одним запросом
   из `document_chunks JOIN documents`; `chunk_sql` и схема `document_chunk_fts` не расширены
   (чтение диффа `listik/search.py`, `listik/db.py`).
2. Тест: задача без документов находится по слову заголовка, `best_hit.kind == "task"`, в хите
   присутствуют ключи `heading, breadcrumb, document_id, document_kind, path, start_line,
   end_line` со значением `None`.
3. CLI: `listik --local search "<фраза>" --mode text` печатает под задачей строку
   `раздел: <breadcrumb> (spec <путь>:<строка>)`.
4. Тест деградации: в `embeddings` заранее вставлены фиктивные векторы чанков задачи A,
   заглушка `ollama_embed` бросает исключение; `mode="hybrid"` даёт тот же список id, что
   `mode="text"`, без исключений, **и заглушка вызвана ровно один раз** (`mock.call_count == 1`).
   Без вставленных векторов или без проверки счётчика пункт не засчитывается — ветка выходит
   до вызова Ollama.
5. Тест сирот: в `embeddings` строка `doc_kind='chunk'` с несуществующим `doc_id` и строка
   реального чанка A; заглушка возвращает вектор сироты — `mode="vector"` не падает и A по
   сироте не находится; заглушка возвращает вектор реального чанка — `score` A равен `score` в
   прогоне без сиротской строки (сирота отброшена до агрегации, её `rrf` не суммируется).
5а. Тест обогащения векторных хитов: фиктивные векторы вставлены, заглушка возвращает вектор
   чанка с уникальной фразой; `mode="vector"` → `best_hit.kind == "chunk"`, `heading`,
   `breadcrumb`, `path`, `start_line` непустые. До правки у векторного хита этих полей нет —
   тест красный.
5б. Тест слияния веток: один чанк найден и лексически (запрос — уникальная фраза), и векторно
   (заглушка возвращает его вектор); `mode="hybrid"` → у `best_hit` `snippet` содержит
   подсветку `[[…]]` из FTS, `heading`/`breadcrumb` непустые, `rrf` больше, чем у того же чанка в
   `mode="text"`. До правки `info` векторный элемент перетирал лексический и `snippet` пустой —
   тест красный.

## Embeddings

6. `server.py`: дефолт `kinds` в `POST /api/embed` равен `"task,comment,chunk"` (чтение диффа).
7. Тест: после переиндексации с уменьшением числа чанков в `embeddings` нет строк с
   `doc_id` старых чанков документа (до правки — остаются, тест красный).
8. `search.invalidate_vectors()` вызывается из `index_document` при замене чанков (чтение диффа
   или тест через monkeypatch счётчика).

## Фоновая перепроверка

9. Тест: `documents.refresh_all` после изменения файла возвращает `reindexed == 1`, `revision`
   документа выросла; второй вызов — `reindexed == 0`; для задачи `status='done'` изменение
   файла ревизию не меняет.
10. `start_embed_worker` вызывает `refresh_all` до `embed_pending`, ошибка в нём не
    прерывает цикл (чтение диффа: вызов внутри существующего `try`).

## RRF и деградация не тронуты

11. `RRF_K == 60`, формула `1.0 / (RRF_K + rank)`, `_snippet`, `fts_query`, `fts_query_or`,
    `SNIPPET_CHARS` не изменены (чтение диффа `listik/search.py`); слияние `info` выполнено как
    `{**vec_item, **lex_item}` (лексический элемент главнее), а не перезаписью.
12. Ветка `except Exception` вокруг `embed.ollama_embed` в `vector()` и `search_memories`
    сохранена (чтение диффа).

## Границы

13. `git diff --stat HEAD` не содержит `bin/listik`, `listik/mcp.py`, `listik/client.py`,
    `listik/db.py`, `alembic/`, `API.md`, `README.md`; `documents.context` не изменена.
14. Тесты порций a и b зелёные.
