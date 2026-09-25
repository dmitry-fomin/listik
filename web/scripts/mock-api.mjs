/**
 * Фейковый API Listik для разработки фронтенда без сервера (и в тестах).
 * Запуск: node scripts/mock-api.mjs [port] [--fill=N] [--links] [--cold] [--slow-ms=N]
 *   → порт по умолчанию 8788.
 * Затем: VITE_API_BASE=http://127.0.0.1:8788 npm run dev
 * `--fill=N` (в любом месте после порта) добавляет N сгенерированных задач
 * поверх пяти базовых — без флага поведение мока не меняется.
 * `--links` добавляет срез «Связи» с краевыми случаями (закрытая связь, удалённая
 * задача, чужой проект, id в другом регистре, взаимные ссылки); `--slow-ms=N`
 * задерживает отдачу карточки `listik-links-slow` — так воспроизводится гонка
 * двух открытий задачи (scripts/verify-deps-links.mjs).
 * `--routes` добавляет `GET /api/routes` и карточку `listik-routes-error` с
 * отказавшим автостартом (`launch_error`, флаг «нужен человек», метки маршрута) —
 * так проверяется пункт «без маршрута» в панели задачи
 * (scripts/verify-route-clear.mjs). В том же режиме живёт карточка
 * `listik-executor`: держит её `claude`, а этап s3-impl по маршруту делает
 * роль `impl` — по ней проверяется подпись «делает …»
 * (scripts/verify-card-executor.mjs).
 * `--assistant` добавляет `GET /api/assistant/status` (`enabled`) и
 * `POST /api/assistant/suggest`; формы — `AssistantStatus` и
 * `AssistantSuggestResponse` из `web/src/api/types.ts`. Ответ подбирается по
 * тексту поля `text` из тела: `full` — переписанный текст, два критерия,
 * сложность с причиной и видимый маршрут из `--routes`; `same` — текст равен
 * входному; `blocked` — маршрут не из `routes.json`; `noroute` — `route: null`;
 * `noacc` — пустой список критериев; `error` — запросы чередуются: нечётный
 * отвечает ошибкой (HTTP 500), чётный — как `full`; любой другой текст — как
 * `full`. Без флага ручек помощника нет, как и раньше
 * (scripts/verify-assistant-apply.mjs).
 * `--voice` добавляет голосовой ввод: `GET /api/assistant/status` отдаёт
 * `voice: true` (без флага поля `voice` нет вовсе), `POST /api/assistant/transcribe`
 * (`{audio_base64, mime}`) и `POST /api/assistant/draft` (`{text}`). Мок декодирует
 * base64 в строку-маркер сценария: транскрипт — пустой при `silence`, HTTP 502 при
 * `transcribe-fail`, иначе непустой и содержит маркер; черновик — `project: null`
 * при `noproject`, `project: 'ghost-project'` (нет в `/api/meta`) при `badproject`,
 * `title: null` при `notitle`, HTTP 502 при `draft-fail`, иначе полный
 * (`project: 'listik'`, `type: 'task'`, видимый маршрут `low-pipeline`).
 * Только под `--voice` ручки голоса и живут.
 * `--cold` добавляет четыре задачи под строку «worktree · branch» блока
 * «Холодный старт»: отдельное дерево, работа в `main`, она же в `master`, и
 * карточка совсем без дерева и ветки (scripts/verify-cold-start.mjs).
 * `--hint` добавляет карточку `listik-hint-hub` со ссылками на шесть задач, на
 * которых проверяется строка «закрывать нельзя: открыты дети» в панели задачи
 * (scripts/verify-children-hint.mjs).
 * `--epic` добавляет эпик `listik-epic-hub` (в работе, без этапа) и четыре его
 * подзадачи: открыта (s1-spec), в работе (s3-impl), готова (этап `done`) и
 * отменена (без этапа). Только в этом режиме `GET /api/tasks/listik-epic-hub`
 * отдаёт поле `children` — все дети в форме `store.child_cards`, включая
 * закрытых; так проверяется статус подзадачи в дереве панели
 * (scripts/verify-epic.mjs).
 * `--markdown` добавляет карточку `listik-markdown-case`, у которой поле
 * `description` — полный набор markdown-конструкций (заголовок `##`,
 * маркированный и нумерованный списки, инлайн-код, блок кода в тройных
 * кавычках, ссылка и строка с HTML-разметкой), а остальные текстовые поля
 * пусты. По ней проверяется вывод markdown в панели задачи
 * (scripts/verify-markdown.mjs).
 *
 * `--server-mode` включает серверный режим владельцев (шаг listik-xt69):
 * `/api/health` отдаёт `mode: 'server'`, `users: ['ann','bob']` и `owner` —
 * имя из заголовка `X-Listik-Owner`. Сидовые задачи в этом режиме свои: часть
 * у `ann`, часть у `bob`, часть без владельца, все без держателей и блокеров
 * (чтобы не было фоновых heartbeat). Списки (`/api/tasks`, `/api/board`,
 * `/api/ready`) с заголовком отдают «свои + без владельца», без заголовка — все,
 * с незнакомым именем — 400; карточка `GET /api/tasks/{id}` не фильтруется;
 * `claim`/`heartbeat`/`stage` без заголовка — 400, на чужую задачу — 403.
 * В этом режиме мок пишет в stdout строку на каждый запрос (метод, путь,
 * значение заголовка) — по ней scripts/verify-owner.mjs проверяет заголовки.
 * Без флага `/api/health` отдаёт `mode: 'local'`, `users: []`, `owner: null`,
 * а всё остальное — как раньше.
 *
 * Служебные ручки для скриптов проверки (в docs/API.md их нет — это не контракт, а
 * ручки управления моком, как `__token`): `POST /__event` рассылает кадр в
 * открытые `/api/stream` (тело `{kind, payload, patch?, comment?}`, patch/comment
 * сперва меняют заглушку — так проверяется, что доска увидела запись), а
 * `GET /__requests` и `POST /__requests/reset` считают чтения карточек
 * `GET /api/tasks/{id}` (scripts/verify-detail-sse.mjs). `GET /__requests` дополнительно
 * отдаёт `voice: {transcribe, draft, create}` — сколько раз пришли
 * `POST /api/assistant/transcribe`, `POST /api/assistant/draft`, `POST /api/tasks`, —
 * и `last_create` (тело последнего `POST /api/tasks` или `null`); `POST /__requests`
 * обнуляет эти счётчики вместе с прежними.
 *
 * Формы ответов повторяют docs/API.md и listik/store.py 1:1 — это заглушка
 * транспорта, а не второй контракт.
 */
import { createServer } from 'node:http'

const port = Number(process.argv[2] ?? 8788)
const fillArg = process.argv.slice(3).find((arg) => arg.startsWith('--fill='))
const fillCount = fillArg ? Number.parseInt(fillArg.slice('--fill='.length), 10) : 0
const linksMode = process.argv.slice(3).includes('--links')
const routesMode = process.argv.slice(3).includes('--routes')
const assistantMode = process.argv.slice(3).includes('--assistant')
const voiceMode = process.argv.slice(3).includes('--voice')
const coldMode = process.argv.slice(3).includes('--cold')
const hintMode = process.argv.slice(3).includes('--hint')
const epicMode = process.argv.slice(3).includes('--epic')
const markdownMode = process.argv.slice(3).includes('--markdown')
const serverMode = process.argv.slice(3).includes('--server-mode')
/** `server.users` из config.toml — кем можно представиться в серверном режиме. */
const SERVER_USERS = ['ann', 'bob']
const slowArg = process.argv.slice(3).find((arg) => arg.startsWith('--slow-ms='))
const slowMs = slowArg ? Number.parseInt(slowArg.slice('--slow-ms='.length), 10) : 0
const now = Date.now()
const iso = (hoursAgo) => new Date(now - hoursAgo * 3600_000).toISOString()

const STATUS_TITLES = {
  open: 'открыта',
  in_progress: 'в работе',
  blocked: 'заблокирована',
  review: 'на проверке',
  done: 'готова',
  cancelled: 'отменена',
}
const STAGE_TITLES = {
  's1-spec': '1. ТЗ и чек-лист',
  's2-review': '2. Второе мнение',
  's3-impl': '3. Реализация',
  's4-judge': '4. Проверка и коммит',
  done: 'готово',
}
const PRIORITY_TITLES = { 0: 'P0 срочно', 1: 'P1 высокий', 2: 'P2 обычный', 3: 'P3 низкий', 4: 'P4 потом' }

