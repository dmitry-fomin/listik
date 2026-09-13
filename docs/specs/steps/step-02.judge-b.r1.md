# Приёмка порции 02.b, заход r1 — разбор

Вердикт: **зелёный**.

Чек-лист: `docs/specs/steps/step-02.check-b.md`; порция: `docs/specs/steps/step-02.b.md`;
дамп: `.git/feature-pipeline/step-02.diff-b.r1.txt` (4 файла, 346 вставок / 59 удалений).

## Предусловие

0. Зелёный. `git log --oneline` → `b1ab7a7 Шаг 02, порция a: needs-owner пишет вопрос и ответ
   в историю карточки`; `grep -n "def set_needs_owner" listik/store.py` → строка 318.

## Текст протокола

1. Зелёный. Первая строка `docs/harness-protocol.md:1` — `## Listik — протокол harness`;
   `grep -c '^# '` → 0, front-matter нет. Заголовки: 1 (`##`), 18 (`### Роли по этапам`),
   67 (`### Команды`).
2. Зелёный. Десять правил из блока ```` ```text ```` спеки `step-02-harness-protocol.md`
   (строки 12–23) извлечены независимо от теста исполнителя, склеены по переносам,
   нормализованы `re.sub(r"\s+", " ", …)` — все десять найдены в нормализованном тексте
   `docs/harness-protocol.md` (строки 6–16). Расхождений нет.
3. Зелёный по содержанию. `docs/harness-protocol.md:34-65`:
   - `s1-spec` → `stage <id>` на `s2-review`, sticky (строки 39–40);
   - `s2-review` → `stage <id>` на `s3-impl`, handoff, «сервер снимает держателя»,
     `ready` → `claim` (45–46);
   - `s3-impl` → `stage <id>` на `s4-judge`, sticky, «не коммитить» (53–54);
   - `s4-judge` → `comment -k verdict`, первое слово «зелёный»/«красный», автоматический
     возврат на `s3-impl`, зелёный → коммит → `done <id> -r` (60–65).
   `context … --stage <этап>` назван у всех четырёх этапов (35, 43, 49, 57), у `s3` —
   с `--portion` (49).
   Сверка с кодом: `listik/config.py:44-50` — `s1-spec:s2-review` sticky,
   `s2-review:s3-impl` handoff, `s3-impl:s4-judge` sticky, `s4-judge:done` handoff;
   `listik/store.py:470-476` — на handoff `fields["holder"] = ""`, на sticky держатель
   меняется только при явном `--holder`; `listik/store.py:441-446` — список слов красного
   вердикта `("красн", "red", "fail", "не прой", "❌")`, проверка по `text.lower()` целиком,
   возврат только со `stage == "s4-judge"`. Текст протокола коду соответствует.

   Наблюдение (не красный пункт): у `s4-judge` две помеченные строки вместо трёх — механика
   перехода вписана в строку «менять» (`docs/harness-protocol.md:60-65`), отдельной строки
   «переход» нет. Так прямо предписывает ТЗ порции (`step-02.b.md:91-99`, у `s4-judge`
   перечислены только «читать» и «менять»), и сам чек-лист в пункте 3а ссылается для
   `s4-judge` на строки «менять»/«читать», а не на «переход». Содержательно всё требуемое
   пунктом 3 для `s4` присутствует.
3а. Зелёный. Абзац о держателе — `docs/harness-protocol.md:20-32`, перед подпунктами этапов,
   все три механики названы: handoff (23–24), sticky с `stage <id> --holder <следующий>` и
   идемпотентным `claim` принимающего (25–29), возврат после красного без смены держателя,
   `release` судьи и повторный `claim` исполнителя (30–32). Повтор механики: строка «переход»
   у `s3-impl` — `stage <id> --holder <судья>` (53–54); у `s4-judge` — `claim <id> --holder
   <свой>` в «читать» (57–59) и `release <id>` при красном в «менять» (63–65).
3б. Зелёный. Сценарий прогнан на временной базе строго `--local`
   (`LISTIK_DB=…/scratchpad/step02b.db`, `./bin/listik --local init`), без `--force`:
   - `new "Проба" -p demo --stage s3-impl` → `demo-ma9x`;
   - `claim --holder agent:impl` → ok;
   - `claim --holder agent:judge` → отказ `ValueError: задача demo-ma9x уже удерживается
     agent:impl; сначала release` (`listik/store.py:375`);
   - `stage demo-ma9x --holder agent:judge` → `s4-judge agent:judge`;
   - `claim --holder agent:judge` → ok (идемпотентно);
   - `comment … "красный: пункт 3 не выполнен" -k verdict --actor agent:judge`;
   - `show --json` → `stage == "s3-impl"`, `holder == "agent:judge"`;
   - `release --actor agent:judge` → `claim --holder agent:impl` → ok.
3в. Зелёный. Предупреждение про «во всём тексте» — `docs/harness-protocol.md:60-63`
   («сервер ищет их во всём тексте и вернёт карточку на `s3-impl`»), не только про первое
   слово. Ловушка подтверждена живым вызовом: карточка возвращена на `s4-judge`
   (`stage --to s4-judge`), затем `comment … "зелёный: красных пунктов нет" -k verdict` →
   `show --json` даёт `stage == "s3-impl"`.
3г. Зелёный. Оговорка — `docs/harness-protocol.md:49-51` («в нём последний review, вердикта и
   журнала на `s3` нет … и обязательно `show <id>` — там … прошлый вердикт судьи»).
   Проверка: `context demo-ma9x --stage s3-impl --format json` после красного вердикта →
   `verdict: None`, `journal: []`; `show demo-ma9x --json` → комментарий с `kind == "verdict"`.
4. Зелёный. `grep -Eic 'claude|codex|dsh|deepseek|grok|gemini|writerllm|opus|sonnet'
   docs/harness-protocol.md` → `0`.
5. Зелёный. `### Команды` (69–88) содержит `ready`, `search`, `show`, `context … --stage
   [--portion]`, `claim --holder`, `heartbeat --holder --note`, `stage` и `stage --holder`,
   `comment -k journal|review|verdict`, `needs-owner`, `needs-owner --clear`, `release`,
   `done -r`. На временной базе выполнены в форме из блока: `ready`, `search`, `show`,
   `context` (в т. ч. `--portion`), `claim --holder`, `heartbeat --holder --note`,
   `comment -k journal`, `comment -k review`, `needs-owner`, `needs-owner --clear`, `stage`,
   `stage --holder`, `release`, `done -r` — ошибок argparse нет. (Один прогон `claim` упёрся
   в занятое worktree от предыдущей карточки той же временной базы — это состояние базы, не
   разбор аргументов; после `release` предыдущей карточки `claim --holder agent:x` проходит.)
   Дополнительно сверены команды из «Ролей», лежащие вне блока: `dep link` и `relates-to`
   (`./bin/listik dep --help` → подкоманда `link`, `--dep-type relates-to`), `new --spec
   --checklist`, `listik actors` — все существуют.
6. Зелёный. `docs/harness-protocol.md:90-91` — восстановление по `show <id>` и `context`,
   «чат не нужен».
7. Зелёный. `grep -cE 'Listik is the queue|Roles:'` → `0`; английский черновик переписан.

## `listik/migrate.py`

8. Зелёный. `python3 -c 'from listik import migrate; print(migrate.block())'` печатает блок
   с `<!-- BEGIN LISTIK -->` … `<!-- END LISTIK -->`, вводным абзацем (Listik вместо beads,
   `.beads` — архив, ссылки на `~/Projects/Listik/AGENTS.md` и `API.md`) и полным текстом
   протокола; длина 5133 байта. Старой шпаргалки нет: ни `$L board`, ни `$L new ` в блоке,
   `### Команды` встречается ровно один раз (`listik/migrate.py:23-31,34-42`).
