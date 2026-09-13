# Порция 04.d. Документация: маршрутизация, связи, лок дерева и окно возврата в README, API.md, протоколе harness, CLAUDE.md и описаниях MCP

## Контекст

После порций 04.a–04.c (закоммичены) код умеет: жёсткая связь от агента без `--confirm`
записывается как предложение `suggested-blocks`, подтверждается человеком `dep confirm` (или
`dep add --confirm`), отклоняется `dep rm`; список ожидающих — `dep suggested` /
`GET /api/deps/suggested`; `DELETE /api/tasks/{id}/deps/{dep}`; ошибки store на локальном
пути CLI печатаются с префиксом `ошибка: …` без трейсбека, на HTTP-пути (сервер поднят) —
`ошибка <код>: …` (`client.request`), текст отказа `claim` в обоих случаях многострочный
(«Ждём завершения: …» / «Варианты: …» на отдельных строках); `show` печатает предложения и
мягкие связи (порция a). Routing проекта
хранится в `config.toml` (`[routing]`, `[routing.projects.<slug>]`) и/или в колонке
`projects.routing`, валидируется, показывается `listik projects <slug>` и в `GET /api/projects`
(`routing`, `routing_effective`, `routing_source`), задаётся `projects <slug> --routing '<json>'`;
задача без этапа под фильтр `--harness` не попадает; `claim --harness X` — необязательный guard
(порция b). `claim` лочит рабочее дерево только для пишущих задач (`s3-impl`, `s4-judge`, без
этапа), отказы называют держателя и варианты, красный вердикт возвращает через `next_stage`
(событие с длительностью), окно возврата (`return_window_hours`, по умолчанию 24) истекает
лениво при `ready`/`claim` и только без активности держателя; `deps_state.worktree_busy`
(порция c).

Документация об этом молчит или врёт. Точные места (нумерация строк — на коммите шага 03,
1aa4128; после порций a–c может сдвинуться, ориентируйся на заголовки):

- `README.md` «Зависимости между задачами» (строки 105–140): список команд без `dep confirm`,
  `dep suggested`; ничего о предложениях агента; пример отказа `claim` — только по блокеру.
  «Этапы конвейера» (142–160): переходы описаны, окна возврата и лока дерева нет.
  «Периодическая калибровка harness» (162–182) говорит «обновляйте routing проекта», не
  объясняя, где он и как его менять. Раздела о маршрутизации нет.
- `API.md`: таблица «Чтение» — `GET /api/projects` без `routing*`, `GET /api/ready` без
  `harness`, нет `GET /api/deps/suggested`; таблица «Запись» — `POST …/deps` без `confirm` и
  формы ответа, нет `DELETE …/deps/{dep}`, `PATCH /api/projects/{slug}` без `routing`,
  `claim` описывает только отказ по блокеру; строка «Статус `blocked` выставляется сам: есть
  незакрытый блокер → `blocked`» (после таблицы «Запись») **неверна** — статус не меняется,
  меняется вычисляемое `blocked_by`/`deps_state`; раздел «Зависимости: можно ли брать задачу»
  — нет `suggested-blocks` в списке мягких, нет `worktree_busy` в `deps_state`; CLI-шпаргалка
  — нет `dep confirm`/`dep suggested`/`projects --routing`/`ready --harness`.
