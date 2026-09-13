<script setup lang="ts">
/**
 * HealthDot — точка здоровья задачи (прототип: `.hdot`). Цвета строго из
 * токенов `--health-*`; `unknown` — прозрачная с внутренней обводкой (правило
 * кита: healthy — нейтральная `--ink-2`, не зелёная; красный — только dead).
 */
import { computed } from 'vue'
import type { Health } from '@/lib/health'

const props = withDefaults(
  defineProps<{
    health: Health
    size?: 'sm' | 'md'
    label?: string
  }>(),
  { size: 'sm', label: undefined },
)

const title = computed(() => props.label ?? `здоровье: ${props.health}`)
</script>

<template>
  <span
    class="listik-health-dot"
    :class="[`listik-health-dot--${size}`, health !== 'healthy' ? `listik-health-dot--${health}` : null]"
    role="img"
    :aria-label="title"
    :title="title"
  />
</template>
