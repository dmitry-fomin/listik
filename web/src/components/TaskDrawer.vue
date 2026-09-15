<script setup lang="ts">
/**
 * Панель задачи (UiDrawer справа, ~720px). Три слоя:
 * - шапка (#header): тип/проект/подсказка о родителе, заголовок, ряд пилюль
 *   и бейджей с id справа;
 * - «Где стоит процесс»: степпер s1…s4→done с описанием прошлого/текущего/
 *   будущего шага, ряд действий (heartbeat/needs-owner/next-stage/release/
 *   удалить/claim-сплит-кнопка) и подсказка под ним;
 * - «Холодный старт», «Кто держит», «Журнал и вердикты» (единая лента
 *   комментариев+событий на иконках: фильтр IconToggle со счётчиками,
 *   закреплённый открытый вопрос над лентой при needs_owner, у каждой записи
 *   кружок-маркер вида/события и строка «автор · время», ответ рисуется
 *   вложенным в свой вопрос; композер — одна рамка с IconToggle вида, полем
 *   и кнопкой-стрелкой отправки), «Связи» (сводка иконками со счётчиками,
 *   красные строки открытых блокеров, компактное дерево «родитель → эта
 *   задача → дети» с прогрессом и чипы мягких связей), «Описание и критерии».
 *
 * Своя разметка — только раскладка; контролы, бейджи, степпер, лента,
 * сплит-кнопка — из кита.
 */
import { computed, nextTick, ref, watch, type ComponentPublicInstance } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmDialog,
  UiCopyButton,
  UiDrawer,
  UiInput,
  UiProgress,
  UiSkeleton,
  UiStatusPill,
  UiSteps,
  UiTableActionButton,
  UiTextarea,
  UiTimeline,
  UiTooltip,
  type StatusPillTone,
  type UiStepItem,
  type UiStepStatus,
  type UiTimelineItem,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import ProjectMark from './marks/ProjectMark.vue'
import RouteIcon from './marks/RouteIcon.vue'
import RoutePicker from './RoutePicker.vue'
import TaskGlyph from './marks/TaskGlyph.vue'
import {
  commentKind,
  COMMENT_KINDS,
  DEP_SUMMARY,
  FEED_FILTERS,
  feedEventMark,
  linkType,
  linkTypeLabel,
  priority,
  verdictMark,
  type DepSummaryKind,
  type FeedFilterValue,
} from '@/lib/dictionaries'
import HarnessIcon from './marks/HarnessIcon.vue'
import type {
  CommentKind,
  DepInfo,
  DepsState,
  DepTree,
  PipelineStage,
  ProjectRow,
  TaskComment,
  TaskDep,
  TaskDependent,
  TaskDetail,
  TaskEvent,
  TaskStage,
} from '@/api/types'
import {
  commentKindTitle,
  datetimeAttr,
  eventKindTitle,
  formatDateTime,
  formatDuration,
  humanAge,
} from '@/lib/format'
import { PIPELINE, TRANSITIONS, stageCode, stageIndex, stageTitle, transitionOut, type TransitionKey } from '@/lib/stages'
import { taskHealth } from '@/lib/health'
import {
  actorShort,
  coldStartRows,
  coldStartTone,
  desktopHolderPresentation,
  hasHolderTitle,
  healthPillText,
  projectOf,
  type ColdRow,
} from '@/lib/task-presentation'
import { NO_ROUTE, routeByKey, routesAlertText } from '@/lib/routes'
import store from '@/store/listik'

const props = defineProps<{
  modelValue: boolean
  task: TaskDetail | null
  loading?: boolean
  error?: string | null
  pending?: string | null
  /** Список акторов из /api/meta — для подсказки, кто держит. */
  actors?: { key: string; title: string | null }[]
  /** Проекты из /api/meta — для монограммы/названия в шапке. */
  projects?: ProjectRow[]
  /** Запрос дерева зависимостей (POST …/deps без depends_on). */
  loadTree: (id: string, depth?: number) => Promise<DepTree | null>
}>()

// `.listik-stack` объявляет gap позже общих стилей drawer и перекрывает его.
// Секции панели задают собственные отступы через margin/padding и разделитель.
const drawerBodyGap = computed(() => 0)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  reload: []
  patch: [payload: { id: string; body: Record<string, unknown>; label: string }]
  claim: [payload: { id: string; holder: string; note?: string; force?: boolean }]
  heartbeat: [payload: { id: string; holder: string; note?: string }]
  stage: [payload: { id: string; holder?: string; note?: string }]
  needsOwner: [payload: { id: string; value: boolean; note?: string }]
  release: [payload: { id: string }]
  done: [payload: { id: string; result: string }]
  remove: [payload: { id: string }]
  comment: [payload: { id: string; text: string; kind: CommentKind; author?: string }]
  dep: [payload: { id: string; dependsOn: string }]
  /** Перейти к другой задаче, не закрывая панель (блокер, ожидающий, ребёнок). */
  'open-other': [id: string]
}>()

const HOLDER_KEY = 'listik.holder'

const holder = ref<string | null>(null)
const holderInitialised = ref(false)
const closeResult = ref('')
const newDep = ref('')
const closeFormOpen = ref(false)
const removeOpen = ref(false)
const depFormOpen = ref(false)
const needsOwnerFormOpen = ref(false)
const needsOwnerNote = ref('')
const feedInputRef = ref<ComponentPublicInstance | null>(null)

const deps = computed<DepsState | null>(() => props.task?.deps_state ?? null)
const depTree = ref<DepTree | null>(null)
const depTreeLoading = ref(false)

const blockedBy = computed<DepInfo[]>(() => deps.value?.blocked_by ?? [])
const waitingFor = computed<DepInfo[]>(() => deps.value?.waiting_for ?? [])
const softLinks = computed<DepInfo[]>(() => deps.value?.soft_links ?? [])
const parentDep = computed<DepInfo | null>(() => deps.value?.parent ?? null)
const childrenOpen = computed<DepInfo[]>(() => deps.value?.children_open ?? [])

/** Ребёнок в дереве «Связей» — общая форма для `children` карточки и `children_open`. */
interface ChildCard {
  id: string
  title: string
  status: string
  stage: TaskStage
  holder: string | null
  holderTitle: string
  /** Ссылка на id рисуется только в первом по приоритету месте блока. */
  linked: boolean
}

/**
 * Дети для дерева: `task.children` — все дети карточки, включая закрытых, —
 * если поле пришло; иначе `deps_state.children_open` (только незакрытые).
 */
const childCards = computed<Omit<ChildCard, 'linked'>[]>(() => {
  const cards = props.task?.children
  if (cards) {
    return cards.map((child) => ({
      id: child.id,
      title: child.title,
      status: child.status,
      stage: child.stage ?? null,
      holder: child.holder ?? null,
      holderTitle: child.holder_title ?? '',
    }))
  }
  return childrenOpen.value.map((dep) => ({
    id: dep.id,
    title: dep.title,
    status: dep.status,
    stage: dep.stage ?? null,
    holder: dep.holder ?? null,
    holderTitle: dep.holder_title ?? '',
  }))
})

/** «Готово» — только `status === 'done'`: отменённый ребёнок входит во «всего», но не сюда. */
const childrenDone = computed(() => childCards.value.filter((child) => child.status === 'done').length)
const childrenTotal = computed(() => childCards.value.length)

/**
 * Связи карточки как они лежат в `deps` (обе стороны, включая мягкие входящие).
 * `deps_state` для сводки не годится: в `soft_links` из входящих связей есть только
 * `discovered-from` («найдена при», см. listik-0wpx), а остального «кто ссылается
 * на эту задачу» там нет.
 */
const dependencies = computed<TaskDep[]>(() => props.task?.dependencies ?? [])
const dependents = computed<TaskDependent[]>(() => props.task?.dependents ?? [])

/**
 * Раскладка блока «Связи». Строки идут в порядке приоритета (блокер → родитель →
 * ребёнок в дереве → «её ждут» → чип мягкой связи), и ссылку
 * (`button.listik-link`) получает только первое появление id: в остальных местах
 * id показывается текстом. `shownIds` собирает всё показанное, чтобы сводки внизу
 * не рисовали те же id ссылками второй раз.
 */
