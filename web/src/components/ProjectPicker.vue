<script setup lang="ts">
/**
 * Переключатель проекта — фильтр `store.filters.project`, который действует на
 * всю доску (все виды), поэтому живёт в шапке (AppHeader.vue), а не в тулбаре.
 * Кит не даёт searchable-select, но даёт `UiCommandPalette` — компактная кнопка
 * открывает её как список с поиском по подстроке (`filterable`, дефолт кита).
 *
 * `:hotkey="false"` обязателен: SearchPanel.vue уже держит глобальный Cmd/Ctrl+K
 * на своей палитре (`hotkey` без значения = true), второй экземпляр с дефолтным
 * `hotkey=true` перехватывал бы тот же хоткей на себя.
 */
import { computed, ref } from 'vue'
import { UiButton, UiCommandPalette, type UiCommandPaletteItem } from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import store from '@/store/listik'

const ALL_LABEL = 'Все проекты'

const open = ref(false)

const currentLabel = computed(() => {
  const slug = store.filters.project
  if (!slug) return ALL_LABEL
  const project = store.meta.value?.projects.find((item) => item.slug === slug)
  return project?.title || project?.slug || slug
})

const items = computed<UiCommandPaletteItem[]>(() => [
  { id: '', label: ALL_LABEL },
  ...(store.meta.value?.projects ?? []).map((project) => ({
    id: project.slug,
    label: project.title || project.slug,
    keywords: [project.slug],
  })),
])

function onSelect(item: UiCommandPaletteItem): void {
  store.filters.project = item.id
  void store.applyFilters()
  open.value = false
}
</script>

<template>
  <UiButton size="sm" variant="ghost" v-bind="{ 'aria-label': 'Фильтр по проекту' }" @click="open = true">
    <template #icon><ListikIcon name="columns" size="sm" /></template>
    {{ currentLabel }}
  </UiButton>

  <UiCommandPalette
    v-model="open"
    :items="items"
    :hotkey="false"
    placeholder="искать проект…"
    empty-title="Проекты не найдены"
    @select="onSelect"
  />
</template>
