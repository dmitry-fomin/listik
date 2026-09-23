<script setup lang="ts">
/**
 * Инбокс «Ты нужен» — главный блок страницы: вопросы автору, брошенные задачи
 * и молчащие держатели одной сеткой карточек над доской. Состояние карточки —
 * одна из трёх веток (needs_owner → dead → at-risk), проверяемых строго по
 * порядку; первая сработавшая задаёт бейдж, левую полосу, текст причины и
 * набор кнопок целиком (карточка не красится по статусу целиком).
 *
 * Если задач, где нужен человек, нет, блок не рендерится вовсе — ни заголовка,
 * ни пустого состояния (listik-05pe).
 */
import { computed, ref, watch } from 'vue'
import { UiBadge, UiButton } from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import ProjectMark from './marks/ProjectMark.vue'
import TaskGlyph from './marks/TaskGlyph.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import store from '@/store/listik'
import type { Task } from '@/api/types'
import { taskHealth } from '@/lib/health'
import { stageCode } from '@/lib/stages'
import { humanAge } from '@/lib/format'
import { markdownPlainText } from '@/lib/markdown'
import { projectOf } from '@/lib/task-presentation'

const props = defineProps<{
  tasks: Task[]
}>()

const emit = defineEmits<{
  open: [id: string]
  answer: [task: Task]
  release: [task: Task]
}>()

const PAGE = 6
const expanded = ref(false)

const visibleTasks = computed(() => (expanded.value ? props.tasks : props.tasks.slice(0, PAGE)))
const hiddenCount = computed(() => Math.max(0, props.tasks.length - PAGE))

const COLLAPSED_KEY = 'listik.needsYouCollapsed'

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSED_KEY) === '1'
  } catch {
    return false
  }
}

/** Свёрнута ли сетка карточек целиком (заголовок с бейджем остаётся всегда). */
const collapsed = ref(readCollapsed())

watch(collapsed, (value) => {
  try {
    window.localStorage.setItem(COLLAPSED_KEY, value ? '1' : '0')
  } catch {
    /* приватный режим */
  }
})

type Branch = 'needs_owner' | 'dead' | 'at-risk'

function branchOf(task: Task): Branch {
  if (task.needs_owner) return 'needs_owner'
  if (taskHealth(task) === 'dead') return 'dead'
  return 'at-risk'
}

function badgeText(task: Task): string {
  const branch = branchOf(task)
  if (branch === 'needs_owner') return 'вопрос автору'
  if (branch === 'dead') {
    if (task.abandoned && !task.holder) return 'брошена · без держателя'
    return `брошена ${task.idle_age}`
  }
  if (task.not_taken_warn) return `не взята ${task.assigned_age}`
  return `молчит ${task.idle_age}`
}

function badgeTone(task: Task): 'accent' | 'danger' | 'warning' {
  const branch = branchOf(task)
  if (branch === 'needs_owner') return 'accent'
  if (branch === 'dead') return 'danger'
  return 'warning'
}

function reasonText(task: Task): string {
  const branch = branchOf(task)
  const code = stageCode(task.stage) ?? '—'
  if (branch === 'needs_owner') {
    // Вопрос и заметка держателя — пользовательский markdown; в двухстрочном
    // превью рендерим их плоским текстом без сырых `**`/кавычек.
    const question = store.inboxQuestions.value[task.id]
    if (question) return markdownPlainText(question)
    return markdownPlainText(task.holder_note) || 'вопрос без текста — откройте карточку'
  }
  if (branch === 'dead') {
    if (task.holder) {
      const note = markdownPlainText(task.holder_note)
      const base = `держатель ${task.holder_title} замолчал на ${code} без heartbeat`
      return note ? `${base}; последняя заметка: «${note}»` : base
    }
    return `в работе без держателя с ${humanAge(task.started_at)}`
  }
  if (task.not_taken_warn) {
    const by = task.holder_assigned_by_title ? ` (выдал ${task.holder_assigned_by_title})` : ''
    return `выдана ${task.holder_title}, но не взята ${task.assigned_age}${by}: claim от агента так и не пришёл — прогон не запустился?`
  }
  return `держатель ${task.holder_title} без heartbeat ${task.idle_age} на ${code}; порог брошенности — 24 ч`
}

function footerText(task: Task): string {
  if (task.not_taken) {
    return `выдана ${task.holder_title}, не взята ${task.assigned_age} · на этапе ${task.stage_age} · ${stageCode(task.stage) ?? '—'}`
  }
  return task.holder
    ? `держит ${task.holder_title} · на этапе ${task.stage_age} · ${stageCode(task.stage) ?? '—'}`
    : `без держателя · на этапе ${task.stage_age} · ${stageCode(task.stage) ?? '—'}`
}
</script>

