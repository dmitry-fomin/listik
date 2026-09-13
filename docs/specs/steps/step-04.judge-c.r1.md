# Приёмка порции 04.c, заход r1 — вердикт зелёный

Чек-лист: `docs/specs/steps/step-04.check-c.md`. Порция: `docs/specs/steps/step-04.c.md`.
Пакет диффа: `.git/feature-pipeline/step-04.diff-c.r1.txt` (4 файла: `listik/store.py`,
`listik/deps.py`, `listik/documents.py`, новый `tests/test_claim.py`).
Все прогоны — на временных базах (`LISTIK_DB` в scratchpad); реальная `listik.db`
не менялась (mtime 12 сент. 19:42, до начала приёмки), `config.toml` не тронут.

## Тесты

1. **зелёный.** `python3 -m unittest tests.test_claim -v` — 25 тестов, OK. Файл наследует
   `TempDbTestCase` (`tests/test_claim.py:393` и далее), в подпроцессе CLI подставляет
   `LISTIK_DB=self.db_path`. Сценарии раздела 5 ТЗ покрыты все:
   блокер + `force` (`ClaimBlockerTests`), идемпотентность с `holder_at`
   (`ClaimIdempotencyTests`), чужой держатель и «брошена» (`ClaimOtherHolderTests`),
   лок дерева / другое дерево / другой проект (`WorktreeLockTests`), `s1`/`s2` не лочат
   (`NonWritingStageDoesNotLockTests`), прямая задача (`DirectTaskLocksTests`), тот же
   держатель на двух (`SameHolderTwoWritingTasksTests`), красный вердикт с `duration_s`
   и зелёный без перехода (`RedVerdictTests`), истечение окна, heartbeat сохраняет
   держателя, повторный `claim` продлевает окно (10a) и порядок `release`→`claim`
   (`ExpireReturnWindowTests`), окно проекта (`ProjectReturnWindowTests`), `claim` после
   истечения (`ClaimAfterExpiredWindowTests`), `worktree_busy` в `deps.ready`
   (`ReadyWorktreeBusyTests`), CLI (`ClaimCliTests`).

   Дополнительно проверил, что «красные до правки» действительно красные: во временном
   `git worktree` на `HEAD` (478c2b8) те же тесты дают 8 FAIL + 7 ERROR, в том числе
   `test_repeated_claim_refreshes_holder_at_without_new_event`,
   `test_red_verdict_returns_to_s3_with_duration`, `test_activity_after_return_keeps_holder`,
   `test_repeated_claim_extends_window`, `test_claim_takes_over_after_expired_window`,
   `test_holder_on_s1_spec_does_not_lock_worktree`,
   `test_holder_on_s2_review_does_not_lock_worktree`, все `ReadyWorktreeBusyTests`,
   `test_other_holder_gets_release_hint`, `test_stale_holder_mentions_abandoned`.
   Тесты написаны под требование, а не под реализацию. Временное дерево удалено.
2. **зелёный.** `python3 -m unittest discover tests` — 171 тест, OK.
   `git diff tests/` пуст, `tests/test_claim.py` — единственный новый файл.

## claim

3. **зелёный.** `ClaimBlockerTests`: текст отказа содержит id блокера, его заголовок,
   «держит», «Варианты», «--force»; с `force=True` задача берётся и в `events` появляется
   `note` «ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ».
4. **зелёный.** Свой прогон: после `claim`, отката `holder_at` на 2 ч и повторного `claim` —
   `holder` тот же, `holder_at` свежий (<1 мин), событий `claim` ровно одно, событий
   `release`/`heartbeat` нет, `status`/`assignee`/`holder_note`/`stage` не изменились
   (`listik/store.py:381-389` — точечный `UPDATE tasks SET holder_at, updated_at`).
5. **зелёный.** Текст: `задача <id> уже удерживается dsh (0 мин). Варианты: дождаться,
   listik release <id> (если держатель мёртв), или взять другую задачу из listik ready`;
   при `holder_at` 30 ч назад добавляется «, молчит — брошена?» (`listik/store.py:390-396`).
