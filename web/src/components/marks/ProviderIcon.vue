<script setup lang="ts">
/**
 * ProviderIcon — глиф вендора одной роли конвейера (ТЗ/критик/исполнитель/судья
 * в `NewTaskModal.vue`). Четыре вендора уже есть как `HarnessIcon`
 * (claude/codex/grok/dsh) — переиспользуем их глифы и цвета. GLM (Z.AI) в
 * `HarnessKey` не входит нарочно: это внешний критик по HTTP, который никогда
 * не становится держателем задачи на сервере, — ему свой маленький глиф.
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
