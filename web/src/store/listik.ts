/**
 * Состояние доски: единственный владелец данных с сервера. Все запросы к API
 * проходят здесь, компоненты читают реактивные поля и вызывают действия.
 *
 * Стейт модульный (один на приложение), а `useListikStore()` — просто точка
 * доступа к нему, как `useToast()` в ките.
 */
import { computed, reactive, ref, shallowRef } from 'vue'
import { ApiError, api, subscribeStream } from '@/api/client'
import { readStoredOwner, readStoredToken, writeStoredOwner, writeStoredToken } from '@/api/config'
import { AT_RISK_IDLE_HOURS, taskHealth } from '@/lib/health'
import { INTAKE_COLUMN_KEY, PIPELINE_STAGE_KEYS, STAGES } from '@/lib/dictionaries'
import { tryRequest, withLoading } from './helpers'
import type {
  AssistantSuggestRequest,
  AssistantSuggestResponse,
  BlockedTask,
  DepTree,
  Board,
  BoardColumn,
  CommentKind,
  DepInfo,
  Health,
  Meta,
  ProjectPatch,
  ProjectRow,
  RouteDef,
  RoutePatch,
  SearchMode,
  ReadyTask,
  SearchResponse,
  Stats,
  StreamEvent,
  Task,
  TaskDetail,
  TaskPatch,
  TasksPage,
  TimelineItem,
  VoiceDraftRequest,
  VoiceDraftResponse,
  VoiceTranscribeRequest,
  VoiceTranscribeResponse,
} from '@/api/types'

export type ViewKey = 'board' | 'list' | 'metrics'

export type DepsFilter = 'all' | 'ready' | 'blocked'

export interface Filters {
  project: string
  status: string
  stage: string
  assignee: string
  type: string
  needsOwner: boolean
  /** Здоровье по heartbeat/держателю (см. lib/health.ts): пусто — без фильтра. */
  health: '' | 'dead' | 'at-risk'
  updatedFrom: string
  updatedTo: string
  /** «только готовые к работе» / «только заблокированные» — по графу зависимостей. */
  deps: DepsFilter
}

/** Фильтры чипов тулбара: действуют только на выборку доски и инбокс. */
export interface BoardFilters {
  health: Filters['health']
  deps: DepsFilter
}

export function emptyFilters(): Filters {
  return {
    project: '',
    status: '',
    stage: '',
    assignee: '',
    type: '',
    needsOwner: false,
    health: '',
    updatedFrom: '',
    updatedTo: '',
    deps: 'all',
  }
}

const HEALTH_POLL_MS = 30000
const SSE_DEBOUNCE_MS = 500

const token = ref(readStoredToken())
/**
 * «Я — …»: имя из `server.users`, которым доска представляется серверу
 * (заголовок `X-Listik-Owner` ставит api/client.ts на каждый запрос). Пусто —
 * доска не представилась: сервер отдаёт все задачи и не даёт ничего взять.
 */
const owner = ref(readStoredOwner())
const view = ref<ViewKey>('board')

const health = ref<Health | null>(null)
const meta = ref<Meta | null>(null)
const board = ref<Board | null>(null)
const stats = ref<Stats | null>(null)
/** Сколько задач закрыто за последние 7 суток — счётчик рельсы «Готово». */
const doneWeekCount = ref<number>(0)
const timeline = ref<TimelineItem[]>([])
const searchResponse = shallowRef<SearchResponse | null>(null)

/**
 * Срез зависимостей по задачам выборки: заполняется из /api/ready (кто ждёт
 * задачу) и /api/blocked (кого ждёт задача). Нужен карточкам и списку — грузить
 * `deps_state` для каждой карточки отдельным запросом было бы 150+ запросов.
 */
export interface DepsSummary {
  blockedBy: DepInfo[]
  waitingForCount: number
  childrenOpen: number
  blockedByStale: boolean
}

const depsSummary = ref<Record<string, DepsSummary>>({})
const readyTasks = ref<ReadyTask[]>([])
const blockedTasks = ref<BlockedTask[]>([])
const depsLoading = ref(false)

const loading = ref(false)
const searchLoading = ref(false)
const lastError = ref<string | null>(null)
const connectionLost = ref(false)
const needsToken = ref(false)
const live = ref(false)
const lastSyncAt = ref<string | null>(null)

/** Режим телефона (шаг 06, порция b): вьюпорт ≤ 767px. Ставит App.vue из useIsPhone(). */
const phone = ref(false)
/** Счётчик «очередь надо перечитать» — растёт при каждом refresh() в режиме телефона. */
const queueTick = ref(0)
/** init() уже вызывался — setPhone() не должен запускать refresh() до первой загрузки. */
let initialised = false

const filters = reactive<Filters>(emptyFilters())
const boardFilters = reactive<BoardFilters>({ health: '', deps: 'all' })
const query = ref('')
const searchMode = ref<SearchMode>('hybrid')

const openTaskId = ref<string | null>(null)
const detail = ref<TaskDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)

const paletteOpen = ref(false)
const pending = ref<string | null>(null)

/** Репозитории (проекты) раздела настроек: что показывать на доске, что скрыто. */
const projects = ref<ProjectRow[]>([])
const projectsRoot = ref('')
const projectsLoading = ref(false)
const projectsError = ref<string | null>(null)

/**
 * Маршруты запуска (`GET /api/routes`, файл `routes.json`): грузятся один раз за
 * сессию доски — при её старте (`init`, иконки уровней нужны карточкам задач) —
 * либо повторно кнопкой «повторить» (`loadRoutes`). Ошибка самого файла приходит
 * в ответе (`ok:false` + `error`), отказ запроса — исключением; в обоих случаях
 * форма показывает алерт и создаёт задачу без маршрута. `routesWarnings` —
 * замечания проверки, которые файл не отменяют (неизвестный `icon` записи):
 * маршруты и автостарт работают, но автору стоит поправить `routes.json`.
 */
const routes = ref<RouteDef[]>([])
const routesOk = ref(true)
const routesError = ref<string | null>(null)
const routesWarnings = ref<string[]>([])
/** Запрос `routes()` не удался (сеть или HTTP) — текста из файла в этом случае нет. */
const routesRequestFailed = ref(false)
const routesLoading = ref(false)
let routesRequested = false

