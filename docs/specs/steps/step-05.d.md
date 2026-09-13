# Порция 05.d. Панель задачи по TaskPanel: процесс, холодный старт, держатель, журнал, связи

## Контекст

Дерево `/Users/dmitry.fomin/Projects/Listik-ui`, правится только `web/*`. Закоммичены порции a–c:
примитивы `components/marks/*`, `lib/{health,harness,projects,stages}.ts`, инбокс с кнопкой
«Ответить» (сейчас она открывает панель и зовёт `drawerRef.focusComment()`), `store.answerQuestion(id,
text)` (= `POST …/needs-owner {value:false, note}` — сервер пишет комментарий `kind=answer` и снимает
флаг), компактная карточка без кнопок (все действия — в панели).

Референс — `docs/prototype/TaskPanel-html/TaskPanel.dc.html`, блок `.drawer` (ширина 720px):
шапка (`.drawer__header`: строка «тип · метка проекта · Symfony · порция 3/5 шага «…»», заголовок
`h2`, строка пилюль «здорова · hb 4 мин» (`pill--healthy`), «в работе» (`pill--info`), иконка
приоритета + «критичный», бейдж `info` «трек billing» (метка), бейдж `accent` «её ждут 2», справа
моноширинный id с иконкой копирования); тело (`.drawer__body`, секции с промежутком `--space-8`):
«Где стоит процесс» (h4 + подсказка «на s3 1.3 ч · порог 8 ч», степпер из 5 шагов с подписями
«claude · 34 мин», «grok · 38 мин · 3 замечания», «dsh · 1.3 ч · сейчас», «та же сессия», «коммит
судьи» и метками переходов sticky/handoff; ряд кнопок «Heartbeat · Нужен автор · Следующий этап ·
Освободить · … · Взять в работу (disabled)» и подсказка «взять нельзя: держит dsh, переход s3 → s4
sticky — приёмка идёт в той же сессии»); «Холодный старт» (h4 + бейдж «6 из 7» + подсказка «что
увидит принимающий по `listik show`», таблица `.cold` из строк «ок/предупреждение · ключ · значение»:
spec_path, acceptance, journal_path, worktree · branch, blocks, comment -k journal, comment -k review);
«Кто держит» (`dl`: держит, heartbeat, что делает, этап с); «Журнал и вердикты» (h4 + бейдж-счётчик +
сегмент «всё / журнал / ревью / вердикт», лента `.tl`, строка ввода «select журнал · input · Отправить»);
«Связи» (h4 + бейдж «её ждут 2» + кнопка «Дерево связей», сетка 2×N карточек `.dep` с id, названием,
бейджами `blocks`/`s3` и подсказкой «держит claude», строка «родитель: <id> …»).

Сейчас `components/TaskDrawer.vue` — `UiDrawer size="lg"` (правило ширины 720px в `app.css`),
заголовок-id, пилюля статуса и бейджи, «Где стоит процесс» (`UiSteps` по `PIPELINE_STAGE_KEYS` +
кнопка «Следующий этап» + `UiSelect` прямой установки этапа через PATCH + поле заметки), длинный
`dl` «Кто держит» (11 строк), вердикт по зависимостям и кнопки (Взять / Взять всё равно / Heartbeat /
Нужен автор / Освободить), инлайн-выбор держателя, «Закрыть с результатом», тексты описания /
критериев / дизайна / заметок / результата, «Комментарии и журнал» (`UiSelect` вида + `UiTextarea` +
`UiTimeline` объединённой ленты), «События задачи» (сырой список), «Зависимости» (ввод id блокера,
дерево, списки), «Быстрые переключатели» (`UiPopover`, `UiSwitch`), `UiConfirmDialog` для force.
Emits: `update:modelValue, reload, patch, claim, heartbeat, stage, needsOwner, release, done,
comment, dep, open-other`; `defineExpose({ focusComment })`. Обработчики и тосты — в `App.vue`
(`onPatch … onDep`). Данные: `TaskDetail` (`comments[]`, `events[]` с `kind, from_value, to_value,
actor, harness, note, duration_s`, `deps_state` с `ready/claimable/can_finish/verdict/reasons/
blocked_by/waiting_for/children_open/parent/soft_links/holder*`), пути `spec_path`, `journal_path`,
`worktree/branch`. **Сервер** отдаёт ещё `checklist_path`, `review_path`, `decision_path` и
`documents[]` (`id, kind, path, revision, content_hash, title, updated_at, status, error,
chunk_count` — см. `API.md`, «Модель задачи»), но в клиентских типах `web/src/api/types.ts` их
**нет** — их надо добавить (см. §0), иначе `vue-tsc` не пропустит обращение к ним.

