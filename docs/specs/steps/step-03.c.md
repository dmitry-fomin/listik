# Порция 03.c. Документация импорта WriterLLM: README, API.md, CLAUDE.md

## Контекст

Listik — трекер задач на stdlib-Python. `README.md` — справочник команд для человека, `API.md` —
авторитетный контракт данных и эндпоинтов (в нём же раздел «CLI» со списком команд), `CLAUDE.md`
— заметки об архитектуре для агентов в этом репозитории. Порциями a и b появилась рабочая
команда:

```text
listik import-writerllm --source <path> [--project <slug>] [--dry-run] [--update] [--json]
```

Она читает JSONL из `bd export` (или JSON-массив `bd list --json`) beads-трекера WriterLLM
(`~/Agents/WriterLLM/.beads`, dolt-бэкенд — `issues.jsonl` в дереве нет, `import-beads` этот
каталог не видит), создаёт задачи с `source='writerllm'`, `project` = `--project` (по умолчанию
`writerllm`), `id` = старый ID (если свободен, иначе новый, старый — в `external_ref`), сохраняет
заголовки, описания, acceptance, design, notes, статус (маппинг `closed→done`, `deferred→open`),
приоритет, даты `created_at/updated_at/started_at/closed_at`, `close_reason` (и как `result`
у закрытых), исполнителя (нормализованного в актора), метки, комментарии, связи всех типов
(после задач, по новым ID) и регистрирует проект в `projects`. Повторный запуск — `skip` по ключу
`(source, project, external_ref)`, недостающие комментарии/связи дописываются; `--update` меняет
поля содержания и пишет журнальный комментарий `[import-writerllm] update:` с diff; ручные
комментарии не трогаются. `--dry-run` ничего не пишет. Код возврата `1`, если в отчёте есть
ошибки; `--json` печатает отчёт целиком (с сырыми ненормализованными записями). Команда работает с
базой напрямую, как `import-beads`. `show` печатает `старый ID (<source>): <external_ref>`.

**Предусловие: порции a и b закоммичены.** Проверка: `./bin/listik import-writerllm --help`
показывает `--update` и `--project` по умолчанию `writerllm`; `grep -n "старый ID" bin/listik`
непуст. Иначе порцию не начинать и вернуть задачу оркестратору.

Документация описывает **фактическое** поведение: каждый пример перед записью прогоняется на
временной базе (`LISTIK_DB=/tmp/…`) с фикстурами `tests/fixtures/writerllm/`. Реальную выгрузку
`bd export` в этой порции не делать и не читать: она содержит имя и e-mail владельца, в
репозиторий и в примеры не попадает.

**Две фикстуры ведут себя по-разному, и это надо знать до прогона примеров.**
`export.jsonl` — «грязная» по построению: запись `WL-a1.1` ссылается на несуществующую
`WL-missing`, поэтому каждый её импорт (dry-run, обычный, повторный, `--update`) даёт одну
ошибку `where="dependency"`, `ok=false` и код возврата `1`, хотя все задачи создаются
(закреплено тестами `test_20`, `test_23`). `export-v2.jsonl` — чистая: те же ID без висячей
связи и без записи-памяти, `WL-a2` с другим `title`, `WL-a5` со статусом `in_progress`, `WL-a3` с
дополнительным комментарием; на пустой базе даёт код `0` (`test_24`). Значит: «чистый успешный
импорт» иллюстрировать и проверять на `export-v2.jsonl`; код `1` и ошибку `dependency` — на
`export.jsonl`; битый JSON — на `broken.jsonl`.

Перед началом прочитай: `README.md` (разделы «Импорт из beads», «Поиск», «Агентам: MCP и CLI»,
«Структура базы», «Тесты»), `API.md` (таблица полей задачи — строка `external_ref`, раздел «CLI»),
`CLAUDE.md` (строки про `listik/import_beads.py`, `listik/import_writerllm.py`),
`listik/import_writerllm.py`, `bin/listik` — `cmd_import_writerllm`, `tests/test_import_writerllm.py`.

## Что сделать

### 1. `README.md` — новый раздел «## Импорт WriterLLM» сразу после «## Импорт из beads»

Содержание (своими словами, коротко, в стиле соседнего раздела):

- Откуда: задачи WriterLLM лежат в beads на dolt (`~/Agents/WriterLLM/.beads`), вне `~/Projects`;
  `import-beads` их не видит, поэтому сначала выгрузка, затем отдельная команда.