/**
 * Вкладка «Маршруты» настроек (`RoutesSettings.vue`): правка/заведение/удаление/
 * порядок записей. Своё состояние загрузки и ошибки — общий алерт «Новой задачи»
 * (`routesError`) сюда не подмешиваем, вкладка живёт своей вкладкой.
 */
const routesSettingsLoading = ref(false)
const routesSettingsError = ref<string | null>(null)

/**
 * Помощник DeepSeek (`GET /api/assistant/status`): ключ живёт в конфиге сервера,
 * доска знает только «настроен или нет». Пока `assistantEnabled` не подтверждён
 * ответом сервера, кнопки у полей формы не рисуются; отказ запроса — тоже
 * «выключен» (доска не должна ломаться из-за необязательного помощника).
 * Статус запрашивается один раз за сессию — при первом открытии формы.
 */
const assistantEnabled = ref(false)
const assistantModel = ref('')
/**
 * Голосовой ввод (`voice: true` в статусе): `[deepgram]` и `[assistant]`
 * настроены. Пока флаг не подтверждён, кнопка записи не рисуется; отказ
 * запроса — «выключено».
 */
const voiceEnabled = ref(false)
const assistantLoading = ref(false)
let assistantRequested = false

let healthTimer: ReturnType<typeof setInterval> | null = null
let sseTimer: ReturnType<typeof setTimeout> | null = null
let unsubscribeStream: (() => void) | null = null

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Неизвестная ошибка'
}

function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.isUnauthorized
}

function isOffline(error: unknown): boolean {
  return error instanceof ApiError && error.isOffline
}

/** Один общий обработчик ошибок: 401 просит токен, отказ сети — плашку «сервер недоступен». */
function handleError(error: unknown): void {
  const message = errorMessage(error)
  lastError.value = message
  if (isUnauthorized(error)) {
    needsToken.value = true
    connectionLost.value = false
    return
  }
  if (isOffline(error)) {
    connectionLost.value = true
    return
  }
  connectionLost.value = false
}

function matchesFilters(task: Task, f: Filters, board: BoardFilters = { health: '', deps: 'all' }): boolean {
  if (f.needsOwner && !task.needs_owner) return false
  if (f.health === 'dead' && taskHealth(task) !== 'dead') return false
  if (f.health === 'at-risk' && taskHealth(task) !== 'at-risk') return false
  if (board.health === 'dead' && taskHealth(task) !== 'dead') return false
  if (board.health === 'at-risk' && taskHealth(task) !== 'at-risk') return false
  if (f.type && task.issue_type !== f.type) return false
  if (f.assignee) {
    const target = f.assignee === '—' ? null : f.assignee
    if ((task.assignee ?? null) !== target) return false
  }
  if (f.updatedFrom && task.updated_at < `${f.updatedFrom}T00:00:00`) return false
  if (f.updatedTo && task.updated_at > `${f.updatedTo}T23:59:59`) return false
  if (f.deps === 'blocked' && !(depsSummary.value[task.id]?.blockedBy.length ?? 0)) return false
  // «готовые к работе» — ровно то, что вернул /api/ready (нет блокеров и держателя)
  if (f.deps === 'ready' && !readyTasks.value.some((item) => item.id === task.id)) return false
  if (board.deps === 'blocked' && !(depsSummary.value[task.id]?.blockedBy.length ?? 0)) return false
  if (board.deps === 'ready' && !readyTasks.value.some((item) => item.id === task.id)) return false
  return true
}

/** Часть фильтров сервер для доски не умеет — эти применяются на клиенте. */
function needsClientFilter(f: Filters, board: BoardFilters = { health: '', deps: 'all' }): boolean {
  return Boolean(
    f.needsOwner || f.health || f.type || f.assignee || f.updatedFrom || f.updatedTo || f.deps !== 'all' ||
      board.health || board.deps !== 'all',
  )
}

function columnCounter(
  tasks: Task[],
): Pick<BoardColumn, 'count' | 'wip' | 'needs_owner' | 'stale' | 'blocked'> {
  return {
    count: tasks.length,
    wip: tasks.filter((task) => task.status === 'in_progress').length,
    needs_owner: tasks.filter((task) => task.needs_owner).length,
    // сервер в этом поле считает только stale; в интерфейсе показываем stale + abandoned
    stale: tasks.filter((task) => task.stale || task.abandoned).length,
    // считаем по графу: у задачи есть незакрытые жёсткие блокеры
    blocked: tasks.filter((task) => (depsSummary.value[task.id]?.blockedBy.length ?? 0) > 0).length,
  }
}

/**
 * Порядок колонок доски по этапу: «Заведена» первой, затем s1–s4, затем
 * «Готово» — сервер отдаёт `s1-spec, s2-review, s3-impl, s4-judge, none, done`.
 * Для остальных группировок порядок сервера не трогается.
 */
const STAGE_COLUMN_ORDER = [INTAKE_COLUMN_KEY, ...STAGES.map((item) => item.value)]

/** Окно рельсы/колонки «Готово» — те же 7 суток, что и в `loadDoneWeek()`. */
const DONE_WINDOW_MS = 7 * 24 * 3600_000

/** Закрыта в пределах окна «Готово» — общий критерий для рельсы и колонки
 *  `done`, чтобы бейдж рельсы и то, что открывается по клику на неё, совпадали. */
function isRecentlyDone(task: Task): boolean {
  return task.closed_at !== null && Date.parse(task.closed_at) >= Date.now() - DONE_WINDOW_MS
}

function orderColumns(source: BoardColumn[]): BoardColumn[] {
  const byKey = new Map(source.map((column) => [column.key, column]))
  const ordered: BoardColumn[] = []
  for (const key of STAGE_COLUMN_ORDER) {
    const found = byKey.get(key)
    if (found) {
      ordered.push(found)
      byKey.delete(key)
    }
  }
  // Незнакомые ключи (не должно случаться, но не теряем их молча) — в хвост.
  ordered.push(...byKey.values())
  return ordered
}

