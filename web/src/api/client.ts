import { apiUrl, readStoredToken } from './config'
import type {
  BlockedResponse,
  Board,
  CommentKind,
  GroupBy,
  Health,
  DepTree,
  DepsState,
  Meta,
  ProjectRemoved,
  ProjectRow,
  ProjectsResponse,
  ReadyResponse,
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

export const api = {
  health: () => get<Health>('/api/health'),

  /** Маршруты запуска из `routes.json` (загружены сервером при старте, без `command`). */
  routes: () => get<RoutesResponse>('/api/routes'),

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
    get<TaskDetail>(`/api/tasks/${encodeURIComponent(id)}${buildQuery({ details: details ? 1 : 0 })}`),

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
  taskReady: (id: string) => post<DepsState>(`/api/tasks/${encodeURIComponent(id)}/ready`, {}),

  /** Дерево зависимостей: `deps` без `depends_on`. */
  depTree: (id: string, depth = 3) =>
    post<DepTree>(`/api/tasks/${encodeURIComponent(id)}/deps`, { depth }),

  createTask: (body: Record<string, unknown>) => post<Task>('/api/tasks', body),

  updateTask: (id: string, body: TaskPatch) =>
    patch<Task | (Task & { unchanged?: boolean })>(`/api/tasks/${encodeURIComponent(id)}`, body),

  removeTask: (id: string) => request<{ deleted: string }>('DELETE', `/api/tasks/${encodeURIComponent(id)}`),

  claim: (id: string, holder: string, note?: string, harness?: string, force = false) =>
    post<Task>(`/api/tasks/${encodeURIComponent(id)}/claim`, { holder, note, harness, force }),

  heartbeat: (id: string, holder: string, note?: string) =>
    post<Task>(`/api/tasks/${encodeURIComponent(id)}/heartbeat`, { holder, note }),

  nextStage: (id: string, holder?: string, note?: string, harness?: string) =>
    post<Task>(`/api/tasks/${encodeURIComponent(id)}/stage`, { holder, note, harness }),

  comment: (id: string, text: string, kind: CommentKind, author?: string, harness?: string) =>
    post<TaskCommentResponse>(`/api/tasks/${encodeURIComponent(id)}/comment`, {
      text,
      kind,
      author,
      harness,
    }),

  needsOwner: (id: string, value: boolean, note?: string, actor?: string) =>
    post<Task>(`/api/tasks/${encodeURIComponent(id)}/needs-owner`, { value, note, actor }),

  release: (id: string, note?: string, actor?: string) =>
    post<Task>(`/api/tasks/${encodeURIComponent(id)}/release`, { note, actor }),

  done: (id: string, result: string, actor?: string, note?: string) =>
    post<Task>(`/api/tasks/${encodeURIComponent(id)}/done`, { result, actor, note }),

  addDep: (id: string, dependsOn: string, depType = 'blocks', actor?: string) =>
    post<unknown>(`/api/tasks/${encodeURIComponent(id)}/deps`, {
      depends_on: dependsOn,
      dep_type: depType,
      actor,
    }),

  embed: (limit = 0, kinds = 'task,comment') =>
    post<Record<string, unknown>>('/api/embed', { limit, kinds }),

  // --- репозитории (проекты) доски: настройка «добавить / убрать»

  /** Все репозитории, включая скрытые с доски, и корень поиска проектов. */
  projects: () => get<ProjectsResponse>('/api/projects'),

  /** Добавить репозиторий на доску: путь к каталогу + необязательный slug/название. */
  addProject: (body: { path?: string; slug?: string; title?: string }) =>
    post<ProjectRow>('/api/projects', body),

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
