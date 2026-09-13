# Приёмка порции 04.d, заход r1 — вердикт: зелёный

Проверял на временной базе `/tmp/claude-501/listik-step04d.db` (`./bin/listik --local init`),
плюс сервер на порту 8799 с той же базой — для HTTP-префикса ошибок. Реальная `listik.db`
(mtime 19:42 MSK, до захода) и `config.toml` (10 сент.) не тронуты; сервер 8799 остановлен.

## Пакет диффа

`git status --short` совпадает с пакетом: `AGENTS.md`, `API.md`, `CLAUDE.md`, `README.md`,
`docs/harness-protocol.md`, `listik/mcp.py`. Вне пакета изменён только
`docs/specs/listik-product.journal.md` — журнал шага (бумаги оркестратора), в коммит порции
не входит. Untracked — только `docs/specs/steps/step-0*.md`, бумаги конвейера.

## Чек-лист

### README.md

1. **зелёный.** `./bin/listik dep --help` перечисляет `{add,confirm,rm,tree,cycles,suggest,link,suggested}`;
   в README `dep confirm`, `dep suggested [--project]` (флаг `--project` у `dep` есть),
   у `dep rm` — «(и предложение — suggested-blocks — тоже)», что подтверждается
   `store.remove_dep` (`dep_type is None` → `IN ('blocks','suggested-blocks')`).
2. **зелёный.** Сценарий целиком прогнан:
   - `dep add demo-b70x demo-crft --actor agent:codex` → `предложение зависимости записано: … подтвердить: listik dep confirm …`;
   - `show demo-b70x` → `предложены блокеры (не подтверждены): demo-crft`;
   - `ready` содержит `demo-b70x`;
   - `dep confirm demo-b70x demo-crft --actor me` → `(подтверждена, предложение заменено)`, после него `ready` задачу не отдаёт.
   - Текст цикла из README воспроизведён дословно: `ошибка: жёсткая зависимость создаёт цикл: demo-crft → demo-b70x → demo-crft`.
   - Список мягких связей в README (`parent-child, relates-to, related, discovered-from,
     duplicates, supersedes, suggested-blocks`) = `deps.SOFT_LINKS` минус служебные
     `parent`, `replies-to` (чек-лист это разрешает).
3. **зелёный.** Три отказа `claim` воспроизведены на живых командах, тексты README совпадают
   по форме и по словам с выводом:
   - `--local`: `ошибка: задача … заблокирована и брать её нельзя.` + `Ждём завершения: …` + `Варианты: …` (три строки);
   - `--local`: `ошибка: задача demo-crft уже удерживается dsh/one (0 мин). Варианты: …`;
   - `--local`: `ошибка: рабочее дерево основное проекта demo занято задачей demo-crft (Задача A), держит dsh/one 0 мин. Варианты: …`;
   - через сервер (`--port 8799`) те же два отказа печатаются как `ошибка 400: …` — оба
     префикса в README названы одной фразой, утверждения «всегда `ошибка: …`» нет.
   Замечание (не красное, не этой порции): в унаследованном примере отказа по блокеру
   строка `Варианты:` разбита на две с отступом, реальный CLI печатает её одной строкой
   (`store.py:418-423`) — косметика старого README, порция её не меняла.
4. **зелёный.** Фрагмент TOML в README посимвольно совпадает с `config.DEFAULTS["routing"]`
   (`default_process`, `harnesses` по четырём этапам в том же порядке, пять `transitions`,
   `return_window_hours = 24`). Проверено командами на проекте `demo2`:
   - `projects demo2` → `маршрутизация (источник: по умолчанию)` с той же таблицей;
   - `projects demo2 --routing '{"harnesses": {"s3-impl": ["dsh"]}}'` → `источник: база`, `s3-impl: dsh`;
   - `ready --harness codex --project demo2` → пусто, `--harness dsh` → задача есть;
   - `projects demo2 --routing '{}'` → снова `по умолчанию`;
   - приоритет «база побеждает TOML» — `config.routing()` сливает `{**toml_override, **db_override}` (`config.py:150`);
   - «задача без этапа — любому» — `config.allowed_harnesses` возвращает `[]` при пустом этапе (`config.py:162-171`);
   - `claim --harness codex` → `ошибка: harness codex не разрешён для этапа s3-impl проекта demo2; разрешены: dsh`,
     `claim` без `--harness` держателем `codex/x` проходит → harness из держателя не выводится (`store.py:366`);
   - подписи источника (`config.toml`, `база`, `config.toml+база`, `по умолчанию`) — `bin/listik:838-847`.
