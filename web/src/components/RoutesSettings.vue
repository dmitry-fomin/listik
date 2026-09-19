<script setup lang="ts">
/**
 * Раздел «Маршруты» страницы настроек (`views/SettingsPage.vue`, `/settings/routes`):
 * список записей `routes` (конвейеры и прямая выдача). Правку самой записи ведут
 * карточки справа: `RouteCard.vue` (конвейер, автосохранение шапки) и
 * `RouteDirectCard.vue` (прямая выдача, явное «Сохранить» и редактор argv).
 * Вторая правится не сама собой, поэтому этот файл сторожит уход с
 * несохранённой карточки.
 *
 * Список — две карточки `UiCard`: «Конвейеры» (`kind=pipeline`) и «Прямая
 * выдача» (`kind=direct`); рядом с заголовком группы — счётчик записей
 * (`UiBadge`). Порядок и удаление маршрута модели данных не принадлежат:
 * перестановки нет, а ненужный маршрут выключают, а не удаляют, — поэтому
 * органов управления списком здесь не осталось. Строка-кнопка — единственная
 * своя разметка: в ките нет выбираемой двухстрочной строки.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiCard,
  UiConfirmDialog,
  UiEmptyState,
  UiSpinner,
} from '@zoloto585/facet'
import RouteCard from './RouteCard.vue'
import RouteDirectCard from './RouteDirectCard.vue'
import RouteIcon from './marks/RouteIcon.vue'
import store from '@/store/listik'
import type { DirectRouteDef, PipelineRouteDef, RouteDef } from '@/api/types'
import { directRoutesOf, pipelineRowsOf, previewCommand } from '@/lib/routes'
import { registerLeaveGuard } from '@/lib/router'

/** Источник списка — сам `store.routes`, без местных копий. */
const pipelineRoutes = computed<PipelineRouteDef[]>(() => pipelineRowsOf(store.routes.value))
const directRoutes = computed<DirectRouteDef[]>(() => directRoutesOf(store.routes.value))

/**
 * Полное склонение слова «роль» по числу: 1 роль, 2 роли, 5 ролей, 11 ролей,
 * 21 роль. Исключение 11–14 перебивает правило последней цифры.
 */
function rolesWord(count: number): string {
  const lastTwo = count % 100
  if (lastTwo >= 11 && lastTwo <= 14) return 'ролей'
  const last = count % 10
  if (last === 1) return 'роль'
  if (last >= 2 && last <= 4) return 'роли'
  return 'ролей'
}

/** Непустые ячейки `route.roles`: ключ со значением `null`/`undefined` не считается. */
function filledRoles(route: PipelineRouteDef): number {
  return Object.values(route.roles).filter((cell) => cell !== null && cell !== undefined).length
}

/** Моноширинная подпись конвейера — `<ключ> · N ролей`. */
function pipelineMeta(route: PipelineRouteDef): string {
  const count = filledRoles(route)
  return `${route.key} · ${count} ${rolesWord(count)}`
}

/** Моноширинная подпись прямой выдачи — команда одной строкой. */
function directMeta(route: DirectRouteDef): string {
  return route.command && route.command.length > 0 ? previewCommand(route.command, route.key) : ''
}

/** Подсказка бейджа «нет скила» — та же, что была у строки. */
function skillMissingHint(route: PipelineRouteDef): string {
  return `скила /feature-pipeline:${route.key} нет, маршрут скрыт от автора`
}

const selectedKey = ref<string | null>(null)
const selected = computed<RouteDef | null>(
  () => store.routes.value.find((route) => route.key === selectedKey.value) ?? null,
)

/**
 * Разновидность выбранной записи отдельными computed, а не `v-if` по
 * `selected.kind` в шаблоне: так карточка получает уже сузившийся тип
 * (`PipelineRouteDef`/`DirectRouteDef`), а не `RouteDef` с приведением.
 */
const selectedPipeline = computed<PipelineRouteDef | null>(() =>
  selected.value?.kind === 'pipeline' ? selected.value : null,
)
const selectedDirect = computed<DirectRouteDef | null>(() =>
  selected.value?.kind === 'direct' ? selected.value : null,
)

/*
 * Карточка прямой выдачи сохраняется явно, поэтому уход с неё — потеря правок.
 * Уходов два, и оба спрашивают одно и то же одним диалогом: выбор другой строки
 * списка (`kind: 'route'`) и уход со всей страницы — пункт настроек, «К доске»,
 * марка, «назад»/«вперёд» браузера (`kind: 'path'`, сторож роутера ждёт ответа
 * через `resolve`). Отсюда одна цель `leaveTarget` и одна пара
 * `confirmLeave`/`cancelLeave`: два независимых диалога разъехались бы текстами
 * и могли бы открыться вдвоём. Флаг `directDirty` приходит от самой карточки
 * (`update:dirty`) и сбрасывается ею же при пересоздании под другой ключ.
 *
 * `beforeunload` не ставим: перезагрузка и закрытие вкладки — не дело раздела.
 */