function task(overrides) {
  const base = {
    id: 'listik-web-a1b2',
    project: 'listik',
    title: 'Собрать доску канбан для трекера',
    description: 'Одностраничная админка на Vue 3 + Vite + @zoloto585/facet.',
    acceptance: 'npm run build и vue-tsc --noEmit проходят без ошибок',
    design: 'Колонки — свои, всё остальное — из кита',
    notes: 'DnD на нативных HTML5-событиях',
    result: '',
    status: 'in_progress',
    status_title: 'в работе',
    stage: 's3-impl',
    stage_title: '3. Реализация',
    priority: 2,
    priority_title: 'P2 обычный',
    issue_type: 'feature',
    assignee: 'agent:dsh',
    assignee_title: 'dsh',
    holder: 'agent:dsh',
    holder_title: 'dsh',
    holder_note: 'пишу панель задачи',
    // «Кто выполнял» (claim/heartbeat своей рукой): у большинства задач заглушки
    // никто ничего не подтверждал — поле пустое.
    worked_by: [],
    worked_by_title: '',
    // Владелец задачи: в локальном режиме его нет ни у кого (`null`).
    owner: null,
    holder_at: iso(0.3),
    holder_age: '18 мин',
    holder_hours: 0.3,
    idle_hours: 0.3,
    idle_age: '18 мин',
    stage_at: iso(2),
    stage_age: '2 ч',
    stage_hours: 2,
    stage_warn: false,
    needs_owner: false,
    // Девять колонок запуска (docs/API.md): у базовых задач автостарта нет.
    autostart: false,
    launch_route: null,
    launched_by: null,
    launch_pid: null,
    launched_at: null,
    launch_log: null,
    launch_exit_code: null,
    launch_finished_at: null,
    launch_error: null,
    labels: ['frontend'],
    spec_path: 'docs/listik-board.md',
    journal_path: 'docs/listik-board.journal.md',
    worktree: '/Users/dmitry.fomin/Projects/Listik',
    branch: 'main',
    blocked_by: [],
    parent: null,
    soft_links: [],
    source: 'native',
    external_ref: null,
    created_at: iso(30),
    created_by: 'agent:dsh',
    updated_at: iso(0.3),
    updated_age: '18 мин',
    started_at: iso(28),
    closed_at: null,
    close_reason: null,
    archived: false,
    stale: false,
    abandoned: false,
  }
  return { ...base, ...overrides, route_editable: routeEditableOf({ ...base, ...overrides }) }
}

/**
 * `route_editable` считает сервер (`store.route_change_denied`): маршрут меняют,
 * только пока задача заведена — статус `open`, без этапа, держателя и запуска.
 * Мок повторяет формулу, чтобы панель задачи показывала выбор там же, где сервер.
 */
function routeEditableOf(item) {
  return Boolean(
    item.status === 'open' &&
      !item.stage &&
      !item.holder &&
      !item.launched_by,
  )
}

const tasks = [
  // Открытая карточка с держателем, который взял её сам: `worked_by` непуст, но
  // «кто выполнял» у открытой не показывается — в подвале и в панели «держит».
  task({ worked_by: ['agent:dsh'], worked_by_title: 'DeepSeek Harness' }),
  task({
    id: 'listik-api-c3d4',
    title: 'Отдать needs_you одной лентой',
    status: 'open',
    status_title: 'открыта',
    stage: 's1-spec',
    stage_title: '1. ТЗ и чек-лист',
    holder: null,
    holder_title: '',
    holder_at: null,
    holder_age: '',
    holder_hours: null,
    holder_note: null,
    stage_at: iso(30),
    stage_age: '30 ч',
    stage_hours: 30,
    stage_warn: true,
    needs_owner: true,
    abandoned: true,
    priority: 1,
    priority_title: 'P1 высокий',
    updated_at: iso(30),
    updated_age: '30 ч',
    idle_age: '30 ч',
  }),
  task({
    id: 'listik-sse-e5f6',
    title: 'Дебаунс SSE 500 мс',
    status: 'review',
    status_title: 'на проверке',
    stage: 's4-judge',
    stage_title: '4. Проверка и коммит',
    holder: 'agent:claude',
    holder_title: 'claude',
    holder_at: iso(40),
    holder_age: '40 ч',
    holder_hours: 40,
    idle_hours: 40,
    idle_age: '40 ч',
    stale: true,
    labels: ['sse', 'realtime'],
    priority: 3,
    priority_title: 'P3 низкий',
  }),
  task({
    id: 'listik-epic-k9l0',
    title: 'Эпик: слой зависимостей на доске',
    issue_type: 'epic',
    status: 'open',
    status_title: 'открыта',
    stage: null,
    stage_title: null,
    holder: null,
    holder_title: '',
    holder_at: null,
    holder_age: '',
    needs_owner: false,
    priority: 1,
    priority_title: 'P1 высокий',
    blocked_by: [],
  }),
  task({
    id: 'listik-metrics-g7h8',
    title: 'Метрики по держателям',
    status: 'blocked',
    status_title: 'заблокирована',
    stage: 's2-review',
    stage_title: '2. Второе мнение',
    blocked_by: ['listik-api-c3d4'],
    parent: 'listik-epic-k9l0',
    soft_links: ['listik-sse-e5f6'],
    holder: null,
    holder_title: '',
    holder_at: null,
    holder_age: '',
    needs_owner: false,
    priority: 2,
  }),
]

/**
 * `--server-mode`: свой набор задач — по две на `ann` и `bob` и две без
 * владельца. Все открыты, без держателя и без блокеров: доска не шлёт по ним
 * фоновых heartbeat, и каждая попадает в `ready` — так видно ровно фильтрацию
 * по владельцу, а не гонку с чем-то ещё.
 */
function ownerTask(id, title, owner, priority) {
  return task({
    id,
    title,
    owner,
    priority,
    priority_title: PRIORITY_TITLES[priority],
    status: 'open',
    status_title: 'открыта',
    stage: null,
    stage_title: null,
    holder: null,
    holder_title: '',
    holder_note: null,
    holder_at: null,
    holder_age: '',
    holder_hours: null,
    idle_hours: null,
    idle_age: '',
    stage_warn: false,
    needs_owner: false,
    stale: false,
    abandoned: false,
    blocked_by: [],
    parent: null,
    soft_links: [],
    assignee: null,
    assignee_title: '',
    labels: [],
  })
}

if (serverMode) {
  tasks.length = 0
  tasks.push(
    ownerTask('listik-owner-ann1', 'Ann: первая задача', 'ann', 1),
    ownerTask('listik-owner-ann2', 'Ann: вторая задача', 'ann', 2),
    ownerTask('listik-owner-bob1', 'Bob: первая задача', 'bob', 1),
    ownerTask('listik-owner-bob2', 'Bob: вторая задача', 'bob', 2),
    ownerTask('listik-owner-free1', 'Ничья задача раз', null, 2),
    ownerTask('listik-owner-free2', 'Ничья задача два', null, 3),
  )
}

/** `--fill=N`: N дополнительных задач поверх пяти базовых, для проверки пагинации/сортировки. */
const STAGE_CYCLE = ['s1-spec', 's2-review', 's3-impl', 's4-judge', null]

function fillTask(i) {
  const nnn = String(i).padStart(3, '0')
  const project = i % 2 === 0 ? 'fill' : 'listik'
  const status = i % 2 === 1 ? 'open' : 'in_progress'
  const stage = STAGE_CYCLE[(i - 1) % STAGE_CYCLE.length]
  const holderFields =
    status === 'in_progress'
      ? {
          holder: 'agent:dsh',
          holder_title: 'dsh',
          holder_at: iso(0.1),
          idle_hours: 0.1,
          idle_age: '6 мин',
          holder_note: null,
        }
      : {
          holder: null,
          holder_title: '',
          holder_at: null,
          holder_hours: null,
          idle_hours: null,
        }
  return task({
    id: `listik-fill-${nnn}`,
    title: `Заполнитель ${nnn}: задача №${nnn}`,
    project,
    status,
    status_title: STATUS_TITLES[status],
    stage,
    stage_title: stage ? STAGE_TITLES[stage] : null,
    ...holderFields,
    updated_at: iso(48 + i),
    updated_age: `${48 + i} ч`,
    created_at: iso(200 + i),
    stage_at: iso(47 + i),
    stage_warn: false,
    needs_owner: false,
    stale: false,
    abandoned: false,
    blocked_by: [],
    labels: [],
    assignee: null,
    assignee_title: '',
    spec_path: null,
    journal_path: null,
    worktree: null,
    branch: null,
  })
}

if (fillCount >= 1) {
  for (let i = 1; i <= fillCount; i += 1) tasks.push(fillTask(i))
}

/**
 * `--links`: срез «Связи» с краевыми случаями. Карточка `listik-links-main`
 * ссылается сразу на все из них, чтобы проверить переходы по ссылкам:
 * `listik-links-done` — закрытая связь (в `dependencies`, но не в `blocked_by`),
 * `listik-links-done` вторым типом — тот же id двумя строками,
 * `listik-links-gone` — задача удалена, `other-links-far` — чужой проект,
 * `Listik-Links-Case` — id в другом регистре, `listik-links-slow` — медленный ответ.
 */