/** Колонки доски с учётом клиентских фильтров. */
const columns = computed<BoardColumn[]>(() => {
  const source = orderColumns(board.value?.columns ?? [])
  // счётчик «ждёт N» в колонке приходит из /api/blocked, поэтому колонки
  // пересобираются и без клиентских фильтров
  if (!needsClientFilter(filters, boardFilters)) {
    // «Готово» режем окном в 7 дней БЕЗУСЛОВНО (не только внутри клиентских
    // фильтров ниже) — иначе быстрый путь без фильтров показывал бы в колонке
    // все закрытые задачи сервера (до `limit: 300`), а рельса «Готово» рядом —
    // счётчик за 7 дней (`doneWeekCount`): два разных числа за одной и той же
    // подписью, путаница из-за которой и заведён этот пункт.
    return source.map((column) => {
      const tasks = column.key === 'done' ? column.tasks.filter(isRecentlyDone) : column.tasks
      return { ...column, tasks, ...columnCounter(tasks) }
    })
  }
  // Колонки этапов не должны исчезать из ряда, когда чип/фильтр опустошает их —
  // иначе сетка рельсы и колонок разъезжается.
  const keepEmpty = STAGE_COLUMN_ORDER
  return source
    .map((column) => {
      const tasks = column.tasks
        .filter((task) => matchesFilters(task, filters, boardFilters))
        .filter((task) => column.key !== 'done' || isRecentlyDone(task))
      return { ...column, tasks, ...columnCounter(tasks) }
    })
    .filter((column) => column.tasks.length > 0 || keepEmpty.includes(column.key))
})

const allBoardTasks = computed<Task[]>(() => (board.value?.columns ?? []).flatMap((column) => column.tasks))

/**
 * Инбокс «Ты нужен»: объединение серверного `board.needs_you` (needs_owner /
 * stale / abandoned) и задач, ставших at-risk по heartbeat на клиенте (сервер
 * этот срез отдельно не считает). Без дублей по id; порядок — needs_owner,
 * затем dead, затем at-risk, внутри группы — по убыванию idle_hours.
 */
const inbox = computed<Task[]>(() => {
  const fromServer = board.value?.needs_you ?? []
  const atRisk = allBoardTasks.value.filter(
    (task) => taskHealth(task) === 'at-risk' && (task.idle_hours ?? 0) >= AT_RISK_IDLE_HOURS,
  )
  const seen = new Set<string>()
  const merged: Task[] = []
  for (const task of [...fromServer, ...atRisk]) {
    if (seen.has(task.id)) continue
    seen.add(task.id)
    merged.push(task)
  }
  const filtered = needsClientFilter(filters, boardFilters)
    ? merged.filter((task) => matchesFilters(task, filters, boardFilters))
    : merged
  const rank = (task: Task): number => {
    if (task.needs_owner) return 0
    if (taskHealth(task) === 'dead') return 1
    return 2
  }
  return filtered.slice().sort((left, right) => {
    const rankDiff = rank(left) - rank(right)
    if (rankDiff !== 0) return rankDiff
    // У «выдана, но не взята» heartbeat ещё не было: её возраст — от выдачи.
    const age = (task: Task): number => task.idle_hours ?? task.assigned_hours ?? 0
    return age(right) - age(left)
  })
})

/** Текст последнего вопроса (kind=question) по id задачи инбокса; null — загружен, но пуст. */
const inboxQuestions = ref<Record<string, string | null>>({})

/**
 * Подгружает текст последнего вопроса для первых 12 задач инбокса с needs_owner.
 * Не блокирует общий `loading`: вызывается из `refresh()` уже после того, как
 * `board` пришёл (нужен для списка инбокса), отдельным заходом.
 */
async function loadInboxQuestions(): Promise<void> {
  const targets = inbox.value.filter((task) => task.needs_owner).slice(0, 12)
  await Promise.all(
    targets.map(async (task) => {
      try {
        const detailData = await api.task(task.id)
        const questions = detailData.comments.filter((comment) => comment.kind === 'question')
        const last = questions.reduce<(typeof questions)[number] | undefined>((latest, candidate) => {
          if (!latest) return candidate
          const byDate = Date.parse(candidate.created_at) - Date.parse(latest.created_at)
          if (byDate !== 0) return byDate > 0 ? candidate : latest
          // При равном created_at (секундная точность) сервер пишет комментарии по порядку —
          // берём тот, что стоит позже в ответе API (детерминированно, без опоры на сортировку).
          return candidate
        }, undefined)
        inboxQuestions.value = { ...inboxQuestions.value, [task.id]: last?.text ?? null }
      } catch {
        inboxQuestions.value = { ...inboxQuestions.value, [task.id]: null }
      }
    }),
  )
}

/** Срез зависимостей конкретной задачи (для карточки и списка). */
function depsFor(taskId: string): DepsSummary | null {
  return depsSummary.value[taskId] ?? null
}

const blockedCount = computed(() => board.value?.blocked_count ?? 0)
const readyCount = computed(() => board.value?.ready?.length ?? readyTasks.value.length)
const cycles = computed(() => board.value?.cycles ?? [])

/** Счётчики строки чипов: dead/atRisk — по taskHealth над всеми задачами колонок. */
const counts = computed(() => {
  const all = allBoardTasks.value
  return {
    dead: all.filter((task) => taskHealth(task) === 'dead').length,
    atRisk: all.filter((task) => taskHealth(task) === 'at-risk').length,
    /** Сколько задач стоит из-за других (считает сервер по графу). */
    blocked: board.value?.blocked_count ?? 0,
    /** Сколько можно взять прямо сейчас (board.ready). */
    ready: board.value?.ready?.length ?? 0,
    inPipeline: all.filter((task) => task.stage && PIPELINE_STAGE_KEYS.includes(task.stage)).length,
    total: board.value?.total ?? all.length,
  }
})

async function loadHealth(): Promise<void> {
  const data = await tryRequest(() => api.health(), handleError)
  if (data === null) {
    health.value = null
    return
  }
  health.value = data
  // Сохранённое имя могло исчезнуть из `server.users` (правка config.toml, другой
  // сервер): с ним сервер отвечает 400 на каждый список, поэтому снимаем его
  // сразу после health и до остальных запросов. Поля `users` нет вовсе (старый
  // сервер, ответ без токена) — не трогаем ничего.
  const known = data.users
  if (data.mode === 'server' && Array.isArray(known) && owner.value && !known.includes(owner.value)) {
    owner.value = ''
    writeStoredOwner('')
  }
  connectionLost.value = false
}

