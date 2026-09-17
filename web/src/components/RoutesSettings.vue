<script setup lang="ts">
/**
 * Вкладка «Маршруты» настроек (`ProjectSettings.vue` → `#panel-routes`):
 * список записей `routes` (конвейеры и прямая выдача), их порядок, скрытие,
 * заведение под скил и удаление. Правка полей записи с автосохранением
 * (`title`/`hint`/`icon`/`visible`), состав ролей и argv команды — другие
 * порции (`e`/`f`); здесь правая карточка только читает.
 *
 * Список — два экземпляра `UiRecordList` (кит сам даёт перетаскивание за
 * ручку, кнопки ▲/▼, ✕ у строки и кнопку добавления снизу): один под
 * «Конвейеры» (`kind=pipeline`), второй под «Прямая выдача» (`kind=direct`).
 * У обоих ровно одна колонка `type: 'custom'` — своей таблицы и своего
 * drag&drop нет нигде в файле.
 *
 * `UiRecordList` мутирует свою модель (`v-model`) раньше, чем эмитит событие:
 * `addRow()` уже добавил в модель пустую строку, `removeRow()` уже убрал
 * строку — оба события в этом файле сначала откатывают эту мутацию (список
 * возвращается к серверному состоянию), а затем открывают своё окно (модалку
 * «Завести маршрут» или подтверждение удаления). Так «добавить» и «удалить»
 * остаются осознанными действиями с обратной связью, а не мгновенной правкой
 * списка без сервера. У перестановки (`reorder`) обратный откат — только на
 * отказ запроса: пока `POST /api/routes/reorder` не ответил, список уже
 * показывает новый порядок (кит переставил модель сам).
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmDialog,
  UiEmptyState,
  UiModal,
  UiRecordList,
  type UiRecordListColumn,
} from '@zoloto585/facet'
import RouteCard from './RouteCard.vue'
import RouteIcon from './marks/RouteIcon.vue'
import store from '@/store/listik'
import type { DirectRouteDef, PipelineRouteDef, RouteDef, RouteSkillInfo } from '@/api/types'
import { directRoutesOf, pipelineRowsOf } from '@/lib/routes'

/** Порядок сервера — единый источник, локальные списки под `UiRecordList` синхронизируются от него. */
const pipelineRoutes = computed<PipelineRouteDef[]>(() => pipelineRowsOf(store.routes.value))
const directRoutes = computed<DirectRouteDef[]>(() => directRoutesOf(store.routes.value))

const pipelineRows = ref<PipelineRouteDef[]>(pipelineRoutes.value.slice())
const directRows = ref<DirectRouteDef[]>(directRoutes.value.slice())

function syncPipelineRows(): void {
  pipelineRows.value = pipelineRoutes.value.slice()
}
function syncDirectRows(): void {
  directRows.value = directRoutes.value.slice()
}

/**
 * `pipelineRows`/`directRows` — свои `ref`, а не сам computed: `UiRecordList`
 * должен уметь оптимистично мутировать их у себя (перестановка/добавление/
 * удаление меняют модель раньше эмита события). Без этого watch список не
 * увидел бы ни одной серверной правки, кроме тех двух моментов, где код сам
 * дергает `syncPipelineRows`/`syncDirectRows` — а патч/создание/удаление
 * маршрута приходят только через `store.routes`, без прямого вызова этих
 * функций, поэтому строка не появлялась и не пропадала из списка сама.
 */
watch(pipelineRoutes, syncPipelineRows)
watch(directRoutes, syncDirectRows)

const columns: UiRecordListColumn[] = [{ key: 'route', label: 'Маршрут', type: 'custom' }]

const selectedKey = ref<string | null>(null)
const selected = computed<RouteDef | null>(
  () => store.routes.value.find((route) => route.key === selectedKey.value) ?? null,
)

function selectRoute(route: RouteDef): void {
  selectedKey.value = route.key
}

/**
 * Перестановка: общий сквозной порядок — сначала все конвейеры в своём
 * порядке, затем все прямые в своём. Модель обоих списков `UiRecordList` уже
 * переставлена (кит меняет её раньше эмита), поэтому это ровно текущий
 * порядок на экране. Отказ запроса возвращает списки к серверному порядку.
 */
async function handleReorder(): Promise<void> {
  const keys = [...pipelineRows.value.map((route) => route.key), ...directRows.value.map((route) => route.key)]
  const result = await store.reorderRoutes(keys)
  if (!result) {
    syncPipelineRows()
    syncDirectRows()
  }
}

