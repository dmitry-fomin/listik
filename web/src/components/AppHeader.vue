<script setup lang="ts">
/**
 * Шапка приложения (UiAppHeader): марка, индикатор живости сервера, фильтр по
 * проекту и поиск задач (глобальные — влияют на всю доску, поэтому в шапке, а
 * не в тулбаре конкретного вида; поиск открывает палитру SearchPanel),
 * переключатель темы, ссылка на настройки (репозитории и маршруты) и кнопка
 * обновления. Поиск, тема, настройки и «Обновить» — один ряд безрамочных
 * (`ghost`) кнопок-иконок без подписи: подпись каждой несёт UiTooltip и
 * `aria-label`, так ряд действий не спорит с фильтрами за ширину шапки. Переключатель вида (Доска / Список /
 * Метрики) — в App.vue как UiSegmented: он ничего не переключает в
 * контенте сам, это radiogroup, а не tablist, и он же несёт счётчики.
 *
 * Два режима (`mode`): на доске — всё перечисленное, на странице настроек
 * (`/settings/*`) из действий остаются только тема и «К доске». Фильтры, поиск и
 * «Обновить» к настройкам отношения не имеют, а «К доске» — единственный путь
 * назад, поэтому он виден и на телефоне (проп `phone` в этом режиме ничего не
 * прячет: страница по прямому адресу иначе была бы тупиком).
 *
 * Настройки и «К доске» — настоящие ссылки (`<a href>`: у «Настроек» это
 * `UiButton` с `as="a"`, у «К доске» — `UiHeaderActionButton`): обычный клик ведёт роутер без перезагрузки, клик с модификатором
 * отдаётся браузеру и открывает новую вкладку. Марка в режиме настроек ведёт
 * туда же, что «К доске» (`/`), и делает это сама, без события `home`: `goHome`
 * в App.vue переключает вид доски на «Доску», а возврат из настроек обязан
 * оставить вид тем, каким он был.
 *
 * Марка — картинка /logo.svg (зелёный контурный лист с черешком и центральной жилкой), не UiBrandMark; многоточие названию ставит приложение: слот #brand в ките
 * сжимается и обрезает содержимое, а кит не знает, какой элемент слота текстовый.
 *
 * Живость сервера показывает один индикатор (health-статус), не два: раньше
 * рядом висел ещё и статус SSE-потока (`live`) отдельной таблеткой — теперь это
 * просто строка в подсказке той же таблетки (см. healthTooltip), `live` как
 * проп остался только ради неё.
 *
 * «Сервер жив» — тон `success` (зелёный), а не `healthy`: в ките
 * `--health-healthy` намеренно нейтральный (`--ink-2`), зелёный закреплён за
 * статусной парой success/danger. Для индикатора соединения это статус, а не
 * здоровье сущности, поэтому живой сервер читается зелёным; точки здоровья
 * задач (HealthDot) остаются нейтральными.
 */
import { computed } from 'vue'
import {
  UiAppHeader,
  UiButton,
  UiChip,
  UiHeaderActionButton,
  UiSelect,
  UiStatusPill,
  UiTooltip,
  type UiSelectOption,
} from '@zoloto585/facet'
import ListikIcon from './ListikIcon.vue'
import ProjectPicker from './ProjectPicker.vue'
import store from '@/store/listik'
import { onLinkClick } from '@/lib/router'
import { useTheme } from '@/lib/theme'
import type { Health } from '@/api/types'
import { formatTime } from '@/lib/format'

const props = withDefaults(
  defineProps<{
    health: Health | null
    live: boolean
    loading: boolean
    lastSyncAt: string | null
    phone?: boolean
    /** Где мы: на доске или на странице настроек. */
    mode?: 'board' | 'settings'
  }>(),
  { phone: false, mode: 'board' },
)

const emit = defineEmits<{
  refresh: []
  home: []
}>()

/**
 * Клик по марке: на доске — событие `home` (закрыть карточку, вернуться на
 * «Доску»), на настройках — обычный переход роутером на `/`, чтобы вид доски
 * остался прежним. С модификатором в обоих случаях отдаём браузеру.
 */
function onBrandClick(event: MouseEvent): void {
  if (props.mode === 'settings') {
    onLinkClick(event, '/')
    return
  }
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  emit('home')
}

const { theme, toggleTheme } = useTheme()

/** Живой сервер — зелёный `success` (см. комментарий выше); нет ответа —
 *  `dead` (красный), любой другой статус здоровья — нейтральный `unknown`. */
const healthTone = computed<'success' | 'dead' | 'unknown'>(() => {
  if (!props.health) return 'dead'
  return props.health.status === 'ok' ? 'success' : 'unknown'
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
  return `синхронизировано ${formatTime(props.lastSyncAt)}`
})

const streamLabel = computed(() => `поток: ${props.live ? 'живой' : 'нет'}`)

const healthTooltip = computed(
  () => `${healthLabel.value} · ${streamLabel.value} · ${embedLabel.value} · ${syncLabel.value} · ${countsLabel.value}`,
)

