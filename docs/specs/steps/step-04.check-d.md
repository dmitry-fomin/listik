# Приёмка порции 04.d. Документация: маршрутизация, связи, лок дерева, окно возврата

Судья сверяет каждое утверждение документации **с кодом и поведением** на временной базе
(`LISTIK_DB=/tmp/…`, `./bin/listik --local init`), а не с ТЗ. Утверждение, которое команда не
подтверждает, — красный пункт.

## README.md

1. Раздел «Зависимости между задачами» содержит в блоке команд `dep confirm <id> <блокер>` и
   `dep suggested`, у `dep rm` — пометку, что снимает и предложение; все три команды
   существуют (`./bin/listik dep --help` перечисляет `confirm`, `suggested`).
2. Есть абзац о предложениях агента: `./bin/listik --local dep add <B> <A> --actor agent:codex`
   печатает `предложение …`, `--local show <B>` — `предложены блокеры (не подтверждены)`,
   `--local ready` содержит `<B>`; `--local dep confirm <B> <A> --actor me` → `<B>` исчезает из
   `ready`. Список мягких связей в README совпадает с `deps.SOFT_LINKS` (сверить построчно;
   `parent`, `replies-to` могут быть опущены как служебные — не красный).
3. Пример отказа `claim` содержит три случая (блокер, чужой держатель, занятое дерево); тексты
   совпадают по форме с реальным выводом CLI: многострочные, слова `Варианты`, `рабочее
   дерево`, `уже удерживается`; рядом названы оба префикса — `ошибка 400: …` при поднятом
   сервере (`./bin/listik --port 8799 claim …` против сервера на временной базе) и
   `ошибка: …` при `--local`. Судья сверяет оба формата командами; пример, сжатый в одну
   строку, или README, утверждающий, что формат всегда `ошибка: …`, — красный пункт.
4. Есть раздел «Маршрутизация…» с фрагментом TOML `[routing]`; ключи и значения фрагмента
   совпадают с `config.DEFAULTS["routing"]` (сверить `harnesses` по этапам, `transitions`,
   `return_window_hours`, `default_process`); описаны `[routing.projects.<slug>]`, `listik
   projects <slug> --routing '<json>'`, приоритет «база побеждает TOML», сброс `'{}'`, `listik
   projects <slug>` показывает источник; `ready --harness` и «задача без этапа — любому»;
   `claim --harness` — необязательная проверка; `default_process` — подсказка. Каждое
   утверждение проверяется командой из «Как проверить» порции b.
5. Раздел «Этапы конвейера» содержит абзац об окне возврата: «держатель остаётся», «событие
   `stage` с длительностью», «`return_window_hours`, 24 ч по умолчанию», «без активности
   держателя», «при следующем `ready` или чужом `claim`», «событие `release`» — всё
   подтверждается сценарием из «Как проверить» порции c. Абзац о рабочем дереве называет
   пишущие этапы `s3-impl`, `s4-judge` и задачи без этапа и говорит, что `s1-spec`/`s2-review`
   дерево не занимают.
6. В README нет утверждений о фоновом истечении окна, о выводе harness из держателя, о влиянии
   `default_process` на `stage`, о UI доски для предложений/routing (`grep -n "фонов\|
   default_process" README.md` — только в допустимом смысле).

## API.md

7. «Чтение»: строка `GET /api/projects` перечисляет `routing`, `routing_effective`,
   `routing_source`; `GET /api/ready` — параметр `harness` с описанием; есть строка
   `GET /api/deps/suggested` с полями `items[]`, совпадающими с ключами элемента
   `deps.suggested` (сверить с кодом).
8. «Запись»: `POST /api/tasks/{id}/deps` описывает `confirm`, правило предложения от агента и
   поля ответа `dep_type, requested_dep_type, suggested, confirmed, promoted, created,
   created_by` (сверить с `store.add_dep`); есть строка `DELETE /api/tasks/{id}/deps/{depends_on}`
   с `dep_type` и ответом `removed, dep_types[]`; `PATCH /api/projects/{slug}` упоминает
   `routing`, сброс `{}` и 400; `POST …/claim` называет три причины 400 и что `force` обходит
   только блокеры, а `harness` проверяется только если передан.
9. Строки «Статус `blocked` выставляется сам…» в `API.md` нет (`grep` пуст); на её месте —
   верное утверждение о вычисляемом `blocked_by`/`deps_state`. **Красный до правки.**
