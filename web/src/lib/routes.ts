/**
 * Правила маршрута — поверх данных `GET /api/routes` (записи `routes.json`,
 * тип `RouteDef`). Сами маршруты, их порядок, роли и иконки в коде не зашиты:
 * здесь только выбор по умолчанию, доступность по типу задачи, группировка
 * для матрицы (`RoutePicker`) и поиск записи по ключу. Метки
 * `harness:<x>`/`process:<y>` доска не считает: их выводит сервер из маршрута
 * (`routes.labels_for`) и он же переписывает при смене — они для человека и
 * поиска, а кто реально допущен до этапа, решает routing проекта.
 */
import type { DirectRouteDef, PipelineRouteDef, RouteDef } from '@/api/types'

/**
 * Пустая строка — снять маршрут на сервере (`set launch_route=` / PATCH
 * `route: ''`). `null` у выбора означает «ещё не выбрано» и на сервер не идёт.
 */
export const NO_ROUTE = ''

/** Эпику всегда нужно ТЗ (он режется на шаги через этап 1) — записи без `roles.spec` закрыты. */
export function pipelineAllowed(route: PipelineRouteDef, type: string): boolean {
  if (type !== 'epic') return true
  return Boolean(route.roles.spec)
}

export function directAllowed(type: string): boolean {
  return type !== 'epic'
}

/**
 * Разрешён ли маршрут типу задачи — одни и те же правила у «Новой задачи» и у
 * смены маршрута в карточке: эпику нужен этап ТЗ, прямой маршрут ему закрыт.
 */
export function routeAllowedForType(route: RouteDef, type: string): boolean {
  return route.kind === 'direct' ? directAllowed(type) : pipelineAllowed(route, type)
}

/**
 * Текст алерта «маршруты недоступны»: отказ запроса и ошибка самого файла
 * (`ok:false`) звучат по-разному, потому что причины разные.
 */
export function routesAlertText(requestFailed: boolean, error: string | null): string {
  const reason = error ?? 'неизвестная ошибка'
  if (requestFailed) return `${reason} — нужен ты`
  return `routes.json с ошибкой: ${reason} — нужен ты`
}

/**
 * Пресет по умолчанию зависит от типа: эпик крупнее и рискованнее — `high-pipeline`,
 * задаче и багу хватает `low-pipeline`. Если нужного ключа нет среди видимых
 * разрешённых, берётся первый видимый разрешённый `pipeline` в порядке ответа;
 * если и его нет — маршрут не выбран (`null`).
 */
export function defaultPipelineFor(type: string, routes: RouteDef[]): PipelineRouteDef | null {
  const allowed = routes.filter(
    (route): route is PipelineRouteDef =>
      route.kind === 'pipeline' && route.visible && pipelineAllowed(route, type),
  )
  const preferred = type === 'epic' ? 'high-pipeline' : 'low-pipeline'
  return allowed.find((route) => route.key === preferred) ?? allowed[0] ?? null
}

/**
 * Запись маршрута по ключу задачи (`launch_route`): иконку уровня и подпись
 * карточка берёт из неё. Ключа нет или записи в списке нет (маршрут убрали из
 * `routes.json` после заведения задачи) — `null`, иконка не рисуется.
 */
export function routeByKey(key: string | null | undefined, routes: RouteDef[]): RouteDef | null {
  if (!key) return null
  return routes.find((route) => route.key === key) ?? null
}

/** Видимые записи — ими рисуется матрица, скрытые (`visible:false`) в выбор не входят. */
export function visibleRoutesOf(routes: RouteDef[]): RouteDef[] {
  return routes.filter((route) => route.visible)
}

/**
 * Записи для матрицы: видимые, плюс текущий ключ, даже если он скрыт —
 * иначе выбранный маршрут пропал бы из выбора (как раньше отдельной
 * строкой селекта).
 */
export function pickerRoutesOf(
  routes: RouteDef[],
  selectedKey: string | null | undefined,
): RouteDef[] {
  const visible = visibleRoutesOf(routes)
  const current = routeByKey(selectedKey, routes)
  if (current && !visible.some((route) => route.key === current.key)) {
    return [...visible, current]
  }
  return visible
}

/** Строки таблицы ролей — все пресеты конвейера. */
export function pipelineRowsOf(routes: RouteDef[]): PipelineRouteDef[] {
  return routes.filter((route): route is PipelineRouteDef => route.kind === 'pipeline')
}

export function directRoutesOf(routes: RouteDef[]): DirectRouteDef[] {
  return routes.filter((route): route is DirectRouteDef => route.kind === 'direct')
}

// ── подстановки команды (`command`, argv записи маршрута) ───────────────────
// Правила и сам набор — `listik/routes.py` (`PLACEHOLDERS`, `PLACEHOLDER_RE`):
// в argv допустимы только эти шесть имён в фигурных скобках, любое другое —
// «незнакомая подстановка». Здесь — то же самое для карточки маршрута:
// разбор строки на куски для подсветки (`splitPlaceholders`), подсчёт
// вхождений для панели «Подстановки» (`countPlaceholders`) и список
// незнакомых имён для блока «Чем запускается» и порции `f` (`unknownPlaceholders`).

export const ROUTE_PLACEHOLDERS = ['task_id', 'project', 'route', 'cwd', 'worktree', 'branch'] as const
export type RoutePlaceholder = (typeof ROUTE_PLACEHOLDERS)[number]

const PLACEHOLDER_RE = /\{([^{}]*)\}/g

function commandText(argv: string[] | string | null | undefined): string {
  if (!argv) return ''
  return Array.isArray(argv) ? argv.join(' ') : argv
}

function isKnownPlaceholder(name: string): name is RoutePlaceholder {
  return (ROUTE_PLACEHOLDERS as readonly string[]).includes(name)
}

/** Кусок разобранной команды — для подсветки в шаблоне без выражений там. */
export interface PlaceholderChunk {
  type: 'text' | 'placeholder' | 'unknown'
  /** Текст куска (`text`) или имя подстановки без фигурных скобок (остальные). */
  value: string
}

/** Разбирает строку на текст и подстановки — рендер решает подсветку по `type`. */
export function splitPlaceholders(text: string): PlaceholderChunk[] {
  const chunks: PlaceholderChunk[] = []
  let lastIndex = 0
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    const index = match.index ?? 0
    if (index > lastIndex) chunks.push({ type: 'text', value: text.slice(lastIndex, index) })
    const name = match[1] ?? ''
    chunks.push({ type: isKnownPlaceholder(name) ? 'placeholder' : 'unknown', value: name })
    lastIndex = index + match[0].length
  }
  if (lastIndex < text.length) chunks.push({ type: 'text', value: text.slice(lastIndex) })
  return chunks
}

/** Сколько раз `{name}` встречается в команде — панель «Подстановки». */
export function countPlaceholders(argv: string[] | string | null | undefined, name: string): number {
  const text = commandText(argv)
  const token = `{${name}}`
  if (!token || text.length === 0) return 0
  let count = 0
  let index = text.indexOf(token)
  while (index !== -1) {
    count += 1
    index = text.indexOf(token, index + token.length)
  }
  return count
}

/** Имена подстановок в команде, которых нет в `ROUTE_PLACEHOLDERS` — такая команда невалидна. */
export function unknownPlaceholders(argv: string[] | string | null | undefined): string[] {
  const text = commandText(argv)
  if (!text) return []
  const found = new Set<string>()
  for (const match of text.matchAll(PLACEHOLDER_RE)) {
    const name = match[1] ?? ''
    if (!isKnownPlaceholder(name)) found.add(name)
  }
  return [...found]
}