/** Серверный режим: есть список пользователей и владелец у задач (см. /api/health). */
const isServerMode = computed(() => health.value?.mode === 'server')

/** Кем можно представиться — `server.users`; в локальном режиме пусто. */
const users = computed<string[]>(() => health.value?.users ?? [])

/**
 * Сменить «я — …». Это только идентичность запросов: данные не меняются, ни
 * PATCH, ни POST не уходит — но списки фильтрует сервер по заголовку, поэтому
 * их надо перечитать. Поток SSE не трогаем: он заголовков не умеет и лишь
 * планирует тот же refresh.
 */
function setOwner(value: string): void {
  const next = value.trim()
  if (next === owner.value) return
  owner.value = next
  writeStoredOwner(next)
  void refresh({ silent: true })
}

async function loadMeta(): Promise<void> {
  const data = await tryRequest(() => api.meta(), handleError)
  if (data !== null) meta.value = data
}

async function loadBoard(): Promise<void> {
  board.value = await api.board({
    group_by: 'stage',
    project: filters.project || undefined,
    limit: 300,
  })
}

async function loadStats(): Promise<void> {
  stats.value = await api.stats(filters.project || undefined)
}

/**
 * Слой зависимостей одним заходом: /api/ready даёт «что можно брать» и счётчик
 * ждущих, /api/blocked — кого ждёт каждая стоящая задача и стоят ли её блокеры.
 * Оба ответа разворачиваются в плоскую карту по id, чтобы карточкам не делать
 * по запросу на каждую задачу.
 */
async function loadDeps(): Promise<void> {
  await withLoading(depsLoading, async () => {
    const result = await tryRequest(
      () =>
        Promise.all([
          api.ready({ project: filters.project || undefined, limit: 150 }),
          api.blocked({ project: filters.project || undefined, limit: 200 }),
        ]),
      (error) => {
        // Граф зависимостей — вспомогательный слой: его отсутствие не должно
        // помечать всю доску недоступной.
        if (isUnauthorized(error) || isOffline(error)) handleError(error)
      },
    )
    if (result === null) {
      depsSummary.value = {}
      readyTasks.value = []
      blockedTasks.value = []
      return
    }
    const [ready, blocked] = result
    readyTasks.value = ready.tasks
    blockedTasks.value = blocked.tasks

    const next: Record<string, DepsSummary> = {}
    for (const task of ready.tasks) {
      next[task.id] = {
        blockedBy: [],
        waitingForCount: task.waiting_for_count ?? 0,
        childrenOpen: 0,
        blockedByStale: false,
      }
    }
    for (const task of blocked.tasks) {
      const previous = next[task.id]
      next[task.id] = {
        blockedBy: task.blockers ?? [],
        waitingForCount: previous?.waitingForCount ?? 0,
        childrenOpen: previous?.childrenOpen ?? 0,
        blockedByStale: Boolean(task.blocked_by_stale),
      }
    }
    depsSummary.value = next
  })
}

/**
 * Сколько задач закрыто за последние 7 суток — счётчик рельсы «Готово», которая
 * заменяет обычную колонку, пока `include_closed` выключен. Слой вспомогательный:
 * ошибка не должна ронять доску (как и `loadDeps`).
 */
async function loadDoneWeek(): Promise<void> {
  const page = await tryRequest(
    () =>
      api.tasks({
        status: 'done',
        include_closed: true,
        order: 'updated',
        limit: 200,
        project: filters.project || undefined,
      }),
    (error) => {
      if (isUnauthorized(error) || isOffline(error)) handleError(error)
    },
  )
  if (page === null) {
    doneWeekCount.value = 0
    return
  }
  doneWeekCount.value = page.tasks.filter(isRecentlyDone).length
}

async function loadTimeline(): Promise<void> {
  const data = await api.timeline(200)
  timeline.value = data.items
}

/**
 * Телефон грузит только своё: health (для шапки) + meta (для селекта проекта),
 * а данные задач — отдельная очередь (см. `loadQueuePage`, вызывается из
 * `PhoneQueue.vue` по `queueTick`). Никаких board/stats/deps/doneWeek/timeline/
 * inboxQuestions — на телефоне нет ни доски, ни инбокса, ни здоровья конвейера.
 */
async function refreshPhone(options: { silent?: boolean } = {}): Promise<void> {
  const refreshData = async (): Promise<void> => {
    await loadHealth()
    if (meta.value === null) await loadMeta()
    lastSyncAt.value = new Date().toISOString()
    queueTick.value += 1
  }
  try {
    if (options.silent) await refreshData()
    else await withLoading(loading, refreshData)
  } catch (error) {
    handleError(error)
  }
}

async function refresh(options: { silent?: boolean } = {}): Promise<void> {
  if (!token.value) {
    needsToken.value = true
    return
  }
  if (phone.value) {
    await refreshPhone(options)
    return
  }
  async function refreshData(): Promise<void> {
    // health — первым и отдельно: он приносит режим и список пользователей, и
    // только он снимает имя, которого сервер уже не знает. Уйди он в общий
    // Promise.all — списки ушли бы со старым именем и получили 400.
    await loadHealth()
    await Promise.all([loadBoard(), loadStats(), loadDeps(), loadDoneWeek()])
    if (meta.value === null) await loadMeta()
    // /api/timeline нужен только блоку «активность» в метриках — отдельной вкладки нет
    if (view.value === 'metrics') await loadTimeline()
    needsToken.value = false
    connectionLost.value = false
    lastError.value = null
    lastSyncAt.value = new Date().toISOString()
  }
  try {
    if (options.silent) await refreshData()
    else await withLoading(loading, refreshData)
  } catch (error) {
    handleError(error)
  }
  // Не в общем Promise.all выше: нужен уже загруженный board, и не должен
  // задерживать снятие loading.
  void loadInboxQuestions()
}

/** Переключить режим телефона (вызывает App.vue из useIsPhone()). */
function setPhone(next: boolean): void {
  if (phone.value === next) return
  phone.value = next
  if (initialised) void refresh({ silent: true })
}

