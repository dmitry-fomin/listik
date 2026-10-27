# Раскладка: режим → роль

Оркестратор — сессия Claude Code. Роль он не исполняет: выдаёт её харнессу из строки.
Все роли, включая критиков, зовёт оркестратор: субагент не может звать других субагентов.
`Claude <усилие>` — локальный субагент `listik-pipeline:<роль>-<усилие>`, параметр `model` не
передаётся: модель роли — модель главной сессии (из сессии на Sonnet все роли пойдут на Sonnet).

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
HARNESS: <имя>
WORKTREE: <abs path>
SPEC: <abs path>
CHECKLIST: <abs path>
```

Критику вместо `CARD:` — `REVIEW: <abs path к <id>.review-<критик>.md>`. Судье после находок линзы — ещё `LENS: <abs path к <id>.lens.md>`. В `xlow`/`nano` ТЗ нет — вместо `SPEC:`/`CHECKLIST:` исполнителю и судье: `CHECK: acceptance карточки; нет его — тесты затронутых модулей, полный набор не запускать`.

`WORKTREE:` — только исполнителю, линзе и судье. Критик работает в **основном дереве** (worktree создаётся на шаге 3, после критики): строка `WORKTREE:` в его задание не идёт, `--cwd` — корень репозитория.

## Запуск

Локальный субагент — `Agent listik-pipeline:<роль>-<усилие>`. Повтор после `FIXES` — та же сессия (`SendMessage` по agent id).

Внешний харнесс — скилом или командой, фоном; ответ — `result <job-id>` (кроме grok). Внешнему критику и линзе забор — после статуса `done` `result <job-id>` с выводом прямо в файл (stdout `result` — ровно ответ); годен только код 0, в контекст рецензию не читаешь. Продолжение после `FIXES` — только у исполнителя-девина, той же сессией: `resume --session lp-<id>-impl`. Критик, линза и судья — всегда свежим заходом.

```bash
# pi — критик (read) и линза (bash)
pi:pi-delegate --channel <glm|deepseek> --permission <read|bash> --cwd <корень|wt> --label "<id> <роль>" <задание>

# devin — код (write = режим dangerous)
devin:devin-delegate --thinking <high|max> --permission write --session lp-<id>-impl --cwd <wt> --label "<id> исполнитель" <задание>

# codex — судья nano (write: в read-only у песочницы нет сети до Listik; без --model/--effort — настройки ~/.codex/config.toml)
codex:codex-delegate --permission write --cwd <wt> --label "<id> судья" <задание>

# grok — судья (команда, не скил: Skill(grok:…) вешает сессию); worktree — строкой задания, все три вызова (delegate/status/result) — из одного каталога
/grok:delegate --background --write --no-web --fresh --model grok-4.7 --effort <xhigh|high> <задание файлом>
```

grok: усилие `xhigh` в `xhigh`/`high`, `high` в остальных. `--write` обязателен, как в старом конвейере: без него форвардер вправе пустить судью на чтение, и ни проверка, ни вердикт в карточку не пройдут. Задание судье — файлом
`<id>.judge.r<N>.task.md`, в команду после флагов одна строка `TASK_FILE: <абс путь>`
(текст задания в командную строку не вставлять). Ход — `/grok:status <job-id>`,
забор — `/grok:result <job-id>` **из того же каталога, что запуск**; ждать терминальной фазы
`done`/`failed`/`cancelled`.

## Готовность канала

`pi:pi-check`, `codex:codex-check`, `devin:devin-check` — до выдачи (все роли выдаёт оркестратор). Судья codex — ещё `[sandbox_workspace_write] network_access = true` в `~/.codex/config.toml`: без сети claim и вердикт не дойдут до Listik. grok — `/grok:setup` + короткий
прогон: `/grok:delegate --no-web --model grok-4.7 --effort low Reply with exactly one word: OK`. Не
`ready` — стоп-фактор: журнал `STOP: <role> — <harness> unavailable: <причина>`, `needs-owner`, тот же
вопрос в чат. Роль не подменяется другим харнессом, моделью ниже или локальным субагентом.

## Грабли

- **Локальный субагент в фоне просит разрешение у человека на каждый `Write`/`Edit`/`Bash`**, а в фоне отвечать некому: плагинные субагенты не принимают `permissionMode` (frontmatter игнорируется). Для этого у плагина есть хук `hooks/approve-agents.py` (`PermissionRequest`, как в pipeline-core): включи настройку плагина `auto_approve_agents` — правки в проекте и worktree и неопасный Bash ролей пройдут без запроса. Выключена — зови локальные роли на переднем плане, иначе исполнитель встанет на первой же правке.
- **`${CLAUDE_PLUGIN_ROOT}` живёт только в теле скилов и агентов.** В файлах, прочитанных Read-ом, и в Bash-окружении его нет: путь берётся из скила режима и уходит в задание готовой строкой.
- **Вердикт — только на `s4-judge`,** иначе Listik молча сохранит его как обычный комментарий (`verdict_accepted: false`).
- **Красный вердикт оставляет держателя-судью:** без `listik release <id>` исполнитель не возьмёт карточку и будет ждать 24 ч до истечения окна возврата.

## Окна ожидания

Критика — 15 минут на всех критиков задачи. Судья grok — 3 часа (не мост, предела 7200 с у него нет), судья codex и исполнитель — 2 часа: предел фоновых
скриптов мостов 7200 с, окно больше него не живёт (или передавай `--timeout` в делегат). Исчерпано — `failed`/`timeout`/`canceled`/`cancelled`/`orphaned`/пустой ответ — не
`FIXES`: вердикта не было. Журнал, `<харнесс>-check`, `needs-owner`, следующий шаг не выбираешь.

## Listik

Оркестратор выдаёт карточку: `stage <id> --to <этап> --holder <харнесс> --actor agent:claude
--harness claude`. Роль, которой выдана карточка (писатель, исполнитель, судья), ведёт её сама: `claim <id> --holder <харнесс> --actor
agent:<харнесс> --harness <харнесс>`, heartbeat, журнал, вердикт — и внешний харнесс, и локальный
субагент (у него есть Bash). Критик и линза карточку не трогают. Коммит и `done` — всегда оркестратор. Полный протокол — `AGENTS.md`
проекта: вопросы человеку, `--actor`, `release`, повторная выдача.