const depBlock = computed(() => {
  const shown = new Set<string>()
  const claim = (id: string): boolean => {
    const first = !shown.has(id)
    shown.add(id)
    return first
  }
  const blockers = blockedBy.value.map((dep) => ({ dep, linked: claim(dep.id) }))
  const parent = parentDep.value ? { dep: parentDep.value, linked: claim(parentDep.value.id) } : null
  const children = childCards.value.map((child) => ({ ...child, linked: claim(child.id) }))
  const waiting = waitingFor.value.map((dep) => ({ dep, linked: claim(dep.id) }))
  const soft = softLinks.value.map((dep) => ({ dep, linked: claim(dep.id), type: linkType(dep.dep_type) }))
  return { blockers, parent, children, waiting, soft, shownIds: shown }
})

/** Сводка у заголовка: счётчики не прячутся даже с нулём — ноль только приглушается. */
const depCounters = computed<{ kind: DepSummaryKind; value: string; zero: boolean; danger: boolean }[]>(() => [
  { kind: 'parent', value: parentDep.value ? '1' : '0', zero: !parentDep.value, danger: false },
  { kind: 'children', value: `${childrenDone.value}/${childrenTotal.value}`, zero: childrenTotal.value === 0, danger: false },
  { kind: 'blockers', value: String(blockedBy.value.length), zero: blockedBy.value.length === 0, danger: blockedBy.value.length > 0 },
  { kind: 'soft', value: String(softLinks.value.length), zero: softLinks.value.length === 0, danger: false },
])

/**
 * id, показанные в блоке «Связи» строками и чипами (blocked_by, parent, дети,
 * waiting_for, soft_links). В сводках такие id не повторяются — иначе одна и та
 * же ссылка рисуется дважды.
 */
const groupedIds = computed<Set<string>>(() => depBlock.value.shownIds)

/**
 * Сводка «зависит от» — только то, чего нет в группах выше. Один id — одна ссылка:
 * в `deps` на одного и того же адресата бывает несколько строк с разными `dep_type`
 * (ключ таблицы — `issue_id, depends_on, dep_type`), а повтор `:key` во `v-for`
 * Vue патчит непредсказуемо.
 */
const dependenciesSummary = computed<TaskDep[]>(() => {
  const out: TaskDep[] = []
  const seen = new Set<string>()
  for (const dep of dependencies.value) {
    if (groupedIds.value.has(dep.depends_on) || seen.has(dep.depends_on)) continue
    seen.add(dep.depends_on)
    out.push(dep)
  }
  return out
})
/** Сводка «от неё зависит» — только то, чего нет в группах выше; тоже без дублей id. */
const dependentsSummary = computed<TaskDependent[]>(() => {
  const out: TaskDependent[] = []
  const seen = new Set<string>()
  for (const dep of dependents.value) {
    if (groupedIds.value.has(dep.issue_id) || seen.has(dep.issue_id)) continue
    seen.add(dep.issue_id)
    out.push(dep)
  }
  return out
})
const canFinish = computed(() => deps.value?.can_finish !== false)
const reasons = computed<string[]>(() => deps.value?.reasons ?? [])
/** Блокеры стоят без движения: ни держателя, ни свежего heartbeat. */
const blockersIdle = computed(
  () => blockedBy.value.length > 0 && blockedBy.value.every((dep) => dep.missing || dep.stale || !dep.holder),
)

const statusTone = computed<StatusPillTone>(() => {
  const status = props.task?.status
  if (status === 'blocked') return 'dead'
  if (status === 'done') return 'success'
  if (status === 'in_progress') return 'info'
  if (status === 'cancelled') return 'unknown'
  return 'healthy'
})

// ── «Маршрут запуска»: смена, пока задача не начата (см. API.md) ──────────

/**
 * Сервер разрешает менять маршрут, пока задача заведена и работа не началась:
 * нет этапа, держателя и запущенного процесса (поле `route_editable`). Как
 * только работа началась, сервер отказывает с понятной ошибкой, а доска не
 * показывает выбор — здесь остаётся только прочитанный маршрут.
 */
const routeEditable = computed(() => props.task?.route_editable === true)

// Маршруты — из `GET /api/routes` (кеш на сессию доски, как у «Новой задачи»):
// запрашиваем, только когда в карточке действительно можно выбирать.
watch(
  [() => props.modelValue, routeEditable],
  ([open, editable]) => {
    if (open && editable) store.ensureRoutes()
  },
  { immediate: true },
)

/** Ошибка файла (`ok:false`) или самого запроса — выбирать не из чего. */
const routesFailed = computed(() => store.routesRequestFailed.value || !store.routesOk.value)

const routesAlert = computed(() =>
  routesAlertText(store.routesRequestFailed.value, store.routesError.value),
)

function retryRoutes(): void {
  void store.loadRoutes()
}

/**
 * Клик по матрице сразу сохраняет: на сервер уходит одно поле `route`. Метки
 * маршрута (`harness:<…>`/`process:<…>`) сервер переписывает сам — старые
 * снимает, метки нового ставит, чужие метки задачи оставляет: то же правило,
 * что при создании (`routes.labels_for`), поэтому доска их не считает.
 *
 * Пункт «без маршрута» шлёт пустую строку — маршрут снимается совсем (как
 * `set launch_route=`): сервер убирает и его метки, и ошибку автостарта.
 * Повторный клик по уже выбранному ничего не шлёт.
 */
function pickRoute(key: string): void {
  if (!props.task || props.pending === 'route') return
  const current = props.task.launch_route ?? NO_ROUTE
  if (key === current) return
  emit('patch', {
    id: props.task.id,
    body: { route: key },
    label: 'route',
  })
}

// ── «Автостарт»: процесс маршрута поднимает сервер (см. API.md) ───────────

/**
 * Маршрут задачи (`launch_route` — ключ записи `routes.json`) для иконки уровня;
 * записи нет в списке `GET /api/routes` — иконки не будет.
 */
const launchRoute = computed(() => routeByKey(props.task?.launch_route, store.routes.value))

/** Блок нужен, если маршрут можно менять или о запуске уже есть что сказать. */
const showLaunch = computed(() => {
  const task = props.task
  if (!task) return false
  return Boolean(task.route_editable || task.autostart || task.launched_by
    || task.launch_error || task.launch_route)
})

/**
 * Статус запуска: «идёт», пока процесс не завершён; «код N» — код выхода;
 * «отслеживание потеряно» — `launch_finished_at` заполнен, а код null (сервер
 * потерял процесс). Задача без попытки запуска строки статуса не получает.
 */
const launchStatus = computed<string | null>(() => {
  const task = props.task
  if (!task) return null
  const closed = task.status === 'done' || task.status === 'cancelled'
  // Закрытая задача без launch_finished_at — процесс потерян, а не «идёт» (listik-3a4m).
  if (task.launched_by === 'listik' && !task.launch_finished_at) return closed ? 'отслеживание потеряно' : 'идёт'
  if (task.launch_finished_at && task.launch_exit_code != null) return `код ${task.launch_exit_code}`
  if (task.launch_finished_at) return 'отслеживание потеряно'
  return null
})

function defaultHolder(): string {
  try {
    const stored = window.localStorage.getItem(HOLDER_KEY)
    if (stored) return stored
  } catch {
    /* приватный режим */
  }
  return 'me'
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    if (!holderInitialised.value) {
      holder.value = props.task?.holder || defaultHolder()
      holderInitialised.value = true
    } else if (props.task?.holder) {
      holder.value = props.task.holder
    }
  },
  { immediate: true },
)

watch(
  () => props.task?.id,
  () => {
    closeResult.value = ''
    newDep.value = ''
    needsOwnerNote.value = ''
    feedText.value = ''
    feedKind.value = 'journal'
    feedFilter.value = 'all'
    pinnedAnswerText.value = ''
    closeFormOpen.value = false
    removeOpen.value = false
    depFormOpen.value = false
    needsOwnerFormOpen.value = false
    // Дерево связей принадлежит задаче: при переходе по ссылке оно не должно
    // оставаться от прежней карточки (ответ старого запроса тоже гасится в loadTree).
    depTree.value = null
    depTreeLoading.value = false
    if (props.task?.holder) holder.value = props.task.holder
  },
)

watch(
  () => props.pending,
  (value, previous) => {
    if (previous === 'remove' && value !== 'remove') removeOpen.value = false
  },
)