Факты сервера (проверены по `listik/store.py`, `server.py`):
- событие `stage` при `POST …/stage` содержит `note` вида `этап -> s3-impl (handoff)` (тип
  перехода — из конфигурации проекта) и `duration_s` — длительность **покинутого** этапа; событие
  `stage` при создании с этапом — `from_value: null`. Вход в этап X = событие `stage` с
  `to_value = X`, длительность X = `duration_s` следующего события `stage` с `from_value = X`;
- **у события `stage` актора нет**: `next_stage` зовёт `update_task` без `actor`, поэтому
  `event.actor` там всегда `null`. Кто работал на этапе, видно по событиям `claim` (`to_value` =
  ключ держателя) и `heartbeat` (`to_value` = ключ держателя), попавшим во временное окно этапа
  (между событием входа и событием выхода); у события `created` актор есть (`actor`);
- `claim` занятой **другим** держателем задачи отвечает 400 «уже удерживается …; сначала release»
  — и `force` этого не обходит; `force` обходит только незакрытые блокеры. Для текущего держателя
  `claim` идемпотентен. Значит «взять можно» = `deps_state.ready` (нет блокеров, нет держателя, не
  закрыта), «взять всё равно» имеет смысл только при `blocked_by.length > 0` и пустом держателе;
- типы переходов по умолчанию — `lib/stages.ts TRANSITIONS`; порог «дольше порога» =
  `task.stage_warn` (8 ч на сервере, наружу число не отдаётся).

Прочитать перед началом: `TaskDrawer.vue` целиком, `App.vue` (обработчики панели), `store/listik.ts`
(`act`, `answerQuestion`, `setNeedsOwner`, `loadDepTree`), `api/types.ts` (`TaskDetail`, `DepsState`,
`DepInfo`, `TaskEvent`), `lib/format.ts`, `lib/stages.ts`, `lib/health.ts`, `marks/*`; в ките —
`UiDrawer` (слоты `header`/default/`footer`), `UiSteps` (`items[{label, description, status}]`,
`currentIndex`), `UiSplitButton` (`items[{key,label,danger?,disabled?}]`, `click`, `select`),
`UiTimeline` (`items[{id,title,description,timestamp,datetime,meta,tone}]`, `dense`), `UiSegmented`,
`UiStatusPill`, `UiBadge`, `UiCopyButton`, `UiConfirmDialog`, `UiEmptyState`, `UiInput`, `UiTextarea`,
`UiSelect`, `UiTooltip`.

## Что сделать

### 0. `web/src/api/types.ts` — точечное расширение типов

В интерфейс `Task` добавить необязательные поля `checklist_path?: string | null`,
`review_path?: string | null`, `decision_path?: string | null`; новый интерфейс `TaskDocument`
(`id: number; kind: 'spec' | 'checklist' | 'review' | 'decision' | string; path: string;
revision: number; content_hash: string | null; title: string | null; updated_at: string | null;
status: 'ok' | 'missing' | string; error: string | null; chunk_count: number`) и в `TaskDetail` —
`documents?: TaskDocument[]`. Только добавление опциональных полей — существующие поля, `client.ts`
и мок не трогать (мок этих полей не отдаёт, поэтому всё, что на них построено, обязано работать
при `undefined`).

### 1. Шапка (`#header` дровера)

Три строки в `.listik-drawer__head`:
1. `TaskGlyph type` · `ProjectMark withTitle` (проект из `store.meta.projects` — прокинуть prop
   `projects`) · `.listik-section__hint` с обрезкой: `· порция шага «{deps_state.parent.title}»`,
   если `parent` есть; иначе ничего;
