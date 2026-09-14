<script setup lang="ts">
/**
 * Панель задачи (UiDrawer справа, ~720px). Три слоя:
 * - шапка (#header): тип/проект/подсказка о родителе, заголовок, ряд пилюль
 *   и бейджей с id справа;
 * - «Где стоит процесс»: степпер s1…s4→done с описанием прошлого/текущего/
 *   будущего шага, ряд действий (heartbeat/needs-owner/next-stage/release/
 *   удалить/claim-сплит-кнопка) и подсказка под ним;
 * - «Холодный старт», «Кто держит», «Журнал и вердикты» (единая лента
 *   комментариев+событий с фильтром и формой отправки), «Связи» (сетка
 *   карточек по группам), «Описание и критерии».
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
  UiSegmented,
  UiSelect,
  UiSkeleton,
  UiSplitButton,
  UiStatusPill,
  UiSteps,
  UiTextarea,
  UiTimeline,
  UiTooltip,
  type StatusPillTone,
  type UiSegmentedOption,
  type UiSelectOption,
  type UiSplitButtonItem,
  type UiStepItem,
  type UiStepStatus,
  type UiTimelineItem,
  type UiTimelineTone,
} from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import ProjectMark from './marks/ProjectMark.vue'
import RouteIcon from './marks/RouteIcon.vue'
import TaskGlyph from './marks/TaskGlyph.vue'
import { priority, worktreeState, worktreeValue } from '@/lib/dictionaries'
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
import { HEALTH_TITLES, healthReason, taskHealth } from '@/lib/health'
import { HARNESS_TITLES, harnessOf } from '@/lib/harness'
import { routeAllowedForType, routeByKey, routesAlertText } from '@/lib/routes'
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
const claimOpen = ref(false)
const claimWarningOpen = ref(false)
const forceDialogOpen = ref(false)
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
/**
 * Связи карточки как они лежат в `deps` (обе стороны, включая мягкие входящие).
 * `deps_state` для сводки не годится: в `soft_links` из входящих связей есть только
 * `discovered-from` («найдена при», см. listik-0wpx), а остального «кто ссылается
 * на эту задачу» там нет.
 */
const dependencies = computed<TaskDep[]>(() => props.task?.dependencies ?? [])
const dependents = computed<TaskDependent[]>(() => props.task?.dependents ?? [])
/**
 * id, уже показанные выше отдельными карточками/строками: blocked_by,
 * waiting_for, children_open, parent, soft_links. В сводке такие id не
 * повторяются — иначе одна и та же ссылка рисуется дважды.
 */
const groupedIds = computed<Set<string>>(() => {
  const ids = new Set<string>()
  for (const dep of [...blockedBy.value, ...waitingFor.value, ...childrenOpen.value, ...softLinks.value]) {
    ids.add(dep.id)
  }
  if (parentDep.value) ids.add(parentDep.value.id)
  return ids
})
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
/** Серверная семантика «взять можно»: нет блокеров, нет держателя, не закрыта. */
const canClaim = computed(() => deps.value?.ready === true)
/** Блокеры стоят без движения: ни держателя, ни свежего heartbeat. */
const blockersIdle = computed(
  () => blockedBy.value.length > 0 && blockedBy.value.every((dep) => dep.missing || dep.stale || !dep.holder),
)

function projectOf(task: TaskDetail): ProjectRow | null {
  return props.projects?.find((project) => project.slug === task.project) ?? null
}

/**
 * `holder_title`/`dep.holder_title` пусты только у мока: сервер (`actors.py display()`,
 * `store.py`) для отсутствующего держателя отдаёт заглушку «—», а не пустую строку —
 * поэтому «есть держатель» проверяется и на непустоту, и на то, что это не «—».
 */
function hasHolderTitle(title: string | null | undefined): title is string {
  return Boolean(title) && title !== '—'
}

/** Держатель/актор коротко: agent:dsh/dsh-flash → dsh, human me → я, иначе сам ключ. */
function actorShort(key: string | null | undefined): string {
  if (!key) return '—'
  const trimmed = key.trim()
  if (!trimmed) return '—'
  const harness = harnessOf(trimmed)
  if (harness && harness !== 'human') return HARNESS_TITLES[harness]
  return trimmed === 'me' ? 'я' : trimmed
}

