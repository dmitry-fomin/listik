<script setup lang="ts">
/**
 * Компактная карточка задачи на доске (лист состояний `CardStates.dc.html`).
 * Своя разметка — в ките карточки канбана нет. Никакого drag&drop: карточка
 * целиком кликабельна и открывает панель задачи, этап меняется только там.
 */
import { computed } from 'vue'
import { UiBadge } from '@zoloto585/facet'
import TaskGlyph from '../marks/TaskGlyph.vue'
import ProjectMark from '../marks/ProjectMark.vue'
import CountGlyph from '../marks/CountGlyph.vue'
import HealthDot from '../marks/HealthDot.vue'
import HarnessIcon from '../marks/HarnessIcon.vue'
import RouteIcon from '../marks/RouteIcon.vue'
import ListikIcon from '../ListikIcon.vue'
import type { ProjectRow, Task } from '@/api/types'
import store, { type DepsSummary } from '@/store/listik'
import { AT_RISK_IDLE_HOURS, taskHealth, healthReason, healthTone } from '@/lib/health'
import { statusTitle } from '@/lib/dictionaries'
import { routeByKey } from '@/lib/routes'

const props = defineProps<{
  task: Task
  deps?: DepsSummary | null
  project?: ProjectRow | null
}>()

const emit = defineEmits<{
  open: [id: string]
}>()

/**
 * Ветка состояния — строго одна, проверки по порядку (как в `taskHealth` и в
 * инбоксе): `needs_owner` → `dead` → `at-risk` → ничего. Первая сработавшая
 * задаёт и модификатор полосы, и единственный бейдж состояния. Задача,
 * одновременно needs_owner и dead, получает только акцентную полосу и «нужен
 * ты»; о брошенности говорит точка здоровья в подвале (считается отдельно).
 */
const health = computed(() => taskHealth(props.task))

const branch = computed<'needs' | 'dead' | 'at-risk' | null>(() => {
  if (props.task.needs_owner) return 'needs'
  if (health.value === 'dead') return 'dead'
  if (health.value === 'at-risk') return 'at-risk'
  return null
})

const stateClass = computed(() => ({
  'is-needs': branch.value === 'needs',
  'is-dead': branch.value === 'dead',
  'is-at-risk': branch.value === 'at-risk',
  'is-muted': health.value === 'unknown' && blockedCount.value > 0,
}))

/**
 * Идёт молча дольше порога — единственная причина at-risk, которая получает
 * бейдж; at-risk только по `stage_warn` бейджа не показывает (о нём говорит
 * цвет возраста в подвале).
 */
const isIdleAtRisk = computed(
  () => props.task.idle_hours !== null && props.task.idle_hours >= AT_RISK_IDLE_HOURS,
)

const stateBadge = computed<{ tone: 'accent' | 'danger' | 'warning' | 'success'; text: string } | null>(() => {
  if (branch.value === 'needs') return { tone: 'accent', text: 'нужен ты' }
  if (branch.value === 'dead') return { tone: 'danger', text: healthReason(props.task) }
  // «Выдана, но не взята» дольше порога — свой бейдж: видно, что прогон не
  // запустился, а не что исполнитель молчит в работе.
  if (branch.value === 'at-risk' && props.task.not_taken_warn) {
    return { tone: 'warning', text: `не взята ${props.task.assigned_age}` }
  }
  // Молчание — шкала, а не одно состояние: тон растёт ступенями вместе с возрастом
  // (см. healthTone), поэтому «молчит 15 мин» не кричит так же, как «молчит час».
  if (branch.value === 'at-risk' && isIdleAtRisk.value) {
    const tone = healthTone(props.task)
    return {
      tone: tone === 'danger' ? 'danger' : tone === 'warning' ? 'warning' : 'success',
      text: healthReason(props.task),
    }
  }
  return null
})

