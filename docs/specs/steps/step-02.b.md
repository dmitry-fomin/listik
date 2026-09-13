# Порция 02.b. Канонический протокол harness и блок для AGENTS.md

## Контекст

Listik — трекер задач и память для нескольких harness (Claude, Codex, dsh, Grok, WriterLLM);
в самом протоколе ни один из них не называется. Код — stdlib-Python: `bin/listik` (CLI),
`listik/store.py` (логика), `listik/migrate.py` — **не** миграции базы, а вставка блока правил
`<!-- BEGIN LISTIK --> … <!-- END LISTIK -->` в `AGENTS.md`/`CLAUDE.md` других проектов командой
`listik init-projects` (`bin/listik`, `cmd_init_projects` → `migrate.migrate_all`). Сейчас
`migrate.BODY` — короткая шпаргалка команд, протокола в ней нет. `docs/harness-protocol.md` —
английский черновик из 12 строк, на него никто не ссылается. `AGENTS.md` Listik содержит свой
список команд и раздел «Этапы конвейера», без маркеров.

**Предусловие: порция a закоммичена** (`git log --oneline` содержит её коммит; в
`listik/store.py` есть `set_needs_owner`). Протокол опирается на то, что вопрос из
`needs-owner` ложится в историю карточки и находится поиском.

Спека шага — `docs/specs/steps/step-02-harness-protocol.md`; текст десяти правил берётся из
неё **дословно**. Что реально делает сервер (нужно, чтобы правильно описать переходы; читать
`listik/store.py`, `listik/config.py`):

- `stage <id>` переводит на следующий этап цепочки `s1-spec → s2-review → s3-impl → s4-judge → done`
  (или `--to <этап>`). Тип перехода считает `config.transition_kind`: `s1→s2` sticky,
  `s2→s3` handoff, `s3→s4` sticky, `s4→done` handoff. На handoff сервер **сам снимает
  держателя** (событие `release`) — следующий исполнитель берёт задачу через `ready` → `claim`.
  На sticky (`store.next_stage`) держатель **не меняется**, если не передан `--holder`; с
  `stage <id> --holder <кто>` сервер переставляет держателя на указанного (событие `claim`)
  без `release`.
- `claim` отказывает на заблокированной задаче и на **чужой** (держатель другой) — и `--force`
  это не обходит, `--force` действует только на блокеры; повторный `claim` того же держателя
  идемпотентен. Следствие: после sticky-перехода без `--holder` карточка приезжает на
  следующий этап с прежним держателем, и другой harness не может ни `claim`, ни `heartbeat`
  под своим именем, пока прежний не сделает `release`.
- `comment -k verdict` на этапе `s4-judge` с текстом, содержащим **где угодно** подстроку
  «красн», `red`, `fail`, «не прой» или `❌` (регистронезависимо, `store.add_comment`), **сам
  возвращает** карточку на `s3-impl` (событие `stage` с заметкой «возврат после красного
  verdict»). Держателя этот возврат не меняет. Значит, зелёный вердикт не должен содержать
  ни одной из этих подстрок ни в каком месте текста («красных пунктов нет» — уже возврат).
- `heartbeat --note` обновляет `holder_note`; событие пишется не чаще раза в 10 минут.
- `needs-owner <id> "вопрос"` (после порции a) — комментарий `question` + событие; ответ
  человека — `needs-owner <id> --clear "ответ"` (комментарий `answer`) или `comment -k journal`.
- `context <id> --stage <этап> [--portion …]` — компактный срез под этап (шаг 01): на `s1/s2`
  ТЗ и чек-лист целиком; на `s3` — карточка, порция, критерии, **последний review — без
  вердикта и без журнала** (`documents.context` кладёт `verdict` и `journal` только при
  `stage == "s4-judge"`); на `s4` — то же плюс последний вердикт, журнал и `worktree`/diff.
  Поэтому после красного вердикта исполнитель на `s3` читает замечания судьи из `show <id>`
  (история комментариев, kind `verdict`), а не из `context`.

## Что сделать

### 1. `docs/harness-protocol.md` — канонический текст (переписать целиком, по-русски)

Файл должен быть пригоден для вставки **как есть** внутрь чужого `AGENTS.md`, поэтому начинается
с заголовка второго уровня, без H1 и без front-matter. Структура и обязательное содержание:

1. `## Listik — протокол harness` и одна вводная строка: Listik — единственная очередь и журнал
   работы, путь `~/Projects/Listik/bin/listik` (в примерах — `L=~/Projects/Listik/bin/listik`),
   `LISTIK_ACTOR=agent:<harness>` — кто ты (значение — из `listik actors`).
2. Десять нумерованных правил — **дословно** из блока `text` спеки шага (сравнение без учёта
   переносов строк и лишних пробелов). Ни добавлять, ни переформулировать, ни переставлять.
