<script setup lang="ts">
/**
 * Раздел «Маршруты» страницы настроек (`views/SettingsPage.vue`, `/settings/routes`):
 * список записей `routes` тремя группами — «Конвейеры» (`kind=pipeline`),
 * «Рой» (`kind=swarm` и конвейеры на `driver=swarm`) и «Прямая выдача»
 * (`kind=direct`). Правку самой записи ведут карточки справа: `RouteCard.vue`
 * (конвейер, автосохранение шапки), `RouteSwarmCard.vue` (рой: выбор
 * исполнителя роли, пропуск, команда роли) и `RouteDirectCard.vue` (прямая
 * выдача, автосохранение и редактор argv).
 *
 * Автосохранение не спасает правку, которую сервер не примет (команда с
 * ошибкой, все роли сняты), — такая правка на сервер не уходит, поэтому этот
 * файл по-прежнему сторожит уход с несохранённой карточки (`update:dirty`).
 *
 * Заведение — первичное действие раздела «Завести маршрут»: открывает выбор
 * вида (рой или прямая выдача; конвейер приходит из поставки сам), выбор ведёт
 * в `NewSwarmRouteModal`/`NewDirectRouteModal`. Те же окна открывают кнопки
 * «+» в заголовках групп «Рой» и «Прямая выдача» — сразу нужный вид, без
 * выбора. У «Конвейеров» кнопки нет: конвейер приходит из поставки сам.
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiCard,
  UiConfirmDialog,
  UiEmptyState,
  UiSpinner,
  UiTooltip,
} from '@zoloto585/facet'
import RouteCard from './RouteCard.vue'
import RouteSwarmCard from './RouteSwarmCard.vue'
import RouteDirectCard from './RouteDirectCard.vue'
import NewDirectRouteModal from './NewDirectRouteModal.vue'
import NewSwarmRouteModal from './NewSwarmRouteModal.vue'
import ListikIcon from './ListikIcon.vue'
import RouteIcon from './marks/RouteIcon.vue'
import store from '@/store/listik'
import type {
  DirectRouteDef,
  PipelineRouteDef,
  RouteDef,
  SwarmLikeRoute,
} from '@/api/types'
import { directRoutesOf, pipelineRowsOf, previewCommand, swarmRoutesOf } from '@/lib/routes'
import { ROLE_KEYS, ROLE_STAGE } from '@/lib/pipelines'
import { PIPELINE_STAGES } from '@/lib/dictionaries'
import { registerLeaveGuard } from '@/lib/router'

/** Источник списка — сам `store.routes`, без местных копий. */
const pipelineRoutes = computed<PipelineRouteDef[]>(() => pipelineRowsOf(store.routes.value))
const swarmRoutes = computed<SwarmLikeRoute[]>(() => swarmRoutesOf(store.routes.value))
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
function filledRoles(route: SwarmLikeRoute): number {
  return Object.values(route.roles).filter((cell) => cell !== null && cell !== undefined).length
}

/** Моноширинная подпись конвейера — только ключ: роли ушли в пилюлю справа. */
function pipelineMeta(route: PipelineRouteDef): string {
  return route.key
}

/** Текст пилюли состава — `N ролей` со склонением. */
function rolesBadge(route: SwarmLikeRoute): string {
  const count = filledRoles(route)
  return `${count} ${rolesWord(count)}`
}

/**
 * Подпись маршрута роя: ключ плюс пропущенные этапы кодами (`s2 пропущен`),
 * как у строки в «Новой задаче». Цепочка исполнителей в узкой колонке не
 * помещается — она видна в карточке справа.
 */
function swarmMeta(route: SwarmLikeRoute): string {
  const skipped = ROLE_KEYS.filter((role) => !route.roles[role]).map((role) => {
    const stage = PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role])
    return stage?.code ?? role
  })
  const tail = skipped.length > 0 ? ` · ${skipped.join(', ')} пропущен` : ''
  return `${route.key}${tail}`
}

/** Моноширинная подпись прямой выдачи — команда одной строкой. */
function directMeta(route: DirectRouteDef): string {
  return route.command && route.command.length > 0 ? previewCommand(route.command, route.key) : ''
}

/** Подсказка бейджа расхождения — та же, что была у строки. */
function skillMissingHint(route: PipelineRouteDef): string {
  return `каталога /feature-pipeline:${route.key} нет, маршрут скрыт от автора`
}

const selectedKey = ref<string | null>(null)
const selected = computed<RouteDef | null>(
  () => store.routes.value.find((route) => route.key === selectedKey.value) ?? null,
)

