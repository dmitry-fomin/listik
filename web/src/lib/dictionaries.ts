/**
 * Справочники значений задачи — тип, приоритет, этап, статус. Единственное место,
 * где перечислены ключи, подписи, иконки и цвета: фильтры, модалка создания,
 * глифы, доска и витрина берут значения отсюда, а не держат свои копии.
 */
import type { AssistantComplexityLevel, CommentKind, PipelineStage, RouteIconKey, TaskStage, TaskStatus } from '@/api/types'

export interface DictionaryItem<T extends string | number> {
  value: T
  label: string
}

// ── Тип ─────────────────────────────────────────────────────────────────────

export type IssueType = 'epic' | 'task' | 'bug'

export interface TaskTypeItem extends DictionaryItem<IssueType> {
  /** Подпись с пояснением — в глифе и переключателе типа модалки. */
  hint: string
  createLabel: string
  icon: string
  color: string
}

export const TASK_TYPES: TaskTypeItem[] = [
  { value: 'epic', label: 'эпик', hint: 'эпик · ТЗ', createLabel: 'Создать эпик', icon: 'epic', color: 'var(--chart-3)' },
  { value: 'task', label: 'задача', hint: 'задача', createLabel: 'Создать задачу', icon: 'task', color: 'var(--info-500)' },
  { value: 'bug', label: 'баг', hint: 'баг', createLabel: 'Создать баг', icon: 'bug', color: 'var(--danger-500)' },
]

/** Незнакомый тип (старые импорты: feature, chore…) показываем как обычную задачу. */
export function taskType(value: string | null | undefined): TaskTypeItem {
  return TASK_TYPES.find((item) => item.value === value) ?? TASK_TYPES[1]
}

// ── Приоритет ───────────────────────────────────────────────────────────────

export type Priority = 0 | 1 | 2 | 3 | 4

export interface PriorityItem extends DictionaryItem<Priority> {
  icon: string
  color: string
}

export const PRIORITIES: PriorityItem[] = [
  { value: 0, label: 'блокер', icon: 'p0', color: 'var(--danger-500)' },
  { value: 1, label: 'критичный', icon: 'p1', color: 'var(--warning-600)' },
  { value: 2, label: 'обычный', icon: 'p2', color: 'var(--ink-3)' },
  { value: 3, label: 'низкий', icon: 'p3', color: 'var(--info-500)' },
  { value: 4, label: 'потом', icon: 'p4', color: 'var(--ink-4)' },
]

export const DEFAULT_PRIORITY: Priority = 2

/** Незнакомое значение — обычный приоритет. */
export function priority(value: number | string | null | undefined): PriorityItem {
  return PRIORITIES.find((item) => item.value === Number(value)) ?? PRIORITIES[DEFAULT_PRIORITY]
}

// ── Этап ────────────────────────────────────────────────────────────────────

export type PipelineCode = 's1' | 's2' | 's3' | 's4'

export interface PipelineStep extends DictionaryItem<PipelineStage> {
  code: PipelineCode
}

/** Этапы конвейера s1…s4 в порядке прохождения. */
export const PIPELINE_STAGES: PipelineStep[] = [
  { value: 's1-spec', code: 's1', label: 'ТЗ и чек-лист' },
  { value: 's2-review', code: 's2', label: 'Второе мнение' },
  { value: 's3-impl', code: 's3', label: 'Реализация' },
  { value: 's4-judge', code: 's4', label: 'Проверка' },
]

export const DONE_STAGE: DictionaryItem<'done'> = { value: 'done', label: 'Готово' }

/** Все этапы, которые может иметь задача: s1…s4 и «Готово». */
export const STAGES: DictionaryItem<PipelineStage | 'done'>[] = [...PIPELINE_STAGES, DONE_STAGE]

export const PIPELINE_STAGE_KEYS: string[] = PIPELINE_STAGES.map((step) => step.value)

/** Колонка доски для задач без этапа. */
export const INTAKE_COLUMN_KEY = 'none'