3. `### Роли по этапам` — сначала абзац о держателе на переходах (обязателен, потому что
   `claim` чужой карточки невозможен даже с `--force`):
   - handoff (`s2→s3`, `s4→done`) — сервер сам снимает держателя; следующий берёт через
     `ready` → `claim`;
   - sticky (`s1→s2`, `s3→s4`) — держатель не меняется: если следующий этап ведёт **та же
     сессия**, она продолжает под тем же `--holder`; если следующий этап ведёт **другой
     harness**, передающий делает `stage <id> --holder <следующий>` (сервер переставит
     держателя без `release`), а принимающий начинает с `claim <id> --holder <свой>` (для
     него он идемпотентен) и дальше `heartbeat`;
   - возврат после красного вердикта держателя не меняет: если судья держал карточку под
     своим именем — судья делает `release <id>`, исполнитель заново `claim`; в одной сессии —
     просто продолжает под прежним держателем.

   Затем четыре подпункта `s1-spec`, `s2-review`, `s3-impl`, `s4-judge`, у каждого ровно три
   помеченных строки **читать / менять / переход**, содержательно:
   - `s1-spec`: читать — `show`, `context <id> --stage s1-spec`, файл `spec_path`, если уже есть;
     менять — Markdown-ТЗ, чек-лист приёмки, дочерние карточки-порции (`new … --spec … --checklist …`),
     предложения зависимостей мягкими связями (`dep link`/`relates-to`; жёсткую `dep add`
     без подтверждения человека не ставить); переход — `stage <id>` на `s2-review`, sticky:
     держатель остаётся, тот же автор идёт критиковать (другой harness — `stage <id> --holder <критик>`).
   - `s2-review`: читать — `context <id> --stage s2-review` (ТЗ и чек-лист целиком); менять —
     ничего в коде и в ТЗ, только `comment -k review` (и файл `review_path`, если задан);
     переход — `stage <id>` на `s3-impl`, handoff: сервер снимает держателя, дальше задачу
     берёт исполнитель через `ready` → `claim`.
   - `s3-impl`: читать — `context <id> --stage s3-impl --portion "<название порции>"` (в нём
     последний review, **вердикта и журнала на `s3` нет**) и обязательно `show <id>` — там
     ответы на вопросы и **прошлый вердикт судьи** после красного возврата; менять — код в
     `worktree`/`branch` карточки, запускать проверки, ход — `comment -k journal`; переход —
     `stage <id>` на `s4-judge`, sticky: не коммитить, дерево остаётся судье; если судья —
     другой harness, `stage <id> --holder <судья>`.
   - `s4-judge`: читать — `context <id> --stage s4-judge` (worktree, diff, последний вердикт),
     чек-лист; если карточка приехала с чужим держателем — сначала `claim <id> --holder <свой>`
     (после `stage --holder` он идемпотентен); менять — код не править; писать
     `comment -k verdict`, **первое слово — «зелёный» или «красный»**, и обязательная фраза:
     **в зелёном вердикте нигде в тексте не употреблять подстроки «красн», `red`, `fail`,
     «не прой», `❌` — сервер ищет их во всём тексте и вернёт карточку на `s3-impl`**; красный
     — сервер сам вернёт карточку на `s3-impl`, держатель не меняется: если судья держит под
     своим именем — `release <id>`, чтобы исполнитель мог `claim`; зелёный — коммит, затем
     `done <id> -r "…"`.
4. `### Команды` — один `sh`-блок: `ready`, `search`, `show`, `context`, `claim --holder`,
   `heartbeat --holder --note`, `stage` (и `stage <id> --holder <следующий>` для sticky-передачи
   другому harness), `comment -k journal|review|verdict`, `needs-owner` (и `--clear "ответ"`),
   `release`, `done -r`. Флаги и имена — как в `bin/listik` (сверить с `./bin/listik <cmd> --help`).
5. Одна строка о восстановлении после холодного старта: всё, что нужно, — в `show <id>`
   (держатель, этап, вопросы и ответы, журнал, вердикты, `spec_path`, `worktree`/`branch`) и
   `context`; чат не нужен.

Запрещено в файле: имена конкретных harness и моделей (`claude`, `codex`, `dsh`, `deepseek`,
`grok`, `gemini`, `writerllm`, `opus`, `sonnet` — регистронезависимо), токены, личные пути
кроме `~/Projects/Listik`. Английский черновик не сохранять.

### 2. `listik/migrate.py` — блок собирается из канонического файла

- `BODY`-константа заменяется функцией `body() -> str`: вводный абзац (Listik вместо beads,
  `.beads` — архив, ссылки на `~/Projects/Listik/AGENTS.md` и `API.md` — то, что есть сейчас в
  начале `BODY`, без блока команд) + пустая строка + содержимое `docs/harness-protocol.md`
  (путь: `paths.ROOT_DIR / "docs" / "harness-protocol.md"`), прочитанное **в момент вызова**,
  не на импорте. Шпаргалку команд из старого `BODY` убрать — она теперь в протоколе.