2. `h2.listik-drawer__title` — `task.title` (`--text-lg`, semibold, `--tracking-tight`);
3. ряд: `UiStatusPill :tone="taskHealth(task)"` с текстом по правилу **без дубля** (собирается в
   `TaskDrawer.vue` функцией `healthPillText(task)`; `lib/health.ts` не меняется): закрытая
   (`status` done/cancelled) → «закрыта»; `dead` и `unknown` → только `healthReason(task)`
   («брошена 40 ч», «брошена · без держателя», «без держателя»); `healthy` и `at-risk` →
   `{HEALTH_TITLES[health]} · {healthReason(task)}` («здорова · hb 4 мин», «под угрозой · молчит
   18 мин», «под угрозой · на этапе дольше порога»); `UiStatusPill` статуса (тон как сейчас в
   `statusTone`, текст `status_title`); `TaskGlyph priority` + `.listik-section__hint` со словом
   приоритета (`PRIORITY_TITLES`); `UiBadge info sm` на каждую метку `labels`; `UiBadge accent sm`
   «её ждут N» при `waiting_for.length`; `UiBadge warning sm` «ждёт N» при `blocked_by.length`;
   `UiBadge accent sm` «нужен ты» при `needs_owner`; справа (`margin-left: auto`) моноширинный id
   + `UiCopyButton` (как сейчас).
`title` дровера (`:title`) оставить равным id для `aria`.

### 2. «Где стоит процесс»

- Заголовок `h4` + справа `.listik-section__hint.tnum`: «на {stageCode} {stage_age}» + « · дольше
  порога» при `stage_warn`; для задачи без этапа — «вне конвейера».
- `UiSteps` из пяти шагов `{label: 's1 · ТЗ и чек-лист', …, 'done'}`; `currentIndex =
  stageIndex(task.stage)` (для `stage = 'done'` — 4 с `status: 'done'` у всех); для `stage = null`
  у всех `status: 'upcoming'`. `description`:
  - пройденный этап X: `{кто работал} · {formatDuration(duration X)} · {transitionOut(X)} →`.
    «Кто работал» — **не** актор события `stage` (его нет, см. «Факты сервера»), а ключ из
    `to_value` последнего события `claim` или `heartbeat`, чей `ts` лежит в окне этапа (от `ts`
    события входа в X до `ts` события выхода из X включительно; нет события входа — окно открыто
    слева, нет события выхода — открыто справа; нет **ни того, ни другого** — окна нет и «кто
    работал» = «—», чтобы не приписать этапу чужой `claim`); если в окне X таких нет, а
    переход **в** X был sticky (держатель остался) — наследуется «кто работал» предыдущего этапа;
    для s1 при отсутствии таких — `actor` события `created`; если ничего нет — «—». Ключ
    показывается коротко: `agent:dsh` →
    «dsh», `dsh/deepseek-flash` → «dsh» (через `harnessOf`, для `human` — `me` → «я», иначе сам
    ключ). Длительность — `duration_s` события выхода; нет события — часть опускается. Событий
    может не быть вовсе (сервер отдаёт последние 100): тогда описание — только `{transitionOut} →`;
  - текущий: `{holder_title || 'без держателя'} · {stage_age} · сейчас`;
  - будущий: «та же сессия», если переход в него sticky, иначе «новый держатель»; `done` —
    «коммит судьи».
- Ряд действий `.listik-row`: `UiButton secondary sm` «Heartbeat» (иконка `clock`, `disabled` без
  держателя-значения); `UiButton secondary sm` «Нужен автор» (иконка `warning`; при `needs_owner`
  — «Снять «нужен автор»» с иконкой `check`); `UiButton secondary sm` «Следующий этап» (иконка
  `bolt`, `disabled` при `stage === 'done'`); `UiButton ghost sm` «Освободить» (`disabled` без
  держателя); spacer; `UiSplitButton variant="primary" size="sm"` «Взять в работу» с иконкой `hand`.
  **Проп `disabled` кнопке не передавать никогда**: в ките `disabled` гасит и стрелку, и меню
  (`isBlocked` уходит в `UiPopover`), а меню нужно как раз тогда, когда главное действие
  недоступно; `loading` — только при `pending === 'claim'`. Доступность главного действия
  выражается поведением: `canClaim = deps?.ready === true` (серверная семантика: нет блокеров,
  нет держателя, не закрыта); при `canClaim` клик раскрывает строку выбора держателя +
  «Подтвердить» (как сейчас); при `!canClaim` клик ничего не запускает, а раскрывает под рядом
  `UiAlert tone="warning"` «Взять нельзя» с `deps.reasons` списком (повторный клик сворачивает);
  на обёртке кнопки — `data-can-claim="true|false"` для проверок. `items`:
  `{key:'force', label:'Взять всё равно (обход блокеров)', danger:true,
  disabled: !(blocked_by.length > 0 && !deps.holder)}` — обход возможен только для заблокированной
  **свободной** задачи (занятую другим сервер не отдаёт и с `force`);
  `{key:'done', label:'Закрыть с результатом…', disabled: !can_finish}`;
  `{key:'dep', label:'Добавить связь…'}`. `@select` — `force` → `UiConfirmDialog` (как сейчас),
  `done` → показать строку `UiTextarea` + «Закрыть с результатом», `dep` → строку `UiInput` id +
  «Добавить связь».