export function stageIndex(stage: TaskStage): number {
  const at = STAGES.findIndex((item) => item.value === stage)
  return at === -1 ? 0 : at
}

// ── Статус ──────────────────────────────────────────────────────────────────

export const STATUSES: DictionaryItem<TaskStatus>[] = [
  { value: 'open', label: 'открыта' },
  { value: 'in_progress', label: 'в работе' },
  { value: 'blocked', label: 'заблокирована' },
  { value: 'review', label: 'на проверке' },
  { value: 'done', label: 'готова' },
  { value: 'cancelled', label: 'отменена' },
]

export function statusTitle(value: TaskStatus): string {
  return STATUSES.find((item) => item.value === value)?.label ?? value
}

// ── Рабочее дерево (строка «worktree · branch» блока «Холодный старт») ──────

export type WorktreeStateValue = 'worktree' | 'main' | 'missing'

export interface WorktreeStateItem extends DictionaryItem<WorktreeStateValue> {
  /** Тон для `UiStatusPill`/`UiBadge` кита: зелёный — дерево указано, жёлтый — основная ветка, красный — пусто. */
  tone: 'success' | 'warning' | 'danger'
  /** Цвет текста значения — токен кита. */
  color: string
  /** Пояснение — в тултип строки. */
  hint: string
  /** Заполнено ли поле для счётчика «Холодный старт N из M»: жёлтое состояние считается заполненным. */
  filled: boolean
  /** Значение — путь (`true`, моноширинный) или слова состояния (`false`). */
  mono: boolean
}

/**
 * Основные ветки репозитория: их значение в `worktree` — маркер «работа идёт в
 * основной ветке, отдельного дерева нет». Тот же список на сервере —
 * `listik/store.py: MAIN_WORKTREE_MARKERS` (API.md, «Работа в основной ветке»).
 */
export const MAIN_BRANCHES = ['main', 'master']

export const WORKTREE_STATES: WorktreeStateItem[] = [
  {
    value: 'worktree',
    label: 'дерево указано',
    tone: 'success',
    color: 'var(--success-700)',
    hint: 'указаны рабочее дерево и/или ветка — работа идёт не в основной ветке',
    filled: true,
    mono: true,
  },
  {
    value: 'main',
    label: 'работа в основной ветке',
    tone: 'warning',
    color: 'var(--warning-700)',
    hint: 'работа идёт в основной ветке репозитория, отдельного дерева нет '
      + '(listik set <id> worktree=main) — поле холодного старта заполнено',
    filled: true,
    mono: false,
  },
  {
    value: 'missing',
    label: 'не указано',
    tone: 'danger',
    color: 'var(--danger-700)',
    hint: 'ни worktree, ни branch не заполнены — принимающий не знает, где искать работу',
    filled: false,
    mono: false,
  },
]

/** Маркер основной ветки (`main`/`master`) или `''` — как `store.main_worktree` на сервере. */
export function mainBranchMarker(value: string | null | undefined): string {
  const marker = (value ?? '').trim().toLowerCase()
  return MAIN_BRANCHES.includes(marker) ? marker : ''
}

/**
 * Состояние строки «worktree · branch»: маркер основной ветки (в `worktree` или,
 * если дерева нет, в `branch`) — жёлтый, любой другой непустой `worktree`/`branch` —
 * зелёный, оба пусты — красный.
 */
export function worktreeState(
  worktree: string | null | undefined,
  branch: string | null | undefined,
): WorktreeStateItem {
  const tree = (worktree ?? '').trim()
  const br = (branch ?? '').trim()
  if (mainBranchMarker(tree) || (!tree && mainBranchMarker(br))) return WORKTREE_STATES[1]
  if (tree || br) return WORKTREE_STATES[0]
  return WORKTREE_STATES[2]
}

/** Текст строки: «работа в main», «/путь · ветка» или «рабочее дерево не указано». */
export function worktreeValue(
  worktree: string | null | undefined,
  branch: string | null | undefined,
): string {
  const state = worktreeState(worktree, branch)
  const tree = (worktree ?? '').trim()
  const br = (branch ?? '').trim()
  if (state.value === 'missing') return 'рабочее дерево не указано'
  if (state.value === 'main') return `работа в ${mainBranchMarker(tree) || mainBranchMarker(br)}`
  return [tree, br].filter(Boolean).join(' · ')
}

