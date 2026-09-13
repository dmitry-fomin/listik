# Порция 02.c. Синхронизация README, API.md и CLAUDE.md с протоколом

## Контекст

Listik — трекер задач на stdlib-Python; `API.md` — авторитетный контракт данных и эндпоинтов,
`README.md` — справочник команд, `CLAUDE.md` — заметки об архитектуре для агентов в этом
репозитории. Порциями a и b в коде появились: `needs-owner` пишет комментарий `question`/`answer`
и событие при каждом вызове (`store.set_needs_owner`, HTTP `POST /api/tasks/{id}/needs-owner`,
MCP `listik_needs_owner`); канонический протокол harness `docs/harness-protocol.md`;
`listik init-projects` ставит в проекты блок с этим протоколом (`migrate.body()` читает файл);
тот же блок стоит в `AGENTS.md` Listik.

**Предусловие: порции a и b закоммичены.** Проверка: `grep -n "def set_needs_owner"
listik/store.py` и `grep -n "def body" listik/migrate.py` непусты, `python3 -c 'from listik import
migrate; from pathlib import Path; print(migrate.upsert(Path("AGENTS.md"), dry_run=True))'` → `unchanged`.
Иначе порцию не начинать и вернуть задачу оркестратору.

Документация описывает **фактическое** поведение кода: перед правкой прогнать примеры на
временной базе (`LISTIK_DB=/tmp/…`, `--local`). Сейчас три списка правил для агента живут
порознь и расходятся: `README.md` «Правила, без которых доска врёт» (5 пунктов), `API.md`
«Правила работы агента с задачами» (7 пунктов), и до порции b — свой в `AGENTS.md`. После этой
порции правила в одном месте — `docs/harness-protocol.md`, остальные ссылаются на него.

Перед началом прочитай: `README.md` (разделы «Работа агента с задачей», «Этапы конвейера
(feature-pipeline)», «Перевод проектов на Listik», «Агентам: MCP и CLI»), `API.md` (строки
`POST /api/tasks/{id}/comment`, `POST /api/tasks/{id}/needs-owner`, раздел «CLI», раздел
«Правила работы агента с задачами»), `CLAUDE.md` (описание `listik/migrate.py`),
`docs/harness-protocol.md`, `listik/migrate.py`, `store.set_needs_owner`.

## Что сделать

### 1. `API.md`

- Строка `POST /api/tasks/{id}/needs-owner`: тело `value=true|false`, `note` (текст вопроса или
  ответа), `actor`, `harness`; поведение — при непустом `note` создаётся комментарий
  `kind=question` (`value=true`) или `answer` (`value=false`), событие `question`/`answer`
  пишется при каждом вызове, даже если флаг уже стоит; ответ — полная карточка, как у
  `PATCH`. Оговорить, что `PATCH /api/tasks/{id}` с `needs_owner` меняет только флаг и
  комментария не пишет.
- Строка `POST /api/tasks/{id}/comment`: у `kind=question|answer` пояснить, что это те же виды,
  которые пишет `needs-owner`; писать их руками можно, флаг они не меняют.
- Раздел «CLI»: строка `listik needs-owner <id> "вопрос автору"` дополняется
  `listik needs-owner <id> --clear "ответ"`.
- Раздел «Правила работы агента с задачами» заменить на два-три предложения: полный протокол —
  `docs/harness-protocol.md` (он же — блок `<!-- BEGIN LISTIK -->` в `AGENTS.md` проектов,
  ставится `listik init-projects`); здесь остаётся только то, что относится к API: заблокированную
  задачу `claim` не берёт, handoff снимает держателя, красный вердикт возвращает на `s3-impl`.
  Семь старых пунктов удалить — не дублировать.

### 2. `README.md`

- «Работа агента с задачей» (`README.md`, раздел с одним `sh`-блоком команд): примеры команд
  оставить, добавить строку `needs-owner <id> --clear "ответ"`. Списка правил в этом разделе
  **нет** — его тут не искать и сюда не переносить.
