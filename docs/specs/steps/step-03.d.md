# Порция 03.d. Прогон импорта на живой базе: задачи WriterLLM

## Контекст

Listik — трекер задач на stdlib-Python; живая база — `listik.db` в корне репозитория
(в `.gitignore`, в git не попадает). Порциями a–c (коммиты 4fce07a, 94ac384, 9d85598) готова и
задокументирована команда `listik import-writerllm --source <path> [--project writerllm]
[--dry-run] [--update] [--json]` (раздел «Импорт WriterLLM» в `README.md`). Эта порция — **не
код, а операция**: перенести реальные задачи WriterLLM в живую базу и зафиксировать числа.
Решение автора: порция входит в шаг, исполнитель выполняет, судья принимает по счётчикам.

Источник: beads-трекер WriterLLM на dolt-бэкенде в `~/Agents/WriterLLM/.beads` (`bd` 1.1.0,
`~/.local/bin/bd`). На 12.09.2026 `bd export` даёт 777 записей `_type=issue` (776 `closed`,
1 `open`), 722 связи, 90 комментариев, ID `WriterLLM-xxxx` и `WriterLLM-xxxx.N`; цифры к моменту
прогона могут вырасти — считать по фактической выгрузке. Выгрузка содержит имя и e-mail владельца:
файл лежит вне репозитория, в git не добавляется, в отчёт и журнал его содержимое не копируется —
только счётчики и ID задач.

Что важно знать о команде (по коду порций a–b):

- `created` считает только записи `_type=issue` (и записи без `_type`); записи с другим `_type`
  уходят в `ignored`, `total` = все записи. Поэтому эталон `N` — число issue-записей, а не `wc -l`.
- Каждый не-dry-run запуск делает UPSERT строки `projects`: `imported_at = now`,
  `import_note = "<created> задач из WriterLLM"`. На повторе `created = 0`, и `import_note`
  станет `0 задач из WriterLLM` — это поведение кода, не дефект; значение после первого импорта
  надо снять **до** повтора.
- Импорт коммитит по одной задаче; при падении на середине база остаётся наполовину
  импортированной. Штатный откат — `./bin/listik projects --remove writerllm --force`
  (`store.remove_project`): удаляет задачи проекта с комментариями, связями, событиями и FTS, но
  **не** чистит `embeddings` этих задач и записи `actor_aliases`. Полный откат — только восстановление
  из снимка базы (см. «Откат»).
- Исполнитель, автор записи и авторы комментариев проходят через `actors.resolve`: строка из
  `ALIASES`/`AGENT_HINTS`/`actor_aliases` сводится к `me`/`agent:*`, **любая другая остаётся
  ключом-человеком как есть** и через `actors.remember` необратимо оседает в `actor_aliases`,
  `tasks.assignee/created_by`, `comments.author`. Добавление алиасов — правка кода, за границами
  этой порции; значит, множество акторов надо увидеть **до** записи.
- В dry-run `store.add_dep` не вызывается: там видны только ошибки чтения/нормализации и
  «target task not found»; цикл среди старых `blocks` проявится только в отчёте реального импорта.

Живая база на старте не содержит задач `source='writerllm'` и проекта `writerllm`
(проверить: `sqlite3 listik.db "select count(*) from tasks where source='writerllm'"` → `0`).
В ней 3077 задач `beads` и ~10 `native`; ID вида `WriterLLM-*` в базе нет, коллизий не ожидается,
команда их всё равно переживёт (новый ID, старый в `external_ref`).

**Предусловие: порции a–c закоммичены**, `git status` чист (кроме `docs/specs/**`), `bd --version`
отвечает, каталог `~/Agents/WriterLLM/.beads` существует. Иначе порцию не начинать и вернуть
задачу оркестратору.

Перед началом прочитай: `README.md` — раздел «Импорт WriterLLM», `bin/listik` —
`cmd_import_writerllm`, `cmd_projects` (`--remove`, `--force`), `listik/import_writerllm.py`
(`import_file`: `ignored`, UPSERT `projects`, что делает `--update` и dry-run),
`listik/actors.py` (`resolve`, `remember`, `ALIASES`), `store.remove_project`.

## Рабочий каталог

Все артефакты — в стабильном каталоге вне репозитория и вне `$TMPDIR` (его чистит система):

```sh
WORK="$HOME/listik-import-writerllm-$(date +%F)"; mkdir -p "$WORK"; echo "$WORK"
```

Пути к файлам из `$WORK` идут в отчёт; судья читает их оттуда. В репозиторий ничего из `$WORK` не
попадает.

## Что сделать

### 1. Выгрузка и её паспорт

