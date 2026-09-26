<script setup lang="ts">
/**
 * RoutePicker — визуальный выбор маршрута двумя группами (макет
 * `docs/prototype/NewTaskRoutes-html/NewTaskRoutes.dc.html`, listik-2gry):
 * «Конвейеры» (таблица пресетов: иконка уровня + роли) и «Рой» (строки с
 * цепочкой харнессов этапов, пропущенный этап — пунктир); под ними — пункт
 * «без маршрута» и осиротевший ключ. Заведение нового маршрута отсюда
 * убрано — оно живёт только в настройках «Маршруты».
 *
 * Тот же контрол в «Новой задаче» и в панели заведённой карточки: кит такого
 * поля не знает, записи приходят с `GET /api/routes`. Клик эмитит ключ —
 * черновик или сразу PATCH решает родитель. Пункт «без маршрута»
 * (`allowClear`) шлёт пустую строку — снять `launch_route`.
 */
import { computed } from 'vue'
import ListikIcon from '@/components/ListikIcon.vue'
import HarnessIcon from '@/components/marks/HarnessIcon.vue'
import ProviderIcon from '@/components/marks/ProviderIcon.vue'
import RouteIcon from '@/components/marks/RouteIcon.vue'
import type { RouteDef } from '@/api/types'
import { harnessTitle } from '@/lib/harness'
import { isSwarmCell, ROLE_KEYS, ROLE_TITLES } from '@/lib/pipelines'
import {
  NO_ROUTE,
  pickerRoutesOf,
  pipelineRowsOf,
  routeAllowedForType,
  routeByKey,
  swarmRoutesOf,
} from '@/lib/routes'

const props = withDefaults(
  defineProps<{
    /** Полный список `GET /api/routes` — скрытые отфильтрует сам контрол. */
    routes: RouteDef[]
    /** Ключ выбранной записи; `null`/пустая строка — маршрут не выбран. */
    selectedKey: string | null
    /** Тип задачи: эпику закрыты маршруты без этапа ТЗ. */
    issueType: string
    disabled?: boolean
    /** Пункт «без маршрута» — снять `launch_route` (только у заведённой задачи). */
    allowClear?: boolean
  }>(),
  {
    disabled: false,
    allowClear: false,
  },
)

const emit = defineEmits<{
  /** Ключ записи или пустая строка («без маршрута»). */
  select: [key: string]
}>()

const shownRoutes = computed(() => pickerRoutesOf(props.routes, props.selectedKey))

const pipelineRows = computed(() => pipelineRowsOf(shownRoutes.value))
const swarmRoutes = computed(() => swarmRoutesOf(shownRoutes.value))

/**
 * Текущий ключ есть на карточке, а записи в `routes.json` уже нет — рисуем
 * отдельную отключённую кнопку, чтобы выбор не выглядел пустым.
 */
const orphanKey = computed(() => {
  if (!props.selectedKey) return null
  return routeByKey(props.selectedKey, props.routes) ? null : props.selectedKey
})

function routeAllowed(route: RouteDef): boolean {
  return routeAllowedForType(route, props.issueType)
}

function isOn(key: string): boolean {
  return (props.selectedKey ?? NO_ROUTE) === key
}

type PickerItem = { key: string; route: RouteDef | null }

const pickerItems = computed<PickerItem[]>(() => {
  const items: PickerItem[] = [
    ...pipelineRows.value.map((route) => ({ key: route.key, route })),
    ...swarmRoutes.value.map((route) => ({ key: route.key, route })),
  ]
  if (props.allowClear) items.push({ key: NO_ROUTE, route: null })
  if (orphanKey.value) items.push({ key: orphanKey.value, route: null })
  return items
})

function itemAllowed(item: PickerItem): boolean {
  if (item.key === NO_ROUTE) return true
  if (item.key === orphanKey.value) return false
  return item.route ? routeAllowed(item.route) : false
}

function selectItem(item: PickerItem): void {
  if (props.disabled || !itemAllowed(item)) return
  emit('select', item.key)
}

function selectRoute(route: RouteDef): void {
  selectItem({ key: route.key, route })
}

function selectClear(): void {
  selectItem({ key: NO_ROUTE, route: null })
}

