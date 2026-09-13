# Приёмка порции 01.c, заход r1

Вердикт: **зелёный**.

Проверено в рабочем дереве `/Users/dmitry.fomin/Projects/Listik`, дифф —
`.git/feature-pipeline/step-01.diff-c.r1.txt` (4 файла, 317 вставок).

## Прогон

```
python3 -m unittest discover tests -v   → Ran 44 tests, OK (0.24 s)
```

Контроль «до правки — красный»: `git worktree add` на `HEAD` (e6b20f5) + скопированный
`tests/test_search_chunks.py` → 8 из 9 тестов модуля падают (6 ERROR, 2 FAIL); зелёным на
старом коде остаётся только тест деградации — он и должен быть зелёным, так как проверяет
сохранённое поведение, а не новое. Тесты порций a и b (`test_chunking`, `test_document_status`,
`test_index_documents`) зелёные — пункт 14.

## Пункты чек-листа

1. **Зелёный.** `test_text_search_finds_task_a_via_chunk_with_section_metadata`: `results[0]` —
   задача A, `best_hit.kind == "chunk"`, `heading == document_chunks.heading`, `breadcrumb`
   содержит корневой заголовок «Долгая спецификация», `path` = путь документа,
   `start_line`/`end_line` — int, равны строке `document_chunks`. Поля читаются одним запросом
   `document_chunks c JOIN documents d` (`listik/search.py:284-290`); `chunk_sql`
   (`search.py:110-121`) и схема `document_chunk_fts` (`listik/db.py`) не тронуты —
   `git diff --stat HEAD -- listik/db.py` пуст. На HEAD тест ERROR (`KeyError: 'best_hit'`).
2. **Зелёный.** `test_text_search_finds_task_b_by_title_with_null_chunk_fields` + проверка CLI:
   у task-хита все семь ключей присутствуют со значением `null`
   (`{"kind":"task",…,"heading":null,"breadcrumb":null,"document_id":null,"document_kind":null,"path":null,"start_line":null,"end_line":null}`).
3. **Зелёный.** CLI на временной базе (`LISTIK_DB` в скретчпаде):
   ```
   ./bin/listik --local search "Абзац номер 013 большого раздела" --mode text
    1. ○ listik-2pk8  [listik]
        поиск по чанкам
        раздел: Долгая спецификация / Большой раздел (spec /…/spec-c.md:17)
   ```
   `listik/search.py:431-440` (`print_results`).
4. **Зелёный с отступлением от буквы.** `test_vector_branch_degrades_silently_and_matches_text_mode`:
   фиктивный вектор чанка A в `embeddings` вставлен (база непуста), заглушка бросает —
   `mode="hybrid"` даёт тот же список id, что `mode="text"`, исключений нет.
   Счётчик проверяется на изолированном вызове `search.vector(...)` (`call_count == 1`), а не
   на полном `search.search(mode="hybrid")`. Обоснование исполнителя проверено эмпирически и
   верно: `search_memories` (`listik/search.py:200-204`) при `hybrid`/`vector` вызывает
   `embed.ollama_embed` безусловно, без раннего выхода на пустой таблице памяти — замер даёт
   `ollama_embed calls in one hybrid search.search(): 2`. Буквальное `mock.call_count == 1`
   после hybrid недостижимо без правки `search_memories`, что порция прямо запрещает. Смысл
   пункта — доказать, что ветка дошла до Ollama, а не вышла на `if not rows: return []` —
   выполнен: векторы вставлены, счётчик проверен.
5. **Зелёный.** `test_orphan_chunk_embedding_is_dropped_before_aggregation`: сирота
   `<doc>:9999` + реальный чанк; при векторе сироты `results == []`; `score` A с сиротой и без
   неё совпадает. Фильтр стоит до построения `ranks` (`search.py:291-292`), поэтому сирота не
   сдвигает и ранг реального чанка. На HEAD тест FAIL (задача находилась по сироте).
5а. **Зелёный.** `test_vector_hits_are_enriched_from_document_chunks`: `mode="vector"` →
   `best_hit.kind == "chunk"`, `heading`/`breadcrumb`/`path` непустые, `start_line` — int.
   На HEAD ERROR.