- Файла нет → `FileNotFoundError` с понятным текстом; молча ставить блок без протокола нельзя.
- `block(body=None)`, `upsert(path, *, dry_run=False, body=None)`: `None` → `body()`.
  Сигнатуры `remove`, `migrate_all`, маркеры `BEGIN`/`END`, `TARGETS`, возвращаемые статусы
  `added|updated|unchanged|skipped|removed` — без изменений.
- `cmd_init_projects` в `bin/listik` не менять.

### 3. `AGENTS.md` Listik — тот же блок между маркерами

Разделы «Команды, которые нужны агенту» и «Этапы конвейера» удалить; на их место (после
«Что это») вставить ровно то, что даёт `migrate.block()` — с маркерами. Разделы «Что это»,
«Зависимости», «Тонкости», «UI-кит @zoloto585/facet», «Рабочий skill продукта Listik» оставить.
В «Тонкости» число MCP-инструментов (18) не трогать. Проверка равенства — тестом (ниже) и
`python3 -c 'from listik import migrate; from pathlib import Path; print(migrate.upsert(Path("AGENTS.md"), dry_run=True))'`
→ `unchanged`.

### 4. Тесты: `tests/test_migrate.py`

`unittest`, временный каталог на тест (`tempfile.TemporaryDirectory`). Пишутся до правок:

1. `migrate.block()` содержит десять строк, начинающихся с `1.`…`10.`, подзаголовок
   `### Роли по этапам` с четырьмя этапами, и не содержит ни одного имени harness из списка
   выше (регистронезависимый regex). *(красный до правки: в `BODY` протокола нет)*
2. Десять правил в `docs/harness-protocol.md` дословно совпадают с блоком `text` в
   `docs/specs/steps/step-02-harness-protocol.md`: из спеки читаются строки между
   ` ```text ` и ` ``` `, нормализуются пробелы, ищутся в нормализованном тексте файла.
   *(красный до правки)*
3. `upsert` в новый файл без маркеров → `added`, файл заканчивается блоком; повторный
   `upsert` → `unchanged`; `upsert(..., body="другой текст\n")` → `updated`, и текст вне
   маркеров побайтно прежний; `remove` → `removed`, файл равен исходному тексту.
4. `migrate.upsert(paths.ROOT_DIR / "AGENTS.md", dry_run=True) == "unchanged"` —
   `AGENTS.md` Listik несёт актуальный блок. *(красный до правки)*
5. `docs/harness-protocol.md` удалён/переименован (симулировать через `unittest.mock.patch`
   пути или подмену функции чтения) → `body()` поднимает `FileNotFoundError`.

## Границы правки

- Правятся: `docs/harness-protocol.md`, `listik/migrate.py`, `AGENTS.md`, новый
  `tests/test_migrate.py`.
- Не менять: `README.md`, `API.md`, `CLAUDE.md` (порция c), `bin/listik`, `store.py`,
  `server.py`, `mcp.py`, `documents.py`, схему базы, `bin/listik-codex`,
  `project-skills/…/SKILL.md`, `web/`, `docs/specs/**` (спеку не «подгонять» под текст).
- Не запускать `listik init-projects` без `--dry-run` на рабочей базе: правка чужих проектов —
  решение автора после приёмки. `--dry-run` на рабочей базе допустим (читает `projects`).
- Десять правил не редактировать; расхождение правила с поведением кода — строкой в отчёте.
- Не класть в файл имена harness/моделей, токены, содержимое `config.toml`.

## Как проверить

```sh
python3 -m unittest tests.test_migrate -v                 # красные до правок — см. пометки
python3 -m unittest discover tests                        # всё зелёное
# Сценарий передачи держателя по протоколу — на временной базе, строго --local:
export LISTIK_DB=/tmp/listik-step02b.db && ./bin/listik --local init
ID=$(./bin/listik --local new "Проба" -p demo --stage s3-impl --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
./bin/listik --local claim $ID --holder agent:impl
./bin/listik --local claim $ID --holder agent:judge || echo "ожидаемый отказ: чужая карточка"
./bin/listik --local stage $ID --holder agent:judge         # s4-judge, держатель agent:judge
./bin/listik --local claim $ID --holder agent:judge         # идемпотентно, без ошибки
./bin/listik --local comment $ID "красный: пункт 3 не выполнен" -k verdict --actor agent:judge
./bin/listik --local show $ID --json | python3 -c 'import json,sys;d=json.load(sys.stdin);print(d["stage"], d["holder"])'   # s3-impl agent:judge
./bin/listik --local release $ID --actor agent:judge && ./bin/listik --local claim $ID --holder agent:impl   # без --force
grep -Eic 'claude|codex|dsh|deepseek|grok|gemini|writerllm|opus|sonnet' docs/harness-protocol.md   # 0
python3 -c 'from listik import migrate; print(migrate.block())' | head -60
python3 -c 'from listik import migrate; from pathlib import Path; print(migrate.upsert(Path("AGENTS.md"), dry_run=True))'   # unchanged
./bin/listik init-projects --dry-run | tail -3            # только отчёт, файлы не тронуты
git diff --stat HEAD                                      # 4 файла из «Границ правки»
```
