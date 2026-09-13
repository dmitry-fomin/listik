<script setup lang="ts">
/**
 * Вид «Список»: UiDataTable с настраиваемыми колонками (UiColumnSetting),
 * сортировкой, пагинацией (UiPaginator) и массовой правкой (UiBulkEditModal).
 * Таблица — только чтение, поэтому именно UiDataTable, а не UiRecordList.
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiBulkEditModal,
  UiCheckbox,
  UiColumnSetting,
  UiDataTable,
  UiPaginator,
  UiTooltip,
  type UiBulkEditModalField,
  type UiColumnSettingOption,
  type UiDataTableColumn,
  type UiDataTableSort,
  type UiFieldFormField,
} from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import TaskFilters from '@/components/TaskFilters.vue'
import TaskGlyph from '@/components/marks/TaskGlyph.vue'
import store, { type Filters } from '@/store/listik'
import type { Task, TaskPatch } from '@/api/types'
import { formatDateTime, humanAge } from '@/lib/format'
import { assigneeOptions, stageOptions, statusOptions, typeOptions } from '@/components/facets'

const COLUMNS_KEY = 'listik.columns'

interface ColumnDef {
  key: string
  label: string
  width?: string
  sortable?: boolean
  align?: 'start' | 'center' | 'end'
}

const COLUMN_CATALOG: ColumnDef[] = [
  { key: 'id', label: 'ID', width: '180px', sortable: true },
  { key: 'title', label: 'Заголовок', sortable: true },
  { key: 'project', label: 'Проект', width: '150px', sortable: true },
  { key: 'status_title', label: 'Статус', width: '120px', sortable: true },
  { key: 'stage_title', label: 'Этап', width: '140px' },
  { key: 'priority_title', label: 'Приоритет', width: '110px', sortable: true },
  { key: 'issue_type', label: 'Тип', width: '100px' },
  { key: 'assignee_title', label: 'Исполнитель', width: '140px' },
  { key: 'holder_title', label: 'Держит', width: '140px' },
  { key: 'holder_age', label: 'Держит, время', width: '120px', sortable: true },
  { key: 'stage_age', label: 'На этапе', width: '120px', sortable: true },
  { key: 'updated_age', label: 'Обновлена', width: '120px', sortable: true },
  { key: 'blocked_count', label: 'Ждёт', width: '90px', sortable: true },
  { key: 'waiting_count', label: 'Её ждут', width: '100px', sortable: true },
  { key: 'labels', label: 'Метки', width: '180px' },
]

const DEFAULT_ORDER = COLUMN_CATALOG.map((column) => column.key)

function defaultSettings(): UiColumnSettingOption[] {
  return COLUMN_CATALOG.map((column, index) => ({
    key: column.key,
    label: column.label,
    visible: index < 11,
    order: index,
  }))
}

function readSettings(): UiColumnSettingOption[] {
  try {
    const raw = window.localStorage.getItem(COLUMNS_KEY)
    if (!raw) return defaultSettings()
    const parsed = JSON.parse(raw) as UiColumnSettingOption[]
    const known = new Set(DEFAULT_ORDER)
    const filtered = parsed.filter((option) => known.has(option.key))
    // новые колонки, появившиеся после сохранения настроек, добавляем скрытыми
    const missing = DEFAULT_ORDER.filter((key) => !filtered.some((option) => option.key === key))
    return [
      ...filtered,
      ...missing.map((key, index) => ({
        key,
        label: COLUMN_CATALOG.find((column) => column.key === key)?.label ?? key,
        visible: false,
        order: filtered.length + index,
      })),
    ]
  } catch {
    return defaultSettings()
  }
}

const settings = ref<UiColumnSettingOption[]>(readSettings())
const page = ref(1)
const pageSize = ref(25)
const rows = ref<Task[]>([])
const total = ref(0)
const loadingRows = ref(false)
const sort = ref<UiDataTableSort | null>({ key: 'updated_at', direction: 'desc' })
const selected = ref<string[]>([])
const bulkOpen = ref(false)
const bulkError = ref<string | null>(null)

watch(
  settings,
  (value) => {
    try {
      window.localStorage.setItem(COLUMNS_KEY, JSON.stringify(value))
    } catch {
      /* приватный режим */
    }
  },
  { deep: true },
)