/**
 * Страница очереди телефона: своим запросом `/api/tasks`, без клиентских
 * фильтров и без status/include_closed/needs_owner/дат/здоровья — только
 * project (если выбран), limit/offset, order=updated. Успех снимает
 * needsToken/connectionLost/lastError (как раньше это делал общий refresh()).
 */
async function loadQueuePage(params: { limit: number; offset: number }): Promise<TasksPage | null> {
  const page = await tryRequest(
    () =>
      api.tasks({
        project: filters.project || undefined,
        limit: params.limit,
        offset: params.offset,
        order: 'updated',
      }),
    handleError,
  )
  if (page === null) return null
  needsToken.value = false
  connectionLost.value = false
  lastError.value = null
  return page
}

/**
 * id задач, которых коснулись события за окно дебаунса. `refresh()` обновляет
 * доску, статистику и граф, но не открытую карточку (`detail`), поэтому её
 * перечитываем отдельно — тихо, без сброса прокрутки и черновиков
 * (`reloadDetailQuiet`). События по чужим задачам открытую не трогают.
 */
let streamTouched = new Set<string>()

function scheduleRefresh(event?: StreamEvent): void {
  const id = event?.payload?.id
  const action = event?.payload?.action
  // Удалённую карточку перечитывать некуда: закрываем панель сразу, не дожидаясь
  // дебаунса — иначе `reloadDetailQuiet` глотает 404 и оставляет призрак.
  if (typeof id === 'string' && id && action === 'deleted' && openTaskId.value === id) {
    closeTask()
  }
  if (typeof id === 'string' && id) streamTouched.add(id)
  if (sseTimer) clearTimeout(sseTimer)
  sseTimer = setTimeout(() => {
    sseTimer = null
    const touched = streamTouched
    streamTouched = new Set()
    refresh({ silent: true }).catch(handleError)
    const open = openTaskId.value
    if (open && touched.has(open)) void reloadDetailQuiet(open)
  }, SSE_DEBOUNCE_MS)
}

function startStream(): void {
  stopStream()
  if (!token.value) return
  unsubscribeStream = subscribeStream(
    (event) => scheduleRefresh(event),
    (connected) => {
      live.value = connected
    },
  )
}

function stopStream(): void {
  unsubscribeStream?.()
  unsubscribeStream = null
  live.value = false
}

function startHealthPolling(): void {
  if (healthTimer) clearInterval(healthTimer)
  healthTimer = setInterval(() => {
    loadHealth().catch(handleError)
  }, HEALTH_POLL_MS)
}

async function setView(next: ViewKey): Promise<void> {
  view.value = next
  try {
    if (next === 'metrics') await loadTimeline()
    if (next === 'metrics' || next === 'list') await loadStats()
  } catch (error) {
    handleError(error)
  }
}

/** Клик по рельсе «Готово»: не колонка на доске, а список закрытых задач. */
async function openDoneList(): Promise<void> {
  filters.status = 'done'
  await setView('list')
}

function setToken(value: string): void {
  token.value = value.trim()
  writeStoredToken(token.value)
  needsToken.value = false
  stopStream()
  refresh()
    .then(() => {
      startStream()
    })
    .catch(handleError)
}

async function applyFilters(): Promise<void> {
  await refresh({ silent: true })
}

function clearFilters(): void {
  Object.assign(filters, emptyFilters())
  void refresh({ silent: true })
}

async function runSearch(): Promise<void> {
  const text = query.value.trim()
  if (!text) {
    searchResponse.value = null
    return
  }
  await withLoading(searchLoading, async () => {
    const result = await tryRequest(
      () =>
        api.search({
          q: text,
          mode: searchMode.value,
          limit: 20,
          project: filters.project || undefined,
          status: filters.status || undefined,
          stage: filters.stage || undefined,
        }),
      handleError,
    )
    if (result !== null) searchResponse.value = result
  })
}

function clearSearch(): void {
  query.value = ''
  searchResponse.value = null
}

/**
 * Открыть палитру поиска с заданным текстом (кнопка/Enter в тулбаре). Пустой
 * текст только открывает палитру, не трогая `query`/`searchResponse`: палитра
 * при каждом открытии сама обнуляет своё поле и эмитит `search('')` — если бы
 * пустая строка отсюда доходила до `runSearch`, она стирала бы уже показанные
 * результаты ещё до того, как пользователь начал вводить новый запрос.
 */
function openSearch(text: string): void {
  const trimmed = text.trim()
  if (trimmed) {
    query.value = trimmed
    void runSearch()
  }
  paletteOpen.value = true
}

/**
 * Номер последнего запроса карточки. Ответ «догоняющего» запроса (клик по ссылке, а
 * следом другая задача; перезагрузка карточки после действия; закрытие панели, пока
 * запрос в пути) не должен перезаписывать то, что открыли позже, — иначе панель
 * показывает не ту задачу, на которую кликнули, и выглядит это как «ссылка не работает».
 */
let detailRequest = 0

/** id из данных задачи: пробелы по краям в ссылке ломали бы поиск задачи. */
function normalizeTaskId(id: string): string {
  return id.trim()
}

/**
 * Карточка задачи одним запросом. Регистр id: канонические id строчные, но в
 * связях мог остаться id из импорта (WriterLLM) с заглавными буквами —
 * сервер ищет задачу по точному id, поэтому 404 повторяем в нижнем регистре.
 */
async function fetchDetail(id: string): Promise<TaskDetail> {
  try {
    return await api.task(id)
  } catch (error) {
    const lower = id.toLowerCase()
    if (!(error instanceof ApiError) || error.status !== 404 || lower === id) throw error
    return await api.task(lower)
  }
}

async function openTask(rawId: string): Promise<void> {
  const id = normalizeTaskId(rawId)
  if (!id) return
  const request = (detailRequest += 1)
  // Карточка, из которой уходим по ссылке: если переход не удался (задача удалена,
  // устаревшая связь), панель остаётся на ней, а не показывает пустой экран с ошибкой.
  const previous = detail.value
  openTaskId.value = id
  detail.value = null
  detailError.value = null
  detailLoading.value = true
  paletteOpen.value = false
  try {
    const data = await fetchDetail(id)
    if (request !== detailRequest) return
    detail.value = data
    // id от сервера: после перехода в другом регистре панель дальше работает с каноническим.
    openTaskId.value = data.id
  } catch (error) {
    if (request !== detailRequest) return
    detail.value = previous
    // Возвращаем id прежней карточки: действия панели и «Повторить» перезагружают
    // карточку по openTaskId и иначе били бы в несуществующую задачу.
    openTaskId.value = previous ? previous.id : id
    detailError.value = errorMessage(error)
    handleError(error)
  } finally {
    // Снимает загрузку только последний запрос: у «догоняющего» она уже снята новым.
    if (request === detailRequest) detailLoading.value = false
  }
}