- Под рядом `.listik-section__hint` (всегда, независимо от алерта): если `deps` есть и
  `!deps.ready` — «взять нельзя: {reasons через «; »}» + при непустом `deps.holder` и sticky-переходе
  из текущего этапа: «, переход {code} → {next code} sticky — следующий этап идёт в той же
  сессии»; если `deps.ready` — «можно брать прямо сейчас»; при `!can_finish` — отдельный
  `UiBadge warning` «закрывать нельзя: открыты дети» рядом. Сравнивать `deps.holder` с локальным
  `holder` бессмысленно — он синхронизируется с `task.holder`; «держит другой» — это просто
  непустой `deps.holder` при `!deps.ready`, и его имя уже есть в `reasons`.
- «Нужен автор» открывает строку `UiTextarea rows=2` «вопрос автору» + `UiButton primary sm`
  «Отправить вопрос» → `emit('needsOwner', {id, value:true, note})`; «Снять» — без текста
  (`{value:false}`).
- Удалить: `UiSelect` прямой установки этапа (PATCH `stage`), поле «заметка к переходу»,
  «Быстрые переключатели» (`UiPopover`, `UiSwitch`), отдельную кнопку «Взять всё равно» (в меню),
  `UiAlert` «Блокеры стоят без движения» (переезжает в «Связи», п. 6).

### 3. «Холодный старт»

`h4` «Холодный старт» + `UiBadge` «{ok} из 7» (`success`, если `ok === 7`, иначе `warning`) +
`.listik-section__hint` «что увидит принимающий по `listik show`». Таблица `.listik-cold` из семи
строк `.listik-cold__row` (grid `20px 132px 1fr`, `--text-sm`, разделители `--hairline`): значок
(`ListikIcon check xs` в кружке `--success-50/--success-700` или `warning xs` в
`--warning-50/--warning-700`), ключ (`--ink-3`), значение (моноширинно `--text-xs`, обрезка;
предупреждение — обычным шрифтом `--warning-700`):

| ключ | ок, если | значение / предупреждение |
| --- | --- | --- |
| `spec_path` | путь непустой | путь / «ТЗ не привязано» |
| `acceptance` | `checklist_path` (поле из §0, может быть `undefined`) или непустой `acceptance` | путь или первая строка текста / «чек-листа нет» |
| `journal_path` | `decision_path ?? journal_path` непустой | путь / «журнала нет» |
| `worktree · branch` | `worktree` непустой | `{worktree} · {branch}` / «рабочее дерево не указано» |
| `blocks` | всегда ок при наличии `deps` | «ничего не ждёт» или «ждёт {ids}»; + « · её ждут {ids}» |
| `comment -k journal` | есть комментарий `journal` | первые 80 символов последнего / «журнальных записей нет» |
| `comment -k review` | `review_path` (из §0) или комментарий `review` | путь или «N замечаний» (число review-комментариев) / «ревью нет» |

Правило «ок» — по непустому пути/тексту, **не** по `documents[].status` (осознанный размен:
мок `documents[]` не отдаёт, а проверка «файл на диске читается» — забота сервера и `show`);
`documents[]` в этой порции не используется, тип заведён в §0 на будущее и для честности
контракта.

Значение пути кликом копируется (`UiCopyButton` с иконкой `dot`, как у id).

### 4. «Кто держит»

`dl.listik-dl` из строк: «держит» — `HarnessIcon` + `holder_title` + «· {holder_age}» (или
«никто»); «heartbeat» — `formatDateTime(holder_at)` + «· {humanAge(holder_at)} назад» (или «—»);
«что делает» — `holder_note` в «кавычках» (или «—»); «этап с» — `formatDateTime(stage_at)` + «·
{stage_age}»; «исполнитель» — только если `assignee` задан и отличается от `holder`. Остальные
строки прежнего `dl` (обновлена/начата/закрыта/источник/worktree/spec) убрать — worktree и пути
живут в «Холодном старте», даты закрытия — в ленте.

### 5. «Журнал и вердикты»

- `h4` + `UiBadge neutral sm` с числом элементов текущего фильтра; справа `UiSegmented size="sm"`
  `всё / журнал / ревью / вердикт`.