- Как выгрузить: `cd ~/Agents/WriterLLM && bd export -o <файл вне репозитория Listik>` —
  подчеркнуть, что файл содержит личные данные и в git не кладётся (пример пути —
  `/tmp/writerllm-export.jsonl`). `bd list --json > файл.json` — тоже подходит, но без
  комментариев.
- Как импортировать: `sh`-блок с `--dry-run`, затем без него, затем повторный запуск
  (даёт `создано: 0`), затем `--update` — четыре команды с однострочными комментариями.
- Что сохраняется (список из абзаца «Контекст» выше: старый ID как `id` и `external_ref`, тексты,
  статус с маппингом, приоритет, даты, исполнитель, метки, комментарии, связи, результат).
- Правила повторного запуска: `skip` по умолчанию, дозапись недостающих комментариев/связей,
  `--update` меняет только поля содержания и пишет `[import-writerllm] update:` в журнал, ручные
  комментарии не трогаются; ничего не удаляется.
- Отчёт: счётчики и примеры в тексте, полный отчёт в `--json` (к каждой ошибке приложена сырая
  запись `raw`), код возврата `1` при ошибках (подходит для CI); `--dry-run` не пишет ничего.
  Отдельной фразой — что считается ошибкой и что при этом происходит: битая строка JSON или
  запись без заголовка — пропущена, остальные импортированы; связь на задачу, которой нет в
  выгрузке и в базе (`target task not found`), — задача создана, ребро нет, ошибка
  `dependency` в отчёте; в обоих случаях код возврата `1`, а повторный запуск даёт ту же ошибку,
  пока источник не исправлен. Это иллюстрируется парой команд с фикстурами
  `tests/fixtures/writerllm/export.jsonl` (висячая связь → `1`) и `broken.jsonl` (битый JSON → `1`)
  в одном коротком `sh`-блоке или в тексте — на выбор, но с явным ожидаемым кодом.
- Как найти старую задачу: `listik show WriterLLM-xxxx`, `listik search WriterLLM-xxxx --mode text`
  (в примере — ID из фикстур, например `WL-a1`, а не реальный).
- Одна фраза: старые `in_progress` без держателя импортируются как есть и висят в линии
  «нужен ты» — так же, как у beads (ссылка на соседний раздел).

### 2. `README.md` — прочие места

- В разделе «Агентам: MCP и CLI» (или где перечислены команды CLI) — одна строка
  `import-writerllm`, если там перечислен `import-beads`; если нет — ничего не добавлять.
- В «Структура базы»/описании `source`, если поле `source` там упоминается как `native | beads`,
  дописать `writerllm`. Если не упоминается — не добавлять.

### 3. `API.md`

- Таблица полей задачи: строка `external_ref` — «старое ID в beads/WriterLLM», строка `source`
  (если есть) — значения `native | beads | writerllm`.
- Раздел «CLI»: после `listik import-beads [--dry-run]` добавить строку
  `listik import-writerllm --source <path> [--project writerllm] [--dry-run] [--update]   # импорт выгрузки bd export WriterLLM, идемпотентно`.
- Эндпоинтов у импорта нет — в таблицу эндпоинтов ничего не добавлять.

### 4. `CLAUDE.md`

Строку про `listik/import_beads.py, listik/import_writerllm.py` в разделе «Architecture»
переписать так, чтобы было видно различие: `import_beads` — обход `.beads/issues.jsonl` под
`~/Projects`; `import_writerllm` — JSON/JSONL из `bd export` (dolt-трекер WriterLLM вне
`~/Projects`), ключ идемпотентности `(source, project, external_ref)`, `--update` пишет diff в
журнал, тесты — `tests/test_import_writerllm.py`. Одно-два предложения; остальной файл не
трогать. Фразу «There is no automated Python test suite» в `CLAUDE.md` **не** править в этой
порции (её актуальность — отдельный вопрос, строка в отчёте исполнителя, если заметил).

### 5. Справка подкоманды в `bin/listik`

Сейчас фраза «работает с базой напрямую, `--local` не нужен» задана как `help=` подпарсера
`import-writerllm` и видна только в общем `./bin/listik --help`; на `./bin/listik import-writerllm
--help` её нет. Добавить в вызов `add("import-writerllm", …)` аргумент `description=` с той же
мыслью (одна строка: команда пишет в SQLite напрямую, как `import-beads`; запущенный сервер и
`--local` не нужны; HTTP-эндпоинта и MCP-инструмента у импорта нет).

