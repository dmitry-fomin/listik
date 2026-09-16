/** База API и токен. Токен: ?token= в адресе, localStorage['listik.token'] либо VITE_LISTIK_TOKEN. */

export const TOKEN_STORAGE_KEY = 'listik.token'
const TOKEN_QUERY_KEY = 'token'

interface ImportMetaEnvLike {
  VITE_API_BASE?: string
  VITE_LISTIK_TOKEN?: string
}

const env = (import.meta as unknown as { env?: ImportMetaEnvLike }).env ?? {}

/**
 * База API.
 *
 * По умолчанию — тот же origin, под которым отдана страница: в проде сборку
 * отдаёт сам сервер Listik (`./bin/listik serve` → `web/dist`), а в dev
 * запросы `/api/*` уходят на 127.0.0.1:8787 через proxy из vite.config.ts.
 * Благодаря этому не зашит ни хост, ни порт, и SSE не упирается в CORS.
 *
 * `VITE_API_BASE` перекрывает это (например, API на другом хосте):
 * `VITE_API_BASE=http://192.168.1.10:8787 npm run dev`.
 */
export const API_BASE: string = (env.VITE_API_BASE ?? '').trim().replace(/\/+$/, '')

let memoryToken: string | null = null

/**
 * Токен из адреса: сервер Listik сам печатает ссылку вида
 * `http://127.0.0.1:8787/?token=<token>` (и отдаёт свой index.html), поэтому
 * приложение обязано этот параметр понимать. Найденный токен запоминается в
 * localStorage, а сам параметр убирается из адресной строки, чтобы он не
 * оставался в истории и в закладках.
 */
function readTokenFromUrl(): string {
  try {
    const url = new URL(window.location.href)
    const value = (url.searchParams.get(TOKEN_QUERY_KEY) ?? '').trim()
    if (!value) return ''
    writeStoredToken(value)
    url.searchParams.delete(TOKEN_QUERY_KEY)
    const cleaned = `${url.pathname}${url.search}${url.hash}`
    window.history.replaceState(window.history.state, '', cleaned || '/')
    return value
  } catch {
    return ''
  }
}

export function readStoredToken(): string {
  if (memoryToken !== null) return memoryToken
  let stored: string | null = null
  try {
    stored = window.localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    stored = null
  }
  const fromEnv = typeof env.VITE_LISTIK_TOKEN === 'string' ? env.VITE_LISTIK_TOKEN : ''
  memoryToken = (readTokenFromUrl() || stored || fromEnv || '').trim()
  return memoryToken
}

export function writeStoredToken(token: string): void {
  memoryToken = token.trim()
  try {
    if (memoryToken) window.localStorage.setItem(TOKEN_STORAGE_KEY, memoryToken)
    else window.localStorage.removeItem(TOKEN_STORAGE_KEY)
  } catch {
    /* localStorage недоступен — токен живёт только в памяти вкладки */
  }
}

/**
 * «Кто я» для сервера: имя из `server.users` уходит заголовком `X-Listik-Owner`
 * на каждый запрос (см. api/client.ts). Хранится там же, где токен — память
 * вкладки плюс localStorage, — но, в отличие от токена, ни из адреса, ни из
 * `VITE_*` не читается: имя выбирают селектом в шапке, а в локальном режиме его
 * вообще нет (сервер заголовок игнорирует).
 */
export const OWNER_STORAGE_KEY = 'listik.owner'

let memoryOwner: string | null = null

export function readStoredOwner(): string {
  if (memoryOwner !== null) return memoryOwner
  let stored: string | null = null
  try {
    stored = window.localStorage.getItem(OWNER_STORAGE_KEY)
  } catch {
    stored = null
  }
  memoryOwner = (stored ?? '').trim()
  return memoryOwner
}

export function writeStoredOwner(value: string): void {
  memoryOwner = value.trim()
  try {
    if (memoryOwner) window.localStorage.setItem(OWNER_STORAGE_KEY, memoryOwner)
    else window.localStorage.removeItem(OWNER_STORAGE_KEY)
  } catch {
    /* localStorage недоступен — имя живёт только в памяти вкладки */
  }
}

/** URL для пути API: относительный (тот же origin) либо с базой из VITE_API_BASE. */
export function apiUrl(path: string): string {
  const base = API_BASE
  const suffix = path.startsWith('/') ? path : `/${path}`
  return base ? `${base}${suffix}` : suffix
}
