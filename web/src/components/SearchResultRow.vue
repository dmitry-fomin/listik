<script setup lang="ts">
/**
 * SearchResultRow — строка выдачи палитры поиска (слот `item` у
 * `UiCommandPalette`, listik-vjzq). Четыре колонки: эмблема проекта, иконка
 * типа, «id · заголовок» со сниппетом под ним, иконка статуса готовности.
 * Текстового хвоста «проект · этап · исполнитель» нет: проект уже в слаге id,
 * а тип и статус читаются по глифам из `lib/dictionaries.ts`.
 *
 * Классы кита (`ui-command-palette__item-*`) внутри слота не работают — его
 * стили scoped, — поэтому разметка тела строки своя, на тех же токенах.
 */
import { computed } from 'vue'
import type { UiCommandPaletteItem } from '@zoloto585/facet'
import store from '@/store/listik'
import type { SearchResult } from '@/api/types'
import { projectBySlug } from '@/lib/projects'
import ProjectMark from './marks/ProjectMark.vue'
import TaskGlyph from './marks/TaskGlyph.vue'

const props = defineProps<{
  /** Палитра отдаёт `row.item?` — при пустом item рисуем только тело с label. */
  item?: UiCommandPaletteItem
  result: SearchResult | undefined
}>()

const project = computed(() => projectBySlug(store.meta.value?.projects, props.result?.project))
</script>

<template>
  <template v-if="result">
    <ProjectMark :project="project" :slug="result.project" size="sm" />
    <TaskGlyph kind="type" :value="result.issue_type" />
  </template>
  <span class="search-result-row__body">
    <span class="search-result-row__label">{{ item?.label }}</span>
    <span v-if="item?.description" class="search-result-row__description">{{ item.description }}</span>
  </span>
  <TaskGlyph v-if="result" kind="status" :value="result.status" />
</template>

<style scoped>
.search-result-row__body {
  display: flex;
  flex-direction: column;
  min-width: 0;
  flex: 1;
}

.search-result-row__label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  font-weight: var(--weight-medium);
}

.search-result-row__description {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: var(--ink-3);
  font-size: var(--text-xs);
}
</style>
