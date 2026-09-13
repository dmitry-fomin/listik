# Приёмка 04.b, заход r1 — вердикт: зелёный

Чек-лист: `docs/specs/steps/step-04.check-b.md`; порция: `docs/specs/steps/step-04.b.md`;
дифф: `.git/feature-pipeline/step-04.diff-b.r1.txt`.

Все сценарии прогонялись на временной базе (`LISTIK_DB`/`LISTIK_CONFIG` в scratchpad-каталоге
сессии). `git diff config.toml` пуст до и после прогона (проверено трижды, в т. ч. после полного
`unittest discover` и после подъёма сервера).

## Тесты

1. **зелёный.** `tests/test_routing.py` наследует `TempDbTestCase`,
   `python3 -m unittest tests.test_routing -v` → 16 тестов, OK. Девять сценариев раздела 6 ТЗ
   на месте: defaults (`test_no_overrides_uses_defaults`), поключевое переопределение из базы
   (`test_db_override_is_per_stage_merged`), TOML + приоритет базы
   (`test_toml_override_and_db_wins_over_toml`), фильтр `ready_tasks`
   (`test_ready_tasks_filters_by_harness`), задача без этапа
   (`test_task_without_stage_is_not_filtered_by_s1_spec_override`), guard `claim` в заданном
   порядке (`test_claim_guard_order`), `validate_routing` принимает/отвергает (два теста +
   `test_update_project_routing_json_and_reset`), форма `list_all_projects`/`update_project`
   (`test_response_shape_has_routing_keys`), CLI (`--routing`, `--json`, две ошибки,
   `ready --harness`) и отдельно `test_local_client_helpers_never_touch_http`.
2. **зелёный.** `setUp` подменяет `paths.CONFIG_PATH` на несуществующий файл во временном
   каталоге (`mock.patch.object`, снимается через `addCleanup`); `_cli` передаёт подпроцессу
   `env["LISTIK_DB"]` и `env["LISTIK_CONFIG"]` на временные пути. Тестов, читающих или пишущих
   реальный `config.toml`, нет.
3. **зелёный.** `python3 -m unittest discover tests` → Ran 146 tests, OK. Существующие тесты не
   изменены: `git status` не показывает ни одного `M` под `tests/`, только новый
   `tests/test_routing.py`.

## config / store / deps

4. **зелёный.** `validate_routing` на примере из чек-листа вернул
   `{'harnesses': {'s3-impl': ['dsh']}, 'default_process': ['s3-impl', 's4-judge'],
   'transitions': {'s3-impl:s4-judge': 'sticky'}, 'return_window_hours': 2}`;
   `validate_routing({})` == `{}`. До правки функции не было (`listik/config.py` в `HEAD`).
5. **зелёный.** Шесть форм подняли `ValueError` с русским текстом:
   `routing: неизвестный ключ bad`; `routing: неизвестный этап в harnesses: s9-x`;
   `routing: harnesses.s3-impl должен быть списком`;
   `routing: return_window_hours должен быть числом > 0`;
   `routing: неизвестный вид перехода s1-spec:s2-review: magic`;
   `routing: default_process содержит дубль: s3-impl`.
6. **зелёный.** После `update_project(..., routing={"harnesses": {"s3-impl": ["dsh"]}})` колонка
   `projects.routing` == `{"harnesses": {"s3-impl": ["dsh"]}}`; `routing={}` → колонка `NULL`
   (`test_update_project_routing_json_and_reset`); `routing="{не json"` → `ValueError`;
   `routing={"bad": 1}` → `ValueError` (валидация идёт до `UPDATE`, `changes` собирается заранее —
   `listik/store.py:1010-1017`).
7. **зелёный.** `allowed_harnesses("demo", "s3-impl", conn)` == `["dsh"]`,
   `("demo", "s1-spec", conn)` == `DEFAULTS[...]["s1-spec"]` — поключевое слияние сохранено
   (`config.routing` не переписан, диффа в нём нет).
8. **зелёный.** `allowed_harnesses("demo", None, conn)` == `[]`, `("demo", "done", conn)` == `[]`
   при переопределении в базе (`listik/config.py:162-165`).
