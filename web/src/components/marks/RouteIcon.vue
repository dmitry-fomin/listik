<script setup lang="ts">
/**
 * RouteIcon — иконка уровня маршрута разработки (xhigh/high/medium/low/xlow/direct):
 * выбор маршрута в `RoutePicker` и карточка заведённой задачи. Уровень и
 * подпись берутся из справочника `lib/dictionaries.ts`, путь рисует `ListikIcon`
 * из общего словаря `icons.ts` (кит icon-agnostic).
 *
 * Уровень считает сервер (`GET /api/routes`, поле `icon` с фолбэком по ключу) —
 * компонент получает готовую запись маршрута. Записи нет или у неё нет уровня
 * (`opus-pipeline` и подобные) — не рисуем ничего. Если сервер не принял
 * явный `icon` записи и уровня в ключе не нашлось (`icon: null` + `icon_error`),
 * рисуем серый кружок с крестиком: так видно, что иконка недоступна из-за
 * опечатки в `routes.json`, а не просто не задана (`ROUTE_ICON_UNKNOWN`).
 *
 * Подсказка — нативным `title`, а не `UiTooltip`: иконка стоит и внутри
 * строки-кнопки выбора маршрута, у которой уже есть свой `title`, — второй слой
 * подсказок всплывал бы одновременно с ним.
 */
import { computed } from 'vue'
import ListikIcon from '@/components/ListikIcon.vue'
import { ROUTE_ICON_UNKNOWN, routeIcon } from '@/lib/dictionaries'
import type { RouteDef } from '@/api/types'

const props = withDefaults(
  defineProps<{
    /** Запись маршрута (`GET /api/routes`) или `null`, если её нет в списке. */
    route?: RouteDef | null
    size?: 'xs' | 'sm' | 'md'
    /** Текст подсказки; по умолчанию — расшифровка уровня из справочника. */
    title?: string
  }>(),
  { route: null, size: 'sm', title: undefined },
)

const item = computed(() => routeIcon(props.route?.icon))
/** Причина, по которой явный `icon` записи не принят, — только когда уровня нет вовсе. */
const unavailable = computed(() => (!item.value ? (props.route?.icon_error ?? null) : null))
const glyph = computed(
  () => item.value?.icon ?? (unavailable.value ? ROUTE_ICON_UNKNOWN.icon : null),
)
const hint = computed(() => {
  if (unavailable.value) {
    const reason = `${ROUTE_ICON_UNKNOWN.label} — ${unavailable.value}`
    return props.title ? `${props.title} · ${reason}` : reason
  }
  return props.title ?? item.value?.hint ?? ''
})
</script>

<template>
  <span
    v-if="glyph"
    class="listik-route-icon"
    :class="{ 'listik-route-icon--unknown': unavailable }"
    :title="hint"
    :aria-label="hint"
  >
    <ListikIcon :name="glyph" :size="size" />
  </span>
</template>
