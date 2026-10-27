<script setup lang="ts">
/**
 * RouteCard — карточка выбранного маршрута-конвейера во вкладке «Маршруты»
 * настроек (`RoutesSettings.vue`, правая панель). Раскладка — по макету
 * `docs/design/settings/Настройки · Маршруты · конвейер-html/Routes.dc.html`.
 *
 * Шапка — своя, а не `UiEntityHeader`: у кита в subtitle нет слота, а ключ
 * маршрута в строке «Конвейер · скилл <key>» обязан быть моноширинным.
 * Остальное — из кита: `UiSwitch` (видимость), `UiField`/`UiInput`,
 * `IconToggle`, `UiEmptyState`, `UiCopyButton`, `UiSaveStatus`,
 * `UiAlert`.
 *
 * Состав ролей и команда пока только показываются: расклад правится через API
 * (`PATCH /api/routes/{key}`), а команда конвейера приходит вместе с его
 * поставкой. Ни одного
 * поля ввода, селекта или кнопки в блоке «Состав конвейера» нет; плитки ролей —
 * обычные `div`. Редактирование прямо отсюда — отдельная задача.
 *
 * Автосохранение шапки: текстовые поля — debounce 600мс после последней
 * клавиши плюс сброс по потере фокуса, переключатель и иконка — сразу.
 * Машина живёт в `useAutosave` (`@/lib/autosave`): диф `draft` против
 * `baseline` (обновлённого только по ключам последнего успешного PATCH) уходит
 * одним `PATCH /api/routes/<key>`, повторные вызовы во время запроса
 * досылаются следом со свежими полями. При размонтировании (выбор другого
 * маршрута) недосохранённая правка уходит, при ошибке PATCH тумблер
 * «В меню» откатывается к принятому сервером положению. `store.patchRoute`
 * после успеха перечитывает список, поэтому тумблер сам гасит строку слева.
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта: при выборе
 * другого маршрута компонент должен пересоздаться заново (свежие `draft`/
 * `baseline`), а не переиспользоваться с патчем пропа поверх недопечатанного
 * ввода.
 */
import { computed, reactive, ref } from 'vue'
import {
  UiAlert,
  UiCopyButton,
  UiEmptyState,
  UiField,
  UiInput,
  UiSaveStatus,
  UiSwitch,
} from '@zoloto585/facet'
import IconToggle from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import RouteIcon from './marks/RouteIcon.vue'
import RouteName from './marks/RouteName.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { PipelineRouteDef, RouteIconKey, RoutePatch } from '@/api/types'
import { useAutosave } from '@/lib/autosave'
import { ROUTE_ICON_OPTIONS, routeIconGlyph } from '@/lib/dictionaries'
import {
  executorGlyph,
  executorName,
  PIPELINE_ROLE_ICONS,
  PIPELINE_ROLE_KEYS,
  PIPELINE_ROLE_TITLES,
  roleExecutors,
  roleStage,
  type PipelineRoleKey,
} from '@/lib/pipelines'
import { routeSkillRef, splitPlaceholders, unknownPlaceholders } from '@/lib/routes'

const props = defineProps<{ route: PipelineRouteDef }>()
/** Скил записи по её `plugin` для бейджа расхождения; плагин не задан — `null`. */
const skillRef = computed(() => routeSkillRef(props.route))

/* ── шапка: title/hint/visible/icon, автосохранение ── */

interface HeaderDraft {
  title: string
  hint: string
  visible: boolean
  icon: RouteIconKey | null
}

function draftOf(route: PipelineRouteDef): HeaderDraft {
  return { title: route.title, hint: route.hint, visible: route.visible, icon: route.icon ?? null }
}

/** Последнее известное серверу состояние — только по ключам успешного PATCH. */
const baseline = reactive<HeaderDraft>(draftOf(props.route))
/** Текущий ввод автора — то, что показывают поля. */
const draft = reactive<HeaderDraft>(draftOf(props.route))

const saveError = ref<string | null>(null)
const errorFields = ref<string[]>([])

function diffPatch(): RoutePatch {
  const patch: RoutePatch = {}
  if (draft.title !== baseline.title) patch.title = draft.title
  if (draft.hint !== baseline.hint) patch.hint = draft.hint
  if (draft.visible !== baseline.visible) patch.visible = draft.visible
  if (draft.icon !== baseline.icon) patch.icon = draft.icon
  return patch
}

