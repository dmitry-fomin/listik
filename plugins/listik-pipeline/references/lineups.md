# Механика ролей

Раскладка (кто, на какой модели и с каким усилием) — в скиле режима (`skills/<режим>/SKILL.md`):
маршрут `lp-<режим>` из Listik, при его отсутствии — таблица скила. Здесь — как роль зовётся.

Оркестратор — сессия Claude Code. Роль он не исполняет: выдаёт её харнессу из раскладки.
Роли зовёт оркестратор, критиков — писатель: субагент может звать субагентов и скилы (до трёх
уровней ниже главной сессии). Строку «Критика» оркестратор передаёт писателю в `CRITICS:`.
Локальная роль — субагент из поля `agent`, модель — параметром `model` вызова `Agent` из поля
`model` (без него роль ушла бы на модель главной сессии). Усилие зашито во frontmatter агента.
Держатель локальной роли — `claude-<модель>` (`claude-opus`, `claude-haiku`), `--harness claude`.

## Маршрут

`listik routes --json` → запись с `key: lp-<режим>` (`plugin: listik-pipeline`, `skill: <режим>`),
поле `roles` — объект: ключ — роль, значение — массив исполнителей (у критика их несколько, у
остальных один). Роли нет в объекте — этапа в режиме нет.

| Роль | Ключ |
| --- | --- |
| ТЗ | `writer` |
| Критика | `critic` |
| Код | `implementer` |
| Линза | `lens` |
| Судья | `judge` |

Исполнитель:

| Поле | Значение |
| --- | --- |
| `harness` | `claude`, `pi`, `devin`, `codex`, `grok` |
| `agent` | только у `claude`: `listik-pipeline:<роль>-<усилие>` |
| `model` | `claude`: `opus`/`sonnet`/`haiku` → параметр `model` у `Agent`; `grok`: `--model`; `devin`, `codex` — справочно |
| `channel` | только у `pi`: `glm`/`deepseek` → `--channel`; модель канала мост берёт из `listik harnesses pi --json` |
| `effort` | `claude`: справочно (зашито в `agent`); `pi`, `devin`: `--thinking`; `grok`: `--effort` |

Файл поставки — `routes.json` в корне Listik. Пока Listik ввозит только старый формат
(маршруты `full-*`, `cc-*`, `claude-*`), `lp-*` в `listik routes --json` нет и скил идёт по своей таблице.

Права: критик — `read` (локальный пишет только файл рецензии, внешний отвечает текстом — в файл его кладёт писатель); линза — `bash` (прогнать команду проверки; карточку не трогает); судья —
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
CRITICS: <только писателю: критики раскладки через «;» — claude listik-pipeline:critic-high model=sonnet; pi channel=glm thinking=high>
REPLY: first line of your reply is exactly one word: <слова роли>; nothing before it, no quotes, no bold
```

`REPLY:` — всегда последней строкой. Слова роли (`statuses.md`): писателю `READY, QUESTION, CANNOT` и `second line after READY: RISK: NO | RISK: YES — <reason>`; локальному критику `REVIEWED <path>, CANNOT`; исполнителю `DONE, CANNOT, QUESTION`; линзе и судье `PASS, FIXES, CANNOT`. Внешнему критику — `REPLY: the review itself, starting with its first ## section; no preface`.

Исполнителю, линзе и судье `SPEC:` — файл порции `<id>.<X>.md`, `CHECKLIST:` — её `<id>.check-<X>.md`; критику `SPEC:` — ТЗ шага `<id>.spec.md`, без `CHECKLIST:` (порции он читает по ссылкам). Критику вместо `CARD:` — `REVIEW: <abs path к <id>.review-<критик>.md>`. Судье после находок линзы — ещё `LENS: <abs path к <id>.lens-<X>.md>`. В `low` и `xlow` ТЗ нет — вместо `SPEC:`/`CHECKLIST:` исполнителю и судье: `CHECK: acceptance карточки; нет его — тесты затронутых модулей, полный набор не запускать`.