/**
 * Тихое обновление уже открытой карточки по событию доски (listik-ch3v).
 * Обычный `openTask` на время запроса обнуляет `detail`, и панель на кадр
 * показывает скелет: прокрутка уезжает в начало, а поля с несохранённым
 * текстом перерисовываются. Здесь данные подменяются только готовым ответом,
 * `detailLoading`/`detailError` не трогаются, поэтому черновики и позиция
 * прокрутки остаются на месте.
 */
async function reloadDetailQuiet(rawId: string): Promise<void> {
  const id = normalizeTaskId(rawId)
  // Пока открытие в пути — оно само принесёт свежие данные, гонку не устраиваем.
  if (!id || id !== openTaskId.value || detailLoading.value) return
  const request = (detailRequest += 1)
  try {
    const data = await fetchDetail(id)
    // Ответ «догоняющего» запроса не должен перезаписывать карточку, открытую
    // позже, а задача в панели могла смениться, пока запрос летел.
    if (request !== detailRequest || openTaskId.value !== data.id) return
    detail.value = data
  } catch {
    // Карточка уже показана: неудачное тихое обновление не подменяет её ошибкой
    // и не стирает введённое. О недоступности сервера скажет refresh()/плашка.
  }
}

function closeTask(): void {
  // Запрос карточки, летящий в закрытую панель, применять некуда — гасим его номером.
  detailRequest += 1
  openTaskId.value = null
  detail.value = null
  detailError.value = null
  detailLoading.value = false
}

async function reloadDetail(): Promise<void> {
  if (openTaskId.value) await openTask(openTaskId.value)
}

/** Общий враппер действий задачи: pending-состояние, перезагрузка доски и деталей. */
async function act(label: string, action: () => Promise<unknown>): Promise<boolean> {
  pending.value = label
  try {
    await action()
    await refresh({ silent: true })
    await reloadDetail()
    lastError.value = null
    return true
  } catch (error) {
    handleError(error)
    return false
  } finally {
    pending.value = null
  }
}

const patchTask = (id: string, body: TaskPatch, label = 'update'): Promise<boolean> =>
  act(label, () => api.updateTask(id, body))

/** Дерево зависимостей задачи (POST …/deps без depends_on). */
async function loadDepTree(id: string, depth = 3): Promise<DepTree | null> {
  return tryRequest(() => api.depTree(id, depth), handleError)
}

const heartbeatTask = (id: string, holder: string, note?: string): Promise<boolean> =>
  act('heartbeat', () => api.heartbeat(id, holder, note))

const nextStage = (id: string, holder?: string, note?: string): Promise<boolean> =>
  act('stage', () => api.nextStage(id, holder, note))

/**
 * Вопрос/ответ с доски пишет человек: `needs-owner` кладёт текст в историю
 * комментарием, а без автора он достался бы не тому актору (listik-z0sd).
 * Автор — «я» доски: имя `owner` (server.users) в серверном режиме, иначе
 * человеческий ключ `me`. Явный `actor` (агентский `agent:<имя>`) сильнее.
 */
const setNeedsOwner = (id: string, value: boolean, note?: string, actor?: string): Promise<boolean> =>
  act('needs-owner', () => api.needsOwner(id, value, note, actor || owner.value || 'me'))

/** Ответ автора на вопрос: снимает флаг «нужен ты» и пишет комментарий kind=answer. */
const answerQuestion = (id: string, text: string): Promise<boolean> =>
  act('answer', () => api.needsOwner(id, false, text, owner.value || 'me'))

const releaseTask = (id: string, note?: string): Promise<boolean> => act('release', () => api.release(id, note))

const doneTask = (id: string, result: string, note?: string): Promise<boolean> =>
  act('done', () => api.done(id, result, undefined, note))

/**
 * Удалить задачу. `act()` после успеха перечитывает карточку — её уже нет,
 * поэтому закрываем панель сами и обновляем только доску.
 */
async function removeTask(id: string): Promise<boolean> {
  pending.value = 'remove'
  try {
    await api.removeTask(id)
    lastError.value = null
    if (openTaskId.value === id) closeTask()
    await refresh({ silent: true })
    return true
  } catch (error) {
    handleError(error)
    return false
  } finally {
    pending.value = null
  }
}

/**
 * Комментарий с доски пишет человек, а не держатель карточки: `author`, который
 * приходит из панели, — это `holder` задачи (им вполне может быть агент), в
 * авторы он не годится. Автор — «я» доски: имя `owner` (server.users) в серверном
 * режиме, иначе человеческий ключ `me` (как в локальном режиме, где списка нет).
 * Аргумент оставлен для совместимости с вызовом из App.vue и намеренно не используется.
 */
const addComment = (id: string, text: string, kind: CommentKind, _author?: string): Promise<boolean> =>
  act('comment', () => api.comment(id, text, kind, owner.value || 'me'))

const addDependency = (id: string, dependsOn: string, actor?: string): Promise<boolean> =>
  act('deps', () => api.addDep(id, dependsOn, 'blocks', actor))

const createTask = (body: Record<string, unknown>): Promise<boolean> => act('create', () => api.createTask(body))

/** Массовая правка выбранных задач: PATCH по каждой. */
async function bulkPatch(ids: string[], changes: TaskPatch): Promise<void> {
  pending.value = 'bulk'
  try {
    for (const id of ids) {
      await api.updateTask(id, changes)
    }
    lastError.value = null
  } catch (error) {
    handleError(error)
  } finally {
    pending.value = null
    await refresh({ silent: true })
  }
}