10. «Зависимости: можно ли брать задачу»: `suggested-blocks` в списке мягких с названием
    «предложенный блокер» (= `deps.DEP_TITLES`); в таблице `deps_state` есть `worktree_busy`
    с формой `{id, title, holder, holder_title, holder_age, stale}` и оговоркой «на
    `ready`/`claimable` не влияет» — сверить с `deps.ready`.
11. CLI-шпаргалка содержит `ready --harness`, `dep confirm`, `dep suggested`,
    `projects <slug> [--routing …]`, и в строке `claim` — три причины отказа.

## Протокол harness и AGENTS.md

12. `docs/harness-protocol.md`: десять нумерованных правил **не изменены** (`git diff
    docs/harness-protocol.md` не содержит строк, начинающихся с `-N. `/`+N. ` для N=1..10;
    `python3 -m unittest tests.test_migrate.BlockContentTests -v` зелёный без правок теста и
    `docs/specs/steps/step-02-harness-protocol.md`); в «Роли по этапам» (вводный абзац) — фраза
    про `ready --harness <твой-harness>` и три причины отказа `claim` (блокер, чужой держатель,
    занятое дерево) с действиями (`release`/`needs-owner`/взять блокер); s1-spec — `dep add` =
    предложение, `dep confirm` — человек, `--confirm` агенту не использовать; s4-judge — окно
    возврата и снятие держателя; шпаргалка — `ready --harness`, `dep add`, `dep confirm`. Файл
    по-прежнему начинается с `## Listik — протокол harness`, без строк, начинающихся с
    одиночного `# `, без новых нумерованных пунктов вроде `11.`, без упоминания конкретных
    harness (`grep -n -i "claude\|codex\|dsh\|grok" docs/harness-protocol.md` пуст — как
    требовал шаг 02).
13. Блоки обновлены **в обоих файлах `migrate.TARGETS`**: `python3 -c "from listik import
    migrate, paths; [print(n, migrate.upsert(paths.ROOT_DIR / n, dry_run=True)) for n in
    migrate.TARGETS]"` печатает `AGENTS.md unchanged` и `CLAUDE.md unchanged` (до порции
    `CLAUDE.md` даёт `updated` — блок устарел ещё с шага 02; **красный до правки**);
    `python3 -m unittest tests.test_migrate -v` зелёный без правок теста; вне маркеров
    `AGENTS.md` не изменился (`git diff AGENTS.md` — только внутри маркеров), в `CLAUDE.md` вне
    маркеров — только правки п. 15.
14. `git status --short` не показывает файлов вне репозитория Listik (`init-projects` не
    запускался: `~/Projects/*/AGENTS.md` и `CLAUDE.md` других проектов без изменений —
    выборочно `git -C <каталог> status --short` по двум-трём проектам из `listik projects`) и
    не показывает `config.toml`, `web/`, `listik/store.py`, `listik/deps.py`, `bin/listik`,
    `tests/`, `docs/specs/`.

## CLAUDE.md и MCP

15. `CLAUDE.md`: описание `listik/deps.py` упоминает `suggested-blocks`/`dep confirm` и
    `expire_return_handoffs`; описание `claim` — три причины отказа и лок только для пишущих;
    описание `config.toml` — `[routing]` и переопределение проекта; в «Commands» есть строка
    `projects <slug> --routing`.
16. `git diff listik/mcp.py` содержит изменения только внутри строковых литералов
    `description`: у инструментов `listik_claim`, `listik_ready`, `listik_deps` и у свойства
    `harness` в `inputSchema` инструмента `listik_ready` (одна строка); структура схем —
    `type`, набор `properties`, `required`, `default` — без изменений (в diff нет строк с
    этими ключами); `tools/list` по stdio отдаёт те же схемы с точностью до текста описаний
    (достаточно diff). Тексты описаний согласуются с кодом: `listik_claim`
    называет блокер/держателя/дерево; `listik_ready` — фильтр по routing и «без этапа — всем»;
    `listik_deps` — предложение от агента и `confirm` по решению человека.

## Общее

17. `python3 -m unittest discover tests` зелёный; `tests/` в diff нет.
18. В diff нет токена, содержимого `[auth]`, e-mail и личных путей (`grep -n "token\|@" `
    по изменённым файлам показывает только прежние упоминания `listik token`/`VITE_LISTIK_TOKEN`).
19. В изменённых файлах нет утверждений, противоречащих коду порций a–c, по чек-листу выше;
    судья выборочно проверяет не менее пяти утверждений командами и записывает, какие.
