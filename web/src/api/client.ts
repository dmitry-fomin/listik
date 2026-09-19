import { apiUrl, readStoredOwner, readStoredToken } from './config'
import type {
  AssistantStatus,
  AssistantSuggestRequest,
  AssistantSuggestResponse,
  BlockedResponse,
  Board,
  CommentKind,
  GroupBy,
  Health,
  DepTree,
  DepsState,
  Meta,
  ProjectPatch,
  ProjectRemoved,
  ProjectRow,
  ProjectsResponse,
  ReadyResponse,
  RouteDef,
  RoutePatch,
  RoutesResponse,
  SearchMode,
  SearchResponse,
  Stats,
  StreamEvent,
  Task,
  TaskDetail,
  TaskPatch,
  TaskQuery,
  TasksPage,
  TimelineItem,
  VoiceDraftRequest,
  VoiceDraftResponse,
  VoiceTranscribeRequest,
  VoiceTranscribeResponse,
} from './types'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }

  /** Сервер недоступен (сеть, отказ соединения) — не ответ с ошибкой, а его отсутствие. */
  get isOffline(): boolean {
    return this.status === 0
  }

  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

interface Envelope<T> {
  ok: boolean
  data?: T
  error?: string
}

type QueryValue = string | number | boolean | undefined | null

