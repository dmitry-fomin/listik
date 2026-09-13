# Порция 04.c. `claim`: лок рабочего дерева для пишущих этапов, отказы с причиной, окно возврата после красного вердикта

## Контекст

Listik — трекер задач на stdlib-Python: `bin/listik` (CLI + API-клиент через `call()`),
`listik/store.py` (`claim`, `heartbeat`, `add_comment`, `next_stage`, `update_task`, `event`),
`listik/deps.py` (`ready`, `ready_tasks`, `expire_return_handoffs`), `listik/config.py`
(`routing(...)["return_window_hours"]`, `transition_kind`), `listik/server.py` (действия
`claim`, `heartbeat`, `stage`, `comment`, `release`), `listik/client.py` (`local_call`),
`listik/mcp.py`. Тесты — `tests/`, `unittest`, `TempDbTestCase`.

Модель: держатель — `tasks.holder`, его последний heartbeat — `tasks.holder_at` (обновляется
`claim` через `update_task(holder=…)` и каждым `heartbeat`, событие `heartbeat` пишется не чаще
раза в 10 минут); этап — `tasks.stage`, момент входа — `stage_at`; переходы — `next_stage` →
`update_task(stage=…)`, которая пишет событие `stage` с `duration_s` прошлого этапа; вид
перехода — `config.transition_kind` (`s2→s3`, `s4→done` handoff — держатель снимается,
`s1→s2`, `s3→s4` sticky, `s4→s3` sticky-return). Рабочее дерево — `tasks.worktree` (NULL/пусто
= основное дерево репозитория), `tasks.project` = репозиторий.

Требования (спека шага 04, п. 3, 6, 7 и критерии приёмки): `claim` отклоняет задачу с открытым
hard blocker и сообщает блокер, holder и варианты действий; sticky/handoff и окно возврата
после красного вердикта — в журнале событий; лок `(repository, worktree)`, пустой worktree —
основное дерево, конфликтует с другим пишущим holder; «красный verdict возвращает задачу
исполнителю в установленное окно, затем корректно release»; «повторный `claim` в том же
worktree безопасно отказывает или подтверждает текущего holder».

Решение автора (12.09.2026): **лочат только пишущие задачи** — держатель на `s3-impl`,
`s4-judge` или задача без этапа (прямая задача «claim → код → done»). Держатели на `s1-spec`/
`s2-review` дерево не занимают, и их собственный `claim` о дерево не спотыкается. Guard по
harness в `claim` — только при явном `--harness` (уже так, не менять).

Что есть сейчас (`store.claim`, `add_comment`, `deps.expire_return_handoffs`; проверено на
временной базе 12.09.2026):

- `claim`: порядок проверок — задача есть → guard harness → финальный статус → держатель (тот
  же → идемпотентно вернуть карточку; чужой → `ValueError("уже удерживается X; сначала
  release")`) → лок дерева: любая **открытая задача того же проекта с тем же `worktree` и
  чужим держателем на любом этапе** → `ValueError("worktree занят задачей …")` → блокеры →
  `update_task(holder=…, status=in_progress)`. Отказ по блокеру уже печатает «Ждём завершения:
  <id> — <title> [status, держит X]» и «Варианты: взять сам блокер, поставить блокеру
  needs-owner, или claim --force». Проверено: карточка на `s1-spec` в основном дереве не даёт
  взять порцию на `s3-impl` — против решения автора.
- Красный вердикт: `add_comment(kind="verdict")` с подстрокой «красн»/«red»/«fail»/«не прой»/
  «❌» на `s4-judge` делает прямой `UPDATE tasks SET stage='s3-impl'` и пишет событие `stage`
  **без `duration_s`** и мимо `update_task` (`stage_at` ставится, но длительность `s4` теряется).
  Держатель не меняется (sticky-return) — верно.
- `deps.expire_return_handoffs(conn)`: для задач на `s3-impl` с держателем находит последнее
  событие `stage s4-judge→s3-impl`; если оно старше `return_window_hours` (по умолчанию 24,
  переопределяется routing проекта) — снимает держателя и пишет событие `release` с заметкой
  «истёк срок возврата после красного verdict». **Не смотрит на активность после возврата**:
  держатель с heartbeat минуту назад снимается (проверено). Вызывается только из `ready_tasks`;
  `claim` чужим держателем после истёкшего окна всё равно отвечает «уже удерживается».