- `docs/harness-protocol.md` (канон; блок между маркерами в `AGENTS.md` **и** `CLAUDE.md`
  Listik и чужих проектов собирается из него — `migrate.TARGETS = ("AGENTS.md", "CLAUDE.md")`).
  `tests/test_migrate.py` проверяет три вещи: (1) `test_ten_rules_match_spec_verbatim` —
  десять нумерованных правил из ```text```-блока `docs/specs/steps/step-02-harness-protocol.md`
  присутствуют в протоколе **дословно** (с нормализацией пробелов), поэтому **пп. 1–10
  протокола менять нельзя** — это текст автора из спеки шага 02, а `docs/specs/*` и `tests/`
  порция не трогает; (2) в блоке есть правила `1.`–`10.`, раздел `### Роли по этапам`, четыре
  этапа и нет имён harness; (3) блок в `AGENTS.md` Listik равен тому, что даёт `migrate.upsert`
  (dry-run → `unchanged`). Блок в `CLAUDE.md` Listik тестом не проверяется и **уже устарел**
  (`migrate.upsert(ROOT_DIR/'CLAUDE.md', dry_run=True)` → `updated` ещё до этой порции). Сейчас
  протокол: п. 1 `listik ready` без `--harness`; п. 2 «не бери заблокированную» — ничего о
  чужом держателе и занятом дереве (оба пункта остаются как есть, новое — в «Роли по этапам»
  и шпаргалке); s1-spec «жёсткую `dep add` без подтверждения человека не ставить» — теперь
  сервер сам делает из неё предложение; s4-judge — про окно возврата ни слова.
- `./bin/listik init-projects --only listik` для обновления блоков **не подходит**: команда
  ходит только по строкам таблицы `projects` с непустым `path` (`cmd_init_projects`), проекта
  со slug, содержащим `listik`, ни в реальной базе, ни во временной нет. Блоки этого
  репозитория обновляются прямым вызовом `migrate.upsert` (см. п. 3).
- `CLAUDE.md` «Architecture»: описание `listik/deps.py` не упоминает предложения; `config.toml`
  описан как «host/port и токен» — про `[routing]` ни слова.
- `listik/mcp.py`: описания `listik_ready` («разрешённый harness для этапа» — уже есть),
  `listik_claim` (про отказы), `listik_deps` (фраза про предложение добавлена в a) — проверить
  и дописать только текст.

Правило порции: документация описывает **то, что делает код после a–c**, ничего сверх.
Перед правкой каждой фразы — проверить утверждение на временной базе или чтением кода;
судья будет сверять с кодом, а не с ТЗ.

Перед началом прочитай: `README.md` целиком, `API.md` целиком, `docs/harness-protocol.md`,
`AGENTS.md` (блок между маркерами), `CLAUDE.md`, `listik/migrate.py` (`_compute_body`, как
собирается блок), `tests/test_migrate.py` (какие свойства блока проверяются: файл начинается
с заголовка `## Listik — протокол harness`, без верхнеуровневого `#`, равенство блока в
`AGENTS.md`), тексты в `listik/mcp.py` `TOOLS`; код порций a–c: `store.add_dep`,
`remove_dep`, `claim`, `add_comment`, `deps.suggested`, `deps.expire_return_handoffs`,
`deps.ready`, `config.validate_routing`, `bin/listik` `cmd_dep`, `cmd_projects`, `main`.

## Что сделать

### 1. `README.md`

- Раздел «Зависимости между задачами»: в блок команд добавить `dep confirm <id> <блокер>`
  («подтвердить предложение агента → жёсткая связь»), `dep suggested [--project]` («что
  агенты предложили, а человек ещё не подтвердил»), пометить у `dep rm` — «снимает и
  предложение»; абзац «Предложения от агента»: жёсткая связь, поставленная агентом
  (`--actor agent:…`/`LISTIK_ACTOR`) без `--confirm`, записывается как `suggested-blocks`, на
  `ready`/`claim` не влияет, видна в `show` («предложены блокеры (не подтверждены)»), в
  `context` на s1/s2 и в `dep suggested`; человек подтверждает `dep confirm` или отклоняет
  `dep rm`; цикл жёстких связей сервер не пропускает (`ошибка: жёсткая зависимость создаёт
  цикл: A → B → A`). Мягкие связи — перечислить актуальный список (`parent-child`,
  `relates-to`, `related`, `discovered-from`, `duplicates`, `supersedes`, `suggested-blocks`).
  Пример отказа `claim` дополнить двумя случаями: чужой держатель и занятое дерево — тексты
  взять **из вывода реальной команды** на временной базе (ID и заголовки — вымышленные).
  Существующий пример в README начинается с `ошибка 400: …` — это HTTP-путь (сервер поднят,
  `client.request`); при `--local` или лежащем сервере тот же текст печатается с префиксом
  `ошибка: …`. Оба формата назвать одной фразой рядом с примерами; сам текст отказа
  многострочный — в примерах сохранить переносы, как их печатает CLI, не сжимать в одну строку.
