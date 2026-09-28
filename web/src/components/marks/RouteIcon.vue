<script setup lang="ts">
/**
 * RouteIcon — иконка уровня маршрута разработки (xhigh/high/medium/low/xlow/direct):
 * выбор маршрута в `RoutePicker` и карточка заведённой задачи. Уровень и
 * подпись берутся из справочника `lib/dictionaries.ts`, путь рисует `ListikIcon`
 * из общего словаря `icons.ts` (кит icon-agnostic).
 *
 * Уровень считает сервер (`GET /api/routes`, поле `icon` с фолбэком по ключу) —
 * компонент получает готовую запись маршрута. Записи нет или у неё нет уровня
 * (`opus-pipeline` и подобные) — не рисуем ничего.
 *
 * Подсказка — нативным `title`, а не `UiTooltip`: иконка стоит и внутри
 * строки-кнопки выбора маршрута, у которой уже есть свой `title`, — второй слой
 * подсказок всплывал бы одновременно с ним.
 */
import { computed } from 'vue'
import ListikIcon from '@/components/ListikIcon.vue'
import { routeIcon } from '@/lib/dictionaries'
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
const glyph = computed(() => item.value?.icon ?? null)
const hint = computed(() => props.title ?? item.value?.hint ?? '')
</script>

<template>
  <span
    v-if="glyph"
    class="listik-route-icon"
    :title="hint"
    :aria-label="hint"
  >
    <ListikIcon :name="glyph" :size="size" />
  </span>
</template>
