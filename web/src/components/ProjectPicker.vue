<script setup lang="ts">
/**
 * Переключатель проекта — фильтр `store.filters.project`, который действует на
 * всю доску (все виды), поэтому живёт в шапке (AppHeader.vue), а не в тулбаре.
 * Кит не даёт searchable-select, но даёт `UiCommandPalette` — метка открывает её
 * как список с поиском по подстроке (`filterable`, дефолт кита).
 *
 * Метка — `UiChip`, а не кнопка: у выбранного проекта справа нужен крестик сброса
 * на «Все проекты», а в ките это `closable`-чип (крестик рисует сам кит, своего
 * SVG не заводим). `:selected="hasProject"` включается тогда же, когда крестик:
 * чип подсвечен и удаляем, только пока фильтр по проекту реально задан. Привязка
 * `selected` заодно переводит чип в интерактивный режим (метка — `<button>`, а не
 * `<span>`), поэтому палитра открывается и с клавиатуры.
 *
 * `:hotkey="false"` обязателен: SearchPanel.vue уже держит глобальный Cmd/Ctrl+K
 * на своей палитре (`hotkey` без значения = true), второй экземпляр с дефолтным
 * `hotkey=true` перехватывал бы тот же хоткей на себя.
 */
import { computed, ref } from 'vue'
import { UiChip, UiCommandPalette, type UiCommandPaletteItem } from '@zoloto585/facet'
import store from '@/store/listik'
import { projectBySlug } from '@/lib/projects'

const ALL_LABEL = 'Все проекты'

const open = ref(false)

/** Фильтр по проекту задан: показываем крестик и подсветку чипа. */
const hasProject = computed(() => Boolean(store.filters.project))

const currentLabel = computed(() => {
  const slug = store.filters.project
  if (!slug) return ALL_LABEL
  const project = projectBySlug(store.meta.value?.projects, slug)
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

/** Крестик чипа: снять фильтр по проекту — доска снова «все проекты». */
function onClose(): void {
  store.filters.project = ''
  void store.applyFilters()
}
</script>

<template>
  <UiChip
    size="sm"
    :label="currentLabel"
    :selected="hasProject"
    :closable="hasProject"
    :hint="hasProject ? 'Фильтр по проекту — крестик вернёт «все проекты»' : 'Фильтр по проекту'"
    @click="open = true"
    @close="onClose"
  />

  <UiCommandPalette
    v-model="open"
    :items="items"
    :hotkey="false"
    placeholder="искать проект…"
    empty-title="Проекты не найдены"
    @select="onSelect"
  />
</template>