`WORKTREE:` — только исполнителю, линзе и судье. Критик работает в **основном дереве** (worktree создаётся на шаге 3, после критики): строка `WORKTREE:` в его задание не идёт, `--cwd` — корень репозитория.

## Запуск

Локальный субагент — `Agent listik-pipeline:<роль>-<усилие>`. Повтор после `FIXES` и ответ человека — та же сессия (`SendMessage` по agent id).

**Критики — у писателя** (`writer.md`, «Как звать критиков»): локальный — `Agent listik-pipeline:critic-<усилие>` с `model`, pi — скил `pi:pi-delegate --channel <glm|deepseek> --thinking <effort> --cwd <корень> --label "<id> critic r<N>"` с заданием следом. Скил работает в fork-контексте, сам ждёт pi и возвращает ответ — писателю он и нужен целиком.

**Остальные внешние роли — у оркестратора**, командой скрипта моста, фоном, не скилом: скилы `*-delegate`/`*-jobs` возвращают ответ целиком в контекст, а оркестратору нужно только слово, ответ — в файл, не читая. Скрипт — `<installPath>/scripts/…`, `installPath` — из `~/.claude/plugins/installed_plugins.json` по ключу `pi@listik`, `devin@listik`, `codex@listik`, `grok@grok-build` (версию в путь не зашиваешь, в кэше их бывает несколько):

```bash
python3 -c 'import json,os,sys;print(json.load(open(os.path.expanduser("~/.claude/plugins/installed_plugins.json")))["plugins"][sys.argv[1]][0]["installPath"])' pi@listik
```

Задание — в stdin (heredoc). `run --background` печатает job-id; ответ — `result <job-id> --wait <сек>`, годен только код 0 (при таймауте или отмене `result` печатает обрубок с ненулевым кодом). Линзе — `result` прямо в файл (stdout `result` — ровно ответ), в контекст не читаешь. Продолжение после `FIXES` — только у исполнителя-девина, той же сессией: `resume lp-<id>-<X>-impl`. Критик, линза и судья — всегда свежим заходом.

```bash
# pi — линза (bash) или судья (write), если так в раскладке
pi-run.sh run --background --permission <bash|write> --channel <glm|deepseek> --thinking <effort> --cwd <wt> --label "<id> <роль>" <<'EOF'
<задание>
EOF
pi-run.sh result <job-id> --wait <сек> > <id>.lens-<X>.md   # судья — > <id>.judge-<X>.r<N>.out.md

# devin — код (write = режим dangerous); после FIXES — resume той же сессии
devin-run.sh run --background --thinking <high|max> --permission write --session lp-<id>-<X>-impl --cwd <wt> --label "<id> исполнитель" <<'EOF' … EOF
devin-run.sh resume lp-<id>-<X>-impl --background --thinking <high|max> --permission write --cwd <wt> <<'EOF' … EOF
devin-run.sh result <job-id> --wait <сек>

# codex — судья, если так в раскладке (write: в read-only у песочницы нет сети до Listik; без --model/--effort — настройки ~/.codex/config.toml)
codex-run.sh run --background --permission write --cwd <wt> --label "<id> судья" <<'EOF' … EOF
codex-run.sh result <job-id> --wait <сек>

# grok — судья: прямой node, пока нет моста (listik-g6xh); все три вызова — из одного каталога
node <installPath>/scripts/grok-companion.mjs task --background --write --no-web --fresh --model <model> --effort <effort> "TASK_FILE: <абс путь>"
node <installPath>/scripts/grok-companion.mjs status <job-id> --json   # job.status: running → completed | failed | cancelled
node <installPath>/scripts/grok-companion.mjs result <job-id> > <id>.judge-<X>.r<N>.out.md
```