function focusFeedInput(): void {
  void nextTick(() => {
    const root = feedInputRef.value?.$el
    if (root instanceof HTMLElement) root.querySelector('input')?.focus()
  })
}

/** Родитель просит фокус в строке ввода журнала — из «Ответить» инбокса и т.п. */
function focusComment(): void {
  focusFeedInput()
}

/** Ставит вид «ответ» и фокус — кнопка «Ответить» инбокса «нужен ты». */
function focusAnswer(): void {
  feedKind.value = 'answer'
  focusFeedInput()
}

defineExpose({ focusComment, focusAnswer })

// ── «Где стоит процесс»: степпер и подписи шагов ─────────────────────────

const STEP_LABELS = [...PIPELINE.map((step) => `${step.code} · ${step.title}`), 'done']

const currentIndex = computed(() => {
  const task = props.task
  if (!task || task.stage === null) return -1
  return stageIndex(task.stage)
})

function statusOfIndex(index: number): UiStepStatus {
  const cur = currentIndex.value
  if (cur === -1) return 'upcoming'
  if (index < cur) return 'done'
  if (index === cur) return props.task?.stage === 'done' ? 'done' : 'current'
  return 'upcoming'
}

/** События по времени: сервер отдаёт последние 100 не гарантированно по порядку. */
const sortedEvents = computed<TaskEvent[]>(() => {
  const events = props.task?.events ?? []
  return [...events].sort((a, b) => Date.parse(a.ts) - Date.parse(b.ts))
})

function findEnter(stageKey: string): TaskEvent | undefined {
  return sortedEvents.value.find((event) => event.kind === 'stage' && event.to_value === stageKey)
}

function findExit(stageKey: string, afterIso?: string): TaskEvent | undefined {
  const afterTs = afterIso ? Date.parse(afterIso) : -Infinity
  return sortedEvents.value.find(
    (event) => event.kind === 'stage' && event.from_value === stageKey && Date.parse(event.ts) >= afterTs,
  )
}

/**
 * «Кто работал» на пройденном этапе X — не актор события `stage` (его нет),
 * а держатель из последнего `claim`/`heartbeat` в окне этапа; при пустом окне
 * (переход в X был sticky) — унаследован у предыдущего этапа, для s1 —
 * `actor` события `created`; нет ни входа, ни выхода из X, но у задачи вообще
 * есть события — окна для X просто нет, «кто работал» = «—»; событий нет
 * вовсе (сервер отдаёт последние 100, может не остаться ни одного) — `null`,
 * чтобы описание шага свелось к одной стрелке перехода.
 */
function workedByRaw(stageKey: PipelineStage, index: number): string | null {
  const enter = findEnter(stageKey)
  const exit = findExit(stageKey, enter?.ts)
  if (!enter && !exit) return sortedEvents.value.length === 0 ? null : '—'
  const startTs = enter ? Date.parse(enter.ts) : -Infinity
  const endTs = exit ? Date.parse(exit.ts) : Infinity
  const candidates = sortedEvents.value.filter(
    (event) =>
      (event.kind === 'claim' || event.kind === 'heartbeat') &&
      event.to_value &&
      Date.parse(event.ts) >= startTs &&
      Date.parse(event.ts) <= endTs,
  )
  const last = candidates[candidates.length - 1]
  if (last) return actorShort(last.to_value)
  if (index > 0) {
    const prevKey = PIPELINE[index - 1].key
    const key = `${prevKey}:${stageKey}` as TransitionKey
    if (TRANSITIONS[key] === 'sticky') {
      const inherited = workedByRaw(prevKey, index - 1)
      if (inherited !== null) return inherited
    }
  } else {
    const created = props.task?.events.find((event) => event.kind === 'created')
    if (created?.actor) return actorShort(created.actor)
  }
  return '—'
}

function stepDuration(stageKey: PipelineStage): string | null {
  const enter = findEnter(stageKey)
  const exit = findExit(stageKey, enter?.ts)
  return exit?.duration_s != null ? formatDuration(exit.duration_s) : null
}

function arrowLabel(stageKey: PipelineStage): string {
  const transition = transitionOut(stageKey)
  return transition ? `${transition} →` : '→'
}

function pastDescription(stageKey: PipelineStage, index: number): string {
  const worked = workedByRaw(stageKey, index)
  const arrow = arrowLabel(stageKey)
  if (worked === null) return arrow
  const duration = stepDuration(stageKey)
  const parts = [worked]
  if (duration) parts.push(duration)
  parts.push(arrow)
  return parts.join(' · ')
}

function describeStep(index: number, status: UiStepStatus): string | undefined {
  const task = props.task
  if (!task) return undefined
  if (index === 4) return 'коммит судьи'
  const step = PIPELINE[index]
  if (status === 'done') return pastDescription(step.key, index)
  if (status === 'current') {
    const holder = hasHolderTitle(task.holder_title) ? task.holder_title : 'без держателя'
    return `${holder} · ${task.stage_age} · сейчас`
  }
  const prevKey = index === 0 ? null : PIPELINE[index - 1].key
  const key = prevKey ? (`${prevKey}:${step.key}` as TransitionKey) : null
  const transitionIn = key ? TRANSITIONS[key] : undefined
  return transitionIn === 'sticky' ? 'та же сессия' : 'новый держатель'
}

const steps = computed<UiStepItem[]>(() =>
  STEP_LABELS.map((label, index) => {
    const status = statusOfIndex(index)
    return { label, status, description: props.task ? describeStep(index, status) : undefined }
  }),
)

const processHint = computed(() => {
  const task = props.task
  if (!task) return ''
  if (!task.stage) return 'вне конвейера'
  const code = stageCode(task.stage) ?? task.stage
  return `на ${code} ${task.stage_age}${task.stage_warn ? ' · дольше порога' : ''}`
})

function nextStageCode(stage: PipelineStage | 'done' | null): string | null {
  if (!stage || stage === 'done') return null
  const index = PIPELINE.findIndex((step) => step.key === stage)
  if (index === -1) return null
  return index + 1 < PIPELINE.length ? PIPELINE[index + 1].code : 'done'
}

const belowRowHint = computed(() => {
  const task = props.task
  const d = deps.value
  if (!d) return ''
  if (!d.ready) {
    let text = `взять нельзя: ${reasons.value.join('; ')}`
    if (d.holder && task) {
      const transition = transitionOut(task.stage)
      if (transition === 'sticky') {
        const code = stageCode(task.stage)
        const next = nextStageCode(task.stage)
        text += `, переход ${code} → ${next} sticky — следующий этап идёт в той же сессии`
      }
    }
    return text
  }
  return 'можно брать прямо сейчас'
})

function submitHeartbeat(): void {
  const value = (holder.value ?? '').trim()
  if (!props.task || !value) return
  emit('heartbeat', { id: props.task.id, holder: value })
}

function submitStage(): void {
  if (!props.task) return
  emit('stage', { id: props.task.id })
}

function openNeedsOwnerForm(): void {
  needsOwnerFormOpen.value = true
  needsOwnerNote.value = ''
}

function submitNeedsOwner(): void {
  if (!props.task) return
  emit('needsOwner', { id: props.task.id, value: true, note: needsOwnerNote.value.trim() || undefined })
  needsOwnerFormOpen.value = false
  needsOwnerNote.value = ''
}

function clearNeedsOwner(): void {
  if (!props.task) return
  emit('needsOwner', { id: props.task.id, value: false })
}

function submitDone(): void {
  if (!props.task) return
  const result = closeResult.value.trim()
  if (!result) return
  emit('done', { id: props.task.id, result })
  closeResult.value = ''
  closeFormOpen.value = false
}

function submitRemove(): void {
  if (!props.task) return
  emit('remove', { id: props.task.id })
}

function submitDep(): void {
  if (!props.task) return
  const dependsOn = newDep.value.trim()
  if (!dependsOn) return
  emit('dep', { id: props.task.id, dependsOn })
  newDep.value = ''
  depFormOpen.value = false
}

/** Enter в поле «ID блокера» отправляет связь так же, как кнопка. */
function onDepKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') submitDep()
}

// ── «Холодный старт» ──────────────────────────────────────────────────────