const visibleColumns = computed<UiDataTableColumn[]>(() =>
  settings.value
    .slice()
    .sort((a, b) => a.order - b.order)
    .filter((option) => option.visible)
    .map((option) => {
      const definition = COLUMN_CATALOG.find((column) => column.key === option.key)
      return {
        key: option.key,
        label: option.label,
        width: definition?.width,
        sortable: definition?.sortable,
        align: definition?.align,
      }
    }),
)

/** Выбор строки — отдельная колонка чекбоксов, её в каталоге настройки нет. */
const tableColumns = computed<UiDataTableColumn[]>(() => [
  { key: 'select', label: '', width: '40px' },
  ...visibleColumns.value,
])

/**
 * Числа зависимостей по id задачи: колонки «ждёт» / «её ждут» берут их отсюда.
 * Отдельным словарём, а не полями строки: `UiDataTable` — generic по типу строки,
 * и его слоты ячеек типизированы `Task`, так что поля-надстройки всё равно
 * пришлось бы кастовать.
 */
interface DepsCell {
  blocked: number
  waiting: number
  stale: boolean
}

const depsCells = computed<Record<string, DepsCell>>(() => {
  const out: Record<string, DepsCell> = {}
  for (const task of rows.value) {
    const summary = store.depsFor(task.id)
    out[task.id] = {
      blocked: summary?.blockedBy.length ?? task.blocked_by.length,
      waiting: summary?.waitingForCount ?? 0,
      stale: summary?.blockedByStale ?? false,
    }
  }
  return out
})

function depsCell(id: string): DepsCell {
  return depsCells.value[id] ?? { blocked: 0, waiting: 0, stale: false }
}

const filteredRows = computed<Task[]>(() =>
  rows.value
    .filter((row) => {
      const mode = store.filters.deps
      if (mode === 'blocked' && depsCell(row.id).blocked === 0) return false
      if (mode === 'ready' && !store.readyTasks.value.some((item) => item.id === row.id)) return false
      return true
    }),
)

const sortedRows = computed<Task[]>(() => {
  const active = sort.value
  if (!active) return filteredRows.value
  const direction = active.direction === 'asc' ? 1 : -1
  return filteredRows.value.slice().sort((a, b) => {
    const left = a[active.key as keyof Task]
    const right = b[active.key as keyof Task]
    if (typeof left === 'number' && typeof right === 'number') return (left - right) * direction
    return String(left ?? '').localeCompare(String(right ?? ''), 'ru') * direction
  })
})

const allSelected = computed(
  () => sortedRows.value.length > 0 && selected.value.length === sortedRows.value.length,
)

async function load(): Promise<void> {
  loadingRows.value = true
  const orderMap: Record<string, 'updated' | 'created' | 'priority' | 'stage'> = {
    updated_age: 'updated',
    stage_age: 'stage',
    priority_title: 'priority',
    id: 'created',
  }
  const result = await store.loadListTasks({
    limit: pageSize.value,
    offset: (page.value - 1) * pageSize.value,
    order: (sort.value && orderMap[sort.value.key]) || 'updated',
  })
  rows.value = result.tasks
  total.value = result.total
  loadingRows.value = false
}

onMounted(() => {
  void load()
})

watch([page, pageSize], () => {
  selected.value = []
  void load()
})

watch(
  () => store.filters,
  () => {
    page.value = 1
    void load()
  },
  { deep: true },
)

function toggleAll(value: boolean): void {
  selected.value = value ? sortedRows.value.map((row) => row.id) : []
}

const filtersModel = computed<Filters>(() => ({ ...store.filters }))

