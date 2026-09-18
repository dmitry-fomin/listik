/**
 * Минимальный роутер приложения: два экрана (доска `/` и настройки
 * `/settings/<раздел>`) на History API, без зависимостей — `vue-router` в проект
 * не берём, разбирать надо ровно один путь.
 *
 * Что здесь есть:
 * - `currentPath` — реактивный `pathname` (без query и hash): по нему `App.vue`
 *   решает, какой экран рисовать, а `SettingsPage.vue` — какой раздел;
 * - `navigate`/`replace`/`onLinkClick` — переходы. Ссылки в интерфейсе остаются
 *   настоящими `<a href>`, чтобы Cmd/Ctrl-клик и средняя кнопка открывали новую
 *   вкладку силами браузера; обычный клик перехватывается здесь;
 * - сторожа ухода (`registerLeaveGuard`): асинхронная проверка «можно ли уйти»
 *   (карточка с несохранёнными правками спрашивает подтверждение). Отказ
 *   отменяет и программный переход, и «назад»/«вперёд» браузера.
 *
 * Откат истории при отказе сторожа сделан одной стратегией: роутер нумерует свои
 * записи (`history.state.listikIdx`), на `popstate` считает, на сколько шагов
 * ушёл пользователь, и возвращает историю тем же числом шагов назад/вперёд
 * (`history.go(-delta)`). `history.go(1)` вслепую или `pushState` текущего пути
 * не годятся: первый не умеет «вперёд», второй плодит дубли записей. Собственный
 * `popstate`, вызванный откатом, гасится флагом `suppressNextPop`.
 *
 * `beforeunload` роутер не ставит: перезагрузка и закрытие вкладки — не его дело.
 */
import { ref, type Ref } from 'vue'

export type LeaveGuard = (to: string) => boolean | Promise<boolean>
export type SettingsSection = 'repos' | 'routes'

/** Раздел настроек по умолчанию: `/settings` без хвоста — это «Репозитории». */
const DEFAULT_SECTION: SettingsSection = 'repos'
const SECTIONS: readonly SettingsSection[] = ['repos', 'routes']

interface RouterHistoryState {
  listikIdx?: number
}

const pathRef = ref(readPath())

/** Текущий `pathname` без query и hash. Менять — только через `navigate`/`replace`. */
export const currentPath: Readonly<Ref<string>> = pathRef

const guards: LeaveGuard[] = []

/** Порядковый номер текущей записи истории — база для отката при отказе сторожа. */
let currentIdx = 0
/** Следующий `popstate` — наш собственный откат: разбирать его не нужно. */
let suppressNextPop = false

function readPath(): string {
  return window.location.pathname || '/'
}

/** Сегменты пути без пустых (ведущий и хвостовой слеш значения не имеют). */
function segmentsOf(path: string): string[] {
  return path.split('/').filter((segment) => segment.length > 0)
}

/**
 * Раздел настроек по пути — только по сегментам: `/settingsfoo` и `/settings-x`
 * это доска, а не «почти настройки» (`startsWith('/settings')` их бы перепутал).
 * Всё непонятное внутри настроек (`/settings/xxx`, `/settings/repos/extra`)
 * сводится к разделу по умолчанию, а не к 404: экран один, и показать его
 * полезнее, чем пустоту.
 */
export function settingsSectionOf(path: string): SettingsSection | null {
  const segments = segmentsOf(path)
  if (segments[0] !== 'settings') return null
  if (segments.length === 2) {
    const section = segments[1] as SettingsSection
    if (SECTIONS.includes(section)) return section
  }
  return DEFAULT_SECTION
}

/** Новое состояние истории: чужие ключи (их пишет, например, чтение токена) сохраняем. */
function stateWith(idx: number): RouterHistoryState {
  const previous = (window.history.state ?? {}) as RouterHistoryState
  return { ...previous, listikIdx: idx }
}

async function canLeave(to: string): Promise<boolean> {
  for (const guard of [...guards]) {
    if (!(await guard(to))) return false
  }
  return true
}

/**
 * Переход с записью в историю. Возвращает `false`, если уход запретил сторож:
 * тогда ни адрес, ни `currentPath` не меняются.
 */
export async function navigate(to: string): Promise<boolean> {
  const target = to || '/'
  // Переход «туда, где и так стоим» — не переход: ни записи в истории, ни опроса сторожей.
  const url = new URL(target, window.location.href)
  const here = `${window.location.pathname}${window.location.search}${window.location.hash}`
  if (`${url.pathname}${url.search}${url.hash}` === here) return true
  if (!(await canLeave(target))) return false
  currentIdx += 1
  window.history.pushState(stateWith(currentIdx), '', target)
  pathRef.value = readPath()
  return true
}

/** Подмена адреса без записи в историю. Сторожей не спрашивает: это не уход. */
export function replace(to: string): void {
  window.history.replaceState(stateWith(currentIdx), '', to || '/')
  pathRef.value = readPath()
}

/**
 * Клик по ссылке интерфейса: обычный левый клик ведём сами, клик с модификатором
 * или средней кнопкой отдаём браузеру — он откроет `href` в новой вкладке.
 */
export function onLinkClick(event: MouseEvent, path: string): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  void navigate(path)
}

/** Регистрирует сторожа ухода; возвращает функцию снятия. */
export function registerLeaveGuard(guard: LeaveGuard): () => void {
  guards.push(guard)
  return () => {
    const index = guards.indexOf(guard)
    if (index >= 0) guards.splice(index, 1)
  }
}

/**
 * Канонизация адреса настроек: `/settings`, `/settings/`, `/settings/repos/` и
 * `/settings/xxx` подменяются на `/settings/<раздел>`. Именно `replace`, а не
 * `navigate`: иначе «назад» упирался бы в редирект и не выпускал со страницы.
 * Query (`?token=…`) и hash сохраняются — токен из адреса снимает `api/config.ts`.
 */
function canonicalize(): void {
  const section = settingsSectionOf(readPath())
  if (section === null) return
  const canonical = `/settings/${section}`
  if (readPath() === canonical) return
  replace(`${canonical}${window.location.search}${window.location.hash}`)
}

async function onPopState(event: PopStateEvent): Promise<void> {
  if (suppressNextPop) {
    suppressNextPop = false
    return
  }
  const state = (event.state ?? {}) as RouterHistoryState
  const targetIdx = typeof state.listikIdx === 'number' ? state.listikIdx : 0
  const to = readPath()
  if (await canLeave(to)) {
    currentIdx = targetIdx
    pathRef.value = to
    canonicalize()
    return
  }
  const delta = targetIdx - currentIdx
  if (delta === 0) {
    // Сюда попадать неоткуда (сторож отказал уйти туда, где мы и так стоим),
    // но `history.go(0)` — перезагрузка страницы, поэтому просто ничего не делаем.
    pathRef.value = to
    return
  }
  suppressNextPop = true
  window.history.go(-delta)
}

function init(): void {
  const state = (window.history.state ?? {}) as RouterHistoryState
  if (typeof state.listikIdx === 'number') currentIdx = state.listikIdx
  else window.history.replaceState(stateWith(0), '', `${readPath()}${window.location.search}${window.location.hash}`)
  window.addEventListener('popstate', (event) => void onPopState(event))
  canonicalize()
  pathRef.value = readPath()
}

init()

// Механизм сторожей проверяется из консоли браузера — в прод-сборке ничего не выставляем.
if (import.meta.env.DEV) {
  ;(window as unknown as Record<string, unknown>).__listikRouter = {
    navigate,
    registerLeaveGuard,
    currentPath,
  }
}
