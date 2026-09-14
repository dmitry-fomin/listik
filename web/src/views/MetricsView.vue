<script setup lang="ts">
/**
 * Вид «Метрики» (макет «Метрики Listik»): сверху «требует внимания» и темп
 * закрытия (UiKpiCard со спарклайном по closed_by_day), под ними поток
 * конвейера «Заведена → s1…s4 → Готово», открытая работа одной полосой по
 * статусам (готовые/отменённые — отдельной строкой архива, чтобы 784 не
 * задавливали остальное), зависимости, что сейчас в работе (UiDataTable),
 * проекты и держатели (UiProportionalBarList), активность из /api/timeline.
 */
import { computed } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiCard,
  UiDataTable,
  UiEmptyState,
  UiHeatmap,
  UiKpiCard,
  UiProportionalBarList,
  UiStatusPill,
  UiTableActionButton,
  type UiDataTableColumn,
  type UiHeatmapCell,
  type UiKpiCardDelta,
  type UiProportionalBarListItem,
} from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import store from '@/store/listik'
import type { Task } from '@/api/types'
import { INTAKE_COLUMN_KEY, PIPELINE_STAGES, priority, statusTitle } from '@/lib/dictionaries'
import { formatHours, formatTime } from '@/lib/format'
import { stageCode as stageCodeFor, stageTitle } from '@/lib/stages'

const stats = computed(() => store.stats.value)
const running = computed(() => stats.value?.running ?? [])

// ── Требует внимания ────────────────────────────────────────────────────────

const attention = computed(() => [
  { key: 'needs_owner', icon: 'user', label: 'Нужен автор', hint: 'ждут вашего решения', value: stats.value?.needs_owner ?? 0 },
  { key: 'stale', icon: 'clock', label: 'Молчат > суток', hint: 'без событий 24 ч', value: stats.value?.stale ?? 0 },
  { key: 'long_stage', icon: 'clock', label: 'Долго на этапе', hint: 'дольше 8 ч на этапе', value: stats.value?.long_stage ?? 0 },
  { key: 'cycles', icon: 'refresh', label: 'Циклы блокеров', hint: 'задачи ждут друг друга', value: store.cycles.value.length },
])

// ── Темп закрытия ───────────────────────────────────────────────────────────

const closedDelta = computed<UiKpiCardDelta>(() => {
  const now = stats.value?.closed_7d ?? 0
  const prev = stats.value?.closed_prev_7d ?? 0
  if (now === prev) return { value: '0%', direction: 'flat' }
  if (prev === 0) return { value: `+${now}`, direction: 'up' }
  const pct = Math.round(((now - prev) / prev) * 100)
  return { value: pct > 0 ? `+${pct}%` : `−${Math.abs(pct)}%`, direction: pct > 0 ? 'up' : 'down' }
})

const closedSpark = computed(() => (stats.value?.closed_by_day ?? []).map((day) => day.count))

const closedHint = computed(
  () => `неделей раньше — ${stats.value?.closed_prev_7d ?? 0} · всего готово ${stats.value?.by_status?.done ?? 0}`,
)

// ── Конвейер ────────────────────────────────────────────────────────────────

const flow = computed(() => {
  const byStage = stats.value?.by_stage ?? {}
  const wipByStage = new Map<string, number>()
  for (const task of running.value) {
    const key = task.stage ?? INTAKE_COLUMN_KEY
    wipByStage.set(key, (wipByStage.get(key) ?? 0) + 1)
  }
  const open = [
    { key: INTAKE_COLUMN_KEY, code: '—', label: 'Заведена' },
    ...PIPELINE_STAGES.map((step) => ({ key: step.value as string, code: step.code as string, label: step.label })),
  ]
  const max = Math.max(1, ...open.map((step) => byStage[step.key] ?? 0))
  const steps = open.map((step) => {
    const count = byStage[step.key] ?? 0
    const wip = Math.min(count, wipByStage.get(step.key) ?? 0)
    return {
      ...step,
      count,
      done: false,
      wipWidth: `${(wip / max) * 100}%`,
      waitWidth: `${((count - wip) / max) * 100}%`,
      note: wip ? `в работе ${wip}` : count ? 'ждут' : 'пусто',
    }
  })
  const closed = stats.value?.closed_7d ?? 0
  steps.push({
    key: 'done', code: '✓', label: 'Готово', count: closed, done: true,
    wipWidth: closed ? '100%' : '0%', waitWidth: '0%', note: 'за 7 дней',
  })
  return steps
})