/**
 * «Я — …»: кем доска представляется серверу. Не вход и не пароль — просто выбор
 * имени из `server.users` (`/api/health`), которое уходит заголовком на каждый
 * запрос; сервер по нему фильтрует списки и не даёт брать чужое. Пункт «все
 * задачи» снимает имя — он же и единственный способ сброса (кнопки очистки у
 * UiSelect нет). В локальном режиме выбора нет вовсе: сервер заголовок
 * игнорирует, у задач владельца нет.
 */
const ownerOptions = computed<UiSelectOption[]>(() => [
  { value: '', label: 'все задачи' },
  ...store.users.value.map((user) => ({ value: user, label: user })),
])

const themeLabel = computed(() => (theme.value === 'dark' ? 'Включить светлую тему' : 'Включить тёмную тему'))
</script>

<template>
  <UiAppHeader>
    <template #brand>
      <!-- Знак Listik — зелёный контурный лист с черешком и прямой центральной жилкой. Не UiBrandMark:
           у марки кита свой градиентный фон и рамка, а знак — самостоятельная цветная иконка -->
      <!-- Марка ведёт на доску: обычный клик на доске переключает вид, на настройках —
           возвращает на «/» роутером (вид доски при этом не трогается), клик с
           модификатором/средней кнопкой отдаётся браузеру (новая вкладка на «/»). -->
      <a class="listik-shell__brand-link" href="/" aria-label="Listik — на доску" @click="onBrandClick">
        <img class="listik-shell__brand-logo" src="/logo.svg" alt="" width="28" height="28" />
        <strong class="listik-shell__brand-name">Listik</strong>
      </a>
      <UiTooltip :text="healthTooltip" placement="bottom">
        <UiStatusPill :tone="healthTone" size="sm">{{ healthLabel }}</UiStatusPill>
      </UiTooltip>
    </template>

    <template #actions>
      <div class="listik-shell__actions">
        <template v-if="mode === 'settings'">
          <UiTooltip :text="themeLabel" placement="bottom">
            <UiButton size="sm" variant="ghost" v-bind="{ 'aria-label': themeLabel }" @click="toggleTheme">
              <template #icon><ListikIcon :name="theme === 'dark' ? 'sun' : 'moon'" size="sm" /></template>
            </UiButton>
          </UiTooltip>
          <!-- Ссылка, а не кнопка: Cmd/Ctrl-клик открывает доску в новой вкладке,
               обычный клик ведёт роутер без перезагрузки. -->
          <UiTooltip text="Вернуться на доску" placement="bottom">
            <UiHeaderActionButton href="/" @click="onLinkClick($event, '/')">
              <template #icon><ListikIcon name="columns" size="sm" /></template>
              К доске
            </UiHeaderActionButton>
          </UiTooltip>
        </template>

        <template v-else>
          <template v-if="!phone">
            <ProjectPicker />
            <template v-if="store.isServerMode.value">
              <UiSelect
                :model-value="store.owner.value"
                :options="ownerOptions"
                placeholder="Я — …"
                ariaLabel="От чьего имени"
                size="sm"
                @update:model-value="store.setOwner($event ?? '')"
              />
              <UiChip v-if="!store.owner.value" label="Представьтесь, чтобы брать задачи" size="sm" />
            </template>
            <UiTooltip text="Поиск по задачам · Cmd K" placement="bottom">
              <UiButton
                size="sm"
                variant="ghost"
                v-bind="{ 'aria-label': 'Поиск по задачам' }"
                @click="store.openSearch('')"
              >
                <template #icon><ListikIcon name="search" size="sm" /></template>
              </UiButton>
            </UiTooltip>
          </template>
          <UiTooltip :text="themeLabel" placement="bottom">
            <UiButton size="sm" variant="ghost" v-bind="{ 'aria-label': themeLabel }" @click="toggleTheme">
              <template #icon><ListikIcon :name="theme === 'dark' ? 'sun' : 'moon'" size="sm" /></template>
            </UiButton>
          </UiTooltip>
          <UiTooltip v-if="!phone" text="Настройки: репозитории и маршруты" placement="bottom">
            <UiButton
              as="a"
              href="/settings/repos"
              size="sm"
              variant="ghost"
              v-bind="{ 'aria-label': 'Настройки: репозитории и маршруты' }"
              @click="onLinkClick($event, '/settings/repos')"
            >
              <template #icon><ListikIcon name="gear" size="sm" /></template>
            </UiButton>
          </UiTooltip>
          <UiTooltip text="Обновить доску" placement="bottom">
            <UiButton
              size="sm"
              variant="ghost"
              :loading="loading"
              v-bind="{ 'aria-label': 'Обновить доску' }"
              @click="emit('refresh')"
            >
              <template #icon><ListikIcon name="refresh" size="xs" /></template>
            </UiButton>
          </UiTooltip>
        </template>
      </div>
    </template>
  </UiAppHeader>
</template>