5. **зелёный.** Окно возврата прогнано целиком: `claim` → `stage` (s3→s4) → `comment -k verdict`
   «красный» → задача на `s3-impl`, держатель `dsh/impl` остался, событие
   `stage s4-judge → s3-impl` с `duration_s` записано. После сдвига события и `holder_at` на
   25 ч назад `ready` снял держателя и записал `release` с текстом
   `истёк срок возврата после красного verdict (24 ч без активности)` — ровно как в README.
   Ленивое истечение вызывается из `deps.ready_tasks` (deps.py:336) и `store.claim` (store.py:375).
   Абзац о рабочем дереве подтверждён `deps.worktree_conflict`: лок только для `''`/`s3-impl`/`s4-judge`,
   в пределах одного `(project, worktree)`, свой держатель исключается; живой прогон — `claim`
   на `s1-spec` и `s2-review` в занятом дереве проходит, `s3-impl` отказывает, а после
   `set <id> worktree=/tmp/wt-f` проходит.
6. **зелёный.** `grep -n "фонов\|default_process" README.md`: `default_process` — только во
   фрагменте TOML и в фразе «подсказка диспетчеру… на цепочку `stage` не влияет»; «фоновый» —
   только про поток эмбеддингов (строка 277, старый текст). Утверждений о фоновом истечении
   окна, о выводе harness из держателя и об UI доски для предложений/routing в README нет.

### API.md

7. **зелёный.** `store.list_projects` отдаёт ключи `routing`, `routing_effective`,
   `routing_source` (`store.py:887-916`, значения `default|config|db|config+db`);
   `GET /api/ready` принимает `harness` (`server.py:236-241`); `GET /api/deps/suggested`
   (`server.py:253-258`) отдаёт `items` + `generated_at`, а поля элемента в API.md —
   `issue_id, issue_title, issue_stage, project, depends_on, depends_on_title,
   depends_on_status, created_by, created_at` — совпадают с `deps.suggested` один в один.
8. **зелёный.** Форма ответа `add_dep` в API.md = возвращаемому словарю (`dep_type`,
   `requested_dep_type`, `suggested`, `confirmed`, `promoted`, `created`, `created_by`);
   `DELETE /api/tasks/{id}/deps/{depends_on}` существует (`server.py:307-311`), `dep_type`
   берётся из строки запроса, ответ `{removed, dep_types[]}` (`store.remove_dep`);
   `PATCH /api/projects/{slug}` с `routing` проверен по HTTP: `{"foo":1}` → `ошибка 400: routing: неизвестный ключ foo`,
   `{"harnesses":{"s9":["x"]}}` → `неизвестный этап в harnesses: s9`,
   `{"return_window_hours":0}` → `должен быть числом > 0`; три причины 400 у `claim` и
   «`harness` проверяется, только если передан» — подтверждено кодом (`store.py:366-412`) и прогоном.
9. **зелёный.** `grep -n "Статус \`blocked\` выставляется" API.md` — пусто; на её месте
   верное «статус … не меняют: „заблокирована“ — вычисляемое `blocked_by`/`deps_state`».
10. **зелёный.** `deps.DEP_TITLES['suggested-blocks'] == 'предложенный блокер'`;
    `suggested-blocks` в списке мягких; `worktree_busy` в `deps.ready` — ровно
    `{id, title, holder, holder_title, holder_age, stale}`, в `ready`/`claimable` не участвует,
    добавляет строку в `reasons` (`deps.py:25-33, 39-40, 56`).
11. **зелёный.** В шпаргалке есть `ready --harness dsh`, `dep confirm`, `dep suggested [--project]`,
    `projects <slug> [--routing '<json>']`, у `claim` — «заблокированную, чужую или в занятом
    дереве не возьмёт, скажет почему».

### Протокол harness и AGENTS.md

12. **зелёный.** В `git diff docs/harness-protocol.md` нет строк `-N. `/`+N. `; правила 1–10 на
    месте, пункта `11.` нет, файл начинается с `## Listik — протокол harness`, строк `^# ` нет,
    `grep -i "claude\|codex\|dsh\|grok"` по протоколу пуст. `python3 -m unittest tests.test_migrate -v`
    — 5 тестов, OK, без правок теста и спеки шага 02. Вводный абзац «Роли по этапам» содержит
    `ready --harness <твой-harness>` и три причины отказа с действиями; s1-spec — про
    предложение и `dep confirm` («`--confirm` агенту не использовать»); s4-judge — про окно
    возврата; шпаргалка — `$L ready --harness <кто>`, `$L dep add`, `$L dep confirm` с пометкой «человек».
13. **частично; остаток перенесён (см. ниже).** `migrate.upsert(AGENTS.md, dry_run=True)` →
    `unchanged`; изменения `AGENTS.md` только внутри маркеров (маркеры на строках 9 и 119,
    хунки — 27–110). `tests/test_migrate.py` зелёный без правок. В `CLAUDE.md` вне маркеров —
    ровно правки п. 15. **`migrate.upsert(CLAUDE.md, dry_run=True)` даёт `updated` и после
    порции — и дать `unchanged` не может ни при какой правке в границах порции** (разбор ниже).
14. **зелёный.** `git status --short` не показывает `config.toml`, `web/`, `listik/store.py`,
    `listik/deps.py`, `bin/listik`, `tests/`; из `docs/specs/` — только журнал шага (бумаги
    оркестратора, в коммит не идёт). Чужие `AGENTS.md`/`CLAUDE.md` не тронуты: сегодняшних
    правок в `~/Projects/*/{AGENTS,CLAUDE}.md` нет, кроме второго рабочего дерева этого же
    репозитория `~/Projects/Listik-ui` (ветка `pipeline-ui`), где обе бумаги в `git status`
    чистые — изменилась только mtime от переключения ветки.

