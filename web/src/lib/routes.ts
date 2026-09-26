/**
 * Правила маршрута — поверх данных `GET /api/routes` (записи `routes.json`,
 * тип `RouteDef`). Сами маршруты, их порядок, роли и иконки в коде не зашиты:
 * здесь только выбор по умолчанию, доступность по типу задачи, группировка
 * для матрицы (`RoutePicker`) и поиск записи по ключу. Метки
 * `harness:<x>`/`process:<y>` доска не считает: их выводит сервер из маршрута
 * (`routes.labels_for`) и он же переписывает при смене — они для человека и
 * поиска, а кто реально допущен до этапа, решает routing проекта.
 */
import type {
  PipelineRouteDef,
  RouteDef,
  SwarmLikeRoute,
} from '@/api/types'

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

/**
 * Рой (`kind = swarm`, listik-2gry): как у конвейера — эпику нужен этап ТЗ,
 * то есть заполненная ячейка `roles.spec`; остальным типам рой разрешён всегда.
 */
export function swarmAllowed(route: SwarmLikeRoute, type: string): boolean {
  if (type !== 'epic') return true
  return Boolean(route.roles.spec)
}

/**
 * Разрешён ли маршрут типу задачи — одни и те же правила у «Новой задачи» и у
 * смены маршрута в карточке: эпику нужен этап ТЗ.
 */
