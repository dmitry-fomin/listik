<script setup lang="ts">
/**
 * ProjectMark — квадрат-монограмма проекта (прототип: `.pmark`). Знак и цвет
 * считает `projectMark()` (lib/projects.ts), здесь только разметка/размер.
 */
import { computed } from 'vue'
import type { ProjectRow } from '@/api/types'
import { projectMark } from '@/lib/projects'

const props = withDefaults(
  defineProps<{
    project: ProjectRow | null
    slug?: string | null
    size?: 'sm' | 'md'
    withTitle?: boolean
  }>(),
  { slug: null, size: 'md', withTitle: false },
)

const result = computed(() => projectMark(props.project, props.slug))
</script>

<template>
  <span class="listik-row" style="flex-wrap: nowrap">
    <span
      class="listik-pmark"
      :class="`listik-pmark--${size}`"
      :style="{ background: result.color }"
      :title="project?.slug ?? slug ?? undefined"
    >{{ result.mark }}</span>
    <span v-if="withTitle" class="listik-pmark__title">{{ result.title }}</span>
  </span>
</template>
