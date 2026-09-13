# Приёмка порции 03.b, заход r1 — вердикт: зелёный

Коммит: `<см. отчёт оркестратору>` «Шаг 03, порция b: `--update` с журналом diff, отчёт для CI,
вывод CLI, подпись источника в `show`».

Все прогоны — на временных базах (`/tmp/listik-step03b*.db`), каждая команда `./bin/listik`
с явным `LISTIK_DB=`. `stat -f %m listik.db` до приёмки — `1789220185`, после — `1789220185`
(живая база репозитория не тронута). Рабочее дерево не перекладывалось: ни `stash`, ни `reset`,
ни `checkout` файлов; поведение `HEAD` проверено чтением `git show HEAD:…` и одноразовым
`git worktree add` во временный каталог (удалён, `git worktree list` чист).

## Предусловие

0. **Выполнено.** `HEAD` = `4fce07a` (порция a). `grep -n "_markdown_record"
   listik/import_writerllm.py` — пусто. `python3 -m unittest discover tests` на дереве порции b —
   `Ran 102 tests … OK` (в порции a было 92 + 10 новых тестов b: 24 в
   `tests.test_import_writerllm`).

## `--update` и сохранность ручных данных

1. **Зелёный.** Поведение `HEAD` подтверждено чтением: `git show HEAD:listik/import_writerllm.py`,
   строки 427–431 — ветка `if existing:` безусловно делает `report["skipped"] += 1`, сравнения
   полей нет. После порции b на последовательности `export.jsonl` → `export-v2.jsonl --update`
   отчёт даёт `обновлено: 2   пропущено: 5`, в `examples["update"]` — `WL-a2` и `WL-a5`.
2. **Зелёный.** `import export.jsonl` (код `1`, 7 задач), `--local comment WL-a1 "ручной"`,
   затем `export-v2.jsonl` без флага и с `--update`: `select task_id,kind,text from comments
   where task_id='WL-a1'` — ровно три строки, `Взял в работу.` (comment), `[listik] stage=done`
   (journal), `ручной` (comment). Комментариев выгрузки по-прежнему два, `--local show WL-a1`
   печатает «ручной» последней строкой истории.
3. **Зелёный.** Без `--update` на `export-v2.jsonl`: `создано: 0   обновлено: 0   пропущено: 7
   комментариев: 1   ошибок: 0`, код `0`; `select id,title,status from tasks` — `WL-a2 |
   Добавить пагинацию списков | open`, `WL-a5 | Подготовить новый формат отчёта | open`
   (прежние); у `WL-a3` появился второй комментарий `Починили дедупликацию по ключу ретрая.`
4. **Зелёный.** С `--update`: `обновлено: 2`, код `0`. `WL-a2.title = 'Добавить пагинацию
   списков в API'`, `WL-a5.status = 'in_progress'`. Журнальные комментарии:
   `WL-a2 | journal | [import-writerllm] update:\ntitle: Добавить пагинацию списков → Добавить
   пагинацию списков в API`, `WL-a5 | journal | [import-writerllm] update:\nstatus: open →
   in_progress` — по одной строке поля, лишних полей нет. `created_at` обеих —
   `2026-09-12T16:05:18Z` (как до `--update`). `WL-a4` — в `skip`, журнала нет.
   В `--json` `examples.update` = `[{id: WL-a2, external_ref: WL-a2, title: …, fields:
   ["title"]}, {id: WL-a5, …, fields: ["status"]}]`.
5. **Зелёный.** Повторный `--update` той же выгрузки: `обновлено: 0`, код `0`;
   `select count(*) from comments where text like '[import-writerllm] update:%'` = `2`
   и после третьего прогона тоже `2`.
6. **Зелёный.** `DB3=/tmp/listik-step03b-dry.db`: до `--dry-run --update` —
   `WL-a2|…|open|` (пустой `started_at`), `WL-a5|…|open|`, `count(comments)=3`; отчёт
   `обновлено: 2` и строка `(dry-run: ничего не записано)`; после — те же значения и
   `count(comments)=3`.
7. **Зелёный.** У `WL-a2` (статус не менялся) `started_at`, `closed_at`, `close_reason`,
   `holder` пусты до и после, `external_ref='WL-a2'`, `project='writerllm'` прежние.
   У `WL-a5` `started_at` из пустого стал `2026-09-12T16:05:35Z` (дата приёмки, выставил
   `update_task`), `closed_at` пуст, `holder` пуст, `external_ref`/`project` прежние; в
   журнальном комментарии `WL-a5` только `status: open → in_progress`, `started_at` не упомянут.
   Чтением диффа: в ветке update нет ни одного прямого `UPDATE tasks SET`, в `update_task`
   передаётся только `fields = {field: new for field, (_old, new) in diff.items()}`, а
   `_UPDATE_FIELD_SOURCE_KEYS` не содержит дат, `holder*`, `external_ref`, `project`, `source`
   и `*_path`.

