# Раскладка: режим → роль

Оркестратор — сессия Claude Code. Роль он не исполняет: выдаёт её харнессу из строки.
Все роли, включая критиков, зовёт оркестратор: субагент не может звать других субагентов.
`Claude <усилие>` — локальный субагент `listik-pipeline:<роль>-<усилие>`, параметр `model` не
передаётся: модель роли — модель главной сессии (из сессии на Sonnet все роли пойдут на Sonnet).
Держатель локальной роли — `claude-<модель>` (`claude-opus`, `claude-haiku`), `--harness claude`.

| Режим | ТЗ | Критика | Код | Линза | Судья |
| --- | --- | --- | --- | --- | --- |
| `xhigh` | Claude xhigh | Claude xhigh + pi deepseek + pi glm | Claude xhigh | pi glm | grok |
| `high` | Claude high | Claude high + pi glm | Claude high | pi glm | grok |
| `medium` | Claude medium | Claude medium + pi deepseek | Claude medium | Claude medium | grok |
| `low` | Claude low | pi deepseek | devin max | Claude low | grok |
| `xlow` | — | — | devin max | — | grok |
| `nano` | — | — | devin high | — | codex |

Модели: pi glm — GLM 5.3 Flash, pi deepseek — DeepSeek V4.1 Flash, devin — SWE-2, grok — grok-4.7
(усилие по режиму), codex — из `~/.codex/config.toml`.

Права: критик — `read` (локальный пишет только файл рецензии, внешний отвечает текстом — в файл его кладёт оркестратор); линза — `bash` (прогнать команду проверки; карточку не трогает); судья —
`write` (прогнать проверку, достучаться до Listik) и карточка (claim, heartbeat, вердикт), правка кода запрещена телом роли; код — `write`. Коммит — всегда
оркестратор: судья не коммитит.

## Задание роли

Задание одно и то же для внешнего харнесса и локального субагента. Роль харнесс читает сам, пересказывать её в задании не нужно. Путь тела роли — абсолютный: `${CLAUDE_PLUGIN_ROOT}` ни в Bash-окружении, ни в файлах, прочитанных Read-ом, не подставляется, поэтому сам путь берёшь из скила режима (там он подставлен) и в задание кладёшь готовую строку. Относительный `plugins/…` из кэша и из worktree не резолвится. Задание — строки `KEY: value`, ключи на английском заглавными (словарь — `statuses.md`, «Ключи задания»):

```
ROLE: <абс путь к references/<роль>.md из скила режима>
CARD: <id>
HARNESS: <имя: pi, devin, codex, grok; локальная роль — claude-<модель>>
WORKTREE: <abs path>
SPEC: <abs path>
CHECKLIST: <abs path>
REPLY: first line of your reply is exactly one word: <слова роли>; nothing before it, no quotes, no bold
```

`REPLY:` — всегда последней строкой. Слова роли (`statuses.md`): писателю `DRAFT, QUESTION, CANNOT`, во втором заходе `READY, QUESTION, CANNOT` и `second line: RISK: NO | RISK: YES — <reason>`; локальному критику `REVIEWED <path>, CANNOT`; исполнителю `DONE, CANNOT, QUESTION`; линзе и судье `PASS, FIXES, CANNOT`. Внешнему критику — `REPLY: the review itself, starting with its first ## section; no preface`.

Критику вместо `CARD:` — `REVIEW: <abs path к <id>.review-<критик>.md>`. Судье после находок линзы — ещё `LENS: <abs path к <id>.lens.md>`. В `xlow`/`nano` ТЗ нет — вместо `SPEC:`/`CHECKLIST:` исполнителю и судье: `CHECK: acceptance карточки; нет его — тесты затронутых модулей, полный набор не запускать`.

`WORKTREE:` — только исполнителю, линзе и судье. Критик работает в **основном дереве** (worktree создаётся на шаге 3, после критики): строка `WORKTREE:` в его задание не идёт, `--cwd` — корень репозитория.

## Запуск

Локальный субагент — `Agent listik-pipeline:<роль>-<усилие>`. Повтор после `FIXES` — та же сессия (`SendMessage` по agent id).

Внешний харнесс — командой скрипта моста, фоном, не скилом: скилы `*-delegate`/`*-jobs` работают в fork-контексте и сами ждут и забирают ответ, а ответ должен лечь в файл, не читая. Скрипт — `<installPath>/scripts/…`, `installPath` — из `~/.claude/plugins/installed_plugins.json` по ключу `pi@listik`, `devin@listik`, `codex@listik`, `grok@grok-build` (версию в путь не зашиваешь, в кэше их бывает несколько):

```bash
python3 -c 'import json,os,sys;print(json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))["plugins"][sys.argv[1]][0]["installPath"])' pi@listik
```

Задание — в stdin (heredoc). `run --background` печатает job-id; ответ — `result <job-id> --wait <сек>`, годен только код 0 (при таймауте или отмене `result` печатает обрубок с ненулевым кодом). Внешнему критику и линзе — `result` прямо в файл (stdout `result` — ровно ответ), в контекст не читаешь. Продолжение после `FIXES` — только у исполнителя-девина, той же сессией: `resume lp-<id>-impl`. Критик, линза и судья — всегда свежим заходом.