5б. **Зелёный.** `test_hybrid_merge_keeps_lexical_snippet_and_sums_rrf`: `snippet` содержит
   `[[…]]`, `heading`/`breadcrumb` непустые, `rrf` в hybrid строго больше, чем в text.
   На HEAD ERROR.
6. **Зелёный.** `listik/server.py:410` — `kinds=body.get("kinds", "task,comment,chunk")`.
7. **Зелёный.** `test_reindex_after_shrinking_file_drops_old_chunk_embeddings`: после
   укорачивания файла строк `embeddings` со старыми chunk-id нет. На HEAD — 12 строк, FAIL.
   Удаление стоит в ветке смены хеша (`listik/documents.py:270-277`), до вставки новых чанков.
8. **Зелёный.** `search.invalidate_vectors()` — `listik/documents.py:279`, сразу после удаления
   старых чанков и их embeddings. Циклического импорта нет: `search` не импортирует `documents`.
9. **Зелёный.** `RefreshAllTests`: изменённый файл → `reindexed == 1`, `revision` 1→2; повтор →
   `reindexed == 0`, ревизия та же; задача `status='done'` в выборку не попадает
   (`checked == 0`, ревизия не растёт). Отдельно проверена устойчивость к исключению одной
   задачи: `spec_path` = каталог → `refresh_all` возвращает
   `{'checked': 1, 'reindexed': 0, 'missing': 1}` и пишет событие `document_error`, не падая.
10. **Зелёный с отступлением от буквы.** `listik/server.py:78-85`: `refresh_all` вызывается
    до `embed_pending`, при `reindexed > 0` печатается `[documents] переиндексировано: M` и
    `publish("documents", res)`. Чек-лист ожидал вызов внутри существующего `try`; сделан
    отдельный `try/except` перед ним. Требование пункта («ошибка не прерывает цикл») выполнено
    строже: при падении `refresh_all` цикл не только продолжается, но и не теряет шаг
    `embed_pending` того же прохода. Считаю это выполнением, а не обходом.
11. **Зелёный.** `RRF_K = 60` (`search.py:15`), формула `1.0 / (RRF_K + rank)` (`search.py:299`),
    `_snippet`, `SNIPPET_CHARS = 220` не изменены; `fts_query`/`fts_query_or` живут в
    `listik/textutil.py`, который не тронут (`git diff --stat HEAD -- listik/textutil.py` пуст).
    Слияние — `info[key] = {**info[key], **i}`, где `info[key]` векторный, `i` лексический
    (`search.py:306-311`): лексический главнее, перезаписи нет.
12. **Зелёный.** `except Exception` вокруг `embed.ollama_embed` сохранён в `vector()`
    (`search.py:160-163`) и в `search_memories` (`search.py:201-204`).
13. **Зелёный.** `git diff --stat HEAD` — только `listik/documents.py`, `listik/search.py`,
    `listik/server.py` (+ новый `tests/test_search_chunks.py`). `bin/listik`, `listik/mcp.py`,
    `listik/client.py`, `listik/db.py`, `alembic/`, `API.md`, `README.md` не затронуты;
    `documents.context` в диффе не появляется.
14. **Зелёный.** См. прогон выше.

## Срезанных углов не найдено

- Тесты не трогают реальную Ollama: единственная заглушка — `patch.object(embed, "ollama_embed", Mock(...))`
  в `setUp`; `os.environ`, `LISTIK_OLLAMA_URL`, `LISTIK_EMBED_MODEL` в модуле не встречаются
  (проверено grep'ом). `search.invalidate_vectors()` — в `setUp`, в `addCleanup` и внутри
  `_insert_vec` после каждой записи в `embeddings`.
- `UNIQUE_PHRASE` взята из фикстуры (`tests/fixtures/long-spec.md:41`), не из кода реализации.
- Хардкода под тест, заглушек вместо реализации и правок не в том слое в диффе нет; обогащение
  сделано в одном месте для обеих веток, как требует §1 порции.
- Секретов в диффе нет.

## Перенесено на приёмку шага

Нет.

## Коммит

`Шаг 01, порция c: поиск и embeddings на уровне чанков` —
`listik/documents.py`, `listik/search.py`, `listik/server.py`, `tests/test_search_chunks.py`.
