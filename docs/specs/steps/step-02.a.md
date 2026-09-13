# Порция 02.a. `needs-owner` записывает вопрос и ответ в историю карточки

## Контекст

Listik — трекер задач на stdlib-Python: `bin/listik` (argparse CLI + API-клиент),
`listik/store.py` (вся логика задач), `listik/server.py` (HTTP на `ThreadingHTTPServer`),
`listik/client.py` (`local_call` — прямой путь в SQLite, когда сервер не поднят),
`listik/mcp.py` (stdio MCP, инструменты `listik_*`). Все три транспорта должны звать одну и ту же
функцию store — иначе поведение расходится. Тесты — `tests/`, stdlib `unittest`, база на каждый
тест временная (`tests/helpers.py`, `TempDbTestCase`).

Команда `needs-owner <id> "вопрос"` поднимает флаг `tasks.needs_owner`, чтобы задача попала в
линию «нужен ты» на доске. Сейчас (см. `bin/listik` `cmd_needs_owner`, `server.py` действие
`needs-owner`, `mcp.py` `listik_needs_owner`) все три пути идут в `store.update_task(...,
needs_owner=…, note=text)`. Из этого два дефекта, проверенных на временной базе:

1. Текст вопроса живёт только в `note` события `question`. В `comments` его нет: он не виден в
   «истории» `show`, не индексируется FTS и не находится `search`.
2. Если флаг уже поднят, `update_task` не видит изменений и возвращает `{"unchanged": true}`:
   второй вопрос с другим текстом теряется целиком — ни события, ни комментария.

Протокол harness (шаг 02) требует: «вопрос человеку оформляй `needs-owner`; не оставляй его
только в чате» и «вопрос можно восстановить только по Listik». Эта порция делает это правдой.

Решение автора: `needs-owner <id> "текст"` **всегда** пишет комментарий `kind=question`
(`--clear "текст"` — `kind=answer`) и событие, даже если флаг уже стоит; те же семантики в
HTTP-эндпоинте и MCP; с тестом. `context` и форма ответа `show` не меняются.

Перед началом прочитай в `listik/store.py`: `update_task` (ветка `needs_owner`, событие
`question`/`answer`), `add_comment` (пишет комментарий, событие `comment` с `to_value=kind`,
индексирует FTS), `event`, `get_task`; в `bin/listik`: `call`, `cmd_needs_owner`; в
`listik/client.py`: `local_call`; в `listik/server.py`: действие `needs-owner` в обработчике
`POST /api/tasks/{id}/<action>`; в `listik/mcp.py`: описание `listik_needs_owner` в `TOOLS` и
ветку в `call_tool`. Форму ответа не придумывать — смотреть, что возвращает `update_task`.

## Что сделать

### 1. `listik/store.py`: функция `set_needs_owner`

Новая публичная функция (имя `set_needs_owner`, чтобы не путать с полем):

```
set_needs_owner(conn, task_id, *, value: bool, text: str | None = None,
                actor: str | None = None, harness: str | None = None) -> dict
```

Поведение, в этом порядке:

1. Задачи нет → `KeyError` с тем же текстом, что у соседей (`задача не найдена: …`).
2. `text` непустой после `strip()` → `add_comment(conn, task_id, text, author=actor,
   kind="question" if value else "answer", harness=harness)`. Комментарий пишется **всегда**,
   независимо от текущего значения флага. Пустой/отсутствующий текст комментария не создаёт
   (пустых комментариев в истории быть не должно).
3. Событие `question` (при `value=True`) или `answer` (при `value=False`) с `note=text`,
   `from_value` = старое значение флага, `to_value` = новое — пишется **всегда**, ровно одно за
   вызов, даже если флаг не изменился. Событие `comment`, которое `add_comment` пишет само, —
   штатное и не считается дублем.
4. Если флаг действительно меняется — обновить `tasks.needs_owner` и `updated_at`; если нет —
   всё равно обновить `updated_at` (карточка изменилась: появился комментарий/событие).
   Делается **прямым `UPDATE tasks SET needs_owner = ?, updated_at = ? WHERE id = ?`** плюс
   `event(...)` из п. 3 — **не** через `update_task`: та пишет собственное событие `question`
   при смене флага и возвращает `unchanged` без смены, а править её запрещено границами
   порции. После `UPDATE` вызвать `deps_mod.refresh_task(conn, task_id)` и
   `_index_task(conn, task_id)` так же, как это делает `update_task` после своего `UPDATE`
   (флаг участвует в фильтрах списка и поиска). Проверяется по факту: одно событие
   `question` на вызов, в диффе `update_task` не тронута.