/**
 * Запрос маршрутов — ровно один на вызов. Старт доски (`init`) и первое открытие
 * формы зовут `ensureRoutes`, кнопка «повторить» — `loadRoutes`; результат (в том
 * числе ошибка) кешируется, повторный вызов запрос не повторяет.
 */
async function loadRoutes(): Promise<void> {
  if (routesLoading.value) return
  routesRequested = true
  await withLoading(routesLoading, async () => {
    try {
      const data = await api.routes()
      routesOk.value = data.ok
      routesError.value = data.error
      routesRequestFailed.value = false
      routesWarnings.value = data.warnings ?? []
      routes.value = data.routes ?? []
    } catch (error) {
      routesOk.value = false
      routesError.value = errorMessage(error)
      routesRequestFailed.value = true
      routesWarnings.value = []
      routes.value = []
    }
  })
}

/** Ленивая загрузка: одно обращение на сессию доски (старт доски и форма). */
function ensureRoutes(): void {
  if (routesRequested) return
  void loadRoutes()
}

/**
 * Вкладка «Маршруты» настроек: свежий список поверх общего `routes` — та же
 * иконка уровня и матрица маршрутов видят обновление сразу, но ошибка и флаг
 * загрузки отдельные (`routesSettingsError`/`routesSettingsLoading`), чтобы не
 * зажечь общий алерт формы «Новая задача».
 */
async function reloadRoutes(): Promise<boolean> {
  return withLoading(routesSettingsLoading, async () => {
    try {
      const data = await api.routes()
      routesOk.value = data.ok
      routesWarnings.value = data.warnings ?? []
      routes.value = data.routes ?? []
      routesRequested = true
      routesRequestFailed.value = false
      routesSettingsError.value = data.ok ? null : data.error
      return data.ok
    } catch (error) {
      routesSettingsError.value = errorMessage(error)
      return false
    }
  })
}

/** Общая обёртка действий вкладки «Маршруты»: ошибка — в `routesSettingsError`. */
async function routesSettingsAction<T>(action: () => Promise<T>): Promise<T | null> {
  return withLoading(routesSettingsLoading, async () => {
    const result = await tryRequest(action, (error) => {
      routesSettingsError.value = errorMessage(error)
    })
    if (result === null) return null
    routesSettingsError.value = null
    return result
  })
}

/**
 * Поправить запись (`title|hint|icon|visible|command`). Ответ сервера не несёт
 * `skill_path`/`skill_missing` (их добавляет только `GET /api/routes`), поэтому
 * после успеха список перечитывается целиком, а не патчится точечно.
 */
async function patchRoute(key: string, body: RoutePatch): Promise<RouteDef | null> {
  const result = await routesSettingsAction(() => api.patchRoute(key, body))
  if (result) await reloadRoutes()
  return result
}

/**
 * Статус помощника — ровно один запрос за сессию (`ensureAssistant` из формы).
 * Ошибка запроса не всплывает на доску: кнопки просто не показываются, а сама
 * причина видна в консоли — помощник необязателен.
 */
async function loadAssistant(): Promise<void> {
  if (assistantLoading.value) return
  assistantRequested = true
  await withLoading(assistantLoading, async () => {
    try {
      const status = await api.assistantStatus()
      assistantEnabled.value = status.enabled
      assistantModel.value = status.model
      voiceEnabled.value = status.voice === true
    } catch {
      assistantEnabled.value = false
      assistantModel.value = ''
      voiceEnabled.value = false
    }
  })
}

/** Ленивая загрузка при первом открытии формы создания задачи. */
function ensureAssistant(): void {
  if (assistantRequested) return
  void loadAssistant()
}

/**
 * Спросить помощника про одно поле формы. Ошибку не глотаем и в общий
 * `handleError` не отдаём: её показывает панель помощника у самого поля,
 * а таймаут DeepSeek не должен выглядеть как «сервер Listik недоступен».
 */
async function askAssistant(body: AssistantSuggestRequest): Promise<AssistantSuggestResponse> {
  return withLoading(assistantLoading, () => api.assistantSuggest(body))
}

/**
 * Расшифровать запись. Ошибку не глотаем и в общий `handleError` не отдаём:
 * её показывает панель записи (`errorMessage`), а отказ Deepgram не должен
 * выглядеть как «сервер Listik недоступен».
 */
async function transcribeVoice(body: VoiceTranscribeRequest): Promise<VoiceTranscribeResponse> {
  return withLoading(assistantLoading, () => api.assistantTranscribe(body))
}

/**
 * Собрать черновик задачи по расшифровке. Ошибка пробрасывается наружу — так
 * же, как у `transcribeVoice`: панель ловит её сама.
 */
async function draftVoice(body: VoiceDraftRequest): Promise<VoiceDraftResponse> {
  return withLoading(assistantLoading, () => api.assistantDraft(body))
}

/**
 * Репозитории доски (проекты). Доска показывает ровно те, что лежат в таблице
 * `projects` и не скрыты, поэтому «добавить репозиторий» и «убрать с доски» —
 * это операции над проектом, а не фильтр по задачам.
 */
async function loadProjects(): Promise<void> {
  await withLoading(projectsLoading, async () => {
    const data = await tryRequest(() => api.projects(), (error) => {
      projectsError.value = errorMessage(error)
    })
    if (data === null) return
    projects.value = data.projects
    projectsRoot.value = data.root
    projectsError.value = null
  })
}

async function projectAction<T>(
  action: () => Promise<T>,
  refresh: () => Promise<void>,
): Promise<T | null> {
  return withLoading(projectsLoading, async () => {
    const result = await tryRequest(action, (error) => {
      projectsError.value = errorMessage(error)
    })
    if (result === null) return null
    try {
      await refresh()
      projectsError.value = null
      return result
    } catch (error) {
      projectsError.value = errorMessage(error)
      return null
    }
  })
}

/**
 * Добавить каталог на доску. `path` — путь к репозиторию, `slug` — куда положить.
 *
 * Возвращает ответ `POST /api/projects` (в нём итоговый `path` и
 * `path_adjusted_from`, если каталог лежал внутри репозитория и его привели к
 * корню) — по нему форма говорит, куда именно добавлен проект. Ошибка — null.
 */