- «Контекст этапа» (следующий раздел): в его конце стоят нумерованный список «Правила, без
  которых доска врёт» (пять пунктов, «Взял задачу — `claim`…») и абзац «Одна задача — один
  держатель…». Список заменить **на том же месте** ссылкой на `docs/harness-protocol.md` с одной
  фразой, что это тот же текст, что стоит в блоке `AGENTS.md` проектов; абзац про одного
  держателя оставить как есть. Остальной текст раздела (пути документов, `context`) не трогать.
- «Этапы конвейера (feature-pipeline)»: таблица остаётся; добавить строку о типах переходов
  (sticky `s1→s2`, `s3→s4`; handoff `s2→s3`, `s4→done` — держатель снимается сервером; красный
  вердикт на `s4` возвращает на `s3`) со ссылкой на роли в `docs/harness-protocol.md`.
- «Перевод проектов на Listik»: блок теперь содержит протокол harness целиком и берётся из
  `docs/harness-protocol.md`; после изменения протокола — повторный `init-projects` обновляет
  блок во всех проектах (идемпотентно). Команды `--dry-run`/`--remove` — как были.
- «Агентам: MCP и CLI»: одно предложение — вопрос через `listik_needs_owner`/`needs-owner`
  ложится в историю карточки и находится `search`.

### 3. `CLAUDE.md`

В описании `listik/migrate.py` (раздел «Architecture») дописать, что тело блока читается из
`docs/harness-protocol.md` — менять протокол нужно там, а не в `migrate.py`. Одно предложение,
остальной файл не трогать.

### 4. Проверка примеров

Каждый пример команды, добавленный или изменённый в этой порции, выполняется на временной базе
без ошибок; утверждения о поведении (`question`/`answer` в `comments`, `unchanged` отсутствует,
handoff снимает держателя) сверяются живым вызовом, а не памятью.

**Исключение — `init-projects`.** Примеры `./bin/listik init-projects` и `init-projects --remove`
из раздела «Перевод проектов на Listik» **не прогонять**: команда пишет блок в `AGENTS.md`/`CLAUDE.md`
чужих репозиториев по списку проектов рабочей базы, и временная база её не изолирует (на
временной базе проектов нет — отчёт пустой и ничего не доказывает). Сами примеры в README
оставить как есть (смысл раздела — как переводить проекты). Утверждения этого раздела сверять
чтением: `python3 -c 'from listik import migrate; print(migrate.block())'` содержит текст
протокола, а идемпотентность — `migrate.upsert(Path("AGENTS.md"), dry_run=True) == "unchanged"`
(из корня репозитория). Единственно допустимый запуск — `./bin/listik init-projects --dry-run`.

## Границы правки

- Правятся: `README.md`, `API.md`, `CLAUDE.md`.
- Не менять код (`listik/**`, `bin/**`), тесты, схему, `docs/harness-protocol.md`, `AGENTS.md`,
  `docs/specs/**`, `project-skills/**`, `web/**`. Расхождение кода с ожиданием — строка в отчёте
  исполнителя, не правка кода.
- Не переписывать разделы `README.md`/`API.md`, не относящиеся к `needs-owner`, правилам агента,
  этапам и `init-projects`.
- Не заводить в `README.md`/`API.md` второй полный список правил: только ссылка на канонический
  файл плюс API-специфичные факты.
- `listik init-projects` — только с `--dry-run`. Запуск без него (и `--remove`) правит файлы
  чужих проектов; это решение автора, вне порции — даже «для проверки примера из README».
- Никаких токенов, содержимого `config.toml`, личных путей кроме `~/Projects/Listik`.

## Как проверить

```sh
python3 -m unittest discover tests                        # зелёные, код не менялся
# Дерево на старте уже содержит бумаги конвейера (docs/specs/**) и, возможно, журнал шага —
# поэтому границы проверяются по списку путей, а не по «только три файла в диффе»:
git diff --stat HEAD -- . ':!docs/specs'                  # ровно README.md, API.md, CLAUDE.md
git status --porcelain -- listik bin tests web AGENTS.md docs/harness-protocol.md   # пусто
grep -n "harness-protocol" README.md API.md CLAUDE.md     # ссылки есть во всех трёх
export LISTIK_DB=/tmp/listik-step02c.db && ./bin/listik --local init
# прогнать примеры needs-owner / --clear из README и API.md; init-projects — только --dry-run
```