// ── Тип связи (блок «Связи» панели задачи) ──────────────────────────────────

export interface LinkTypeItem {
  /** Ключ связи — `dep_type` из карточки задачи. */
  value: string
  /** Подпись — как у серверных `DEP_TITLES` (`listik/deps.py`). */
  label: string
  /** Имя иконки из `lib/icons.ts`. */
  icon: string
}

/**
 * Типы связей: ключи сервера, подписи — как у `DEP_TITLES` (`listik/deps.py`).
 * `dep_title` сервер считает по той же таблице, поэтому подпись берём отсюда;
 * исключение — незнакомый тип (см. `linkTypeLabel`).
 */
export const LINK_TYPES: LinkTypeItem[] = [
  { value: 'blocks', label: 'блокирует', icon: 'lock' },
  { value: 'blocked-by', label: 'заблокирована', icon: 'lock' },
  { value: 'waits-for', label: 'ждёт', icon: 'clock' },
  { value: 'parent-child', label: 'родитель', icon: 'branch' },
  { value: 'parent', label: 'родитель', icon: 'branch' },
  { value: 'relates-to', label: 'связана', icon: 'link' },
  { value: 'related', label: 'связана', icon: 'link' },
  { value: 'discovered-from', label: 'найдена при', icon: 'search' },
  { value: 'duplicates', label: 'дублирует', icon: 'copy' },
  { value: 'supersedes', label: 'заменяет', icon: 'refresh' },
  { value: 'replies-to', label: 'ответ на', icon: 'timeline' },
  { value: 'suggested-blocks', label: 'предложенный блокер', icon: 'warning' },
]

/** Незнакомый тип: подпись равна самому ключу, иконка — звено цепи. */
export function linkType(value: string | null | undefined): LinkTypeItem {
  const key = (value ?? '').trim()
  return LINK_TYPES.find((item) => item.value === key) ?? { value: key, label: key, icon: 'link' }
}

/**
 * Подпись типа связи для подсказки чипа. У типа, которого нет в `LINK_TYPES`,
 * словарь подписи не знает — тогда берём `dep_title` сервера (в моке он врёт
 * для известных типов, поэтому приоритет у словаря).
 */
export function linkTypeLabel(value: string | null | undefined, depTitle?: string | null): string {
  const key = (value ?? '').trim()
  if (LINK_TYPES.some((item) => item.value === key)) return linkType(key).label
  return (depTitle ?? '').trim() || key
}

/** Иконка сводки у заголовка «Связи»: родитель, дети, блокеры, мягкие связи. */
export type DepSummaryKind = 'parent' | 'children' | 'blockers' | 'soft'

export interface DepSummaryItem {
  /** Подпись — в подсказку иконки. */
  label: string
  /** Имя иконки из `lib/icons.ts`. */
  icon: string
}

/**
 * Иконки сводки «Связей» — подписи и иконки держим здесь, чтобы шаблон панели
 * не собирал их сам (счётчики значений он берёт из данных задачи).
 */
export const DEP_SUMMARY: Record<DepSummaryKind, DepSummaryItem> = {
  parent: { label: 'родитель', icon: 'parent' },
  children: { label: 'дети: готово/всего', icon: 'branch' },
  blockers: { label: 'блокеры', icon: 'lock' },
  soft: { label: 'мягкие связи', icon: 'link' },
}

// ── Маршрут ─────────────────────────────────────────────────────────────────

export interface RouteIconItem extends DictionaryItem<RouteIconKey> {
  /** Имя иконки из `lib/icons.ts` (16×16, currentColor). */
  icon: string
  /** Расшифровка уровня — в подсказку иконки. */
  hint: string
}