- Новый раздел «Маршрутизация: кто какой этап ведёт» (после «Этапы конвейера»): таблица
  `этап → harnesses` живёт в `config.toml` `[routing]` (глобальные умолчания — привести
  фрагмент TOML с `default_process`, `[routing.harnesses]`, `[routing.transitions]`,
  `return_window_hours`) и переопределяется на проекте: `[routing.projects.<slug>.harnesses]`
  в TOML или `listik projects <slug> --routing '{"harnesses": {"s3-impl": ["dsh"]}}'`
  (пишет в базу, побеждает TOML, `'{}'` сбрасывает); `listik projects <slug>` показывает
  действующую таблицу и источник; `listik ready --harness dsh` отдаёт только то, что этому
  harness разрешено на этапе задачи (задача без этапа — любому); `claim --harness X` —
  необязательная проверка, harness из держателя не выводится; `default_process` — подсказка
  диспетчеру, цепочку `stage` не меняет. Одним предложением сослаться на «Периодическую
  калибровку» как на повод менять таблицу.
- Раздел «Этапы конвейера»: после абзаца о переходах — абзац «Возврат после красного вердикта
  и окно»: держатель остаётся, событие `stage s4-judge → s3-impl` с длительностью проверки;
  если держатель не проявил активность (`heartbeat`/`claim`) в течение `return_window_hours`
  (24 ч по умолчанию, настраивается в routing проекта) — при следующем `ready` или чужом
  `claim` держатель снимается с событием `release` «истёк срок возврата…», задача уходит в
  `ready`. Абзац «Рабочее дерево»: `claim` пишущей задачи (`s3-impl`, `s4-judge`, без этапа)
  отказывает, если в том же проекте то же `worktree` (пустое = основное дерево) держит другой
  держатель пишущей задачи; `s1-spec`/`s2-review` дерево не занимают; выход — `release`,
  другое `worktree` (`listik set <id> worktree=/путь/к/дереву` или `PATCH /api/tasks/{id}`
  с `worktree`) или другая задача.
- Раздел «Работа агента с задачей» или CLI-шпаргалка: `ready --harness <свой>` как первая
  команда harness (согласовано с протоколом).

### 2. `API.md`

- «Чтение»: `GET /api/projects` — добавить `routing` (объект или null), `routing_effective`,
  `routing_source`; `GET /api/ready` — параметр `harness` («только задачи, чей этап разрешён
  этому harness в routing проекта; без этапа — все»); новая строка `GET /api/deps/suggested`
  (`project`, `limit` → `items[]`: перечислить поля из `deps.suggested`, `generated_at`).
- «Запись»: `POST /api/tasks/{id}/deps` — `depends_on, dep_type=blocks, confirm=false, actor`;
  описать правило предложения и форму ответа (`dep_type, requested_dep_type, suggested,
  confirmed, promoted, created, created_by`), 400 на цикл и самосвязь; новая строка
  `DELETE /api/tasks/{id}/deps/{depends_on}` (`dep_type` в строке запроса; без него снимает
  `blocks` и `suggested-blocks`; ответ `removed, dep_types[]`); `PATCH /api/projects/{slug}` —
  добавить `routing` (объект; `{}` сбрасывает; 400 при невалидной форме, перечислить ключи и
  формы из `validate_routing`); `POST …/claim` — три причины 400: блокеры (обходится `force`),
  чужой держатель, занятое рабочее дерево (не обходятся), и что `harness` проверяется только
  если передан. Строку «Статус `blocked` выставляется сам…» заменить на верную: статус не
  меняется, «заблокирована» — `blocked_by`/`deps_state`, пересчитывается при изменении связей
  и статусов.
