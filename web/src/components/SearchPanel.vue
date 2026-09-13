<script setup lang="ts">
/**
 * Поиск: только палитра команд (Cmd/Ctrl+K). Вход — тулбар (`BoardToolbar`) и
 * хоткей; строка ввода и инлайн-список результатов сюда не переехали — палитра
 * показывает результаты сама.
 *
 * Контракт `onPaletteSearch`: пустая строка игнорируется полностью. Палитра
 * при каждом открытии обнуляет своё поле и эмитит `search('')` — если бы это
 * доходило до `store.query`/`runSearch`, второе открытие стирало бы и текст,
 * набранный в тулбаре, и уже показанные результаты. Очистка — только явно,
 * через `store.clearSearch()` (кнопки для неё в этой порции нет).
 */
import { computed, onBeforeUnmount } from 'vue'
import { UiCommandPalette, type UiCommandPaletteItem } from '@zoloto585/facet'
import store from '@/store/listik'

const items = computed<UiCommandPaletteItem[]>(() =>
  (store.searchResponse.value?.results ?? []).map((result) => ({
    id: result.id,
    label: `${result.id} · ${result.title}`,
    description: result.snippet,
    hint: [
      result.project ?? 'без проекта',
      result.stage ?? 'без этапа',
      result.actor_name || result.actor || 'без исполнителя',
    ].join(' · '),
    group: result.needs_owner ? 'нужен ты' : 'задачи',
    keywords: [result.id, result.issue_type, ...result.labels],
  })),
)

let debounce: ReturnType<typeof setTimeout> | null = null

function onPaletteSearch(text: string): void {
  if (!text) return
  store.query.value = text
  if (debounce) clearTimeout(debounce)
  debounce = setTimeout(() => {
    void store.runSearch()
  }, 400)
}

onBeforeUnmount(() => {
  if (debounce) clearTimeout(debounce)
})

function onSelect(item: UiCommandPaletteItem): void {
  void store.openTask(item.id)
}
</script>

<template>
  <UiCommandPalette
    v-model="store.paletteOpen.value"
    :items="items"
    :loading="store.searchLoading.value"
    :filterable="false"
    placeholder="Искать задачу: текст, метка, ID…"
    empty-title="Ничего не найдено"
    hotkey
    @search="onPaletteSearch"
    @select="onSelect"
  />
</template>