function patchFilters(patch: Partial<Filters>): void {
  Object.assign(store.filters, patch)
  selected.value = []
  void store.applyFilters()
}

function openRow(id: string): void {
  void store.openTask(id)
}

function toggleRow(id: string, value: boolean): void {
  if (value) selected.value = [...selected.value, id]
  else selected.value = selected.value.filter((item) => item !== id)
}

const bulkFields = computed<UiBulkEditModalField[]>(() => {
  const statuses: UiFieldFormField = {
    key: 'status',
    label: 'Статус',
    type: 'select',
    options: statusOptions().map((option) => ({ value: option.value, label: option.label })),
  }
  const assignees: UiFieldFormField = {
    key: 'assignee',
    label: 'Исполнитель',
    type: 'select',
    options: assigneeOptions(store.meta.value).map((option) => ({ value: option.value, label: option.label })),
  }
  const labels: UiFieldFormField = {
    key: 'labels',
    label: 'Метки (через запятую)',
    type: 'text',
    hint: 'пустое значение очистит список меток',
  }
  const holder: UiFieldFormField = { key: 'holder', label: 'Держатель', type: 'text' }
  return [statuses, assignees, labels, holder]
})

async function submitBulk(changes: Record<string, unknown>): Promise<void> {
  bulkError.value = null
  const patch: TaskPatch = {}
  if (typeof changes.status === 'string' && changes.status) patch.status = changes.status as Task['status']
  if (typeof changes.assignee === 'string' && changes.assignee) patch.assignee = changes.assignee
  if (typeof changes.holder === 'string') patch.holder = changes.holder
  if (typeof changes.labels === 'string') {
    patch.labels = changes.labels
      .split(',')
      .map((label) => label.trim())
      .filter(Boolean)
  }
  if (Object.keys(patch).length === 0) {
    bulkError.value = 'Не отмечено ни одного поля для изменения'
    return
  }
  await store.bulkPatch(selected.value, patch)
  bulkOpen.value = false
  await load()
}

function setSort(value: UiDataTableSort | null): void {
  sort.value = value
  if (value) void load()
}

defineExpose({ reload: load })
</script>

