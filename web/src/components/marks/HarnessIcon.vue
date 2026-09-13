<script setup lang="ts">
/**
 * HarnessIcon — глиф харнесса-держателя (прототип: `.hico`). SVG-пути — ассеты
 * из прототипа (multi-path/fill, не влезают в контракт icons.ts), фирменные
 * цвета — токены `--listik-harness-*` (app.css). Для `human` — обычная иконка
 * `user` из общего словаря. `null`/пустой актор — ничего не рендерит.
 */
import { computed } from 'vue'
import ListikIcon from '@/components/ListikIcon.vue'
import { harnessOf, HARNESS_TITLES, type HarnessKey } from '@/lib/harness'

const props = withDefaults(
  defineProps<{
    actor?: string | null
    harness?: HarnessKey
    size?: 'xs' | 'sm' | 'md'
  }>(),
  { actor: undefined, harness: undefined, size: 'sm' },
)

const resolved = computed<HarnessKey | null>(() => props.harness ?? harnessOf(props.actor))
const title = computed(() => (resolved.value ? HARNESS_TITLES[resolved.value] : ''))
</script>

<template>
  <ListikIcon v-if="resolved === 'human'" name="user" :size="size" />
  <span
    v-else-if="resolved"
    class="listik-harness-icon"
    :class="`listik-harness-icon--${size}`"
    :style="{ color: `var(--listik-harness-${resolved})` }"
    :title="title"
  >
    <svg v-if="resolved === 'claude'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M4.6 5.4h6.8a1.6 1.6 0 0 1 1.6 1.6v3.4a1.6 1.6 0 0 1-1.6 1.6H4.6A1.6 1.6 0 0 1 3 10.4V7a1.6 1.6 0 0 1 1.6-1.6ZM6.2 5.4V3.8M9.8 5.4V3.8M5.4 12v1.6M8 12v1.6M10.6 12v1.6"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <path d="M6.4 8.6h.01M9.6 8.6h.01" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
    </svg>
    <svg v-else-if="resolved === 'dsh'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 9.6C3 6.4 5.6 4.4 8.8 4.6c1.8.1 3.2 1 4 2.2.3-1.6 1-2.9 2-3.6-.1 1.4.1 2.6.6 3.6-1 .1-1.8.5-2.4 1.2.1.4.2.9.2 1.4 0 2.6-2.4 4.4-5.6 4.4C4.6 13.8 3 12 3 9.6Z"
        fill="currentColor"
      />
      <circle cx="10.6" cy="8.2" r="1" fill="var(--surface)" />
      <path d="M4.2 10.4c1.6.8 3.4.6 5-.6" stroke="var(--surface)" stroke-width="1" stroke-linecap="round" />
    </svg>
    <svg v-else-if="resolved === 'codex'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M5.2 12.6h6.2a2.6 2.6 0 0 0 .5-5.2 3.6 3.6 0 0 0-6.9-.9 3 3 0 0 0 .2 6.1Z"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <svg v-else-if="resolved === 'grok'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 13.4a5.4 5.4 0 1 0 0-10.8 5.4 5.4 0 0 0 0 10.8ZM2.8 13.2 13.2 2.8"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <svg v-else-if="resolved === 'gemini'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 2.2c.4 3.3 2.5 5.4 5.8 5.8-3.3.4-5.4 2.5-5.8 5.8-.4-3.3-2.5-5.4-5.8-5.8 3.3-.4 5.4-2.5 5.8-5.8Z"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
  </span>
</template>