9. Зелёный. Чтение — внутри `_compute_body()` (`listik/migrate.py:34-42`), на импорте файл не
   читается. Ручная проверка: `patch.object(paths, "ROOT_DIR", <пустой каталог>)` → `body()`
   поднимает `FileNotFoundError: docs/harness-protocol.md не найден (<путь>/docs/
   harness-protocol.md) — блок правил без протокола ставить нельзя`; путь в сообщении есть,
   `import listik.migrate` при этом проходит.
10. Зелёный. `remove` (84–95), `migrate_all` (98–111), статусы `added|updated|unchanged|
    skipped|removed`, `BEGIN`/`END`, `TARGETS` в диффе не менялись — изменены только `BODY` →
    `INTRO`/`body()`/`_compute_body()` и сигнатуры `block`/`upsert` (`body: str | None = None`).
    `bin/listik` не тронут, `cmd_init_projects` (`bin/listik:229-253`) вызывает
    `migrate.migrate_all` без `body`. `./bin/listik init-projects --dry-run` выполняется и
    печатает отчёт: «добавлено: 6, обновлено: 33, без изменений: 0, удалено блоков: 0,
    пропущено: 5»; чужие деревья не проверялись, запуск только с `--dry-run`, файлы не
    изменялись.

## `AGENTS.md`