// ── Открытая работа ─────────────────────────────────────────────────────────

const OPEN_STATUSES = [
  { key: 'open', color: 'var(--accent-200)' },
  { key: 'in_progress', color: 'var(--accent-500)' },
  { key: 'review', color: 'var(--chart-3)' },
  { key: 'blocked', color: 'var(--danger-500)' },
] as const

const openWork = computed(() =>
  OPEN_STATUSES.map((status) => ({
    ...status,
    label: statusTitle(status.key),
    value: stats.value?.by_status?.[status.key] ?? 0,
  })),
)

const openTotal = computed(() => openWork.value.reduce((sum, status) => sum + status.value, 0))

// ── Сейчас в работе ─────────────────────────────────────────────────────────

const runningColumns: UiDataTableColumn[] = [
  { key: 'id', label: 'Задача', width: '180px' },
  { key: 'title', label: '' },
  { key: 'stage', label: 'Этап', width: '170px' },
  { key: 'holder', label: 'Держатель', width: '160px' },
  { key: 'age', label: 'На этапе', width: '110px', align: 'end' },
  { key: 'open', label: '', width: '96px', align: 'end' },
]

// ── Проекты и держатели ─────────────────────────────────────────────────────

const projectItems = computed<UiProportionalBarListItem[]>(() =>
  (stats.value?.by_project ?? []).map((project) => ({
    id: project.project,
    label: project.project,
    value: project.total,
    tone: project.waiting ? 'warning' : 'accent',
  })),
)

/** «—» и пустой держатель — одно и то же «никто»: складываем в одну строку. */
const holderItems = computed<UiProportionalBarListItem[]>(() => {
  let nobody = 0
  const rows: UiProportionalBarListItem[] = []
  for (const holder of stats.value?.by_holder ?? []) {
    if (!holder.holder || holder.holder === '—') nobody += holder.count
    else rows.push({ id: holder.holder, label: holder.title || holder.holder, value: holder.count, tone: 'accent' })
  }
  if (nobody) rows.push({ id: '—', label: 'никто', value: nobody, tone: 'neutral' })
  return rows
})

// ── Активность ──────────────────────────────────────────────────────────────

const heatmapCells = computed<UiHeatmapCell[]>(() => {
  const counts = new Map<string, number>()
  for (const item of store.timeline.value) {
    const day = item.ts.slice(0, 10)
    counts.set(day, (counts.get(day) ?? 0) + 1)
  }
  const cells: UiHeatmapCell[] = []
  const today = new Date()
  for (let offset = 182; offset >= 0; offset -= 1) {
    const iso = new Date(today.getTime() - offset * 86400000).toISOString().slice(0, 10)
    cells.push({ date: iso, value: counts.get(iso) ?? 0 })
  }
  return cells
})

const updatedAt = computed(() =>
  stats.value
    ? formatTime(stats.value.generated_at, { hour: '2-digit', minute: '2-digit' })
    : '—',
)

function openTask(id: string): void {
  void store.openTask(id)
}
</script>