5. Вернуть `get_task(conn, task_id)` — полную карточку, как возвращает `update_task`. Ключа
   `unchanged` в ответе быть не должно ни при каком сценарии.
6. `conn.commit()`.

`update_task(..., needs_owner=…)` и `PATCH /api/tasks/{id}` с `needs_owner` **не трогать**: это
низкоуровневая правка поля, комментарий она не пишет — так и остаётся.

### 2. Три транспорта на одну функцию

- `listik/client.py` `local_call`: новая операция `"needs-owner"` →
  `store.set_needs_owner(conn, task_id, value=…, text=…, actor=…, harness=…)`.
- `bin/listik` `cmd_needs_owner`: `call("needs-owner", args, f"/api/tasks/{id}/needs-owner",
  method="POST", body={"value": not args.clear, "note": args.text, "actor": actor,
  "harness": args.harness}, local_kwargs={...})` — тело HTTP-запроса **сохранить прежним**
  (`value`, `note`, `actor`, `harness`): его уже шлёт доска `web/src/api/client.ts`. Вывод
  команды: как сейчас плюс при `--clear` с текстом печатать строку `  ответ: <текст>`
  (симметрично строке `  вопрос: …`). В `--json` режиме печатается только JSON карточки
  (сейчас после JSON печатается лишняя строка `вопрос:` — это ломает `--json | python3 -c …`;
  исправить: при `--json` человеческие строки не печатать).
- `listik/server.py`: действие `needs-owner` → `store.set_needs_owner(conn, tid,
  value=as_bool(body.get("value", True)), text=body.get("note"), actor=body.get("actor"),
  harness=body.get("harness"))`. Путь, метод, имена полей тела и форма ответа (`{"ok": true,
  "data": <task>}`) не меняются.
- `listik/mcp.py`: ветка `listik_needs_owner` → `store.set_needs_owner(..., value=bool(
  args.get("value", True)), text=args.get("text"), actor=args.get("actor"))`. Схема инструмента
  не меняется; описание дополнить одной фразой: текст вопроса ложится в историю карточки
  комментарием `question` (ответ при `value=false` — `answer`) и находится поиском.

### 3. Тесты: `tests/test_needs_owner.py`

На `TempDbTestCase`, через `store` напрямую, плюс один сценарий CLI `--local` через
`subprocess`. Образца запуска `bin/listik` из тестов в репозитории **нет** (в `tests/`
`subprocess` используется только для `git`), поэтому контракт запуска задаётся здесь:

- путь к скрипту — абсолютный, от файла теста:
  `LISTIK_BIN = pathlib.Path(__file__).resolve().parent.parent / "bin" / "listik"`;
- команда — `[sys.executable, str(LISTIK_BIN), "--local", "needs-owner", task_id, "Q-cli", "--json"]`;
  `--local` обязателен: без него `call()` при поднятом на машине сервере уйдёт в рабочую базу;
- окружение — `env={**os.environ, "LISTIK_DB": str(self.db_path)}` (`paths.DB_PATH` читается
  на импорте, поэтому передаётся именно через env, а не через флаг); `cwd` — корень репозитория
  (`LISTIK_BIN.parent.parent`);
- `subprocess.run(..., capture_output=True, text=True, check=True)`; для сценария «неизвестная
  задача» — без `check`, проверяется `returncode != 0` и непустой `stderr`;
- результат в базе проверяется **через `store.get_task` по `self.conn`** (это тот же файл, что
  писал субпроцесс; SQLite видит закоммиченные изменения другой связи), а не разбором
  человеческого вывода. Единственное, что читается из stdout, — сам JSON (`json.loads(stdout)`),
  потому что чистота JSON-вывода и есть проверяемое требование.

Тесты пишутся **до** правки store и должны быть красными на текущем коде там, где это отмечено:

1. Первый вопрос: после `set_needs_owner(value=True, text="Q1")` — `needs_owner` истинен,
   `comments` содержит ровно один комментарий `kind=question` с текстом `Q1`, событий `question`
   ровно одно, у него `note == "Q1"`. *(красный до правки: функции нет)*
