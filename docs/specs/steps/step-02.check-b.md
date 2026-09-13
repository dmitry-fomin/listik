# Приёмка порции 02.b. Канонический протокол и блок для AGENTS.md

## Предусловие

0. Порция a закоммичена: `git log --oneline` содержит её коммит, `grep -n "def set_needs_owner"
   listik/store.py` непуст. Иначе приёмка не проводится.

## Текст протокола (`docs/harness-protocol.md`)

1. Файл на русском, первая непустая строка — `## Listik — протокол harness`; в файле нет
   строк, начинающихся с `# ` (H1), и нет front-matter.
2. Десять нумерованных правил присутствуют и **дословно** совпадают с блоком `text` из
   `docs/specs/steps/step-02-harness-protocol.md` (судья сравнивает после нормализации
   пробелов и переносов: `python3 -c` с `re.sub(r"\s+", " ", …)` для каждого правила).
   Любое переформулированное или пропущенное правило — красный пункт.
3. Есть `### Роли по этапам` с четырьмя подпунктами `s1-spec`, `s2-review`, `s3-impl`,
   `s4-judge`, у каждого три помеченных строки «читать / менять / переход». По содержанию:
   `s1` → `stage` на `s2-review`, sticky; `s2` → `stage` на `s3-impl`, handoff, «сервер снимает
   держателя», дальше `ready`→`claim`; `s3` → `stage` на `s4-judge`, sticky, без коммита;
   `s4` → `comment -k verdict` с первым словом «зелёный»/«красный», красный возвращает на
   `s3-impl` автоматически, зелёный → коммит → `done -r`. Каждый этап называет команду
   `context … --stage <этап>` как источник чтения; `s3` — с `--portion`.
   Судья сверяет заявленную семантику с кодом: `config.DEFAULTS["routing"]["transitions"]`,
   `store.next_stage` (handoff → `holder=""`), `store.add_comment` (список слов красного
   вердикта). Расхождение текста с кодом — красный пункт.
3а. Перед подпунктами этапов есть абзац о держателе на переходах, и в нём названы все три
   механики: handoff — сервер снимает держателя, дальше `ready`→`claim`; sticky другому
   harness — `stage <id> --holder <следующий>`, принимающий начинает с идемпотентного `claim`;
   после красного вердикта держатель не меняется — судья, державший под своим именем, делает
   `release`, исполнитель — `claim`. Строка «переход» у `s3-impl` и строка «менять»/«читать» у
   `s4-judge` повторяют эту механику (`stage --holder <судья>`; `claim` судьи; `release` при
   красном). Отсутствие любого из трёх — красный пункт.
3б. Сценарий воспроизведён на временной базе **строго `--local`** (`export LISTIK_DB=/tmp/…;
   ./bin/listik --local init`) командами из блока `### Команды`, без `--force`: задача на
   `s3-impl`, `claim --holder agent:impl` → `claim --holder agent:judge` отказывает («уже
   удерживается») → `stage <id> --holder agent:judge` → карточка на `s4-judge` с держателем
   `agent:judge` → `claim --holder agent:judge` проходит без ошибки → `comment -k verdict
   "красный: …"` → `show --json`: `stage == "s3-impl"`, `holder == "agent:judge"` →
   `release` → `claim --holder agent:impl` проходит. Любой шаг с ошибкой или потребовавший
   `--force` — красный пункт.
3в. В строке `s4-judge` есть предупреждение, что подстроки «красн», `red`, `fail`, «не прой»,
   `❌` запрещены **во всём тексте** зелёного вердикта, а не только в первом слове, с
   объяснением, что сервер вернёт карточку на `s3-impl`. Судья подтверждает ловушку живым
   вызовом на временной базе: на `s4-judge` `comment -k verdict "зелёный: красных пунктов нет"`
   → `stage` становится `s3-impl`. Формулировка только про первое слово — красный пункт.
