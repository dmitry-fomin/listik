# Приёмка порции 01.d, заход r1

Вердикт: **зелёный**.

Проверено в рабочем дереве `/Users/dmitry.fomin/Projects/Listik`, дифф —
`.git/feature-pipeline/step-01.diff-d.r1.txt` (`bin/listik`, `listik/client.py`,
`listik/documents.py`, `listik/server.py`, новый `tests/test_context.py`; 604 вставки).

## Прогон

```
python3 -m unittest discover tests -v   → Ran 64 tests, OK (1.27 s)
```

Сценарии CLI — временная база `--local` в scratchpad, задача с `--spec` = копия
`tests/fixtures/long-spec.md` (30 528 символов) и `--checklist` = `tests/fixtures/checklist.md`,
блокер с держателем, `dep add --confirm`, строка `suggested-blocks`, комментарии
review ×2 / journal / verdict, три перехода `stage`, временный git-репозиторий для worktree.

Контроль «до правки — красный»: `git worktree add --detach` на `HEAD` (6e033e5) + скопированный
`tests/test_context.py` → 16 из 20 тестов модуля падают (12 FAIL, 4 ERROR). Красными на старом
коде, в частности, были `test_s1_has_every_chunk_of_spec_and_checklist` (пункт 5),
`test_task_has_no_detail_keys_at_any_stage` / `test_s1_hides_review_and_verdict_text` /
`test_s3_hides_first_review_keeps_last` (пункт 7а), `test_no_age_or_stale_fields_anywhere` /
`test_dependencies_has_no_reasons_key` / `test_no_age_phrases_in_serialized_response` (18а) и все
тесты слоёв s3 (9–12). Зелёным на старом коде остался `test_repeated_calls_are_byte_stable` —
это ровно то, о чём говорит пункт 18б: два вызова подряд стабильность во времени не доказывают,
поэтому пункт зачтён только вместе с 18а и 7а. Временное дерево удалено
(`git worktree remove`), рабочее дерево не трогалось.

## Пункты чек-листа

1. **Зелёный.** Набор верхнеуровневых ключей на s1/s2/s3/s4 одинаков и равен
   `task, card, stage, portion, documents, chunks, acceptance, dependencies, reviews, verdict,
   journal, worktree, limits, reasons, generated_at` — проверено собственным скриптом поверх
   CLI `--format json` на всех четырёх этапах (плюс тест
   `test_same_key_set_at_every_stage`). Паритет держится и когда `deps_state is None`:
   `_DEPENDENCIES_DEFAULTS` (`listik/documents.py:407`) даёт те же 15 ключей, что `deps.ready`,
   а `hard`/`suggested` добавляются в обеих ветках `_dependencies` (`documents.py:414-436`).
2. **Зелёный.** На всех этапах в `documents[]` нет ключа `chunks` (`documents.py:661`,
   `docs_out = [{k: v … if k != "chunks"}]`), есть `status`, `error`, `revision`, `chunk_count`,
   `path`, `kind`. У каждого элемента `chunks[]` набор ключей —
   `id, document_id, kind, path, ordinal, heading, breadcrumb, text, start_line, end_line,
   token_count, content_hash, reason` (проверено на живом ответе, не по диффу).
3. **Зелёный.** `reasons[]` на всех четырёх этапах содержит блоки `card`, `acceptance`,
   `dependencies` (`documents.py:663-676`), число записей `block == "chunk"` равно длине
   `chunks[]`, множества `chunk_id` совпадают с множеством `id` чанков — проверено на всех
   этапах, не только на s1.
4. **Зелёный.** Задача без документов (CLI `new` без `--spec`): все четыре этапа отвечают без
   исключений, `documents == []`, `chunks == []`, `limits.truncated` ложь, `worktree` на s4 —
   `{"exists": false, "reason": "worktree не задан в карточке"}`. `--format text` на такой
   задаче тоже не падает.
5. **Зелёный.** s1 без `--max-chars` на файле 30 528 символов: 14 чанков (сумма `chunk_count`),
   `limits = {max_chars: 150000, default_for_stage: 150000, used_chars: 32260,
   truncated: false, dropped_chunks: 0, reason: "все выбранные блоки помещены"}`. На HEAD этот
   же вызов давал обрез по 24000 — соответствующий тест красный до правки (см. выше).
6. **Зелёный.** `limits.default_for_stage` — 24000 на s3 и s4 (`STAGE_MAX_CHARS`,
   `documents.py:21`). `--max-chars 5000` на s1: `truncated: true`, `dropped_chunks: 8`,
   `reason` = «документ spec «…/spec-d.md» не поместился целиком: обрезан начиная со строки 17»
   — содержит и путь spec, и `truncated_chunks[0].start_line == 17`.