Дополнительно (не пункт чек-листа, проверка на хардкод под фикстуру): синтетическая пара
выгрузок во временном каталоге, где у одной записи меняются сразу `title, description, status,
priority, issue_type, labels, close_reason`, даёт `updated=1`, `errors=[]`,
`fields=['title','description','status','priority','issue_type','labels','close_reason']`,
корректно сериализованные `labels` (`["a","c"]`) и целый `priority=3`; повторный прогон —
`updated=0`. То есть сравнение общее, а не подогнано под `WL-a2`/`WL-a5`.

## Отчёт и код возврата

8. **Зелёный.** `broken.jsonl --dry-run` → `1`; `export.jsonl` → `1` ровно с одной ошибкой
   `! запись WL-a1.1, связь → WL-missing: target task not found` (`where="dependency"`),
   задачи при этом созданы; `export-v2.jsonl` на пустой `DB2` → `0` (`создано: 7   ошибок: 0`).
9. **Зелёный.** `--json` разбирается `python3 -m json.tool` и `json.loads`; в stdout только
   JSON (диагностика импорта идёт в stderr — поведение неизменённого логгера порции a).
   Ключи отчёта: `ok, total, created, updated, skipped, ignored, dependencies, comments,
   errors, warnings, examples` (+ `source, project, dry_run, update`). `ok == (errors == [])`
   проверено на `broken.jsonl` (`False`) и на `export-v2.jsonl` (`True`). Каждый элемент
   `errors` имеет `where`, `error`, `raw`; `raw` — сырая строка `'{not json'` для нечитаемой
   строки и сырой словарь `{'id': 'WL-b2'}` для записи без заголовка; `raw=None` встречается
   только при `where="source"`.
10. **Зелёный.** Текст: `записей: 8   создано: 7   обновлено: 0   пропущено: 0   игнорировано: 1
    связей: 3   комментариев: 3   ошибок: 1`; примеры `create`/`skip`/`update` с ID и заголовком,
    по пять на действие (срез `[:5]` в `cmd_import_writerllm`); ошибки — строками `  ! `,
    первые 10, без `raw`; `(dry-run: ничего не записано)` печатается только при `--dry-run`
    (проверено: в прогонах без флага строки нет); предупреждения — одной строкой
    `предупреждений: 2 (подробности в --json)`.
11. **Зелёный с замечанием.** `import-writerllm --help` показывает
    `--project PROJECT  slug проекта для импортируемых задач (по умолчанию: writerllm)` и
    `--update  обновлять поля уже импортированных задач и писать diff в журнал`.
    Фраза про прямую работу с базой добавлена как `help=` подкоманды
    (`bin/listik:1018–1019`) и печатается в списке команд `./bin/listik --help`:
    `import-writerllm    идемпотентный импорт WriterLLM (работает с базой напрямую,
    --local не нужен)`, но **не** в выводе `./bin/listik import-writerllm --help` —
    у подпарсера нет `description`, а `help=` argparse показывает только в родительском списке.
    Требование ТЗ порции («это отметить в `help` команды одной фразой») выполнено буквально:
    фраза лежит в `help` команды и наблюдаема пользователем. Пробу чек-листа она проходит
    частично — это замечание, не дефект; при желании закрывается одним `description=` и
    может быть добавлено в приёмку шага.

## `show`

12. **Зелёный.** `LISTIK_DB=$DB ./bin/listik --local show WL-a1` печатает
    `  старый ID (writerllm): WL-a1`. `grep -n "было в beads" bin/listik` — пусто.
    У созданной вручную задачи `test-wzza` без `external_ref` строки со «старый ID» нет
    (`grep -c "старый ID"` → `0`). Прочие строки `show_task` в диффе не тронуты — хунк
    ровно одна строка.

## Тесты и границы

13. **Зелёный.** `python3 -m unittest discover tests` → `Ran 102 tests … OK`. Новые тесты
    покрывают пункты 1–7 раздела «Тесты» ТЗ: `test_15` (ручной комментарий), `test_16`
    (skip без флага + дозапись комментария), `test_17` (update, журнал, даты по правилу Б3,
    `examples["update"]`), `test_18` (повторный update), `test_19` (dry-run + update),
    `test_20` (`ok` на трёх фикстурах, ключи `errors`), `test_21`–`test_24` (CLI через
    `subprocess` с `env={"LISTIK_DB": …}`, коды `1`/`1`/`0` и строка `старый ID (writerllm)`).
    `git diff HEAD --numstat -- tests/test_import_writerllm.py` → `183  0` — только добавления,
    тесты порции a не изменены.
    **Тесты не подогнаны под реализацию:** новый файл тестов и `export-v2.jsonl` скопированы
    в `git worktree add /tmp/lk-b-check HEAD` (код порции a) → `Ran 24 tests, FAILED
    (failures=5, errors=2)`: падают `test_17`, `test_18`, `test_19`, `test_20`, `test_21`,
    `test_22`, `test_23`. Проходят на `HEAD` только `test_15`, `test_16` и `test_24` — они
    описывают поведение, которое порция b сохраняет неизменным. Worktree удалён.