/**
 * Уровни маршрута разработки — значения поля `icon` записи `routes.json`
 * (`GET /api/routes`). Сам уровень считает сервер: явное поле или фолбэк по
 * ключу маршрута (`xhigh-pipeline` → `xhigh`, `direct`-записи → `direct`), —
 * поэтому таблица нужна только для подписи и иконки.
 */
export const ROUTE_ICONS: RouteIconItem[] = [
  { value: 'xhigh', label: 'xhigh', icon: 'route-xhigh', hint: 'xhigh · самый дорогой и долгий маршрут' },
  { value: 'high', label: 'high', icon: 'route-high', hint: 'high · обычный маршрут' },
  { value: 'medium', label: 'medium', icon: 'route-medium', hint: 'medium · средний маршрут' },
  { value: 'low', label: 'low', icon: 'route-low', hint: 'low · дешёвый маршрут с ТЗ и критикой' },
  { value: 'xlow', label: 'xlow', icon: 'route-xlow', hint: 'xlow · самый дешёвый: без ТЗ и критики' },
  { value: 'direct', label: 'direct', icon: 'route-direct', hint: 'direct · один харнесс, без конвейера' },
]

/** Уровня нет (поле пустое) или он незнаком — иконку не рисуем. */
export function routeIcon(value: string | null | undefined): RouteIconItem | null {
  return ROUTE_ICONS.find((item) => item.value === value) ?? null
}

/**
 * Замена иконки записи, у которой `icon` в `routes.json` не принят сервером:
 * серый кружок с крестиком — «иконка недоступна». Такие записи отличает поле
 * `icon_error` из `GET /api/routes`; уровень, выведенный по ключу, рисуется
 * обычной иконкой из `ROUTE_ICONS`, крестик же значит, что уровня нет вовсе.
 */
export const ROUTE_ICON_UNKNOWN = {
  label: 'иконка недоступна',
  icon: 'route-unknown',
} as const

// ── Вид записи ленты (секция «Журнал и вердикты» панели задачи) ─────────────

export interface CommentKindItem extends DictionaryItem<CommentKind> {
  /** Имя иконки из `lib/icons.ts` — в фильтре, композере и маркере ленты. У
   *  `verdict` в ленте иконку/цвет маркера переопределяет `verdictMark` —
   *  здесь только композерная иконка (`flag`). */
  icon: string
  /** Placeholder поля композера для этого вида. */
  placeholder: string
  /** Фон и цвет кружка-маркера в ленте — токены кита (без hex). */
  markerBg: string
  markerColor: string
}

/**
 * Виды записей ленты «Журнал и вердикты»: `comment, journal, question,
 * answer, review, verdict`. Единственное место подписей — `commentKindTitle`
 * (`lib/format.ts`) берёт `label` отсюда.
 */
export const COMMENT_KINDS: CommentKindItem[] = [
  { value: 'comment', label: 'комментарий', icon: 'comment', placeholder: 'комментарий', markerBg: 'var(--surface-2)', markerColor: 'var(--ink-3)' },
  { value: 'journal', label: 'журнал', icon: 'list', placeholder: 'строка журнала', markerBg: 'var(--surface-2)', markerColor: 'var(--ink-3)' },
  { value: 'question', label: 'вопрос', icon: 'question', placeholder: 'вопрос человеку', markerBg: 'var(--warning-50)', markerColor: 'var(--warning-700)' },
  { value: 'answer', label: 'ответ', icon: 'answer', placeholder: 'ответ на вопрос', markerBg: 'var(--info-50)', markerColor: 'var(--info-700)' },
  { value: 'review', label: 'ревью', icon: 'review', placeholder: 'замечание ревью', markerBg: 'var(--surface-2)', markerColor: 'var(--ink-3)' },
  // Маркер вердикта в ленте считает verdictMark по тексту — markerBg/markerColor
  // здесь не используются (композеру и фильтру хватает иконки flag).
  { value: 'verdict', label: 'вердикт', icon: 'flag', placeholder: 'вердикт судьи', markerBg: 'var(--surface-2)', markerColor: 'var(--ink-3)' },
]