function routeTooltip(route: RouteDef): string {
  if (routeAllowed(route)) return route.hint
  return 'Эпик всегда режется на шаги через ТЗ (s1) — пресеты без этапа ТЗ для эпиков закрыты.'
}

function routeTabindex(key: string): number {
  const item = pickerItems.value.find((entry) => entry.key === key)
  if (!item || !itemAllowed(item)) return -1
  if (isOn(key)) return 0
  const hasSelected = pickerItems.value.some((entry) => itemAllowed(entry) && isOn(entry.key))
  if (hasSelected) return -1
  const firstAllowed = pickerItems.value.find((entry) => itemAllowed(entry))
  return firstAllowed?.key === key ? 0 : -1
}

const routeRefs = new Map<string, HTMLButtonElement>()

function setRouteRef(key: string, el: unknown): void {
  const button = el instanceof HTMLButtonElement ? el : null
  if (button) routeRefs.set(key, button)
  else routeRefs.delete(key)
}

function onRouteKeydown(event: KeyboardEvent, key: string): void {
  const allowed = pickerItems.value.filter((item) => itemAllowed(item))
  const at = allowed.findIndex((item) => item.key === key)
  if (at === -1) return
  let target = -1
  if (event.key === 'ArrowRight' || event.key === 'ArrowDown') target = (at + 1) % allowed.length
  else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
    target = (at - 1 + allowed.length) % allowed.length
  } else if (event.key === 'Home') target = 0
  else if (event.key === 'End') target = allowed.length - 1
  else return
  event.preventDefault()
  const next = allowed[target]
  if (!next) return
  selectItem(next)
  routeRefs.get(next.key)?.focus()
}
</script>