Там же `--source` описан как «JSON/JSONL файл или каталог Markdown», хотя каталог с порции a
отвергается как источник (`test_12_directory_and_markdown_are_rejected_as_source`). Заменить
`help=` этого аргумента на фактическое: JSONL из `bd export` или JSON-массив из `bd list --json`
(файл; каталог не принимается). Итого в `bin/listik` — две строки в блоке `add("import-writerllm",
…)`: новый kwarg `description=` и текст `help=` у `--source`; `help=` подпарсера и остальных
аргументов не менять.

### 6. Проверка примеров

Каждая команда, добавленная в `README.md`/`API.md`, кроме `bd export`/`bd list` (чужой каталог),
выполняется на **новой** временной базе (`LISTIK_DB=$(mktemp -d)/listik.db`) с подстановкой
фикстуры вместо пути выгрузки, и её описанный эффект сверяется живым вызовом:

- dry-run / импорт / повтор / `--update` из основного `sh`-блока — с
  `tests/fixtures/writerllm/export-v2.jsonl`: код `0` у всех четырёх, после dry-run база пуста,
  на повторе `создано: 0`, `--update` на той же выгрузке — `обновлено: 0`;
- фраза про код `1` — с `export.jsonl` (одна ошибка `dependency`, задачи созданы, код `1`) и с
  `broken.jsonl` (битый JSON, код `1`);
- пример поиска — `show WL-a1` печатает `старый ID (writerllm): WL-a1`, `search WL-a1 --mode text`
  находит задачу.

Утверждение README, что `export.jsonl`-подобная выгрузка «с ошибкой» всё же импортирует задачи,
проверяется `select count(*) from tasks` после прогона.

## Границы правки

- Правятся: `README.md`, `API.md`, `CLAUDE.md` и **две строки** `bin/listik` в блоке
  `add("import-writerllm", …)` — kwarg `description=` подпарсера и `help=` аргумента `--source` (§5).
- Не менять другой код (`listik/**`, остальной `bin/listik` — включая `help=` подпарсера и
  остальных аргументов подкоманды, `cmd_import_writerllm`, `show_task`), тесты, фикстуры, схему,
  `AGENTS.md`, `docs/harness-protocol.md`, `docs/specs/**`, `project-skills/**`, `web/**`.
  Расхождение кода с ожиданием — строка в отчёте исполнителя, не правка кода и не «документация
  желаемого».
- Не переписывать разделы `README.md`/`API.md`, не относящиеся к импорту и полю `source`/
  `external_ref`; раздел «Импорт из beads» не трогать (кроме ссылки на него из нового раздела).
- Не запускать `bd export`/`bd list` в `~/Agents/WriterLLM` и не читать реальную выгрузку — это
  порция d. В примерах — только фикстурные ID (`WL-a1`) и абстрактный `WriterLLM-xxxx`.
- Никаких имён людей, e-mail, токенов, содержимого `config.toml`, личных путей кроме
  `~/Projects/Listik` и `~/Agents/WriterLLM`.

## Как проверить

Каждая команда `./bin/listik` — с явным `LISTIK_DB=…` в той же строке (без `export`).

```sh
python3 -m unittest discover tests                        # зелёные
git diff --stat HEAD -- . ':!docs/specs'                  # README.md, API.md, CLAUDE.md и bin/listik (две строки одного блока)
git status --porcelain -- listik tests web AGENTS.md      # пусто
grep -n "import-writerllm" README.md API.md CLAUDE.md     # есть во всех трёх
./bin/listik import-writerllm --help | grep -n "напрямую"  # фраза видна на --help подкоманды
./bin/listik import-writerllm --help | grep -ci "markdown"  # 0: --source больше не обещает каталог
DB=$(mktemp -d)/listik.db; LISTIK_DB=$DB ./bin/listik --local init
# прогнать четыре команды основного sh-блока README с tests/fixtures/writerllm/export-v2.jsonl — код 0 у всех
# затем export.jsonl → код 1 (одна ошибка dependency, задачи созданы) и broken.jsonl → код 1
LISTIK_DB=$DB ./bin/listik --local show WL-a1; LISTIK_DB=$DB ./bin/listik --local search WL-a1 --mode text
```
