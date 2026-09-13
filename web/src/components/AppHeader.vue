<script setup lang="ts">
/**
 * Шапка приложения (UiAppHeader): марка, индикатор живости сервера, фильтр по
 * проекту и поиск задач (глобальные — влияют на всю доску, поэтому в шапке, а
 * не в тулбаре конкретного вида; оба — кнопка «иконка + текст», поиск открывает
 * палитру SearchPanel), переключатель темы, кнопка настроек (репозитории,
 * оформление, ФИО) и кнопка обновления. Переключатель вида (Доска / Список /
 * Метрики) — в App.vue как UiSegmented: он ничего не переключает в
 * контенте сам, это radiogroup, а не tablist, и он же несёт счётчики.
 *
 * Марка — картинка /logo.svg (зелёный лист, жилка которого — галочка), не UiBrandMark; многоточие названию ставит приложение: слот #brand в ките
 * сжимается и обрезает содержимое, а кит не знает, какой элемент слота текстовый.
 *
 * Живость сервера показывает один индикатор (health-статус), не два: раньше
 * рядом висел ещё и статус SSE-потока (`live`) отдельной таблеткой — теперь это
 * просто строка в подсказке той же таблетки (см. healthTooltip), `live` как
 * проп остался только ради неё.
 */
import { computed } from 'vue'
import { UiAppHeader, UiButton, UiStatusPill, UiTooltip } from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import ProjectPicker from './ProjectPicker.vue'
import store from '@/store/listik'
import { useTheme } from '@/lib/theme'
import type { Health } from '@/api/types'

const props = withDefaults(
  defineProps<{
    health: Health | null
    live: boolean
    loading: boolean
    lastSyncAt: string | null
    phone?: boolean
  }>(),
  { phone: false },
)

const emit = defineEmits<{
  refresh: []
  projects: []
}>()

const { theme, toggleTheme } = useTheme()

const healthTone = computed<'healthy' | 'dead' | 'unknown'>(() => {
  if (!props.health) return 'dead'
  return props.health.status === 'ok' ? 'healthy' : 'unknown'
})

const healthLabel = computed(() => {
  if (!props.health) return 'сервер не отвечает'
  return props.health.status === 'ok' ? 'сервер жив' : `сервер: ${props.health.status}`
})

const embedLabel = computed(() => {
  const embed = props.health?.embed
  if (!embed) return 'эмбеддинги: нет данных'
  const models = Array.isArray(embed.models) ? embed.models.join(', ') : (embed.models ?? embed.model)
  return embed.ok ? `эмбеддинги: ок${models ? ` · ${models}` : ''}` : 'эмбеддинги: недоступны'
})

const countsLabel = computed(() => {
  const counts = props.health?.counts
  if (!counts) return 'счётчиков нет'
  return Object.entries(counts)
    .map(([key, value]) => `${key}: ${value}`)
    .join(' · ')
})

const syncLabel = computed(() => {
  if (!props.lastSyncAt) return 'ещё не синхронизировались'
  return `синхронизировано ${new Date(props.lastSyncAt).toLocaleTimeString('ru-RU')}`
})

const streamLabel = computed(() => `поток: ${props.live ? 'живой' : 'нет'}`)

const healthTooltip = computed(
  () => `${healthLabel.value} · ${streamLabel.value} · ${embedLabel.value} · ${syncLabel.value} · ${countsLabel.value}`,
)

const themeLabel = computed(() => (theme.value === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'))
</script>

<template>
  <UiAppHeader>
    <template #brand>
      <!-- Знак Listik — зелёный лист, чья центральная жилка складывается в галочку. Не UiBrandMark:
           у марки кита свой градиентный фон и рамка, а знак — самостоятельная цветная иконка -->
      <img class="listik-shell__brand-logo" src="/logo.svg" alt="Listik" width="28" height="28" />
      <strong class="listik-shell__brand-name">Listik</strong>
      <UiTooltip :text="healthTooltip" placement="bottom">
        <UiStatusPill :tone="healthTone" size="sm">{{ healthLabel }}</UiStatusPill>
      </UiTooltip>
    </template>

    <template #actions>
      <div class="listik-shell__actions">
        <template v-if="!phone">
          <ProjectPicker />
          <UiTooltip text="Поиск по задачам · Cmd K" placement="bottom">
            <UiButton size="sm" variant="ghost" @click="store.openSearch('')">
              <template #icon><ListikIcon name="search" size="sm" /></template>
              Поиск
            </UiButton>
          </UiTooltip>
        </template>
        <UiButton size="sm" variant="ghost" v-bind="{ 'aria-label': themeLabel }" @click="toggleTheme">
          <template #icon><ListikIcon :name="theme === 'dark' ? 'sun' : 'moon'" size="sm" /></template>
        </UiButton>
        <UiTooltip v-if="!phone" text="Репозитории, оформление, ФИО" placement="bottom">
          <UiButton size="sm" variant="ghost" @click="emit('projects')">
            <template #icon><ListikIcon name="gear" size="sm" /></template>
            <span class="listik-shell__hide-compact">Настройки</span>
          </UiButton>
        </UiTooltip>
        <UiTooltip text="Обновить" placement="bottom">
          <UiButton
            size="sm"
            variant="secondary"
            :loading="loading"
            v-bind="{ 'aria-label': 'Обновить' }"
            @click="emit('refresh')"
          >
            <template #icon><ListikIcon name="refresh" size="xs" /></template>
          </UiButton>
        </UiTooltip>
      </div>
    </template>
  </UiAppHeader>
</template>