- «Зависимости: можно ли брать задачу»: в мягкие добавить `suggested-blocks` (предложение
  агента, `dep_title` «предложенный блокер»); в таблицу `deps_state` — `worktree_busy`
  (null или `{id, title, holder, holder_title, holder_age, stale}`; на `ready`/`claimable` не
  влияет, добавляет строку в `reasons`); правила для агента дополнить пунктом про предложение
  зависимости.
- CLI-шпаргалка: `listik ready --harness dsh`, `listik dep confirm <id> <dep>`,
  `listik dep suggested [--project]`, `listik projects <slug> [--routing '<json>']`; в строке
  `listik claim …` — «заблокированную, чужую или в занятом дереве не возьмёт, скажет почему».

### 3. `docs/harness-protocol.md` → блоки в `AGENTS.md` и `CLAUDE.md`

Правки минимальные, стиль и длина файла — как есть (короткие пункты, без упоминания
конкретных harness). **Десять нумерованных правил (пп. 1–10) не менять ни на символ** —
`test_ten_rules_match_spec_verbatim` сверяет их со спекой шага 02 дословно. Новый смысл
кладётся в свободные части файла:

- в раздел «Роли по этапам», в вводный абзац «Держатель на переходах» (или отдельным коротким
  абзацем сразу перед ним): «Что брать: `ready --harness <твой-harness>` — только задачи, чей
  этап разрешён тебе в routing проекта. `claim` откажет и скажет почему, если у задачи
  открытый блокер, чужой держатель или занятое рабочее дерево (`s3-impl`/`s4-judge`/задача без
  этапа в том же дереве); если блокер или держатель брошен — возьми блокер, сделай `release`
  или поставь `needs-owner`» (две-три строки, без новых нумерованных пунктов — тест ищет
  ровно строки `1.`–`10.`, добавлять `11.` нельзя);
- s1-spec «менять»: «предложения зависимостей — `dep add <порция> <блокер>`: от агента сервер
  записывает предложение, не жёсткую связь; подтверждает человек (`dep confirm`); `--confirm`
  агенту не использовать»;
- s4-judge, красный вердикт: дописать «держатель на `s3-impl` остаётся; если исполнитель не
  подаст `heartbeat`/`claim` в окно возврата (по умолчанию 24 ч), сервер снимет держателя и
  задача вернётся в `ready`»;
- шпаргалка команд: `$L ready --harness <кто>`, `$L dep add <id> <блокер>` (предложение),
  `$L dep confirm <id> <блокер>` с пометкой «человек».

После правки протокола обновить **оба** блока этого репозитория прямым вызовом (не
`init-projects` — см. «Контекст»):

```sh
python3 -c "from listik import migrate, paths
for n in migrate.TARGETS: print(n, migrate.upsert(paths.ROOT_DIR / n))"
```

Ожидаемо: `AGENTS.md updated`, `CLAUDE.md updated` (блок `CLAUDE.md` устарел ещё до порции —
его обновление здесь штатно, это генерируемый текст). Повторный вызов с `dry_run=True` даёт
`unchanged` для обоих. Вне маркеров `AGENTS.md` не меняется; в `CLAUDE.md` вне маркеров —
только правки п. 4. `tests/test_migrate.py` зелёный без правок теста и спеки шага 02.

### 4. `CLAUDE.md`

- Описание `listik/deps.py`: добавить «жёсткая связь от агента без `--confirm` записывается
  как `suggested-blocks` (мягкая, «предложенный блокер»), подтверждается `dep confirm`;
  `expire_return_handoffs` — ленивое истечение окна возврата после красного вердикта
  (`ready`/`claim`)».
- Описание `listik/store.py` `claim`: «отказывает по блокеру, чужому держателю и занятому
  рабочему дереву (лок `(project, worktree)` только для `s3-impl`/`s4-judge`/задач без этапа)».
- `config.toml`: «host/port, токен доски и `[routing]` — harnesses по этапам, виды переходов,
  окно возврата; переопределение проекта — `[routing.projects.<slug>]` или колонка
  `projects.routing` (`listik projects <slug> --routing`)».