- Лента `UiTimeline dense` из объединения (как сейчас) с фильтром: «всё» — все комментарии +
  события `stage, claim, release, heartbeat, question, answer, done, created, document_error,
  document_restored`; «журнал» — комментарии `journal` + события `stage`; «ревью» — комментарии
  `review`; «вердикт» — комментарии `verdict`. Заголовки: комментарий — `{commentKindTitle} · {автор}`,
  событие `stage` — `stage {from ?? '—'} → {to} · {sticky|handoff из note, если есть}`, `heartbeat`
  — `heartbeat · {actor}` с `holder_note`/`note` описанием, `claim` — `взял в работу · {actor}`,
  `release` — `освободил · {actor}`, остальные — `eventKindTitle`. `meta` — `{actor} · {harness}` ·
  для комментариев `comment -k {kind}`. Тона: `verdict` — `accent`, а если текст начинается с
  «красн»/«red»/«fail» — `danger`; `review` — `info`; `question` — `warning`; `answer` — `success`;
  `release` — `warning`; `stage` — `accent` при handoff, `neutral` иначе. Сортировка — новые сверху.
  Пусто — `UiEmptyState compact` «Записей нет».
- Строка ввода: `UiSelect size="sm"` вида (`журнал` по умолчанию; варианты `journal, comment,
  question, answer, review, verdict` с подписями `commentKindTitle`), `UiInput size="sm"`
  (placeholder «строка журнала или комментарий», `Enter` отправляет), `UiButton primary sm`
  «Отправить». Отправка: вид `answer` → `emit('needsOwner', {id, value:false, note:text})`
  (в `App.vue` → `store.answerQuestion`); вид `question` → `emit('needsOwner', {id, value:true,
  note:text})`; остальные — `emit('comment', …)` как сейчас. `defineExpose({ focusComment,
  focusAnswer })`: `focusAnswer` ставит вид `answer` и фокус в поле; `App.vue` для кнопки
  «Ответить» инбокса зовёт `focusAnswer` (в `openTaskWithComment` — параметр режима).
- Секцию «События задачи» удалить (события — в ленте).

### 6. «Связи»

- `h4` «Связи» + `UiBadge warning sm` «ждёт N» и/или `UiBadge accent sm` «её ждут N»; справа
  `UiButton ghost sm` «Дерево связей» (иконка `timeline`, как сейчас).
- Сетка `.listik-dep-grid` (2 колонки при ширине панели 720, 1 — ниже 640) из карточек
  `.listik-dep` для `blocked_by` (сначала), `waiting_for`, `children_open`: строка id
  (`button.listik-link.listik-mono` → `emit('open-other', id)`) + название; строка бейджей —
  `UiBadge neutral sm` `dep_title || dep_type`, `UiBadge info sm` `stageCode(dep.stage)` (если есть),
  `UiBadge danger sm` «задача не найдена» при `missing`; `.listik-section__hint`: «держит
  {holder_title} · {holder_age}» или «без держателя» + « · стоит без движения» при `stale`.
  Группу `blocked_by` предваряет `h5.listik-subtitle` «Ждёт завершения», `waiting_for` — «Ждут
  её завершения», `children_open` — «Незакрытые дети».
- Если все блокеры `missing || stale || !holder` — `UiAlert warning` «Блокеры стоят без движения:
  возьмите блокер сами или поставьте ему «нужен автор»» (как сейчас, но здесь).
- Ниже — «родитель: <id> {title}» и «связано, не блокирует: <ids>» (как сейчас); при отсутствии
  `deps_state` — `UiAlert info` (как сейчас) и сырые `dependencies/dependents`.
- Дерево по кнопке — как сейчас. Форма «Добавить связь» показывается из меню `UiSplitButton`
  (п. 2), не постоянно.

### 7. «Описание и критерии»

`h4` «Описание · ТЗ» + `.listik-prose` (`description` или «—»), `h4` «Критерии приёмки» + текст;
«Дизайн», «Заметки», «Результат» — только если непустые. Секция стоит последней.

### 8. `App.vue`

- `onNeedsOwner`: при `value:false` с непустым `note` звать `store.answerQuestion(id, note)` и тост
  «Ответ отправлен»; при `value:true` с `note` — тост «Вопрос автору записан»; без `note` — как
  сейчас.
- `openTaskWithComment(task, mode: 'comment' | 'answer')` → после открытия `drawerRef.focusAnswer()`
  или `focusComment()`; инбокс «Ответить» → `'answer'`.
