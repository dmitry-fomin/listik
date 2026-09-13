# Приёмка порции 04.c. `claim`: лок дерева, отказы с причиной, окно возврата

Все сценарии — на временной базе (`LISTIK_DB=/tmp/…`, `./bin/listik --local init`), тесты —
`python3 -m unittest`. «Красный до правки» — падает на коммите до порции; судья проверяет
чтением теста. Время «в прошлом» — прямым `UPDATE` по `events.ts`/`tasks.holder_at`.

## Тесты

1. Файл `tests/test_claim.py` существует, наследует `TempDbTestCase`,
   `python3 -m unittest tests.test_claim -v` зелёный; покрыты все сценарии раздела 5 ТЗ
   (блокер и `force`; идемпотентность с обновлением `holder_at`; чужой держатель и «брошена»;
   лок дерева, другое дерево, другой проект; `s1`/`s2` не лочат и не лочатся; прямая задача
   лочит; тот же держатель на двух; красный вердикт с `duration_s`; истечение окна; heartbeat
   сохраняет держателя; повторный `claim` продлевает окно (10a); окно проекта; `claim` после
   истечения; `worktree_busy` в `deps.ready`; CLI). Отсутствие любого — красный пункт.
2. `python3 -m unittest discover tests` зелёный целиком; существующие тесты не изменены
   (`git diff tests/ -- ':!tests/test_claim.py'` пуст).

## claim

3. `store.claim(conn, B, holder="codex")` при открытом блокере `A` (держит `dsh`) →
   `ValueError`; текст содержит `A`, заголовок `A`, `держит`, `Варианты`, `--force`.
   `force=True` берёт задачу; в `events` есть `note` с «ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ».
4. `claim(conn, A, holder="dsh")`, затем `holder_at` отодвинут на 2 ч назад прямым `UPDATE`,
   затем снова `claim(conn, A, holder="dsh")`: второй вызов возвращает карточку с `holder ==
   "dsh"`, `holder_at` не старше минуты, в `events` ровно одно событие `claim` и ни одного
   `release`/`heartbeat`, `status`/`assignee`/`holder_note` не изменились. Обновление
   `holder_at` — **красный до правки** (сегодня идемпотентная ветка выходит через `get_task`,
   не трогая строку).
5. `claim(conn, A, holder="codex")` при держателе `dsh` → `ValueError`; текст содержит `dsh`
   (или `DeepSeek Harness`), `release`, `Варианты`; при `holder_at` 30 ч назад — содержит
   `брошена`. **Красный до правки** (сегодня — только «уже удерживается …; сначала release»).
6. Лок дерева: `A` на `s3-impl`, worktree NULL, держит `dsh`; `claim(conn, C, holder="codex")`
   (`C` на `s3-impl`, тот же проект, worktree `''`) → `ValueError`, текст содержит
   `рабочее дерево`, `A`, `dsh`, `Варианты`; после `update_task(conn, C, worktree="/tmp/wt2")`
   — берётся; `C` в другом проекте — берётся.
7. Держатель на `s1-spec` в основном дереве **не** мешает `claim` порции на `s3-impl` в том же
   проекте и дереве. **Красный до правки.** Симметрично: при порции на `s3-impl` у `dsh` `claim`
   задачи на `s2-review` другим держателем проходит. **Красный до правки.**
8. Прямая задача без этапа у `dsh` блокирует `claim` порции на `s3-impl` и другой задачи без
   этапа другим держателем; задача на `s4-judge` у `dsh` блокирует `claim` порции на `s3-impl`
   другим держателем.
9. Тот же держатель на второй пишущей задаче в том же дереве — `claim` проходит.
10. `--force` не обходит чужого держателя (п. 5) и занятое дерево (п. 6): `claim(..., force=True)`
    в этих случаях — тот же `ValueError`.
11. `claim(conn, T, holder="codex", harness="codex")` при routing `s3-impl: ["dsh"]` —
    по-прежнему `ValueError` про harness; без `harness` — проходит (не изменилось; проверяется
    чтением `claim`: строка guard без изменений).

## Красный вердикт и окно возврата

12. Задача на `s4-judge` у `dsh`, `stage_at` 2 ч назад; `store.add_comment(conn, T,
    "красный: тест падает", author="agent:claude", kind="verdict")` → `stage == "s3-impl"`,
    `holder == "dsh"`, `stage_at` обновлён; последнее событие `stage`: `from_value ==
    "s4-judge"`, `to_value == "s3-impl"`, `duration_s` не `None` и ≥ 7000, `note` содержит
    «красного verdict», `actor == "agent:claude"`. `duration_s` — **красный до правки**
    (сегодня `None`). Комментарий `verdict` в `comments` есть, событие `comment` есть.
13. `add_comment(..., "зелёный: всё хорошо", kind="verdict")` на `s4-judge` этап не меняет.
14. `deps.expire_return_handoffs(conn)`: событие возврата 30 ч назад, `holder_at` 31 ч назад →
    возвращает 1, `holder == ''`, в `events` `release` с `from_value == "dsh"`, `to_value ==
    ''`, `note` содержит «истёк срок возврата» и «24»; `ready_tasks(conn)` содержит задачу.
15. Событие возврата 30 ч назад, затем `store.heartbeat(conn, T, holder="dsh")` →
    `expire_return_handoffs(conn)` возвращает 0, держатель на месте, события `release` нет.
    **Красный до правки** (сегодня снимает).
