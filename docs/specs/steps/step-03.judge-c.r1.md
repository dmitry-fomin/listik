# Приёмка порции 03.c, заход r1 — вердикт: красный

Чек-лист: `docs/specs/steps/step-03.check-c.md`, порция: `docs/specs/steps/step-03.c.md`,
дифф: `.git/feature-pipeline/step-03.diff-c.r1.txt` (API.md, CLAUDE.md, README.md, bin/listik).

Коммита нет: один красный пункт.

## Красное

**Пункт 15.** `grep -n "import-writerllm" README.md API.md CLAUDE.md` даёт совпадения только
в двух файлах из трёх: `README.md` — 7, `API.md` — 1, `CLAUDE.md` — **0**.
`CLAUDE.md:78-83` называет только модуль (`listik/import_writerllm.py`) и флаг (`--update`),
имени команды `import-writerllm` в файле нет нигде. Та же проверка стоит и в самой порции
(`step-03.c.md`, блок «Как проверить»: `grep -n "import-writerllm" README.md API.md CLAUDE.md
# есть во всех трёх`), то есть требование не выполнено, а не разошлось между бумагами.

## Предусловие

0. `./bin/listik import-writerllm --help` показывает `--update` и `--project` (по умолчанию
   `writerllm`); `grep -n "старый ID" bin/listik` → `bin/listik:143`. HEAD = `94ac384`. ✔

## Зелёное (прогоны)

Все живые прогоны — на новых временных базах, `LISTIK_DB=` в той же строке, `--local init`.

1. `grep -n "^## Импорт" README.md` → `205:## Импорт из beads`, `221:## Импорт WriterLLM`,
   две строки подряд в нужном порядке. ✔
2. `README.md:223-231` — dolt-трекер `~/Agents/WriterLLM/.beads` вне `~/Projects`,
   `import-beads` его не видит; `bd export -o /tmp/writerllm-export.jsonl` с прямым
   предупреждением про личные данные и «в git не кладётся»; отдельной строкой `bd list --json`
   без комментариев. ✔
3. Основной `sh`-блок (`README.md:233-238`) — четыре вызова с комментариями. Прогон с
   `--source tests/fixtures/writerllm/export-v2.jsonl`:
   - `--dry-run` → `rc=0`, `создано: 7`, `(dry-run: ничего не записано)`,
     `select count(*) from tasks` = **0**;
   - импорт → `rc=0`, `создано: 7`, в базе 7 задач;
   - повтор → `rc=0`, `создано: 0 … пропущено: 7`;
   - `--update` той же выгрузки → `rc=0`, `обновлено: 0`, не падает.
   README описывает нормальный импорт как код `0` и иллюстрирует его отдельно от `export.jsonl`. ✔
4. `README.md:240-243` перечисляет старый ID (`id` + `external_ref`), тексты, статус с
   `closed→done`, приоритет, даты, исполнителя, метки, комментарии, связи, результат.
   Сверка: `show WL-a1` печатает `старый ID (writerllm): WL-a1`, даты из фикстуры
   (`создана 2024-01-01T09:00:00Z`, `закрыта 2024-01-06T09:00:00Z`, `причина: workaround
   shipped`), метки `core, imported`, историю из 2 комментариев и связь
   `от неё зависят: WL-a1.1`. ✔
5. `README.md:245-249` — `skip` по ключу `(source, project, external_ref)`, дозапись
   комментариев/связей, «ничего не удаляется», `--update` меняет поля содержания и пишет
   `[import-writerllm] update:`, ручные комментарии не трогает. Сверка на живой базе
   (импорт `export.jsonl`, затем `--update` от `export-v2.jsonl`): `обновлено: 2`,
   `update WL-a2 (title)`, `update WL-a5 (status)`, комментарий
   `journal | [import-writerllm] update: title: Добавить пагинацию списков → …в API`. ✔