/**
 * Разновидность выбранной записи отдельными computed, а не `v-if` по
 * `selected.kind` в шаблоне: так карточка получает уже сузившийся тип
 * (`PipelineRouteDef`/`SwarmRouteDef`/`DirectRouteDef`), а не `RouteDef` с
 * приведением. Конвейер с `driver=swarm` правится карточкой роя — у него те
 * же ячейки `{harness, argv, prompt}`.
 */
const selectedPipeline = computed<PipelineRouteDef | null>(() =>
  selected.value?.kind === 'pipeline' && selected.value.driver !== 'swarm'
    ? selected.value
    : null,
)
const selectedSwarm = computed<SwarmLikeRoute | null>(() =>
  selected.value?.kind === 'swarm' ||
  (selected.value?.kind === 'pipeline' && selected.value.driver === 'swarm')
    ? (selected.value as SwarmLikeRoute)
    : null,
)
const selectedDirect = computed<DirectRouteDef | null>(() =>
  selected.value?.kind === 'direct' ? selected.value : null,
)

/*
 * Карточки роя и прямой выдачи сохраняются сами, но правку, которую сервер не
 * примет (команда с ошибкой, все роли сняты), на сервер не пускают — она при
 * уходе теряется, о ней и спрашиваем (`update:dirty` поднимается только в
 * этом случае). Уходов два, и оба спрашивают одно и то же одним диалогом:
 * выбор другой строки списка (`kind: 'route'`) и уход со всей страницы
 * (`kind: 'path'`, сторож роутера ждёт ответа через `resolve`).
 *
 * `beforeunload` не ставим: перезагрузка и закрытие вкладки — не дело раздела.
 */
type LeaveTarget =
  | { kind: 'route'; route: RouteDef }
  | { kind: 'path'; resolve: (allowed: boolean) => void }

const cardDirty = ref(false)
const leaveTarget = ref<LeaveTarget | null>(null)

/** Несохранённая правка есть у карточек с редактируемым составом — не у конвейера. */
const hasUnsaved = computed(
  () => cardDirty.value && Boolean(selectedDirect.value || selectedSwarm.value),
)

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
  cardDirty.value = false
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

/** Сторож роутера: с несохранённой карточки спрашиваем одинаково, куда бы ни уходили. */
function leaveGuard(): boolean | Promise<boolean> {
  if (!hasUnsaved.value) return true
  return new Promise<boolean>((resolve) => {
    leaveTarget.value = { kind: 'path', resolve }
  })
}

let unregisterLeaveGuard: (() => void) | null = null

/*
 * Заведение маршрута. Первичное действие раздела («Завести маршрут» в шапке
 * страницы) открывает выбор вида — небольшую панель над списком; конвейер там
 * отсутствует как заводимый: он появляется сам из поставки. Выбор ведёт в
 * своё окно (`v-if` — каждый раз пустой черновик).
 */
const chooserOpen = ref(false)
const createKind = ref<'swarm' | 'direct' | null>(null)

function openPrimaryAction(): void {
  chooserOpen.value = !chooserOpen.value
}

function openCreate(kind: 'swarm' | 'direct'): void {
  chooserOpen.value = false
  createKind.value = kind
}

/** Успех: стор уже перечитал список — выбираем новую запись и открываем её карточку. */
function onCreate(route: RouteDef): void {
  createKind.value = null
  cardDirty.value = false
  selectedKey.value = route.key
}

defineExpose({ openPrimaryAction })