### CLAUDE.md и MCP

15. **зелёный.** `deps.py` — `suggested-blocks` + `dep confirm` + `expire_return_handoffs`;
    `store.py` `claim` — три причины и лок `(project, worktree)` только для `s3-impl`/`s4-judge`/
    без этапа; `config.toml` — `[routing]` и переопределение проекта; в «Commands» — строка
    `./bin/listik projects <slug> --routing '<json>'`.
16. **зелёный.** `git diff -U0 listik/mcp.py` — ровно три правки, все внутри строк `description`
    (`listik_claim`, свойство `harness` у `listik_ready`, `listik_deps`); строк с `type`,
    `properties`, `required`, `default` в диффе нет. `tools/list` по stdio отдаёт 19 инструментов,
    схемы прежние, новые тексты на месте. Тексты согласуются с кодом (`store.claim`,
    `config.allowed_harnesses`, `store.add_dep`).

### Общее

17. **зелёный.** `python3 -m unittest discover tests` — 171 тест, OK; `tests/` в диффе нет.
18. **зелёный.** В добавленных строках `token`/`@`/`/Users/` встречаются только как
    «the board's auth token» (существующая фраза) и имя переменной `VITE_LISTIK_TOKEN`.
    Значений токенов, `[auth]`, e-mail и личных путей нет.
19. **зелёный.** Проверено командами заметно больше пяти утверждений — перечислены в пп. 1–5,
    7, 8, 10, 12, 16 выше. Противоречий коду порций a–c не нашёл.

## Разбор: почему п. 13 не даёт `CLAUDE.md unchanged`

Это дефект `listik/migrate.py`, а не порции, и он вскрылся именно при выполнении §3 ТЗ.

`upsert` ищет маркеры как **подстроки в любом месте текста**:

```python
if BEGIN in text and END in text:
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
```

(`listik/migrate.py:69-71`). В `CLAUDE.md` этого репозитория сгенерированного блока нет
никогда не было — зато в строке 84, в прозе про сам `listik/migrate.py`, оба маркера стоят
рядом: `` inserts/updates the `<!-- BEGIN LISTIK --> / <!-- END LISTIK -->` block ``. Проверка
(`dry_run=True`, файл не пишется) показывает: найденный «блок» — это 42 символа
`<!-- BEGIN LISTIK --> / <!-- END LISTIK -->` внутри предложения, и реальный `upsert` заменил
бы их всем протоколом (+112 строк), разорвав фразу пополам. Именно это и произошло у
исполнителя при первом вызове из §3 ТЗ; он откатил файл (`git checkout -- CLAUDE.md`, в
котором на тот момент не было ничего, кроме этой порчи: порции a–c закоммичены, правки §4
внесены после) и дальше правил `CLAUDE.md` руками.

Проверил результат: `git diff CLAUDE.md` — три хунка, 17 строк, ровно п. 4 ТЗ (строка
`projects --routing` в «Commands», `store.py`, `deps.py`, `config.toml`); ни обрывков
протокола, ни повреждённой строки 84, ни потерянного текста. `listik/migrate.py` не тронут
(`git diff --stat listik/migrate.py` пуст, последний коммит файла — 640c4c5, шаг 02).

Выполнить п. 13 для `CLAUDE.md` было нельзя: любой путь к `unchanged` — это либо правка
`migrate.py` (код, прямо запрещён границами порции), либо переписывание прозы строки 84 так,
чтобы маркеры не стояли парой, то есть маскировка дефекта. ТЗ в этом месте исходило из ложной
предпосылки («блок в `CLAUDE.md` устарел») — на самом деле блока нет, а `updated` приходил от
совпадения с прозой.

## Перенесено на приёмку шага

- `listik/migrate.py:69-71` — маркеры `BEGIN/END LISTIK` ищутся подстрокой, а не отдельной
  строкой: `upsert` на любом файле, который просто *упоминает* маркеры в тексте (например,
  `CLAUDE.md` этого репозитория, строка 84), затирает кусок предложения целым протоколом.
  Под ударом и чужие проекты: `init-projects` пройдёт по ним тем же `upsert`.
  Следствие — п. 13 чек-листа в части «`CLAUDE.md unchanged`» невыполним в границах порции.
- `bin/listik:413-418` (`cmd_context`) — текстовый вывод `context` печатает только
  `dependencies.hard`; `dependencies.suggested`, которое `documents.py:428-434` кладёт в
  контракт на `s1-spec`/`s2-review`, в текстовом режиме не показывается (в `--format json`
  есть). Фраза README «видна … в `context` на s1/s2» верна для контракта и MCP, но не для
  текстового вывода CLI. Не красное: контракт соответствует, дефект — в неизменённом коде
  порции a.
