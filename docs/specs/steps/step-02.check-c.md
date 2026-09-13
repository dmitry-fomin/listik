# Приёмка порции 02.c. README, API.md, CLAUDE.md

## Предусловие

0. Порции a и b закоммичены: `grep -n "def set_needs_owner" listik/store.py` и
   `grep -n "def body" listik/migrate.py` непусты; `migrate.upsert(Path("AGENTS.md"), dry_run=True)`
   → `unchanged`. Иначе приёмка не проводится.

## `API.md` (чтение файла + сверка с живым выводом на временной базе)

1. Строка `POST /api/tasks/{id}/needs-owner` описывает поля `value`, `note`, `actor`, `harness`
   и поведение: комментарий `question`/`answer` при непустом `note`, событие при каждом вызове,
   даже при уже поднятом флаге; ответ — карточка. Судья сверяет живым вызовом
   `./bin/listik --local needs-owner <id> "Q" --json` дважды: в `comments` два `question`,
   ключа `unchanged` нет.
2. Есть оговорка, что `PATCH /api/tasks/{id}` с `needs_owner` меняет только флаг без
   комментария; проверка: `./bin/listik --local set <id> needs_owner=0` не добавляет комментариев.
3. У `POST /api/tasks/{id}/comment` пояснено, что `question|answer` — те же виды, что пишет
   `needs-owner`, и что ручной комментарий флаг не меняет (сверка: `comment <id> "x" -k question`
   не меняет `needs_owner`).
4. В разделе «CLI» есть `listik needs-owner <id> --clear "ответ"`.
5. Раздела «Правила работы агента с задачами» в прежнем виде нет: нет нумерованного списка из
   семи пунктов, начинающегося с «Перед работой посмотри `listik ready`»; есть ссылка на
   `docs/harness-protocol.md` и упоминание, что тот же текст ставит `init-projects` в
   `AGENTS.md`; оставшиеся API-факты (claim не берёт заблокированную, handoff снимает держателя,
   красный вердикт возвращает на `s3-impl`) соответствуют `store.claim`, `store.next_stage`,
   `store.add_comment` (проверяется чтением кода).

## `README.md`

6. В `sh`-блоке раздела «Работа агента с задачей» есть строка `needs-owner <id> --clear "ответ"`.
   В разделе «Контекст этапа» (именно там, а не в «Работа агента с задачей») нумерованного
   списка «Правила, без которых доска врёт» нет (`grep -n "Взял задачу — \`claim\`" README.md`
   пуст), на его месте ссылка на `docs/harness-protocol.md` и фраза про блок в `AGENTS.md`
   проектов; абзац «Одна задача — один держатель» в «Контексте этапа» остался; остальной текст
   раздела (пути документов, примеры `context`) не изменён (чтение диффа).
7. В «Этапы конвейера (feature-pipeline)» описаны sticky/handoff по переходам и возврат по
   красному вердикту; список переходов совпадает с `config.DEFAULTS["routing"]["transitions"]`
   (`s1→s2` sticky, `s2→s3` handoff, `s3→s4` sticky, `s4→done` handoff). Судья проверяет handoff
   живым вызовом: задача на `s2-review` с держателем → `stage` → `holder` пуст.
8. В «Перевод проектов на Listik» сказано, что блок содержит протокол из
   `docs/harness-protocol.md` и что повторный `init-projects` обновляет его идемпотентно.
   Сверка **чтением и без записи**: `python3 -c 'from listik import migrate; print(migrate.block())'`
   (из корня репозитория) содержит десять правил протокола; `migrate.upsert(Path("AGENTS.md"),
   dry_run=True)` → `unchanged`. `init-projects` судья запускает **только с `--dry-run`**;
   запуск без него правит чужие репозитории и приёмкой не предусмотрен.
9. В «Агентам: MCP и CLI» есть фраза, что вопрос через `listik_needs_owner`/`needs-owner`
   попадает в историю карточки и находится `search`; сверка: `search "<слово из вопроса>" --mode text`
   находит задачу.
10. Все примеры команд, добавленные или изменённые в диффе `README.md`, выполняются на временной
    базе (`LISTIK_DB=/tmp/…`, `--local`) без ошибок — судья прогоняет каждый добавленный,
    **кроме** `init-projects` и `init-projects --remove`: их не запускать (правят файлы чужих
    проектов, временной базой не изолируются); для них достаточно пункта 8. Следы запуска
    `init-projects` без `--dry-run` в отчёте исполнителя или судьи — красный пункт.

## `CLAUDE.md`

11. В описании `listik/migrate.py` сказано, что тело блока читается из
    `docs/harness-protocol.md`; остальной текст файла в диффе не изменён.

## Границы

12. Границы — по списку путей, а не по «только три файла в диффе» (дерево на старте уже
    содержит бумаги конвейера в `docs/specs/**` и журнал шага):
    `git diff --stat HEAD -- . ':!docs/specs'` показывает ровно `README.md`, `API.md`,
    `CLAUDE.md`; `git status --porcelain -- listik bin tests web AGENTS.md docs/harness-protocol.md`
    пуст (ни изменённых, ни неотслеживаемых файлов в этих путях). Изменения внутри
    `docs/specs/**` порцией не считаются и не оцениваются.
13. `grep -n "harness-protocol" README.md API.md CLAUDE.md` даёт совпадения во всех трёх файлах.
14. В `README.md` и `API.md` нет второй копии десяти правил протокола (`grep -c "Чужую карточку не переписывай"`
    в каждом из двух файлов → `0`).
15. `python3 -m unittest discover tests` зелёный; в диффе нет токенов и содержимого `config.toml`.
