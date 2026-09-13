<script lang="ts">
/** Размер страницы очереди телефона (первая загрузка и «показать ещё»). */
export const PHONE_PAGE = 20
</script>

<script setup lang="ts">
/**
 * Единственная секция телефонного режима (шаг 06, порция b): фильтр «проект» +
 * поиск + постраничная очередь задач. Данные — свой запрос store.loadQueuePage
 * (не store.columns/store.loadListTasks — те тянут доску/применяют все фильтры).
 */
import { computed, ref, watch } from 'vue'
import { UiBadge, UiButton, UiEmptyState, UiInput, UiSelect, UiSkeleton, type UiSelectOption } from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import MobileTaskRow from '@/components/MobileTaskRow.vue'
import store from '@/store/listik'
import { projectOptions } from '@/components/facets'
import type { Task } from '@/api/types'

const searchText = ref(store.query.value)

const projectSelectOptions = computed<UiSelectOption[]>(() => [
  { value: '', label: 'все проекты' },
  ...projectOptions(store.meta.value).map((option) => ({ value: option.value, label: option.label })),
])

const tasks = ref<Task[]>([])
const total = ref(0)
const loaded = ref(false)
const loading = ref(false)
const loadingMore = ref(false)
let inFlight = false

function setProject(value: unknown): void {
  store.filters.project = String(value ?? '')
}

function submitSearch(): void {
  store.openSearch(searchText.value)
}

function mergeById(existing: Task[], incoming: Task[]): Task[] {
  const seen = new Set(existing.map((task) => task.id))
  const merged = existing.slice()
  for (const task of incoming) {
    if (seen.has(task.id)) continue
    seen.add(task.id)
    merged.push(task)
  }
  return merged
}

/** Перечитывание: заменяет список ответом, сохраняя число загруженных строк. */
async function reload(): Promise<void> {
  if (inFlight) return
  inFlight = true
  const limit = Math.min(200, Math.max(PHONE_PAGE, tasks.value.length))
  const isFirstLoad = tasks.value.length === 0
  if (isFirstLoad) loading.value = true
  try {
    const page = await store.loadQueuePage({ limit, offset: 0 })
    if (page) {
      tasks.value = page.tasks
      total.value = page.total
      loaded.value = true
    }
  } finally {
    loading.value = false
    inFlight = false
  }
}

async function loadMore(): Promise<void> {
  loadingMore.value = true
  try {
    const page = await store.loadQueuePage({ limit: PHONE_PAGE, offset: tasks.value.length })
    if (page) {
      tasks.value = mergeById(tasks.value, page.tasks)
      total.value = page.total
    }
  } finally {
    loadingMore.value = false
  }
}

watch(
  () => store.queueTick.value,
  () => void reload(),
)

watch(
  () => store.filters.project,
  () => {
    tasks.value = []
    total.value = 0
    void reload()
  },
)

if (store.token.value) void reload()

const moreCount = computed(() => Math.min(PHONE_PAGE, total.value - tasks.value.length))
</script>

<template>
  <section class="listik-phone-queue" aria-label="Очередь задач">
    <div class="listik-phone-toolbar">
      <UiSelect
        :model-value="store.filters.project"
        :options="projectSelectOptions"
        size="sm"
        v-bind="{ 'aria-label': 'Проект' }"
        @update:model-value="setProject"
      />
      <UiInput
        v-model="searchText"
        size="sm"
        placeholder="поиск"
        v-bind="{
          'aria-label': 'Поиск по задачам',
          onKeydown: (event: KeyboardEvent) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              submitSearch()
            }
          },
        }"
      >
        <template #leadingIcon><ListikIcon name="search" size="sm" /></template>
      </UiInput>
    </div>

    <div class="listik-phone-queue__head">
      <h2 class="listik-section__title">Задачи</h2>
      <UiBadge v-if="loaded" tone="neutral" size="sm"> {{ tasks.length }} из {{ total }} </UiBadge>
    </div>

    <template v-if="loading && tasks.length === 0">
      <UiSkeleton v-for="n in 3" :key="n" variant="rect" height="var(--space-24)" />
    </template>

    <UiEmptyState
      v-else-if="!loading && total === 0"
      compact
      title="Задач нет"
      description="Измените проект или обновите очередь."
    >
      <template #icon><ListikIcon name="list" /></template>
    </UiEmptyState>

    <div v-else class="listik-phone-queue__list">
      <MobileTaskRow v-for="task in tasks" :key="task.id" :task="task" @open="(id) => store.openTask(id)" />
    </div>

    <UiButton
      v-if="tasks.length < total"
      block
      size="sm"
      variant="ghost"
      class="listik-phone-queue__more"
      :loading="loadingMore"
      @click="loadMore"
    >
      Показать ещё {{ moreCount }}
    </UiButton>
  </section>
</template>