15a. Повторный `claim` продлевает окно: событие возврата 30 ч назад, `holder_at` 1 ч назад;
    `store.claim(conn, T, holder="dsh")` → `holder_at` обновлён на «сейчас», новых событий
    `claim`/`release` нет; затем `holder_at` отодвинут на 25 ч назад (старше окна, новее
    события возврата) → `expire_return_handoffs(conn)` возвращает 0, держатель на месте.
    **Красный до правки** (сегодня повторный `claim` `holder_at` не обновляет, и держателя
    снимут). Если же `holder_at` старше события возврата и окно истекло, повторный
    `claim(holder="dsh")` даёт в `events` по порядку `release` (истечение), затем `claim` —
    задача снова у `dsh`.
16. `update_project(conn, "demo", routing={"return_window_hours": 1})`: возврат 2 ч назад,
    `holder_at` 3 ч назад → снят; при значении по умолчанию — не снят.
17. `store.claim(conn, T, holder="codex")` при условиях п. 14 **без** предварительного `ready`
    → задача взята, `holder == "codex"`; в `events` по порядку: `release` (истечение), затем
    `claim`. **Красный до правки** (сегодня «уже удерживается dsh»). Реализация, которая после
    `expire_return_handoffs` проверяет держателя по строке, прочитанной до истечения, даст
    здесь «уже удерживается» — проверяется этим же тестом и чтением `claim` (после вызова
    истечения строка задачи перечитывается).
18. Задача на `s3-impl` с держателем, у которой **нет** события `s4-judge → s3-impl` (обычный
    handoff-claim), `expire_return_handoffs` не трогает, сколько бы часов ни прошло.

## deps_state

19. `deps.ready(conn, C)` при занятом дереве: `worktree_busy` — словарь с `id == A`, `holder`,
    `holder_title`, `holder_age`, `stale`; в `reasons` строка `дерево занято: A (держит …)`;
    `ready is True`, `claimable is True`, `verdict == "можно брать"` (лок не про эту задачу без
    держателя). При свободном дереве `worktree_busy is None`; у задачи на `s1-spec` при занятом
    дереве — `None`. **Красный до правки** (ключа нет).
20. `python3 -m unittest tests.test_context` зелёный; если менялся `documents.py` — только
    строка `_DEPENDENCIES_DEFAULTS` (`git diff listik/documents.py` показывает одно добавление
    ключа `worktree_busy`), `documents.context(conn, T, "s3-impl")["dependencies"]` содержит
    `worktree_busy`.

## CLI / HTTP

21. `./bin/listik --local claim <C> --holder codex` при занятом дереве → код 1, stderr одна
    строка `ошибка: рабочее дерево … занято задачей <A> …, держит …`, без `Traceback`.
22. `./bin/listik --local claim <A> --holder dsh` повторно → код 0, печатает карточку с
    держателем `dsh`. `./bin/listik --local claim <A> --holder codex` → код 1, stderr
    `ошибка: задача <A> уже удерживается …Варианты…`.
23. `./bin/listik --local show <C> --json` → `deps_state.worktree_busy` присутствует;
    `./bin/listik --local show <T>` после красного вердикта показывает этап `3. Реализация`
    и в истории событие перехода с длительностью (строка `stage` с ненулевой длительностью).
24. HTTP — сервер **на временной базе и нестандартном порту**, в отдельном терминале, без
    `--daemon`: `LISTIK_DB=/tmp/listik-step04c.db ./bin/listik --port 8799 serve` (демон при
    живом рабочем сервере не стартует из-за `listik.pid`, а порт 8787 занят рабочим сервером
    с реальной базой). Клиент: тот же `LISTIK_DB` и `--port 8799` у `./bin/listik …`, либо
    `curl -H "Authorization: Bearer $(./bin/listik token)" http://127.0.0.1:8799/…` (токен в
    отчёт не копировать). Проверить: `POST /api/tasks/{C}/claim` `{"holder": "codex"}` при
    занятом дереве → 400, `error` содержит `рабочее дерево`; при чужом держателе → 400 с
    `Варианты`; тело запроса и форма успешного ответа (`data` = карточка) не изменились;
    `POST /api/tasks/{T}/comment` `{"text": "красный: …", "kind": "verdict", "author":
    "agent:claude"}` → 200, затем `GET /api/tasks/{T}` показывает `stage == "s3-impl"` и
    событие с `duration_s`. После проверки: сервер остановлен `Ctrl-C`, `./bin/listik status`
    показывает прежний рабочий сервер на 8787, `git status` не показывает `listik.db`,
    `listik.pid`, `config.toml`. Пункт, выполненный на порту 8787 или без `LISTIK_DB`, —
    красный (риск записи в реальную базу).

## Границы

25. `git diff --stat` не содержит `web/`, `listik/db.py`, `listik/config.py`, `listik/server.py`,
    `listik/client.py`, `listik/mcp.py`, `bin/listik`, `README.md`, `API.md`,
    `docs/harness-protocol.md`, `AGENTS.md`; в `listik/store.py` изменены только `claim`,
    `add_comment` (ветка вердикта), `next_stage` (параметр `actor`); список подстрок красного
    вердикта в `add_comment` без изменений; `heartbeat` и `update_task` без изменений.
26. В `listik/server.py` не появился фоновый поток истечения окна (файл не в diff).
