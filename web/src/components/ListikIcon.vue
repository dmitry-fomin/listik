<script setup lang="ts">
/**
 * ListikIcon — тонкая обёртка над своими SVG-путями (16×16, currentColor).
 * Кит icon-agnostic: иконки всегда приходят в его слоты, а габарит задаётся
 * токенами --icon-xs|sm|md|lg — здесь ровно они.
 */
import { computed } from 'vue'
import { icons } from '@/lib/icons'

type IconSize = 'xs' | 'sm' | 'md' | 'lg'

const props = withDefaults(
  defineProps<{
    name: string
    size?: IconSize
  }>(),
  { size: 'sm' },
)

const icon = computed(() => icons[props.name] ?? null)
const dimension = computed(() => `var(--icon-${props.size})`)
</script>

<template>
  <svg
    v-if="icon"
    viewBox="0 0 16 16"
    :style="{ width: dimension, height: dimension, flexShrink: 0 }"
    fill="none"
    aria-hidden="true"
  >
    <path
      v-if="icon.stroke"
      :d="icon.stroke"
      stroke="currentColor"
      stroke-width="1.6"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <path v-if="icon.fill" :d="icon.fill" fill="currentColor" />
  </svg>
</template>
