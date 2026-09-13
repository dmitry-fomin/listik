# Приёмка порции 01.a — заход r1

Вердикт: **зелёный**. Все 14 пунктов чек-листа пройдены, дефектов и срезанных углов не найдено.

## Каркас

**1. `python3 -m unittest discover tests -v` → код 0.** 26 тестов, `OK`. Модули: `test_chunking`
(21 тест) и `test_index_documents` (5 тестов) — оба присутствуют. Сервер и Ollama не требуются:
тесты чанкера работают на чистых строках, тесты индексации — на `db.init(<tmp>/listik.db)`.

**2. Фикстуры.** `wc -c`: `long-spec.md` 55493 байта, `lists.md` 13649, `fenced.md` 317,
`checklist.md` 182. В символах (`len(str)`) `long-spec.md` — 30528 (>= 25 000). Раздел
`## Большой раздел` — 29933 символа (> 15 000), состоит из абзацев обычного текста, уникальная
фраза `ксилофонный-градиент-42` стоит в его середине (абзац 65 из 129) и встречается в
`tests/fixtures/` ровно один раз (`grep -rc`: 1 в `long-spec.md`, 0 в остальных трёх).

**3. Рабочая база не тронута.** До и после прогона `md5 listik.db` = `b72edc9b455ccb456aecf6762ea97252`,
`git status --porcelain` идентичен, `listik.log`/`listik.pid` не изменились, новых `.db` в корне нет.
`tests/helpers.py:22-23` передаёт путь явно (`db_mod.init(self.db_path)` во
`tempfile.TemporaryDirectory`), `paths.DB_PATH` нигде не переопределяется.

**4. `CLAUDE.md`.** `git diff CLAUDE.md` — ровно один хунк `@@ -23,9 +23,14 @@`: фраза
«There is no automated Python test suite» удалена, добавлен блок
`python3 -m unittest discover tests   # Python test suite` и строка про временную базу без
сервера и Ollama. Других разделов диff не касается.

## Чанкер: «красный до правки»

Приложенный исполнителем `red-baseline.txt` формально показывает `FAILED (errors=21)`, но все 21
падения — `AttributeError: module 'listik.documents' has no attribute 'split_markdown'`, то есть
отсутствие нового имени, а не дефекты резки. Это проба, доказывающая не то, что нужно (процедура
из ТЗ самопротиворечива: тесты зовут `split_markdown`, которого на `b757cf6` нет; ревью это уже
отмечало пунктом Б3). Поэтому я воспользовался правом чек-листа «при сомнении повторяет ту же
процедуру сам» и прогнал базовую ревизию напрямую через старое имя `_split_markdown`:

```
git worktree add /tmp/listik-base b757cf6
cp -R tests /tmp/listik-base/tests
# probe.py: documents._split_markdown на тех же фикстурах
FENCE headings: ['not heading', 'Блок кода', 'После кода', 'Раздел с кодом']
FENCE 'not heading' present as heading: True
LISTS chunks: 2  chunks not starting with marker: 1  mid-item continuation lines: 1
TAIL limit=200 chunk lens: [193, 385, 435]  max overflow: 235   (содержимое теста порции)
TAIL MAX_CHARS chunk lens: [5993, 6235, 6235]  max overflow: 235
git worktree remove --force /tmp/listik-base
```

Все три дефекта на базовой ревизии реальны, и все три теста порции на ней падают по существу:
`# not heading` внутри fence становился заголовком; длинный список резался посередине пункта;
чанк на overlap-хвосте доходил до `MAX_CHARS + 235` (теоретический потолок из ТЗ —
`limit + OVERLAP + 2 = MAX_CHARS + 242`, разница — от длины конкретного абзаца). Тесты
не подогнаны под реализацию: они ловят настоящие дефекты.

**5. Code-fence.** Тест `fenced` зелёный на текущем коде. Ручной прогон из ТЗ:
`split_markdown("# a\n\n```\n# not heading\n```\n\n## b\n\nтекст")` → два чанка, `heading` `a`
(с телом, включающим блок кода) и `b`. Реализация — `listik/documents.py:105-111`.

**6. Списки.** Независимая проверка `lists.md` на текущем коде: 2 чанка длиной 5949 и 1735;
каждый после префикса breadcrumb начинается с маркера; строк-продолжений, не начинающихся с
`- `, нет (0); первая строка чанка 2 не встречается в чанке 1 (overlap между группами — 0);
все 89 пунктов исходника сохранены ровно один раз и в исходном порядке. Реализация —
`_group_list_items` (`listik/documents.py:52-80`) и ветка `unit[0] == "list"` (`:170-181`).

