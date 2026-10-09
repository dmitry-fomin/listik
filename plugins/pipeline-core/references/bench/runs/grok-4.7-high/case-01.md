судья: grok-4.7-high
модель: grok-4.7 (effort high)
начало: 2026-09-29T12:12:18Z
конец: 2026-09-29T12:19:18Z
секунд: 420
---
Читаю чек-лист, порцию и пакет диффа, затем прогоню каждый пункт по факту.Тесты чек-листа зелёные. Дальше сверю `LIMIT` и инвариант живым сценарием: в диффе параметр не равен `limit`.`limit=1` уходит в запрос как `-1` и возвращает все три свободные задачи. Доберу отказ `claim` и поведение старого кода.красный

1. Ок. NEW `limit=2` n=2 ids=['demo-lhur', 'demo-t49p']; на HEAD `a8bbb99` OLD `limit=2` n=0 ids=[]. `test_ready_tasks_limit_skips_blocked_above` ok.
2. Ок. NEW X `waiting_for_count=10`, F1 `waiting_for_count=0`. `test_ready_tasks_waiting_for_count` ok.
3. Ок. NEW `limit=0` n=3; OLD `limit=0` n=3. `test_ready_tasks_limit_zero_is_unlimited` ok.
4. Ок. `blocked_by=[]`, `A_in_ready=False`, `claimable=False`. `test_ready_tasks_uses_live_rule_not_column` ok.
5. Ок. `test_ready_tasks_every_hard_type_and_missing_blocker` ok.
6. Доделать. `listik/deps.py:464` передаёт `limit if limit > 1 else -1`. Зонд: `limit=1` → SQL `limit_param=-1`, n=3 ids=['demo-lhur', 'demo-t49p', 'demo-hq0u']. `limit=None` → `TypeError: '>' not supported between instances of 'NoneType' and 'int'` (HEAD на том же сценарии: OLD `limit=None` n=3). При истинном `limit`, включая 1, параметр `LIMIT` должен быть равен `limit`; при 0 и None лимита нет и исключения нет. Закрепить оба случая тестом на фикстуре F1–F3.
7. Ок. SQL `not_exists=1 blocked_by_col=False`; сценарий п.4 A в выдачу не пускает.
8. Ок. Правило одно, в `_open_hard_sql` (`listik/deps.py:94`), вызовы `listik/deps.py:118` и `listik/deps.py:443`; `coalesce` только `listik/deps.py:102`; типы из `HARD_BLOCKERS`, статусы из `FINAL_STATUSES`. Докстринг `open_hard_edges` это описывает.
9. Ок. `tests.test_open_hard_edges` целиком, включая `test_filters`, ok.
10. Ок. Порядок `t.priority ASC, t.updated_at DESC` на месте. `NotTakenTests`, `ExpireReturnWindowTests`, `ServerModeListCase`, `ScopeCardTests` ok.
11. Ок. `git diff --name-only`: `listik/deps.py`, `tests/test_open_hard_edges.py`. Удалений строк в тесте нет.
12. Ок. Хунки только помощник, `open_hard_edges`, `ready_tasks`.
13. Ок. Зонд: `blocked_by=[]`, `A_in_ready=False`, `claimable=False`, `verdict='нельзя: ждёт другие задачи'`, `claim=ValueError задача demo-2ued заблокирована и брать её нельзя.`

Срезанных углов нет: в тесте только добавления, прежние ассерты целы, skip/xfail нет, чужих путей нет.

Проверки: `python3 -m unittest` — Ran 64 tests in 3.112s, OK. `grep -n "3000\|limit \* 3" listik/deps.py` пусто, exit 1. Полный `unittest discover` не запускал: чек-лист запрещает, дифф не трогает сборку и зависимости.

Негативный контроль: `claim` отказал — `INVARIANT claim=ValueError задача demo-2ued заблокирована и брать её нельзя.`

Коммита нет.