- `deps.ready` (`deps_state` в `show`) про лок дерева ничего не знает; `reasons` — только
  блокеры, держатель, завершённость.

Перед началом прочитай: в `listik/store.py` — `claim`, `heartbeat`, `add_comment`,
`next_stage`, `update_task` (ветки `stage` и `holder`), `row_to_task` (поля `holder_title`,
`holder_age`, `stale`, `idle_age`), `hours_since`, `now_iso`, `event`; в `listik/deps.py` —
`ready`, `ready_tasks`, `expire_return_handoffs`, `_info`; в `listik/config.py` — `routing`,
`transition_kind`; в `bin/listik` — `_holder_action`, `cmd_claim`, `cmd_stage`, `cmd_comment`;
в `listik/server.py` — ветки `claim`/`stage`/`comment`; `tests/test_needs_owner.py` — образец.

## Что сделать

### 1. `store.claim`: порядок проверок и лок дерева только для пишущих

Сигнатура не меняется: `claim(conn, task_id, *, holder, harness=None, note=None,
force=False) -> dict`. Порядок:

1. Задачи нет → `KeyError`. Финальный статус → `ValueError("задача … уже done|cancelled")`.
2. Guard harness — как сейчас (только при непустом `harness`).
3. `deps.expire_return_handoffs(conn, task_id=task_id)` (см. п. 3) — если окно возврата у
   **этой** задачи истекло, держатель снят до проверки п. 4. **После вызова перечитать строку
   задачи (`row = conn.execute("SELECT * FROM tasks WHERE id = ?", …)`)**: истечение делает
   `UPDATE … holder=''` и `commit`, а `row`, прочитанная в п. 1, этого не видит — иначе п. 4
   ответит «уже удерживается» и сценарий п. 5.12 (`claim` после истёкшего окна) упадёт.
4. Держатель: тот же — **обновить `holder_at` (и `updated_at`) прямым `UPDATE tasks SET
   holder_at = ?, updated_at = ? WHERE id = ?`, без события и без изменения других полей**,
   и вернуть карточку (`get_task`). Это продлевает окно возврата (п. 3): агент, который после
   перерыва делает `claim`, а не `heartbeat`, не будет снят как «молчащий». Нового события
   `claim` не пишется (идемпотентность по журналу сохраняется); статус, `assignee`, `holder_note`
   не трогаются. Чужой → `ValueError` с текстом:
   `задача <id> уже удерживается <holder_title> (<holder_age>[, молчит — брошена?]).
   Варианты: дождаться, listik release <id> (если держатель мёртв), или взять другую задачу из
   listik ready`. Пометка «молчит — брошена?» — если `row_to_task(...)["stale"]` истинен.
5. Лок дерева — только если **сама** задача пишущая: `stage` ∈ {`s3-impl`, `s4-judge`} или
   `stage` пустой/NULL. Конфликт — другая задача того же `project` (сравнивать
   `coalesce(project,'')`), не архивная, статус в `('open','in_progress','review','blocked')`,
   `coalesce(worktree,'') = coalesce(<worktree этой задачи>,'')` (после `strip()`), с непустым
   держателем `≠ holder`, **и сама пишущая** (тот же критерий по `stage`). Первый конфликт →
   `ValueError`: `рабочее дерево <'основное' | путь> проекта <project> занято задачей <id>
   (<title[:80]>), держит <holder_title> <holder_age>[, молчит — брошена?]. Варианты: дождаться
   или listik release <id>, указать этой задаче другое дерево (listik set <id>
   worktree=/путь), или взять другую задачу`.
   Задача на `s1-spec`/`s2-review` ни сама не проверяется, ни как конфликт не считается.
   Тот же держатель на двух задачах в одном дереве — допустимо (sticky: судья и исполнитель в
   одной сессии).
6. Блокеры — как сейчас (`force` обходит только их, с событием `note` «ЗАПУСК БЕЗ РАЗРЕШЕНИЯ
   БЛОКЕРОВ»); текст отказа оставить, он уже содержит блокер, статус, держателя и варианты.
7. `update_task(holder=…, assignee=…, status=in_progress при open)` — как сейчас.

Тексты п. 4–5 — одной-двумя строками, без переносов внутри предложения; они уходят в
`ошибка: …` CLI и в `error` HTTP 400.

