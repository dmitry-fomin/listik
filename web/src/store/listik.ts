/**
 * Состояние доски: единственный владелец данных с сервера. Все запросы к API
 * проходят здесь, компоненты читают реактивные поля и вызывают действия.
 *
 * Стейт модульный (один на приложение), а `useListikStore()` — просто точка
 * доступа к нему, как `useToast()` в ките.
 */
import { computed, reactive, ref, shallowRef } from 'vue'
import { ApiError, api, subscribeStream } from '@/api/client'
import { readStoredToken, writeStoredToken } from '@/api/config'
import { AT_RISK_IDLE_HOURS, taskHealth } from '@/lib/health'
import { INTAKE_COLUMN_KEY, PIPELINE_STAGE_KEYS, STAGES } from '@/lib/dictionaries'
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
  ProjectRow,
  RouteDef,
  SearchMode,
  ReadyTask,
  SearchResponse,
  Stats,
  Task,
  TaskDetail,
  TaskPatch,
  TasksPage,
  TimelineItem,
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
const query = ref('')
const searchMode = ref<SearchMode>('hybrid')

const openTaskId = ref<string | null>(null)
const detail = ref<TaskDetail | null>(null)
const detailLoading = ref(false)
const detailError = ref<string | null>(null)

const paletteOpen = ref(false)
const pending = ref<string | null>(null)

/** Настройки доски: репозитории (проекты) — что показывать, что скрыто с доски. */
const projectsOpen = ref(false)
const projects = ref<ProjectRow[]>([])
const projectsRoot = ref('')
const projectsLoading = ref(false)
const projectsError = ref<string | null>(null)

/**
 * Маршруты запуска (`GET /api/routes`, файл `routes.json`): грузятся один раз за
 * сессию доски — при её старте (`init`, иконки уровней нужны карточкам задач) —
 * либо повторно кнопкой «повторить» (`loadRoutes`). Ошибка самого файла приходит
 * в ответе (`ok:false` + `error`), отказ запроса — исключением; в обоих случаях
 * форма показывает алерт и создаёт задачу без маршрута.
 */
const routes = ref<RouteDef[]>([])
const routesOk = ref(true)
const routesError = ref<string | null>(null)
/** Запрос `routes()` не удался (сеть или HTTP) — текста из файла в этом случае нет. */
const routesRequestFailed = ref(false)
const routesLoading = ref(false)
let routesRequested = false

/**
 * Помощник DeepSeek (`GET /api/assistant/status`): ключ живёт в конфиге сервера,
 * доска знает только «настроен или нет». Пока `assistantEnabled` не подтверждён
 * ответом сервера, кнопки у полей формы не рисуются; отказ запроса — тоже
 * «выключен» (доска не должна ломаться из-за необязательного помощника).
 * Статус запрашивается один раз за сессию — при первом открытии формы.
 */
const assistantEnabled = ref(false)
const assistantModel = ref('')
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

function matchesFilters(task: Task, f: Filters): boolean {
  if (f.needsOwner && !task.needs_owner) return false
  if (f.health === 'dead' && taskHealth(task) !== 'dead') return false
  if (f.health === 'at-risk' && taskHealth(task) !== 'at-risk') return false
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
  return true
}

/** Часть фильтров сервер для доски не умеет — эти применяются на клиенте. */
function needsClientFilter(f: Filters): boolean {
  return Boolean(
    f.needsOwner || f.health || f.type || f.assignee || f.updatedFrom || f.updatedTo || f.deps !== 'all',
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
  if (!needsClientFilter(filters)) {
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
        .filter((task) => matchesFilters(task, filters))
        .filter((task) => column.key !== 'done' || isRecentlyDone(task))
      return { ...column, tasks, ...columnCounter(tasks) }
    })
    .filter((column) => column.tasks.length > 0 || keepEmpty.includes(column.key))
})

const allBoardTasks = computed<Task[]>(() => (board.value?.columns ?? []).flatMap((column) => column.tasks))