const extraDeps = {}
if (linksMode) {
  for (const item of [
    task({
      id: 'listik-links-main',
      title: 'Карточка со связями',
      project: 'listik',
      status: 'in_progress',
      status_title: 'в работе',
      stage: 's3-impl',
      stage_title: '3. Реализация',
      holder: 'agent:dsh',
      holder_title: 'dsh',
      blocked_by: ['listik-links-open', 'listik-links-gone', 'Listik-Links-Case', 'listik-links-slow'],
      parent: 'listik-links-parent',
      soft_links: ['listik-links-soft'],
      labels: ['frontend', 'deps'],
    }),
    task({ id: 'listik-links-open', title: 'Открытый блокер', status: 'open', status_title: 'открыта' }),
    task({
      id: 'listik-links-parent',
      title: 'Эпик: связи на доске',
      issue_type: 'epic',
      status: 'open',
      status_title: 'открыта',
      stage: null,
      stage_title: null,
    }),
    task({ id: 'listik-links-soft', title: 'Мягкая связь', status: 'open', status_title: 'открыта' }),
    // Закрытая, держатель заглушки остался: в панели — «выполнял», не «держит».
    task({
      id: 'listik-links-done',
      title: 'Закрытый блокер',
      status: 'done',
      status_title: 'готова',
      closed_at: iso(4),
      worked_by: ['agent:dsh'],
      worked_by_title: 'DeepSeek Harness',
    }),
    task({
      id: 'listik-links-child',
      title: 'Ребёнок карточки со связями',
      status: 'open',
      status_title: 'открыта',
      parent: 'listik-links-main',
    }),
    task({
      id: 'other-links-far',
      title: 'Задача другого проекта',
      project: 'other',
      status: 'done',
      status_title: 'готова',
      closed_at: iso(6),
    }),
    task({ id: 'listik-links-case', title: 'Задача, на которую ссылаются в другом регистре', status: 'open', status_title: 'открыта' }),
    task({ id: 'listik-links-slow', title: 'Медленная карточка', status: 'open', status_title: 'открыта' }),
    task({ id: 'listik-links-a', title: 'Взаимная ссылка A', status: 'open', status_title: 'открыта', soft_links: ['listik-links-b'] }),
    task({ id: 'listik-links-b', title: 'Взаимная ссылка B', status: 'open', status_title: 'открыта', soft_links: ['listik-links-a'] }),
  ]) {
    tasks.push(item)
  }
  Object.assign(extraDeps, {
    'listik-links-main': [
      { depends_on: 'listik-links-done', dep_type: 'blocks' },
      { depends_on: 'listik-links-done', dep_type: 'waits-for' },
      { depends_on: 'other-links-far', dep_type: 'blocks' },
    ],
  })
}

/**
 * `--hint`: карточка `listik-hint-hub` (открыта, без детей) ссылается мягкими
 * связями на шесть задач, на которых проверяется строка «закрывать нельзя:
 * открыты дети» (scripts/verify-children-hint.mjs): закрытая и отменённая с
 * открытым ребёнком, закрытая без детей, открытая с открытым ребёнком,
 * открытая с закрытым ребёнком и открытая без детей. Ребёнок привязывается
 * полем `parent` у самого ребёнка.
 */
if (hintMode) {
  for (const item of [
    task({
      id: 'listik-hint-hub',
      title: 'Хаб подсказки про открытых детей',
      status: 'open',
      status_title: 'открыта',
      soft_links: [
        'listik-hint-closed',
        'listik-hint-closed-kids',
        'listik-hint-cancelled-kids',
        'listik-hint-open-kids',
        'listik-hint-open-no-kids',
        'listik-hint-open-done-kids',
      ],
    }),
    task({
      id: 'listik-hint-closed',
      title: 'Закрытая задача без детей',
      status: 'done',
      status_title: 'готова',
      closed_at: iso(5),
      // Закрытая без держателя, но с подтверждённой работой — «выполнял Claude».
      holder: null,
      holder_title: '',
      worked_by: ['agent:claude'],
      worked_by_title: 'Claude',
    }),
    task({
      id: 'listik-hint-closed-kids',
      title: 'Закрытая задача с открытым ребёнком',
      status: 'done',
      status_title: 'готова',
      closed_at: iso(5),
    }),
    task({
      id: 'listik-hint-kid-1',
      title: 'Открытый ребёнок закрытой задачи',
      status: 'open',
      status_title: 'открыта',
      parent: 'listik-hint-closed-kids',
    }),
    task({
      id: 'listik-hint-cancelled-kids',
      title: 'Отменённая задача с открытым ребёнком',
      status: 'cancelled',
      status_title: 'отменена',
      closed_at: iso(7),
      // Два актора — «выполняли …, …».
      worked_by: ['agent:claude', 'agent:dsh'],
      worked_by_title: 'Claude, DeepSeek Harness',
    }),
    task({
      id: 'listik-hint-kid-2',
      title: 'Открытый ребёнок отменённой задачи',
      status: 'open',
      status_title: 'открыта',
      parent: 'listik-hint-cancelled-kids',
    }),
    task({
      id: 'listik-hint-open-kids',
      title: 'Открытая задача с открытым ребёнком',
      status: 'open',
      status_title: 'открыта',
    }),
    task({
      id: 'listik-hint-kid-3',
      title: 'Открытый ребёнок открытой задачи',
      status: 'open',
      status_title: 'открыта',
      parent: 'listik-hint-open-kids',
    }),
    task({
      id: 'listik-hint-open-no-kids',
      title: 'Открытая задача без детей',
      status: 'open',
      status_title: 'открыта',
    }),
    task({
      id: 'listik-hint-open-done-kids',
      title: 'Открытая задача с закрытым ребёнком',
      status: 'open',
      status_title: 'открыта',
    }),
    task({
      id: 'listik-hint-kid-4',
      title: 'Закрытый ребёнок открытой задачи',
      status: 'done',
      status_title: 'готова',
      closed_at: iso(3),
      parent: 'listik-hint-open-done-kids',
    }),
  ]) {
    tasks.push(item)
  }
}

/**
 * `--epic`: эпик `listik-epic-hub` и четыре подзадачи во всех статусах. Ребёнок
 * привязан полем `parent`; поле `children` у эпика собирает `details()`.
 */
const EPIC_HUB = 'listik-epic-hub'
if (epicMode) {
  const child = (id, title, status, stage, extra = {}) =>
    task({ id, title, status, status_title: STATUS_TITLES[status], stage, stage_title: stage ? STAGE_TITLES[stage] : null, parent: EPIC_HUB, ...extra })
  tasks.push(
    task({
      id: EPIC_HUB,
      title: 'Эпик: проверка подзадач',
      issue_type: 'epic',
      status: 'in_progress',
      status_title: 'в работе',
      stage: null,
      stage_title: null,
    }),
    child('listik-epic-open', 'Подзадача открыта', 'open', 's1-spec', { holder: null, holder_title: '' }),
    child('listik-epic-work', 'Подзадача в работе', 'in_progress', 's3-impl'),
    child('listik-epic-done', 'Подзадача готова', 'done', 'done', { closed_at: iso(3), holder: null, holder_title: '' }),
    child('listik-epic-cancel', 'Подзадача отменена', 'cancelled', null, { closed_at: iso(4), holder: null, holder_title: '' }),
  )
}

/**
 * `--markdown`: карточка `listik-markdown-case` для проверки markdown-вывода
 * панели задачи (scripts/verify-markdown.mjs). В `description` — только целевые
 * конструкции: заголовок `##`, маркированный и нумерованный списки, инлайн-код,
 * блок в тройных кавычках, таблица GFM, ссылка и строка с HTML. Вне этих конструкций `##`,
 * `- ` в начале строки и тройных кавычек в тексте нет — иначе проверка «сырой
 * разметки не осталось» была бы ложной. Остальные текстовые поля пусты, чтобы
 * кейсы в поддереве секции описания не ловили чужие элементы.
 */
const MARKDOWN_DESCRIPTION = [
  '## Разметка карточки',
  '',
  'Проверяем вывод: заголовок, списки, инлайн-код, блок кода и ссылку.',
  '',
  '- первый пункт маркированного списка',
  '- второй пункт маркированного списка',
  '',
  '1. первый пункт нумерованного списка',
  '2. второй пункт нумерованного списка',
  '',
  'Инлайн-код `npm run build` стоит прямо в абзаце.',
  '',
  '| Код | Файл | Итог |',
  '|---|---|---:|',
  '| 31cc004 | `lexicon.jsonl` | 40/41 |',
  '| 92ac192 | нет | 41/41 |',
  '',
  '```js',
  'const answer = 42',
  'console.log(answer)',
  '```',
  '',
  'Ссылка: [текст](https://example.com).',
  '',
  '<script>alert(1)</script>',
].join('\n')

/**
 * Комментарии `listik-markdown-case` (`--markdown`): жирный, инлайн-код,
 * маркированный список и вложенный ответ с кодом — по ним verify-markdown
 * проверяет, что лента «Журнал и вердикты» рендерит markdown, а не сырой
 * текст. Тексты не содержат `##`, `- ` в начале строки и тройных кавычек вне
 * целевых конструкций — как и MARKDOWN_DESCRIPTION.
 */
const MARKDOWN_COMMENTS = [
  {
    id: 10,
    author: 'agent:dsh',
    kind: 'journal',
    text: '**Итог проверки:** перенесено, см. `docs/tasks/decision.md`.\n\n- первый пункт журнала\n- второй пункт журнала',
    created_at: iso(4),
  },
  {
    id: 11,
    author: 'agent:dsh',
    kind: 'question',
    text: 'Переносить ли **MobileDetect**?',
    created_at: iso(3),
  },
  {
    id: 12,
    author: 'me',
    kind: 'answer',
    text: 'не переносить — `as-is` остаётся',
    created_at: iso(2),
  },
]

if (markdownMode) {
  tasks.push(
    task({
      id: 'listik-markdown-case',
      title: 'Карточка с markdown-разметкой',
      description: MARKDOWN_DESCRIPTION,
      acceptance: '',
      design: '',
      notes: '',
      result: '',
    }),
  )
}

/**
 * `--routes`: записи `routes.json` и карточка с отказавшим автостартом. Маршрут
 * `low-pipeline` у неё есть, а запуск не удался — `launch_error`, флаг «нужен
 * человек» и метки маршрута стоят ровно так, как их пишет `launcher.refuse`.
 * Панель задачи по `route_editable: true` показывает ту же матрицу маршрутов,
 * что «Новая задача»; снятие маршрута пунктом «без маршрута» (клик сразу
 * шлёт PATCH) проверяет scripts/verify-route-clear.mjs.
 */
