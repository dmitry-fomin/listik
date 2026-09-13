# Приёмка порции 03.a, заход r1 — вердикт: зелёный

Коммит: `4fce07a` «Шаг 03, порция a: модуль импорта WriterLLM — адаптеры, задачи, связи,
комментарии, тесты».

Все прогоны — на временных базах (`/tmp/listik-step03a*.db`), каждая команда `./bin/listik`
с явным `LISTIK_DB=`. `stat -f %m listik.db` до приёмки — `1789220185`, после — `1789220185`
(живая база не тронута).

## Тесты и фикстуры

1. **Зелёный.** `python3 -m unittest discover tests` → `Ran 92 tests … OK`.
   `python3 -m unittest tests.test_import_writerllm -v` → `Ran 14 tests … OK`; имена
   `test_1_…`…`test_14_…` прямо нумеруют пункты 1–14 раздела «Тесты» ТЗ.
   «Красные до правки» подтверждены без `git stash`: `git worktree add /tmp/lk-draft HEAD`,
   туда скопированы новые тесты и фикстуры → `FAILED (failures=10, errors=3)`, то есть 13 из 14
   падают на черновике; в их числе все четыре обязательных — п. 2 (даты), п. 3 (актор),
   п. 4 (связи), п. 11 (строка `projects`). Единственный зелёный на черновике — `test_13`
   (поиск по старому ID), что ожидаемо: черновик уже писал `external_ref`.
   Worktree удалён, `git worktree list` чист.
2. **Зелёный.** Фикстуры на месте и синтетические. `grep -rniE "@|фомин|fomin"` даёт 4 совпадения,
   все — значения `assignee`/`created_by`/`author` со значением `dfomin` (в том числе
   `created_by` внутри элемента `dependencies`), что чек-лист явно разрешает; `@`, e-mail и имён
   людей нет. `export.jsonl`: 7 записей `_type=issue` + 1 `_type=memory`; есть `blocks`
   (`WL-a4→WL-a5`), `parent-child` (`WL-a1.1→WL-a1`), `relates-to` (`WL-a4→WL-a2`), связь на
   несуществующую цель (`WL-missing`), запись с двумя комментариями (`WL-a1`, один с `[listik]`),
   запись с комментарием без `id` и без `created_at` (`WL-a3`), запись со статусом `weird`
   и приоритетом `"x"` (`WL-a9`).

## Поведение модуля

3. **Зелёный.** После `init`: `actors=7`, `actor_aliases=10`. После `--dry-run`:
   `tasks/comments/deps/events/projects` = 0, `actors=7`, `actor_aliases=10` — побочных записей
   через `actors.remember`/`seed_actors` нет. Чтением кода: `_resolve_actor` вызывает
   `actors_mod.remember` под `if not dry_run` (стр. 203–209); `create_task`/`_write_comments`/
   `_index_task`/`store.event`/`conn.commit()` — за `if dry_run: … continue` (стр. 453–498);
   `add_dep` — за `if dry_run: … continue` во втором проходе; `recompute_blocked`, строка
   `projects` и `commit()` — под `if not dry_run` (стр. 535–543). Аудит всех пишущих вызовов
   в модуле: `grep -n "seed_actors|update_task|add_comment|store.event|recompute_blocked|remember|commit()"`
   даёт ровно 6 совпадений, каждое проверено выше. `store.gen_id` (вызывается до проверки
   `dry_run` при коллизии) — только `SELECT` (`listik/store.py:80–87`), записи не делает.
4. **Зелёный.** `created=7` (= число `issue`-записей), `ignored=1`, `total=8`. Все 7 задач:
   `id` = старому ID, `source='writerllm'`, `project='writerllm'`, `external_ref` = старому ID.
5. **Зелёный.** `WL-a1`: `created_at=2024-01-01T09:00:00Z`, `updated_at=2024-01-06T09:00:00Z`,
   `started_at=2024-01-02T09:00:00Z`, `closed_at=2024-01-06T09:00:00Z`,
   `close_reason='workaround shipped'`, `result='workaround shipped'`, `status='done'`,
   `priority=1`. В `events`: `created` с `ts=2024-01-01T09:00:00Z` и `import`
   с `ts=2024-01-06T09:00:00Z`.
6. **Зелёный.** `assignee='me'`, `created_by='me'`, авторы обоих комментариев `WL-a1` — `me`.
7. **Зелёный.** `deps`: `(WL-a4, WL-a5, blocks)`, `(WL-a4, WL-a2, relates-to)`,
   `(WL-a1.1, WL-a1, parent-child)`. `tasks.blocked_by` у `WL-a4` = `["WL-a5"]`;
   `./bin/listik --local show WL-a4` печатает «заблокирована: WL-a5». Связь на `WL-missing` —
   в `errors` с `where="dependency"`, `target_ref="WL-missing"`; задача-источник `WL-a1.1`
   создана.
8. **Зелёный.** `WL-a1` — ровно два комментария, `created_at` из фикстуры,
   `kind='journal'` у текста с `[listik]` и `kind='comment'` у второго. `WL-a3` — ровно один
   комментарий, `created_at='2024-02-01T08:00:00Z'` (= `created_at` задачи),
   `id='WL-a3:wl:8e4760f16d88633d'`. `select count(*) from events where kind='comment'` → 0.
9. **Зелёный.** Второй прогон: `create=0 skip=7 deps=0`; счётчики `tasks|comments|deps|events`
   до = `7|3|3|14`, после второго = `7|3|3|14`, после третьего = `7|3|3|14`. Комментарий без
   `created_at` не продублировался.