### 2. Красный вердикт — через `next_stage`

В `add_comment` при красном вердикте на `s4-judge` вместо прямого `UPDATE` вызвать
`next_stage(conn, task_id, to_stage="s3-impl", actor=author, harness=harness,
note="возврат после красного verdict")`. Для этого `next_stage` получает необязательный
параметр `actor: str | None = None` и передаёт его в `update_task(actor=…)`. Результат: одно
событие `stage` `s4-judge → s3-impl` с `duration_s` (сколько длилась проверка), `note`
«возврат после красного verdict», актором судьи; `stage_at` = момент вердикта; держатель не
меняется (`transition_kind` = `sticky-return`, `holder` в `next_stage` не передаётся).
Детектор красного (список подстрок) не менять. `add_comment` не должна коммитить дважды с
несогласованным состоянием: комментарий и переход — в одной транзакции (`update_task` сама
делает `commit`; порядок — сначала INSERT комментария, потом переход, потом `_index_comment`
и `commit`, как сейчас).

### 3. `deps.expire_return_handoffs(conn, *, task_id=None) -> int`

- Область: все задачи на `s3-impl` с непустым держателем, либо одна (`task_id`).
- Для задачи берётся последнее событие `stage` с `from_value='s4-judge'`, `to_value='s3-impl'`;
  нет события → не трогать.
- Окно: `config.routing(project, conn)["return_window_hours"]` (число часов; по умолчанию 24).
- Условие снятия: `hours_since(ev.ts) > окно` **и** держатель не проявлял активность после
  возврата: `holder_at` пустой **или** `holder_at <= ev.ts` (сравнение ISO-строк одного
  формата `%Y-%m-%dT%H:%M:%SZ` допустимо; `parse_ts` — надёжнее). `heartbeat` (уже сегодня)
  и повторный `claim` тем же держателем (п. 1.4 — это новое поведение, без него повторный
  `claim` окно **не** продлевал бы) после возврата обновляют `holder_at` → держатель
  остаётся, дальше его судьба — обычная логика «брошена» на доске.
- Снятие: `UPDATE tasks SET holder='', updated_at=…`, событие `release` с `from_value` =
  старый держатель, `to_value=''`, `note = "истёк срок возврата после красного verdict
  (<окно> ч без активности)"`, актор пустой. `commit`, если что-то снято. Возвращает число снятых.
- Вызовы: `ready_tasks` (как сейчас, вся база) и `store.claim` (п. 1.3, одна задача).

### 4. `deps.ready`: причина «дерево занято» в `reasons`

В `deps.ready(conn, task_id)` (это `deps_state` в `show` и `POST /api/tasks/{id}/ready`)
добавить ключ `worktree_busy`: `None`, если дерево свободно или задача не пишущая; иначе
словарь `{"id", "title", "holder", "holder_title", "holder_age", "stale"}` конфликтующей
задачи по тому же критерию, что в п. 1.5 (вынести критерий в одну функцию
`deps.worktree_conflict(conn, row, holder=None) -> sqlite3.Row | None`, чтобы `claim` и
`ready` считали одинаково). При конфликте — строка в `reasons`: `дерево занято: <id> (держит
<holder_title>)`; `ready` и `claimable` при этом **не** меняются (лок — про конкретного
держателя, `ready` без держателя оценить нельзя), `verdict` не меняется. Новый ключ
добавляется в конец словаря; `documents.context` на `s3`/`s4` копирует `deps_state` через
`_stable` — новый ключ `None`/словарь сериализуем, тест `tests/test_context.py` должен
остаться зелёным (если он сравнивает набор ключей `dependencies` с эталоном — эталон
`_DEPENDENCIES_DEFAULTS` в `documents.py` дополнить ключом `worktree_busy: None`; это
единственное допустимое касание `documents.py`).

### 5. Тесты: `tests/test_claim.py`

На `TempDbTestCase`. Время «в прошлом» задаётся прямым `UPDATE events SET ts=…` /
`UPDATE tasks SET holder_at=…` строками формата `store.now_iso()` минус N часов
(`datetime` арифметика). Обязательные сценарии:

1. отказ по блокеру: `claim(B)` при открытом блокере `A`, который держит `dsh` → `ValueError`,
   текст содержит `A`, заголовок `A`, `держит`, `Варианты`, `--force`; `claim(B, force=True)`
   берёт и пишет событие `note` с «ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ»;
2. идемпотентность: `claim(A, holder="dsh")`, затем `holder_at` отодвинуть на 2 ч назад
   прямым `UPDATE`, затем снова `claim(A, holder="dsh")` → карточка с тем же держателем,
   `holder_at` свежий (не старше минуты), событий `claim` в `events` ровно одно, `status`,
   `assignee`, `holder_note` не изменились — обновление `holder_at` **красный до правки**;
3. чужой держатель: `claim(A, holder="codex")` при держателе `dsh` → `ValueError`, текст
   содержит `dsh` (или его `holder_title`), `release`, `Варианты`; при `holder_at` 30 часов
   назад — текст содержит `брошена`;
4. лок дерева: `A` на `s3-impl` держит `dsh` (worktree NULL); `claim(C на s3-impl, holder=
   "codex")` в том же проекте с worktree `''` → `ValueError` с `рабочее дерево`, `A`, `dsh`,
   `Варианты`; с `worktree="/tmp/wt2"` у `C` → берётся; в другом проекте → берётся;
5. **держатель на `s1-spec` не лочит**: `S` на `s1-spec` держит `claude` (основное дерево);
   `claim(P на s3-impl, holder="dsh")` в том же проекте и дереве → берётся — **красный до
   правки**; симметрично `claim(S2 на s2-review, holder="grok")` при `P` на `s3-impl` у `dsh`
   → берётся — **красный до правки**;
6. прямая задача (без этапа) лочит и лочится: `D` без этапа у `dsh`; `claim(P на s3-impl,
   holder="codex")` → отказ; `claim(D2 без этапа, holder="codex")` → отказ;
7. тот же держатель на двух пишущих задачах в одном дереве → второй `claim` проходит;
8. красный вердикт: задача на `s4-judge` у `dsh` (пройти `claim` → `next_stage` из `s3-impl`,
   `stage_at` отодвинуть на 2 часа назад прямым UPDATE); `add_comment(kind="verdict",
   text="красный: тест падает", author="agent:claude")` → `stage == "s3-impl"`, `holder ==
   "dsh"`, последнее событие `stage` имеет `from_value="s4-judge"`, `to_value="s3-impl"`,
   `duration_s` не `None` и ≥ 7000, `note` содержит «красного verdict» — `duration_s` **красный
   до правки**; зелёный вердикт этап не меняет;
9. истечение окна без активности: после п. 8 событие возврата отодвинуть на 30 ч назад,
   `holder_at` — на 31 ч; `expire_return_handoffs(conn)` → 1, `holder == ''`, событие `release`
   с `from_value="dsh"` и `note` про «истёк срок возврата»; `ready_tasks` содержит задачу;
10. **активность после возврата сохраняет держателя**: событие возврата 30 ч назад, но
    `heartbeat(holder="dsh")` сейчас → `expire_return_handoffs` → 0, держатель на месте —
    **красный до правки**;
10a. **повторный `claim` продлевает окно** (ловит расхождение между п. 1.4 и п. 3): событие
    возврата 30 ч назад, `holder_at` — 1 ч назад (держатель ещё жив); затем `claim(task,
    holder="dsh")` тем же держателем → `holder_at` обновлён на «сейчас», в `events` не
    появилось ни `claim`, ни `release`; затем `holder_at` снова отодвинуть на 25 ч назад (то
    есть старше окна, но новее события возврата) — `expire_return_handoffs(conn)` → 0
    (активность после возврата была). **Красный до правки** (сегодня повторный `claim`
    `holder_at` не трогает). Отдельно: если `holder_at` **старше** события возврата и окно
    истекло, то повторный `claim(holder="dsh")` сначала (п. 1.3) снимет держателя (`release`),
    а затем возьмёт задачу заново (`claim`) — это ожидаемо, проверяется одним assert на
    порядок событий `release`, `claim`;
11. окно проекта: `upsert_project("demo")` + `update_project(routing={"return_window_hours":
    1})` (валидация — порция b); возврат 2 ч назад, `holder_at` 3 ч назад → снят; с
    настройкой по умолчанию (24) — не снят;