/**
 * Инбокс «Нужен ты»: объединение серверного `board.needs_you` (needs_owner /
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
  const filtered = needsClientFilter(filters) ? merged.filter((task) => matchesFilters(task, filters)) : merged
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
  try {
    health.value = await api.health()
    connectionLost.value = false
  } catch (error) {
    health.value = null
    handleError(error)
  }
}

async function loadMeta(): Promise<void> {
  try {
    meta.value = await api.meta()
  } catch (error) {
    handleError(error)
  }
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
  depsLoading.value = true
  try {
    const [ready, blocked] = await Promise.all([
      api.ready({ project: filters.project || undefined, limit: 150 }),
      api.blocked({ project: filters.project || undefined, limit: 200 }),
    ])
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
  } catch (error) {
    // Граф зависимостей — вспомогательный слой: его отсутствие не должно
    // помечать всю доску недоступной.
    depsSummary.value = {}
    readyTasks.value = []
    blockedTasks.value = []
    if (isUnauthorized(error) || isOffline(error)) handleError(error)
  } finally {
    depsLoading.value = false
  }
}

/**
 * Сколько задач закрыто за последние 7 суток — счётчик рельсы «Готово», которая
 * заменяет обычную колонку, пока `include_closed` выключен. Слой вспомогательный:
 * ошибка не должна ронять доску (как и `loadDeps`).
 */
