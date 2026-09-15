<script setup lang="ts">
/**
 * MarkdownProse — вывод текстов карточки (ТЗ, критерии, дизайн, заметки,
 * результат, review) с markdown-форматированием.
 *
 * Это локальный компонент, а не Ui*-кит: в списке компонентов
 * `node_modules/@zoloto585/facet/README.md` read-only-вьюера markdown нет —
 * в «Admin-паттернах» есть только редактор `UiMarkdownEditor` (он для ввода, а
 * не показа). Сам рендер берётся из кита: `lib/markdown.ts` вызывает
 * `renderMarkdownToHtml`. `v-html` живёт только здесь, и его вход — ровно
 * результат `renderMarkdown`, не сырое поле задачи.
 */
import { computed } from 'vue'
import { renderMarkdown } from '@/lib/markdown'

const props = defineProps<{ text?: string | null }>()

const html = computed(() => renderMarkdown(props.text))
const hasText = computed(() => Boolean(props.text))
</script>

<template>
  <div v-if="hasText" class="listik-prose listik-prose--markdown" v-html="html" />
  <p v-else class="listik-prose">—</p>
</template>