const { status, schedule } = useAutosave<RoutePatch>({
  diff: diffPatch,
  send: async (patch) => (await store.patchRoute(props.route.key, patch)) !== null,
  onSaved: (patch) => {
    for (const key of Object.keys(patch) as (keyof RoutePatch)[]) {
      (baseline as Record<string, unknown>)[key] = patch[key]
    }
    saveError.value = null
    errorFields.value = []
  },
  onFailed: (patch) => {
    saveError.value = store.routesSettingsError.value
    errorFields.value = Object.keys(patch)
    if ('visible' in patch) draft.visible = baseline.visible
  },
})

function onTitle(value: string): void {
  draft.title = value
  schedule(false)
}
function onHint(value: string): void {
  draft.hint = value
  schedule(false)
}
function onBlurText(): void {
  schedule(true)
}
function onVisible(value: boolean): void {
  if (value === draft.visible) return
  draft.visible = value
  schedule(true)
}

function errorFor(field: string): string | undefined {
  return status.value === 'error' && errorFields.value.includes(field) ? (saveError.value ?? undefined) : undefined
}

/* ── иконка: семь кнопок-глифов (шесть ROUTE_ICONS + «Без иконки») ── */

const iconValue = computed(() => draft.icon ?? '')

function onIcon(value: string): void {
  draft.icon = value === '' ? null : (value as RouteIconKey)
  schedule(true)
}

/* ── состав конвейера: только показ ── */

interface RoleTile {
  role: PipelineRoleKey
  title: string
  icon: string
  /** Код этапа (`s1`…) — из `PIPELINE_STAGES` по `PIPELINE_ROLE_STAGE`. */
  stageCode: string
  /** Исполнители роли построчно: глиф харнесса, имя и усилие. */
  models: { glyph: string; name: string; effort: string }[]
}

/** Колонка схемы: основной шаг и подшаг под ним (критика под ТЗ, судья под линзой). */
interface RoleColumn {
  main: RoleTile
  sub: RoleTile | null
}

function tileOf(role: PipelineRoleKey): RoleTile | null {
  const executors = roleExecutors(props.route.roles[role])
  if (executors.length === 0) return null
  return {
    role,
    title: PIPELINE_ROLE_TITLES[role],
    icon: PIPELINE_ROLE_ICONS[role],
    stageCode: roleStage(role)?.code ?? '',
    models: executors.map((executor) => ({
      glyph: executorGlyph(executor),
      name: executorName(executor),
      effort: executor.effort,
    })),
  }
}

/** Роли v2 с непустым массивом исполнителей; без линзы судья встаёт в основной ряд. */
const roleColumns = computed<RoleColumn[]>(() => {
  const tiles = Object.fromEntries(PIPELINE_ROLE_KEYS.map((role) => [role, tileOf(role)])) as Record<
    PipelineRoleKey,
    RoleTile | null
  >
  const columns: RoleColumn[] = []
  const push = (main: RoleTile | null, sub: RoleTile | null) => {
    if (main) columns.push({ main, sub })
    else if (sub) columns.push({ main: sub, sub: null })
  }
  push(tiles.writer, tiles.critic)
  push(tiles.implementer, null)
  push(tiles.lens, tiles.judge)
  return columns
})

/* ── чем запускается + подстановки ── */

/** Команда построчно: элемент argv — строка блока, подсветка считается внутри элемента. */
const commandLines = computed(() => (props.route.command ?? []).map((element) => splitPlaceholders(element)))
/** Та же команда одной строкой — для кнопки копирования рядом с заголовком секции. */
const commandText = computed(() => (props.route.command ?? []).join(' '))
const unknownNames = computed(() => unknownPlaceholders(props.route.command))

/** `{name}` — вынесено функцией: буквальные `{}` внутри `{{ }}` шаблона путают парсер Vue. */
function braced(name: string): string {
  return '{' + name + '}'
}
</script>