onMounted(() => {
  unregisterLeaveGuard = registerLeaveGuard(leaveGuard)
  void store.reloadRoutes()
  void store.ensureHarnesses()
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

    <!-- Выбор вида заводимого маршрута (макет: панель под кнопкой «Завести
         маршрут»). Конвейера как пункта нет — он приходит из поставки сам. -->
    <UiCard v-if="chooserOpen" padding="sm" class="listik-routes-settings__chooser">
      <button
        type="button"
        class="listik-routes-row"
        @click="openCreate('swarm')"
      >
        <ListikIcon name="branch" size="sm" />
        <span class="listik-routes-row__main">
          <span class="listik-routes-row__title">Маршрут роя</span>
          <span class="listik-routes-settings__chooser-hint">
            Listik водит карточку по этапам, на каждый — свой харнесс; роли выбираются и пропускаются
          </span>
        </span>
      </button>
      <button
        type="button"
        class="listik-routes-row"
        @click="openCreate('direct')"
      >
        <ListikIcon name="route-direct" size="sm" />
        <span class="listik-routes-row__main">
          <span class="listik-routes-row__title">Прямая выдача</span>
          <span class="listik-routes-settings__chooser-hint">
            одна команда на всю задачу; держатель — любой харнесс из раздела «Харнессы»
          </span>
        </span>
      </button>
      <p class="listik-routes-settings__chooser-note">
        Конвейер из поставки появится сам, когда в <code class="listik-mono">plugins/feature-pipeline/skills/</code>
        ляжет новый SKILL.md
      </p>
    </UiCard>

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
                      нет каталога
                    </UiBadge>
                    <!-- Состав конвейера — счётчиком у самого правого края строки. -->
                    <UiBadge tone="neutral" size="sm">{{ rolesBadge(route) }}</UiBadge>
                  </button>
                </li>
              </ul>
            </UiCard>
          </section>

          <section class="listik-routes-settings__group">
            <UiCard padding="sm">
              <div class="listik-routes-settings__group-head">
                <h3 class="listik-section__title">Рой</h3>
                <UiBadge tone="neutral" size="sm">{{ swarmRoutes.length }}</UiBadge>
                <UiTooltip text="добавить новый рой" placement="left">
                  <UiButton
                    class="listik-routes-settings__add"
                    size="sm"
                    variant="ghost"
                    v-bind="{ 'aria-label': 'добавить новый рой' }"
                    @click="openCreate('swarm')"
                  >
                    <template #icon><ListikIcon name="plus" size="sm" /></template>
                  </UiButton>
                </UiTooltip>
              </div>

              <UiEmptyState v-if="swarmRoutes.length === 0" compact title="Маршрутов роя нет" />

              <ul v-else class="listik-routes-settings__rows">
                <li
                  v-for="route in swarmRoutes"
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
                      <code class="listik-mono">{{ swarmMeta(route) }}</code>
                    </span>
                    <UiBadge v-if="!route.visible" tone="neutral" size="sm">выключен</UiBadge>
                    <UiBadge tone="neutral" size="sm">{{ rolesBadge(route) }}</UiBadge>
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
                <UiTooltip text="добавить прямую выдачу" placement="left">
                  <UiButton
                    class="listik-routes-settings__add"
                    size="sm"
                    variant="ghost"
                    v-bind="{ 'aria-label': 'добавить прямую выдачу' }"
                    @click="openCreate('direct')"
                  >
                    <template #icon><ListikIcon name="plus" size="sm" /></template>
                  </UiButton>
                </UiTooltip>
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
            @update:dirty="(value: boolean) => (cardDirty = value)"
          />
          <RouteSwarmCard
            v-else-if="selectedSwarm"
            :key="selectedSwarm.key"
            :route="selectedSwarm"
            @update:dirty="(value: boolean) => (cardDirty = value)"
          />
          <RouteCard v-else-if="selectedPipeline" :key="selectedPipeline.key" :route="selectedPipeline" />
        </div>
      </UiCard>
    </div>

    <NewSwarmRouteModal
      v-if="createKind === 'swarm'"
      @created="onCreate"
      @close="createKind = null"
    />
    <NewDirectRouteModal
      v-else-if="createKind === 'direct'"
      @created="onCreate"
      @close="createKind = null"
    />

    <UiConfirmDialog
      :model-value="Boolean(leaveTarget)"
      tone="danger"
      title="Уйти и потерять правки?"
      description="Правка маршрута изменена, но не сохранена — уход с карточки вернёт её к тому, что в базе."
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
   счётчик-бейдж стоит рядом с ним. */
.listik-routes-settings .listik-section__title {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--ink-3);
}

/* Выбор вида заводимого маршрута: две строки-варианта и приглушённая сноска
   про конвейер (макет «Завести маршрут»). */
.listik-routes-settings__chooser {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.listik-routes-settings__chooser-hint {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-routes-settings__chooser-note {
  margin: var(--space-1) 0 0;
  padding: 0 var(--space-3);
  font-size: var(--text-xs);
  color: var(--ink-3);
}

/* Ширину стоит отдавать редактору argv, плиткам состава и блоку команды —
   карточка растёт на `1fr`. Списку хватает 320px: суженная колонка разделов
   отдала место и ему — в строке помещаются ключ и бейджи состава. */
.listik-routes-settings__layout {
  display: grid;
  grid-template-columns: minmax(0, 320px) minmax(0, 1fr);
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

/* «+» заведения прижата к правому краю заголовка группы. */
.listik-routes-settings__add {
  margin-left: auto;
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
</style>