const coldRows = computed<ColdRow[]>(() => {
  const task = props.task
  if (!task) return []
  return coldStartRows(task, blockedBy.value, waitingFor.value)
})

const coldOkCount = computed(() => coldRows.value.filter((row) => row.ok).length)

/** Тон счётчика: всё заполнено — зелёный; красная строка (worktree не указан) — красный; иначе жёлтый. */
const coldTone = computed(() => coldStartTone(coldRows.value))

const holderBlock = computed(() => (props.task ? desktopHolderPresentation(props.task) : null))

// ── «Журнал и вердикты» ───────────────────────────────────────────────────
//
// Лента — UiTimeline кита без его встроенного заголовка/времени: и `title`, и
// `timestamp`/`datetime`, и `meta` рисуются им безусловно, поверх любого
// #content-слота (см. UiTimeline.vue), поэтому у наших FeedRow эти поля не
// используются (`title` — обязательное поле контракта, оставляем пустым) —
// вся раскладка «маркер-кружок · автор/время · текст» рисуется в #marker/
// #content своими средствами на токенах кита. Порядок в ленте — по `sortKey`
// (ISO-время), а не по built-in `datetime`, ровно по той же причине.

const ALL_EVENT_KINDS = new Set([
  'stage',
  'claim',
  'release',
  'heartbeat',
  'question',
  'answer',
  'done',
  'created',
  'route',
  'document_error',
  'document_restored',
])

const feedFilter = ref<FeedFilterValue>('all')

function setFeedFilter(value: FeedFilterValue): void {
  feedFilter.value = value
}

function matchesCommentFilter(kind: string, filter: FeedFilterValue): boolean {
  if (filter === 'all') return true
  if (filter === 'journal') return kind === 'journal'
  if (filter === 'question') return kind === 'question' || kind === 'answer'
  if (filter === 'review') return kind === 'review'
  return kind === 'verdict'
}

function eventsForFilter(filter: FeedFilterValue, events: TaskEvent[]): TaskEvent[] {
  if (filter === 'all') return events.filter((event) => ALL_EVENT_KINDS.has(event.kind))
  if (filter === 'journal') return events.filter((event) => event.kind === 'stage')
  return []
}

/** Счётчики фильтра — по «сырым» записям (включая ответы, вложенные в вопросы
 *  ниже), поэтому видимых записей верхнего уровня после сборки в вопрос может
 *  быть меньше счётчика. */
const feedCounts = computed<Record<FeedFilterValue, number>>(() => {
  const comments = props.task?.comments ?? []
  const events = props.task?.events ?? []
  const countComments = (filter: FeedFilterValue): number =>
    comments.filter((comment) => matchesCommentFilter(comment.kind, filter)).length
  return {
    all: countComments('all') + eventsForFilter('all', events).length,
    journal: countComments('journal') + eventsForFilter('journal', events).length,
    question: countComments('question'),
    review: countComments('review'),
    verdict: countComments('verdict'),
  }
})

// IconToggle типизирует опцию слота ровно как IconToggleOption<V> (только
// value/label) — иконку и счётчик берём по option.value через FEED_FILTERS/
// feedCounts, а не как лишнее поле на самой опции (кит API менять нельзя).
const feedFilterOptions: IconToggleOption<FeedFilterValue>[] = FEED_FILTERS.map((item) => ({
  value: item.value,
  label: item.label,
}))

function feedFilterIcon(value: FeedFilterValue): string | null {
  return FEED_FILTERS.find((item) => item.value === value)?.icon ?? null
}

function feedFilterCount(value: FeedFilterValue): number {
  return feedCounts.value[value]
}

function stageEventTitle(event: TaskEvent): string {
  let title = `stage ${event.from_value ?? '—'} → ${event.to_value}`
  const match = event.note?.match(/\((sticky|handoff)\)/)
  if (match) title += ` · ${match[1]}`
  return title
}

function eventTitle(event: TaskEvent): string {
  if (event.kind === 'stage') return stageEventTitle(event)
  if (event.kind === 'heartbeat') return `heartbeat · ${event.actor ?? '—'}`
  if (event.kind === 'claim') return `взял в работу · ${event.actor ?? '—'}`
  if (event.kind === 'release') return `освободил · ${event.actor ?? '—'}`
  if (event.kind === 'route') {
    return `маршрут ${event.from_value ?? '—'} → ${event.to_value ?? '—'}`
  }
  return eventKindTitle(event.kind)
}

function eventDescription(event: TaskEvent): string | undefined {
  if (event.kind === 'heartbeat') return props.task?.holder_note || event.note || undefined
  return event.note || undefined
}

interface FeedMarker {
  icon: string
  bg: string
  color: string
  hint: string
}

interface FeedAnswer extends FeedMarker {
  author: string
  time: string
  text: string
}

interface FeedRow extends UiTimelineItem {
  marker: FeedMarker
  authorLine?: string
  text?: string
  subtext?: string
  answer: FeedAnswer | null
  sortKey: string
}

/** Маркер комментария: иконка/цвет — из COMMENT_KINDS, у `verdict` — по тексту (verdictMark). */
function commentMark(kind: string, text: string): FeedMarker {
  if (kind === 'verdict') {
    const mark = verdictMark(text)
    return { ...mark, hint: commentKindTitle('verdict') }
  }
  const item = commentKind(kind)
  return { icon: item.icon, bg: item.markerBg, color: item.markerColor, hint: item.label }
}

function commentTime(iso: string): string {
  return `${formatDateTime(iso)} · ${humanAge(iso)}`
}

function answerRow(comment: TaskComment): FeedAnswer {
  return {
    ...commentMark(comment.kind, comment.text),
    author: comment.author || 'без автора',
    time: commentTime(comment.created_at),
    text: comment.text,
  }
}

function commentRow(comment: TaskComment, answer: TaskComment | null): FeedRow {
  return {
    id: `c-${comment.id}`,
    title: '',
    marker: commentMark(comment.kind, comment.text),
    authorLine: `${comment.author || 'без автора'} · ${commentTime(comment.created_at)}`,
    text: comment.text,
    answer: answer ? answerRow(answer) : null,
    sortKey: datetimeAttr(comment.created_at) ?? '',
  }
}

function eventRow(event: TaskEvent, index: number): FeedRow {
  const mark = feedEventMark(event.kind)
  return {
    id: `e-${event.ts}-${index}`,
    title: '',
    marker: { icon: mark.icon, bg: 'var(--surface-2)', color: 'var(--ink-3)', hint: mark.label },
    text: eventTitle(event),
    subtext: eventDescription(event),
    answer: null,
    sortKey: datetimeAttr(event.ts) ?? '',
  }
}

/**
 * Пары «вопрос → ответ»: ответ привязывается к ближайшему предшествующему по
 * времени комментарию `question` без ответа (стек — самый недавний открытый
 * вопрос из ещё не отвеченных). Считается по всем комментариям задачи, а не
 * по отфильтрованным — привязка не должна зависеть от активного фильтра.
 */
const answerByQuestion = computed<Map<number, TaskComment>>(() => {
  const map = new Map<number, TaskComment>()
  const task = props.task
  if (!task) return map
  const sorted = [...task.comments].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  const openQuestions: TaskComment[] = []
  for (const comment of sorted) {
    if (comment.kind === 'question') openQuestions.push(comment)
    else if (comment.kind === 'answer') {
      const question = openQuestions.pop()
      if (question) map.set(question.id, comment)
    }
  }
  return map
})

// UiTimeline типизирует слот ровно как UiTimelineItem (не дженерик) — FeedRow
// его строго расширяет, поэтому даункаст безопасен: сама лента строится из
// feed() ниже, никакой другой массив в :items не попадает.
function asFeedRow(item: UiTimelineItem): FeedRow {
  return item as FeedRow
}

const feed = computed<FeedRow[]>(() => {
  const task = props.task
  if (!task) return []
  const filter = feedFilter.value
  const answers = answerByQuestion.value
  const answeredCommentIds = new Set([...answers.values()].map((comment) => comment.id))
  const commentRows = task.comments
    .filter((comment) => matchesCommentFilter(comment.kind, filter))
    .filter((comment) => !answeredCommentIds.has(comment.id))
    .map((comment) => commentRow(comment, comment.kind === 'question' ? answers.get(comment.id) ?? null : null))
  const eventRows = eventsForFilter(filter, task.events).map((event, index) => eventRow(event, index))
  const all = [...commentRows, ...eventRows]
  all.sort((a, b) => b.sortKey.localeCompare(a.sortKey))
  return all
})

