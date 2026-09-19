<script setup lang="ts">
/**
 * RouteCard — карточка выбранного маршрута-конвейера во вкладке «Маршруты»
 * настроек (`RoutesSettings.vue`, правая панель). Раскладка — по макету
 * `docs/design/settings/Настройки · Маршруты · конвейер-html/Routes.dc.html`.
 *
 * Шапка — своя, а не `UiEntityHeader`: у кита в subtitle нет слота, а ключ
 * маршрута в строке «Конвейер из скила · ключ <key>» обязан быть моноширинным.
 * Остальное — из кита: `UiSwitch` (видимость), `UiField`/`UiInput`,
 * `IconToggle`, `UiBadge`, `UiEmptyState`, `UiCopyButton`, `UiSaveStatus`,
 * `UiAlert`.
 *
 * Состав ролей и команда — только показ: расклад правится через API
 * (`PATCH /api/routes/{key}`), а команду конвейера меняет его скил. Ни одного
 * поля ввода, селекта или кнопки в блоке «Состав конвейера» нет; плитки ролей —
 * обычные `div` (треб. 7).
 *
 * Автосохранение шапки: текстовые поля — debounce 600мс после последней
 * клавиши плюс сброс по потере фокуса, переключатель и иконка — сразу.
 * `flush()` шлёт диф (`draft` против `baseline`, обновлённого только по
 * ключам последнего успешного PATCH) одним `PATCH /api/routes/<key>`; запрос
 * уже в пути — новый вызов лишь помечает `queued`, а не летит вторым сразу,
 * и после ответа первого перезапускает себя тем же `flush()`, забирая самое
 * свежее значение полей. `store.patchRoute` после успеха перечитывает список,
 * поэтому тумблер сам гасит строку в списке слева.
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта: при выборе
 * другого маршрута компонент должен пересоздаться заново (свежие `draft`/
 * `baseline`), а не переиспользоваться с патчем пропа поверх недопечатанного
 * ввода.
 */
import { computed, reactive, ref } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiCopyButton,
  UiEmptyState,
  UiField,
  UiInput,
  UiSaveStatus,
  UiSwitch,
  type SaveStatusValue,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import RouteIcon from './marks/RouteIcon.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { PipelineRouteDef, RouteIconKey, RoutePatch } from '@/api/types'
import { PIPELINE_STAGES, ROUTE_ICONS } from '@/lib/dictionaries'
import { ROLE_KEYS, ROLE_STAGE, ROLE_TITLES, type RoleKey } from '@/lib/pipelines'
import { splitPlaceholders, unknownPlaceholders } from '@/lib/routes'

const props = defineProps<{ route: PipelineRouteDef }>()

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

const status = ref<SaveStatusValue>('idle')
const saveError = ref<string | null>(null)
const errorFields = ref<string[]>([])

let inFlight = false
let queued = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

function diffPatch(): RoutePatch {
  const patch: RoutePatch = {}
  if (draft.title !== baseline.title) patch.title = draft.title
  if (draft.hint !== baseline.hint) patch.hint = draft.hint
  if (draft.visible !== baseline.visible) patch.visible = draft.visible
  if (draft.icon !== baseline.icon) patch.icon = draft.icon
  return patch
}

async function flush(): Promise<void> {
  const patch = diffPatch()
  const keys = Object.keys(patch) as (keyof RoutePatch)[]
  if (keys.length === 0) return
  if (inFlight) {
    queued = true
    return
  }
  inFlight = true
  status.value = 'saving'
  const result = await store.patchRoute(props.route.key, patch)
  inFlight = false
  if (result) {
    for (const key of keys) (baseline as Record<string, unknown>)[key] = patch[key]
    saveError.value = null
    errorFields.value = []
    status.value = 'saved'
  } else {
    saveError.value = store.routesSettingsError.value
    errorFields.value = keys
    status.value = 'error'
  }
  if (queued) {
    queued = false
    await flush()
  }
}

function scheduleFlush(immediate: boolean): void {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  if (immediate) {
    void flush()
    return
  }
  debounceTimer = setTimeout(() => {
    debounceTimer = null
    void flush()
  }, 600)
}

function onTitle(value: string): void {
  draft.title = value
  scheduleFlush(false)
}
function onHint(value: string): void {
  draft.hint = value
  scheduleFlush(false)
}
function onBlurText(): void {
  scheduleFlush(true)
}
function onVisible(value: boolean): void {
  draft.visible = value
  scheduleFlush(true)
}