```sh
EXPORT="$WORK/writerllm-export.jsonl"
( cd ~/Agents/WriterLLM && bd export -o "$EXPORT" )
wc -l "$EXPORT"                      # справочно
python3 - "$EXPORT" <<'EOF'
import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
by_type = collections.Counter(r.get("_type", "issue") for r in rows)
issues = [r for r in rows if r.get("_type", "issue") == "issue"]
print("записей:", len(rows), "по _type:", dict(by_type))
print("N (issue):", len(issues))
print("D (сумма dependencies):", sum(len(r.get("dependencies") or []) for r in issues))
print("C (сумма comments):", sum(len(r.get("comments") or []) for r in issues))
print("статусы:", dict(collections.Counter(r.get("status") for r in issues)))
PATH_KEYS = ("spec_path", "spec", "journal_path", "journal", "log_path", "checklist_path", "checklist")
with_paths = [r["id"] for r in issues if any(str(r.get(k) or "").strip() for k in PATH_KEYS)]
print("записей с путями документов (spec/journal/checklist):", len(with_paths), with_paths[:10])
EOF
```

В отчёт: число записей, разбивка по `_type`, `N` = число issue-записей, `D`, `C`, статусы и
**число записей с путями документов**. Последнее — красный флаг паспорта: `_build_record` читает
ключи `spec_path|spec`, `journal_path|journal|log_path`, `checklist_path|checklist`, передаёт их в
`store.create_task`, а та вызывает `documents.index_task_documents`, которая **прочитает файлы по
этим путям** (из `~/Agents/WriterLLM` или откуда угодно) и положит их текст чанками в живую базу —
вне заявленного объёма «задачи, связи, комментарии» и с личными данными. По разведке 12.09.2026
таких ключей в выгрузке нет; если число больше нуля — **импорт не запускать**, вернуть задачу
оркестратору с этим числом и первыми ID (решение — чистить ключи в выгрузке или расширять объём
— за автором). Если ноль — так и записать в паспорт, ничего не меняется.

Далее все равенства ведутся к `N`; `ignored` в отчётах команды должен равняться числу не-issue
записей (`total − N`), а не нулю. Если `bd export` не отвечает, даёт 0 строк или файл не
оканчивается на `.jsonl` — остановиться, отчёт с текстом ошибки, ничего не импортировать.

### 2. Проверки только чтением — до любой записи

**2а. Dry-run на живой базе.**

```sh
./bin/listik import-writerllm --source "$EXPORT" --dry-run --json > "$WORK/dryrun.json"; echo "код: $?"
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print({k:r[k] for k in ("total","created","skipped","ignored","dependencies","comments")}); print("errors", len(r["errors"]), [(e["where"], e.get("external_ref"), e.get("target_ref"), e["error"]) for e in r["errors"][:10]])' "$WORK/dryrun.json"
sqlite3 listik.db "select count(*) from tasks where source='writerllm'"   # 0: dry-run не пишет
```

Ожидание: `created = N`, `skipped = 0`, `ignored = total − N`, ошибок `where="record"`/`"source"`
нет. Правило остановки: **любая ошибка `where="record"` или `where="source"` — импорт не
запускать**, вернуть задачу оркестратору с перечнем `(external_ref, error)` (без `raw`). Ошибки
`where="dependency"` в dry-run (`target task not found`, пустая цель) импорт не блокируют — они
пойдут в отчёт как известные потери; цикл в dry-run не виден, он может появиться только в отчёте
импорта (§3) и в отчёте повтора.

**2б. Множество акторов — read-only, до необратимой записи.**

```sh
python3 - "$EXPORT" <<'EOF'
import json, sys, sqlite3, collections
from listik import actors
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
raw_values = set()
for r in rows:
    if r.get("_type", "issue") != "issue": continue
    for k in ("assignee", "created_by", "holder"):
        if r.get(k): raw_values.add(str(r[k]))
    for c in r.get("comments") or []:
        if c.get("author"): raw_values.add(str(c["author"]))
    for d in r.get("dependencies") or []:
        if d.get("created_by"): raw_values.add(str(d["created_by"]))
conn = sqlite3.connect("file:listik.db?mode=ro", uri=True); conn.row_factory = sqlite3.Row
keys = collections.Counter(actors.resolve(v, conn)[0] for v in raw_values)
known = {k: n for k, n in keys.items() if k == "me" or (k or "").startswith("agent:")}
unknown = {k: n for k, n in keys.items() if k not in known}
print("различных сырых значений:", len(raw_values))
print("сведены к me/agent:*:", known)
print("НЕ сведены (останутся ключами как есть):", unknown)
EOF
```

