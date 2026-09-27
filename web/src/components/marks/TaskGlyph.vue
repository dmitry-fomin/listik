<script setup lang="ts">
/**
 * TaskGlyph — иконка типа задачи, приоритета или статуса готовности
 * (прототип: `.pico`). Путь, цвет и подпись берутся из справочника
 * `lib/dictionaries.ts`, сам путь рисует `ListikIcon` из общего словаря
 * `icons.ts`.
 */
import { computed } from 'vue'
import { UiTooltip } from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import { priority, statusItem, taskType } from '@/lib/dictionaries'

const props = withDefaults(
  defineProps<{
    kind: 'type' | 'priority' | 'status'
    value: string | number
    size?: 'sm' | 'md'
  }>(),
  { size: 'sm' },
)

const glyph = computed(() => {
  if (props.kind === 'priority') {
    const item = priority(props.value)
    return { icon: item.icon, color: item.color, title: `P${item.value} · ${item.label}` }
  }
  if (props.kind === 'status') {
    const item = statusItem(String(props.value))
    return { icon: item.icon, color: item.color, title: item.label }
  }
  const item = taskType(String(props.value))
  return { icon: item.icon, color: item.color, title: item.hint }
})
</script>

<template>
  <UiTooltip :text="glyph.title">
    <span class="listik-glyph" :style="{ color: glyph.color }" :aria-label="glyph.title">
      <ListikIcon :name="glyph.icon" :size="size === 'md' ? 'sm' : 'xs'" />
    </span>
  </UiTooltip>
</template>