/** Пилюля здоровья без дубля со status_title: закрытая → «закрыта», dead/unknown —
 *  только причина, healthy/at-risk — заголовок + причина. */
function healthPillText(task: TaskDetail): string {
  if (task.status === 'done' || task.status === 'cancelled') return 'закрыта'
  const health = taskHealth(task)
  if (health === 'dead' || health === 'unknown') return healthReason(task)
  return `${HEALTH_TITLES[health]} · ${healthReason(task)}`
}

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

/**
 * Пункт «без маршрута»: значение-пустышка выбора. Пустая строка — это и есть
 * «снять маршрут» на сервере (`set launch_route=`), `null` у выбора означает
 * «ничего не выбрано» и до сохранения не доводит.
 */
const NO_ROUTE = ''

/** Черновик выбора: в задачу уходит только по кнопке, а не на каждый клик. */
const routeDraft = ref<string | null>(null)

watch(
  () => props.task?.launch_route,
  (value) => {
    routeDraft.value = value ?? NO_ROUTE
  },
  { immediate: true },
)

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

const routeOptions = computed<UiSelectOption<string>[]>(() => {
  if (routesFailed.value) return []
  const type = props.task?.issue_type ?? 'task'
  // «Без маршрута» — первым пунктом: у заведённой задачи маршрут можно не только
  // сменить, но и снять совсем (раньше это умел только CLI).
  const options: UiSelectOption<string>[] = [
    { value: NO_ROUTE, label: 'без маршрута' },
    ...store.routes.value
      .filter((route) => route.visible)
      .map((route) => ({
        value: route.key,
        label: route.title,
        // Те же правила, что в «Новой задаче»: эпику нужен этап ТЗ, прямой маршрут закрыт.
        disabled: !routeAllowedForType(route, type),
      })),
  ]
  const current = props.task?.launch_route ?? null
  // Текущий маршрут может быть скрыт, устареть или ещё не приехать вместе со
  // списком: показываем его отдельной строкой, а не пустой подписью селекта.
  if (current && !options.some((option) => option.value === current)) {
    options.unshift({ value: current, label: current, disabled: true })
  }
  return options
})

const routeDirty = computed(() => routeDraft.value !== (props.task?.launch_route ?? NO_ROUTE))

/**
 * Смена маршрута: на сервер уходит одним полем `route`. Метки маршрута
 * (`harness:<…>`/`process:<…>`) сервер переписывает сам — старые снимает, метки
 * нового ставит, чужие метки задачи оставляет: то же правило, что при создании
 * (`routes.labels_for`), поэтому доска их не считает.
 *
 * Пункт «без маршрута» шлёт пустую строку — маршрут снимается совсем (как
 * `set launch_route=`): сервер убирает и его метки, и ошибку автостарта.
 */