const feedEmptyState = computed<{ title: string; description?: string }>(() => {
  if (feed.value.length > 0) return { title: 'Записей нет' }
  if (feedFilter.value === 'question') return { title: 'Вопросов нет' }
  if (feedFilter.value !== 'verdict') return { title: 'Записей нет' }
  const task = props.task
  const reachedJudge = task?.stage === 's4-judge' || task?.stage === 'done'
  if (!reachedJudge) {
    const code = stageCode('s4-judge')
    const title = stageTitle('s4-judge')
    return {
      title: 'Вердиктов пока нет',
      description: `Вердикт появляется на этапе ${code} (${title}) — сейчас задача до него не дошла.`,
    }
  }
  return {
    title: 'Вердиктов пока нет',
    description:
      'Судья ещё не оставил вердикт — по протоколу это делает командой listik comment -k verdict <id> "..." перед приёмкой.',
  }
})

const feedKind = ref<CommentKind>('journal')
const feedText = ref('')

const feedKindOptions: IconToggleOption<CommentKind>[] = COMMENT_KINDS.map((item) => ({
  value: item.value,
  label: item.label,
}))

const feedPlaceholder = computed(() => commentKind(feedKind.value).placeholder)

function onFeedKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') submitFeed()
}

function submitFeed(): void {
  if (!props.task) return
  const text = feedText.value.trim()
  if (!text) return
  const kind = feedKind.value
  if (kind === 'answer') {
    emit('needsOwner', { id: props.task.id, value: false, note: text })
  } else if (kind === 'question') {
    emit('needsOwner', { id: props.task.id, value: true, note: text })
  } else {
    emit('comment', { id: props.task.id, text, kind, author: (holder.value ?? '').trim() || undefined })
  }
  feedText.value = ''
}

// ── Закреплённый открытый вопрос (над лентой, при task.needs_owner) ────────

const lastQuestion = computed<TaskComment | null>(() => {
  const task = props.task
  if (!task) return null
  const questions = task.comments.filter((comment) => comment.kind === 'question')
  if (!questions.length) return null
  return [...questions].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)).pop() ?? null
})

const pinnedAuthor = computed<string>(() => actorShort(lastQuestion.value?.author ?? props.task?.holder ?? null))

const pinnedAge = computed<string>(() => humanAge(lastQuestion.value?.created_at ?? props.task?.holder_at ?? null))

const pinnedText = computed<string>(() => lastQuestion.value?.text || props.task?.holder_note || 'вопрос без текста')

const pinnedAnswerText = ref('')

function onPinnedAnswerKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') submitPinnedAnswer()
}

function submitPinnedAnswer(): void {
  if (!props.task) return
  const text = pinnedAnswerText.value.trim()
  if (!text) return
  emit('needsOwner', { id: props.task.id, value: false, note: text })
  pinnedAnswerText.value = ''
}

// ── «Связи» ────────────────────────────────────────────────────────────────

function depCardHint(dep: DepInfo): string {
  const base = hasHolderTitle(dep.holder_title) ? `держит ${dep.holder_title} · ${dep.holder_age}` : 'без держателя'
  return dep.stale ? `${base} · стоит без движения` : base
}

/** Держатель ребёнка коротко: ключ актора (`agent:dsh` → dsh), иначе `holder_title`, иначе «—». */
function childHolder(child: { holder: string | null; holderTitle: string }): string {
  const short = actorShort(child.holder)
  return short === '—' && hasHolderTitle(child.holderTitle) ? child.holderTitle : short
}

/** Дерево связей запрашивается по кнопке: у задачи оно может быть глубоким. */
async function loadTree(): Promise<void> {
  const task = props.task
  if (!task) return
  const id = task.id
  depTreeLoading.value = true
  const tree = await props.loadTree(id)
  // Пока грузилось, могли уйти по ссылке на другую задачу — чужое дерево не показываем
  // (сброс состояния и снятие загрузки в этом случае уже сделал вотчер по id).
  if (props.task?.id !== id) return
  depTree.value = tree
  depTreeLoading.value = false
}
</script>