<template>
  <section v-if="tasks.length > 0" class="listik-section" aria-label="Ты нужен">
    <div class="listik-section__head">
      <h2 class="listik-section__title">
        <ListikIcon name="hand" size="md" />
        Ты нужен
        <UiBadge tone="accent" size="sm">{{ tasks.length }}</UiBadge>
      </h2>
      <div class="listik-row">
        <span class="listik-section__hint">
          вопрос автору, брошенные и молчащие держатели — то, без чего конвейер стоит
        </span>
        <UiButton
          size="sm"
          variant="ghost"
          v-bind="{ 'aria-label': collapsed ? 'Развернуть' : 'Свернуть', 'aria-expanded': !collapsed }"
          @click="collapsed = !collapsed"
        >
          <template #icon>
            <ListikIcon name="chevron" size="sm" class="listik-inbox-toggle-icon" :class="{ 'is-expanded': !collapsed }" />
          </template>
        </UiButton>
      </div>
    </div>

    <template v-if="!collapsed">
      <div class="listik-inbox">
        <article
          v-for="task in visibleTasks"
          :key="task.id"
          class="listik-inbox-card"
          role="button"
          tabindex="0"
          :aria-label="`Открыть задачу ${task.title}`"
          @click="emit('open', task.id)"
          @keydown.enter.self.prevent="emit('open', task.id)"
          @keydown.space.self.prevent="emit('open', task.id)"
          :class="{
            'is-danger': branchOf(task) === 'dead',
            'is-warning': branchOf(task) === 'at-risk',
          }"
        >
          <div class="listik-inbox-card__top">
            <TaskGlyph kind="type" :value="task.issue_type" />
            <ProjectMark :project="projectOf(task, store.meta.value?.projects)" :slug="task.project" with-title size="sm" />
            <span class="listik-inbox-card__spacer" />
            <UiBadge :tone="badgeTone(task)" size="sm">{{ badgeText(task) }}</UiBadge>
          </div>

          <h3 class="listik-inbox-card__title" :title="task.title">{{ task.title }}</h3>

          <p class="listik-inbox-card__q">
            <template v-if="branchOf(task) === 'needs_owner'">
              <HarnessIcon :actor="task.holder" />
              <strong>{{ task.holder_title || 'агент' }} спрашивает:</strong>
              {{ reasonText(task) }}
            </template>
            <template v-else>{{ reasonText(task) }}</template>
          </p>

          <div class="listik-inbox-card__foot">
            <span class="listik-section__hint tnum">
              <ListikIcon name="clock" size="xs" />
              {{ footerText(task) }}
            </span>
            <div class="listik-inbox-card__actions" @click.stop>
              <template v-if="branchOf(task) === 'needs_owner'">
                <UiButton size="sm" variant="primary" @click="emit('answer', task)">Ответить</UiButton>
                <UiButton size="sm" variant="ghost" @click="emit('open', task.id)">Открыть</UiButton>
              </template>
              <template v-else-if="branchOf(task) === 'dead'">
                <UiButton size="sm" variant="secondary" @click="emit('release', task)">Освободить</UiButton>
                <UiButton size="sm" variant="ghost" @click="emit('open', task.id)">Открыть</UiButton>
              </template>
              <template v-else>
                <UiButton size="sm" variant="ghost" @click="emit('open', task.id)">Открыть</UiButton>
              </template>
            </div>
          </div>
        </article>
      </div>

      <UiButton v-if="!expanded && hiddenCount > 0" size="sm" variant="ghost" @click="expanded = true">
        Показать ещё {{ hiddenCount }}
      </UiButton>
    </template>
  </section>
</template>

<style scoped>
.listik-inbox-toggle-icon {
  transition: transform 0.15s ease;
}

.listik-inbox-toggle-icon.is-expanded {
  transform: rotate(180deg);
}

.listik-inbox {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-4);
}

@media (min-width: 1024px) {
  .listik-inbox {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (min-width: 1280px) {
  .listik-inbox {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

.listik-inbox-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--hairline);
  border-left: 3px solid var(--accent-500);
  border-radius: var(--radius-lg);
  background: var(--surface);
  box-shadow: var(--shadow-xs);
  cursor: pointer;
}

.listik-inbox-card:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.listik-inbox-card.is-danger {
  border-left-color: var(--danger-500);
}

.listik-inbox-card.is-warning {
  border-left-color: var(--warning-600);
}

.listik-inbox-card__top {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.listik-inbox-card__spacer {
  flex: 1 1 auto;
}

.listik-inbox-card__title {
  margin: 0;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-size: var(--text-base);
  font-weight: var(--weight-medium);
  line-height: var(--leading-snug);
}

.listik-inbox-card__q {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.listik-inbox-card__q strong {
  color: var(--ink-1);
  font-weight: var(--weight-semibold);
}

.listik-inbox-card__foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: auto;
}

.listik-inbox-card__actions {
  display: flex;
  gap: var(--space-2);
}
</style>
