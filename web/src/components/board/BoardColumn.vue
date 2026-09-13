<script setup lang="ts">
/**
 * Колонка доски: заголовок + список карточек. Своя разметка — колонки канбана
 * в ките нет. Без drag&drop: этап меняется только из панели задачи (`stage`),
 * доска только показывает.
 */
import { computed, ref, watch } from 'vue'
import { UiBadge, UiButton, UiEmptyState, UiSkeleton } from '@zoloto585/facet'
import TaskCard from './TaskCard.vue'
import HarnessIcon from '../marks/HarnessIcon.vue'
import HealthDot from '../marks/HealthDot.vue'
import ListikIcon from '../ListikIcon.vue'
import type { BoardColumn, ProjectRow, Task, TaskStage } from '@/api/types'
import type { DepsSummary } from '@/store/listik'
import { taskHealth, healthReason } from '@/lib/health'
import { harnessOf, HARNESS_TITLES, type HarnessKey } from '@/lib/harness'
import { stageCode, stageTitle } from '@/lib/stages'

/** Сколько карточек колонка показывает сразу: 300 карточек в DOM — это лишние тысячи узлов. */
const PAGE = 60
/** Больше этого числа точек здоровья в шапке не показываем — дальше «+N». */
const MAX_HEALTH_DOTS = 12

const props = defineProps<{
  column: BoardColumn
  loading?: boolean
  /** Колонка свёрнута в узкую рельсу (любая колонка, переключается кликом по заголовку). */
  collapsed?: boolean
  depsSummary?: Record<string, DepsSummary>
  projects?: ProjectRow[]
}>()

const emit = defineEmits<{
  open: [id: string]
  /** Клик по заголовку или по рельсе — свернуть/развернуть колонку. */
  toggle: []
}>()

const shown = ref(PAGE)
const visibleTasks = computed(() => props.column.tasks.slice(0, shown.value))

watch(
  () => props.column.tasks.length,
  () => {
    shown.value = PAGE
  },
)

function showMore(): void {
  shown.value += PAGE
}

const columnStage = computed<TaskStage>(() => props.column.key as TaskStage)
const isStageColumn = computed(() => stageCode(columnStage.value) !== null)

const headTitle = computed(() => {
  if (props.column.key === 'none') return 'Заведена'
  if (isStageColumn.value || props.column.key === 'done') return stageTitle(columnStage.value) ?? props.column.title
  return props.column.title
})

function onHeadKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar') {
    event.preventDefault()
    emit('toggle')
  }
}

/** Уникальные харнессы держателей задач колонки, без `human` и без пустых. */
const harnesses = computed(() => {
  if (props.column.key === 'none') return []
  const seen = new Set<string>()
  const list: { key: HarnessKey; title: string }[] = []
  for (const task of props.column.tasks) {
    const harness = harnessOf(task.holder)
    if (!harness || harness === 'human' || seen.has(harness)) continue
    seen.add(harness)
    list.push({ key: harness, title: HARNESS_TITLES[harness] })
  }
  return list
})

const healthDots = computed(() =>
  props.column.tasks.slice(0, MAX_HEALTH_DOTS).map((task) => ({ id: task.id, health: taskHealth(task), title: healthReason(task) })),
)

const healthOverflow = computed(() => Math.max(0, props.column.tasks.length - MAX_HEALTH_DOTS))

function projectOf(task: Task): ProjectRow | null {
  return (props.projects ?? []).find((project) => project.slug === task.project) ?? null
}
</script>

<template>
  <button
    v-if="collapsed"
    type="button"
    class="listik-column__rail"
    :data-stage="column.key"
    aria-expanded="false"
    :aria-label="`Развернуть колонку ${headTitle}`"
    @click="emit('toggle')"
  >
    <span class="listik-column__rail-title">
      <span v-if="isStageColumn" class="listik-column__code">{{ stageCode(columnStage) }}</span>
      {{ headTitle }}
    </span>
    <UiBadge tone="neutral" size="sm">{{ column.count }}</UiBadge>
  </button>

  <section v-else class="listik-column" :data-stage="column.key" :aria-label="`Колонка ${headTitle}`">
    <header
      class="listik-column__head listik-column__head--toggleable"
      role="button"
      tabindex="0"
      aria-expanded="true"
      :aria-label="`Свернуть колонку ${headTitle}`"
      @click="emit('toggle')"
      @keydown="onHeadKeydown"
    >
      <div class="listik-column__title-row">
        <h2 class="listik-column__title">
          <span v-if="isStageColumn" class="listik-column__code">{{ stageCode(columnStage) }}</span>
          {{ headTitle }}
        </h2>
        <span class="listik-column__count tnum">{{ column.count }}</span>
      </div>
      <div class="listik-column__meta">
        <p class="listik-column__harness">
          <template v-if="column.key === 'none'">без этапа</template>
          <template v-else>
            <template v-for="(harness, index) in harnesses" :key="harness.key">
              <span v-if="index > 0" aria-hidden="true"> · </span>
              <HarnessIcon :harness="harness.key" size="xs" />
              {{ harness.title }}
            </template>
          </template>
        </p>
        <p v-if="healthDots.length > 0" class="listik-health-row">
          <HealthDot v-for="dot in healthDots" :key="dot.id" :health="dot.health" size="sm" :label="dot.title" />
          <span v-if="healthOverflow > 0" class="tnum">+{{ healthOverflow }}</span>
        </p>
      </div>
    </header>

    <div class="listik-column__body">
      <template v-if="loading && column.tasks.length === 0">
        <UiSkeleton v-for="n in 3" :key="`sk-${n}`" variant="rect" height="120px" />
      </template>

      <UiEmptyState v-if="!loading && column.tasks.length === 0" compact title="Здесь пусто">
        <template #icon><ListikIcon name="columns" size="lg" /></template>
      </UiEmptyState>

      <TaskCard
        v-for="task in visibleTasks"
        :key="task.id"
        :task="task"
        :deps="depsSummary?.[task.id] ?? null"
        :project="projectOf(task)"
        @open="emit('open', $event)"
      />

      <UiButton v-if="column.tasks.length > visibleTasks.length" size="sm" variant="ghost" block @click="showMore">
        Показать ещё {{ Math.min(PAGE, column.tasks.length - visibleTasks.length) }}
        (осталось {{ column.tasks.length - visibleTasks.length }})
      </UiButton>
    </div>
  </section>
</template>