7. **Зелёный.** s1: `reviews == []`, `verdict is None`, `journal == []`, `worktree is None`;
   число чанков равно сумме `chunk_count` по документам; reason каждого чанка начинается с
   «полный документ» (проверено перебором всех 14, не только тестом на количество).
7а. **Зелёный.** `store.get_task(..., with_details=False)` (`documents.py:588`), затем
   `_stable` и `stable_task.pop("deps_state")` (`documents.py:657-658`). На всех этапах в `task`
   нет `comments`, `events`, `documents`, `dependencies`, `dependents`, `deps_state`. На s1 в
   `json.dumps(ответ, ensure_ascii=False)` нет ни текста вердикта, ни текстов обоих review; на
   s3 нет текста первого review, а текст последнего есть.
8. **Зелёный.** s2 отдаёт оба review-комментария (CLI: длины `reviews` по этапам — 0 / 2 / 1 / 1).
9. **Зелёный.** s3 без порции: чанки `checklist` — все, reason «чек-лист целиком»; чанки `spec`
   — только с reason «лексическое совпадение с заголовком и acceptance, ранг N», среди них
   «Первый подраздел» — раздел, из которого взяты слова acceptance (ранг 1). Всего 4 чанка
   против 14 на s1.
10. **Зелёный.** `--portion "первый ПОДРАЗДЕЛ"` (нарочно другой регистр) → единственный чанк
    spec с heading «Первый подраздел» и reason «совпадение с порцией «первый ПОДРАЗДЕЛ»»
    (`documents.py:536-545`).
11. **Зелёный.** `--portion "такого раздела нет"` → чанки spec с reason «лексическое
    совпадение…, ранг 1/2».
12. **Зелёный.** Задача с пустым acceptance и заголовком без совпадений («zzz нетокенов»):
    все чанки — с reason «начало документа: совпадений по порции и лексике нет».
13. **Зелёный.** s3: `reviews` — ровно один, последний по `created_at`
    (`reviews_all[-1:]`, `documents.py:646-647`). `dependencies.hard` — одна запись
    `{depends_on, dep_type: "blocks", status, title}`, `suggested == []`, строки
    `suggested-blocks` в `hard` нет (запрос фильтрует по `deps.HARD_BLOCKERS`,
    `documents.py:421-425`). На s1 `suggested` при этом непуст — форма ключа сохраняется.
14. **Зелёный.** s4: `verdict` — последний verdict-комментарий; у каждого элемента `journal`
    есть `ts, kind, actor, text, id, from, to`; есть элемент `kind == "journal"` с id
    комментария и элементы `kind == "stage"` с `from`/`to` и id вида `event:<n>`; список равен
    `sorted(journal, key=(ts, kind, id))` — проверено на живом ответе CLI.
15. **Зелёный.** Временный репозиторий с одним коммитом и изменённым `a.txt`:
    `exists: true`, `git: true`, `head` — 40 hex-символов, `branch: "main"`,
    `changed_files: ["M a.txt"]`, `diff_stat: "a.txt | 2 +-\n 1 file changed, …"`.
15а. **Зелёный.** `git init` без коммитов: `head is None`, `diff_stat == ""`, `changed_files`
    содержит файл, `reason == "нет коммитов"`, исключений нет. Отдельно отмечу корректную
    деталь: `rev-parse HEAD` в пустом репозитории печатает в stdout саму строку `HEAD`, и
    `store._git_value` вернул бы её как «значение»; реализация отсекает это проверкой
    `re.fullmatch(r"[0-9a-f]{40}")` (`documents.py:454-455`) и отдельно `branch_raw == "HEAD"`.
16. **Зелёный.** Без `tasks.worktree` — `{"exists": false, "reason": "worktree не задан в
    карточке"}`; несуществующий каталог — `exists: false`, `reason: "каталог не найден"`;
    каталог без git — `exists: true, git: false`. Исключений нет ни в одном случае.
17. **Зелёный.** CLI: `set $TID worktree=<git-каталог>` → `context --stage s4-judge
    --format json` печатает `worktree.exists == true`, `head` — полный хеш, `changed_files`
    непуст.
18. **Зелёный.** Два подряд вызова CLI для каждого из четырёх этапов — `cmp` побайтно равен.
18а. **Зелёный.** Задача с незакрытым жёстким блокером, у которого проставлен держатель
    (`claim`): рекурсивный обход ответа не находит ключей на `_age`/`_hours` и ключей `stale`,
    `stale_holder`, `abandoned`; в `dependencies` нет ключа `reasons`; при этом
    `dependencies.blocked_by` непуст (одна запись с `holder: "human-holder"`), а
    `dependencies.verdict` — строка «нельзя: ждёт другие задачи». Проверка не на пустом объекте.