function errorFor(field: string): string | undefined {
  return status.value === 'error' && errorFields.value.includes(field) ? (saveError.value ?? undefined) : undefined
}

/* ── иконка: семь кнопок-глифов (шесть ROUTE_ICONS + «Без иконки») ── */

const iconOptions = computed<IconToggleOption<string>[]>(() => [
  ...ROUTE_ICONS.map((item) => ({ value: item.value as string, label: item.hint })),
  { value: '', label: 'Без иконки' },
])
const iconValue = computed(() => draft.icon ?? '')

function glyphFor(value: string): string | null {
  return ROUTE_ICONS.find((item) => item.value === value)?.icon ?? null
}

function onIcon(value: string): void {
  draft.icon = value === '' ? null : (value as RouteIconKey)
  scheduleFlush(true)
}

/* ── состав конвейера: только показ ── */

interface RoleTile {
  role: RoleKey
  /** Код этапа (`s1`…) — из `PIPELINE_STAGES` по `ROLE_STAGE`. */
  stageCode: string
  /** Подпись этапа — из `PIPELINE_STAGES`, руками не пишется. */
  stageLabel: string
  roleTitle: string
  vendor: string
}

const roleTiles = computed<RoleTile[]>(() =>
  ROLE_KEYS.filter((role) => Boolean(props.route.roles[role])).map((role) => {
    const cell = props.route.roles[role]!
    const stage = PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role])
    return {
      role,
      stageCode: stage?.code ?? '',
      stageLabel: stage?.label ?? '',
      roleTitle: ROLE_TITLES[role],
      vendor: cell.provider,
    }
  }),
)

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
        <h2 class="listik-route-card__name">{{ route.title }}</h2>
        <p class="listik-route-card__keyline">
          Конвейер из скила · ключ <code class="listik-mono">{{ route.key }}</code>
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
        :options="iconOptions"
        ariaLabel="Иконка маршрута"
        @update:model-value="onIcon"
      >
        <template #icon="{ option }">
          <span v-if="option.value === ''" class="listik-route-card__icon-none">Без иконки</span>
          <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
        </template>
      </IconToggle>
    </section>

    <section class="listik-route-card__section">
      <div class="listik-route-card__section-head">
        <h4 class="listik-route-card__section-title">Состав конвейера</h4>
        <UiBadge tone="neutral" size="sm">только показ</UiBadge>
        <span class="listik-route-card__section-note">правятся только через API, из настроек — нет</span>
      </div>
      <div v-if="roleTiles.length > 0" class="listik-route-card__roles">
        <div v-for="tile in roleTiles" :key="tile.role" class="listik-route-card__role">
          <span class="listik-route-card__role-stage">{{ tile.stageCode }} · {{ tile.stageLabel }}</span>
          <span class="listik-route-card__role-title">{{ tile.roleTitle }}</span>
          <code class="listik-mono listik-route-card__role-vendor">{{ tile.vendor }}</code>
        </div>
      </div>
      <UiEmptyState v-else compact title="ролей нет" />
    </section>

    <section class="listik-route-card__section">
      <div class="listik-route-card__section-head">
        <h4 class="listik-route-card__section-title">Чем запускается</h4>
        <UiBadge tone="neutral" size="sm">только показ</UiBadge>
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
      <p class="listik-route-card__note">Команду конвейера меняет его скил, а не настройки</p>
    </section>

    <footer class="listik-route-card__footer">
      <div class="listik-route-card__skill">
        <UiAlert v-if="route.skill_missing" tone="warning">
          <template #title>Расхождение со скилом</template>
          скила <code class="listik-mono">/feature-pipeline:{{ route.key }}</code> нет, маршрут скрыт от автора.
        </UiAlert>
        <template v-else-if="route.skill_path">
          <span class="listik-route-card__skill-label">Каталог скила на месте:</span>
          <code class="listik-mono listik-route-card__skill-path-text">{{ route.skill_path }}</code>
          <UiCopyButton :value="route.skill_path" label="Путь к скилу">
            <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
          </UiCopyButton>
        </template>
      </div>
      <UiSaveStatus :status="status" @retry="() => scheduleFlush(true)" />
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

.listik-route-card__section-note {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

/* ── плитки ролей (только показ) ── */

.listik-route-card__roles {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.listik-route-card__role {
  display: flex;
  flex: 1 1 0;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  background: var(--surface-2);
}

.listik-route-card__role-stage {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent-600);
}

.listik-route-card__role-title {
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-1);
}

.listik-route-card__role-vendor {
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