grok: модель и усилие — из раскладки. `--write` обязателен: без него судья пойдёт на чтение, и ни проверка, ни вердикт в карточку не пройдут. Задание судье — файлом
`<id>.judge-<X>.r<N>.task.md`, в команду после флагов одна строка `TASK_FILE: <абс путь>`
(текст задания в командную строку не вставлять). Опрос `status --json` — до терминального `completed`/`failed`/`cancelled`, забор `result` — из того же каталога, что запуск. `result` grok склеивает ход работы с ответом (до listik-g6xh): слово ответа там не первой строкой, поэтому исход судьи grok читаешь из карточки — последний `verdict`, первая строка `VERDICT: PASS` или `VERDICT: FAIL`; вердикта нет — сбой роли (`statuses.md`).

## Готовность канала

`pi-run.sh check --probe-timeout 30`, `devin-run.sh check`, `codex-run.sh check` — до выдачи писателя, по всей строке режима, включая критиков (их зовёт писатель, но канал проверяешь ты), годен код 0; у pi ещё канал роли — `ok` (код 0 значит, что жив хоть один канал). Судья codex — ещё `[sandbox_workspace_write] network_access = true` в `~/.codex/config.toml`: без сети claim и вердикт не дойдут до Listik. grok — `grok-companion.mjs setup --json` + короткий
прогон: `task --no-web --model grok-4.7 --effort low "Reply with exactly one word: OK"`. Не
готов — стоп-фактор: журнал `STOP: <role> — <harness> unavailable: <причина>`, `needs-owner`, тот же
вопрос в чат. Роль не подменяется другим харнессом, моделью ниже или локальным субагентом.

## Грабли

- **Локальный субагент в фоне просит разрешение у человека на каждый `Write`/`Edit`/`Bash`**, а в фоне отвечать некому: плагинные субагенты не принимают `permissionMode` (frontmatter игнорируется). Для этого у плагина есть хук `hooks/approve-agents.py` (`PermissionRequest`): включи настройку плагина `auto_approve_agents` — правки в проекте и worktree и неопасный Bash ролей пройдут без запроса. Выключена — зови локальные роли на переднем плане, иначе исполнитель встанет на первой же правке.
- **`${CLAUDE_PLUGIN_ROOT}` живёт только в теле скилов и агентов.** В файлах, прочитанных Read-ом, и в Bash-окружении его нет: путь берётся из скила режима и уходит в задание готовой строкой.
- **Вердикт — только на `s4-judge`,** иначе Listik молча сохранит его как обычный комментарий (`verdict_accepted: false`).
- **Красный вердикт оставляет держателя-судью:** без `listik release <id>` исполнитель не возьмёт карточку и будет ждать 24 ч до истечения окна возврата.

## Окна ожидания

Критика — у писателя: `pi:pi-delegate` ждёт pi сам, pi glm тратит на рецензию около 8 минут — раньше его молчание не сбой. Судья grok — 3 часа (не мост, предела 7200 с у него нет), судья codex и исполнитель — 2 часа: предел фоновых
скриптов мостов 7200 с, окно больше него не живёт (или передавай `--timeout` в `run`). Исчерпано — `failed`/`timeout`/`canceled`/`cancelled`/`orphaned`/пустой ответ — не
`FIXES`: вердикта не было. Журнал, `<харнесс>-run.sh check`, `needs-owner`, следующий шаг не выбираешь.

## Listik

Оркестратор выдаёт карточку: `stage <id> --to <этап> --holder <HARNESS> --actor agent:claude
--harness claude`, где `<HARNESS>` — то же имя, что в задании (локальная роль — `claude-<модель>`). Роль, которой выдана карточка (писатель, исполнитель, судья), ведёт её сама: `claim <id> --holder <HARNESS> --actor
agent:<HARNESS> --harness <HARNESS до «-»>`, heartbeat, журнал, вердикт — и внешний харнесс, и локальный
субагент (у него есть Bash). Критик и линза карточку не трогают. Коммит и `done` — всегда оркестратор. Полный протокол — `AGENTS.md`
проекта: вопросы человеку, `--actor`, `release`, повторная выдача.
