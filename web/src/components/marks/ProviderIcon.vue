<script setup lang="ts">
/**
 * ProviderIcon — глиф вендора одной роли конвейера (ТЗ/критик/исполнитель/судья
 * в `RoutePicker`). Четыре вендора уже есть как `HarnessIcon`
 * (claude/codex/grok/dsh) — переиспользуем их глифы и цвета. GLM (Z.AI) в
 * `HarnessKey` не входит нарочно: это внешний критик по HTTP, который никогда
 * не становится держателем задачи на сервере, — ему свой маленький глиф;
 * devin — тоже свой: три шестиугольника его лого, монохромно, как grok.
 */
import HarnessIcon from './HarnessIcon.vue'
import type { HarnessKey } from '@/lib/harness'
import type { ProviderKey } from '@/lib/pipelines'

const props = withDefaults(defineProps<{ provider: ProviderKey; size?: 'xs' | 'sm' | 'md' }>(), { size: 'sm' })

const HARNESS_OF: Partial<Record<ProviderKey, HarnessKey>> = {
  claude: 'claude',
  openai: 'codex',
  grok: 'grok',
  deepseek: 'dsh',
}
</script>

<template>
  <HarnessIcon v-if="HARNESS_OF[props.provider]" :harness="HARNESS_OF[props.provider]!" :size="size" />
  <span
    v-else-if="props.provider === 'devin'"
    class="listik-harness-icon"
    :class="`listik-harness-icon--${size}`"
    style="color: var(--listik-harness-devin)"
    title="devin"
  >
    <svg viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M5 1.4 7.6 2.9v3L5 7.4 2.4 5.9v-3Z" />
      <path d="M5 8.6 7.6 10.1v3L5 14.6 2.4 13.1v-3Z" />
      <path d="M10.8 5 13.4 6.5v3L10.8 11 8.2 9.5v-3Z" />
    </svg>
  </span>
  <span
    v-else
    class="listik-harness-icon"
    :class="`listik-harness-icon--${size}`"
    style="color: var(--listik-harness-glm)"
    title="GLM"
  >
    <svg viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 2.2 13.2 5.4v5.2L8 13.8 2.8 10.6V5.4Z"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linejoin="round"
      />
      <path d="M8 2.2v11.6" stroke="currentColor" stroke-width="1.25" />
    </svg>
  </span>
</template>