6. `README.md:251-259` — счётчики и примеры в тексте, `--json` целиком с `raw` у ошибок,
   код `1` при ошибках, что именно считается ошибкой и что при этом происходит. Сверка:
   `export.jsonl` → `rc=1`, `создано: 7`, `select count(*) from tasks` = 7, ровно одна строка
   `  ! запись WL-a1.1, связь → WL-missing: target task not found`;
   `broken.jsonl --dry-run` → `rc=1` со строкой `! строка 2: invalid JSON on line 2: …`
   (и без `--dry-run`, как в README, тоже `rc=1`). ✔
7. `README.md:261-262` — `listik show WriterLLM-xxxx` / `search … --mode text` с оговоркой, что
   `WriterLLM-xxxx` условный, а в фикстурах `WL-a1`; реальных ID нет. Сверка: `show WL-a1` →
   `старый ID (writerllm): WL-a1`, `search WL-a1 --mode text` → `найдено: 1`, `WL-a1`. ✔
8. `README.md:264-265` — фраза про старые `in_progress` без держателя со ссылкой на соседний
   раздел. ✔
9. Раздел «Импорт из beads» в диффе только контекстными строками, ни одной правки. ✔
10. `API.md:55-56`: `source` → `native | beads | writerllm`, `external_ref` → «старое ID в
    beads/WriterLLM». ✔
11. `API.md:250` — строка `listik import-writerllm --source <path> …` сразу после
    `import-beads`; таблица эндпоинтов в диффе не тронута. ✔
12. `CLAUDE.md:78-83` различает источники (`.beads/issues.jsonl` под `~/Projects` против
    `bd export` dolt-трекера вне `~/Projects`), называет ключ `(source, project, external_ref)`,
    `--update` с diff в журнал и `tests/test_import_writerllm.py`; остальной файл не изменён
    (в т.ч. «There is no automated Python test suite» — по §4 порции не трогать). ✔
13. `import-writerllm --help` печатает `description` с «напрямую» (`grep -c` = 1);
    `grep -ci "markdown"` = 0. В диффе `bin/listik` тронут только блок
    `add("import-writerllm", …)`: добавлен `description=`, изменён `help=` у `--source`;
    текст `help=` подпарсера прежний (менялась только `)` → `,`), `--project`, `--dry-run`,
    `--update`, `cmd_import_writerllm`, `show_task` не тронуты. ✔ с оговоркой ниже.
14. `git diff --stat HEAD -- . ':!docs/specs'` → ровно `API.md`, `CLAUDE.md`, `README.md`,
    `bin/listik`; `git status --porcelain -- listik tests web AGENTS.md
    docs/harness-protocol.md` пуст. ✔ с оговоркой ниже.
16. В диффе нет имён, e-mail, токенов и содержимого `config.toml` (строки про `config.toml` в
    хунке `CLAUDE.md` — неизменённый контекст, значений в них нет); следов запуска
    `bd export`/`bd list` нет. ✔
17. `python3 -m unittest discover tests` → `Ran 102 tests … OK`. `stat -f %m listik.db` до и
    после приёмки — `1789220185`, живая база не тронута. ✔

## Оговорки, не влияющие на вердикт

- **Пункт 13, буквальный grep.** `grep -ci "markdown\|каталог"` даёт `1`, а не `0`: справка
  `--source` кончается словами «(файл; каталог не принимается)». Это ровно та формулировка,
  которую предписал §5 порции («JSONL из `bd export` или JSON-массив из `bd list --json`
  (файл; каталог не принимается)»), и собственная проверка порции грепает только `markdown`
  (даёт `0`). Слово «каталог» стоит в отрицании, обещания каталога в справке нет — требование
  выполнено, расходится лишь механика чек-листа с текстом ТЗ.
- **Пункт 14, «не больше двух изменённых строк» в `bin/listik`.** `git diff --numstat` даёт
  `5 2`: обе правки (kwarg `description=` и `help=` у `--source`) перенесены по ширине строки.
  Логических правок ровно две, обе в блоке `add("import-writerllm", …)`, как и требует §5
  («две строки в блоке»).

## Перенесено на приёмку шага

Нет.