type LeaveTarget =
  | { kind: 'route'; route: RouteDef }
  | { kind: 'path'; resolve: (allowed: boolean) => void }

const directDirty = ref(false)
const leaveTarget = ref<LeaveTarget | null>(null)

/** Несохранённая правка есть только у карточки прямой выдачи — у конвейера автосохранение. */
const hasUnsaved = computed(() => directDirty.value && Boolean(selectedDirect.value))

function selectRoute(route: RouteDef): void {
  if (route.key === selectedKey.value) return
  if (hasUnsaved.value) {
    leaveTarget.value = { kind: 'route', route }
    return
  }
  selectedKey.value = route.key
}

function confirmLeave(): void {
  const target = leaveTarget.value
  leaveTarget.value = null
  if (!target) return
  directDirty.value = false
  if (target.kind === 'route') selectedKey.value = target.route.key
  else target.resolve(true)
}

/**
 * «Остаться», крестик, Escape и клик по фону — один и тот же отказ. Диалог кита
 * шлёт на закрытие и `cancel`, и `update:model-value(false)`, поэтому функция
 * обязана быть идемпотентной: цель уже снята — второй вызов ничего не делает.
 */
function cancelLeave(): void {
  const target = leaveTarget.value
  leaveTarget.value = null
  if (target?.kind === 'path') target.resolve(false)
}

/**
 * Сторож роутера: аргумент `to` не нужен — с несохранённой карточки спрашиваем
 * одинаково, куда бы ни уходили.
 */
function leaveGuard(): boolean | Promise<boolean> {
  if (!hasUnsaved.value) return true
  return new Promise<boolean>((resolve) => {
    leaveTarget.value = { kind: 'path', resolve }
  })
}

let unregisterLeaveGuard: (() => void) | null = null

onMounted(() => {
  unregisterLeaveGuard = registerLeaveGuard(leaveGuard)
  void store.reloadRoutes()
})

onBeforeUnmount(() => {
  unregisterLeaveGuard?.()
  unregisterLeaveGuard = null
})
</script>

<template>
  <div class="listik-routes-settings">
    <UiAlert
      v-if="store.routesSettingsError.value"
      tone="warning"
      closable
      @close="store.routesSettingsError.value = null"
    >
      <template #title>Не получилось</template>
      {{ store.routesSettingsError.value }}
    </UiAlert>

    <div class="listik-routes-settings__layout">
      <div class="listik-routes-settings__list">
        <div
          v-if="store.routesSettingsLoading.value && store.routes.value.length === 0"
          class="listik-routes-settings__loading"
        >
          <UiSpinner size="sm" label="Читаю маршруты" />
        </div>

        <template v-else>
          <section class="listik-routes-settings__group">
            <UiCard padding="sm">
              <div class="listik-routes-settings__group-head">
                <h3 class="listik-section__title">Конвейеры</h3>
                <UiBadge tone="neutral" size="sm">{{ pipelineRoutes.length }}</UiBadge>
              </div>

              <UiEmptyState v-if="pipelineRoutes.length === 0" compact title="Конвейеров нет" />

              <ul v-else class="listik-routes-settings__rows">
                <li
                  v-for="route in pipelineRoutes"
                  :key="route.key"
                  class="listik-routes-settings__row"
                >
                  <button
                    type="button"
                    class="listik-routes-row"
                    :class="{ 'is-selected': selectedKey === route.key, 'is-off': !route.visible }"
                    :data-key="route.key"
                    @click="selectRoute(route)"
                  >
                    <RouteIcon :route="route" size="sm" />
                    <span class="listik-routes-row__main">
                      <span class="listik-routes-row__title">{{ route.title }}</span>
                      <code class="listik-mono">{{ pipelineMeta(route) }}</code>
                    </span>
                    <UiBadge v-if="!route.visible" tone="neutral" size="sm">выключен</UiBadge>
                    <UiBadge
                      v-if="route.skill_missing"
                      tone="warning"
                      size="sm"
                      v-bind="{ title: skillMissingHint(route) }"
                    >
                      нет скила
                    </UiBadge>
                  </button>
                </li>
              </ul>
            </UiCard>
          </section>

          <section class="listik-routes-settings__group">
            <UiCard padding="sm">
              <div class="listik-routes-settings__group-head">
                <h3 class="listik-section__title">Прямая выдача</h3>
                <UiBadge tone="neutral" size="sm">{{ directRoutes.length }}</UiBadge>
              </div>

              <UiEmptyState v-if="directRoutes.length === 0" compact title="Прямых маршрутов нет" />

              <ul v-else class="listik-routes-settings__rows">
                <li
                  v-for="route in directRoutes"
                  :key="route.key"
                  class="listik-routes-settings__row"
                >
                  <button
                    type="button"
                    class="listik-routes-row"
                    :class="{ 'is-selected': selectedKey === route.key, 'is-off': !route.visible }"
                    :data-key="route.key"
                    @click="selectRoute(route)"
                  >
                    <RouteIcon :route="route" size="sm" />
                    <span class="listik-routes-row__main">
                      <span class="listik-routes-row__title">{{ route.title }}</span>
                      <code class="listik-mono">{{ directMeta(route) }}</code>
                    </span>
                    <UiBadge v-if="!route.visible" tone="neutral" size="sm">выключен</UiBadge>
                  </button>
                </li>
              </ul>
            </UiCard>
          </section>
        </template>
      </div>

      <UiCard class="listik-routes-settings__panel" padding="lg">
        <UiEmptyState
          v-if="!selected"
          compact
          title="Выбери маршрут слева"
          description="Справа откроется карточка: подпись, уровень, состав и команда запуска."
        />
        <div v-else class="listik-routes-settings__card">
          <RouteDirectCard
            v-if="selectedDirect"
            :key="selectedDirect.key"
            :route="selectedDirect"
            @update:dirty="(value: boolean) => (directDirty = value)"
          />
          <RouteCard v-else-if="selectedPipeline" :key="selectedPipeline.key" :route="selectedPipeline" />
        </div>
      </UiCard>
    </div>

    <UiConfirmDialog
      :model-value="Boolean(leaveTarget)"
      tone="danger"
      title="Уйти и потерять правки?"
      description="Команда маршрута изменена, но не сохранена — уход с карточки вернёт её к тому, что в базе."
      confirm-label="Уйти"
      cancel-label="Остаться"
      @update:model-value="(value: boolean) => { if (!value) cancelLeave() }"
      @confirm="confirmLeave"
      @cancel="cancelLeave"
    />
  </div>