const ROUTES = [
  {
    key: 'low-pipeline',
    kind: 'pipeline',
    title: 'Низкий — конвейер',
    hint: 'дёшево и быстро',
    visible: true,
    icon: 'low',
    roles: {
      spec: { provider: 'claude', label: 'ТЗ', title: 'ТЗ и чек-лист' },
      critic: { provider: 'glm', label: 'GLM', title: 'GLM — критик' },
      // provider — валидный ProviderKey ('deepseek', не 'dsh'); title отличен
      // от названия этапа, чтобы подпись «делает …» проверялась однозначно.
      impl: { provider: 'deepseek', label: 'DeepSeek', title: 'DeepSeek — код' },
      judge: { provider: 'grok', label: 'Судья', title: 'Проверка' },
    },
  },
  {
    key: 'high-pipeline',
    kind: 'pipeline',
    title: 'Высокий — конвейер',
    hint: 'дороже, но надёжнее',
    visible: true,
    icon: 'high',
    roles: {
      spec: { provider: 'claude', label: 'ТЗ', title: 'ТЗ и чек-лист' },
      impl: { provider: 'claude', label: 'Код', title: 'Реализация' },
      judge: { provider: 'grok', label: 'Судья', title: 'Проверка' },
    },
  },
  {
    key: 'dsh-direct',
    kind: 'direct',
    title: 'DeepSeek — целиком',
    hint: 'один харнесс, без конвейера',
    visible: true,
    icon: 'direct',
    harness: 'dsh',
  },
  {
    key: 'hidden-route',
    kind: 'direct',
    title: 'Скрытый маршрут',
    hint: 'в списке доски не показывается',
    visible: false,
    icon: 'direct',
    harness: 'codex',
  },
]

/* ── помощник DeepSeek (`--assistant`): заглушка транспорта ──────────────────── */

/** Модель в ответе — она же в шапке панели помощника («Помощник · deepseek-mock»). */
const ASSISTANT_MODEL = 'deepseek-mock'

/** Критерии приёмки ответа `full` — два пункта, как у настоящего предложения. */
const ASSISTANT_ACCEPTANCE = [
  'npm run build проходит без ошибок',
  'поповер помощника остаётся открытым после применения',
]

/** Сложность с причиной: бейдж «сложность: средняя» и строка причины в панели. */
const ASSISTANT_COMPLEXITY = {
  level: 'medium',
  reason: 'несколько шагов: правка панели и стенда проверки',
}

/**
 * Маршрут ответа `full` — видимая запись из `--routes`, допустимая типу `task`.
 * Умолчание формы для задачи (`lib/routes.ts` → `low-pipeline`) пропускаем: оно и
 * так выбрано, и кнопка была бы «Уже выбран» — применить маршрут нечем.
 */
function assistantRouteOf() {
  const visible = ROUTES.filter((route) => route.visible)
  return visible.find((route) => route.key !== 'low-pipeline') ?? visible[0] ?? null
}

/** Поле `route` ответа: `noroute` — null, `blocked` — ключ, которого нет в routes. */
function assistantRouteFor(mode) {
  if (mode === 'noroute') return null
  if (mode === 'blocked') {
    return {
      key: 'ghost-pipeline',
      kind: 'pipeline',
      title: 'Маршрут, которого нет в routes.json',
      hint: 'появится после правки файла',
      reason: 'модель предложила ключ, которого доска не знает',
    }
  }
  const route = assistantRouteOf()
  if (!route) return null
  return {
    key: route.key,
    kind: route.kind,
    title: route.title,
    hint: route.hint,
    reason: 'подходит по типу и объёму задачи',
  }
}

/** Ответ `POST /api/assistant/suggest` — форма `AssistantSuggestResponse` из types.ts. */
function assistantResponse(field, text, mode) {
  return {
    field,
    model: ASSISTANT_MODEL,
    suggestion: {
      text: mode === 'same' ? text : `${text} — переписано помощником`,
      acceptance: mode === 'noacc' ? [] : [...ASSISTANT_ACCEPTANCE],
      complexity: { ...ASSISTANT_COMPLEXITY },
      route: assistantRouteFor(mode),
    },
  }
}

/** `error`: 1-й, 3-й, … запросы с этим текстом — ошибка, 2-й, 4-й, … — как `full`. */
let assistantErrorCalls = 0

/* ── голосовой ввод (`--voice`): заглушка транспорта ─────────────────────────── */

/** Модель распознавания — имя в ответе `transcribe`; ключ Deepgram наружу не отдаётся. */
const VOICE_MODEL = 'nova-mock'

/** Критерии приёмки полного черновика — 2–3 непустые строки, как у настоящего разбора. */
const VOICE_DRAFT_ACCEPTANCE = [
  'npm run build проходит без ошибок',
  'кнопка «Голосом» появляется только при voice:true',
]

/** Счётчики голосовых ручек и созданий задачи — их читает `GET /__requests`. */
const voiceCounters = { transcribe: 0, draft: 0, create: 0 }
/** Тело последнего `POST /api/tasks` — для скриптовых проверок порций b/c. */
let lastCreate = null
/** id созданных моком карточек: `mock-1`, `mock-2`, … */
let nextMockId = 1

/** Маршрут полного черновика — видимая запись `ROUTES` (`key: 'low-pipeline'`). */
function voiceDraftRoute() {
  const route =
    ROUTES.find((item) => item.key === 'low-pipeline' && item.visible) ??
    ROUTES.find((item) => item.visible)
  if (!route) return null
  return {
    key: route.key,
    kind: route.kind,
    title: route.title,
    hint: route.hint,
    reason: 'мок: подходит по типу и объёму задачи',
  }
}

/** Черновик ответа `draft`: все шесть ключей есть всегда, ненайденное — `null`. */
function voiceDraftOf(mode, text) {
  const project = mode === 'noproject' ? null : mode === 'badproject' ? 'ghost-project' : 'listik'
  const title = mode === 'notitle' ? null : text.slice(0, 60)
  return {
    project,
    type: 'task',
    title,
    description: `Рассказ человека: ${text}`,
    acceptance: [...VOICE_DRAFT_ACCEPTANCE],
    route: voiceDraftRoute(),
  }
}

/** Единый формат ошибки listik/errors.py: `ok:false`, `error`, `code`, `message`, `hint`. */
function errorBody(code, message, hint) {
  return { ok: false, error: message, code, message, hint }
}

/** Декодировать base64; `null` — пустая или невалидная строка. */
function decodeBase64(value) {
  if (typeof value !== 'string' || value.length === 0) return null
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(value) || value.length % 4 !== 0) return null
  try {
    return Buffer.from(value, 'base64').toString('utf8')
  } catch {
    return null
  }
}

/**
 * Создать карточку в массиве `tasks` по телу `POST /api/tasks`: поля из тела —
 * `title`, `project`, `type`, `priority`, остальное — как у заведённой задачи.
 */
function createTaskFromBody(body) {
  const priority = Number.isFinite(Number(body.priority)) ? Number(body.priority) : 2
  return task({
    id: `mock-${nextMockId++}`,
    title: typeof body.title === 'string' ? body.title : '',
    project: typeof body.project === 'string' ? body.project : null,
    issue_type: typeof body.type === 'string' ? body.type : 'task',
    priority,
    priority_title: PRIORITY_TITLES[priority] ?? PRIORITY_TITLES[2],
    status: 'open',
    status_title: 'открыта',
    stage: null,
    stage_title: null,
    holder: null,
    holder_title: '',
    holder_at: null,
    holder_age: '',
    holder_hours: null,
    holder_note: null,
    stage_at: null,
    stage_age: '',
    stage_hours: null,
    needs_owner: false,
    created_by: 'me',
    labels: [],
  })
}

if (routesMode) {
  for (const item of [
    task({
      id: 'listik-routes-error',
      title: 'Маршрут можно сменить',
      status: 'open',
      status_title: 'открыта',
      stage: null,
      stage_title: null,
      holder: null,
      holder_title: '',
      holder_at: null,
      holder_age: '',
      holder_hours: null,
      holder_note: null,
      stage_at: null,
      stage_age: '',
      assignee: null,
      assignee_title: '',
      autostart: true,
      launch_route: 'low-pipeline',
      launch_error: 'маршрута low-pipeline нет в routes.json',
      needs_owner: true,
      labels: ['harness:claude', 'process:low-pipeline', 'frontend'],
      stale: false,
      abandoned: false,
    }),
    task({
      id: 'listik-routes-fresh',
      title: 'Заведена без маршрута',
      status: 'open',
      status_title: 'открыта',
      stage: null,
      stage_title: null,
      holder: null,
      holder_title: '',
      holder_at: null,
      holder_age: '',
      holder_hours: null,
      holder_note: null,
      stage_at: null,
      stage_age: '',
      assignee: null,
      assignee_title: '',
      labels: ['frontend'],
      stale: false,
      abandoned: false,
    }),
    // Карточка на s3-impl конвейера `low-pipeline`: держит оркестратор
    // (`claude`), а этап делает роль `impl` (DeepSeek) — по ней проверяется
    // подпись «делает …» в подвале и в панели (scripts/verify-card-executor.mjs).
    task({
      id: 'listik-executor',
      title: 'Этап делает роль маршрута',
      status: 'in_progress',
      status_title: 'в работе',
      stage: 's3-impl',
      stage_title: '3. Реализация',
      holder: 'claude',
      holder_title: 'Claude',
      holder_at: iso(0.1),
      holder_age: '6 мин',
      holder_hours: 0.1,
      idle_hours: 0.1,
      idle_age: '6 мин',
      launch_route: 'low-pipeline',
      labels: ['harness:claude', 'process:low-pipeline', 'frontend'],
      stale: false,
      abandoned: false,
    }),
  ]) {
    tasks.push(item)
  }
}