9. **зелёный.** Временный TOML с `[routing.projects.demo.harnesses] s3-impl = ["codex"]` →
   `["codex"]`, `routing_source == "config"`; при колонке `["dsh"]` — `["dsh"]`,
   `routing_source == "config+db"` (`test_toml_override_and_db_wins_over_toml`).
10. **зелёный.** Отдельный прогон на временной базе с пятью задачами:
    `harness="codex"` → `s3-impl` нет, `s1-spec` есть, задача без этапа есть;
    `harness="dsh"` и вызов без `harness` → все три. Задача с жёстким блокером и задача с
    держателем не появляются ни при одном значении `harness`.
11. **зелёный.** `test_claim_guard_order` идёт ровно по порядку из чек-листа на одной задаче:
    `ValueError` с `codex`/`s3-impl`/`dsh` и пустым держателем → `claim` без `harness` →
    `update_task(holder="")` → `claim(holder="dsh", harness="dsh")`. `claim` в `listik/store.py`
    не изменён: `git diff listik/store.py` не содержит ни одной строки со словом `claim`, хунки
    диффа — только `@@ -856,0 +857` (новый хелпер), `@@ -867 +902` (return `list_projects`) и
    `@@ -960…-969` (`update_project`).
12. **зелёный.** `list_all_projects` → `routing` `None`/`dict`, `routing_effective` с четырьмя
    ключами, `routing_source` соответствует источнику (`default`/`db`/`config`/`config+db` —
    проверено во всех четырёх состояниях). `update_project(..., routing={...})` возвращает
    `routing_effective["harnesses"]["s3-impl"] == ["dsh"]`, `routing_source == "db"`; после
    `routing={}` — `routing is None`, `routing_source == "default"`. Логика в одном хелпере
    `store._project_with_routing` (`listik/store.py:857`), его зовут `list_projects`
    (`:902`) и `update_project` (`:1022`) — дублирования нет.
13. **зелёный.** `mock.patch.object(config_mod, "load", …)` с разбором стека на 30 задачах одного
    проекта и этапа: из `ready_tasks`/`allowed_harnesses` — **1** вызов `config.load` на пару
    `(project, stage)` (кэш `harness_cache`, `listik/deps.py:296-308`). Остальные 30 вызовов
    приходят из неизменённого `store.row_to_task` (`listik/store.py:523`, читает
    `board.wip_warn_hours`) — это поведение до порции, в границы правки не входит.

## CLI

13a. **зелёный.** С поднятым на 8787 сервером (`serve --no-embed --quiet` на отдельной базе,
    реальный `config.toml` только для токена) команды
    `LISTIK_DB=<tmp> LISTIK_CONFIG=<tmp> ./bin/listik --local projects demo --routing '…'`,
    `--local projects demo`, `--local projects --add … --slug demo2`,
    `--local projects --archive demo2` отработали на временной базе, код 0, `401` не возникало.
    `./bin/listik projects demo` без `--local` ушёл по HTTP в базу сервера и напечатал
    `проект не найден: demo` — временная база и реальный `config.toml` не затронуты.
    В тестах то же покрыто `test_local_client_helpers_never_touch_http`:
    `is_up.assert_not_called()` и `request.assert_not_called()`.
14. **зелёный.** `--local projects demo` печатает `маршрутизация (источник: по умолчанию):`,
    четыре этапа, `процесс по умолчанию: s1-spec → s2-review → s3-impl → s4-judge`,
    строку `переходы:` со всеми пятью переходами и
    `окно возврата после красного вердикта: 24 ч`.
15. **зелёный.** `--routing '{"harnesses":{"s3-impl":["dsh"]}}'` → код 0, в stdout
    `s3-impl:   dsh` и `источник: база`; блок печатается из словаря, который вернул
    `client.set_project_routing` (в `cmd_projects` ветка `--routing` печатает и делает
    `return 0` без повторного `list_projects`). `--routing '{}'` → `источник: по умолчанию`.