3г. В строке «читать» у `s3-impl` явно сказано, что `context --stage s3-impl` не содержит
   вердикта и журнала и что прошлый вердикт читается из `show <id>`. Судья сверяет с
   `listik --local context <id> --stage s3-impl --format json` после красного вердикта:
   `verdict` пуст/`null`, `journal` — пустой список, а `show <id> --json` содержит комментарий
   `kind == "verdict"`. Отсутствие оговорки — красный пункт.
4. `grep -Eic 'claude|codex|dsh|deepseek|grok|gemini|writerllm|opus|sonnet' docs/harness-protocol.md`
   печатает `0`.
5. `### Команды` содержит `ready`, `search`, `show`, `context`, `claim … --holder`,
   `heartbeat … --note`, `stage`, `comment … -k`, `needs-owner` (в т. ч. `--clear`), `release`,
   `done … -r`; каждая команда с флагами из блока принимается парсером: судья выполняет на
   временной базе минимум `claim`, `heartbeat`, `needs-owner`, `needs-owner --clear`, `comment -k review`,
   `stage`, `done -r` в форме из блока — без ошибок argparse.
6. Есть фраза о холодном старте: восстановление по `show <id>` и `context`, без чата.
7. Английского черновика в файле не осталось (нет строк `Listik is the queue`, `Roles:`).

## `listik/migrate.py`

8. `python3 -c 'from listik import migrate; print(migrate.block())'` печатает блок с маркерами,
   вводным абзацем (Listik вместо beads, `.beads` — архив, ссылки на `AGENTS.md`/`API.md`) и
   полным содержимым `docs/harness-protocol.md`; отдельного старого `sh`-блока команд из
   прежнего `BODY` в нём нет (команды — только внутри `### Команды` протокола).
9. Текст читается в момент вызова, а не на импорте: при подмене `paths.ROOT_DIR` (или пути к
   файлу внутри `migrate`) на пустой каталог `body()` поднимает `FileNotFoundError` с текстом,
   содержащим путь, а `import listik.migrate` при этом не падает. Проверяется тестом 5 из ТЗ
   и/или вручную через `python3 -c` с `unittest.mock.patch`.
10. `remove`, `migrate_all`, статусы `added|updated|unchanged|skipped|removed`, маркеры и
    `TARGETS` не изменились (чтение диффа); `./bin/listik init-projects --dry-run` на рабочей базе
    выполняется и печатает отчёт (файлы не изменены: `git -C <любой проект> status` чист — либо
    судья ограничивается `--dry-run` и не проверяет чужие деревья).

## `AGENTS.md` Listik

11. `python3 -c 'from listik import migrate; from pathlib import Path; print(migrate.upsert(Path("AGENTS.md"), dry_run=True))'`
    печатает `unchanged`. **Красный до правки** (маркеров в файле нет → `added`).
12. Разделов «Команды, которые нужны агенту» и «Этапы конвейера» в `AGENTS.md` больше нет;
    разделы «Что это», «Зависимости», «Тонкости», «UI-кит @zoloto585/facet», «Рабочий skill
    продукта Listik» на месте (`grep -n "^## "`).

## Тесты

13. `tests/test_migrate.py` есть, `python3 -m unittest tests.test_migrate -v` зелёный, в нём
    пять тестов из ТЗ: блок содержит десять правил и роли без имён harness; дословность правил
    относительно спеки; `added → unchanged → updated → removed` на временном файле с
    сохранением текста вне маркеров; `AGENTS.md` репозитория → `unchanged`; отсутствие файла →
    `FileNotFoundError`.
14. `python3 -m unittest discover tests` зелёный целиком.

## Границы

15. `git diff --stat HEAD` содержит только `docs/harness-protocol.md`, `listik/migrate.py`,
    `AGENTS.md`, `tests/test_migrate.py`. `README.md`, `API.md`, `CLAUDE.md`, `bin/listik`,
    `docs/specs/**`, `project-skills/**`, `bin/listik-codex`, код store/server/mcp — без
    изменений.
16. В диффе нет токенов, содержимого `config.toml`, личных путей кроме `~/Projects/Listik`.
