# Приёмка порции 03.c. README, API.md, CLAUDE.md про импорт WriterLLM

## Предусловие

0. Порции a и b закоммичены (94ac384): `./bin/listik import-writerllm --help` показывает
   `--update` и `--project` (по умолчанию `writerllm`); `grep -n "старый ID" bin/listik` непуст.
   Иначе приёмка не проводится.

Все живые прогоны — на **новой** временной базе (`DB=$(mktemp -d)/listik.db; LISTIK_DB=$DB
./bin/listik --local init`), каждая команда `./bin/listik` с явным `LISTIK_DB=$DB` в той же
строке. Фикстуры ведут себя по-разному, судья это учитывает: `export-v2.jsonl` чистая (код `0`),
`export.jsonl` даёт одну ошибку `dependency` (`WL-a1.1 → WL-missing`) и код `1` при созданных
задачах, `broken.jsonl` — битый JSON и код `1`.

## `README.md` (чтение + живой прогон примеров)

1. Есть раздел `## Импорт WriterLLM` сразу после `## Импорт из beads`
   (`grep -n "^## Импорт" README.md` — две строки подряд в этом порядке).
2. В разделе сказано, откуда данные (beads на dolt в `~/Agents/WriterLLM`, вне `~/Projects`,
   `import-beads` не видит) и как выгрузить (`bd export -o <файл вне репозитория>`), с
   предупреждением, что файл содержит личные данные и в git не кладётся.
3. Основной `sh`-блок содержит четыре вызова `import-writerllm`: `--dry-run`, обычный, повторный,
   `--update`, каждый с комментарием. Судья прогоняет их на новой базе, подставив
   `--source tests/fixtures/writerllm/export-v2.jsonl`, и сверяет коды по отдельности:
   dry-run → код `0`, база пуста (`select count(*) from tasks` = 0); импорт → код `0`, задачи
   созданы; повтор → код `0`, `создано: 0`; `--update` той же выгрузки → код `0`, `обновлено: 0`,
   не падает. Если исполнитель прогонял их на `export.jsonl` и описал в README нормальный импорт
   как завершающийся кодом `1` — красный пункт.
4. Перечислено, что сохраняется: старый ID (`id` + `external_ref`), тексты, статус с маппингом
   `closed→done`, приоритет, даты, исполнитель, метки, комментарии, связи, результат —
   сверка: `LISTIK_DB=$DB ./bin/listik --local show WL-a1` после импорта фикстуры показывает
   старый ID, даты из фикстуры и связи.
5. Описаны правила повтора: `skip` по умолчанию, дозапись комментариев/связей, `--update` меняет
   только поля содержания и пишет `[import-writerllm] update:` в журнал, ручные комментарии не
   трогаются, ничего не удаляется.
6. Описан отчёт: счётчики и примеры в тексте, `--json` с `raw` у ошибок, код возврата `1` при
   ошибках — и сказано, какие ошибки бывают и что при них происходит: битая строка/запись без
   заголовка пропускается, остальные импортируются; связь на отсутствующую задачу — задача
   создана, ребро нет, ошибка `dependency`; в обоих случаях код `1`. Сверка обеими фикстурами на
   новой базе: `LISTIK_DB=$DB ./bin/listik import-writerllm --source
   tests/fixtures/writerllm/export.jsonl; echo $?` → `1`, при этом `select count(*) from tasks`
   > 0 и в выводе одна строка `  ! ` про `WL-missing`; `… --source
   tests/fixtures/writerllm/broken.jsonl --dry-run; echo $?` → `1` со строкой про битый JSON.
   Код `1` на `export.jsonl` — не признак сломанной выгрузки, а именно та ошибка `dependency`,
   которую README описывает.
7. Есть пример поиска старой задачи через `show` и `search … --mode text` с фикстурным или
   абстрактным ID (реальных `WriterLLM-<суффикс>` нет); сверка: после импорта `export-v2.jsonl`
   `LISTIK_DB=$DB ./bin/listik --local show WL-a1` печатает `старый ID (writerllm): WL-a1`,
   `LISTIK_DB=$DB ./bin/listik --local search WL-a1 --mode text` находит `WL-a1`.
8. Есть фраза про старые `in_progress` без держателя (импортируются как есть, «нужен ты»).
9. Раздел «Импорт из beads» в диффе не изменён (кроме, возможно, одной ссылки).

## `API.md`

10. Строка `external_ref` в таблице полей упоминает WriterLLM; если есть строка `source`, в ней
    есть `writerllm`.
11. В разделе «CLI» есть строка `listik import-writerllm --source <path> …` рядом с
    `import-beads`; таблица эндпоинтов не изменена (диф).

## `CLAUDE.md`

12. Описание `listik/import_beads.py` / `listik/import_writerllm.py` различает источники
    (`.beads/issues.jsonl` под `~/Projects` против `bd export` WriterLLM), называет ключ
    идемпотентности `(source, project, external_ref)`, `--update` и тесты
    `tests/test_import_writerllm.py`; остальной файл в диффе не изменён.

## Справка подкоманды

13. `./bin/listik import-writerllm --help` печатает описание с фразой, что команда работает с
    базой напрямую и `--local`/сервер не нужны (`./bin/listik import-writerllm --help | grep -c
    "напрямую"` ≥ 1), а справка `--source` говорит про JSONL из `bd export`/JSON-массив из
    `bd list --json` и не упоминает Markdown и каталог (`./bin/listik import-writerllm --help |
    grep -ci "markdown\|каталог"` → `0`). В диффе `bin/listik` — только блок
    `add("import-writerllm", …)`: добавленный kwarg `description=` и изменённый `help=` у
    `--source`; `git diff HEAD -- bin/listik` не трогает `help=` подпарсера, `--project`,
    `--dry-run`, `--update`, `cmd_import_writerllm`, `show_task` (чтение диффа).

## Границы

14. `git diff --stat HEAD -- . ':!docs/specs'` показывает ровно `README.md`, `API.md`,
    `CLAUDE.md`, `bin/listik` (в последнем — не больше двух изменённых строк, обе в блоке
    `import-writerllm`); `git status --porcelain -- listik tests web AGENTS.md
    docs/harness-protocol.md` пуст.
15. `grep -n "import-writerllm" README.md API.md CLAUDE.md` даёт совпадения во всех трёх.
16. В диффе нет имён людей, e-mail, токенов, содержимого `config.toml`; нет следов запуска
    `bd export`/`bd list` в отчёте исполнителя (это порция d).
17. `python3 -m unittest discover tests` зелёный; `stat -f %m listik.db` до и после приёмки
    совпадает (живая база не тронута).