- Раздел «Commands»: одна строка `./bin/listik projects <slug> --routing '<json>'`.

### 5. `listik/mcp.py` — только строки описаний

- `listik_claim`: «отказывает, если у задачи открытый блокер (обход `force`), чужой держатель
  или занято рабочее дерево — текст ошибки называет причину и варианты».
- `listik_ready`: текст `description` у свойства `harness` **внутри `inputSchema`** (это
  единственное место, где он есть) — «фильтр по routing проекта: только задачи, чей этап
  разрешён этому harness; задачи без этапа — всем». Разрешена правка только этой строки:
  структура схемы (`type`, набор `properties`, `required`, `default`) не меняется.
- `listik_deps`: проверить, что фраза о предложении (порция a) есть; дописать «`confirm` —
  только по решению человека».
Структуру схем (`inputSchema`) не менять; допустимые правки — строки `description` на верхнем
уровне инструмента и одна строка `description` свойства `harness` у `listik_ready`.

## Границы правки

- Файлы: `README.md`, `API.md`, `docs/harness-protocol.md`, `AGENTS.md` (только блок между
  маркерами, генерируется), `CLAUDE.md` (блок между маркерами генерируется, вне блока —
  правки п. 4), `listik/mcp.py` (только строковые литералы `description` в `TOOLS` — у
  инструментов и у свойства `harness` схемы `listik_ready`; `git diff listik/mcp.py` не
  содержит изменений вне строк `description`).
- Десять нумерованных правил `docs/harness-protocol.md` не менять; `docs/specs/steps/
  step-02-harness-protocol.md` и `tests/test_migrate.py` не трогать — если кажется, что без
  правки правил не обойтись, это ошибка ТЗ: остановиться и записать в отчёт.
- `./bin/listik init-projects` не запускать (без `--only` он пройдёт по всем проектам из
  реальной базы и изменит чужие `AGENTS.md`/`CLAUDE.md`).
- Не трогать: код `store.py`/`deps.py`/`config.py`/`server.py`/`client.py`/`bin/listik`
  (если документируя находишь дефект — записать в отчёт, не чинить), `web/`, `tests/` (кроме
  того, что `tests/test_migrate.py` должен остаться зелёным без правок), `docs/specs/*`,
  `docs/listik-vision.md`, `project-skills/`, `bin/listik-codex`, `config.toml` (в diff его
  быть не должно — он содержит токен), чужие `AGENTS.md`/`CLAUDE.md` вне репозитория.
- Не документировать того, чего нет: фонового истечения окна, вывода harness из держателя,
  влияния `default_process` на `stage`, UI доски для предложений/routing (шаг 05).
- Не переписывать разделы целиком и не менять структуру заголовков `README.md`/`API.md`
  сверх добавления одного раздела «Маршрутизация»; не удалять примеры команд.
- В документацию не копировать токен, содержимое `config.toml` `[auth]`, пути домашнего
  каталога с личными данными.

## Как проверить

```sh
export LISTIK_DB=/tmp/listik-step04d.db && ./bin/listik --local init
# каждую фразу — на команде: тексты отказов claim (с --local → «ошибка: …», через сервер на
# временной базе и порту 8799 → «ошибка 400: …»), вывод dep suggested/confirm, projects <slug>
python3 -c "from listik import migrate, paths
for n in migrate.TARGETS: print(n, migrate.upsert(paths.ROOT_DIR / n))"        # AGENTS.md updated, CLAUDE.md updated
python3 -c "from listik import migrate, paths
for n in migrate.TARGETS: print(n, migrate.upsert(paths.ROOT_DIR / n, dry_run=True))"   # оба unchanged
git status --short            # только README.md API.md docs/harness-protocol.md AGENTS.md CLAUDE.md listik/mcp.py
python3 -m unittest discover tests
grep -n "Статус \`blocked\` выставляется" API.md   # пусто
grep -n "dep confirm\|dep suggested\|--harness\|--routing" README.md API.md docs/harness-protocol.md AGENTS.md
```
