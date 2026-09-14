<script setup lang="ts">
/**
 * Короткий мобильный просмотр очереди. На телефоне канбан не масштабируется:
 * здесь остаётся один читаемый список и тот же TaskDrawer для деталей.
 */
import { computed, ref } from 'vue'
import { UiBadge, UiButton, UiEmptyState } from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import MobileTaskRow from './MobileTaskRow.vue'
import store from '@/store/listik'
import { sortedTasksByUpdatedDesc } from '@/lib/task-presentation'

const PAGE = 24
const shown = ref(PAGE)

const tasks = computed(() => sortedTasksByUpdatedDesc(store.columns.value.flatMap((column) => column.tasks)))
const visibleTasks = computed(() => tasks.value.slice(0, shown.value))

function openTask(id: string): void {
  void store.openTask(id)
}

function showMore(): void {
  shown.value += PAGE
}
</script>

<template>
  <section class="listik-mobile-queue" aria-label="Очередь задач">
    <div class="listik-mobile-queue__head">
      <div>
        <p class="listik-mobile-queue__eyebrow">Очередь</p>
        <h2 class="listik-section__title">Задачи</h2>
      </div>
      <UiBadge tone="neutral" size="sm">{{ tasks.length }}</UiBadge>
    </div>

    <UiEmptyState
      v-if="!store.loading.value && tasks.length === 0"
      compact
      title="Задач не найдено"
      description="Измените фильтры или обновите очередь."
    >
      <template #icon><ListikIcon name="list" size="lg" /></template>
    </UiEmptyState>

    <div v-else class="listik-mobile-queue__list">
      <MobileTaskRow
        v-for="task in visibleTasks"
        :key="task.id"
        :task="task"
        @open="openTask"
      />
    </div>

    <UiButton v-if="shown < tasks.length" block size="sm" variant="ghost" @click="showMore">
      Показать ещё {{ Math.min(PAGE, tasks.length - shown) }}
    </UiButton>
  </section>
</template>