async function addProject(body: { path: string; slug?: string; title?: string }): Promise<ProjectRow | null> {
  return projectAction(
    () =>
      api.addProject({
        path: body.path,
        slug: body.slug || undefined,
        title: body.title || undefined,
      }),
    () => Promise.all([loadProjects(), loadMeta(), loadBoard()]).then(() => undefined),
  )
}

/**
 * Поправить репозиторий: название и путь (`PATCH /api/projects/<slug>`).
 *
 * Возвращает сохранённый проект, а не просто «получилось»: на ответе форма
 * показывает, что именно записалось, и видит `path_exists` — каталог мог
 * исчезнуть, тогда доска пометит проект «нет каталога». Ошибка — null,
 * текст в `projectsError`.
 */
async function updateProject(slug: string, body: ProjectPatch): Promise<ProjectRow | null> {
  return projectAction(
    () => api.updateProject(slug, body),
    () => Promise.all([loadProjects(), loadMeta(), loadBoard()]).then(() => undefined),
  )
}

/** Скрыть репозиторий с доски или вернуть обратно: задачи при этом не теряются. */
async function setProjectArchived(slug: string, archived: boolean): Promise<boolean> {
  const result = await projectAction(
    () => api.setProjectArchived(slug, archived),
    () => Promise.all([loadProjects(), loadMeta(), loadBoard(), loadStats()]).then(() => undefined),
  )
  return result !== null
}

/** Убрать репозиторий совсем. Задачи уносит только `force` — сервер иначе откажет. */
async function removeProject(slug: string, force = false): Promise<boolean> {
  const result = await projectAction(
    () => api.removeProject(slug, force),
    () => Promise.all([loadProjects(), loadMeta(), loadBoard(), loadStats()]).then(() => undefined),
  )
  return result !== null
}

async function importEmbeddings(): Promise<void> {
  await act('embed', () => api.embed(200))
}

async function loadListTasks(params: {
  limit: number
  offset: number
  order?: 'updated' | 'created' | 'priority' | 'stage'
}): Promise<{ tasks: Task[]; total: number }> {
  const page = await tryRequest(
    () =>
      api.tasks({
        project: filters.project || undefined,
        status: filters.status || undefined,
        stage: filters.stage || undefined,
        assignee: filters.assignee || undefined,
        needs_owner: filters.needsOwner || undefined,
        type: filters.type || undefined,
        limit: params.limit,
        offset: params.offset,
        order: params.order ?? 'updated',
      }),
    handleError,
  )
  if (page === null) {
    return { tasks: [], total: 0 }
  }
  let tasks = page.tasks
  if (filters.health) tasks = tasks.filter((task) => taskHealth(task) === filters.health)
  if (filters.updatedFrom) tasks = tasks.filter((task) => task.updated_at >= `${filters.updatedFrom}T00:00:00`)
  if (filters.updatedTo) tasks = tasks.filter((task) => task.updated_at <= `${filters.updatedTo}T23:59:59`)
  return { tasks, total: page.total }
}

function init(): void {
  // Все ветки получают обработчик отказа: необработанный reject в консоли —
  // такой же шум, как и ошибка, а показать её пользователю должен handleError.
  loadMeta().catch(handleError)
  // Маршруты нужны не только форме: карточки задач рисуют иконку уровня по
  // `launch_route`. Запрос один на сессию, ошибку разбирает сама форма.
  ensureRoutes()
  refresh()
    .then(() => {
      startStream()
    })
    .catch(handleError)
  startHealthPolling()
  initialised = true
}

function dispose(): void {
  stopStream()
  if (healthTimer) clearInterval(healthTimer)
  healthTimer = null
  if (sseTimer) clearTimeout(sseTimer)
  sseTimer = null
  streamTouched = new Set()
}

export function useListikStore() {
  return {
    // состояние
    token,
    owner,
    view,
    phone,
    queueTick,
    health,
    meta,
    board,
    stats,
    doneWeekCount,
    timeline,
    searchResponse,
    loading,
    searchLoading,
    lastError,
    connectionLost,
    needsToken,
    live,
    lastSyncAt,
    filters,
    boardFilters,
    query,
    searchMode,
    openTaskId,
    detail,
    detailLoading,
    detailError,
    paletteOpen,
    pending,
    projects,
    projectsRoot,
    projectsLoading,
    projectsError,
    routes,
    routesOk,
    routesError,
    routesWarnings,
    routesRequestFailed,
    routesLoading,
    routesSettingsLoading,
    routesSettingsError,
    assistantEnabled,
    assistantModel,
    assistantLoading,
    voiceEnabled,
    inboxQuestions,
    // производные
    isServerMode,
    users,
    columns,
    inbox,
    counts,
    allBoardTasks,
    depsSummary,
    readyTasks,
    blockedTasks,
    depsLoading,
    blockedCount,
    readyCount,
    cycles,
    depsFor,
    // загрузка
    init,
    dispose,
    refresh,
    loadHealth,
    loadMeta,
    loadTimeline,
    loadDeps,
    loadDoneWeek,
    loadDepTree,
    loadListTasks,
    loadQueuePage,
    loadInboxQuestions,
    runSearch,
    clearSearch,
    openSearch,
    setView,
    openDoneList,
    setPhone,
    setToken,
    setOwner,
    clearFilters,
    applyFilters,
    // действия
    openTask,
    closeTask,
    reloadDetail,
    patchTask,
    heartbeatTask,
    nextStage,
    setNeedsOwner,
    answerQuestion,
    releaseTask,
    doneTask,
    removeTask,
    addComment,
    addDependency,
    createTask,
    bulkPatch,
    loadRoutes,
    ensureRoutes,
    reloadRoutes,
    patchRoute,
    loadAssistant,
    ensureAssistant,
    askAssistant,
    transcribeVoice,
    draftVoice,
    loadProjects,
    addProject,
    updateProject,
    setProjectArchived,
    removeProject,
    importEmbeddings,
  }
}

export type ListikStore = ReturnType<typeof useListikStore>

/**
 * Стейт модульный — один на приложение, поэтому удобная точка входа нужна и вне
 * setup: `import store from '@/store/listik'`. `useListikStore()` остаётся для
 * компонентов, которые хотят обращаться к стейту «по-китовому».
 */
const store: ListikStore = useListikStore()

export default store

export { ApiError, errorMessage }