/** Бейдж статуса — независимо от ветки состояния (закрыта / отменена / после s4). */
const statusBadge = computed<{ tone: 'success' | 'neutral' | 'info'; text: string } | null>(() => {
  if (props.task.status === 'done') return { tone: 'success', text: 'закрыта' }
  if (props.task.status === 'cancelled') return { tone: 'neutral', text: statusTitle('cancelled') }
  if (props.task.stage === 'done') return { tone: 'info', text: 'после s4' }
  return null
})

const blockedBy = computed(() => props.deps?.blockedBy ?? [])
const blockedCount = computed(() => blockedBy.value.length || props.task.blocked_by.length)
const waitingForCount = computed(() => props.deps?.waitingForCount ?? 0)

/**
 * Маршрут разработки задачи: `launch_route` — ключ записи `routes.json`, которую
 * список отдаёт `GET /api/routes` (грузятся при старте доски). Задача заведена
 * без маршрута или запись из файла убрали — иконки нет.
 */
const taskRoute = computed(() => routeByKey(props.task.launch_route, store.routes.value))

const blockedTooltip = computed(() => {
  if (blockedBy.value.length > 0) {
    const names = blockedBy.value.map((dep) => dep.id).join(', ')
    return `ждёт завершения: ${names}`
  }
  if (props.task.blocked_by.length > 0) return `ждёт завершения: ${props.task.blocked_by.join(', ')}`
  return ''
})

const stageAgeClass = computed(() => {
  if (!props.task.stage_warn) return null
  return (props.task.stage_hours ?? 0) > 24 ? 'is-late' : 'is-warn'
})

function onKeydown(event: KeyboardEvent): void {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    emit('open', props.task.id)
  }
}
</script>

<template>
  <article
    class="listik-task-card"
    :class="stateClass"
    role="button"
    tabindex="0"
    :aria-label="`Открыть задачу ${task.title}`"
    @click="emit('open', task.id)"
    @keydown="onKeydown"
  >
    <div class="listik-task-card__top">
      <TaskGlyph kind="type" :value="task.issue_type" />
      <RouteIcon
        v-if="taskRoute"
        :route="taskRoute"
        size="xs"
        :title="`маршрут: ${taskRoute.title}`"
      />
      <ProjectMark :project="project ?? null" :slug="task.project" with-title size="sm" />
      <span class="listik-task-card__spacer" />
      <TaskGlyph kind="priority" :value="task.priority" />
    </div>

    <h3 class="listik-task-card__title">{{ task.title }}</h3>

    <div v-if="stateBadge || statusBadge || blockedCount > 0 || waitingForCount > 0" class="listik-task-card__meta">
      <div class="listik-task-card__badges">
        <UiBadge v-if="stateBadge" :tone="stateBadge.tone" size="sm">{{ stateBadge.text }}</UiBadge>
        <UiBadge v-if="statusBadge" :tone="statusBadge.tone" size="sm">{{ statusBadge.text }}</UiBadge>
      </div>
      <div class="listik-task-card__counts">
        <CountGlyph icon="lock" tone="warning" :count="blockedCount" :title="blockedTooltip" />
        <CountGlyph
          icon="key"
          tone="accent"
          :count="waitingForCount"
          :title="`её ждут ${waitingForCount}: пока не закрыта, эти задачи стоят`"
        />
      </div>
    </div>

    <div class="listik-task-card__foot">
      <span class="listik-task-card__holder">
        <HealthDot :health="health" size="sm" :label="healthReason(task)" />
        <template v-if="task.holder">
          <HarnessIcon :actor="task.holder" size="xs" />
          <strong>{{ task.holder_title }}</strong>
          <span v-if="task.not_taken" class="listik-task-card__assigned">
            выдана, не взята {{ task.assigned_age }}
          </span>
        </template>
        <template v-else>без держателя</template>
      </span>
      <span class="listik-task-card__age tnum" :class="stageAgeClass">
        <ListikIcon name="clock" size="xs" />
        {{ task.stage_age }}
      </span>
    </div>
  </article>
</template>
