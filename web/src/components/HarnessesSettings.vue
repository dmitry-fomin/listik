<script setup lang="ts">
/**
 * Раздел «Харнессы» страницы настроек (`views/SettingsPage.vue`,
 * `/settings/harnesses`): каталог исполнителей `GET /api/harnesses`. Слева —
 * список записей (глиф, ключ, подпись `agent:<key> · …`, пилюли состояния);
 * справа — карточка `HarnessCard.vue` с правкой имени, команды по умолчанию и
 * блоком «Где используется». Заведение — окно `NewHarnessModal.vue`, его
 * открывает первичное действие раздела через `openPrimaryAction`.
 *
 * Харнесс не удаляется: `DELETE` у каталога нет — ненужный выключают
 * тумблером «В списках выбора», и он пропадает из выборов исполнителя,
 * оставаясь в базе (на ключ ссылаются маршруты и уже запущенные задачи).
 */
import { computed, onMounted, ref } from 'vue'
import { UiAlert, UiBadge, UiCard, UiEmptyState, UiSpinner } from '@zoloto585/facet'
import HarnessCard from './HarnessCard.vue'
import NewHarnessModal from './NewHarnessModal.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import store from '@/store/listik'
import type { Harness } from '@/api/types'

/** Список — сам `store.harnesses` (позиции приходят с сервера). */
const harnesses = computed<Harness[]>(() => store.harnesses.value)

const selectedKey = ref<string | null>(null)
const selected = computed<Harness | null>(
  () => harnesses.value.find((item) => item.key === selectedKey.value) ?? null,
)

function selectHarness(item: Harness): void {
  selectedKey.value = item.key
}

/** Подпись строки списка: `agent:<key>` плюс характер записи одной строкой. */
function metaOf(item: Harness): string {
  const parts = [`agent:${item.key}`]
  if (item.kind === 'manual') parts.push('ручная выдача, без команды')
  else if (item.hint) parts.push(item.hint)
  else if (item.builtin) parts.push('встроенный')
  return parts.join(' · ')
}

/*
 * Окно заведения харнесса: его открывает первичное действие раздела — кнопка
 * в шапке страницы зовёт `openPrimaryAction`, а форма и запрос остаются здесь.
 */
const createOpen = ref(false)

function openPrimaryAction(): void {
  createOpen.value = true
}

/** Успех: стор уже перечитал список — выбираем новую запись и её карточку. */
function onCreated(item: Harness): void {
  createOpen.value = false
  selectedKey.value = item.key
}

defineExpose({ openPrimaryAction })

onMounted(() => {
  void store.ensureHarnesses()
})
</script>

<template>
  <div class="listik-harnesses">
    <UiAlert
      v-if="store.harnessesError.value"
      tone="warning"
      closable
      @close="store.harnessesError.value = null"
    >
      <template #title>Не получилось</template>
      {{ store.harnessesError.value }}
    </UiAlert>

    <div class="listik-harnesses__layout">
      <div class="listik-harnesses__list">
        <div
          v-if="store.harnessesLoading.value && harnesses.length === 0"
          class="listik-harnesses__loading"
        >
          <UiSpinner size="sm" label="Читаю харнессы" />
        </div>

        <section v-else class="listik-harnesses__group">
          <UiCard padding="sm">
            <div class="listik-harnesses__group-head">
              <h3 class="listik-section__title">Исполнители</h3>
              <UiBadge tone="neutral" size="sm">{{ harnesses.length }}</UiBadge>
            </div>

            <UiEmptyState v-if="harnesses.length === 0" compact title="Исполнителей нет" />

            <ul v-else class="listik-harnesses__rows">
              <li v-for="item in harnesses" :key="item.key" class="listik-harnesses__row">
                <button
                  type="button"
                  class="listik-routes-row"
                  :class="{ 'is-selected': selectedKey === item.key, 'is-off': !item.enabled }"
                  :data-key="item.key"
                  @click="selectHarness(item)"
                >
                  <HarnessIcon :harness="item.key" size="sm" />
                  <span class="listik-routes-row__main">
                    <span class="listik-routes-row__title">{{ item.label }}</span>
                    <code class="listik-mono">{{ metaOf(item) }}</code>
                  </span>
                  <UiBadge v-if="item.kind === 'manual'" tone="neutral" size="sm">особый</UiBadge>
                  <UiBadge v-else-if="!item.builtin" tone="neutral" size="sm">свой</UiBadge>
                  <UiBadge v-if="!item.enabled" tone="neutral" size="sm">выключен</UiBadge>
                </button>
              </li>
            </ul>
          </UiCard>
        </section>
      </div>

      <UiCard class="listik-harnesses__panel" padding="lg">
        <UiEmptyState
          v-if="!selected"
          compact
          title="Выбери харнесс слева"
          description="Справа откроется карточка: имя, команда по умолчанию и список, где он используется."
        />
        <HarnessCard v-else :key="selected.key" :harness="selected" />
      </UiCard>
    </div>

    <NewHarnessModal v-if="createOpen" @created="onCreated" @close="createOpen = false" />
  </div>
</template>

<style scoped>
.listik-harnesses {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.listik-harnesses .listik-section__title {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-3);
}

/* Та же раскладка, что у «Маршрутов»: узкий список слева, карточка справа. */
.listik-harnesses__layout {
  display: grid;
  grid-template-columns: minmax(0, 240px) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

@media (max-width: 720px) {
  .listik-harnesses__layout {
    grid-template-columns: 1fr;
  }
}

.listik-harnesses__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.listik-harnesses__group {
  min-width: 0;
}

.listik-harnesses__group-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.listik-harnesses__rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-harnesses__row {
  min-width: 0;
}

.listik-harnesses__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-8) 0;
}

.listik-harnesses__panel {
  position: sticky;
  top: calc(var(--header-h) + var(--space-4));
  min-width: 0;
}
</style>
