<script setup lang="ts">
/**
 * Карточка задачи на телефоне (шаг 06, порция c) — чистый просмотр, без
 * действий: держатель/heartbeat, критерии приёмки, блокеры, последний
 * review/verdict, журнал (последние 20 комментариев, без событий). Никаких
 * emit кроме `reload` при ошибке — ни claim/comment/needs-owner/heartbeat/
 * stage/release/done, ни полей ввода. Планшет и десктоп продолжают открывать
 * `TaskDrawer`; этот компонент монтируется вместо него только на телефоне
 * (`App.vue`, `v-else`).
 */
import { computed } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiDrawer,
  UiSkeleton,
  UiStatusPill,
  UiTimeline,
  type UiTimelineItem,
} from '@zoloto585/facet'
import ProjectMark from './marks/ProjectMark.vue'
import TaskGlyph from './marks/TaskGlyph.vue'
import type { DepInfo, ProjectRow, TaskComment, TaskDetail } from '@/api/types'
import { commentKindTitle, datetimeAttr, formatDateTime, humanAge, taskStageLabel } from '@/lib/format'
import { HEALTH_TITLES, taskHealth } from '@/lib/health'
import { stageCode } from '@/lib/stages'

const props = defineProps<{
  modelValue: boolean
  task: TaskDetail | null
  loading?: boolean
  error?: string | null
  projects?: ProjectRow[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  reload: []
}>()

function projectOf(task: TaskDetail): ProjectRow | null {
  return props.projects?.find((project) => project.slug === task.project) ?? null
}

const blockedBy = computed<DepInfo[]>(() => props.task?.deps_state?.blocked_by ?? [])

/** Комментарии, отсортированные по `created_at` по убыванию; при равенстве —
 *  позже в исходном массиве выше (индекс совпадает с относительным порядком,
 *  filter/map его не меняют). */
function sortedDesc(comments: TaskComment[]): TaskComment[] {
  return comments
    .map((comment, index) => ({ comment, index }))
    .sort((a, b) => {
      const diff = Date.parse(b.comment.created_at) - Date.parse(a.comment.created_at)
      if (diff !== 0) return diff
      return b.index - a.index
    })
    .map((entry) => entry.comment)
}

const lastReview = computed<TaskComment | null>(() => {
  const task = props.task
  if (!task) return null
  const candidates = task.comments.filter((comment) => comment.kind === 'review' || comment.kind === 'verdict')
  if (!candidates.length) return null
  return sortedDesc(candidates)[0]
})

const journalItems = computed<UiTimelineItem[]>(() => {
  const task = props.task
  if (!task) return []
  return sortedDesc(task.comments)
    .slice(0, 20)
    .map((comment) => ({
      id: comment.id,
      title: `${commentKindTitle(comment.kind)} · ${comment.author || '—'}`,
      description: comment.text,
      timestamp: humanAge(comment.created_at),
      datetime: datetimeAttr(comment.created_at),
    }))
})

function depHolderHint(dep: DepInfo): string {
  return dep.holder ? `держит ${dep.holder_title}` : 'без держателя'
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
      <div v-if="task" class="listik-drawer__head listik-phone-sheet__head">
        <div class="listik-row listik-drawer__meta-row">
          <TaskGlyph kind="type" :value="task.issue_type" />
          <ProjectMark :project="projectOf(task)" :slug="task.project" with-title size="sm" />
        </div>
        <h2 class="listik-drawer__title">{{ task.title }}</h2>
        <div class="listik-row listik-drawer__pills">
          <UiStatusPill :tone="taskHealth(task)" size="md">{{ HEALTH_TITLES[taskHealth(task)] }}</UiStatusPill>
          <UiBadge tone="info" size="sm">{{ taskStageLabel(task) }}</UiBadge>
          <UiBadge v-if="task.needs_owner" tone="accent" size="sm">нужен ты</UiBadge>
          <span class="listik-mono">{{ task.id }}</span>
        </div>
      </div>
      <div v-else class="listik-drawer__head listik-phone-sheet__head">
        <span class="listik-mono">Задача</span>
      </div>
    </template>

    <div class="listik-phone-sheet">
      <div v-if="loading && !task" class="listik-stack">
        <UiSkeleton variant="text" width="40%" />
        <UiSkeleton variant="rect" height="80px" />
        <UiSkeleton variant="rect" height="160px" />
      </div>

      <UiAlert v-else-if="error" tone="danger">
        <template #title>Не удалось загрузить задачу</template>
        {{ error }}
        <div class="listik-row" style="margin-top: var(--space-3)">
          <UiButton size="sm" variant="secondary" @click="emit('reload')">Повторить</UiButton>
        </div>
      </UiAlert>

      <div v-else-if="task" class="listik-stack listik-drawer__body">
        <section class="listik-section">
          <h4 class="listik-section__title">Кто держит</h4>
          <dl class="listik-dl">
            <dt>держит</dt>
            <dd>{{ task.holder ? `${task.holder_title} · ${task.holder_age}` : 'никто' }}</dd>
            <dt>heartbeat</dt>
            <dd>{{ task.holder_at ? `${formatDateTime(task.holder_at)} · ${humanAge(task.holder_at)} назад` : '—' }}</dd>
            <dt>что делает</dt>
            <dd>{{ task.holder_note ? `«${task.holder_note}»` : '—' }}</dd>
            <dt>этап с</dt>
            <dd>{{ task.stage_at ? `${formatDateTime(task.stage_at)} · ${task.stage_age}` : '—' }}</dd>
          </dl>
        </section>

        <section class="listik-section">
          <h4 class="listik-section__title">Критерии приёмки</h4>
          <p class="listik-prose">{{ task.acceptance || '—' }}</p>
        </section>

        <section class="listik-section">
          <div class="listik-section__head">
            <h4 class="listik-section__title">
              Блокеры
              <UiBadge v-if="blockedBy.length" tone="warning" size="sm">{{ blockedBy.length }}</UiBadge>
            </h4>
          </div>
          <p v-if="!task.deps_state" class="listik-section__hint">сервер не отдал вердикт по зависимостям</p>
          <p v-else-if="!blockedBy.length" class="listik-section__hint">нет</p>
          <ul v-else class="listik-phone-sheet__deps">
            <li v-for="dep in blockedBy" :key="dep.id">
              <span class="listik-mono">{{ dep.id }}</span>
              <span>{{ dep.title }}</span>
              <UiBadge v-if="dep.stage" tone="info" size="sm">{{ stageCode(dep.stage) }}</UiBadge>
              <span class="listik-section__hint">{{ depHolderHint(dep) }}</span>
              <UiBadge v-if="dep.missing" tone="danger" size="sm">задача не найдена</UiBadge>
            </li>
          </ul>
        </section>

        <section class="listik-section">
          <h4 class="listik-section__title">Последний review</h4>
          <template v-if="lastReview">
            <p class="listik-section__hint">
              {{ commentKindTitle(lastReview.kind) }} · {{ lastReview.author || '—' }} · {{ humanAge(lastReview.created_at) }}
            </p>
            <p class="listik-prose">{{ lastReview.text }}</p>
          </template>
          <p v-else class="listik-section__hint">ещё нет</p>
        </section>

        <section class="listik-section">
          <div class="listik-section__head">
            <h4 class="listik-section__title">
              Журнал
              <UiBadge tone="neutral" size="sm">{{ task.comments.length }}</UiBadge>
            </h4>
          </div>
          <UiTimeline :items="journalItems" dense empty-title="Записей нет" />
        </section>
      </div>
    </div>
  </UiDrawer>
</template>

<!-- Стили — assets/app.css, раздел «Телефон (шаг 06)»: .listik-phone-sheet
     (overflow-wrap для длинных id/путей), .listik-phone-sheet__head (то же для
     заголовка в слоте #header — он вне .listik-phone-sheet и иначе расширяет
     панель) и .listik-phone-sheet__deps. -->