10. **Зелёный.** `broken.jsonl`: `total=5`, `created=3` (две валидные + запись без `id`),
    запись без `id` получила `external_ref='writerllm:b4bc01f650b2f47faed5c622'`; второй запуск
    её не дублирует (`created=0`, `skipped=3`). В `errors` — запись с `line=2` и
    `raw='{not json'` и запись `where="record"` с `line=4` (запись без заголовка).
    Код возврата команды — `1`.
11. **Зелёный.** На чистой `DB2` с нативной `WL-a1`: импортированная задача получила
    `id='writerllm-w8km'`, `external_ref='WL-a1'`, `source='writerllm'`; в `examples.create`
    у неё `remapped: True`. Нативная `WL-a1` не изменилась (`title='native'`,
    `source='native'`, `project='demo'`).
12. **Зелёный.** Сравнение проведено на двух чистых базах (`DB3` — `export.json`, `DB4` —
    `export.jsonl`), поскольку к моменту этого пункта `$DB` уже содержит задачи из
    `broken.jsonl` и буквальное сравнение множеств с `$DB` невозможно по построению чек-листа.
    `export.json`: `total=7`, `created=7`, `ignored=0`, `dependencies=3`, `comments=0`;
    множества `select id from tasks` и рёбер `(issue_id, depends_on, dep_type)` в `DB3` и `DB4`
    совпадают. `grep -c memory tests/fixtures/writerllm/export.json` → `0`.
13. **Зелёный.** `projects`: `writerllm | writerllm | writerllm | 7 задач из WriterLLM` сразу
    после первого импорта. После повторных прогонов `import_note` становится
    `0 задач из WriterLLM` — это ровно то, что предписывает ТЗ §5 (`'<created> задач из
    WriterLLM'`, `created` повторного прогона = 0), а не отклонение.
14. **Зелёный.** Каталог как источник → `total=0`, одна ошибка `where="source"`
    (`[Errno 21] Is a directory`); `README.md` → `total=0`, одна ошибка `where="source"`
    (`invalid JSON: Expecting value`). Число задач в `$DB` не изменилось (10 до и после).
    `grep -n "_markdown_record\|rglob\|def _scalar" listik/import_writerllm.py` — пусто.
15. **Зелёный.** `show WL-a1` печатает карточку со строкой «было в beads: WL-a1»;
    `search WL-a1 --mode text` находит её первой и единственной.
16. **Зелёный.** `WL-a9` создана как `open`/`2`; `warnings` — ровно два элемента:
    `{field: 'status', value: 'weird', used: 'open'}` и `{field: 'priority', value: 'x', used: 2}`.

## Границы

17. **Зелёный.** `git diff --stat HEAD -- . ':!docs/specs'` — только `listik/import_writerllm.py`;
    untracked — только `tests/test_import_writerllm.py` и три файла
    `tests/fixtures/writerllm/`. `git status --porcelain -- bin listik/store.py listik/deps.py
    listik/actors.py listik/db.py listik/import_beads.py listik/server.py listik/mcp.py
    listik/client.py README.md API.md CLAUDE.md alembic` — пусто.
18. **Зелёный.** `--update` → `created=0, updated=0, skipped=7, update=True`, без падения.
19. **Зелёный.** В диффе нет имён людей, e-mail, токенов, `.env`/`*.key`/`*.pem`/
    `credentials.json` и содержимого `config.toml`. Тесты работают только с `FIXTURES_DIR`
    и `self.tmp_path`; обращений к `~/Agents` и `listik.db` нет.
    (Замечание: строка «исполнитель: Дмитрий Фомин» в выводе `show` берётся из таблицы
    `actors` временной базы, засеянной существующим `db.seed_actors`; ни фикстуры, ни дифф
    имени не содержат — это поведение неизменённого кода.)
20. **Зелёный.** `grep -n "init()\|subprocess\|bin/listik" tests/test_import_writerllm.py` —
    пусто; единственный вызов `db_mod.init(self.tmp_path / "second.db")` идёт с явным путём.
    `grep -n "bin/listik" docs/specs/steps/step-03.a.md | grep -v "LISTIK_DB="` показывает
    три строки-прозы (стр. 32, 274, 287), команд среди них нет. `stat -f %m listik.db`
    до и после совпал.

## Проверка диффа на срезанные углы — чисто

- Тесты не подогнаны под реализацию: 13 из 14 падают на коде `HEAD`.
- Хардкода под фикстуры в модуле нет: ветвлений по конкретным ID/заголовкам не встречается,
  вся логика идёт через таблицы `_STATUS_MAP`/`_PRIORITY_NAMES` и общий `_first_nonempty`.
- Правка ровно в своём слое: `store`/`deps`/`actors`/`db`/`bin/listik` не тронуты, недостающие
  операции сделаны прямым SQL внутри модуля импорта, как разрешает ТЗ.
- Заглушек нет: `--update` не «сделан частично», а честно ведёт себя как skip, что порция
  прямо предписывает (реализация — порция b).
- Отчёт содержит все ключи контракта §7, включая `source`, `project`, `ignored`, `comments`,
  `warnings` и `examples` с четырьмя корзинами по ≤5 элементов.

## Пункты, перенесённые на приёмку шага

Нет.

## Непоказательные замечания (не блокируют, не красные)

- `tests/test_import_writerllm.py:9` — `import pathlib` не используется. Мусорный импорт,
  на поведение не влияет, отдельным пунктом чек-листа не покрыт.