6. **зелёный.** `рабочее дерево основное проекта demo занято задачей <A> (<title>), держит
   dsh 0 мин. Варианты: …` (`listik/store.py:397-410`); при `worktree="/tmp/wt2"` и при
   другом проекте задача берётся.
7. **зелёный.** Держатель на `s1-spec` не мешает `claim` на `s3-impl` и наоборот
   (`deps.worktree_conflict`, `listik/deps.py:196-215`: фильтр по `stage` и для самой
   задачи, и для кандидата).
8. **зелёный.** Задача без этапа лочит и лочится, `s4-judge` тоже (тот же фильтр
   `coalesce(stage,'') IN ('', 's3-impl', 's4-judge')`).
9. **зелёный.** Тот же держатель на второй пишущей задаче в том же дереве проходит
   (`holder` передаётся в `worktree_conflict` из `claim`, `listik/deps.py:212-214`).
10. **зелёный.** Свой прогон: `claim(..., force=True)` при чужом держателе → `ValueError`
    «уже удерживается»; при занятом дереве → `ValueError` «рабочее дерево». `force`
    проверяется только в ветке блокеров (`listik/store.py:412-420`).
11. **зелёный.** Свой прогон на проекте с `routing.harnesses = {"s3-impl": ["dsh"]}`:
    `claim(holder="codex", harness="codex")` → `ValueError` про harness; без `harness`
    проходит. Строка guard в диффе не менялась (`listik/store.py:364-366`).

## Красный вердикт и окно возврата

12. **зелёный.** Свой прогон: `stage == "s3-impl"`, `holder == "dsh"`, `stage_at` обновлён,
    последнее событие `stage` — `s4-judge → s3-impl`, `duration_s = 7200`, `note`
    «возврат после красного verdict», `actor == "agent:claude"`; комментарий `verdict`
    в `comments` один, событие `comment` одно, событие возврата одно
    (`listik/store.py:472-473` — вызов `next_stage(..., actor=author)`).
13. **зелёный.** Зелёный вердикт этап не меняет (`RedVerdictTests`).
14. **зелёный.** Свой прогон: снято 1, `holder == ''`, событие `release` `dsh → ''`,
    `note = "истёк срок возврата после красного verdict (24 ч без активности)"`,
    `ready_tasks` содержит задачу.
15. **зелёный.** `heartbeat` после возврата сохраняет держателя, `expire_return_handoffs` → 0
    (`listik/deps.py:296-300` — сравнение `holder_at` с `ts` события возврата).
15a. **зелёный.** Повторный `claim` обновляет `holder_at`, новых событий нет, последующее
    истечение → 0; при `holder_at` старше события возврата порядок событий `release` → `claim`
    (`ExpireReturnWindowTests.test_repeated_claim_extends_window` и
    `…_expires_then_claim_takes_it`).
16. **зелёный.** `ProjectReturnWindowTests`: с `routing={"return_window_hours": 1}` снят,
    с умолчанием — нет.
17. **зелёный.** `claim` после истёкшего окна берёт задачу, в `events` `release` затем `claim`;
    строка задачи перечитывается после истечения (`listik/store.py:368-375`).
18. **зелёный.** Свой прогон: `s3-impl` с держателем и `holder_at` 500 ч назад, но без
    события `s4-judge → s3-impl` — `expire_return_handoffs` → 0, держатель на месте.

## deps_state

19. **зелёный.** Свой прогон: `worktree_busy = {"id", "title", "holder", "holder_title",
    "holder_age", "stale"}`, `reasons` содержит `дерево занято: <A> (держит dsh)`,
    `ready is True`, `claimable is True`, `verdict == "можно брать"`; при свободном дереве
    и для задачи на `s1-spec` — `None`.