/** Метка маршрута (`harness:<x>`/`process:<y>`) — её ставит и снимает сервер. */
const ROUTE_LABEL_RE = /^(harness|process):/

/** Метки маршрута — правило `routes.labels_for`: их ставит и снимает сервер. */
function routeLabelsOf(key) {
  const route = ROUTES.find((item) => item.key === key)
  if (!route) return []
  return route.kind === 'direct'
    ? [`harness:${route.harness}`, 'process:direct']
    : ['harness:claude', `process:${route.key}`]
}

/**
 * `--cold`: четыре задачи под строку «worktree · branch» блока «Холодный старт»
 * (scripts/verify-cold-start.mjs) — отдельное дерево с веткой, маркер основной
 * ветки `main`, он же `master`, и карточка без `worktree`/`branch`. Все прочие
 * поля холодного старта у них заполнены одинаково, поэтому счётчик «N из M»
 * отличается только состоянием дерева: у трёх первых 7 из 7 (жёлтое состояние
 * считается заполненным), у последней 6 из 7.
 */
if (coldMode) {
  const coldFields = {
    status: 'open',
    status_title: 'открыта',
    stage: 's3-impl',
    stage_title: '3. Реализация',
    holder: null,
    holder_title: '',
    holder_at: null,
    holder_age: '',
    holder_hours: null,
    spec_path: 'docs/listik-cold.md',
    acceptance: 'строка «worktree · branch» красится по состоянию дерева',
    journal_path: 'docs/listik-cold.journal.md',
    review_path: 'docs/listik-cold.review.md',
    blocked_by: [],
    parent: null,
    soft_links: [],
    labels: [],
    needs_owner: false,
  }
  for (const item of [
    task({
      ...coldFields,
      id: 'listik-cold-branch',
      title: 'Холодный старт: дерево и ветка',
      worktree: '/Users/dmitry.fomin/Projects/Listik-wt/listik-cold-branch',
      branch: 'task/listik-cold-branch',
    }),
    task({ ...coldFields, id: 'listik-cold-main', title: 'Холодный старт: работа в main',
           worktree: 'main', branch: 'main' }),
    task({ ...coldFields, id: 'listik-cold-master', title: 'Холодный старт: работа в master',
           worktree: 'master', branch: 'master' }),
    task({ ...coldFields, id: 'listik-cold-none', title: 'Холодный старт: дерево не указано',
           worktree: null, branch: null }),
  ]) {
    tasks.push(item)
  }
}

/**
 * Связи задачи как в `deps` на сервере: `blocked_by` (blocks) плюс `parent-child`
 * плюс краевые строки из `extraDeps` (`--links`). Один и тот же id может прийти
 * двумя строками с разными `dep_type` — как в таблице `deps`, где ключ
 * (issue_id, depends_on, dep_type).
 */
function dependenciesOf(id) {
  const found = tasks.find((item) => item.id === id)
  if (!found) return []
  const rows = []
  const seen = new Set()
  const push = (dependsOn, depType) => {
    const key = `${dependsOn}\u0000${depType}`
    if (seen.has(key)) return
    seen.add(key)
    rows.push({ depends_on: dependsOn, dep_type: depType })
  }
  for (const dep of found.blocked_by ?? []) push(dep, 'blocks')
  if (found.parent) push(found.parent, 'parent-child')
  for (const dep of extraDeps[id] ?? []) push(dep.depends_on, dep.dep_type)
  return rows
}

/** Обратная сторона `dependenciesOf` — кто ссылается на эту задачу. */
function dependentsOf(id) {
  const out = []
  const seen = new Set()
  for (const item of tasks) {
    for (const dep of dependenciesOf(item.id)) {
      if (dep.depends_on !== id) continue
      const key = `${item.id}\u0000${dep.dep_type}`
      if (seen.has(key)) continue
      seen.add(key)
      out.push({ issue_id: item.id, dep_type: dep.dep_type })
    }
  }
  return out
}

/**
 * Комментарии, дописанные ручкой `POST /__event` (`comment`): проверка видит по
 * ним, что открытая карточка действительно перечиталась и показала свежую
 * запись. Ключ — id задачи, значение — комментарии в форме `TaskComment`.
 */
const extraComments = new Map()
let nextCommentId = 100

function details(id) {
  const found = tasks.find((item) => item.id === id)
  if (!found) return null
  return {
    ...found,
    deps_state: depsStateOf(id),
    ...(epicMode && id === EPIC_HUB
      ? {
          children: tasks
            .filter((item) => item.parent === EPIC_HUB)
            .map(({ id, title, status, status_title, stage, holder, holder_title }) => ({
              id, title, status, status_title, stage, holder, holder_title,
            })),
        }
      : {}),
    comments: [
      ...(extraComments.get(id) ?? []),
      ...(markdownMode && id === 'listik-markdown-case' ? MARKDOWN_COMMENTS : []),
      { id: 1, author: 'agent:dsh', kind: 'journal', text: 'взял в работу', created_at: iso(2) },
      { id: 2, author: 'me', kind: 'verdict', text: 'ок, собирай', created_at: iso(1) },
    ],
    dependencies: dependenciesOf(id),
    dependents: dependentsOf(id),
    events: [
      {
        ts: iso(2),
        kind: 'stage',
        from_value: 's2-review',
        to_value: 's3-impl',
        actor: 'agent:dsh',
        harness: 'dsh',
        note: 'перешёл к реализации',
        duration_s: 5400,
      },
      {
        ts: iso(3),
        kind: 'claim',
        from_value: null,
        to_value: 'agent:dsh',
        actor: 'agent:dsh',
        harness: 'dsh',
        note: null,
        duration_s: null,
      },
    ],
  }
}

const TASK_STATUSES = ['open', 'in_progress', 'blocked', 'review', 'done', 'cancelled']

/** Применить PATCH/POST-тело к заглушке — иначе доска после перемещения не меняется. */
function applyPatch(id, body) {
  const found = tasks.find((item) => item.id === id)
  if (!found) return null
  let routeChanged = false
  for (const [key, value] of Object.entries(body ?? {})) {
    if (key === 'status' && TASK_STATUSES.includes(String(value))) {
      found.status = String(value)
      found.status_title = STATUS_TITLES[found.status] ?? found.status
    } else if (key === 'stage') {
      found.stage = String(value)
      found.stage_title = STAGE_TITLES[found.stage] ?? found.stage
    } else if (key === 'holder' || key === 'assignee' || key === 'project' || key === 'title') {
      found[key] = value === '' ? null : value
      if (key === 'holder') found.holder_title = value || ''
    } else if (key === 'owner') {
      // Пустая строка снимает владельца — как `PATCH {owner: ''}` на сервере.
      found.owner = value === '' || value == null ? null : String(value)
    } else if (key === 'needs_owner') {
      found.needs_owner = Boolean(value)
    } else if (key === 'labels') {
      found.labels = Array.isArray(value) ? value : found.labels
    } else if (key === 'route' || key === 'launch_route') {
      // Пустая строка — «без маршрута»; смена маршрута снимает прошлый отказ
      // автостарта вместе с флагом — как store.update_task (docs/API.md).
      const next = value === '' || value == null ? null : String(value)
      if (next !== found.launch_route) {
        found.launch_route = next
        found.launch_error = null
        found.needs_owner = false
        routeChanged = true
      }
    }
  }
  // Метки маршрута (`harness:`/`process:`) переписывает сервер, а не доска:
  // `store.labels_after_route_change` — старые метки маршрута снимаются, метки
  // нового встают на их место, чужие метки задачи остаются. Неизвестный непустой
  // ключ (устаревший `routes.json`) метки не трогает; пустой — убирает.
  if (routeChanged) {
    const keep = (found.labels ?? []).filter((label) => !ROUTE_LABEL_RE.test(label))
    const fresh = routeLabelsOf(found.launch_route)
    if (fresh.length || !found.launch_route) {
      found.labels = [...keep, ...fresh.filter((label) => !keep.includes(label))]
    }
  }
  found.route_editable = routeEditableOf(found)
  found.updated_at = new Date().toISOString()
  found.updated_age = 'только что'
  return found
}

/** `DELETE /api/tasks/{id}` и кадр `action=deleted` в `/__event`. */
function removeTask(id) {
  const index = tasks.findIndex((item) => item.id === id)
  if (index === -1) return false
  tasks.splice(index, 1)
  extraComments.delete(id)
  return true
}

const OPEN_STATUSES = ['open', 'in_progress', 'blocked', 'review']
const ORDER_KEYS = {
  updated: (item) => item.updated_at,
  created: (item) => item.created_at,
  priority: (item) => item.priority,
  stage: (item) => item.stage_at,
}

