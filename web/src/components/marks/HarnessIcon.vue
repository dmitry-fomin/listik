<script setup lang="ts">
/**
 * HarnessIcon — глиф харнесса-держателя (прототип: `.hico`). SVG-пути — ассеты
 * из прототипа (multi-path/fill, не влезают в контракт icons.ts), фирменные
 * цвета — токены `--listik-harness-*` (app.css). `human`/`me` и запись с
 * `icon='user'` — обычная иконка `user` из общего словаря. `null`/пустой
 * актор — ничего не рендерит.
 *
 * Глиф решается так: явный проп `icon` (пикер иконки в карточке), потом `icon`
 * записи каталога — он и есть выбранный знак (`PATCH /api/harnesses/<key>`),
 * в том числе `null` — осознанный «общий глиф». Вывод из ключа (точное
 * совпадение или голова до дефиса, `pi-foo` → `pi`) работает только когда
 * записи в каталоге нет — каталог ещё не догрузился или ключ не из него.
 * Фирменного глифа нет (свой ключ без иконки, незнакомый `icon`) —
 * рисуется общий `bolt`.
 */
import { computed, onMounted } from 'vue'
import ListikIcon from '@/components/ListikIcon.vue'
import store from '@/store/listik'
import { HARNESS_GLYPHS, harnessOf, harnessTitle, type HarnessKey } from '@/lib/harness'

const props = withDefaults(
  defineProps<{
    actor?: string | null
    harness?: HarnessKey | string
    /** Явный ключ глифа — рисуется сам, минуя ключ и каталог (пикер иконки). */
    icon?: string
    size?: 'xs' | 'sm' | 'md'
  }>(),
  { actor: undefined, harness: undefined, icon: undefined, size: 'sm' },
)

/** Ключи каталога: голый держатель `mini`/`devin` разбирается ключом, не «человеком». */
const catalogKeys = computed(() => new Set(store.harnesses.value.map((item) => item.key)))
onMounted(() => store.ensureHarnesses())

const resolved = computed<string | null>(
  () => props.harness ?? harnessOf(props.actor, catalogKeys.value),
)
const title = computed(() => harnessTitle(resolved.value))

/** Запись каталога для разобранного ключа — её `icon` задаёт глиф явно. */
const record = computed(() =>
  store.harnesses.value.find((item) => item.key === resolved.value),
)

/** Что умеет рисовать шаблон: фирменные ветки плюс `user` из общего словаря. */
const DRAWABLE = new Set([...HARNESS_GLYPHS, 'user'])

const glyph = computed<string | null>(() => {
  if (props.icon) return DRAWABLE.has(props.icon) ? props.icon : null
  const key = resolved.value
  if (!key || key === 'human' || key === 'me') return null
  // Запись каталога есть — решает её `icon`: `null` («общий глиф») и
  // незнакомый ключ дают bolt, фолбэка на ключ харнесса нет.
  if (record.value !== undefined) {
    const icon = record.value.icon
    return icon && DRAWABLE.has(icon) ? icon : null
  }
  if (HARNESS_GLYPHS.includes(key)) return key
  const head = key.split('-', 1)[0]
  return head && HARNESS_GLYPHS.includes(head) ? head : null
})
</script>

<template>
  <ListikIcon
    v-if="resolved === 'human' || resolved === 'me' || glyph === 'user'"
    name="user"
    :size="size"
  />
  <ListikIcon v-else-if="(resolved || icon) && !glyph" name="bolt" :size="size" />
  <span
    v-else-if="glyph"
    class="listik-harness-icon"
    :class="`listik-harness-icon--${size}`"
    :style="{ color: `var(--listik-harness-${glyph}, var(--ink-3))` }"
    :title="title"
  >
    <svg v-if="glyph === 'claude'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M4.6 5.4h6.8a1.6 1.6 0 0 1 1.6 1.6v3.4a1.6 1.6 0 0 1-1.6 1.6H4.6A1.6 1.6 0 0 1 3 10.4V7a1.6 1.6 0 0 1 1.6-1.6ZM6.2 5.4V3.8M9.8 5.4V3.8M5.4 12v1.6M8 12v1.6M10.6 12v1.6"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
      <path d="M6.4 8.6h.01M9.6 8.6h.01" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" />
    </svg>
    <svg v-else-if="glyph === 'dsh'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M3 9.6C3 6.4 5.6 4.4 8.8 4.6c1.8.1 3.2 1 4 2.2.3-1.6 1-2.9 2-3.6-.1 1.4.1 2.6.6 3.6-1 .1-1.8.5-2.4 1.2.1.4.2.9.2 1.4 0 2.6-2.4 4.4-5.6 4.4C4.6 13.8 3 12 3 9.6Z"
        fill="currentColor"
      />
      <circle cx="10.6" cy="8.2" r="1" fill="var(--surface)" />
      <path d="M4.2 10.4c1.6.8 3.4.6 5-.6" stroke="var(--surface)" stroke-width="1" stroke-linecap="round" />
    </svg>
    <svg v-else-if="glyph === 'codex'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M5.2 12.6h6.2a2.6 2.6 0 0 0 .5-5.2 3.6 3.6 0 0 0-6.9-.9 3 3 0 0 0 .2 6.1Z"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <svg v-else-if="glyph === 'grok'" viewBox="0 0 16 16" fill="none" aria-hidden="true">
      <path
        d="M8 13.4a5.4 5.4 0 1 0 0-10.8 5.4 5.4 0 0 0 0 10.8ZM2.8 13.2 13.2 2.8"
        stroke="currentColor"
        stroke-width="1.25"
        stroke-linecap="round"
        stroke-linejoin="round"
      />
    </svg>
    <svg v-else-if="glyph === 'devin'" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M5 1.4 7.6 2.9v3L5 7.4 2.4 5.9v-3Z" />
      <path d="M5 8.6 7.6 10.1v3L5 14.6 2.4 13.1v-3Z" />
      <path d="M10.8 5 13.4 6.5v3L10.8 11 8.2 9.5v-3Z" />
    </svg>
    <!-- pi: фирменного пути нет, глиф — буква «π» моношириной, как в макете.
         У pi-glm/pi-deepseek — та же «π» сдвинута влево плюс точка цвета
         модели в правом нижнем углу. -->
    <svg v-else-if="glyph === 'pi'" viewBox="0 0 16 16" aria-hidden="true">
      <text
        x="8"
        y="13.2"
        text-anchor="middle"
        font-family="var(--font-mono)"
        font-size="15"
        fill="currentColor"
      >π</text>
    </svg>
    <svg v-else-if="glyph === 'pi-glm'" viewBox="0 0 16 16" aria-hidden="true">
      <text
        x="6.6"
        y="12.4"
        text-anchor="middle"
        font-family="var(--font-mono)"
        font-size="12"
        fill="currentColor"
      >π</text>
      <circle
        cx="12.2"
        cy="12.2"
        r="2.3"
        fill="var(--listik-harness-glm)"
        stroke="var(--surface)"
        stroke-width="1"
      />
    </svg>
    <svg v-else-if="glyph === 'pi-deepseek'" viewBox="0 0 16 16" aria-hidden="true">
      <text
        x="6.6"
        y="12.4"
        text-anchor="middle"
        font-family="var(--font-mono)"
        font-size="12"
        fill="currentColor"
      >π</text>
      <circle
        cx="12.2"
        cy="12.2"
        r="2.3"
        fill="var(--listik-harness-dsh)"
        stroke="var(--surface)"
        stroke-width="1"
      />
    </svg>
  </span>
</template>