function stubPipelineRow(): PipelineRouteDef {
  return { key: '', title: '', hint: '', visible: false, position: 0, command: null, kind: 'pipeline', roles: {} }
}
function stubDirectRow(): DirectRouteDef {
  return { key: '', title: '', hint: '', visible: false, position: 0, command: null, kind: 'direct', harness: 'claude' }
}

/* ── удаление: ✕ строки кита уже вычистил её из модели — откатываем и спрашиваем подтверждение ── */

const removeTarget = ref<RouteDef | null>(null)
const removeBusy = ref(false)
const removeResult = ref<{ removed: string; tasks_cleared: number } | null>(null)

function askRemove(route: RouteDef): void {
  removeResult.value = null
  removeTarget.value = route
}

function handleRemovePipeline(row: PipelineRouteDef): void {
  syncPipelineRows()
  askRemove(row)
}
function handleRemoveDirect(row: DirectRouteDef): void {
  syncDirectRows()
  askRemove(row)
}

function cancelRemove(): void {
  removeTarget.value = null
}

async function confirmRemove(): Promise<void> {
  const route = removeTarget.value
  if (!route) return
  removeBusy.value = true
  const result = await store.deleteRoute(route.key)
  removeBusy.value = false
  removeTarget.value = null
  if (!result) return
  removeResult.value = result
  if (selectedKey.value === route.key) selectedKey.value = null
}

/** «Скрыть» у строки с `skill_missing`: сервер и так отдаёт её скрытой, действие закрепляет это в базе. */
async function hideRoute(route: RouteDef): Promise<void> {
  await store.patchRoute(route.key, { visible: false })
}

/* ── «Завести маршрут»: кнопка добавления кита открывает общую модалку у обоих списков ── */

const addOpen = ref(false)
const addBusy = ref(false)

async function openAddModal(): Promise<void> {
  addOpen.value = true
  await store.loadRoutesSync()
}

function handleAddPipeline(): void {
  syncPipelineRows()
  void openAddModal()
}
function handleAddDirect(): void {
  syncDirectRows()
  void openAddModal()
}

async function pickMissingRoute(item: RouteSkillInfo): Promise<void> {
  if (addBusy.value) return
  addBusy.value = true
  const created = await store.createRoute(item.key)
  addBusy.value = false
  if (!created) return
  addOpen.value = false
  selectedKey.value = created.key
}

onMounted(() => {
  void store.reloadRoutes()
})
</script>