20. **зелёный.** `python3 -m unittest tests.test_context` — 20 тестов, OK.
    `git diff listik/documents.py` — ровно одна строка, добавлен ключ `worktree_busy: None`
    в `_DEPENDENCIES_DEFAULTS`. `documents.context(conn, T, "s3-impl")["dependencies"]`
    содержит `worktree_busy`.

## CLI / HTTP

21. **зелёный.** `./bin/listik --local claim <C> --holder codex` → код 1, ровно одна строка
    stderr `ошибка: рабочее дерево основное проекта demo занято задачей …, держит dsh 0 мин.
    Варианты: …`, без `Traceback`.
22. **зелёный.** Повторный `claim` тем же держателем → код 0, карточка
    `▶ demo-tody [demo] этап код держит dsh 0 мин`; чужим → код 1, stderr
    `ошибка: задача … уже удерживается dsh (0 мин). Варианты: …`.
23. **зелёный.** `show <C> --json` → `deps_state.worktree_busy` — словарь с id занявшей
    задачи; `show <T>` после красного вердикта → `этап: 3. Реализация`, в событиях
    `stage s4-judge → s3-impl (2 ч 0 мин) — возврат после красного verdict`.
24. **зелёный.** Сервер поднимался на временной базе и временном конфиге
    (`LISTIK_DB`/`LISTIK_CONFIG` в scratchpad), порт 8799, без `--daemon`.
    `POST /api/tasks/{C}/claim {"holder":"codex"}` при занятом дереве → 400,
    `error` содержит «рабочее дерево»; при чужом держателе → 400 с «Варианты»;
    тело `{holder, harness, note, force}` принимается, успешный ответ — `{"ok", "data"}`,
    `data` — карточка с `holder`. `POST /api/tasks/{T}/comment {"text": "красный: …",
    "kind": "verdict", "author": "agent:claude"}` → 200, затем `GET /api/tasks/{T}` даёт
    `stage == "s3-impl"`, событие возврата с `duration_s = 7200`, держателя `dsh`;
    `POST /api/tasks/{C}/ready` отдаёт `worktree_busy`. Сервер остановлен, порт 8799
    не отвечает, `./bin/listik status` показывает прежнее состояние рабочего сервера
    (он и до приёмки не был запущен: «сервер: не отвечает»), `listik.pid` возвращён
    к исходному содержимому, `git status` не показывает `listik.db`, `listik.pid`,
    `config.toml`. Токен в отчёт не копировался.

## Границы

25. **зелёный.** `git diff --stat` по коду — только `listik/deps.py`, `listik/documents.py`,
    `listik/store.py` (плюс новый `tests/test_claim.py`); `web/`, `db.py`, `config.py`,
    `server.py`, `client.py`, `mcp.py`, `bin/listik`, `README.md`, `API.md`,
    `docs/harness-protocol.md`, `AGENTS.md` не затронуты. Хунки в `store.py` — только
    в `claim`, `add_comment` (ветка вердикта) и `next_stage` (параметр `actor`); список
    подстрок красного вердикта — контекстная строка, не менялся; `heartbeat` и
    `update_task` в диффе отсутствуют.
26. **зелёный.** `listik/server.py` в диффе нет — фонового потока истечения окна не добавлено.

## Срезанные углы — не найдено

- Ни хардкода под тест, ни заглушек: критерий лока вынесен в одну функцию
  `deps.worktree_conflict`, ею пользуются и `claim`, и `ready` — расхождения между
  отказом и подсказкой невозможны.
- `claim` после `expire_return_handoffs` перечитывает строку задачи, а не полагается на
  прочитанную ранее (ловушка из п. 17 чек-листа обойдена честно).
- Переход по красному вердикту идёт через `next_stage`/`update_task`, а не прямым `UPDATE`,
  поэтому `duration_s` считается общим механизмом.
- Секретов и файлов вроде `.env`/`*.key`/`credentials.json` в диффе нет.

## Перенесено на приёмку шага

Нет.

## Вердикт

Зелёный. Порция закоммичена.