export function routeAllowedForType(route: RouteDef, type: string): boolean {
  if (route.kind === 'swarm' || route.driver === 'swarm') return swarmAllowed(route, type)
  return pipelineAllowed(route, type)
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

/**
 * Строки таблицы ролей — пресеты конвейера режима скила. Конвейер с
 * `driver='swarm'` исполняется роем — в таблицу конвейеров не входит, а в
 * группу «Рой» (`swarmRoutesOf`); иначе запись оказывалась бы в обеих группах.
 */
export function pipelineRowsOf(routes: RouteDef[]): PipelineRouteDef[] {
  return routes.filter(
    (route): route is PipelineRouteDef =>
      route.kind === 'pipeline' && route.driver !== 'swarm',
  )
}

/**
 * Роевые маршруты: `kind = swarm` и конвейеры с `driver='swarm'` (ячейки у
 * них — `SwarmRoleCell`). Секция «Рой» в пикере «как делать» и группа «Рой»
 * в настройках маршрутов.
 */
export function swarmRoutesOf(routes: RouteDef[]): SwarmLikeRoute[] {
  return routes.filter(
    (route): route is SwarmLikeRoute =>
      route.kind === 'swarm' || (route.kind === 'pipeline' && route.driver === 'swarm'),
  )
}

// ── подстановки команды (`command`, argv записи маршрута) ───────────────────
// Правила и сам набор — `listik/routes.py` (`PLACEHOLDERS`, `PLACEHOLDER_RE`):
// в argv допустимы только эти девять имён в фигурных скобках, любое другое —
// «незнакомая подстановка». Здесь — то же самое для карточки маршрута:
// разбор строки на куски для подсветки (`splitPlaceholders`), подсчёт
// вхождений для панели «Подстановки» (`countPlaceholders`) и список
// незнакомых имён для блока «Чем запускается» и порции `f` (`unknownPlaceholders`).

export const ROUTE_PLACEHOLDERS = [
  'task_id',
  'project',
  'route',
  'cwd',
  'worktree',
  'branch',
  'stage',
  'role',
  'harness',
] as const
export type RoutePlaceholder = (typeof ROUTE_PLACEHOLDERS)[number]

const PLACEHOLDER_RE = /\{([^{}]*)\}/g

function commandText(argv: string[] | string | null | undefined): string {
  if (!argv) return ''
  return Array.isArray(argv) ? argv.join(' ') : argv
}

/** Команда поэлементно: сервер проверяет каждый элемент argv отдельно, а не склейку. */
function commandElements(argv: string[] | string | null | undefined): string[] {
  if (!argv) return []
  return Array.isArray(argv) ? argv : [argv]
}

/** Скобки, оставшиеся после вырезания подстановок, — ровно проверка сервера. */
function bareBraces(value: string): string[] {
  const rest = value.replace(PLACEHOLDER_RE, '')
  return [...rest].filter((char) => char === '{' || char === '}')
}

/** `{name}` — строкой: буквальные `{}` в шаблонах Vue путают парсер, а тут они нужны. */
export function braced(name: string): string {
  return '{' + name + '}'
}

function isKnownPlaceholder(name: string): name is RoutePlaceholder {
  return (ROUTE_PLACEHOLDERS as readonly string[]).includes(name)
}

/** Кусок разобранной команды — для подсветки в шаблоне без выражений там. */
export interface PlaceholderChunk {
  /**
   * `text` — обычный текст, `placeholder` — одна из девяти допустимых,
   * `unknown` — `{имя}` не из набора, `brace` — голая скобка вне подстановки
   * (`{{`, одиночная `{`, `{foo`): сервер такую команду тоже не принимает.
   * `brace` выдаёт только `splitCommandChunks`; `splitPlaceholders` его не
   * возвращает — разбор команды, уже принятой сервером, в нём не нуждается.
   */
  type: 'text' | 'placeholder' | 'unknown' | 'brace'
  /** Текст куска (`text`), сама скобка (`brace`) или имя подстановки без фигурных скобок. */
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

/**
 * Всё, на что сервер ответит «неизвестная подстановка»/«фигурные скобки» —
 * именами подстановок и самими скобками. Повторяет `routes.validate_command`
 * поэлементно и в том же порядке: сначала «после вырезания подстановок осталась
 * скобка» (тогда в ответе `{`/`}`), потом имена вырезанных подстановок. Список
 * пуст ровно тогда, когда команду примет сервер, — поэтому по нему и решается,
 * слать ли `PATCH` вовсе (команда роли роя).
 */
export function unknownPlaceholders(argv: string[] | string | null | undefined): string[] {
  const found = new Set<string>()
  for (const element of commandElements(argv)) {
    for (const brace of bareBraces(element)) found.add(brace)
    for (const match of element.matchAll(PLACEHOLDER_RE)) {
      const name = match[1] ?? ''
      if (!isKnownPlaceholder(name)) found.add(name)
    }
  }
  return [...found]
}

/**
 * Причина отказа одного элемента argv (аргумента или промпта) словами сервера,
 * `null` — элемент годится. Порядок проверок — серверный: голая скобка раньше
 * незнакомого имени (`listik/routes.py`, `validate_command`), чтобы на одном и
 * том же вводе доска и сервер называли одну и ту же причину.
 */
export function commandProblemText(value: string): string | null {
  if (bareBraces(value).length > 0) {
    return `фигурные скобки допустимы только в подстановках ${ROUTE_PLACEHOLDERS.map(braced).join(', ')}`
  }
  for (const match of value.matchAll(PLACEHOLDER_RE)) {
    const name = match[1] ?? ''
    if (!isKnownPlaceholder(name)) return `неизвестная подстановка ${braced(name)}`
  }
  return null
}

/**
 * Разбор строки для подсветки в редакторе команды: то же, что
 * `splitPlaceholders`, плюс голые скобки отдельными кусками (`brace`) — в
 * редакторе автор видит и недописанную `{`, а не только целое `{имя}`.
 */
export function splitCommandChunks(text: string): PlaceholderChunk[] {
  const chunks: PlaceholderChunk[] = []
  for (const chunk of splitPlaceholders(text)) {
    if (chunk.type !== 'text') {
      chunks.push(chunk)
      continue
    }
    let plain = ''
    for (const char of chunk.value) {
      if (char === '{' || char === '}') {
        if (plain) chunks.push({ type: 'text', value: plain })
        plain = ''
        chunks.push({ type: 'brace', value: char })
        continue
      }
      plain += char
    }
    if (plain) chunks.push({ type: 'text', value: plain })
  }
  return chunks
}

// ── опасные флаги и предпросмотр команды ───────────────────────────────────

/**
 * Аргументы, снимающие подтверждения у харнесса: процесс получает полный доступ
 * к репозиторию. Доска ими ничего не запрещает и сама их не убирает — только
 * помечает («полный доступ к репозиторию»), чтобы автор оставлял такой флаг
 * осознанно.
 */
export const DANGEROUS_ARGS = [
  '--dangerously-skip-permissions',
  '--yolo',
  '--write',
  '--full-auto',
  '--dangerously-bypass-approvals-and-sandbox',
] as const

/** Флаг с `=значением` (`--write=all`) — тот же флаг, поэтому сравнение не только точное. */
export function isDangerousArg(value: string): boolean {
  const arg = value.trim()
  return DANGEROUS_ARGS.some((flag) => arg === flag || arg.startsWith(`${flag}=`))
}

/**
 * Значения подстановок «для примера» — общие у панели «Подстановки» и у
 * предпросмотра команды, поэтому живут здесь, а не в компоненте. `{route}` —
 * ключ самой записи, остальные из `docs/specs/routes-settings-ui.md`.
 */
const PLACEHOLDER_EXAMPLES: Record<string, string> = {
  task_id: 'listik-8jgz',
  project: 'listik',
  cwd: '/Users/dmitry.fomin/Projects/Listik',
  worktree: '/Users/dmitry.fomin/Projects/Listik/.worktrees/listik-8jgz',
  branch: 'listik-8jgz',
  stage: 's3-impl',
  role: 'impl',
  harness: 'claude',
}

export function placeholderExample(name: string, routeKey: string): string {
  if (name === 'route') return routeKey
  return PLACEHOLDER_EXAMPLES[name] ?? ''
}

/** Подстановки заменены примерными значениями — один элемент argv. */
export function previewArg(value: string, routeKey: string): string {
  return value.replace(PLACEHOLDER_RE, (whole, name: string) =>
    isKnownPlaceholder(name) ? placeholderExample(name, routeKey) : whole,
  )
}

/**
 * Тот же предпросмотр, что `previewArg`, но кусками для подсветки: допустимая
 * подстановка приходит куском `placeholder` с примерным значением в `value`,
 * незнакомое имя остаётся куском `unknown` (рендер показывает его сырым
 * `{имя}`). Элемент, чья подставленная строка содержит пробел, оборачивается
 * кавычками — то же правило, что у `previewCommand`.
 */
export function previewChunks(element: string, routeKey: string): PlaceholderChunk[] {
  const quoted = /\s/.test(previewArg(element, routeKey))
  const chunks = splitPlaceholders(element).map((chunk): PlaceholderChunk => {
    if (chunk.type === 'placeholder') {
      return { type: 'placeholder', value: placeholderExample(chunk.value, routeKey) }
    }
    return { type: chunk.type, value: quoted ? chunk.value.replace(/"/g, '\\"') : chunk.value }
  })
  if (!quoted) return chunks
  return [{ type: 'text', value: '"' }, ...chunks, { type: 'text', value: '"' }]
}

/**
 * Картинка того, что получится: элементы argv с подставленными примерными
 * значениями, соединённые пробелами; элемент с пробелом внутри — в кавычках,
 * чтобы было видно его границы. Это только показ: на сервер уходит массив
 * строк, shell не участвует и склейка нигде больше не используется.
 */
export function previewCommand(argv: string[], routeKey: string): string {
  return argv
    .map((element) => {
      const shown = previewArg(element, routeKey)
      return /\s/.test(shown) ? `"${shown.replace(/"/g, '\\"')}"` : shown
    })
    .join(' ')
}