<template>
  <div class="listik-routes-settings">
    <p class="listik-prose">
      Порядок и видимость записей — что автор видит и в каком порядке при выборе маршрута
      задачи. Заведение и удаление не трогают уже запущенные задачи: у них только снимается
      ссылка на маршрут.
    </p>

    <UiAlert
      v-if="store.routesSettingsError.value"
      tone="warning"
      closable
      @close="store.routesSettingsError.value = null"
    >
      <template #title>Не получилось</template>
      {{ store.routesSettingsError.value }}
    </UiAlert>

    <UiAlert v-if="removeResult" tone="success" closable @close="removeResult = null">
      маршрут удалён, у {{ removeResult.tasks_cleared }} задач снят маршрут
    </UiAlert>

    <div class="listik-routes-settings__layout">
      <div class="listik-routes-settings__list">
        <section class="listik-routes-settings__group">
          <h3 class="listik-section__title">Конвейеры</h3>
          <UiRecordList
            v-model="pipelineRows"
            :columns="columns"
            :row-key="(row) => row.key"
            :create-row="stubPipelineRow"
            :loading="store.routesSettingsLoading.value"
            add-label="Завести маршрут"
            empty-title="Конвейеров нет"
            @add="handleAddPipeline"
            @remove="handleRemovePipeline"
            @reorder="handleReorder"
          >
            <template #cell-route="{ row }">
              <button
                type="button"
                class="listik-routes-row"
                :class="{ 'is-selected': selectedKey === row.key }"
                @click="selectRoute(row)"
              >
                <RouteIcon :route="row" size="sm" />
                <span class="listik-routes-row__title">{{ row.title }}</span>
                <code class="listik-mono">{{ row.key }}</code>
                <UiBadge v-if="!row.visible" tone="neutral" size="sm">скрыт</UiBadge>
                <UiBadge
                  v-if="row.skill_missing"
                  tone="warning"
                  size="sm"
                  v-bind="{ title: `скила /feature-pipeline:${row.key} нет, маршрут скрыт от автора` }"
                >
                  нет скила
                </UiBadge>
              </button>
            </template>
            <template #row-actions="{ row }">
              <template v-if="row.skill_missing">
                <UiButton size="sm" variant="ghost" @click="hideRoute(row)">Скрыть</UiButton>
                <UiButton size="sm" variant="ghost" @click="askRemove(row)">Удалить</UiButton>
              </template>
            </template>
          </UiRecordList>
        </section>

        <section class="listik-routes-settings__group">
          <h3 class="listik-section__title">Прямая выдача</h3>
          <UiRecordList
            v-model="directRows"
            :columns="columns"
            :row-key="(row) => row.key"
            :create-row="stubDirectRow"
            :loading="store.routesSettingsLoading.value"
            add-label="Завести маршрут"
            empty-title="Прямых маршрутов нет"
            @add="handleAddDirect"
            @remove="handleRemoveDirect"
            @reorder="handleReorder"
          >
            <template #cell-route="{ row }">
              <button
                type="button"
                class="listik-routes-row"
                :class="{ 'is-selected': selectedKey === row.key }"
                @click="selectRoute(row)"
              >
                <RouteIcon :route="row" size="sm" />
                <span class="listik-routes-row__title">{{ row.title }}</span>
                <code class="listik-mono">{{ row.key }}</code>
                <UiBadge v-if="!row.visible" tone="neutral" size="sm">скрыт</UiBadge>
              </button>
            </template>
          </UiRecordList>
        </section>
      </div>

      <div class="listik-routes-settings__detail">
        <UiEmptyState v-if="!selected" compact title="Выбери маршрут слева" />
        <div v-else class="listik-routes-settings__card">
          <RouteCard :key="selected.key" :route="selected" />
        </div>
      </div>
    </div>

    <UiConfirmDialog
      :model-value="Boolean(removeTarget)"
      tone="danger"
      :title="`Удалить маршрут ${removeTarget?.title ?? ''}?`"
      description="Задачи с этим маршрутом останутся — у них будет снят маршрут."
      confirm-label="Удалить"
      cancel-label="Отмена"
      :loading="removeBusy"
      @update:model-value="(value: boolean) => { if (!value) removeTarget = null }"
      @confirm="confirmRemove"
      @cancel="cancelRemove"
    />

    <UiModal v-model="addOpen" title="Завести маршрут">
      <p v-if="!store.routesSync.value" class="listik-prose">Проверяю скилы…</p>
      <UiEmptyState
        v-else-if="!store.routesSync.value.skills_available"
        compact
        title="Каталог скилов недоступен"
        description="Установленная копия Listik без plugins/ — заводить нечего."
      />
      <UiEmptyState
        v-else-if="store.routesSync.value.missing_route.length === 0"
        compact
        title="Все скилы заведены"
      />
      <ul v-else class="listik-routes-missing">
        <li v-for="item in store.routesSync.value.missing_route" :key="item.key">
          <button
            type="button"
            class="listik-routes-missing__item"
            :disabled="addBusy"
            @click="pickMissingRoute(item)"
          >
            <span class="listik-routes-missing__title">{{ item.title }}</span>
            <code class="listik-mono">{{ item.key }}</code>
            <span class="listik-routes-missing__hint">{{ item.hint }}</span>
            <span class="listik-routes-missing__path">{{ item.skill_path }}</span>
          </button>
        </li>
      </ul>
      <template #footer>
        <UiButton variant="ghost" @click="addOpen = false">Закрыть</UiButton>
      </template>
    </UiModal>
  </div>
</template>

<style scoped>
.listik-routes-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.listik-routes-settings .listik-section__title {
  font-size: var(--text-md);
}

.listik-routes-settings__layout {
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(0, 2fr);
  gap: var(--space-4);
  align-items: start;
}

@media (max-width: 720px) {
  .listik-routes-settings__layout {
    grid-template-columns: 1fr;
  }
}

.listik-routes-settings__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

.listik-routes-settings__group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
}

.listik-routes-settings__detail {
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-4);
  min-width: 0;
}

.listik-routes-settings__card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.listik-routes-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-1) var(--space-2);
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.listik-routes-row:hover {
  background: var(--surface-2);
}

.listik-routes-row.is-selected {
  background: var(--accent-50);
}

.listik-routes-row__title {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.listik-routes-missing {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 360px;
  overflow-y: auto;
}

.listik-routes-missing__item {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  background: var(--surface);
  text-align: left;
  cursor: pointer;
}

.listik-routes-missing__item:hover:not(:disabled) {
  background: var(--surface-2);
}

.listik-routes-missing__item:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.listik-routes-missing__title {
  font-weight: var(--weight-medium);
}

.listik-routes-missing__hint,
.listik-routes-missing__path {
  color: var(--ink-3);
  font-size: var(--text-sm);
}

.listik-routes-missing__path {
  width: 100%;
  font-family: var(--font-mono);
}
</style>