16. **зелёный.** `'{"bad":1}'` → код 1, stderr `ошибка: routing: неизвестный ключ bad`, без
    трейсбека; `'не json'` → код 1, `ошибка: невалидный JSON: …`; `projects nope --routing '{}'`
    → код 1, `ошибка: проект не найден: nope; добавьте его: listik projects --add <путь> …`.
17. **зелёный.** `--local projects demo --json` → список из одного элемента с ключами
    `routing`, `routing_effective`, `routing_source`.
18. **зелёный.** После установки ограничения `--local ready --harness codex` печатает только
    прямую задачу, `--harness dsh` — обе; `--local claim <T> --holder codex --harness codex` →
    код 1, `ошибка: harness codex не разрешён для этапа s3-impl проекта demo; разрешены: dsh`;
    `claim` без `--harness` — код 0; после `release` `claim --holder dsh --harness dsh` — код 0.
19. **зелёный.** Ветки `--add`, `--remove`, `--archive`, `--unarchive`, список без slug в
    `cmd_projects` в диффе не тронуты (добавлены только `local` в `list_kwargs`, ветка
    `--routing` и печать блока при показе одного проекта); `--local projects`, `--add`,
    `--archive` отработали в прогоне.

## HTTP

20. **зелёный.** На поднятом сервере: `GET /api/projects` → у `demo` `routing: null`,
    `routing_source: "default"`, `routing_effective` с четырьмя ключами;
    `PATCH /api/projects/demo {"routing": {"harnesses": {"s3-impl": ["dsh"]}}}` → 200 и **в ответе
    PATCH** `routing_effective.harnesses["s3-impl"] == ["dsh"]`, `routing_source == "db"`;
    последующий `GET` показывает то же; `{"routing": {"bad": 1}}` → `400: routing: неизвестный
    ключ bad` (`listik/server.py:206-207`).
21. **зелёный.** `GET /api/ready?harness=codex` → задачи `s3-impl` нет (пустой список),
    `?harness=dsh` → задача есть. Совпадает с CLI.
22. **зелёный.** `GET /api/meta` → 200, `projects` на месте, у строки проекта появились
    `routing`, `routing_effective`, `routing_source` рядом со старыми ключами;
    `cd web && npm run typecheck` — без ошибок.

## Границы

23. **зелёный.** `git diff --stat` по коду порции: `bin/listik`, `listik/client.py`,
    `listik/config.py`, `listik/deps.py`, `listik/server.py`, `listik/store.py` (+ новый
    `tests/test_routing.py`). Нет `web/`, `db.py`, `mcp.py`, `import_*.py`, `README.md`,
    `API.md`, `docs/harness-protocol.md`, `AGENTS.md`, `config.toml`. В `store.py` не изменены
    `claim`, `next_stage`, `add_comment`, `add_dep`, `project_row`, `add_project`,
    `upsert_project` (по хункам диффа); `next_stage` по-прежнему идёт по `PIPELINE_STAGES`
    (`listik/store.py:460-468`). В `config.py` `DEFAULTS` и `_dump` не изменены (диффа в них
    нет). В `client.py` у четырёх хелперов добавлен только `local: bool = False` и условие
    `not local and is_up(...)`; тела HTTP-запросов и форма ответов прежние.
24. **зелёный.** `grep -n "resolve" listik/deps.py listik/config.py` — пусто; `claim` не
    изменён; harness нигде не выводится из `holder`/`actor`.

## Наблюдения (не блокируют)

- `store._project_with_routing` зовёт `config.routing()` и `config.load()` на каждую строку
  проекта, а `config.load()` читает TOML с диска без кэша — `GET /api/meta` с N проектами делает
  ~2N чтений `config.toml`. Это ровно та схема, которую предписывает ТЗ порции («хелпер зовут все
  места»), и на текущем числе проектов незаметно; отмечено на будущее.
- В `cmd_projects` ветка `--routing` идёт раньше `--add`: при одновременных `--add` и `--routing`
  отработает только `--routing`. Комбинация в ТЗ не описана.

## Коммит

`git add -- bin/listik listik/client.py listik/config.py listik/deps.py listik/server.py
listik/store.py tests/test_routing.py` (`docs/specs/listik-product.journal.md` — бумаги шага,
в порцию не входит).
