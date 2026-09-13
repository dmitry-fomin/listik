# Приёмка порции 01.e, заход r2 — зелёный

Повторный заход. Проверялась **только разница** между дампами
`.git/feature-pipeline/step-01.diff-e.r1.txt` и `…r2.txt`; полный чек-лист заново не гонялся
(он зелёный по r1, кроме двух пунктов ниже).

## Что изменилось между заходами

`diff r1 r2` — три текстовые правки, ровно по красным пунктам r1, плюс сдвиг заголовков ханков
и хэшей blob'ов. Кода не тронуто: `API.md` +2 строки, `README.md` +1, `listik/mcp.py` +1
(итого 169+/14- против 165+/14-).

## Красные пункты r1

### R1 — закрыт

`API.md:86-89` теперь:

> Переиндексация происходит при вызове `context`/`create`/`update` для задачи
> (`index_task_documents`), а также фоновым воркером сервера… `show` файлы не читает и
> переиндексацию не запускает — он только отдаёт то, что уже лежит в таблице `documents`.

Сверено с кодом: `grep -rn index_task_documents listik/ bin/listik bin/listik-codex` даёт ровно
четыре вызова — `store.py:201` (create, под условием наличия путей), `store.py:311` (update),
`documents.py:589` (`context`), `documents.py:378` (`refresh_all`, фоновый воркер). В пути
`show`/`get_task` вызова нет. Формулировка совпадает и с решением порции b
(`docs/specs/steps/step-01.b.md:79-80`). Живой прогон r1 (ревизия менялась только после
`context`) остаётся в силе — код с тех пор не менялся.

### R2 — закрыт

`README.md:84-85`: «…а на s4 ещё и последний вердикт, журнал и состояние рабочего дерева
(на s3 этих трёх полей нет)».
`listik/mcp.py:124-126` (описание `listik_context`): «…на s4 ещё и последний вердикт, журнал и
состояние рабочего дерева (на s3 этих трёх полей нет)».

Сверено с `listik/documents.py:640-643`:

```python
verdict = verdicts_all[-1] if stage == "s4-judge" and verdicts_all else None
journal = _journal_items(conn, task_id, journals_all) if stage == "s4-judge" else []
worktree = _worktree(task) if stage == "s4-judge" else None
```

Текст согласован и с разделом «По этапам» в `API.md` того же диффа.

## Не сломано ли что-то в пределах разницы

- `listik/mcp.py` — изменена одна строка склеиваемого литерала описания. Живой `tools/list`
  через stdio на временной БД отдаёт `listik_context` с новым описанием,
  `required = ['id','stage']`, `properties = ['id','max_chars','portion','stage']` —
  пункты 11 и 13 чек-листа не пострадали.
- `python3 -m unittest discover tests` — `Ran 64 tests … OK`.
- `git diff --stat HEAD` — только `API.md`, `README.md`, `listik/mcp.py` (пункт 15).
- `git diff -- API.md README.md listik/mcp.py | grep -inE "token|password|secret|api_key|bearer|
  \.env|\.pem|\.key"` — пусто (пункт 16).

## Перенесено на приёмку шага

Нет.

## Замечания (не блокирующие, из r1 — остались)

- В перечне полей `limits` (`API.md:129-130`) не упомянут отдаваемый ключ `applies_to`
  (`documents.py:672`).
- Формулировка про `worktree` при недоступном каталоге (`API.md:146-147`) чуть строже кода:
  для «не git-репозиторий» отдаётся `exists: true, git: false, reason` (`documents.py:451-452`).
- «на s3 этих трёх полей нет» — в JSON ключи присутствуют со значениями `null`/`[]`; точный
  перечень из 15 ключей даёт `API.md:122-131`, так что расхождения в контракте нет.

## Чистота дерева

Временная БД MCP-прогона — в scratchpad, `listik.db`/`listik.pid` не затрагивались.
`git status --porcelain` до коммита — те же три изменённых файла плюс неотслеживаемые бумаги шага.
