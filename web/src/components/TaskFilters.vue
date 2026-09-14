<script setup lang="ts">
/**
 * Фильтры (UiFilterBar + UiFilterField). Значения — из /api/meta (facets),
 * поиск по тексту/проекту и признакам идёт в запрос к API.
 * Счётчик активных фильтров и кнопку сброса рисует сам UiFilterBar — он для
 * этого и предназначен; контролы внутри слотов — UiSelect/UiMultiSelect.
 */
import { computed } from 'vue'
import {
  UiButton,
  UiCheckbox,
  UiDatePicker,
  UiFilterBar,
  UiFilterField,
  UiPopover,
  UiSegmented,
  UiSelect,
  type UiSegmentedOption,
  type UiSelectOption,
} from '@zoloto585/facet'
import type { FacetsOption } from '@/lib/facets'
import type { DepsFilter, Filters } from '@/store/listik'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HealthDot from './marks/HealthDot.vue'
import TaskGlyph from './marks/TaskGlyph.vue'
import type { TaskStage } from '@/api/types'
import { stageCode } from '@/lib/stages'
import { formatIsoDate, isoDateDaysAgo, parseIsoDate } from '@/lib/format'

const props = defineProps<{
  filters: Filters
  statusOptions: FacetsOption[]
  stageOptions: FacetsOption[]
  assigneeOptions: FacetsOption[]
  typeOptions: FacetsOption[]
}>()

const emit = defineEmits<{
  'update:filters': [patch: Partial<Filters>]
  reset: []
  apply: []
}>()

function toOptions(options: FacetsOption[]): UiSelectOption[] {
  return options.map((option) => ({ value: option.value, label: option.label }))
}

const statusOptions = computed(() => toOptions(props.statusOptions))
/** Этап — переключатель s1…s4 + «готово», как Тип и Здоровье; полное название — в title. */
const stageToggleOptions = computed<IconToggleOption<string>[]>(() => [
  { value: '', label: 'все этапы' },
  ...toOptions(props.stageOptions).map((option) => ({ value: String(option.value), label: option.label })),
])
const assigneeOptions = computed(() => toOptions(props.assigneeOptions))
const typeOptions = computed(() => toOptions(props.typeOptions))

/** «Любой тип» + facet-список — TaskGlyph уже знает иконку каждого известного
 * типа и честно падает на общую иконку задачи для незнакомых значений. */
const typeToggleOptions = computed<IconToggleOption<string>[]>(() => [
  { value: '', label: 'любой тип' },
  ...typeOptions.value,
])

/** Здоровье по heartbeat/держателю — тот же клиентский признак, что чипы тулбара. */
const healthOptions: IconToggleOption<string>[] = [
  { value: '', label: 'все' },
  { value: 'dead', label: 'брошены' },
  { value: 'at-risk', label: 'под угрозой' },
]

/** Слой зависимостей: данные приходят из /api/ready и /api/blocked. */
const depsOptions: IconToggleOption<string>[] = [
  { value: 'all', label: 'все' },
  { value: 'ready', label: 'можно брать' },
  { value: 'blocked', label: 'заблокированные' },
]

const updateOptions: UiSegmentedOption[] = [
  { value: 'all', label: 'всё время' },
  { value: 'day', label: 'сутки' },
  { value: 'week', label: '7 дн' },
  { value: 'month', label: '30 дн' },
]

function patch(next: Partial<Filters>): void {
  emit('update:filters', next)
}

const updateQuick = computed<string>({
  get() {
    if (!props.filters.updatedFrom) return 'all'
    const from = Date.parse(`${props.filters.updatedFrom}T00:00:00`)
    const days = Math.round((Date.now() - from) / 86400000)
    if (days <= 1) return 'day'
    if (days <= 8) return 'week'
    if (days <= 31) return 'month'
    return 'all'
  },
  set(value: string) {
    if (value === 'all') patch({ updatedFrom: '', updatedTo: '' })
    else if (value === 'day') patch({ updatedFrom: isoDateDaysAgo(1) })
    else if (value === 'week') patch({ updatedFrom: isoDateDaysAgo(7) })
    else patch({ updatedFrom: isoDateDaysAgo(30) })
  },
})

/** Диапазон задан руками, а не быстрой кнопкой — подсвечиваем календарь. */
const isCustomRange = computed(() => Boolean(props.filters.updatedTo) || (Boolean(props.filters.updatedFrom) && updateQuick.value === 'all'))

const activeCount = computed(() => {
  const f = props.filters
  let count = 0
  if (f.status) count += 1
  if (f.stage) count += 1
  if (f.assignee) count += 1
  if (f.type) count += 1
  if (f.needsOwner) count += 1
  if (f.health) count += 1
  if (f.updatedFrom || f.updatedTo) count += 1
  if (f.deps !== 'all') count += 1
  return count
})
</script>