Соединение только на чтение (`mode=ro`), `actors.remember` не вызывается. В отчёт — число
различных сырых значений, ключи `me`/`agent:*` с числом значений и **список несведённых ключей**
(это нормализованные строки, которые иначе стали бы акторами навсегда). Правило остановки:
**если несведённых ключей больше нуля — импорт не запускать**, вернуть задачу оркестратору с этим
списком: решение — добавить алиасы в `actors.ALIASES` отдельной порцией или принять эти ключи как
есть — принимает автор до записи, а не после. Если список пуст — идти дальше; в отчёте так и
записать: «несведённых акторов: 0».

### 3. Снимок базы и импорт

Снимок — обязателен, `.backup` (а не `cp`: база в WAL, сервер может быть поднят):

```sh
sqlite3 listik.db ".backup $WORK/listik-before-writerllm.db"
sqlite3 "$WORK/listik-before-writerllm.db" "pragma integrity_check; select count(*) from tasks; select source, count(*) from tasks group by source"
sqlite3 listik.db "select count(*) from tasks; select source, count(*) from tasks group by source"   # те же числа
```

В отчёт: путь к снимку, `ok` от `integrity_check`, счётчики (равны живой базе). Без снимка
импорт не запускать.

```sh
./bin/listik import-writerllm --source "$EXPORT" --json > "$WORK/import.json"; echo "код: $?"
python3 -c 'import json,sys; r=json.load(open(sys.argv[1])); print({k:r[k] for k in ("total","created","skipped","ignored","dependencies","comments","ok")}); print("errors", len(r["errors"]), [(e["where"], e.get("external_ref"), e.get("target_ref"), e["error"]) for e in r["errors"]])' "$WORK/import.json"
```

Сервер останавливать не нужно: команда пишет в SQLite напрямую (WAL), как `import-beads`;
если сервер поднят, фоновый поток досчитает векторы сам (Ollama может занять время — это не
условие приёмки). Если сервер не поднят — не поднимать ради порции.

**Сразу после импорта, до повтора** — снять строку проекта и счётчики (Б1: повтор перепишет
`import_note`):

```sh
sqlite3 listik.db "select slug, kind, imported_at, import_note from projects where slug='writerllm'" | tee "$WORK/project-after-import.txt"
sqlite3 listik.db "select count(*) from tasks where source='writerllm' and project='writerllm'; \
  select count(*) from deps d join tasks t on t.id=d.issue_id where t.source='writerllm'; \
  select count(*) from comments c join tasks t on t.id=c.task_id where t.source='writerllm'" | tee "$WORK/counts-after-import.txt"
```

Ожидание: `import_note` = `"<N> задач из WriterLLM"`, `kind='writerllm'`; счётчики — как в §4.

### 4. Повторный запуск и сверка

```sh
./bin/listik import-writerllm --source "$EXPORT" --json > "$WORK/rerun.json"; echo "код: $?"
sqlite3 listik.db "select slug, kind, imported_at, import_note from projects where slug='writerllm'"   # import_note = '0 задач из WriterLLM' — норма, см. Контекст
sqlite3 listik.db "select count(*) from tasks where source='writerllm' and project='writerllm'"
sqlite3 listik.db "select count(*) from deps d join tasks t on t.id=d.issue_id where t.source='writerllm'"
sqlite3 listik.db "select count(*) from comments c join tasks t on t.id=c.task_id where t.source='writerllm'"
sqlite3 listik.db "select count(*) from events e join tasks t on t.id=e.task_id where t.source='writerllm' and e.kind='import'"
sqlite3 listik.db "select count(*) from tasks where source='writerllm' and created_at >= date('now')"   # 0: даты не подменены
sqlite3 listik.db "select status, count(*) from tasks where source='writerllm' group by status"
sqlite3 listik.db "select id, external_ref from tasks where source='writerllm' and id != external_ref"  # переназначенные ID, ожидается пусто
sqlite3 listik.db "select assignee, count(*) from tasks where source='writerllm' group by assignee"       # только me / agent:* / NULL — или ключи, согласованные в 2б
./bin/listik --local show WriterLLM-yti4          # закрытая задача: даты 2026-09-05/06, «старый ID (writerllm): WriterLLM-yti4», причина закрытия
./bin/listik --local show WriterLLM-dyaw.4        # ID с точкой, связь parent-child на WriterLLM-dyaw
./bin/listik --local search WriterLLM-yti4 --mode text -n 3
./bin/listik --local list --project writerllm --all -n 5 --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print(type(d).__name__, len(d) if isinstance(d,list) else list(d)[:5])'
```

Если сервер поднят — дополнительно `./bin/listik status` (счётчик задач вырос на `N`) и
`./bin/listik show WriterLLM-yti4` без `--local` (тот же результат через HTTP).