11. Зелёный. `migrate.upsert(Path("AGENTS.md"), dry_run=True)` → `unchanged`.
12. Зелёный. `grep -n "^## " AGENTS.md`: «Что это» (1), блок протокола (10, 18), «Зависимости»
    (111), «Тонкости» (119), «UI-кит @zoloto585/facet» (127), «Рабочий skill продукта Listik»
    (141). Разделов «Команды, которые нужны агенту» и «Этапы конвейера» нет. Число MCP-
    инструментов (18) в «Тонкостях» не тронуто.

## Тесты

13. Зелёный. `python3 -m unittest tests.test_migrate -v` → 5 тестов, OK: десять правил + роли
    без имён harness; дословность относительно спеки; `added → unchanged → updated → removed`
    на временном файле; `AGENTS.md` → `unchanged`; отсутствие файла → `FileNotFoundError`.
14. Зелёный. `python3 -m unittest discover tests` → `Ran 78 tests … OK`.

## Границы

15. Зелёный. Дифф порции — ровно `AGENTS.md`, `docs/harness-protocol.md`, `listik/migrate.py`,
    `tests/test_migrate.py`. `README.md`, `API.md`, `CLAUDE.md`, `bin/listik`,
    `project-skills/**`, `bin/listik-codex`, `store.py`/`server.py`/`mcp.py`/`documents.py` —
    без изменений; `docs/specs/**` исполнителем не правился.
    В рабочем дереве дополнительно изменён `docs/specs/listik-product.journal.md` и лежат
    неотслеживаемые бумаги шага (`step-02*.md`) — это папки оркестратора, в дифф порции они
    не входят и в коммит не берутся.
16. Зелёный. В диффе нет токенов, паролей, ключей, содержимого `config.toml`; личные пути —
    только `~/Projects/Listik`.

## Замечания без вердикта (в коммит не влияют)

- `tests/test_migrate.py:475-481`: мёртвая переменная `outside_marker_original` (обе ветки
  тернарного выражения дают `original`, значение не используется), а равенство текста вне
  маркеров проверяется через `.strip("\n")`, то есть чуть слабее, чем «побайтно» из ТЗ.
  Побайтное равенство при этом покрыто следующим шагом того же теста:
  `remove` → `target.read_text() == original`.

## Проверки, выполненные судьёй

```
git log --oneline -3
grep -n "def set_needs_owner" listik/store.py
python3 <извлечение и нормализация десяти правил из спеки + поиск в протоколе>
grep -c '^# ' docs/harness-protocol.md; grep -n '^#' docs/harness-protocol.md
grep -Eic 'claude|codex|dsh|deepseek|grok|gemini|writerllm|opus|sonnet' docs/harness-protocol.md
export LISTIK_DB=<scratchpad>/step02b.db; ./bin/listik --local init
<сценарий 3б: new/claim/claim/stage --holder/claim/comment -k verdict/show/release/claim>
<ловушка 3в: stage --to s4-judge; comment "зелёный: красных пунктов нет" -k verdict; show>
./bin/listik --local context demo-ma9x --stage s3-impl --format json
<прогон всех команд блока ### Команды на временной базе>
python3 -c 'from listik import migrate; print(migrate.block())'
python3 <patch paths.ROOT_DIR → body() → FileNotFoundError>
python3 -m unittest tests.test_migrate -v
python3 -m unittest discover tests
python3 -c 'from listik import migrate; from pathlib import Path; print(migrate.upsert(Path("AGENTS.md"), dry_run=True))'
./bin/listik init-projects --dry-run | tail -5
grep -n "^## " AGENTS.md; git status --porcelain; git diff --stat HEAD
```

Рабочее дерево судьёй не перекладывалось, код не правился.