<template>
  <section class="listik-section listik-metrics" aria-label="Метрики">
    <div class="listik-section__head">
      <h2 class="listik-section__title">Метрики</h2>
      <span class="listik-section__hint">обновлено в {{ updatedAt }}</span>
    </div>

    <div class="listik-metrics__row">
      <UiCard class="listik-metrics__span-7">
        <template #header>
          <div class="listik-metrics__card-head">
            <h3 class="listik-metrics__title">Требует внимания</h3>
            <span class="listik-section__hint">ноль — хорошо</span>
          </div>
        </template>
        <div class="listik-attn">
          <div
            v-for="item in attention"
            :key="item.key"
            class="listik-attn__item"
            :class="{ 'listik-attn__item--hot': item.value > 0 }"
          >
            <span class="listik-attn__label">
              <ListikIcon :name="item.icon" size="sm" />
              {{ item.label }}
            </span>
            <span class="listik-attn__value">{{ item.value }}</span>
            <span class="listik-attn__hint">{{ item.hint }}</span>
          </div>
        </div>
        <UiAlert v-if="store.cycles.value.length > 0" tone="danger">
          <template #title>Задачи ждут друг друга по кругу</template>
          <span class="listik-mono">{{ store.cycles.value.map((cycle) => cycle.join(' → ')).join('; ') }}</span>
        </UiAlert>
      </UiCard>

      <UiKpiCard
        class="listik-metrics__span-5"
        label="Закрыто за 7 дней"
        :value="stats?.closed_7d ?? 0"
        :hint="closedHint"
        :delta="closedDelta"
        :sparkline-data="closedSpark"
        accent
      >
        <template #icon><ListikIcon name="check" size="sm" /></template>
      </UiKpiCard>
    </div>

    <UiCard>
      <template #header>
        <div class="listik-metrics__card-head">
          <h3 class="listik-metrics__title">Конвейер</h3>
          <div class="listik-legend-inline">
            <span class="listik-legend__label">
              <span class="listik-swatch" style="background: var(--accent-500)"></span>в работе
            </span>
            <span class="listik-legend__label">
              <span class="listik-swatch" style="background: var(--accent-200)"></span>ждут
            </span>
          </div>
        </div>
      </template>
      <div class="listik-flow">
        <div
          v-for="step in flow"
          :key="step.key"
          class="listik-flow__step"
          :class="{ 'listik-flow__step--busy': step.count > 0 && !step.done, 'listik-flow__step--done': step.done }"
        >
          <span class="listik-flow__head">
            <span class="listik-flow__code">{{ step.code }}</span>
            <span class="listik-flow__label">{{ step.label }}</span>
          </span>
          <span class="listik-flow__count" :class="{ 'listik-flow__count--zero': step.count === 0 }">
            {{ step.count }}
          </span>
          <span class="listik-flow__track">
            <span class="listik-flow__fill listik-flow__fill--wip" :style="{ width: step.wipWidth }"></span>
            <span class="listik-flow__fill listik-flow__fill--wait" :style="{ width: step.waitWidth }"></span>
          </span>
          <span class="listik-flow__note">{{ step.note }}</span>
        </div>
      </div>
    </UiCard>

    <div class="listik-metrics__row">
      <UiCard class="listik-metrics__span-7">
        <template #header>
          <div class="listik-metrics__card-head">
            <h3 class="listik-metrics__title">Открытая работа по статусу</h3>
            <span class="listik-section__hint tnum">{{ openTotal }} задач</span>
          </div>
        </template>
        <div class="listik-stack listik-metrics__body">
          <div class="listik-split" role="img" :aria-label="`Открытых задач: ${openTotal}`">
            <span
              v-for="status in openWork.filter((s) => s.value > 0)"
              :key="status.key"
              class="listik-split__part"
              :style="{ flexGrow: status.value, background: status.color }"
            ></span>
          </div>
          <div class="listik-legend">
            <div v-for="status in openWork" :key="status.key" class="listik-legend__item">
              <span class="listik-legend__label">
                <span class="listik-swatch" :style="{ background: status.color }"></span>{{ status.label }}
              </span>
              <span class="listik-legend__value" :class="{ 'listik-legend__value--zero': status.value === 0 }">
                {{ status.value }}
              </span>
            </div>
          </div>
          <div class="listik-metrics__foot">
            <span>Архив: готово <b class="tnum">{{ stats?.by_status?.done ?? 0 }}</b></span>
            <span>отменено <b class="tnum">{{ stats?.by_status?.cancelled ?? 0 }}</b></span>
          </div>
        </div>
      </UiCard>

      <UiCard class="listik-metrics__span-5">
        <template #header><h3 class="listik-metrics__title">Зависимости</h3></template>
        <div class="listik-deps">
          <div class="listik-deps__row">
            <span class="listik-deps__term">
              <span class="listik-deps__name">Можно брать</span>
              <span class="listik-deps__hint">нет блокеров и держателя</span>
            </span>
            <span class="listik-deps__value listik-deps__value--good">{{ store.readyCount.value }}</span>
          </div>
          <div class="listik-deps__row">
            <span class="listik-deps__term">
              <span class="listik-deps__name">Стоят из-за других</span>
              <span class="listik-deps__hint">есть незакрытый жёсткий блокер</span>
            </span>
            <span
              class="listik-deps__value"
              :class="store.blockedCount.value ? 'listik-deps__value--warn' : 'listik-deps__value--zero'"
            >{{ store.blockedCount.value }}</span>
          </div>
          <div class="listik-deps__row">
            <span class="listik-deps__term">
              <span class="listik-deps__name">В выборке</span>
              <span class="listik-deps__hint">на доске с учётом фильтра</span>
            </span>
            <span class="listik-deps__value">{{ store.board.value?.total ?? 0 }}</span>
          </div>
        </div>
      </UiCard>
    </div>

    <div class="listik-metrics__row">
      <UiCard class="listik-metrics__span-8">
        <template #header>
          <div class="listik-metrics__card-head">
            <h3 class="listik-metrics__title">
              Сейчас в работе
              <UiBadge :tone="running.length ? 'info' : 'neutral'" size="sm">{{ running.length }}</UiBadge>
            </h3>
            <UiButton size="sm" variant="ghost" @click="store.importEmbeddings()">
              <template #icon><ListikIcon name="bolt" size="xs" /></template>
              Досчитать векторы
            </UiButton>
          </div>
        </template>
        <UiEmptyState
          v-if="running.length === 0"
          compact
          title="Ничего не выполняется"
          description="Ни одна задача не в статусе «в работе»."
        >
          <template #icon><ListikIcon name="clock" size="lg" /></template>
        </UiEmptyState>
        <UiDataTable
          v-else
          :columns="runningColumns"
          :rows="running"
          :row-key="(task: Task) => task.id"
          density="compact"
          frameless
        >
          <template #cell-id="{ row }">
            <span class="listik-mono listik-metrics__muted">{{ (row as Task).id }}</span>
          </template>
          <template #cell-title="{ row }">
            <span class="listik-metrics__task">
              <span class="listik-metrics__prio" :style="{ color: priority((row as Task).priority).color }">
                P{{ (row as Task).priority }}
              </span>
              <span class="listik-metrics__ellipsis">{{ (row as Task).title }}</span>
            </span>
          </template>
          <template #cell-stage="{ row }">
            <span class="listik-metrics__stage">
              <span class="listik-flow__code">{{ stageCodeFor((row as Task).stage) ?? '—' }}</span>{{ stageTitle((row as Task).stage === 'done' ? null : (row as Task).stage) ?? ((row as Task).stage_title || 'без этапа') }}
            </span>
          </template>
          <template #cell-holder="{ row }">
            <span v-if="(row as Task).holder_title && (row as Task).holder_title !== '—'">
              {{ (row as Task).holder_title }}
            </span>
            <UiStatusPill v-else tone="dead" size="sm">держателя нет</UiStatusPill>
          </template>
          <template #cell-age="{ row }">
            <UiStatusPill :tone="(row as Task).stage_warn ? 'at-risk' : 'healthy'" size="sm">
              <span class="tnum">{{ formatHours((row as Task).stage_hours) }}</span>
            </UiStatusPill>
          </template>
          <template #cell-open="{ row }">
            <UiTableActionButton label="Открыть" @click="openTask((row as Task).id)">Открыть</UiTableActionButton>
          </template>
        </UiDataTable>
      </UiCard>

      <UiCard class="listik-metrics__span-4">
        <template #header><h3 class="listik-metrics__title">По проектам</h3></template>
        <UiProportionalBarList :items="projectItems" label-width="120px" empty-title="Нет открытых задач" />
        <h3 class="listik-metrics__title listik-metrics__subtitle">Кто держит</h3>
        <UiProportionalBarList :items="holderItems" label-width="120px" empty-title="Никто ничего не держит" />
      </UiCard>
    </div>

    <UiCard>
      <template #header><h3 class="listik-metrics__title">Активность за полгода</h3></template>
      <UiHeatmap
        :cells="heatmapCells"
        label="Активность задач по дням"
        empty-title="Событий пока нет"
        :value-formatter="(value: number) => `${value} событий`"
      />
    </UiCard>
  </section>
</template>
