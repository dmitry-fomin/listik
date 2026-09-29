Рабочее дерево: {{TREE}}

# Чек-лист приёмки listik-ds5l, порция a

База: коммит HEAD. «Без ключа» — тот же вызов `mcp.call_tool`, но без этого аргумента
в `args`.

`null` = умолчание, по инструментам (каждый пункт проверяется тестом из
`tests/test_mcp_null_args.py`. На базе HEAD, с `listik/mcp.py` из базы, этот тест падает
с `TypeError`, после правки проходит):
- [ ] `listik_search` с `"limit": None`, `"mode": "text"`: `hits` не пусты и равны `hits` без
      ключа.
- [ ] `listik_list` с `"limit": None`: в ответе `limit == 50` (не 200 и не `None`), набор id
      совпадает с вызовом без ключа.
- [ ] `listik_create` с `"priority": None`: у созданной задачи приоритет `2`, и в ответе,
      и в `store.get_task`.
- [ ] `listik_dep_tree` с `"depth": None` на цепочке E→D→C→B→A: по `waits_for`/`up` видны
      ровно D, C, B, у B `up == []`. Без ключа `depth` — то же.
- [ ] `listik_memory` с `"limit": None` без `query` и с `query`: `items` не пусты и равны
      вызову без ключа, в обеих ветках.
- [ ] `listik_timeline` с `"limit": None`: `items` не пусты и равны вызову без ключа.
- [ ] `listik_deps_suggested` с `"limit": None`: `items` не пусты, содержат предложенную связь
      и равны вызову без ключа.
- [ ] Через `mcp.handle` (`tools/call`) вызов с `null` в числовом аргументе даёт ответ без
      `isError`. На базе HEAD там `isError: true`.

Прежнее поведение не сломано:
- [ ] `tests/test_ready_limit.py` и `tests/test_blocked_limit.py` зелёные без правок, включая
      `test_mcp_null_limit_as_absent` и `limit: 0` = без ограничения у `ready`/`blocked`.
- [ ] `tests/test_mcp_tools.py` зелёный без правок.
- [ ] Нечисловое значение по-прежнему даёт ошибку. Ручная проверка:
      `mcp.call_tool("listik_list", {"limit": "abc"}, conn=...)` бросает `ValueError`,
      умолчание вместо неё не подставляется.

Чтение диффа `listik/mcp.py`:
- [ ] В `mcp.py` один хелпер для числовых аргументов с параметром-ключом. Старого `_limit`
      рядом с ним нет.
- [ ] `grep -n "int(args" listik/mcp.py` пуст. Все девять мест (`search`, `list`, `create`,
      `dep_tree`, `memory`, `timeline`, `deps_suggested`, `ready`, `blocked`) идут через
      хелпер, с умолчаниями 10, 50, 2, 3, 20, 100, 100, 30, 50.
- [ ] Докстринг хелпера не утверждает «`0` — без ограничения» для всех инструментов.
- [ ] Вокруг `int()` нет `try/except`.

Границы (`git diff --stat HEAD` и `git status --short`, новый файл может быть ещё не закоммичен):
- [ ] Изменены только `listik/mcp.py` и новый `tests/test_mcp_null_args.py`.
- [ ] Схемы `TOOLS` в `mcp.py` не менялись. Нечисловые `args.get(key, default)` (`type`,
      `order`, `mode`, `kind`, `dep_type`, `group_by`, `description`, `acceptance`, `value`)
      не менялись.

## Проверки порции

Из корня дерева `{{TREE}}`:

```
python3 -m unittest tests.test_mcp_null_args tests.test_ready_limit tests.test_blocked_limit tests.test_mcp_tools
git diff --stat HEAD
git status --short
grep -n "int(args" listik/mcp.py
```

Линтера и проверки типов в проекте нет.