```bash
# pi — критик (read) и линза (bash)
pi-run.sh run --background --permission <read|bash> --channel <glm|deepseek> --cwd <корень|wt> --label "<id> <роль>" <<'EOF'
<задание>
EOF
pi-run.sh result <job-id> --wait <сек> > <id>.review-pi-<канал>.md   # линза — > <id>.lens.md

# devin — код (write = режим dangerous); после FIXES — resume той же сессии
devin-run.sh run --background --thinking <high|max> --permission write --session lp-<id>-impl --cwd <wt> --label "<id> исполнитель" <<'EOF' … EOF
devin-run.sh resume lp-<id>-impl --background --thinking <high|max> --permission write --cwd <wt> <<'EOF' … EOF
devin-run.sh result <job-id> --wait <сек>

# codex — судья nano (write: в read-only у песочницы нет сети до Listik; без --model/--effort — настройки ~/.codex/config.toml)
codex-run.sh run --background --permission write --cwd <wt> --label "<id> судья" <<'EOF' … EOF
codex-run.sh result <job-id> --wait <сек>

# grok — судья: прямой node, пока нет моста (listik-g6xh); все три вызова — из одного каталога
node <installPath>/scripts/grok-companion.mjs task --background --write --no-web --fresh --model grok-4.7 --effort <xhigh|high> "TASK_FILE: <абс путь>"
node <installPath>/scripts/grok-companion.mjs status <job-id> --json   # job.status: running → completed | failed | cancelled
node <installPath>/scripts/grok-companion.mjs result <job-id> > <id>.judge.r<N>.out.md
```

grok: усилие `xhigh` в `xhigh`/`high`, `high` в остальных. `--write` обязателен: без него судья пойдёт на чтение, и ни проверка, ни вердикт в карточку не пройдут. Задание судье — файлом
`<id>.judge.r<N>.task.md`, в команду после флагов одна строка `TASK_FILE: <абс путь>`
(текст задания в командную строку не вставлять). Опрос `status --json` — до терминального `completed`/`failed`/`cancelled`, забор `result` — из того же каталога, что запуск. `result` grok склеивает ход работы с ответом (до listik-g6xh): слово ответа там не первой строкой, поэтому исход судьи grok читаешь из карточки — последний `verdict`, первая строка `VERDICT: PASS` или `VERDICT: FAIL`; вердикта нет — сбой роли (`statuses.md`).

## Готовность канала

`pi-run.sh check --probe-timeout 30`, `devin-run.sh check`, `codex-run.sh check` — до выдачи (все роли выдаёт оркестратор), годен код 0; у pi ещё канал роли — `ok` (код 0 значит, что жив хоть один канал). Судья codex — ещё `[sandbox_workspace_write] network_access = true` в `~/.codex/config.toml`: без сети claim и вердикт не дойдут до Listik. grok — `grok-companion.mjs setup --json` + короткий
прогон: `task --no-web --model grok-4.7 --effort low "Reply with exactly one word: OK"`. Не
готов — стоп-фактор: журнал `STOP: <role> — <harness> unavailable: <причина>`, `needs-owner`, тот же
вопрос в чат. Роль не подменяется другим харнессом, моделью ниже или локальным субагентом.

## Грабли

- **Локальный субагент в фоне просит разрешение у человека на каждый `Write`/`Edit`/`Bash`**, а в фоне отвечать некому: плагинные субагенты не принимают `permissionMode` (frontmatter игнорируется). Для этого у плагина есть хук `hooks/approve-agents.py` (`PermissionRequest`, как в pipeline-core): включи настройку плагина `auto_approve_agents` — правки в проекте и worktree и неопасный Bash ролей пройдут без запроса. Выключена — зови локальные роли на переднем плане, иначе исполнитель встанет на первой же правке.
- **`${CLAUDE_PLUGIN_ROOT}` живёт только в теле скилов и агентов.** В файлах, прочитанных Read-ом, и в Bash-окружении его нет: путь берётся из скила режима и уходит в задание готовой строкой.
- **Вердикт — только на `s4-judge`,** иначе Listik молча сохранит его как обычный комментарий (`verdict_accepted: false`).
- **Красный вердикт оставляет держателя-судью:** без `listik release <id>` исполнитель не возьмёт карточку и будет ждать 24 ч до истечения окна возврата.

## Окна ожидания

Критика — 15 минут на всех критиков задачи: pi glm тратит на рецензию около 8 минут, так что `result --wait` — на остаток окна, раньше его молчание не сбой. Судья grok — 3 часа (не мост, предела 7200 с у него нет), судья codex и исполнитель — 2 часа: предел фоновых
скриптов мостов 7200 с, окно больше него не живёт (или передавай `--timeout` в `run`). Исчерпано — `failed`/`timeout`/`canceled`/`cancelled`/`orphaned`/пустой ответ — не
`FIXES`: вердикта не было. Журнал, `<харнесс>-run.sh check`, `needs-owner`, следующий шаг не выбираешь.

## Listik

Оркестратор выдаёт карточку: `stage <id> --to <этап> --holder <HARNESS> --actor agent:claude
--harness claude`, где `<HARNESS>` — то же имя, что в задании (локальная роль — `claude-<модель>`). Роль, которой выдана карточка (писатель, исполнитель, судья), ведёт её сама: `claim <id> --holder <HARNESS> --actor
agent:<HARNESS> --harness <HARNESS до «-»>`, heartbeat, журнал, вердикт — и внешний харнесс, и локальный
субагент (у него есть Bash). Критик и линза карточку не трогают. Коммит и `done` — всегда оркестратор. Полный протокол — `AGENTS.md`
проекта: вопросы человеку, `--actor`, `release`, повторная выдача.
