<script setup lang="ts">
/**
 * Одна строка компактного списка задач: используется планшетным
 * MobileTaskList (< 1024 px) и телефонным режимом (шаг 06, порция b).
 */
import { UiBadge, UiStatusPill } from '@zoloto585/facet'
import type { Task } from '@/api/types'
import { humanAge, taskStageLabel } from '@/lib/format'
import { HEALTH_TITLES, taskHealth } from '@/lib/health'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{ open: [id: string] }>()

function healthTone(task: Task): 'healthy' | 'at-risk' | 'dead' | 'unknown' {
  return taskHealth(task)
}

function healthLabel(task: Task): string {
  return HEALTH_TITLES[taskHealth(task)]
}
</script>

<template>
  <button type="button" class="listik-mobile-task" @click="emit('open', props.task.id)">
    <span class="listik-mobile-task__top">
      <span class="listik-mono">{{ props.task.id }}</span>
      <UiStatusPill :tone="healthTone(props.task)" size="sm">{{ healthLabel(props.task) }}</UiStatusPill>
    </span>
    <strong class="listik-mobile-task__title">{{ props.task.title }}</strong>
    <span class="listik-mobile-task__meta">
      <UiBadge tone="neutral" size="sm">{{ props.task.project || 'без проекта' }}</UiBadge>
      <span>{{ taskStageLabel(props.task) }}</span>
    </span>
    <span class="listik-mobile-task__meta listik-mobile-task__meta--muted">
      <span>{{ props.task.holder ? `держит ${props.task.holder_title}` : 'держателя нет' }}</span>
      <span aria-hidden="true">·</span>
      <span>{{ props.task.holder_at ? `heartbeat ${humanAge(props.task.holder_at)}` : 'heartbeat —' }}</span>
    </span>
  </button>
</template>