12. **`claim` после истёкшего окна**: условия п. 9, без предварительного `ready`;
    `claim(task, holder="codex")` → берётся, в `events` есть `release` (истечение) и затем
    `claim` — **красный до правки** («уже удерживается»);
13. `deps.ready(conn, C)` при занятом дереве → `worktree_busy["id"] == A`, в `reasons` есть
    строка `дерево занято`; `ready` истинен (без держателя, без блокеров); при свободном дереве
    `worktree_busy is None`; у задачи на `s1-spec` при занятом дереве — `None`;
14. CLI: `--local claim <C> --holder codex` при занятом дереве → код 1, stderr `ошибка:
    рабочее дерево …`, без `Traceback` (обёртка `main()` из порции a); `--local claim <A>
    --holder dsh` повторно → код 0; `--local show <C> --json` содержит `deps_state.worktree_busy`.

## Границы правки

- Файлы: `listik/store.py` (`claim`, `add_comment` — только ветка красного вердикта,
  `next_stage` — параметр `actor`), `listik/deps.py` (`expire_return_handoffs`,
  `ready`, новая `worktree_conflict`), `listik/documents.py` — только `_DEPENDENCIES_DEFAULTS`
  при необходимости, `tests/test_claim.py` (новый).
- Не трогать: детектор красного вердикта (подстроки), `heartbeat`, `update_task`,
  `transition_kind`/`DEFAULTS` в `config.py`, `server.py`/`client.py`/`mcp.py` (действия и
  тела запросов не меняются: `claim {holder, harness, note, force}`), `bin/listik` (тексты
  отказов приходят из store), `web/`, `db.py` (схема без изменений — новых колонок нет),
  `add_dep`/`remove_dep` (порция a), routing (порция b), документацию (порция d).
- Guard harness в `claim` не расширять и harness из держателя не выводить — решение автора.
- `--force` не должен обходить чужого держателя и занятое дерево.
- Фонового потока для истечения окна в `server.py` не добавлять — истечение ленивое
  (`ready`, `claim`).
- Существующие тесты не править; `tests/test_context.py` должен остаться зелёным без правок
  (кроме случая, описанного в п. 4, когда правится эталон в `documents.py`, а не тест).
- Реальную `listik.db` не трогать.

## Как проверить

HTTP-путь — **только** сервером на временной базе и нестандартном порту, в отдельном
терминале и без `--daemon` (демон при живом рабочем сервере откажется стартовать по
`listik.pid`, а с обычным портом 8787 упрётся в «address in use» или уйдёт в реальную базу):
`LISTIK_DB=/tmp/listik-step04c.db ./bin/listik --port 8799 serve`; клиентские команды — с тем
же `LISTIK_DB` и `--port 8799`; токен — `./bin/listik token` (читается из `config.toml`, в
отчёт не копировать); после проверки сервер остановить `Ctrl-C`.

```sh
export LISTIK_DB=/tmp/listik-step04c.db && ./bin/listik --local init
new() { ./bin/listik --local new "$1" -p demo ${2:+--stage $2} --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])'; }
S=$(new "карточка шага" s1-spec); P=$(new "порция" s3-impl); Q=$(new "порция 2" s3-impl)
./bin/listik --local claim $S --holder claude                 # держит s1
./bin/listik --local claim $P --holder dsh                    # берётся: s1 дерево не занимает
./bin/listik --local claim $Q --holder codex; echo rc=$?      # ошибка: рабочее дерево … занято задачей $P, держит dsh …
./bin/listik --local claim $P --holder dsh                    # повтор — тихо подтверждает
./bin/listik --local stage $P --holder dsh                    # s3 → s4 (sticky)
./bin/listik --local comment $P "красный: тесты падают" -k verdict --actor agent:claude
./bin/listik --local show $P                                  # этап s3-impl, держатель dsh, событие stage с длительностью
sqlite3 $LISTIK_DB "update events set ts=strftime('%Y-%m-%dT%H:%M:%SZ','now','-30 hours') where task_id='$P' and kind='stage' and to_value='s3-impl'; update tasks set holder_at=strftime('%Y-%m-%dT%H:%M:%SZ','now','-31 hours') where id='$P'"
./bin/listik --local claim $P --holder codex                  # берётся: окно истекло, в истории release + claim
python3 -m unittest tests.test_claim -v && python3 -m unittest discover tests
```