- Прокинуть `projects` в `TaskDrawer`.

### 9. Стили и smoke

- `app.css`: раздел «Панель задачи»: `.listik-drawer__head`, `.listik-cold*`, `.listik-dep-grid`,
  промежуток между секциями панели `--space-8` (`.listik-drawer__body` как `listik-stack` с большим
  gap). Мобильные правила скрытия (`.listik-drawer__process`, `__close-action`, `__unsafe-*`) —
  оставить рабочими: классы повесить на новые блоки (ряд действий процесса, форма закрытия,
  пункты force/release).
- `scripts/smoke.mjs` `report.drawer`: `шагов === 5`, есть тексты «Где стоит процесс», «Холодный
  старт», «Кто держит», «Журнал и вердикты», «Связи»; `coldRows` = число `.listik-cold__row` (7);
  `splitButton` = есть `.ui-split-button`.
- `web/README.md`: пункт «панель задачи» в «Слое зависимостей» дополнить меню `UiSplitButton`
  и «Холодным стартом»; в «Особенностях» — что ответ автору из панели снимает флаг через
  `needs-owner`.

## Границы правки

- Правятся: `components/TaskDrawer.vue`, `App.vue` (обработчики панели, `openTaskWithComment`,
  проброс `projects`), `api/types.ts` (**только** добавление опциональных полей из §0),
  `assets/app.css` (раздел панели), `lib/format.ts` (новые подписи, если нужны; существующие
  сигнатуры не менять), `scripts/smoke.mjs`, `web/README.md`.
- Не трогать: доску (`BoardView`, `BoardColumn`, `TaskCard`), инбокс/тулбар (кроме вызова
  `focusAnswer`), `store/listik.ts` (всё нужное уже есть: `answerQuestion`, `setNeedsOwner`,
  `addComment`, `loadDepTree`, `claimTask`, `heartbeatTask`, `nextStage`, `releaseTask`, `doneTask`,
  `addDependency`), `api/client.ts`, `lib/health.ts` (подпись пилюли собирается в панели),
  примитивы, `MobileTaskList.vue`, `scripts/mock-api.mjs`, окно «Новая задача» (порция e),
  `sheet/*`, кит в `node_modules` (`UiSplitButton` не патчить).
- Не возвращать прямую установку этапа через PATCH (`stage` меняется только `POST …/stage`);
  не заводить новых `kind` комментариев; не показывать «красный ×N» и «порция N/M» (данных нет).
- Ширина панели 720px — существующим правилом в `app.css`; кит не патчить.
- Никаких токенов и содержимого `config.toml` в файлах и отчёте.

## Как проверить

```sh
cd /Users/dmitry.fomin/Projects/Listik-ui/web
npm run typecheck && npm run build
node scripts/mock-api.mjs 8788 &
VITE_API_BASE=http://127.0.0.1:8788 VITE_LISTIK_TOKEN=mock-token npm run dev &
npm run smoke -- http://localhost:5173/ 1440   # drawer.шагов 5, coldRows 7, splitButton true
```

Живой сервер на временной базе (сценарий для ручной проверки):
```sh
cd /Users/dmitry.fomin/Projects/Listik-ui
export LISTIK_DB=/tmp/listik-step05d.db && ./bin/listik --local init
ID=$(./bin/listik --local new "Проба панели" -p demo --stage s1-spec --spec docs/specs/steps/step-05.md --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
./bin/listik --local claim $ID --holder agent:claude && ./bin/listik --local stage $ID   # s1→s2 sticky
./bin/listik --local stage $ID                                                            # s2→s3 handoff, держатель снят
./bin/listik --local comment $ID "VO должен быть immutable" -k review
./bin/listik --local needs-owner $ID "снапшот прода или фикстуры?"
LISTIK_DB=$LISTIK_DB ./bin/listik serve --port 8797   # и VITE_API_BASE=http://127.0.0.1:8797 npm run dev
```
В панели: степпер s1 done «claude · … · sticky →» (claude — из события `claim` в окне s1), s2
done «claude · … · handoff →» (в окне s2 нет ни `claim`, ни `heartbeat`, но переход в s2 был
sticky — «кто работал» унаследован от s1), s3 текущий «без держателя · … · сейчас»; «Холодный старт» — `spec_path` ок, `comment -k review` «1 замечаний», `worktree`
предупреждение; журнал «ревью» — одна запись; отправка вида «ответ» снимает флаг (карточка уходит
из инбокса) и в ленте появляется запись «ответ · …».