/** `GET /api/tasks`: фильтры/сортировка/пагинация как в listik/store.py list_tasks. */
function listTasks(params, source = tasks) {
  const project = params.get('project')
  const status = params.get('status')
  const includeClosed = ['1', 'true'].includes(params.get('include_closed') ?? '')
  const needsOwner = ['1', 'true'].includes(params.get('needs_owner') ?? '')
  const order = params.get('order') ?? 'updated'
  let limit = Number.parseInt(params.get('limit') ?? '', 10)
  if (!Number.isFinite(limit) || limit < 0) limit = 200
  let offset = Number.parseInt(params.get('offset') ?? '', 10)
  if (!Number.isFinite(offset) || offset < 0) offset = 0

  let filtered = source.slice()
  if (project) filtered = filtered.filter((item) => item.project === project)
  if (status) {
    filtered = filtered.filter((item) => item.status === status)
  } else if (!includeClosed) {
    filtered = filtered.filter((item) => OPEN_STATUSES.includes(item.status))
  }
  if (needsOwner) filtered = filtered.filter((item) => item.needs_owner === true)

  const orderKey = ORDER_KEYS[order] ? order : 'updated'
  const keyFn = ORDER_KEYS[orderKey]
  const indexed = filtered.map((item, index) => ({ item, index }))
  indexed.sort((a, b) => {
    const av = keyFn(a.item)
    const bv = keyFn(b.item)
    let cmp
    if (orderKey === 'priority') {
      cmp = av - bv
      if (cmp === 0) cmp = a.item.updated_at < b.item.updated_at ? 1 : a.item.updated_at > b.item.updated_at ? -1 : 0
    } else if (orderKey === 'stage') {
      cmp = av < bv ? -1 : av > bv ? 1 : 0
    } else {
      // updated/created — по убыванию
      cmp = av < bv ? 1 : av > bv ? -1 : 0
    }
    if (cmp !== 0) return cmp
    return a.index - b.index
  })
  const sorted = indexed.map((entry) => entry.item)

  const total = sorted.length
  const page = sorted.slice(offset, offset + limit)
  return { total, limit, offset, tasks: page }
}

function board(groupBy, source = tasks) {
  // Находка lint — у одной карточки «в работе без держателя» (как в store.lint); копии,
  // чтобы поле `lint` не протекло в list/show.
  const lintTarget = source.find(
    (item) =>
      item.status === 'in_progress' && !item.holder && !item.needs_owner && !item.stale && !item.abandoned,
  )
  const open = source
    .filter((item) => !['done', 'cancelled'].includes(item.status))
    .map((item) => ({ ...item, lint: item === lintTarget ? ['in_progress_no_holder'] : [] }))
  const lintItems = lintTarget
    ? [{
        rule: 'in_progress_no_holder',
        id: lintTarget.id,
        title: lintTarget.title,
        stage: lintTarget.stage,
        status: lintTarget.status,
        message: 'в работе без держателя',
        details: { released_at: null },
      }]
    : []
  const columnsMap = new Map()
  const keyOf = (item) => {
    if (groupBy === 'stage') return item.stage ?? 'none'
    if (groupBy === 'project') return item.project ?? 'без проекта'
    if (groupBy === 'holder') return item.holder ?? '—'
    return item.status
  }
  for (const item of open) {
    const key = keyOf(item)
    if (!columnsMap.has(key)) {
      const title =
        groupBy === 'stage'
          ? (STAGE_TITLES[key] ?? key)
          : groupBy === 'project'
            ? key
            : groupBy === 'holder'
              ? (item.holder_title || 'никто не держит')
              : (STATUS_TITLES[key] ?? key)
      columnsMap.set(key, { key, title, tasks: [] })
    }
    columnsMap.get(key).tasks.push(item)
  }
  const columns = [...columnsMap.values()].map((column) => ({
    ...column,
    count: column.tasks.length,
    wip: column.tasks.filter((item) => item.status === 'in_progress').length,
    needs_owner: column.tasks.filter((item) => item.needs_owner).length,
    stale: column.tasks.filter((item) => item.stale).length,
  }))
  const ready = open
    .filter((item) => blockersOf(item.id).length === 0 && !item.holder)
    .sort((a, b) => a.priority - b.priority || (a.updated_at < b.updated_at ? 1 : -1))
    .map((item) => ({ ...item, waiting_for_count: waitingForOf(item.id).length }))
  const blockedCount = open.filter((item) => blockersOf(item.id).length > 0).length
  return {
    group_by: groupBy,
    columns,
    total: open.length,
    needs_you: open.filter((item) => item.needs_owner || item.stale || item.abandoned || item.lint?.length),
    ready,
    blocked_count: blockedCount,
    cycles: [],
    lint: { count: lintItems.length, items: lintItems },
    generated_at: new Date().toISOString(),
  }
}

/**
 * Тело запроса: у createServer колбэк не async, поэтому собираем чанки вручную.
 * Результат кешируется на самом запросе: поток читается ровно один раз, и второй
 * вызов (лог заголовков в серверном режиме + сам обработчик) не виснет на `end`,
 * который уже случился.
 */
function readJsonBody(request) {
  if (request.__body) return request.__body
  const promise = new Promise((resolve) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(chunk))
    request.on('end', () => {
      if (chunks.length === 0) return resolve({})
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')))
      } catch {
        resolve({})
      }
    })
  })
  request.__body = promise
  return promise
}

/** Справка о связанной задаче — форма deps._info на сервере. */
function depInfo(id, depType = 'blocks') {
  const found = tasks.find((item) => item.id === id)
  if (!found) {
    return { id, dep_type: depType, dep_title: 'блокирует', title: '(задача не найдена)',
             status: 'missing', closed: false, missing: true, holder_title: '—', idle_age: '—' }
  }
  return {
    id,
    dep_type: depType,
    dep_title: 'блокирует',
    title: found.title,
    status: found.status,
    closed: ['done', 'cancelled'].includes(found.status),
    project: found.project,
    stage: found.stage,
    stage_title: found.stage_title,
    holder: found.holder,
    holder_title: found.holder_title || '—',
    holder_age: found.holder_age || '—',
    idle_age: found.idle_age || '—',
    stale: Boolean(found.stale),
    missing: false,
  }
}

const FINAL = ['done', 'cancelled']

function blockersOf(id) {
  const found = tasks.find((item) => item.id === id)
  return (found?.blocked_by ?? [])
    .filter((dep) => !FINAL.includes(tasks.find((t) => t.id === dep)?.status ?? 'open'))
    .map((dep) => depInfo(dep))
}

function waitingForOf(id) {
  return tasks
    .filter((item) => (item.blocked_by ?? []).includes(id) && !FINAL.includes(item.status))
    .map((item) => depInfo(item.id))
}

function depsStateOf(id) {
  const found = tasks.find((item) => item.id === id)
  const blockedBy = blockersOf(id)
  const waitingFor = waitingForOf(id)
  const finished = FINAL.includes(found.status)
  const occupied = Boolean(found.holder)
  const childrenOpen = tasks.filter((item) => item.parent === id && !FINAL.includes(item.status))
  const reasons = []
  if (finished) reasons.push(`задача уже ${found.status_title}`)
  if (blockedBy.length) {
    reasons.push(`ждёт завершения: ${blockedBy.map((b) => `${b.id} (${b.status}, ${b.idle_age})`).join(', ')}`)
  }
  if (occupied) reasons.push(`держит ${found.holder_title} (${found.holder_age})`)
  return {
    task_id: id,
    title: found.title,
    status: found.status,
    stage: found.stage,
    ready: !finished && blockedBy.length === 0 && !occupied,
    claimable: !finished && blockedBy.length === 0,
    can_finish: !finished && childrenOpen.length === 0,
    verdict: finished ? 'уже завершена'
      : blockedBy.length ? 'нельзя: ждёт другие задачи'
      : occupied ? 'занята другим' : 'можно брать',
    reasons,
    blocked_by: blockedBy,
    waiting_for: waitingFor,
    children_open: childrenOpen.map((item) => depInfo(item.id, 'parent-child')),
    parent: found.parent ? depInfo(found.parent, 'parent-child') : null,
    soft_links: (found.soft_links ?? []).map((dep) => depInfo(dep, 'relates-to')),
    holder: found.holder ?? null,
    holder_title: found.holder_title || '',
    holder_age: found.holder_age || '',
    stale_holder: Boolean(found.stale),
  }
}

/** Открытые подписки `/api/stream`: ручка `POST /__event` шлёт кадры в них. */
const streamClients = new Set()
/** Сколько раз читали карточку `GET /api/tasks/{id}` — счёт для проверок. */
const detailReads = new Map()