<template>
  <UiFilterBar class="listik-filters" :active-count="activeCount" reset-label="Сбросить фильтры" @reset="emit('reset')">
    <!-- Статус и Этап — строки от сервера без готового словаря иконок, поэтому
         остаются выпадающими списками, а не IconToggle, в отличие от полей ниже. -->
    <UiFilterField label="Статус" :active="Boolean(filters.status)" @clear="patch({ status: '' })">
      <template #default="{ fieldId }">
        <UiSelect
          :model-value="filters.status || null"
          :options="statusOptions"
          v-bind="{ 'aria-labelledby': fieldId }"
          size="sm"
          placeholder="все статусы"
          @update:model-value="(value) => patch({ status: value ? String(value) : '' })"
        />
      </template>
    </UiFilterField>

    <UiFilterField label="Этап" :active="Boolean(filters.stage)">
      <IconToggle
        :model-value="filters.stage || ''"
        :options="stageToggleOptions"
        ariaLabel="Фильтр по этапу"
        size="sm"
        @update:model-value="(value) => patch({ stage: value })"
      >
        <template #icon="{ option }">
          <ListikIcon v-if="!option.value" name="check" size="xs" />
          <span v-else class="listik-filters__stage">{{ stageCode(option.value as TaskStage) ?? option.label.toLowerCase() }}</span>
        </template>
      </IconToggle>
    </UiFilterField>

    <UiFilterField label="Исполнитель" :active="Boolean(filters.assignee)" @clear="patch({ assignee: '' })">
      <template #default="{ fieldId }">
        <UiSelect
          :model-value="filters.assignee || null"
          :options="assigneeOptions"
          v-bind="{ 'aria-labelledby': fieldId }"
          size="sm"
          placeholder="любой"
          @update:model-value="(value) => patch({ assignee: value ? String(value) : '' })"
        />
      </template>
    </UiFilterField>

    <UiFilterField label="Тип" :active="Boolean(filters.type)" @clear="patch({ type: '' })">
      <IconToggle
        :model-value="filters.type || ''"
        :options="typeToggleOptions"
        ariaLabel="Фильтр по типу"
        size="sm"
        @update:model-value="(value) => patch({ type: value })"
      >
        <template #icon="{ option }">
          <ListikIcon v-if="!option.value" name="check" size="xs" />
          <TaskGlyph v-else kind="type" :value="option.value" />
        </template>
      </IconToggle>
    </UiFilterField>

    <UiFilterField label="Здоровье" :active="Boolean(filters.health)">
      <IconToggle
        :model-value="filters.health"
        :options="healthOptions"
        ariaLabel="Фильтр по здоровью"
        size="sm"
        @update:model-value="(value) => patch({ health: value as Filters['health'] })"
      >
        <template #icon="{ option }">
          <ListikIcon v-if="!option.value" name="check" size="xs" />
          <HealthDot v-else :health="option.value as 'dead' | 'at-risk'" size="sm" :label="option.label" />
        </template>
      </IconToggle>
    </UiFilterField>

    <UiFilterField label="Зависимости" :active="filters.deps !== 'all'">
      <IconToggle
        :model-value="filters.deps"
        :options="depsOptions"
        ariaLabel="Фильтр по зависимостям"
        size="sm"
        @update:model-value="(value) => patch({ deps: value as DepsFilter })"
      >
        <template #icon="{ option }">
          <ListikIcon
            :name="option.value === 'blocked' ? 'lock' : option.value === 'ready' ? 'key' : 'check'"
            size="xs"
          />
        </template>
      </IconToggle>
    </UiFilterField>

    <UiFilterField label="Обновлена" :active="Boolean(filters.updatedFrom || filters.updatedTo)">
      <div class="listik-row listik-filters__updated">
        <UiSegmented v-model="updateQuick" :options="updateOptions" size="sm" label="Диапазон обновления" />
        <!-- Точный диапазон «с/по» — редкий случай, поэтому не два поля в панели, а поповер. -->
        <UiPopover label="Точный диапазон обновления" placement="bottom">
          <template #trigger>
            <UiButton
              size="sm"
              :variant="isCustomRange ? 'secondary' : 'ghost'"
              ariaLabel="Точный диапазон"
              v-bind="{ title: 'Точный диапазон' }"
            >
              <template #icon><ListikIcon name="calendar" size="xs" /></template>
            </UiButton>
          </template>
          <div class="listik-filters__range">
            <UiFilterField label="С" :active="Boolean(filters.updatedFrom)" @clear="patch({ updatedFrom: '' })">
              <UiDatePicker
                :model-value="parseIsoDate(filters.updatedFrom)"
                size="sm"
                v-bind="{ 'aria-label': 'Обновлена с' }"
                @update:model-value="(value) => patch({ updatedFrom: formatIsoDate(value) })"
              />
            </UiFilterField>
            <UiFilterField label="По" :active="Boolean(filters.updatedTo)" @clear="patch({ updatedTo: '' })">
              <UiDatePicker
                :model-value="parseIsoDate(filters.updatedTo)"
                size="sm"
                v-bind="{ 'aria-label': 'Обновлена по' }"
                @update:model-value="(value) => patch({ updatedTo: formatIsoDate(value) })"
              />
            </UiFilterField>
          </div>
        </UiPopover>
      </div>
    </UiFilterField>

    <UiFilterField>
      <div class="listik-row listik-filters__special">
        <label class="listik-row">
          <UiCheckbox
            :model-value="filters.needsOwner"
            @update:model-value="(value) => patch({ needsOwner: Boolean(value) })"
          />
          <span class="text-caption">нужен ты</span>
        </label>
      </div>
    </UiFilterField>

    <template #actions>
      <slot name="actions" />
    </template>
  </UiFilterBar>
</template>
