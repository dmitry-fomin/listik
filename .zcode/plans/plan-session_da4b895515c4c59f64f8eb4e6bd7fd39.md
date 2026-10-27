# etalon-pipeline → listik-pipeline: переименование + консистентность

## Исходное состояние (проверено чтением всех файлов)

`plugins/etalon-pipeline` (v0.1.0, ещё не в git) — конвейер «ТЗ → критика → код → линза → судья», оркестратор — сессия Claude Code, роли — локальные субагенты + внешние харнессы (pi, devin, codex, grok). Коммитит всегда оркестратор. Критиков зовёт оркестратор (все сразу, сводку сводит сам) — здесь противоречий нет.

Решения пользователя: скилы запуска — по режимам (`/listik-pipeline:high`, без флагов); агенты — `<роль>-<усилие>` без префикса; grok-судья остаётся (внешнее требование, раскладку не трогаем).

## 1. Переименование

- `mv plugins/etalon-pipeline plugins/listik-pipeline`; удалить `.DS_Store` внутри.
- `plugins/listik-pipeline/.claude-plugin/plugin.json`: `name: "listik-pipeline"`, `displayName: "Listik pipeline"` (описание уже корректное).
- `.claude-plugin/marketplace.json`: запись `etalon-pipeline` → `listik-pipeline` (name, source, description — убрать «на эталонном наборе»).
- Агенты: frontmatter `name: etalon-<role>-<effort>` → `<role>-<effort>` во всех 20 файлах; `etalon-orchestrator` → `orchestrator`. Имена файлов уже `writer-xhigh.md` и т.п. — не меняются.
- Все ссылки `etalon-pipeline:etalon-<роль>-<усилие>` → `listik-pipeline:<роль>-<усилие>` (`agents/orchestrator.md:34`, `references/lineups.md:4,37`, досье в README.html).
- Префикс коммита `etalon: <id>` → `listik-pipeline: <id>` (`orchestrator.md:24`, README.html:1244,1370); метка сессии devin `etalon-<id>-impl` → `lp-<id>-impl` (`lineups.md:41,49`).
- Пробел «эталон/etalon» по всему новому каталогу добить grep-ом (описания скилов «…эталон-пайплайна», README.html title и досье).

## 2. Скилы запуска — по режимам (вместо `etalon` + флаг)

Удалить `skills/etalon/`; создать шесть тонких скилов `skills/{xhigh,high,medium,low,xlow,nano}/SKILL.md`, каждый ~10 строк:

- `argument-hint: "<описание задачи или номер карточки Listik>"`;
- тело: «Режим этого запуска — <режим>. Правила потока — `agents/orchestrator.md` (читаешь до первого действия, единственный источник). Раскладка — `references/lineups.md`. Задачу отдаёшь писателю как есть (в xlow/nano писателя нет — сразу worktree и код). Статусы фиксированы. Запуск скила — согласие автора отправить задачу, ТЗ, чек-лист и дифф наружу. Коммит — всегда оркестратор.»

Сопутствующие правки текста: `orchestrator.md` «аргументы вызова (в них режим)» → «режим — имя скила, которым вызвали»; «В `--xlow`/`--nano`» → «В `xlow`/`nano`» (2 места); `lineups.md` — убрать `--` у режимов в таблице и тексте; `skills/writer/SKILL.md` argument-hint — убрать флаги режимов. README.md «Запуск» и README.html (board-line, блок команды в досье оркестратора, панели режимов «режим (флаг)») — примеры вида `/listik-pipeline:high "Добавить экспорт в CSV"`, `/listik-pipeline:nano listik-abc1`.

Роль-скилы `writer/implementer/lens/judge` остаются (ручной запуск роли), пути внутри — на `plugins/listik-pipeline/references/…`.

## 3. Правки консистентности (найдено при сверке README ↔ скилы/агенты)

1. **Коммит судьи**: `skills/judge/SKILL.md:3,9` «вердикт и коммит» / «При `PASS` коммит твой» → «Коммит и `done` — оркестратора; твой — только вердикт в карточку». Иначе противоречит `orchestrator.md:24`, `lineups.md:17`, `judge.md:14`, README.md:36, README.html:1244.
2. **Права линзы**: `lineups.md:16` «линза и судья — bash (…вести карточку)» → линза: bash только прогнать проверку, карточку не трогает (`lens.md:3`, README.html:1532); судья: bash + карточка (claim, heartbeat, вердикт).
3. **Вердикт без судьи**: README.html:1554 «…и сам пишет вердикт» → «коммит и закрытие — оркестратор; вердикт не пишет никогда — приёмку линзы фиксирует `journal`-записью» (соответствует `orchestrator.md:42`).
4. **Кто держит карточку**: README.html:1363 «`claim`… карточку держит он один, роли — его субагенты» → «выдаёт карточку этапами (`stage --holder`); внешний харнесс берёт её сам, за локального субагента держит и heartbeat'ит оркестратор» (`orchestrator.md:42`, `lineups.md:79–82`, README.html:1255).
5. **Петля линзы**: README.html:1150 «с 3-го захода сразу судья» → «после правки — судья; линза повторно не зовётся» (`orchestrator.md:21`, README.html:1247).
6. **Кривая фраза**: README.html:1533–1534 «когда приёмка идёт нашла находки» → «При `RISK: YES` или находках линзы приёмку ведёт судья».
7. **Досье судьи**: README.html:1571 «grok (xhigh…xlow)» → «grok (все режимы, кроме nano; усилие xhigh в xhigh/high, high в остальных) · codex (nano)».
8. **Ярлыки режимов**: README.md:19 и описание скилов — «без ТЗ» → «без ТЗ, критики и линз» (сверить с README.html «Без ТЗ и критики»).
9. **Рамка README.md:3**: «роли он выдаёт другим харнессам» → «роли — локальные субагенты и внешние харнессы» (как в plugin.json:5).
10. **grok отдельно**: в README.md «Что нужно» добавить строку — плагин grok не входит в маркетплейс listik, ставится отдельно; при его отсутствии конвейер останавливается до первого действия.

## 4. Модели в lineups.md (критерий «разные модели по режимам»)

Одна строка под таблицей раскладки: «Модели: pi glm = GLM 5.3 Flash, pi deepseek = DeepSeek V4.1 Flash, devin = SWE-2, grok = grok-4.7, codex = из `~/.codex/config.toml`» — модельное измерение становится явным без дублирования.

## 5. Проверка

- `grep -rn "эталон\|etalon" plugins/listik-pipeline .claude-plugin/marketplace.json` — пусто.
- `python3 -m json.tool` на `plugin.json` и `marketplace.json`; frontmatter всех 21 агента и 10 скилов парсится (PyYAML), имена уникальны.
- README.html — только текстовые правки, вёрстку не трогаем.

## Не делаю

- Не трогаю `plugins/pipeline-core` и его незакоммиченный рефакторинг, пресеты, `docs/pipeline-core-agents-critique-*.md`.
- Ничего не коммичу (вне запроса).
- Раскладку судьи (grok) и список харнессов не меняю.