/** Незнакомый вид — первый словарный (`comment`), как и у остальных справочников файла. */
export function commentKind(value: string): CommentKindItem {
  return COMMENT_KINDS.find((item) => item.value === value) ?? COMMENT_KINDS[0]
}

export interface FeedMark {
  icon: string
  bg: string
  color: string
}

/**
 * Вид вердикта по тексту — перенос логики прежнего `TaskDrawer.commentTone`:
 * текст обрезается по краям и приводится к нижнему регистру, начинается на
 * «красн», «red» или «fail» → иконка `close` и danger-токены, иначе `check`
 * и success-токены.
 */
export function verdictMark(text: string): FeedMark {
  const trimmed = text.trim().toLowerCase()
  const danger = trimmed.startsWith('красн') || trimmed.startsWith('red') || trimmed.startsWith('fail')
  return danger
    ? { icon: 'close', bg: 'var(--danger-50)', color: 'var(--danger-700)' }
    : { icon: 'check', bg: 'var(--success-50)', color: 'var(--success-700)' }
}

export interface FeedEventKindItem {
  /** Ключ события (`TaskEvent.kind`). */
  value: string
  /** Подпись — подсказка иконки-маркера в ленте. */
  label: string
  /** Имя иконки из `lib/icons.ts`. */
  icon: string
}

/** Иконки и подписи событий ленты; незнакомое событие — иконка `dot`. */
export const FEED_EVENT_KINDS: FeedEventKindItem[] = [
  { value: 'created', label: 'создана', icon: 'plus' },
  { value: 'stage', label: 'этап', icon: 'play' },
  { value: 'claim', label: 'взял в работу', icon: 'hand' },
  { value: 'heartbeat', label: 'heartbeat', icon: 'heart' },
  { value: 'release', label: 'освободил', icon: 'user' },
  { value: 'done', label: 'закрыта', icon: 'check' },
  { value: 'route', label: 'маршрут', icon: 'route-direct' },
  { value: 'document_error', label: 'ошибка документа', icon: 'warning' },
  { value: 'document_restored', label: 'документ восстановлен', icon: 'check' },
]

/** Иконка и подпись события; незнакомый `kind` — иконка `dot`, подпись — сам ключ. */
export function feedEventMark(kind: string): FeedEventKindItem {
  return FEED_EVENT_KINDS.find((item) => item.value === kind) ?? { value: kind, label: kind, icon: 'dot' }
}

export type FeedFilterValue = 'all' | 'journal' | 'question' | 'review' | 'verdict'

export interface FeedFilterItem {
  value: FeedFilterValue
  label: string
  /** `null` у `all` — фильтр «все» подписан текстом, без иконки. */
  icon: string | null
}

export const FEED_FILTERS: FeedFilterItem[] = [
  { value: 'all', label: 'все', icon: null },
  { value: 'journal', label: 'журнал', icon: 'list' },
  { value: 'question', label: 'вопросы', icon: 'question' },
  { value: 'review', label: 'ревью', icon: 'review' },
  { value: 'verdict', label: 'вердикты', icon: 'flag' },
]

// ── Когнитивная сложность (оценка помощника DeepSeek) ───────────────────────

export interface ComplexityItem extends DictionaryItem<AssistantComplexityLevel> {
  /** Пояснение уровня — в подсказке и в панели помощника. */
  hint: string
  /** Тон бейджа: чем сложнее, тем громче. */
  tone: 'success' | 'info' | 'warning'
}

/** Уровни — ключи сервера (`assistant.COMPLEXITY_LEVELS`), подписи — здесь. */
export const COMPLEXITY_LEVELS: ComplexityItem[] = [
  { value: 'low', label: 'низкая', hint: 'механическая работа', tone: 'success' },
  { value: 'medium', label: 'средняя', hint: 'несколько шагов', tone: 'info' },
  { value: 'high', label: 'высокая', hint: 'много контекста и развилок', tone: 'warning' },
]

/** Незнакомый или пустой уровень — `null`: бейдж не рисуем. */
export function complexity(value: string | null | undefined): ComplexityItem | null {
  return COMPLEXITY_LEVELS.find((item) => item.value === value) ?? null
}