function buildQuery(params: Record<string, QueryValue>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const qs = search.toString()
  return qs ? `?${qs}` : ''
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = readStoredToken()
  if (token) headers.Authorization = `Bearer ${token}`
  // Идентичность «я — …»: сервер фильтрует списки по владельцу и запрещает брать
  // чужое. Заголовок уходит на любой метод и в любом режиме — в локальном сервер
  // его просто игнорирует, поэтому условий на `mode` тут нет.
  const owner = readStoredOwner()
  if (owner) headers['X-Listik-Owner'] = owner
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let response: Response
  try {
    response = await fetch(apiUrl(path), {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch {
    throw new ApiError(0, 'Сервер Listik недоступен: проверьте, что запущен ./bin/listik serve')
  }

  const raw = await response.text()
  let parsed: Envelope<T> | null = null
  if (raw) {
    try {
      parsed = JSON.parse(raw) as Envelope<T>
    } catch {
      parsed = null
    }
  }

  if (!response.ok || (parsed && parsed.ok === false)) {
    const message = parsed?.error || raw.slice(0, 300) || `HTTP ${response.status}`
    throw new ApiError(response.status, message)
  }
  if (!parsed || parsed.data === undefined) {
    throw new ApiError(response.status, 'Пустой ответ сервера')
  }
  return parsed.data
}

const get = <T>(path: string): Promise<T> => request<T>('GET', path)
const post = <T>(path: string, body: unknown): Promise<T> => request<T>('POST', path, body ?? {})
const patch = <T>(path: string, body: unknown): Promise<T> => request<T>('PATCH', path, body ?? {})
const taskPath = (id: string, suffix = '') => `/api/tasks/${encodeURIComponent(id)}${suffix}`

export const api = {
  health: () => get<Health>('/api/health'),

  /** Маршруты запуска — таблица `routes` в базе (см. docs/API.md «Маршруты запуска»). */
  routes: () => get<RoutesResponse>('/api/routes'),

  /**
   * Правка записи маршрута: `title|hint|icon|visible` у обоих видов, `command` —
   * только у `kind=direct` (сервер отвечает `400` на `command` у `pipeline`).
   */
  patchRoute: (key: string, body: RoutePatch) =>
    patch<RouteDef>(`/api/routes/${encodeURIComponent(key)}`, body),

  /**
   * Настроен ли помощник DeepSeek (`[assistant]` в config.toml). Ключ наружу не
   * отдаётся: доска прячет кнопки, когда `enabled=false`.
   */
  assistantStatus: () => get<AssistantStatus>('/api/assistant/status'),

  /**
   * Спросить помощника про одно поле формы: переписать текст, дописать приёмку,
   * оценить сложность и предложить маршрут. Запрос уходит на сервер Listik —
   * ключ DeepSeek в браузер не попадает.
   */
  assistantSuggest: (body: AssistantSuggestRequest) =>
    post<AssistantSuggestResponse>('/api/assistant/suggest', body),

  /**
   * Расшифровать запись через сервер Listik (Deepgram): в браузер ни ключ, ни
   * запрос к провайдеру не попадают. Тишина — не ошибка: пустой `transcript`.
   */
  assistantTranscribe: (body: VoiceTranscribeRequest) =>
    post<VoiceTranscribeResponse>('/api/assistant/transcribe', body),

  /**
   * Собрать черновик задачи из расшифровки через сервер Listik (DeepSeek).
   * Черновик ничего не создаёт — форму заполняет человек.
   */
  assistantDraft: (body: VoiceDraftRequest) =>
    post<VoiceDraftResponse>('/api/assistant/draft', body),

  meta: (archived = false) => get<Meta>(`/api/meta${buildQuery({ archived })}`),

  stats: (project?: string) => get<Stats>(`/api/stats${buildQuery({ project })}`),

  board: (params: {
    group_by?: GroupBy
    project?: string
    include_closed?: boolean
    limit?: number
  }) => get<Board>(`/api/board${buildQuery({ ...params })}`),

  tasks: (query: TaskQuery = {}) => get<TasksPage>(`/api/tasks${buildQuery({ ...query })}`),

  task: (id: string, details = true) =>
    get<TaskDetail>(taskPath(id, buildQuery({ details: details ? 1 : 0 }))),

  search: (params: {
    q: string
    mode?: SearchMode
    limit?: number
    project?: string
    status?: string
    stage?: string
    actor?: string
    needs_owner?: boolean
  }) => get<SearchResponse>(`/api/search${buildQuery({ ...params })}`),

  timeline: (limit = 100) => get<{ items: TimelineItem[] }>(`/api/timeline${buildQuery({ limit })}`),

  /** Что можно взять прямо сейчас: нет незакрытых блокеров и держателя. */
  ready: (params: { project?: string; stage?: string; include_occupied?: boolean; limit?: number } = {}) =>
    get<ReadyResponse>(`/api/ready${buildQuery({ ...params })}`),

  /** Задачи, которые стоят из-за других, с разбором по каждому блокеру. */
  blocked: (params: { project?: string; limit?: number } = {}) =>
    get<BlockedResponse>(`/api/blocked${buildQuery({ ...params })}`),

  /** Вердикт по задаче отдельным запросом (то же, что `deps_state` в карточке). */
  taskReady: (id: string) => post<DepsState>(taskPath(id, '/ready'), {}),

  /** Дерево зависимостей: `deps` без `depends_on`. */
  depTree: (id: string, depth = 3) =>
    post<DepTree>(taskPath(id, '/deps'), { depth }),

  createTask: (body: Record<string, unknown>) => post<Task>('/api/tasks', body),

  updateTask: (id: string, body: TaskPatch) =>
    patch<Task | (Task & { unchanged?: boolean })>(taskPath(id), body),

  removeTask: (id: string) => request<{ deleted: string }>('DELETE', taskPath(id)),

  claim: (id: string, holder: string, note?: string, harness?: string, force = false) =>
    post<Task>(taskPath(id, '/claim'), { holder, note, harness, force }),

  heartbeat: (id: string, holder: string, note?: string) =>
    post<Task>(taskPath(id, '/heartbeat'), { holder, note }),

  nextStage: (id: string, holder?: string, note?: string, harness?: string) =>
    post<Task>(taskPath(id, '/stage'), { holder, note, harness }),

  comment: (id: string, text: string, kind: CommentKind, author?: string, harness?: string) =>
    post<TaskCommentResponse>(taskPath(id, '/comment'), {
      text,
      kind,
      author,
      harness,
    }),

  needsOwner: (id: string, value: boolean, note?: string, actor?: string) =>
    post<Task>(taskPath(id, '/needs-owner'), { value, note, actor }),

  release: (id: string, note?: string, actor?: string) =>
    post<Task>(taskPath(id, '/release'), { note, actor }),

  done: (id: string, result: string, actor?: string, note?: string) =>
    post<Task>(taskPath(id, '/done'), { result, actor, note }),

  addDep: (id: string, dependsOn: string, depType = 'blocks', actor?: string) =>
    post<unknown>(taskPath(id, '/deps'), {
      depends_on: dependsOn,
      dep_type: depType,
      actor,
    }),

  embed: (limit = 0, kinds = 'task,comment') =>
    post<Record<string, unknown>>('/api/embed', { limit, kinds }),

  // --- репозитории (проекты) доски: настройка «добавить / изменить / убрать»

  /** Все репозитории, включая скрытые с доски, и корень поиска проектов. */
  projects: () => get<ProjectsResponse>('/api/projects'),

  /** Добавить репозиторий на доску: путь к каталогу + необязательный slug/название. */
  addProject: (body: { path?: string; slug?: string; title?: string }) =>
    post<ProjectRow>('/api/projects', body),

  /**
   * Поправить репозиторий: название и/или путь. Slug — ключ проекта, в теле его
   * нет: `PATCH` ищет проект по slug из адреса и переименовывать не умеет.
   */
  updateProject: (slug: string, body: ProjectPatch) =>
    patch<ProjectRow>(`/api/projects/${encodeURIComponent(slug)}`, body),

  /** Скрыть проект с доски (`archived=true`) или вернуть обратно. */
  setProjectArchived: (slug: string, archived: boolean) =>
    patch<ProjectRow>(`/api/projects/${encodeURIComponent(slug)}`, { archived: archived ? 1 : 0 }),

  /** Убрать проект совсем; задачи уносит только `force`. */
  removeProject: (slug: string, force = false) =>
    request<ProjectRemoved>(
      'DELETE',
      `/api/projects/${encodeURIComponent(slug)}${force ? '?force=1' : ''}`,
    ),
}

export interface TaskCommentResponse {
  id?: number
  [key: string]: unknown
}

/**
 * Подписка на SSE `/api/stream`. Токен уходит параметром `?token=`
 * (EventSource не умеет заголовки). Возвращает функцию отписки.
 */
export function subscribeStream(
  onEvent: (event: StreamEvent) => void,
  onStatus?: (connected: boolean) => void,
): () => void {
  const token = readStoredToken()
  const query = token ? `?token=${encodeURIComponent(token)}` : ''
  const source = new EventSource(apiUrl(`/api/stream${query}`))

  source.onopen = () => onStatus?.(true)
  source.onmessage = (message: MessageEvent<string>) => {
    if (!message.data) return
    try {
      onEvent(JSON.parse(message.data) as StreamEvent)
    } catch {
      /* пинг или неразобранный кадр — не повод рвать соединение */
    }
  }
  source.onerror = () => onStatus?.(false)

  return () => {
    source.onopen = null
    source.onmessage = null
    source.onerror = null
    source.close()
  }
}