<template>
  <div class="listik-route-card">
    <header class="listik-route-card__head">
      <span class="listik-route-card__tile" aria-hidden="true">
        <RouteIcon :route="route" size="md" />
      </span>
      <div class="listik-route-card__head-main">
        <h2 class="listik-route-card__name"><RouteName :route="route" /></h2>
        <p class="listik-route-card__keyline">
          Конвейер · скилл <code class="listik-mono">{{ route.key }}</code>
        </p>
      </div>
      <UiSwitch
        :model-value="draft.visible"
        @update:model-value="onVisible"
      >
        В меню «Запустить»
      </UiSwitch>
    </header>

    <div class="listik-route-card__fields">
      <UiField label="Название в меню" :error="errorFor('title')">
        <UiInput
          :model-value="draft.title"
          @update:model-value="onTitle"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
      <UiField label="Подпись под названием" :error="errorFor('hint')">
        <UiInput
          :model-value="draft.hint"
          @update:model-value="onHint"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
    </div>

    <section class="listik-route-card__icon">
      <h4 class="listik-route-card__label">Иконка в списках и на карточке</h4>
      <IconToggle
        :model-value="iconValue"
        :options="ROUTE_ICON_OPTIONS"
        ariaLabel="Иконка маршрута"
        @update:model-value="onIcon"
      >
        <template #icon="{ option }">
          <span v-if="option.value === ''" class="listik-route-card__icon-none">Без иконки</span>
          <ListikIcon v-else :name="routeIconGlyph(option.value) ?? ''" size="sm" />
        </template>
      </IconToggle>
    </section>

    <section class="listik-route-card__section">
      <div class="listik-route-card__section-head">
        <h4 class="listik-route-card__section-title">Состав конвейера</h4>
      </div>
      <ol v-if="roleColumns.length > 0" class="listik-route-card__flow">
        <li v-for="column in roleColumns" :key="column.main.role" class="listik-route-card__column">
          <div
            v-for="tile in column.sub ? [column.main, column.sub] : [column.main]"
            :key="tile.role"
            :class="['listik-route-card__role', `is-${tile.role}`, { 'is-sub': tile === column.sub }]"
          >
            <span class="listik-route-card__role-stage">
              <ListikIcon :name="tile.icon" size="sm" />
              {{ tile.stageCode }}<template v-if="tile === column.sub"> · подшаг</template>
            </span>
            <span class="listik-route-card__role-title">{{ tile.title }}</span>
            <ul class="listik-route-card__role-models">
              <li v-for="(model, at) in tile.models" :key="at" class="listik-route-card__role-model">
                <HarnessIcon :harness="model.glyph" size="xs" />
                <span class="listik-route-card__role-name">{{ model.name }}</span>
                <span v-if="model.effort" class="listik-route-card__role-effort">{{ model.effort }}</span>
              </li>
            </ul>
          </div>
        </li>
      </ol>
      <UiEmptyState v-else compact title="ролей нет" />
    </section>

    <section class="listik-route-card__section">
      <div class="listik-route-card__section-head">
        <h4 class="listik-route-card__section-title">Чем запускается</h4>
        <UiCopyButton v-if="route.command" :value="commandText" label="Команда запуска">
          <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
        </UiCopyButton>
      </div>
      <p v-if="!route.command" class="listik-prose">маршрут не запускается автоматически</p>
      <template v-else>
        <!-- Внутри `pre` компилятор Vue сохраняет пробелы как есть, поэтому тут нет
             ни одного переноса строки между узлами: перенос даёт сама плитка строки
             (`display: block`), а лишний отступ шаблона утёк бы в команду на экране. -->
        <pre
          class="listik-route-card__command"
        ><span v-for="(line, index) in commandLines" :key="index" class="listik-route-card__command-line"><template
          v-for="(chunk, at) in line"
          :key="at"
        ><span v-if="chunk.type === 'text'">{{ chunk.value }}</span><span
          v-else-if="chunk.type === 'placeholder'"
          class="listik-route-card__placeholder"
        >{{ braced(chunk.value) }}</span><span
          v-else
          class="listik-route-card__placeholder listik-route-card__placeholder--unknown"
          :title="`неизвестная подстановка: ${chunk.value}`"
        >{{ braced(chunk.value) }}</span></template></span></pre>
        <p v-if="unknownNames.length > 0" class="listik-route-card__unknown">
          неизвестная подстановка: {{ unknownNames.join(', ') }}
        </p>
      </template>
      <RouteSubstitutions :route-key="route.key" :command="route.command" />
      <p class="listik-route-card__note">Команда конвейера здесь не меняется — она приходит вместе с маршрутом</p>
    </section>

    <footer class="listik-route-card__footer">
      <div class="listik-route-card__skill">
        <UiAlert v-if="route.skill_missing" tone="warning">
          <template #title>Расхождение с поставкой</template>
          <template v-if="skillRef">скила <code class="listik-mono">{{ skillRef }}</code> нет, маршрут скрыт от автора.</template>
          <template v-else>у маршрута <code class="listik-mono">{{ route.key }}</code> не задан плагин, маршрут скрыт от автора.</template>
        </UiAlert>
        <template v-else-if="route.skill_path">
          <span class="listik-route-card__skill-label">Каталог поставки на месте:</span>
          <code class="listik-mono listik-route-card__skill-path-text">{{ route.skill_path }}</code>
          <UiCopyButton :value="route.skill_path" label="Путь к каталогу">
            <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
          </UiCopyButton>
        </template>
      </div>
      <UiSaveStatus :status="status" @retry="() => schedule(true)" />
    </footer>
  </div>