</template>

<style scoped>
.listik-routes-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

/* Заголовок группы — мелкий прописной надзаголовок карточки, как в макете;
   счётчик-бейдж стоит рядом с ним, но вне этого элемента (треб. 3). */
.listik-routes-settings .listik-section__title {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-3);
}

/* 2:3 в пользу карточки: раздел живёт уже не в узкой модалке, а на целой
   странице, и ширину стоит отдавать туда, где правят, — редактору argv, плиткам
   состава и блоку команды. Списку хватает своего минимума (280px: иконка,
   название с подписью и ключ), меньше — и ключ начинал бы теснить название. */
.listik-routes-settings__layout {
  display: grid;
  grid-template-columns: minmax(280px, 2fr) minmax(0, 3fr);
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
  gap: var(--space-4);
  min-width: 0;
}

.listik-routes-settings__group {
  min-width: 0;
}

.listik-routes-settings__group-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.listik-routes-settings__rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-routes-settings__row {
  min-width: 0;
}

.listik-routes-settings__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-8) 0;
}

/* Карточка не уезжает с экрана вслед за длинным списком маршрутов: правая
   колонка прилипает под шапкой страницы. */
.listik-routes-settings__panel {
  position: sticky;
  top: calc(var(--header-h) + var(--space-4));
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
  padding: var(--space-2) var(--space-3);
  border: 1px solid transparent;
  border-radius: var(--radius-lg);
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.listik-routes-row:hover {
  background: var(--surface-2);
}

/* Выбранная строка — акцентная заливка и рамка по макету (accent-50/accent-200). */
.listik-routes-row.is-selected {
  background: var(--accent-50);
  border-color: var(--accent-200);
}

/* Выключенный маршрут приглушён целиком (иконка наследует currentColor). */
.listik-routes-row.is-off {
  color: var(--ink-3);
}

/* `width: 0` — не опечатка: колонку название+подпись растягивает `flex-grow`, а
   нулевая базовая ширина не даёт длинной команде считаться минимумом ячейки.
   Без неё строка вырастала шире своей колонки раздела и уезжала под
   горизонтальный скролл. */
.listik-routes-row__main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  width: 0;
  min-width: 0;
}

.listik-routes-row__title {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: var(--weight-medium);
}

/* Моноширинная подпись — вторая строка: ключ с числом ролей или команда одной
   строкой с многоточием, без переноса. */
.listik-routes-row .listik-mono {
  display: block;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--ink-3);
  font-size: var(--text-xs);
}
</style>