18б. **Зелёный.** Зачтено вместе с 18а и 7а; на HEAD тест «два вызова подряд» был зелёным и
    сам по себе ничего не доказывал — это подтверждено прогоном на старом коде.
19. **Зелёный.** `--format text` на s3 печатает заголовок, карточку с путями, `acceptance`,
    блок «зависимости» с `hard[]` и `verdict`, документы строкой `## <title> [kind] rev N
    статус ok`, чанки заголовком `### <breadcrumb> (<path>:<start>-<end>) — <reason>`; на s4 —
    ещё reviews, verdict, journal и строку `worktree: …`; при `--max-chars 5000` последняя
    строка — `[контекст обрезан: <limits.reason>]`.
20. **Зелёный.** `context $TID --stage s1-spec --json` и `--format json` дают побайтно
    одинаковый вывод (`cmp`).
21. **Зелёный.** `git diff --stat HEAD` — ровно `bin/listik`, `listik/client.py`,
    `listik/documents.py`, `listik/server.py` (+ новый `tests/test_context.py`); `search.py`,
    `embed.py`, `db.py`, `alembic/`, `mcp.py`, `API.md`, `README.md` не тронуты. В
    `documents.py` тела `split_markdown`, `index_document`, `refresh_all` сверены с версией
    `HEAD` через AST-выборку — побайтно идентичны; правки лежат только после `document_json`.
    `listik/store.py` не изменён вовсе — `_git_value` переиспользован как есть.
22. **Зелёный.** В `documents.context` и во всех новых хелперах нет обращений к `embed.*`,
    Ollama, `datetime.now`, `time.time`; единственные вызовы git — пять `store._git_value`
    внутри `_worktree` (`documents.py:449-471`), и все с путём, полученным из `tasks.worktree`
    после проверки `is_dir()`. `_worktree` вызывается только при `stage == "s4-judge"`.
23. **Зелёный.** Тесты порций a–c (`test_chunking`, `test_document_status`,
    `test_index_documents`, `test_search_chunks`) зелёные в общем прогоне 64 тестов.

## Срезанные углы: чего не нашлось

- Хардкода под тест нет: все проверенные значения воспроизводятся через CLI на независимо
  собранной базе, включая слои s3, лимиты и worktree.
- Тесты не подогнаны под реализацию: 16 из 20 красные на `HEAD`, и падают они по существу
  требований, а не по форме сообщений.
- Заглушек нет: слой «лексика» — реальный FTS-запрос к `document_chunk_fts` с
  `bm25`-сортировкой и фильтром по `d.kind IN ('spec','decision')`, а не фильтрация в Python.
- Правка в нужном слое: зачистка `task`/`dependencies` сделана в `documents.context`, а
  `store.get_task`/`deps.ready` не изменены — ровно как требуют «Границы правки».
- Секретов в диффе нет.

## Замечания без статуса (не блокируют, к исполнению не обязательны)

- `suggested[]` на s3/s4 обнуляется безусловно (`documents.py:436`), а не читается из таблицы.
  Это буквальное прочтение фразы ТЗ «`suggested[]` там пустой список», поэтому не красное;
  но если смысл был «к s3 предложений уже не остаётся», поведение разойдётся с данными.
- Слои 3 и 4 набирают все подходящие чанки и полагаются на общий обрез по лимиту, тогда как ТЗ
  говорит «пока не исчерпан лимит». Итоговый состав чанков тот же, отличается только то, что
  `limits.truncated` становится истиной вместо остановки слоя (наблюдалось на задаче без
  acceptance: 10 чанков из 12, `dropped_chunks: 2`).
- `--format text` печатает worktree как `repr` словаря Python одной строкой. ТЗ требует «блок
  worktree при наличии», формально выполнено, читаемость страдает.
- `task.stage_warn` — тоже производное от часов поле (`store.row_to_task:545`), и `_stable` его
  не вырезает, так как имя не оканчивается на `_hours`/`_age`. Поведение унаследовано от
  прежней версии `stable`, в чек-листе и ТЗ порции не упомянуто; на стабильность между двумя
  вызовами не влияет.

## Перенесено на приёмку шага

Нет.

## Коммит

`git add -- bin/listik listik/client.py listik/documents.py listik/server.py
tests/test_context.py` и коммит «Шаг 01, порция d: контракт context по этапам».