</template>

<style scoped>
.listik-route-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

/* ── шапка: плитка с глифом, название с ключом, тумблер видимости ── */

.listik-route-card__head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.listik-route-card__tile {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: var(--space-8);
  height: var(--space-8);
  border-radius: var(--radius-md);
  background: var(--accent-50);
  color: var(--accent-600);
}

.listik-route-card__head-main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.listik-route-card__name {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-tight);
  color: var(--ink-1);
}

.listik-route-card__keyline {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-card__fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

/* ── иконка ── */

.listik-route-card__icon {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.listik-route-card__label {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-card__icon-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

/* ── секции ── */

.listik-route-card__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-card__section-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.listik-route-card__section-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

/* ── схема ролей (только показ): основной ряд → и подшаги под шагом ── */

.listik-route-card__flow {
  display: grid;
  grid-auto-columns: minmax(0, 1fr);
  grid-auto-flow: column;
  gap: var(--space-6);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-route-card__column {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.listik-route-card__column + .listik-route-card__column::before {
  content: '→';
  position: absolute;
  top: var(--space-6);
  left: calc(-1 * var(--space-6));
  width: var(--space-6);
  text-align: center;
  color: var(--ink-3);
}

.listik-route-card__role {
  --role-color: var(--accent-600);
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--hairline);
  border-top: 3px solid var(--role-color);
  border-radius: var(--radius-lg);
  background: var(--surface-1);
}

.listik-route-card__role.is-sub {
  border: 1px dashed var(--hairline);
  background: var(--surface-2);
}

/* Подшаг висит на шаге: короткая пунктирная связка сверху. */
.listik-route-card__role.is-sub::before {
  content: '';
  position: absolute;
  top: calc(-1 * var(--space-3) - 1px);
  left: var(--space-4);
  height: var(--space-3);
  border-left: 1px dashed var(--hairline);
}

.listik-route-card__role.is-writer,
.listik-route-card__role.is-critic {
  --role-color: var(--info-500);
}
.listik-route-card__role.is-implementer {
  --role-color: var(--success-600);
}
.listik-route-card__role.is-lens {
  --role-color: var(--warning-600);
}
.listik-route-card__role.is-judge {
  --role-color: var(--danger-600);
}

.listik-route-card__role-stage {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--role-color);
}

.listik-route-card__role-title {
  font-size: var(--text-md);
  font-weight: var(--weight-semibold);
  color: var(--ink-1);
}

.listik-route-card__role-models {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-route-card__role-model {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
  font-size: var(--text-sm);
  color: var(--ink-2);
}

.listik-route-card__role-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.listik-route-card__role-effort {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

/* ── команда ── */

/* Команда — блок, а не строка: каждый элемент argv на своей строке, длинный
   элемент переносится внутри себя, а не уезжает под горизонтальный скролл. */
.listik-route-card__command {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-md);
  background: var(--surface-2);
  font-family: var(--font-mono);
  font-size: var(--text-sm);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.listik-route-card__command-line {
  display: block;
}

.listik-route-card__placeholder {
  color: var(--accent-600);
  font-weight: var(--weight-medium);
}

.listik-route-card__placeholder--unknown {
  color: var(--danger-600);
  text-decoration: underline wavy;
}

.listik-route-card__unknown {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

.listik-route-card__note {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

/* ── нижняя полоса: каталог скила слева, статус сохранения справа ── */

.listik-route-card__footer {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-card__skill {
  display: flex;
  flex: 1 1 auto;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}

.listik-route-card__skill-label {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-route-card__skill-path-text {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-xs);
}
</style>