Ожидания: повтор — `created=0`, `skipped=N`, `ignored` = `total − N`, счётчики задач/связей/
комментариев равны снятым в §3; число задач = `N`; число связей = `D` минус ошибки
`where="dependency"` из отчёта импорта; число комментариев = `C`; событие `import` у каждой из `N`
задач; проект `writerllm` есть с `kind='writerllm'`; ни у одной задачи `created_at` не сегодня.
Список ошибок повтора по природе тот же, что у импорта (те же несозданные рёбра пробуются снова).

### 5. Отчёт исполнителя

В отчёте (текстом оркестратору, не файлом в репозитории): `$WORK`; путь к выгрузке и её паспорт
(§1: записей, по `_type`, `N`, `D`, `C`, число записей с путями документов); счётчики dry-run; результат 2б (число сырых значений,
ключи `me`/`agent:*`, «несведённых акторов: 0» либо список и остановка); путь к снимку и его
`integrity_check`; счётчики импорта и повтора; содержимое `project-after-import.txt` и
`counts-after-import.txt`; список ошибок `dependency` (пары `external_ref → target_ref`, текст
ошибки; **без сырых записей**); вывод `show WriterLLM-yti4` без блока описания; состояние сервера.
Файлы в `$WORK` не удалять до приёмки (судья читает их) и в репозиторий не класть.

## Откат

Откат — **решение автора**, не самостоятельное действие исполнителя: при расхождении счётчиков,
падении на середине или ошибках `where="record"` в отчёте импорта исполнитель останавливается,
описывает состояние (счётчики, путь к снимку) и предлагает один из двух путей:

1. **Частичный, штатный:** `./bin/listik projects --remove writerllm --force` — удаляет задачи
   проекта `writerllm` с комментариями, связями, событиями, FTS и строку `projects`. **Не** удаляет
   строки `embeddings` этих задач (осиротеют; `embeddings.task_id` без задачи) и записи
   `actor_aliases`, добавленные `actors.remember` при импорте. Пригоден, если нужно просто убрать
   задачи и повторить импорт после правки.
2. **Полный:** восстановление из снимка `$WORK/listik-before-writerllm.db` через
   `sqlite3 listik.db ".restore …"` при **остановленном** сервере (`./bin/listik stop`, затем
   `serve --daemon` обратно). Это стирает всё, что записано в базу после снимка любыми
   агентами, — поэтому только с явного согласия автора и сразу после неудачного импорта.

Оба пути в этой порции исполнитель не выполняет без решения автора.

## Границы правки

- Порция **не меняет ни одного файла репозитория**: `git status --porcelain -- . ':!docs/specs'`
  до и после пуст. Обнаруженный дефект команды (падение, потерянные даты, неверный код возврата)
  — не чинить на месте: остановиться, вернуть задачу оркестратору с описанием и путём к отчёту
  dry-run; правка — отдельная порция. Правка `actors.ALIASES` — тоже отдельная порция.
- Не запускать `--update`, `import-beads`, `init-projects`, `bd import`, `bd` с записью в
  `~/Agents/WriterLLM` — только `bd export` (и, если понадобится проверить число, `bd list --json`
  в файл в `$WORK`).
- Не трогать существующие задачи живой базы; не удалять и не архивировать ничего; не менять
  `config.toml`; не останавливать и не перезапускать сервер (кроме отката по пути 2 — и только по
  решению автора). `projects --remove` и `.restore` без решения автора не выполнять.
- Скрипты §1 и §2б — только чтение (`mode=ro` для базы); `actors.remember`, `db.init()` без
  явного пути в этих скриптах не вызывать.
- В отчёт, журнал и ТЗ не копировать содержимое выгрузки (e-mail, тексты задач) — только ID,
  счётчики, статусы, даты и ключи акторов из 2б. Выгрузку, снимок и JSON-отчёты не добавлять в git
  (`git status` должен оставаться чистым; `listik.db` уже в `.gitignore`).
- Если счётчики разошлись с паспортом выгрузки (задач меньше `N` без соответствующих ошибок,
  связей меньше `D` минус ошибки), это красный результат порции, а не повод «дожать» ручным SQL;
  дальнейшее — по разделу «Откат».

## Как проверить

Пункты 1–4 выше и есть проверка. Ключевые равенства: `tasks(source='writerllm')` = `N` (число
issue-записей выгрузки); `ignored` = `total − N`; повтор даёт `created=0`; `created_at` не
сегодняшний ни у одной задачи; `project-after-import.txt` содержит `N задач из WriterLLM`;
снимок базы существует в `$WORK` и проходит `integrity_check`; несведённых акторов в 2б — 0;
записей с путями документов в паспорте — 0 (иначе импорт не запускался);
`show WriterLLM-yti4` и `search WriterLLM-yti4 --mode text` находят карточку;
`git status --porcelain -- . ':!docs/specs'` пуст.