const server = createServer(async (request, response) => {
  const url = new URL(request.url ?? '/', `http://127.0.0.1:${port}`)
  if (process.env.MOCK_LOG) {
    request.on('end', () => console.log(`${request.method} ${url.pathname}${url.search}`))
  }
  const json = (status, body) => {
    response.writeHead(status, {
      'Content-Type': 'application/json; charset=utf-8',
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': '*',
    })
    response.end(JSON.stringify(body))
    // Возврат — не данные, а «ответ отправлен»: вызывающий код пишет
    // `return json(...)`, а проверки владельца — `if (denied) return denied`,
    // и им нужен истинный признак, иначе ответ уйдёт дважды.
    return true
  }
  const ok = (data) => json(200, { ok: true, data })

  // ── идентичность «я — …» (только `--server-mode`) ──────────────────────────
  // Node отдаёт имена заголовков в нижнем регистре; пустой/пробельный = нет.
  const viewer = String(request.headers['x-listik-owner'] ?? '').trim()
  if (serverMode) {
    // Строка на запрос: метод, путь, заголовок и (у пишущих запросов) ключи тела
    // — по ней verify-owner.mjs и судья видят, с каким именем шёл запрос и что
    // смена «я — …» ничего не пишет.
    let keys = null
    if (url.pathname.startsWith('/api/') && ['POST', 'PATCH', 'PUT'].includes(request.method)) {
      keys = Object.keys((await readJsonBody(request)) ?? {})
    }
    const body = keys ? ` body: ${keys.join(',') || '-'}` : ''
    console.log(`${request.method} ${url.pathname}${url.search} x-listik-owner: ${viewer || '-'}${body}`)
  }
  /** Незнакомое имя — 400, как на сервере: доска обязана его снять, а не слать дальше. */
  const unknownViewer = () =>
    json(400, {
      ok: false,
      error: `владелец ${viewer} не в списке server.users`,
      code: 'bad_argument',
      message: `владелец ${viewer} не в списке server.users`,
      hint: `известны: ${SERVER_USERS.join(', ')}`,
    })
  const viewerBad = serverMode && viewer !== '' && !SERVER_USERS.includes(viewer)
  /** Что видно в списках: с заголовком — свои и ничьи, без заголовка — все. */
  const visibleTasks = () =>
    serverMode && viewer ? tasks.filter((item) => !item.owner || item.owner === viewer) : tasks
  const forbidden = (id, ownerName) => {
    const message = `задача ${id} принадлежит ${ownerName}: чужую задачу брать нельзя`
    return json(403, { ok: false, error: message, code: 'forbidden', message, hint: 'смените «я — …»' })
  }
  /** Проверка перед claim/heartbeat/stage с держателем: кто взял, тот и владелец. */
  const denyForeign = (id) => {
    if (!serverMode) return null
    if (!viewer) {
      const message = 'не указано, от чьего имени: заголовок X-Listik-Owner пуст'
      return json(400, { ok: false, error: message, code: 'bad_argument', message, hint: 'выберите «я — …»' })
    }
    const found = tasks.find((item) => item.id === id)
    if (found?.owner && found.owner !== viewer) return forbidden(id, found.owner)
    return null
  }

  // Запрос с заголовком Authorization — не «простой», браузер шлёт preflight;
  // без 2xx на OPTIONS fetch падает ещё до GET (реальный сервер это умеет, см. _cors).
  if (request.method === 'OPTIONS') {
    response.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Headers': '*',
      'Access-Control-Allow-Methods': '*',
      'Access-Control-Max-Age': '600',
    })
    response.end()
    return
  }

  if (url.pathname === '/__token') {
    response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
    response.end(
      '<!doctype html><meta charset="utf-8"><script>' +
        "localStorage.setItem('listik.token','mock-token');" +
        "document.body.textContent='token set';" +
      '</script>',
    )
    return
  }

  // ── служебные ручки скриптов проверки (не часть docs/API.md) ────────────────
  if (url.pathname === '/__requests') {
    if (request.method === 'POST') {
      detailReads.clear()
      voiceCounters.transcribe = 0
      voiceCounters.draft = 0
      voiceCounters.create = 0
      lastCreate = null
      return ok({
        detail_reads: {},
        streams: streamClients.size,
        voice: { transcribe: 0, draft: 0, create: 0 },
        last_create: null,
      })
    }
    return ok({
      detail_reads: Object.fromEntries(detailReads),
      streams: streamClients.size,
      voice: { ...voiceCounters },
      last_create: lastCreate,
    })
  }

  if (url.pathname === '/__event') {
    const body = await readJsonBody(request)
    const id = body.payload?.id
    if (body.payload?.action === 'deleted' && id) removeTask(id)
    if (body.patch && id) applyPatch(id, body.patch)
    if (body.comment && id) {
      const list = extraComments.get(id) ?? []
      list.push({
        id: nextCommentId++,
        author: body.comment.author ?? 'agent:dsh',
        kind: body.comment.kind ?? 'journal',
        text: String(body.comment.text ?? ''),
        created_at: new Date().toISOString(),
      })
      extraComments.set(id, list)
    }
    const frame = `data: ${JSON.stringify({
      kind: body.kind ?? 'task',
      at: new Date().toISOString(),
      payload: body.payload ?? {},
    })}\n\n`
    for (const client of streamClients) client.write(frame)
    // clients=0 — событие ушло в пустоту: проверка должна это заметить, а не
    // решить, что доска не отреагировала.
    return ok({ clients: streamClients.size, payload: body.payload ?? {} })
  }

  if (url.pathname === '/api/stream') {
    response.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store',
      // настоящий сервер Listik отдаёт этот заголовок сам (см. _stream в server.py)
      'Access-Control-Allow-Origin': '*',
    })
    response.write(': mock stream\n\n')
    streamClients.add(response)
    const timer = setInterval(() => response.write(': ping\n\n'), 15000)
    request.on('close', () => {
      clearInterval(timer)
      streamClients.delete(response)
    })
    return
  }

  if (url.pathname === '/api/health') {
    return ok({
      status: 'ok',
      version: '0.1.0-mock',
      db: '/tmp/mock.db',
      counts: { tasks: tasks.length, comments: 2 },
      embed: { ok: true, models: ['bge-m3'] },
      now: new Date().toISOString(),
      // Режим и список пользователей доска читает отсюда: без `--server-mode`
      // это `local` с пустым списком — новых элементов на доске не будет.
      mode: serverMode ? 'server' : 'local',
      users: serverMode ? SERVER_USERS : [],
      owner: serverMode && SERVER_USERS.includes(viewer) ? viewer : null,
    })
  }

  if (url.pathname === '/api/meta') {
    const fillTasks = tasks.filter((item) => item.project === 'fill')
    const known = new Set(['listik', 'fill', 'other'])
    const extra = [...new Set(tasks.map((item) => item.project))].filter((slug) => !known.has(slug))
    const projects = [{ slug: 'listik', title: 'Listik', kind: 'native', n_tasks: tasks.length - fillTasks.length }]
    if (fillCount >= 1) {
      projects.push({ slug: 'fill', title: 'Заполнитель', kind: 'native', n_tasks: fillTasks.length })
    }
    if (linksMode) {
      projects.push({
        slug: 'other',
        title: 'Другой проект',
        kind: 'native',
        n_tasks: tasks.filter((item) => item.project === 'other').length,
      })
    }
    return ok({
      projects,
      actors: [
        { key: 'me', title: 'Дмитрий', kind: 'human', n_tasks: 1 },
        { key: 'agent:dsh', title: 'dsh', kind: 'agent', n_tasks: 1 },
      ],
      facets: {
        projects: [...projects.map((project) => project.slug), ...extra],
        assignees: ['agent:dsh', '—'],
        holders: ['agent:dsh', 'agent:claude', '—'],
        statuses: Object.keys(STATUS_TITLES),
        stages: Object.keys(STAGE_TITLES),
        types: ['feature', 'task', 'bug'],
      },
      statuses: STATUS_TITLES,
      stages: STAGE_TITLES,
      priorities: PRIORITY_TITLES,
    })
  }

  if (url.pathname === '/api/stats') {
    return ok({
      by_status: { open: 1, in_progress: 1, blocked: 1, review: 1, done: 4 },
      by_stage: { 's1-spec': 1, 's2-review': 1, 's3-impl': 1, 's4-judge': 1 },
      by_project: [{ project: 'listik', total: 4, in_progress: 1, waiting: 1 }],
      by_holder: [{ holder: 'agent:dsh', title: 'dsh', count: 1 }],
      by_actor: [{ actor: 'agent:dsh', title: 'dsh', count: 2 }],
      stale: 1,
      needs_owner: 1,
      running: tasks.filter((item) => item.status === 'in_progress'),
      generated_at: new Date().toISOString(),
    })
  }

  if (url.pathname === '/api/ready') {
    if (viewerBad) return unknownViewer()
    const open = visibleTasks().filter((item) => !FINAL.includes(item.status) && !item.archived)
    return ok({
      tasks: open
        .filter((item) => blockersOf(item.id).length === 0 && !item.holder)
        .sort((a, b) => a.priority - b.priority)
        .map((item) => ({ ...item, waiting_for_count: waitingForOf(item.id).length })),
      cycles: [],
      generated_at: new Date().toISOString(),
    })
  }

  if (url.pathname === '/api/blocked') {
    const open = tasks.filter((item) => !FINAL.includes(item.status) && !item.archived)
    const list = open
      .map((item) => {
        const blockers = blockersOf(item.id)
        if (blockers.length === 0) return null
        // как на сервере: блокеры «стоят», если ни одного из них никто не двигает
        const idle = blockers.every((b) => b.missing || b.stale || !b.holder)
        return {
          ...item,
          blockers,
          blocked_by: blockers.map((b) => b.id),
          blockers_idle: idle,
          blocked_by_stale: idle,
          blocked_by_holder: blockers.find((b) => b.holder)?.holder_title ?? null,
        }
      })
      .filter(Boolean)
      .sort((a, b) => Number(b.blocked_by_stale) - Number(a.blocked_by_stale) || a.priority - b.priority)
    return ok({ tasks: list, generated_at: new Date().toISOString() })
  }

  if (url.pathname === '/api/board') {
    if (viewerBad) return unknownViewer()
    return ok(board(url.searchParams.get('group_by') ?? 'status', visibleTasks()))
  }

  if (url.pathname === '/api/routes') {
    // Форма ответа — как у сервера: ошибка файла приезжает `ok:false` с текстом,
    // `command` наружу не отдаётся (маршруты есть только в режиме `--routes`).
    return ok({
      ok: routesMode,
      error: routesMode ? null : 'routes.json не читался: мок запущен без --routes',
      path: '~/.config/listik/routes.json',
      routes: routesMode ? ROUTES : [],
    })
  }

  // Без `--assistant` ручек нет вовсе — мок отвечает как до правки (404 ниже).
  if (assistantMode && url.pathname === '/api/assistant/status') {
    // Ключ наружу не отдаётся и моком: только факт «настроен» и имя модели.
    const status = { enabled: true, model: ASSISTANT_MODEL, base_url: 'https://api.deepseek.com' }
    // Без `--voice` поля нет вовсе — доска читает `undefined` как «выключено».
    if (voiceMode) status.voice = true
    return ok(status)
  }

  if (assistantMode && url.pathname === '/api/assistant/suggest' && request.method === 'POST') {
    const body = await readJsonBody(request)
    const text = typeof body.text === 'string' ? body.text : ''
    const asked = text.trim()
    let mode = ['full', 'same', 'blocked', 'noroute', 'noacc', 'error'].includes(asked) ? asked : 'full'
    if (mode === 'error') {
      assistantErrorCalls += 1
      if (assistantErrorCalls % 2 === 1) {
        const message = 'DeepSeek недоступен: мок отдаёт ошибку на каждый нечётный запрос'
        // Доска читает `error` (client.ts → ApiError.message), а `code`/`message`/`hint` —
        // триада единого формата ошибок listik/errors.py.
        return json(500, {
          ok: false,
          error: message,
          code: 'server_error',
          message,
          hint: 'повторите запрос: следующий ответ мока будет успешным',
        })
      }
      mode = 'full'
    }
    return ok(assistantResponse(body.field, text, mode))
  }

  // Голосовые ручки живут только под `--voice` — как ветки помощника под `--assistant`.
  if (voiceMode && url.pathname === '/api/assistant/transcribe' && request.method === 'POST') {
    const body = await readJsonBody(request)
    voiceCounters.transcribe += 1
    const marker = decodeBase64(body.audio_base64)
    const mime = typeof body.mime === 'string' ? body.mime : ''
    if (marker === null || !mime.startsWith('audio/')) {
      return json(400, errorBody('bad_argument', 'пустая или невалидная запись', 'нужен base64 аудио и mime вида audio/…'))
    }
    if (marker.includes('transcribe-fail')) {
      return json(502, errorBody('server_error', 'распознавание речи не ответило', 'повторите запрос позже'))
    }
    // Тишина — не ошибка: 200 с пустым `transcript`.
    if (marker.includes('silence')) return ok({ transcript: '', model: VOICE_MODEL })
    return ok({ transcript: `Расшифровка: ${marker}`, model: VOICE_MODEL })
  }

  if (voiceMode && url.pathname === '/api/assistant/draft' && request.method === 'POST') {
    const body = await readJsonBody(request)
    voiceCounters.draft += 1
    const text = typeof body.text === 'string' ? body.text : ''
    if (!text.trim()) {
      return json(400, errorBody('bad_argument', 'нечего разбирать: запись пустая', 'передайте непустой text'))
    }
    if (text.includes('draft-fail')) {
      return json(502, errorBody('server_error', 'DeepSeek не ответил', 'повторите запрос позже'))
    }
    const mode = text.includes('badproject')
      ? 'badproject'
      : text.includes('noproject')
        ? 'noproject'
        : text.includes('notitle')
          ? 'notitle'
          : 'full'
    return ok({ model: ASSISTANT_MODEL, draft: voiceDraftOf(mode, text) })
  }

  if (url.pathname === '/api/tasks') {
    if (viewerBad) return unknownViewer()
    if (request.method === 'POST') {
      const body = await readJsonBody(request)
      voiceCounters.create += 1
      lastCreate = body
      const wanted = typeof body.owner === 'string' ? body.owner.trim() : ''
      if (serverMode && !wanted && !viewer) {
        const message = 'у задачи должен быть владелец: представьтесь или укажите owner'
        return json(400, { ok: false, error: message, code: 'bad_argument', message, hint: 'выберите «я — …»' })
      }
      const created = createTaskFromBody(body)
      // Владелец: в серверном режиме — из тела или представившийся, локально — ничей.
      created.owner = serverMode ? wanted || viewer : null
      tasks.push(created)
      return ok(created)
    }
    // Без серверного режима visibleTasks() — это весь `tasks`, фильтр включается с «я — …».
    return ok(listTasks(url.searchParams, visibleTasks()))
  }

  if (url.pathname.startsWith('/api/tasks/')) {
    const id = decodeURIComponent(url.pathname.replace('/api/tasks/', '').split('/')[0])
    const action = url.pathname.replace('/api/tasks/', '').split('/')[1]
    if (action === 'deps') {
      const state = depsStateOf(id)
      return ok({
        task: depInfo(id, 'self'),
        waits_for: state.blocked_by,
        waited_by: state.waiting_for,
        soft_links: state.soft_links,
      })
    }
    if (action === 'ready') return ok(depsStateOf(id))
    if (!action && request.method === 'DELETE') {
      return removeTask(id)
        ? ok({ deleted: id })
        : json(404, { ok: false, error: `задача не найдена: ${id}` })
    }
    // Серверный режим: `claim`/`heartbeat`/`stage` с держателем — только своя
    // задача и только от представившегося. Тело читается один раз: повторный
    // readJsonBody по уже прочитанному потоку не дождался бы `end`.
    if (serverMode && request.method === 'POST' && ['claim', 'heartbeat', 'stage'].includes(action)) {
      const body = await readJsonBody(request)
      if (String(body.holder ?? '').trim()) {
        const denied = denyForeign(id)
        if (denied) return denied
      }
      const found = tasks.find((item) => item.id === id)
      if (!found) return json(404, { ok: false, error: `задача не найдена: ${id}` })
      if (action === 'claim') {
        found.holder = body.holder ?? 'probe'
        found.holder_title = body.holder ?? 'probe'
        if (found.status === 'open') { found.status = 'in_progress'; found.status_title = 'в работе' }
        return ok(found)
      }
      return ok(applyPatch(id, body))
    }
    if (action === 'claim') {
      const body = await readJsonBody(request)
      const state = depsStateOf(id)
      if (state.blocked_by.length > 0 && !body.force) {
        return json(400, {
          ok: false,
          error:
            `задача ${id} заблокирована и брать её нельзя.\n` +
            `Ждём завершения: ${state.blocked_by.map((b) => b.id).join(', ')}\n` +
            'Варианты: взять сам блокер, поставить блокеру needs-owner, или осознанно обойти запрет: claim --force',
        })
      }
      const found = tasks.find((item) => item.id === id)
      found.holder = body.holder ?? 'probe'
      found.holder_title = body.holder ?? 'probe'
      if (found.status === 'open') { found.status = 'in_progress'; found.status_title = 'в работе' }
      return ok(found)
    }
    if (request.method === 'GET') {
      detailReads.set(id, (detailReads.get(id) ?? 0) + 1)
      // `--slow-ms`: задержка отдачи карточки — гонка «клик по ссылке, потом другая
      // задача» (scripts/verify-deps-links.mjs) без правки мока не воспроизводится.
      if (slowMs > 0 && id === 'listik-links-slow') {
        await new Promise((resolve) => setTimeout(resolve, slowMs))
      }
      const found = details(id)
      return found ? ok(found) : json(404, { ok: false, error: `задача не найдена: ${id}` })
    }
    const body = await readJsonBody(request)
    // Смена владельца проходит при любом заголовке (тело только с `owner`);
    // любая другая правка чужой задачи — 403, как на сервере.
    if (serverMode && request.method === 'PATCH') {
      const keys = Object.keys(body ?? {})
      const ownerOnly = keys.length > 0 && keys.every((key) => key === 'owner')
      const found = tasks.find((item) => item.id === id)
      if (!ownerOnly && viewer && found?.owner && found.owner !== viewer) return forbidden(id, found.owner)
    }
    const updated = applyPatch(id, body)
    return updated ? ok(updated) : json(404, { ok: false, error: `задача не найдена: ${id}` })
  }

  if (url.pathname === '/api/timeline') {
    return ok({
      items: [
        {
          ts: iso(0.5),
          kind: 'stage',
          from_value: 's2-review',
          to_value: 's3-impl',
          actor: 'agent:dsh',
          actor_title: 'dsh',
          harness: 'dsh',
          note: 'перешёл к реализации',
          duration_s: 5400,
          task_id: tasks[0].id,
          title: tasks[0].title,
          project: 'listik',
          stage: 's3-impl',
          status: 'in_progress',
          age: '30 мин',
        },
      ],
    })
  }

  if (url.pathname === '/api/search') {
    const q = url.searchParams.get('q') ?? ''
    return ok({
      query: q,
      mode: url.searchParams.get('mode') ?? 'hybrid',
      took_ms: 42,
      lexical_docs: 3,
      vector_docs: 2,
      count: 1,
      results: [
        {
          id: tasks[0].id,
          project: 'listik',
          title: tasks[0].title,
          status: 'in_progress',
          stage: 's3-impl',
          holder: 'agent:dsh',
          actor: 'agent:dsh',
          actor_name: 'dsh',
          priority: 2,
          issue_type: 'feature',
          updated_at: iso(0.3),
          labels: ['frontend'],
          snippet: `…${q}… найден в описании задачи`,
          score: 0.032,
          hits: [{ kind: 'task', doc_id: 1, rrf: 0.032, snippet: `…${q}…`, author: null }],
          needs_owner: false,
        },
      ],
    })
  }

  return json(404, { ok: false, error: `нет такого пути: ${url.pathname}` })
})

server.listen(port, '127.0.0.1', () => {
  console.log(`mock listik api: http://127.0.0.1:${port}`)
})