2. Повторный вопрос при поднятом флаге: `set_needs_owner(value=True, text="Q2")` →
   комментариев `question` два (`Q1`, `Q2` в порядке создания), событий `question` два, флаг
   истинен, в ответе нет ключа `unchanged`. *(красный до правки: сегодня `update_task` возвращает
   `unchanged` и ничего не пишет)*
3. Ответ: `set_needs_owner(value=False, text="Ответ автора")` → флаг ложен, добавлен один
   комментарий `kind=answer`, одно событие `answer` с `note`.
4. Без текста: `set_needs_owner(value=True)` на задаче без флага → флаг поднят, событие
   `question` одно, комментариев **не добавилось**; `set_needs_owner(value=False)` → флаг снят,
   событие `answer`, комментариев не добавилось.
5. Поиск: после вопроса с уникальным словом (например `квазипериодический`)
   `search.search(conn, "квазипериодический", mode="text")` возвращает эту задачу
   (`results[0]["id"]` или `task_id` — сверить с формой ответа `search.search`).
   *(красный до правки: события не индексируются)*
6. `get_task` не приобрёл новых ключей: набор ключей верхнего уровня ответа `set_needs_owner`
   равен набору ключей `get_task` (форма `show` не менялась).
7. CLI `--local` по контракту выше: `needs-owner <id> "Q-cli" --json` печатает **только** JSON
   (`json.loads` всего stdout проходит), затем `needs-owner <id> "Q-cli-2" --json`; после обоих
   вызовов `store.get_task(self.conn, id)["comments"]` содержит два комментария `question`
   с текстами `Q-cli` и `Q-cli-2`. *(красный до правки по обеим причинам: лишняя строка после
   JSON, второй вопрос теряется)*
8. Неизвестная задача → `KeyError` из store; в CLI `--local` — ненулевой код возврата и
   сообщение об ошибке (как у соседних команд).

Существующие тесты `python3 -m unittest discover tests` остаются зелёными: в
`tests/test_context.py` есть проверки стабильности `context`, они не должны увидеть новых
комментариев (в них `needs-owner` не вызывается).

## Границы правки

- Правятся: `listik/store.py` (только новая функция `set_needs_owner`; `update_task` и
  `add_comment` не менять ни по коду, ни по поведению — флаг ставится прямым `UPDATE`, см. §1 п. 4),
  `listik/client.py` (одна ветка `local_call`), `bin/listik` (`cmd_needs_owner`),
  `listik/server.py` (одна ветка действия), `listik/mcp.py` (ветка `call_tool` + описание),
  новый `tests/test_needs_owner.py`.
- Не менять: контракт и код `documents.context`, `get_task`/`row_to_task` (форма `show`),
  `add_comment` (в т. ч. авто-возврат по красному вердикту), схему базы, `db.py`, `alembic/`,
  `web/`, `API.md`/`README.md` (документация — порция c), `docs/harness-protocol.md`,
  `AGENTS.md`, `migrate.py` (порция b).
- Не менять существующие тесты под реализацию; не добавлять новых `kind` комментариев
  (`question`/`answer` уже есть в списке `choices`).
- Не трогать тело запроса `POST /api/tasks/{id}/needs-owner` (`value`, `note`, `actor`,
  `harness`) и ответ — доска шлёт его как есть.
- Никаких токенов, содержимого `config.toml` и личных данных в тестах и отчёте.

## Как проверить

```sh
python3 -m unittest tests.test_needs_owner -v            # красные до правки store — см. пометки, зелёные после
python3 -m unittest discover tests                        # всё зелёное
export LISTIK_DB=/tmp/listik-step02a.db && ./bin/listik --local init
ID=$(./bin/listik --local new "Проба" -p demo --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
./bin/listik --local needs-owner $ID "Первый вопрос"
./bin/listik --local needs-owner $ID "Второй вопрос" --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print([c["kind"] for c in d["comments"]], "unchanged" in d)'
# ожидаемо: ['question', 'question'] False
./bin/listik --local needs-owner $ID --clear "Ответ" && ./bin/listik --local show $ID | grep -c "(answer)"
./bin/listik --local search "Второй" --mode text          # находит задачу
git diff --stat HEAD                                       # только файлы из «Границ правки»
```

HTTP и MCP проверяются так же, как в шаге 01: сервер на временной базе (`LISTIK_DB=… ./bin/listik
serve`, токен из `./bin/listik token`) и stdio-обмен `initialize` → `tools/call listik_needs_owner`.