function submitRoute(): void {
  if (!props.task || !routeDirty.value) return
  emit('patch', {
    id: props.task.id,
    body: { route: routeDraft.value ?? NO_ROUTE },
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
  if (task.launched_by === 'listik' && !task.launch_finished_at) return 'идёт'
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
    claimOpen.value = false
    claimWarningOpen.value = false
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

const splitItems = computed<UiSplitButtonItem[]>(() => [
  {
    key: 'force',
    label: 'Взять всё равно (обход блокеров)',
    danger: true,
    disabled: !(blockedBy.value.length > 0 && !deps.value?.holder),
  },
  { key: 'done', label: 'Закрыть с результатом…', disabled: !canFinish.value },
  { key: 'dep', label: 'Добавить связь…' },
])

function onClaimMainClick(): void {
  if (canClaim.value) {
    claimWarningOpen.value = false
    claimOpen.value = !claimOpen.value
  } else {
    claimOpen.value = false
    claimWarningOpen.value = !claimWarningOpen.value
  }
}

function onSplitSelect(item: UiSplitButtonItem): void {
  if (item.key === 'force') forceDialogOpen.value = true
  else if (item.key === 'done') closeFormOpen.value = true
  else if (item.key === 'dep') depFormOpen.value = true
}

function submitClaim(force = false): void {
  if (!props.task) return
  const value = (holder.value ?? '').trim()
  if (!value) return
  try {
    window.localStorage.setItem(HOLDER_KEY, value)
  } catch {
    /* не критично */
  }
  emit('claim', { id: props.task.id, holder: value, force })
  claimOpen.value = false
  forceDialogOpen.value = false
}

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

const actorOptions = computed<UiSelectOption<string>[]>(() => {
  const list = props.actors ?? []
  const options: UiSelectOption<string>[] = list.map((actor) => ({
    value: actor.key,
    label: actor.title || actor.key,
  }))
  if (!options.some((option) => option.value === 'me')) options.unshift({ value: 'me', label: 'я (me)' })
  return options
})

// ── «Холодный старт» ──────────────────────────────────────────────────────

interface ColdRow {
  key: string
  label: string
  /** Заполнено ли поле для счётчика «Холодный старт N из M»: жёлтое состояние — заполнено. */
  ok: boolean
  value: string
  /** Тон точки-статуса (`UiStatusPill`): success — заполнено, warning — основная ветка, danger — пусто. */
  tone: 'success' | 'warning' | 'danger'
  /** Цвет значения и пояснение — из справочника `WORKTREE_STATES` (строка worktree). */
  color?: string
  hint?: string
  /** Значение — слова состояния, а не путь: строку показываем не моноширинной. */
  words?: boolean
}

const coldRows = computed<ColdRow[]>(() => {
  const task = props.task
  if (!task) return []
  const rows: ColdRow[] = []
  const simpleRow = (key: string, label: string, ok: boolean, value: string): ColdRow => ({
    key,
    label,
    ok,
    value,
    tone: ok ? 'success' : 'warning',
  })
  rows.push(simpleRow('spec_path', 'spec_path', Boolean(task.spec_path),
                  task.spec_path || 'ТЗ не привязано'))
  const checklistPath = task.checklist_path ?? null
  const acceptanceText = task.acceptance?.trim() || ''
  rows.push(simpleRow('acceptance', 'acceptance', Boolean(checklistPath) || Boolean(acceptanceText),
                  checklistPath || (acceptanceText ? acceptanceText.split('\n')[0] : 'чек-листа нет')))
  const journalRef = task.decision_path ?? task.journal_path
  rows.push(simpleRow('journal_path', 'journal_path', Boolean(journalRef), journalRef || 'журнала нет'))
  // Три состояния строки: жёлтое «работа в main» (маркер основной ветки),
  // зелёное с путём и веткой, красное «рабочее дерево не указано». Жёлтое —
  // заполненное поле, поэтому в счётчике холодного старта идёт как ok.
  const worktree = worktreeState(task.worktree, task.branch)
  rows.push({
    key: 'worktree',
    label: 'worktree · branch',
    ok: worktree.filled,
    value: worktreeValue(task.worktree, task.branch),
    tone: worktree.tone,
    color: worktree.color,
    hint: worktree.hint,
    words: !worktree.mono,
  })
  const blockedIds = blockedBy.value.map((dep) => dep.id)
  const waitingIds = waitingFor.value.map((dep) => dep.id)
  let blocksValue = blockedIds.length ? `ждёт ${blockedIds.join(', ')}` : 'ничего не ждёт'
  if (waitingIds.length) blocksValue += ` · её ждут ${waitingIds.join(', ')}`
  rows.push(simpleRow('blocks', 'blocks', true, blocksValue))
  const journalComments = task.comments
    .filter((comment) => comment.kind === 'journal')
    .sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  const lastJournal = journalComments[journalComments.length - 1]
  rows.push(simpleRow('comment_journal', 'comment -k journal', Boolean(lastJournal),
                  lastJournal ? lastJournal.text.slice(0, 80) : 'журнальных записей нет'))
  const reviewComments = task.comments.filter((comment) => comment.kind === 'review')
  rows.push(simpleRow('comment_review', 'comment -k review',
                  Boolean(task.review_path) || reviewComments.length > 0,
                  task.review_path || (reviewComments.length ? `${reviewComments.length} замечаний` : 'ревью нет')))
  return rows
})

const coldOkCount = computed(() => coldRows.value.filter((row) => row.ok).length)

/** Тон счётчика: всё заполнено — зелёный; красная строка (worktree не указан) — красный; иначе жёлтый. */
const coldTone = computed<'success' | 'warning' | 'danger'>(() => {
  const rows = coldRows.value
  if (rows.length && rows.every((row) => row.ok)) return 'success'
  return rows.some((row) => row.tone === 'danger') ? 'danger' : 'warning'
})

// ── «Журнал и вердикты» ───────────────────────────────────────────────────

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

const feedFilter = ref<'all' | 'journal' | 'review' | 'verdict'>('all')

function setFeedFilter(value: string): void {
  if (value === 'all' || value === 'journal' || value === 'review' || value === 'verdict') feedFilter.value = value
}

const feedFilterOptions: UiSegmentedOption[] = [
  { value: 'all', label: 'всё' },
  { value: 'journal', label: 'журнал' },
  { value: 'review', label: 'ревью' },
  { value: 'verdict', label: 'вердикт' },
]

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

function eventTone(event: TaskEvent): UiTimelineTone {
  if (event.kind === 'release') return 'warning'
  if (event.kind === 'stage') {
    const match = event.note?.match(/\((sticky|handoff)\)/)
    return match?.[1] === 'handoff' ? 'accent' : 'neutral'
  }
  return 'neutral'
}

function commentTone(comment: TaskComment): UiTimelineTone {
  if (comment.kind === 'verdict') {
    const lower = comment.text.trim().toLowerCase()
    if (lower.startsWith('красн') || lower.startsWith('red') || lower.startsWith('fail')) return 'danger'
    return 'accent'
  }
  if (comment.kind === 'review') return 'info'
  if (comment.kind === 'question') return 'warning'
  if (comment.kind === 'answer') return 'success'
  return 'neutral'
}

const feed = computed<UiTimelineItem[]>(() => {
  const task = props.task
  if (!task) return []
  const filter = feedFilter.value
  const comments = task.comments.filter((comment) => {
    if (filter === 'all') return true
    if (filter === 'journal') return comment.kind === 'journal'
    if (filter === 'review') return comment.kind === 'review'
    return comment.kind === 'verdict'
  })
  const events =
    filter === 'all'
      ? task.events.filter((event) => ALL_EVENT_KINDS.has(event.kind))
      : filter === 'journal'
        ? task.events.filter((event) => event.kind === 'stage')
        : []
  const commentItems: UiTimelineItem[] = comments.map((comment) => ({
    id: `c-${comment.id}`,
    title: `${commentKindTitle(comment.kind)} · ${comment.author || 'без автора'}`,
    description: comment.text,
    timestamp: formatDateTime(comment.created_at),
    datetime: datetimeAttr(comment.created_at),
    meta: `comment -k ${comment.kind}`,
    tone: commentTone(comment),
  }))
  const eventItems: UiTimelineItem[] = events.map((event, index) => ({
    id: `e-${event.ts}-${index}`,
    title: eventTitle(event),
    description: eventDescription(event),
    timestamp: `${formatDateTime(event.ts)} · ${humanAge(event.ts)}`,
    datetime: datetimeAttr(event.ts),
    meta: [event.actor, event.harness].filter(Boolean).join(' · ') || undefined,
    tone: eventTone(event),
  }))
  const all = [...commentItems, ...eventItems]
  all.sort((a, b) => (b.datetime ?? '').localeCompare(a.datetime ?? ''))
  return all
})

const feedEmptyState = computed<{ title: string; description?: string }>(() => {
  if (feedFilter.value !== 'verdict' || feed.value.length > 0) return { title: 'Записей нет' }
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

const COMMENT_KINDS: CommentKind[] = ['comment', 'journal', 'question', 'answer', 'review', 'verdict']

const feedKind = ref<CommentKind | null>('journal')
const feedText = ref('')

const feedKindOptions: UiSelectOption<CommentKind>[] = COMMENT_KINDS.map((kind) => ({
  value: kind,
  label: commentKindTitle(kind),
}))

function onFeedKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter') submitFeed()
}

function submitFeed(): void {
  if (!props.task) return
  const text = feedText.value.trim()
  if (!text) return
  const kind = feedKind.value ?? 'comment'
  if (kind === 'answer') {
    emit('needsOwner', { id: props.task.id, value: false, note: text })
  } else if (kind === 'question') {
    emit('needsOwner', { id: props.task.id, value: true, note: text })
  } else {
    emit('comment', { id: props.task.id, text, kind, author: (holder.value ?? '').trim() || undefined })
  }
  feedText.value = ''
}

// ── «Связи» ────────────────────────────────────────────────────────────────

function depCardHint(dep: DepInfo): string {
  const base = hasHolderTitle(dep.holder_title) ? `держит ${dep.holder_title} · ${dep.holder_age}` : 'без держателя'
  return dep.stale ? `${base} · стоит без движения` : base
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
          <ProjectMark :project="projectOf(task)" :slug="task.project" with-title size="sm" />
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
          <UiBadge v-for="label in task.labels" :key="label" tone="info" size="sm">{{ label }}</UiBadge>
          <UiBadge v-if="waitingFor.length" tone="accent" size="sm">её ждут {{ waitingFor.length }}</UiBadge>
          <UiBadge v-if="blockedBy.length" tone="warning" size="sm">ждёт {{ blockedBy.length }}</UiBadge>
          <UiBadge v-if="task.needs_owner" tone="accent" size="sm">нужен ты</UiBadge>
          <span class="listik-drawer__id">
            <span class="listik-mono">{{ task.id }}</span>
            <UiCopyButton :value="task.id" label="ID задачи">
              <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
            </UiCopyButton>
          </span>
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

    <div v-else-if="task" class="listik-stack listik-drawer__body">
      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">Где стоит процесс</h4>
          <span class="listik-section__hint tnum">{{ processHint }}</span>
        </div>
        <UiSteps :items="steps" :current-index="currentIndex" label="Этапы конвейера" />

        <div class="listik-row listik-drawer__process">
          <UiButton
            size="sm"
            variant="secondary"
            :loading="pending === 'heartbeat'"
            :disabled="!holder"
            @click="submitHeartbeat"
          >
            <template #icon><ListikIcon name="clock" size="xs" /></template>
            Heartbeat
          </UiButton>
          <UiButton
            v-if="task.needs_owner"
            size="sm"
            variant="secondary"
            :loading="pending === 'needs-owner'"
            @click="clearNeedsOwner"
          >
            <template #icon><ListikIcon name="check" size="xs" /></template>
            Снять «нужен автор»
          </UiButton>
          <UiButton v-else size="sm" variant="secondary" :loading="pending === 'needs-owner'" @click="openNeedsOwnerForm">
            <template #icon><ListikIcon name="warning" size="xs" /></template>
            Нужен автор
          </UiButton>
          <UiButton
            size="sm"
            variant="secondary"
            :disabled="task.stage === 'done'"
            :loading="pending === 'stage'"
            @click="submitStage"
          >
            <template #icon><ListikIcon name="bolt" size="xs" /></template>
            Следующий этап
          </UiButton>
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
            <UiButton
              size="sm"
              variant="ghost"
              :loading="pending === 'remove'"
              v-bind="{ 'aria-label': `Удалить задачу ${task.id}` }"
              @click="removeOpen = true"
            >
              <template #icon><ListikIcon name="close" size="xs" /></template>
              Удалить
            </UiButton>
          </span>
          <span class="listik-drawer__spacer" />
          <span :data-can-claim="canClaim">
            <UiSplitButton
              variant="primary"
              size="sm"
              :items="splitItems"
              :loading="pending === 'claim'"
              @click="onClaimMainClick"
              @select="onSplitSelect"
            >
              <template #icon><ListikIcon name="hand" size="xs" /></template>
              Взять в работу
            </UiSplitButton>
          </span>
        </div>

        <div class="listik-row">
          <span class="listik-section__hint">{{ belowRowHint }}</span>
          <UiBadge v-if="!canFinish" tone="warning" size="sm">закрывать нельзя: открыты дети</UiBadge>
        </div>

        <UiAlert v-if="claimWarningOpen" tone="warning">
          <template #title>Взять нельзя</template>
          <ul class="listik-verdict__reasons">
            <li v-for="reason in reasons" :key="reason">{{ reason }}</li>
          </ul>
        </UiAlert>

        <div v-if="claimOpen" class="listik-row">
          <UiSelect
            v-model="holder"
            :options="actorOptions"
            size="sm"
            placeholder="кто берёт"
            v-bind="{ 'aria-label': 'Кто берёт задачу' }"
          />
          <UiButton size="sm" variant="primary" :loading="pending === 'claim'" @click="submitClaim(false)">
            Подтвердить
          </UiButton>
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

        <div v-if="depFormOpen" class="listik-row">
          <UiInput v-model="newDep" size="sm" placeholder="ID задачи-блокера" v-bind="{ 'aria-label': 'ID блокера' }" />
          <UiButton size="sm" variant="secondary" :disabled="!newDep.trim()" @click="submitDep">
            Добавить связь
          </UiButton>
        </div>
      </section>

      <section v-if="showLaunch" class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">{{ routeEditable ? 'Маршрут запуска' : 'Автостарт' }}</h4>
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

          <div v-else class="listik-row">
            <UiSelect
              v-model="routeDraft"
              :options="routeOptions"
              size="sm"
              placeholder="маршрут не выбран"
              :disabled="pending === 'route'"
              v-bind="{ 'aria-label': 'Маршрут запуска' }"
            />
            <UiButton
              size="sm"
              variant="secondary"
              :disabled="!routeDirty"
              :loading="pending === 'route'"
              @click="submitRoute"
            >
              Сохранить маршрут
            </UiButton>
          </div>

          <p v-if="!routesFailed" class="listik-section__hint">
            «Тип запуска» — запись из <span class="listik-mono">routes.json</span> (её отдаёт
            <span class="listik-mono">GET /api/routes</span>): кто исполняет задачу и по какому
            процессу. Пока задача заведена — без этапа, держателя и запуска — маршрут можно
            сменить или снять совсем пунктом «без маршрута» (вместе с маршрутом уезжают
            метки <span class="listik-mono">harness:</span>/<span class="listik-mono">process:</span>
            и ошибка автостарта); после начала работы сервер откажет. Сам маршрут ничего не
            запускает: процесс поднимает только галочка «Автостарт» при создании.
          </p>

          <UiAlert v-if="task.launch_error" tone="warning">
            <template #title>Автостарт не выполнен</template>
            {{ task.launch_error }}
          </UiAlert>
        </template>

        <dl v-else class="listik-dl">
          <dt>маршрут</dt>
          <dd class="listik-row" style="flex-wrap: nowrap">
            <RouteIcon
              v-if="launchRoute"
              :route="launchRoute"
              size="sm"
              :title="`маршрут: ${launchRoute.title}`"
            />
            <span class="listik-mono">{{ task.launch_route || '—' }}</span>
          </dd>
          <template v-if="task.launched_by === 'listik'">
            <dt>запуск</dt>
            <dd>
              запущена Listik<template v-if="task.launch_pid != null"> · pid {{ task.launch_pid }}</template>
              <template v-if="task.launched_at"> · {{ formatDateTime(task.launched_at) }}</template>
            </dd>
          </template>
          <template v-if="launchStatus">
            <dt>статус</dt>
            <dd>{{ launchStatus }}</dd>
          </template>
          <template v-if="task.launch_error">
            <dt>ошибка</dt>
            <dd class="listik-row" style="flex-wrap: nowrap; align-items: flex-start">
              <ListikIcon name="warning" size="xs" />
              <span>{{ task.launch_error }}</span>
            </dd>
          </template>
          <template v-if="task.launch_log">
            <dt>лог</dt>
            <dd class="listik-row" style="flex-wrap: nowrap; min-width: 0">
              <span class="listik-mono">{{ task.launch_log }}</span>
              <UiCopyButton :value="task.launch_log" label="Путь к логу запуска">
                <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
              </UiCopyButton>
            </dd>
          </template>
        </dl>
      </section>

      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">Холодный старт</h4>
          <UiBadge :tone="coldTone" size="sm">{{ coldOkCount }} из {{ coldRows.length }}</UiBadge>
          <span class="listik-section__hint">что увидит принимающий по <code class="listik-mono">listik show</code></span>
        </div>
        <div class="listik-cold">
          <div v-for="row in coldRows" :key="row.key" class="listik-cold__row">
            <UiTooltip :text="row.hint ?? ''" :disabled="!row.hint">
              <UiStatusPill :tone="row.tone" size="sm" />
            </UiTooltip>
            <span class="listik-cold__key">{{ row.label }}</span>
            <span class="listik-row" style="flex-wrap: nowrap; min-width: 0">
              <span
                class="listik-cold__value"
                :class="{ 'listik-cold__value--state': row.words }"
                :style="row.color ? { color: row.color } : undefined"
              >{{ row.value }}</span>
              <UiCopyButton :value="row.value" :label="row.label">
                <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
              </UiCopyButton>
            </span>
          </div>
        </div>
      </section>

      <section class="listik-section">
        <h4 class="listik-section__title">Кто держит</h4>
        <dl class="listik-dl">
          <dt>держит</dt>
          <dd>
            <HarnessIcon :actor="task.holder" />
            {{ hasHolderTitle(task.holder_title) ? task.holder_title : 'никто' }}<template v-if="hasHolderTitle(task.holder_title)"> · {{ task.holder_age }}</template>
          </dd>
          <template v-if="task.not_taken">
            <dt>взята</dt>
            <dd>
              <UiBadge tone="warning" size="sm">выдана, не взята {{ task.assigned_age }}</UiBadge>
              <span v-if="task.holder_assigned_by_title"> — выдал {{ task.holder_assigned_by_title }}.</span>
              <span>Claim от агента не приходил: прогон не запустился?</span>
            </dd>
          </template>
          <dt>heartbeat</dt>
          <dd>
            {{ task.holder_at ? formatDateTime(task.holder_at) : '—' }}
            <template v-if="task.holder_at"> · {{ humanAge(task.holder_at) }} назад</template>
          </dd>
          <dt>что делает</dt>
          <dd>{{ task.holder_note ? `«${task.holder_note}»` : '—' }}</dd>
          <dt>этап с</dt>
          <dd>{{ task.stage_at ? formatDateTime(task.stage_at) : '—' }} · {{ task.stage_age }}</dd>
          <template v-if="task.assignee && task.assignee !== task.holder">
            <dt>исполнитель</dt>
            <dd>{{ task.assignee_title || task.assignee }}</dd>
          </template>
        </dl>
      </section>

      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">
            Журнал и вердикты
            <UiBadge tone="neutral" size="sm">{{ feed.length }}</UiBadge>
          </h4>
          <UiSegmented
            :model-value="feedFilter"
            :options="feedFilterOptions"
            size="sm"
            label="Фильтр журнала"
            @update:model-value="setFeedFilter"
          />
        </div>
        <UiTimeline :items="feed" dense :empty-title="feedEmptyState.title" :empty-description="feedEmptyState.description" />
        <div class="listik-row">
          <UiSelect
            v-model="feedKind"
            :options="feedKindOptions"
            size="sm"
            v-bind="{ 'aria-label': 'Тип записи' }"
          />
          <UiInput
            ref="feedInputRef"
            v-model="feedText"
            size="sm"
            placeholder="строка журнала или комментарий"
            v-bind="{ 'aria-label': 'Текст записи', onKeydown: onFeedKeydown }"
          />
          <UiButton size="sm" variant="primary" :loading="pending === 'comment' || pending === 'answer'" :disabled="!feedText.trim()" @click="submitFeed">
            Отправить
          </UiButton>
        </div>
      </section>

      <section class="listik-section">
        <div class="listik-section__head">
          <h4 class="listik-section__title">
            Связи
            <UiBadge v-if="blockedBy.length" tone="warning" size="sm">ждёт {{ blockedBy.length }}</UiBadge>
            <UiBadge v-if="waitingFor.length" tone="accent" size="sm">её ждут {{ waitingFor.length }}</UiBadge>
          </h4>
          <UiButton size="sm" variant="ghost" :loading="depTreeLoading" @click="loadTree">
            <template #icon><ListikIcon name="timeline" size="xs" /></template>
            Дерево связей
          </UiButton>
        </div>

        <UiAlert v-if="blockedBy.length > 0 && blockersIdle" tone="warning">
          <template #title>Блокеры стоят без движения</template>
          Ждать молча бессмысленно: возьмите блокер сами или поставьте ему «нужен автор».
        </UiAlert>

        <div v-if="blockedBy.length || waitingFor.length || childrenOpen.length" class="listik-dep-grid">
          <template v-if="blockedBy.length">
            <h5 class="listik-subtitle listik-dep-grid__head">Ждёт завершения</h5>
            <article v-for="dep in blockedBy" :key="`b-${dep.id}`" class="listik-dep">
              <div class="listik-dep__main">
                <button type="button" class="listik-link listik-mono" @click="emit('open-other', dep.id)">
                  {{ dep.id }}
                </button>
                <span class="listik-dep__title">{{ dep.title }}</span>
              </div>
              <div class="listik-row">
                <UiBadge tone="neutral" size="sm">{{ dep.dep_title || dep.dep_type }}</UiBadge>
                <UiBadge v-if="dep.stage" tone="info" size="sm">{{ stageCode(dep.stage) }}</UiBadge>
                <UiBadge v-if="dep.missing" tone="danger" size="sm">задача не найдена</UiBadge>
              </div>
              <p class="listik-section__hint">{{ depCardHint(dep) }}</p>
            </article>
          </template>

          <template v-if="waitingFor.length">
            <h5 class="listik-subtitle listik-dep-grid__head">Ждут её завершения</h5>
            <article v-for="dep in waitingFor" :key="`w-${dep.id}`" class="listik-dep">
              <div class="listik-dep__main">
                <button type="button" class="listik-link listik-mono" @click="emit('open-other', dep.id)">
                  {{ dep.id }}
                </button>
                <span class="listik-dep__title">{{ dep.title }}</span>
              </div>
              <div class="listik-row">
                <UiBadge tone="neutral" size="sm">{{ dep.dep_title || dep.dep_type }}</UiBadge>
                <UiBadge v-if="dep.stage" tone="info" size="sm">{{ stageCode(dep.stage) }}</UiBadge>
                <UiBadge v-if="dep.missing" tone="danger" size="sm">задача не найдена</UiBadge>
              </div>
              <p class="listik-section__hint">{{ depCardHint(dep) }}</p>
            </article>
          </template>

          <template v-if="childrenOpen.length">
            <h5 class="listik-subtitle listik-dep-grid__head">Незакрытые дети</h5>
            <article v-for="dep in childrenOpen" :key="`c-${dep.id}`" class="listik-dep">
              <div class="listik-dep__main">
                <button type="button" class="listik-link listik-mono" @click="emit('open-other', dep.id)">
                  {{ dep.id }}
                </button>
                <span class="listik-dep__title">{{ dep.title }}</span>
              </div>
              <div class="listik-row">
                <UiBadge tone="neutral" size="sm">{{ dep.dep_title || dep.dep_type }}</UiBadge>
                <UiBadge v-if="dep.stage" tone="info" size="sm">{{ stageCode(dep.stage) }}</UiBadge>
              </div>
              <p class="listik-section__hint">{{ depCardHint(dep) }}</p>
            </article>
          </template>
        </div>

        <p v-if="parentDep" class="listik-section__hint">
          родитель:
          <button type="button" class="listik-link listik-mono" @click="emit('open-other', parentDep.id)">
            {{ parentDep.id }}
          </button>
          {{ parentDep.title }}
        </p>

        <p v-if="softLinks.length" class="listik-section__hint">
          связано, не блокирует:
          <template v-for="(dep, index) in softLinks" :key="`s-${dep.id}`">
            <button type="button" class="listik-link listik-mono" @click="emit('open-other', dep.id)">
              {{ dep.id }}
            </button>
            <span v-if="dep.dep_title"> ({{ dep.dep_title }})</span><span v-if="index < softLinks.length - 1">, </span>
          </template>
        </p>

        <UiAlert v-if="!deps" tone="info">
          Сервер не отдал вердикт по зависимостям — возможно, старая версия API.
          Показаны только связи из карточки задачи.
        </UiAlert>

        <!-- Сводка связей: id через запятую, каждый — ссылка, открывающая задачу
             в этой же панели. Строится по спискам карточки, поэтому показывает и
             мягкие входящие связи, которых нет ни в blocked_by, ни в waiting_for;
             id, уже показанные карточками выше, в сводку не попадают. -->
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

  <!-- Обход запрета блокеров — отдельным подтверждением и danger-тоном:
       это осознанное действие, и оно останется в истории задачи. -->
  <UiConfirmDialog
    v-model="forceDialogOpen"
    tone="danger"
    title="Взять заблокированную задачу?"
    :description="`Задача ${task?.id ?? ''} ждёт: ${blockedBy.map((dep) => dep.id).join(', ') || '—'}. В историю уйдёт запись «ЗАПУСК БЕЗ РАЗРЕШЕНИЯ БЛОКЕРОВ».`"
    confirm-label="Взять всё равно"
    cancel-label="Отмена"
    :loading="pending === 'claim'"
    @confirm="submitClaim(true)"
  />

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

<!-- Стили панели (шапка, промежуток тела, .listik-cold*, .listik-dep-grid) —
     в assets/app.css, раздел «Панель задачи»: заголовок телепортируется в
     body вместе с data-v-атрибутом, а .listik-events переиспользуется и вне
     этого компонента (общие «мелочи страницы»). -->