<template>
  <UiDrawer
    :model-value="modelValue"
    size="lg"
    side="right"
    :title="task ? task.id : 'Задача'"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div v-if="task" class="listik-drawer__head">
        <div class="listik-row listik-drawer__meta-row">
          <TaskGlyph kind="type" :value="task.issue_type" />
          <ProjectMark :project="projectOf(task, projects)" :slug="task.project" with-title size="sm" />
          <span class="listik-drawer__slash">/</span>
          <span class="listik-drawer__id">
            <span class="listik-mono">{{ task.id }}</span>
            <UiCopyButton :value="task.id" label="ID задачи">
              <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
            </UiCopyButton>
          </span>
          <span v-if="parentDep" class="listik-section__hint listik-drawer__truncate">
            · порция шага «{{ parentDep.title }}»
          </span>
        </div>
        <h2 class="listik-drawer__title">{{ task.title }}</h2>
        <div class="listik-row listik-drawer__pills">
          <UiStatusPill :tone="taskHealth(task)" size="md">{{ healthPillText(task) }}</UiStatusPill>
          <UiStatusPill :tone="statusTone" size="md">{{ task.status_title }}</UiStatusPill>
          <span class="listik-row" style="gap: var(--space-1); flex-wrap: nowrap">
            <TaskGlyph kind="priority" :value="task.priority" />
            <span class="listik-section__hint">{{ priority(task.priority).label }}</span>
          </span>
          <span
            v-if="task.labels.length || waitingFor.length || blockedBy.length || task.needs_owner"
            class="listik-drawer__vsep"
            aria-hidden="true"
          />
          <UiBadge v-for="label in task.labels" :key="label" tone="info" size="sm"><span class="listik-mono">{{ label }}</span></UiBadge>
          <UiBadge v-if="waitingFor.length" tone="accent" size="sm">её ждут {{ waitingFor.length }}</UiBadge>
          <UiBadge v-if="blockedBy.length" tone="warning" size="sm">ждёт {{ blockedBy.length }}</UiBadge>
          <UiBadge v-if="task.needs_owner" tone="accent" size="sm">нужен ты</UiBadge>
        </div>
      </div>
      <div v-else class="listik-drawer__head">
        <span class="listik-mono">Задача</span>
      </div>
    </template>

    <!-- Ошибку показываем и когда карточка уже есть: неудачный переход по ссылке из
         «Связей» (задача удалена, связь устарела) не должен стирать открытую панель. -->
    <UiAlert v-if="error" tone="danger">
      <template #title>Не удалось открыть задачу</template>
      {{ error }}
      <div v-if="task" class="listik-section__hint">
        Панель осталась на задаче <span class="listik-mono">{{ task.id }}</span> — ссылку можно нажать ещё раз.
      </div>
      <div v-else class="listik-row" style="margin-top: var(--space-3)">
        <UiButton size="sm" variant="secondary" @click="emit('reload')">Повторить</UiButton>
      </div>
    </UiAlert>

    <div v-if="loading && !task" class="listik-stack">
      <UiSkeleton variant="text" width="40%" />
      <UiSkeleton variant="rect" height="80px" />
      <UiSkeleton variant="rect" height="160px" />
    </div>

    <div v-else-if="task" class="listik-stack listik-drawer__body" :style="{ gap: `${drawerBodyGap}px` }">
      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">Где стоит процесс</h4>
          <span class="listik-section__hint tnum">{{ processHint }}</span>
        </div>
        <UiSteps :items="steps" :current-index="currentIndex" label="Этапы конвейера" />

          <div class="listik-row listik-drawer__process">
          <UiTooltip text="Heartbeat">
            <UiButton
              size="sm"
              variant="secondary"
              ariaLabel="Heartbeat"
              :loading="pending === 'heartbeat'"
              :disabled="!holder"
              @click="submitHeartbeat"
            >
              <template #icon><ListikIcon name="heart" size="xs" /></template>
            </UiButton>
          </UiTooltip>
          <UiTooltip v-if="task.needs_owner" text="Снять «нужен автор»">
            <UiButton
              size="sm"
              variant="secondary"
              ariaLabel="Снять «нужен автор»"
              :loading="pending === 'needs-owner'"
              @click="clearNeedsOwner"
            >
              <template #icon><ListikIcon name="check" size="xs" /></template>
            </UiButton>
          </UiTooltip>
          <UiTooltip v-else text="Нужен автор">
            <UiButton
              size="sm"
              variant="secondary"
              ariaLabel="Нужен автор"
              :loading="pending === 'needs-owner'"
              @click="openNeedsOwnerForm"
            >
              <template #icon><ListikIcon name="user" size="xs" /></template>
            </UiButton>
          </UiTooltip>
          <UiTooltip text="Следующий этап">
            <UiButton
              size="sm"
              variant="secondary"
              ariaLabel="Следующий этап"
              :disabled="task.stage === 'done'"
              :loading="pending === 'stage'"
              @click="submitStage"
            >
              <template #icon><ListikIcon name="play" size="xs" /></template>
            </UiButton>
          </UiTooltip>
            <UiTooltip text="Закрыть с результатом">
            <UiButton
              size="sm"
              variant="secondary"
              ariaLabel="Закрыть с результатом"
              :disabled="!canFinish"
              @click="closeFormOpen = !closeFormOpen"
            >
              <template #icon><ListikIcon name="flag" size="xs" /></template>
            </UiButton>
            </UiTooltip>
            <span class="listik-drawer__spacer" />
            <span class="listik-drawer__unsafe-release">
            <UiButton
              size="sm"
              variant="ghost"
              :disabled="!task.holder"
              :loading="pending === 'release'"
              @click="emit('release', { id: task.id })"
            >
              Освободить
            </UiButton>
            <span class="listik-drawer__release-sep" aria-hidden="true" />
            <UiTableActionButton
              tone="danger-quiet"
              :label="`Удалить задачу ${task.id}`"
              :disabled="pending === 'remove'"
              @click="removeOpen = true"
            >
              <template #icon><ListikIcon name="close" size="xs" /></template>
              Удалить
            </UiTableActionButton>
            </span>
          </div>

        <div class="listik-drawer__reasons">
          <div v-if="belowRowHint" class="listik-drawer__reason" :class="{ 'listik-drawer__reason--ready': deps?.ready }">
            <ListikIcon :name="deps?.ready ? 'check' : 'lock'" size="sm" />
            <span>{{ belowRowHint }}</span>
          </div>
          <div v-if="!canFinish" class="listik-drawer__reason listik-drawer__reason--warning">
            <ListikIcon name="warning" size="sm" />
            <span>закрывать нельзя: открыты дети</span>
          </div>
        </div>

        <div v-if="needsOwnerFormOpen" class="listik-row">
          <UiTextarea v-model="needsOwnerNote" :rows="2" placeholder="вопрос автору" />
          <UiButton size="sm" variant="primary" :loading="pending === 'needs-owner'" @click="submitNeedsOwner">
            Отправить вопрос
          </UiButton>
        </div>

        <div v-if="closeFormOpen" class="listik-row listik-drawer__close-action">
          <UiTextarea v-model="closeResult" :rows="2" placeholder="чем кончилось (результат)" />
          <UiButton
            size="sm"
            variant="secondary"
            :loading="pending === 'done'"
            :disabled="!closeResult.trim()"
            @click="submitDone"
          >
            <template #icon><ListikIcon name="check" size="xs" /></template>
            Закрыть с результатом
          </UiButton>
        </div>
      </section>

      <section v-if="showLaunch && routeEditable" class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">Маршрут запуска</h4>
          <span v-if="routeEditable" class="listik-section__hint">менять можно, пока задача не начата</span>
        </div>

        <template v-if="routeEditable">
          <UiAlert v-if="routesFailed" tone="warning">
            {{ routesAlert }}
            <div class="listik-row" style="margin-top: var(--space-3)">
              <UiButton size="sm" variant="secondary" :loading="store.routesLoading.value" @click="retryRoutes">
                Повторить
              </UiButton>
            </div>
          </UiAlert>

          <RoutePicker
            v-else
            :routes="store.routes.value"
            :selected-key="task.launch_route"
            :issue-type="task.issue_type ?? 'task'"
            :disabled="pending === 'route'"
            allow-clear
            @select="pickRoute"
          />

          <p v-if="!routesFailed" class="listik-section__hint">
            «Тип запуска» — запись из <span class="listik-mono">routes.json</span> (её отдаёт
            <span class="listik-mono">GET /api/routes</span>): кто исполняет задачу и по какому
            процессу. Та же матрица, что при создании: клик сразу сохраняет. Пока задача
            заведена — без этапа, держателя и запуска — маршрут можно сменить или снять
            пунктом «без маршрута» (вместе с маршрутом уезжают метки
            <span class="listik-mono">harness:</span>/<span class="listik-mono">process:</span>
            и ошибка автостарта); после начала работы сервер откажет. Сам маршрут ничего не
            запускает: процесс поднимает только галочка «Автостарт» при создании.
          </p>

          <UiAlert v-if="task.launch_error" tone="warning">
            <template #title>Автостарт не выполнен</template>
            {{ task.launch_error }}
          </UiAlert>
        </template>

      </section>

      <section class="listik-section">
        <div class="listik-cold__head">
          <div class="listik-row">
            <h4 class="listik-section__title">Холодный старт</h4>
            <UiBadge :tone="coldTone" size="sm">{{ coldOkCount }} из {{ coldRows.length }}</UiBadge>
          </div>
          <span class="listik-section__hint">что увидит принимающий по <code class="listik-mono">listik show</code></span>
        </div>
        <div class="listik-cold">
          <div v-for="row in coldRows" :key="row.key" class="listik-cold__row">
            <UiTooltip :text="row.hint ?? ''" :disabled="!row.hint">
              <UiStatusPill :tone="row.tone" size="sm" />
            </UiTooltip>
            <span class="listik-cold__key">{{ row.label }}</span>
            <span
              class="listik-cold__value"
              :class="{ 'listik-cold__value--state': row.words }"
              :style="row.color ? { color: row.color } : undefined"
            >{{ row.value }}</span>
            <UiCopyButton :value="row.value" :label="row.label">
              <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
            </UiCopyButton>
          </div>
        </div>
      </section>

      <div class="listik-drawer__holder-block">
        <div class="listik-drawer__holder-grid">
          <section class="listik-section">
            <h4 class="listik-section__title">Кто держит</h4>
            <dl class="listik-dl">
          <dt>держит</dt>
          <dd>
            <HarnessIcon :actor="task.holder" />
            {{ holderBlock?.holder }}<template v-if="holderBlock?.holderHasTitle"> · {{ holderBlock.holderAge }}</template>
          </dd>
          <template v-if="holderBlock?.notTaken">
            <dt>взята</dt>
            <dd>
              <UiBadge tone="warning" size="sm">{{ holderBlock.assigned }}</UiBadge>
              <span v-if="holderBlock.assignedBy"> — выдал {{ holderBlock.assignedBy }}.</span>
              <span>Claim от агента не приходил: прогон не запустился?</span>
            </dd>
          </template>
          <dt>heartbeat</dt>
          <dd>
            {{ holderBlock?.heartbeat }}
          </dd>
          <dt>этап с</dt>
          <dd>{{ holderBlock?.stageStarted }}</dd>
          <template v-if="holderBlock?.assignee">
            <dt>исполнитель</dt>
            <dd>{{ holderBlock.assignee }}</dd>
          </template>
            </dl>
          </section>
          <section v-if="showLaunch && !routeEditable" class="listik-section">
            <h4 class="listik-section__title">Автостарт</h4>
            <dl class="listik-dl">
              <dt>маршрут</dt>
              <dd class="listik-row listik-drawer__nowrap">
                <RouteIcon v-if="launchRoute" :route="launchRoute" size="sm" :title="`маршрут: ${launchRoute.title}`" />
                <span class="listik-mono">{{ task.launch_route || '—' }}</span>
              </dd>
              <template v-if="task.launched_by === 'listik'">
                <dt>запуск</dt>
                <dd>
                  запущена Listik<template v-if="task.launch_pid != null"> · pid {{ task.launch_pid }}</template>
                  <template v-if="task.launched_at"> · {{ formatDateTime(task.launched_at) }}</template>
                </dd>
              </template>
              <template v-if="launchStatus"><dt>статус</dt><dd>{{ launchStatus }}</dd></template>
              <template v-if="task.launch_error">
                <dt>ошибка</dt>
                <dd class="listik-row listik-drawer__nowrap listik-drawer__start"><ListikIcon name="warning" size="xs" /><span>{{ task.launch_error }}</span></dd>
              </template>
              <template v-if="task.launch_log">
                <dt>лог</dt>
                <dd class="listik-row listik-drawer__nowrap"><span class="listik-mono">{{ task.launch_log }}</span><UiCopyButton :value="task.launch_log" label="Путь к логу запуска"><template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template></UiCopyButton></dd>
              </template>
            </dl>
          </section>
        </div>
        <dl class="listik-dl">
          <dt>что делает</dt>
          <dd>{{ holderBlock?.note }}</dd>
        </dl>
      </div>

      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">Журнал и вердикты</h4>
          <IconToggle
            :model-value="feedFilter"
            :options="feedFilterOptions"
            ariaLabel="Фильтр ленты"
            size="sm"
            @update:model-value="setFeedFilter"
          >
            <template #icon="{ option }">
              <span class="listik-feed-filter">
                <ListikIcon v-if="feedFilterIcon(option.value)" :name="feedFilterIcon(option.value) ?? 'dot'" size="xs" />
                <span v-else>{{ option.label }}</span>
                <span class="tnum">{{ feedFilterCount(option.value) }}</span>
              </span>
            </template>
          </IconToggle>
        </div>

        <!-- Открытый вопрос закреплён сверху — дополнительная копия: тот же
             вопрос остаётся и в ленте ниже (см. FeedRow). -->
        <div v-if="task.needs_owner" class="listik-feed-pinned">
          <div class="listik-feed-pinned__head">
            <span class="listik-feed-pinned__icon"><ListikIcon name="question" size="sm" /></span>
            <span class="listik-feed-pinned__title">ждёт ответа</span>
            <span class="listik-feed-pinned__meta tnum">{{ pinnedAuthor }} · {{ pinnedAge }}</span>
          </div>
          <p class="listik-feed-pinned__text">{{ pinnedText }}</p>
          <div class="listik-feed-pinned__reply">
            <UiInput
              v-model="pinnedAnswerText"
              size="sm"
              placeholder="ответить…"
              v-bind="{ 'aria-label': 'Ответ на вопрос', onKeydown: onPinnedAnswerKeydown }"
            />
            <UiButton
              size="sm"
              variant="ghost"
              ariaLabel="Ответить"
              :loading="pending === 'needs-owner' || pending === 'answer'"
              :disabled="!pinnedAnswerText.trim()"
              @click="submitPinnedAnswer"
            >
              <template #icon><ListikIcon name="send" size="xs" /></template>
            </UiButton>
          </div>
        </div>

        <UiTimeline :items="feed" dense :empty-title="feedEmptyState.title" :empty-description="feedEmptyState.description">
          <template #marker="{ item }">
            <span
              class="listik-feed-marker"
              :style="{ background: asFeedRow(item).marker.bg, color: asFeedRow(item).marker.color }"
              :title="asFeedRow(item).marker.hint"
            >
              <ListikIcon :name="asFeedRow(item).marker.icon" size="xs" />
            </span>
          </template>
          <template #content="{ item }">
            <div class="listik-feed-row">
              <span v-if="asFeedRow(item).authorLine" class="listik-feed-row__meta tnum">{{ asFeedRow(item).authorLine }}</span>
              <p v-if="asFeedRow(item).text" class="listik-feed-row__text">{{ asFeedRow(item).text }}</p>
              <p v-if="asFeedRow(item).subtext" class="listik-feed-row__subtext">{{ asFeedRow(item).subtext }}</p>
            </div>
            <div v-if="asFeedRow(item).answer" class="listik-feed-answer">
              <span
                class="listik-feed-marker listik-feed-marker--sm"
                :style="{ background: asFeedRow(item).answer!.bg, color: asFeedRow(item).answer!.color }"
                :title="asFeedRow(item).answer!.hint"
              >
                <ListikIcon :name="asFeedRow(item).answer!.icon" size="xs" />
              </span>
              <div class="listik-feed-row">
                <span class="listik-feed-row__meta tnum">{{ asFeedRow(item).answer!.author }} · {{ asFeedRow(item).answer!.time }}</span>
                <p class="listik-feed-row__text">{{ asFeedRow(item).answer!.text }}</p>
              </div>
            </div>
          </template>
        </UiTimeline>

        <!-- «Поле как единая рамка»: composer раскладывает вид/текст/отправку в
             одной визуальной рамке, а не тремя китовыми контролами подряд —
             своей рамки у UiInput внутри приглушены стилем (см. app.css). -->
        <div class="listik-feed-composer">
          <IconToggle
            v-model="feedKind"
            :options="feedKindOptions"
            ariaLabel="Вид записи"
            size="sm"
          >
            <template #icon="{ option }">
              <ListikIcon :name="commentKind(option.value).icon" size="xs" />
            </template>
          </IconToggle>
          <UiInput
            ref="feedInputRef"
            v-model="feedText"
            size="sm"
            class="listik-feed-composer__input"
            :placeholder="feedPlaceholder"
            v-bind="{ 'aria-label': 'Текст записи', onKeydown: onFeedKeydown }"
          />
          <UiButton
            size="sm"
            variant="ghost"
            ariaLabel="Отправить"
            :loading="pending === 'comment' || pending === 'answer'"
            :disabled="!feedText.trim()"
            @click="submitFeed"
          >
            <template #icon><ListikIcon name="send" size="xs" /></template>
          </UiButton>
        </div>
      </section>

      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">
            Связи
            <!-- Сводка иконками: ноль не прячем, только приглушаем. -->
            <span class="listik-dep-summary">
              <UiTooltip v-for="item in depCounters" :key="item.kind" :text="DEP_SUMMARY[item.kind].label">
                <span
                  class="listik-dep-summary__item"
                  :class="{
                    'listik-dep-summary__item--zero': item.zero,
                    'listik-dep-summary__item--danger': item.danger,
                  }"
                >
                  <ListikIcon :name="DEP_SUMMARY[item.kind].icon" size="sm" />
                  <span>{{ item.value }}</span>
                </span>
              </UiTooltip>
            </span>
          </h4>
          <span class="listik-row" style="gap: var(--space-1); flex-wrap: nowrap">
            <UiTooltip text="Дерево связей">
              <UiButton
                size="sm"
                variant="ghost"
                ariaLabel="Дерево связей"
                :loading="depTreeLoading"
                @click="loadTree"
              >
                <template #icon><ListikIcon name="timeline" size="sm" /></template>
              </UiButton>
            </UiTooltip>
            <UiTooltip text="Добавить связь">
              <UiButton
                size="sm"
                variant="ghost"
                ariaLabel="Добавить связь"
                @click="depFormOpen = !depFormOpen"
              >
                <template #icon><ListikIcon name="plus" size="sm" /></template>
              </UiButton>
            </UiTooltip>
          </span>
        </div>

        <div v-if="depFormOpen" class="listik-row">
          <UiInput
            v-model="newDep"
            size="sm"
            placeholder="ID задачи-блокера"
            v-bind="{ 'aria-label': 'ID блокера', onKeydown: onDepKeydown }"
          />
          <UiButton size="sm" variant="secondary" :disabled="!newDep.trim()" @click="submitDep">
            Добавить связь
          </UiButton>
        </div>

        <!-- Открытые блокеры — единственное, что требует внимания: красные строки сверху. -->
        <div v-if="depBlock.blockers.length" class="listik-dep-blockers">
          <article
            v-for="(row, index) in depBlock.blockers"
            :key="`b-${index}-${row.dep.id}`"
            class="listik-dep-blocker"
          >
            <ListikIcon name="lock" size="sm" class="listik-dep-blocker__icon" />
            <span class="listik-dep-blocker__verb">ждёт</span>
            <button
              v-if="row.linked"
              type="button"
              class="listik-link listik-mono"
              @click="emit('open-other', row.dep.id)"
            >
              {{ row.dep.id }}
            </button>
            <span v-else class="listik-mono">{{ row.dep.id }}</span>
            <span class="listik-dep-blocker__title">{{ row.dep.title }}</span>
            <UiBadge v-if="row.dep.missing" tone="danger" size="sm">задача не найдена</UiBadge>
            <UiBadge v-if="row.dep.stage" tone="info" size="sm">{{ stageCode(row.dep.stage) }}</UiBadge>
            <span class="listik-dep-blocker__hint">{{ depCardHint(row.dep) }}</span>
          </article>
        </div>

        <UiAlert v-if="blockedBy.length > 0 && blockersIdle" tone="warning">
          <template #title>Блокеры стоят без движения</template>
          Ждать молча бессмысленно: возьмите блокер сами или поставьте ему «нужен автор».
        </UiAlert>

        <!-- Компактное дерево «родитель → эта задача → дети»; без родителя и детей его нет. -->
        <div v-if="depBlock.parent || depBlock.children.length" class="listik-dep-tree">
          <div v-if="depBlock.parent" class="listik-dep-tree__row listik-dep-tree__row--parent">
            <ListikIcon name="epic" size="sm" />
            <button
              v-if="depBlock.parent.linked"
              type="button"
              class="listik-link listik-mono"
              @click="emit('open-other', depBlock.parent.dep.id)"
            >
              {{ depBlock.parent.dep.id }}
            </button>
            <span v-else class="listik-mono">{{ depBlock.parent.dep.id }}</span>
            <span class="listik-dep-tree__title">{{ depBlock.parent.dep.title }}</span>
          </div>
          <div class="listik-dep-tree__row listik-dep-tree__row--self">
            <span class="listik-dep-tree__dot" />
            <span class="listik-dep-tree__self">эта задача</span>
            <span v-if="childrenTotal" class="listik-dep-tree__progress">
              <UiProgress
                :value="childrenDone"
                :max="childrenTotal"
                size="sm"
                :label="`дети: ${childrenDone} из ${childrenTotal} готово`"
              />
              <span class="listik-dep-tree__progress-text">{{ childrenDone }} из {{ childrenTotal }} готово</span>
            </span>
          </div>
          <div
            v-for="(child, index) in depBlock.children"
            :key="`c-${index}-${child.id}`"
            class="listik-dep-tree__row listik-dep-tree__row--child"
          >
            <UiBadge v-if="child.stage" tone="info" size="sm">{{ stageCode(child.stage) }}</UiBadge>
            <button
              v-if="child.linked"
              type="button"
              class="listik-link listik-mono"
              @click="emit('open-other', child.id)"
            >
              {{ child.id }}
            </button>
            <span v-else class="listik-mono">{{ child.id }}</span>
            <span class="listik-dep-tree__title">{{ child.title }}</span>
            <span class="listik-dep-tree__holder">
              <ListikIcon name="user" size="xs" />
              {{ childHolder(child) }}
            </span>
          </div>
        </div>

        <!-- «Её ждут» в макете нет, но данные не теряем: строка-подсказка под деревом. -->
        <p v-if="depBlock.waiting.length" class="listik-section__hint">
          её ждут:
          <template v-for="(row, index) in depBlock.waiting" :key="`w-${index}-${row.dep.id}`">
            <button
              v-if="row.linked"
              type="button"
              class="listik-link listik-mono"
              @click="emit('open-other', row.dep.id)"
            >
              {{ row.dep.id }}
            </button>
            <span v-else class="listik-mono">{{ row.dep.id }}</span><span v-if="index < depBlock.waiting.length - 1">, </span>
          </template>
        </p>

        <!-- Мягкие связи — одной строкой чипами: иконка типа, id, короткое название. -->
        <div v-if="depBlock.soft.length" class="listik-dep-chips">
          <UiTooltip
            v-for="(row, index) in depBlock.soft"
            :key="`s-${index}-${row.dep.id}`"
            :text="linkTypeLabel(row.dep.dep_type, row.dep.dep_title)"
          >
            <span class="listik-dep-chip">
              <ListikIcon :name="row.type.icon" size="sm" />
              <button
                v-if="row.linked"
                type="button"
                class="listik-link listik-mono"
                @click="emit('open-other', row.dep.id)"
              >
                {{ row.dep.id }}
              </button>
              <span v-else class="listik-mono">{{ row.dep.id }}</span>
              <span class="listik-dep-chip__title">{{ row.dep.title }}</span>
            </span>
          </UiTooltip>
        </div>

        <UiAlert v-if="!deps" tone="info">
          Сервер не отдал вердикт по зависимостям — возможно, старая версия API.
          Показаны только связи из карточки задачи.
        </UiAlert>

        <!-- Сводка связей: id через запятую, каждый — ссылка, открывающая задачу
             в этой же панели. Строится по спискам карточки, поэтому показывает и
             мягкие входящие связи, которых нет ни в blocked_by, ни в waiting_for;
             id, уже показанные строками и чипами выше, в сводку не попадают. -->
        <p v-if="dependenciesSummary.length" class="listik-section__hint">
          зависит от
          <template v-for="(dep, index) in dependenciesSummary" :key="`d-${dep.depends_on}`">
            <button type="button" class="listik-link listik-mono" @click="emit('open-other', dep.depends_on)">
              {{ dep.depends_on }}
            </button><span v-if="index < dependenciesSummary.length - 1">, </span>
          </template>
        </p>
        <p v-if="dependentsSummary.length" class="listik-section__hint">
          от неё зависит
          <template v-for="(dep, index) in dependentsSummary" :key="`r-${dep.issue_id}`">
            <button type="button" class="listik-link listik-mono" @click="emit('open-other', dep.issue_id)">
              {{ dep.issue_id }}
            </button><span v-if="index < dependentsSummary.length - 1">, </span>
          </template>
        </p>

        <div v-if="depTree" class="listik-stack">
          <h5 class="listik-subtitle">Дерево (глубина 3)</h5>
          <ul class="listik-dep-list">
            <li v-for="entry in depTree.waits_for" :key="`tw-${entry.id}`" class="listik-dep">
              <span class="listik-mono">ждёт {{ entry.id }}</span>
              <span class="listik-dep__title">{{ entry.title }}</span>
              <span class="listik-dep__note">{{ entry.status }} · {{ hasHolderTitle(entry.holder_title) ? entry.holder_title : 'без держателя' }}</span>
            </li>
            <li v-for="entry in depTree.waited_by" :key="`tb-${entry.id}`" class="listik-dep">
              <span class="listik-mono">её ждёт {{ entry.id }}</span>
              <span class="listik-dep__title">{{ entry.title }}</span>
              <span class="listik-dep__note">{{ entry.status }}</span>
            </li>
          </ul>
        </div>
      </section>

      <section class="listik-section">
        <h4 class="listik-section__title">Описание · ТЗ</h4>
        <p class="listik-prose">{{ task.description || '—' }}</p>
        <h4 class="listik-section__title">Критерии приёмки</h4>
        <p class="listik-prose">{{ task.acceptance || '—' }}</p>
        <template v-if="task.design">
          <h4 class="listik-section__title">Дизайн</h4>
          <p class="listik-prose">{{ task.design }}</p>
        </template>
        <template v-if="task.notes">
          <h4 class="listik-section__title">Заметки</h4>
          <p class="listik-prose">{{ task.notes }}</p>
        </template>
        <template v-if="task.result">
          <h4 class="listik-section__title">Результат</h4>
          <p class="listik-prose">{{ task.result }}</p>
        </template>
      </section>
    </div>
  </UiDrawer>


  <!-- Сосед дровера, не содержимое: оба оверлея телепортируются в body. -->
  <UiConfirmDialog
    v-model="removeOpen"
    tone="danger"
    title="Удалить задачу?"
    :description="`Задача ${task?.id ?? ''} исчезнет безвозвратно — вместе с комментариями, связями и историей. Если нужна запись, закройте её с результатом.`"
    confirm-label="Удалить"
    cancel-label="Отмена"
    :loading="pending === 'remove'"
    @confirm="submitRemove"
  />
</template>

<!-- Стили панели (шапка, промежуток тела, .listik-cold*, .listik-dep*) —
     в assets/app.css, раздел «Панель задачи»: заголовок телепортируется в
     body вместе с data-v-атрибутом, а .listik-stack переиспользуется и вне
     этого компонента (общие «мелочи страницы»). -->