**6а. Переполнение на overlap-хвосте.** На текущем коде `len(text) <= limit` для всех чанков
теста `OverlapTailBudgetTests` (limit=200). Бюджет — `listik/documents.py:195-198`:
`max_tail = max(0, chunk_limit - len(text) - 2)`, хвост укорачивается вплоть до нуля, поэтому
`len(prefix) + len(piece) <= limit` выполняется алгебраически, а не для конкретной фикстуры.
Сверх чек-листа прогнал рандомизированный property-тест (300 документов из абзацев, списков и
fence-блоков, лимиты 60/120/200/1000): ни одного превышения лимита, кроме документированного
исключения «слово длиннее лимита», ни одного нарушения `1 <= start_line <= end_line <= N`,
ни одного расхождения между двумя вызовами.

**7. Алиас.** `documents._split_markdown is documents.split_markdown` → `True`
(`listik/documents.py:217`). Во всём репозитории имя зовётся только из `index_document`
(`listik/documents.py:250`), сломанных вызовов нет.

**7а. Worktree убран.** `git worktree list` показывает только основное дерево,
каталога `.git/worktrees` не существует, `/tmp/listik-base` удалён.

## Свойства резки

**8. `long-spec.md`** (12 чанков, независимая проверка, не через тесты): у всех
`text.startswith(breadcrumb)`; у всех `1 <= start_line <= end_line <= 279`; максимальная длина
чанка 5846 <= 6000. Раздел > 15 000 символов даёт 6 чанков (>= 3). Уникальная фраза найдена
ровно в одном чанке, его `heading` — `Большой раздел`. Пар соседних чанков с символьным overlap
в этом разделе — 5 из 5 (требуется хотя бы одна). Политика overlap из §2 соблюдена: между
группами пунктов списка overlap отсутствует.

**9. Детерминизм.** Два вызова `split_markdown` на `long-spec.md` дают равные списки словарей;
то же подтверждено на 300 случайных документах.

## Идемпотентность индексации

**10-12.** Тесты `test_index_documents` прогнаны мной, все зелёные, и читаются как настоящие
проверки: повторная индексация не меняет `(documents, document_chunks, document_chunk_fts)`,
`revision` остаётся 1, `updated_at` совпадает, `document_error`-событий 0; после дописывания
раздела `revision == 2`, чанков больше, `fts_chunk_ids ⊆ chunk_ids`; после укорачивания
`max(ordinal)` равен новому числу чанков в обеих таблицах (сирот нет);
`store.create_task(..., spec_path=…, checklist_path=…)` создаёт ровно две строки `documents`
с `kind` `spec` и `checklist`, у обеих чанки > 0.

## Границы

**13.** `git diff --stat HEAD`: только `CLAUDE.md` и `listik/documents.py`; плюс новый
неотслеживаемый `tests/**`. `listik/db.py`, `alembic/`, `search.py`, `embed.py`, `server.py`,
`client.py`, `bin/listik` не изменены.

**14.** `MAX_CHARS = 6000` и `OVERLAP = 240` в диффе только как контекстные строки; хунки
диффа не доходят до `def context` (`listik/documents.py:292`) — функция не тронута; формат
`chunk_id = f"{doc_id}:{ordinal}"` и вычисление `content_hash` через `textutil.text_hash`
не изменены.

## Срезанные углы — не найдено

Хардкода под фикстуры в `listik/documents.py` нет: и группировка списка, и бюджет хвоста
выражены через `chunk_limit`/`OVERLAP` и подтверждены рандомизированным прогоном. Тесты не
ослаблены: каждый из трёх спорных проверяется дефектом, реально существовавшим на `b757cf6`.
Единственная лёгкая цикличность — `expected_new`/`expected_chunks` в `test_index_documents`
считаются вызовом самой `split_markdown`, но чек-лист в пунктах 11 требует лишь «чанков стало
больше» и «сирот нет», и это проверяется независимо (`fts ⊆ chunks`, `max(ordinal)`).

Секретов в диффе нет: коммитятся 10 путей — `CLAUDE.md`, `listik/documents.py` и семь файлов
`tests/**`; `.env`, `*.key`, `*.pem`, `credentials.json` и токенов не встречается,
`__pycache__` отсечён `.gitignore`.

## Перенесено на приёмку шага

Нет.
