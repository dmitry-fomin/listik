<script setup lang="ts">
/**
 * Одна строка компактного списка задач: используется планшетным
 * MobileTaskList (< 1024 px) и телефонным режимом (шаг 06, порция b).
 */
import { UiBadge, UiStatusPill } from '@zoloto585/facet'
import { computed } from 'vue'
import type { Task } from '@/api/types'
import { humanAge, taskStageLabel } from '@/lib/format'
import { HEALTH_TITLES, taskHealth } from '@/lib/health'

const props = defineProps<{ task: Task }>()
const emit = defineEmits<{ open: [id: string] }>()

/** «Взята» и «выдана, но не взята» — разные состояния: вторая строка это показывает. */
const holderText = computed(() => {
  if (!props.task.holder) return 'держателя нет'
  if (props.task.not_taken) return `выдана, не взята ${props.task.assigned_age}`
  return `держит ${props.task.holder_title}`
})
</script>

<template>
  <button type="button" class="listik-mobile-task" @click="emit('open', props.task.id)">
    <span class="listik-mobile-task__top">
      <span class="listik-mono">{{ props.task.id }}</span>
      <UiStatusPill :tone="taskHealth(props.task)" size="sm">{{ HEALTH_TITLES[taskHealth(props.task)] }}</UiStatusPill>
    </span>
    <strong class="listik-mobile-task__title">{{ props.task.title }}</strong>
    <span class="listik-mobile-task__meta">
      <UiBadge tone="neutral" size="sm">{{ props.task.project || 'без проекта' }}</UiBadge>
      <span>{{ taskStageLabel(props.task) }}</span>
    </span>
    <span class="listik-mobile-task__meta listik-mobile-task__meta--muted">
      <span>{{ holderText }}</span>
      <span aria-hidden="true">·</span>
      <span>{{ props.task.holder_at ? `heartbeat ${humanAge(props.task.holder_at)}` : 'heartbeat —' }}</span>
    </span>
  </button>
</template>