14. **Зелёный.** `tests/fixtures/writerllm/export-v2.jsonl` — 7 `issue`-записей;
    `grep -c "WL-missing\|memory"` → `0`; `grep -rniE "@|фомин|(^|[^d])fomin"` — пусто
    (есть только алиас `dfomin`, разрешённый порцией a). `WL-a2` — новый `title`, статус
    `open`; `WL-a5` — `status: in_progress`; `WL-a3` — второй комментарий с `id` и `created_at`;
    `WL-a1.1` — только `parent-child → WL-a1`. `git diff HEAD --stat` по `export.jsonl`,
    `export.json`, `broken.jsonl` — пусто.
15. **Зелёный.** `git diff --stat HEAD -- . ':!docs/specs'` — `bin/listik`,
    `listik/import_writerllm.py`, `tests/test_import_writerllm.py`; `git status --porcelain
    -- . ':!docs/specs'` добавляет только `?? tests/fixtures/writerllm/export-v2.jsonl`.
    В `bin/listik` ровно три хунка: одна строка `show_task`, `cmd_import_writerllm`, блок
    `add("import-writerllm", …)`. `git status --porcelain -- listik/store.py listik/deps.py
    listik/db.py listik/import_beads.py listik/server.py listik/mcp.py listik/client.py web
    README.md API.md` — пусто.
16. **Зелёный.** До `--update`: `count(deps)=3`, `count(comments)=5`; после: `count(deps)=3`,
    `count(comments)=7` (+2 журнальных). Ни одна строка не удалена; повторные `--update`
    оставляют `3`/`7`.
17. **Зелёный.** В диффе нет e-mail, имён людей, токенов, `.env`/`*.key`/`*.pem`/
    `credentials.json` (`grep -rniE "[a-z0-9._%-]+@[a-z0-9.-]+|фомин|token|secret|password"`
    по пакету диффа — пусто). Все четыре `subprocess.run` в тестах идут с
    `env={**os.environ, "LISTIK_DB": str(...)}`; `db_mod.init` вызывается только с явным путём
    (`self.tmp_path / "dirty.db"`); `grep -n "Agents\|listik.db"` по тестам — совпадений нет,
    `tests/helpers.py` работает во временном каталоге. `stat -f %m listik.db` до и после
    приёмки — `1789220185`.

## Проверка диффа на срезанные углы — чисто

- Хардкода под фикстуру нет: сравнение идёт по таблице `_UPDATE_FIELD_SOURCE_KEYS`, ветвлений
  по конкретным ID нет; проверено синтетической выгрузкой с семью изменёнными полями (см. п. 7).
- Правка в своём слое: `store.update_task`/`store.add_comment` вызываются как есть, их
  семантика не обходится; побочная простановка `started_at` у `WL-a5` оставлена как норма и
  в журнал не попала.
- Заглушек нет: `--update`, `ok`, коды возврата и текстовый вывод реализованы целиком;
  `dry_run` честно не вызывает ни `update_task`, ни `add_comment` (проверено на `$DB3`).
- Все поля diff входят в `store.UPDATABLE` (`listik/store.py:208–213`), `labels` уходит списком
  и сериализуется самим `update_task`, `priority` сравнивается целым.
- Ошибка внутри `update_task`/`add_comment` не роняет импорт, а попадает в `errors`
  (`where="record"`), то есть контракт «исключение — через отчёт» соблюдён и на этом пути.

## Пункты, перенесённые на приёмку шага

Нет.

## Непоказательные замечания (не блокируют, не красные)

- `bin/listik:1018` — фраза «работает с базой напрямую, --local не нужен» видна в
  `./bin/listik --help`, но не в `./bin/listik import-writerllm --help` (см. п. 11).
- Автор журнального комментария `[import-writerllm] update:` пуст у `WL-a2`/`WL-a5`, потому что
  в `export-v2.jsonl` у этих записей нет ключа `created_by`; это ровно то, что предписывает ТЗ
  («актор из `created_by` записи»), и совпадает с поведением `_write_comments` порции a.