async function loadDoneWeek(): Promise<void> {
  try {
    const page = await api.tasks({
      status: 'done',
      include_closed: true,
      order: 'updated',
      limit: 200,
      project: filters.project || undefined,
    })
    doneWeekCount.value = page.tasks.filter(isRecentlyDone).length
  } catch (error) {
    doneWeekCount.value = 0
    if (isUnauthorized(error) || isOffline(error)) handleError(error)
  }
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
  if (!options.silent) loading.value = true
  try {
    await loadHealth()
    if (meta.value === null) await loadMeta()
    lastSyncAt.value = new Date().toISOString()
    queueTick.value += 1
  } finally {
    loading.value = false
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
  if (!options.silent) loading.value = true
  try {
    await Promise.all([loadBoard(), loadStats(), loadHealth(), loadDeps(), loadDoneWeek()])
    if (meta.value === null) await loadMeta()
    // /api/timeline нужен только блоку «активность» в метриках — отдельной вкладки нет
    if (view.value === 'metrics') await loadTimeline()
    needsToken.value = false
    connectionLost.value = false
    lastError.value = null
    lastSyncAt.value = new Date().toISOString()
  } catch (error) {
    handleError(error)
  } finally {
    loading.value = false
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
  try {
    const page = await api.tasks({
      project: filters.project || undefined,
      limit: params.limit,
      offset: params.offset,
      order: 'updated',
    })
    needsToken.value = false
    connectionLost.value = false
    lastError.value = null
    return page
  } catch (error) {
    handleError(error)
    return null
  }
}

function scheduleRefresh(): void {
  if (sseTimer) clearTimeout(sseTimer)
  sseTimer = setTimeout(() => {
    sseTimer = null
    refresh({ silent: true }).catch(handleError)
  }, SSE_DEBOUNCE_MS)
}

function startStream(): void {
  stopStream()
  if (!token.value) return
  unsubscribeStream = subscribeStream(
    () => scheduleRefresh(),
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
  searchLoading.value = true
  try {
    searchResponse.value = await api.search({
      q: text,
      mode: searchMode.value,
      limit: 20,
      project: filters.project || undefined,
      status: filters.status || undefined,
      stage: filters.stage || undefined,
    })
  } catch (error) {
    handleError(error)
  } finally {
    searchLoading.value = false
  }
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
    let data: TaskDetail
    try {
      data = await api.task(id)
    } catch (error) {
      // Регистр id: канонические id строчные, но в связях мог остаться id из импорта
      // (beads/WriterLLM) с заглавными буквами — сервер ищет задачу по точному id.
      const lower = id.toLowerCase()
      if (!(error instanceof ApiError) || error.status !== 404 || lower === id) throw error
      data = await api.task(lower)
    }
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

const claimTask = (id: string, holder: string, note?: string, force = false): Promise<boolean> =>
  act('claim', () => api.claim(id, holder, note, undefined, force))

/** Дерево зависимостей задачи (POST …/deps без depends_on). */
async function loadDepTree(id: string, depth = 3): Promise<DepTree | null> {
  try {
    return await api.depTree(id, depth)
  } catch (error) {
    handleError(error)
    return null
  }
}

const heartbeatTask = (id: string, holder: string, note?: string): Promise<boolean> =>
  act('heartbeat', () => api.heartbeat(id, holder, note))

const nextStage = (id: string, holder?: string, note?: string): Promise<boolean> =>
  act('stage', () => api.nextStage(id, holder, note))

const setNeedsOwner = (id: string, value: boolean, note?: string, actor?: string): Promise<boolean> =>
  act('needs-owner', () => api.needsOwner(id, value, note, actor))

/** Ответ автора на вопрос: снимает флаг «нужен ты» и пишет комментарий kind=answer. */
const answerQuestion = (id: string, text: string): Promise<boolean> =>
  act('answer', () => api.needsOwner(id, false, text))

const releaseTask = (id: string, note?: string): Promise<boolean> => act('release', () => api.release(id, note))

const doneTask = (id: string, result: string, note?: string): Promise<boolean> =>
  act('done', () => api.done(id, result, undefined, note))

const addComment = (id: string, text: string, kind: CommentKind, author?: string): Promise<boolean> =>
  act('comment', () => api.comment(id, text, kind, author))

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
  routesLoading.value = true
  try {
    const data = await api.routes()
    routesOk.value = data.ok
    routesError.value = data.error
    routesRequestFailed.value = false
    routes.value = data.routes ?? []
  } catch (error) {
    routesOk.value = false
    routesError.value = errorMessage(error)
    routesRequestFailed.value = true
    routes.value = []
  } finally {
    routesLoading.value = false
  }
}

/** Ленивая загрузка: одно обращение на сессию доски (старт доски и форма). */
function ensureRoutes(): void {
  if (routesRequested) return
  void loadRoutes()
}

/**
 * Статус помощника — ровно один запрос за сессию (`ensureAssistant` из формы).
 * Ошибка запроса не всплывает на доску: кнопки просто не показываются, а сама
 * причина видна в консоли — помощник необязателен.
 */
async function loadAssistant(): Promise<void> {
  if (assistantLoading.value) return
  assistantRequested = true
  assistantLoading.value = true
  try {
    const status = await api.assistantStatus()
    assistantEnabled.value = status.enabled
    assistantModel.value = status.model
  } catch {
    assistantEnabled.value = false
    assistantModel.value = ''
  } finally {
    assistantLoading.value = false
  }
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
  assistantLoading.value = true
  try {
    return await api.assistantSuggest(body)
  } finally {
    assistantLoading.value = false
  }
}

/**
 * Репозитории доски (проекты). Доска показывает ровно те, что лежат в таблице
 * `projects` и не скрыты, поэтому «добавить репозиторий» и «убрать с доски» —
 * это операции над проектом, а не фильтр по задачам.
 */
async function loadProjects(): Promise<void> {
  projectsLoading.value = true
  try {
    const data = await api.projects()
    projects.value = data.projects
    projectsRoot.value = data.root
    projectsError.value = null
  } catch (error) {
    projectsError.value = errorMessage(error)
  } finally {
    projectsLoading.value = false
  }
}

async function openProjects(): Promise<void> {
  projectsOpen.value = true
  await loadProjects()
}

/**
 * Добавить каталог на доску. `path` — путь к репозиторию, `slug` — куда положить.
 *
 * Возвращает ответ `POST /api/projects` (в нём итоговый `path` и
 * `path_adjusted_from`, если каталог лежал внутри репозитория и его привели к
 * корню) — по нему форма говорит, куда именно добавлен проект. Ошибка — null.
 */
async function addProject(body: { path: string; slug?: string; title?: string }): Promise<ProjectRow | null> {
  projectsLoading.value = true
  try {
    const project = await api.addProject({
      path: body.path,
      slug: body.slug || undefined,
      title: body.title || undefined,
    })
    await Promise.all([loadProjects(), loadMeta(), loadBoard()])
    projectsError.value = null
    return project
  } catch (error) {
    projectsError.value = errorMessage(error)
    return null
  } finally {
    projectsLoading.value = false
  }
}

/** Скрыть репозиторий с доски или вернуть обратно: задачи при этом не теряются. */
async function setProjectArchived(slug: string, archived: boolean): Promise<boolean> {
  projectsLoading.value = true
  try {
    await api.setProjectArchived(slug, archived)
    await Promise.all([loadProjects(), loadMeta(), loadBoard(), loadStats()])
    projectsError.value = null
    return true
  } catch (error) {
    projectsError.value = errorMessage(error)
    return false
  } finally {
    projectsLoading.value = false
  }
}

/** Убрать репозиторий совсем. Задачи уносит только `force` — сервер иначе откажет. */
async function removeProject(slug: string, force = false): Promise<boolean> {
  projectsLoading.value = true
  try {
    await api.removeProject(slug, force)
    await Promise.all([loadProjects(), loadMeta(), loadBoard(), loadStats()])
    projectsError.value = null
    return true
  } catch (error) {
    projectsError.value = errorMessage(error)
    return false
  } finally {
    projectsLoading.value = false
  }
}

async function importEmbeddings(): Promise<void> {
  await act('embed', () => api.embed(200))
}

async function loadListTasks(params: {
  limit: number
  offset: number
  order?: 'updated' | 'created' | 'priority' | 'stage'
}): Promise<{ tasks: Task[]; total: number }> {
  try {
    const page = await api.tasks({
      project: filters.project || undefined,
      status: filters.status || undefined,
      stage: filters.stage || undefined,
      assignee: filters.assignee || undefined,
      needs_owner: filters.needsOwner || undefined,
      type: filters.type || undefined,
      limit: params.limit,
      offset: params.offset,
      order: params.order ?? 'updated',
    })
    let tasks = page.tasks
    if (filters.health) tasks = tasks.filter((task) => taskHealth(task) === filters.health)
    if (filters.updatedFrom) tasks = tasks.filter((task) => task.updated_at >= `${filters.updatedFrom}T00:00:00`)
    if (filters.updatedTo) tasks = tasks.filter((task) => task.updated_at <= `${filters.updatedTo}T23:59:59`)
    return { tasks, total: page.total }
  } catch (error) {
    handleError(error)
    return { tasks: [], total: 0 }
  }
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
}

export function useListikStore() {
  return {
    // состояние
    token,
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
    query,
    searchMode,
    openTaskId,
    detail,
    detailLoading,
    detailError,
    paletteOpen,
    pending,
    projectsOpen,
    projects,
    projectsRoot,
    projectsLoading,
    projectsError,
    routes,
    routesOk,
    routesError,
    routesRequestFailed,
    routesLoading,
    assistantEnabled,
    assistantModel,
    assistantLoading,
    inboxQuestions,
    // производные
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
    clearFilters,
    applyFilters,
    // действия
    openTask,
    closeTask,
    reloadDetail,
    patchTask,
    claimTask,
    heartbeatTask,
    nextStage,
    setNeedsOwner,
    answerQuestion,
    releaseTask,
    doneTask,
    addComment,
    addDependency,
    createTask,
    bulkPatch,
    loadRoutes,
    ensureRoutes,
    loadAssistant,
    ensureAssistant,
    askAssistant,
    loadProjects,
    openProjects,
    addProject,
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