<template>
  <div
    class="listik-route-picker"
    role="radiogroup"
    aria-label="Маршрут запуска"
    :aria-disabled="disabled || undefined"
    :aria-busy="disabled || undefined"
  >
    <!-- Группа «Конвейеры»: этапы ведёт claude внутри одного процесса;
         конвейер приходит из plugins/ сам, из доски его не завести. -->
    <div class="listik-rgroup">
      <div class="listik-rgroup__head">
        <span class="listik-rgroup__title">Конвейеры</span>
        <span class="listik-rgroup__hint">этапы внутри одного процесса</span>
        <span class="listik-rgroup__spacer" aria-hidden="true" />
      </div>

      <div class="listik-pipelines">
        <div class="listik-pipelines__header">
          <span class="listik-pipelines__header-spacer" aria-hidden="true" />
          <span v-for="role in ROLE_KEYS" :key="role" class="listik-pipelines__col-title">
            {{ ROLE_TITLES[role] }}
          </span>
        </div>

        <button
          v-for="route in pipelineRows"
          :key="route.key"
          :ref="(el) => setRouteRef(route.key, el)"
          type="button"
          role="radio"
          class="listik-pipelines__row"
          :class="{ 'is-on': isOn(route.key), 'is-off': !routeAllowed(route) }"
          :data-route-key="route.key"
          :aria-checked="isOn(route.key)"
          :aria-disabled="!routeAllowed(route) || undefined"
          :disabled="disabled || !routeAllowed(route)"
          :tabindex="routeTabindex(route.key)"
          :title="routeTooltip(route)"
          @click="selectRoute(route)"
          @keydown="onRouteKeydown($event, route.key)"
        >
          <span class="listik-pipelines__row-title">
            <RouteIcon :route="route" size="sm" />
            <span class="listik-pipelines__row-text">
              <span class="listik-pipelines__row-name">{{ route.title }}</span>
              <span class="listik-pipelines__row-hint">{{ route.hint }}</span>
            </span>
          </span>

          <span v-for="role in ROLE_KEYS" :key="role" class="listik-pipelines__cell">
            <template v-if="route.roles[role]">
              <!-- Ячейка роевой формы (`driver=swarm` у pipeline) рисуется
                   глифом харнесса — у неё нет `provider`. -->
              <template v-if="isSwarmCell(route.roles[role])">
                <HarnessIcon :harness="route.roles[role]!.harness" size="sm" />
                <span class="listik-pipelines__cell-label">{{ harnessTitle(route.roles[role]!.harness) }}</span>
              </template>
              <template v-else>
                <ProviderIcon :provider="route.roles[role]!.provider" size="sm" />
                <span class="listik-pipelines__cell-label">{{ route.roles[role]!.label }}</span>
              </template>
            </template>
            <span v-else class="listik-pipelines__cell-empty" aria-hidden="true">—</span>
          </span>
        </button>
      </div>
    </div>

    <!-- Группа «Рой»: Listik водит карточку по этапам, на каждый — свой
         харнесс; пропущенный этап в цепочке — пунктирная черта. -->
    <div class="listik-rgroup">
      <div class="listik-rgroup__head">
        <span class="listik-rgroup__title">Рой</span>
        <span class="listik-rgroup__hint">Listik водит карточку по этапам — на каждый отдельный харнесс</span>
        <span class="listik-rgroup__spacer" aria-hidden="true" />
      </div>

      <div class="listik-pipelines">
        <button
          v-for="route in swarmRoutes"
          :key="route.key"
          :ref="(el) => setRouteRef(route.key, el)"
          type="button"
          role="radio"
          class="listik-pipelines__row"
          :class="{ 'is-on': isOn(route.key), 'is-off': !routeAllowed(route) }"
          :data-route-key="route.key"
          :aria-checked="isOn(route.key)"
          :aria-disabled="!routeAllowed(route) || undefined"
          :disabled="disabled || !routeAllowed(route)"
          :tabindex="routeTabindex(route.key)"
          :title="routeTooltip(route)"
          @click="selectRoute(route)"
          @keydown="onRouteKeydown($event, route.key)"
        >
          <span class="listik-pipelines__row-title">
            <RouteIcon :route="route" size="sm" />
            <span class="listik-pipelines__row-text">
              <span class="listik-pipelines__row-name">{{ route.title }}</span>
              <span class="listik-pipelines__row-hint">{{ route.hint }}</span>
            </span>
          </span>

          <span v-for="role in ROLE_KEYS" :key="role" class="listik-pipelines__cell">
            <template v-if="route.roles[role]">
              <!-- Ячейка роевой формы (`driver=swarm` у pipeline) рисуется
                   глифом харнесса — у неё нет `provider`. -->
              <template v-if="isSwarmCell(route.roles[role])">
                <HarnessIcon :harness="route.roles[role]!.harness" size="sm" />
                <span class="listik-pipelines__cell-label">{{ harnessTitle(route.roles[role]!.harness) }}</span>
              </template>
              <template v-else>
                <ProviderIcon :provider="route.roles[role]!.provider" size="sm" />
                <span class="listik-pipelines__cell-label">{{ route.roles[role]!.label }}</span>
              </template>
            </template>
            <span v-else class="listik-pipelines__cell-empty" aria-hidden="true">—</span>
          </span>
        </button>
      </div>
    </div>

    <!-- Пункт «без маршрута» и осиротевший ключ выбранной записи — отдельным
         блоком под группами, без заголовка. -->
    <div v-if="allowClear || orphanKey" class="listik-rgroup">
      <div class="listik-direct__items">
        <button
          v-if="allowClear"
          :ref="(el) => setRouteRef(NO_ROUTE, el)"
          type="button"
          role="radio"
          class="listik-direct__item"
          :class="{ 'is-on': isOn(NO_ROUTE) }"
          data-route-clear
          :data-route-key="NO_ROUTE"
          :aria-checked="isOn(NO_ROUTE)"
          :disabled="disabled"
          :tabindex="routeTabindex(NO_ROUTE)"
          title="снять маршрут — задача останется без типа запуска"
          @click="selectClear"
          @keydown="onRouteKeydown($event, NO_ROUTE)"
        >
          <ListikIcon name="close" size="sm" />
          без маршрута
        </button>

        <button
          v-if="orphanKey"
          :ref="(el) => orphanKey && setRouteRef(orphanKey, el)"
          type="button"
          role="radio"
          class="listik-direct__item is-on is-off"
          :data-route-key="orphanKey"
          :aria-checked="true"
          disabled
          :tabindex="-1"
          :title="orphanKey"
        >
          {{ orphanKey }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* Группы маршрутов: заголовок + ряды (макет `.rgroup` из NewTaskRoutes). */
.listik-rgroup {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.listik-rgroup + .listik-rgroup {
  margin-top: var(--space-3);
}

.listik-rgroup__head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.listik-rgroup__title {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-3);
}

.listik-rgroup__hint {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-rgroup__spacer {
  flex: 1 1 auto;
}
</style>