<template>
  <section class="listik-section" aria-label="Список задач">
    <TaskFilters
      :filters="filtersModel"
      :status-options="statusOptions()"
      :stage-options="stageOptions()"
      :assignee-options="assigneeOptions(store.meta.value)"
      :type-options="typeOptions()"
      @update:filters="patchFilters"
      @reset="store.clearFilters()"
    />

    <div class="listik-section__head">
      <div class="listik-row">
        <span class="listik-section__hint">
          выбрано {{ selected.length }} из {{ total }}
          <template v-if="store.filters.deps === 'ready'">· готовых к работе: {{ sortedRows.length }}</template>
          <template v-else-if="store.filters.deps === 'blocked'">· блокированных: {{ sortedRows.length }}</template>
        </span>
        <UiButton size="sm" variant="secondary" :disabled="selected.length === 0" @click="bulkOpen = true">
          <template #icon><ListikIcon name="list" size="xs" /></template>
          Массовая правка
        </UiButton>
      </div>
      <div class="listik-row">
        <label class="listik-row">
          <UiCheckbox :model-value="allSelected" @update:model-value="toggleAll" />
          <span class="text-caption">выбрать страницу</span>
        </label>
        <UiColumnSetting v-model="settings" trigger-label="Колонки" @reset="settings = defaultSettings()">
          <template #icon><ListikIcon name="columns" size="xs" /></template>
        </UiColumnSetting>
      </div>
    </div>

    <UiAlert v-if="bulkError" tone="danger" closable @close="bulkError = null">
      <template #title>Массовая правка не применена</template>
      {{ bulkError }}
    </UiAlert>

    <UiDataTable
      :columns="tableColumns"
      :rows="sortedRows"
      :loading="loadingRows"
      :sort="sort"
      :row-key="(row: Task) => row.id"
      density="compact"
      empty-title="Задач не найдено"
      empty-description="Измените фильтры или снимите «нужен ты» / «только брошенные»."
      @sort="setSort"
    >
      <template #cell-select="{ row }">
        <UiCheckbox
          :model-value="selected.includes(row.id)"
          @update:model-value="(value) => toggleRow(row.id, Boolean(value))"
        />
      </template>
      <template #cell-id="{ row }">
        <button type="button" class="listik-link" @click="openRow(row.id)">
          <span class="listik-mono">{{ row.id }}</span>
        </button>
      </template>
      <template #cell-title="{ row }">
        <span>{{ row.title }}</span>
        <span v-if="row.needs_owner" class="listik-cell-flag is-accent">нужен ты</span>
        <span v-if="row.abandoned" class="listik-cell-flag is-danger">брошена</span>
        <span v-else-if="row.stale" class="listik-cell-flag is-warning">stale</span>
      </template>
      <template #cell-stage_age="{ row }">
        <span :class="{ 'is-warn': row.stage_warn }">{{ row.stage_age }}</span>
      </template>
      <template #cell-blocked_count="{ row }">
        <UiTooltip
          v-if="depsCell(row.id).blocked > 0"
          :text="`ждёт ${depsCell(row.id).blocked}: ${row.blocked_by.join(', ')}`"
        >
          <UiBadge :tone="depsCell(row.id).stale ? 'danger' : 'warning'" size="sm">
            ждёт {{ depsCell(row.id).blocked }}
          </UiBadge>
        </UiTooltip>
        <span v-else class="listik-mono">—</span>
      </template>
      <template #cell-waiting_count="{ row }">
        <UiTooltip
          v-if="depsCell(row.id).waiting > 0"
          text="пока эта задача не закрыта, эти задачи стоят"
        >
          <UiBadge tone="accent" size="sm">её ждут {{ depsCell(row.id).waiting }}</UiBadge>
        </UiTooltip>
        <span v-else class="listik-mono">—</span>
      </template>
      <template #cell-updated_age="{ row }">
        <span :title="formatDateTime(row.updated_at)">{{ humanAge(row.updated_at) }}</span>
      </template>
      <template #cell-holder_title="{ row }">{{ row.holder_title || '—' }}</template>
      <template #cell-labels="{ row }">
        <span class="listik-mono">{{ row.labels.join(', ') || '—' }}</span>
      </template>
      <template #cell-issue_type="{ row }">
        <TaskGlyph kind="type" :value="row.issue_type" />
      </template>
      <template #cell-priority_title="{ row }">
        <TaskGlyph kind="priority" :value="row.priority" />
      </template>
    </UiDataTable>

    <div class="listik-row" style="justify-content: space-between">
      <UiPaginator v-model="page" :total="total" :page-size="pageSize" size="sm" />
      <label class="listik-row">
        <span class="text-caption">на странице</span>
        <UiButton
          v-for="size in [25, 50, 100]"
          :key="size"
          size="sm"
          :variant="pageSize === size ? 'primary' : 'ghost'"
          @click="pageSize = size"
        >
          {{ size }}
        </UiButton>
      </label>
    </div>

    <UiBulkEditModal
      v-model="bulkOpen"
      title="Массовая правка задач"
      :fields="bulkFields"
      :selected-count="selected.length"
      :loading="store.pending.value === 'bulk'"
      :errors="{ status: bulkError }"
      submit-label="Применить"
      @submit="submitBulk"
    />
  </section>
</template>

<style scoped>
.listik-cell-flag {
  margin-left: var(--space-2);
  font-size: var(--text-xs);
}

.listik-cell-flag.is-accent {
  color: var(--accent-700);
}

.listik-cell-flag.is-danger {
  color: var(--danger-700);
}

.listik-cell-flag.is-warning {
  color: var(--warning-700);
}

.is-warn {
  color: var(--warning-700);
}
</style>
