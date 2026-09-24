<script setup lang="ts">
/**
 * RouteSwarmCard — карточка маршрута `kind=swarm` (и конвейера с
 * `driver=swarm`) во вкладке «Маршруты» настроек (`RoutesSettings.vue`,
 * правая панель). Раскладка — по макету
 * `docs/design/settings/Настройки · Маршруты · рой-html/RoutesSwarm.dc.html`.
 *
 * Отличие от карточки конвейера: «Состав конвейера» редактируемый — у каждой
 * роли `UiSelect` исполнителя из каталога харнессов (`store.ensureHarnesses`)
 * или «— пропуск» (этап не запускается, ячейка `null`). Клик по плитке роли
 * раскрывает её команду: список argv (`UiRecordList`) и промпт — как у
 * прямой выдачи, но по роли. Пустой argv у роли — наследование команды по
 * умолчанию из карточки харнесса.
 *
 * Сохранение автоматическое, как у остальных карточек: текст — через 600мс и
 * по blur, тумблер/иконка/выбор исполнителя — сразу; в `PATCH` уходит диф
 * (`title|hint|visible|icon|roles`) одним запросом. Роли уходят только целиком
 * валидными: ячейка с незнакомой подстановкой или пустым аргументом держит
 * весь `roles` у себя, причина стоит в нижней полосе, а об уходе с такой
 * правкой спрашивает сторож `RoutesSettings` (`update:dirty`).
 *
 * `:key="route.key"` у вызывающей стороны — часть контракта: другой маршрут =
 * заново созданная карточка со свежим черновиком.
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  UiAlert,
  UiCopyButton,
  UiField,
  UiInput,
  UiRecordList,
  UiSaveStatus,
  UiSelect,
  UiSwitch,
  UiTextarea,
  type SaveStatusValue,
  type UiRecordListColumn,
  type UiSelectOption,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import RouteIcon from './marks/RouteIcon.vue'
import RouteCommandText from './RouteCommandText.vue'
import RouteSubstitutions from './RouteSubstitutions.vue'
import store from '@/store/listik'
import type { RouteIconKey, RoutePatch, SwarmLikeRoute, SwarmRoles } from '@/api/types'
import { PIPELINE_STAGES, ROUTE_ICONS } from '@/lib/dictionaries'
import { harnessTitle, runnableHarness } from '@/lib/harness'
import { isSwarmCell, ROLE_KEYS, ROLE_STAGE, ROLE_TITLES, type RoleKey } from '@/lib/pipelines'
import {
  braced,
  commandProblemText,
  previewChunks,
  previewCommand,
  unknownPlaceholders,
  type PlaceholderChunk,
} from '@/lib/routes'

const props = defineProps<{ route: SwarmLikeRoute }>()
const emit = defineEmits<{ 'update:dirty': [value: boolean] }>()

/* ── черновик: шапка + расклад ролей ── */

interface HeaderDraft {
  title: string
  hint: string
  visible: boolean
  icon: RouteIconKey | null
}

/** Черновик одной роли: `null` — этап пропускается. */
interface RoleDraft {
  /** Ключ харнесса из каталога; `null` внутри RoleDraft не бывает — пропуск это `null` у всей роли. */
  harness: string
  /** Свой argv; `null` — роль берёт команду по умолчанию харнесса. */
  argv: string[] | null
  prompt: string
}

interface ArgRow extends Record<string, unknown> {
  id: string
  value: string
}

let rowSeq = 0
function argRowsOf(values: string[]): ArgRow[] {
  return values.map((value) => ({ id: `arg-${(rowSeq += 1)}`, value }))
}

function headerOf(route: SwarmLikeRoute): HeaderDraft {
  return { title: route.title, hint: route.hint, visible: route.visible, icon: route.icon ?? null }
}

function roleDraftOf(cell: unknown): RoleDraft | null {
  // Provider-ячейка под driver=swarm сервер не примет — читаем как пропуск.
  if (!isSwarmCell(cell)) return null
  return {
    harness: cell.harness,
    argv: cell.argv ? [...cell.argv] : null,
    prompt: cell.prompt ?? '',
  }
}

type RolesDraft = Record<RoleKey, RoleDraft | null>

function rolesOf(route: SwarmLikeRoute): RolesDraft {
  return {
    spec: roleDraftOf(route.roles.spec),
    critic: roleDraftOf(route.roles.critic),
    impl: roleDraftOf(route.roles.impl),
    judge: roleDraftOf(route.roles.judge),
  }
}

const draft = reactive<HeaderDraft>(headerOf(props.route))
const roles = reactive<RolesDraft>(rolesOf(props.route))

/** Последнее известное серверу состояние: с ним сравнивается черновик. */
const baseline = reactive<HeaderDraft & { roles: string }>({
  ...headerOf(props.route),
  roles: rolesJson(rolesOf(props.route)),
})

/**
 * Каноническая форма ролей для сравнения и отправки: пропуск — `null`,
 * пустой argv/`prompt` из ячейки не уходят (их отсутствие и есть «наследуй
 * харнесс» / «промпта нет»). Порядок ключей фиксирован — JSON сравнивается
 * строкой.
 */
function rolesJson(source: RolesDraft): string {
  const out: Record<string, unknown> = {}
  for (const role of ROLE_KEYS) {
    const cell = source[role]
    if (!cell) {
      out[role] = null
      continue
    }
    const entry: Record<string, unknown> = { harness: cell.harness }
    const argv = cell.argv?.filter((value) => value.trim() !== '')
    if (argv && argv.length > 0) entry.argv = argv
    if (cell.prompt.trim() !== '') entry.prompt = cell.prompt
    out[role] = entry
  }
  return JSON.stringify(out)
}

const status = ref<SaveStatusValue>('idle')
const saveError = ref<string | null>(null)

/** Роль, чья команда раскрыта ниже списка; пропущенная — без редактора. */
const openRole = ref<RoleKey>(ROLE_KEYS.find((role) => roles[role]) ?? 'spec')

function resetDraft(route: SwarmLikeRoute): void {
  const header = headerOf(route)
  draft.title = header.title
  draft.hint = header.hint
  draft.visible = header.visible
  draft.icon = header.icon
  const fresh = rolesOf(route)
  for (const role of ROLE_KEYS) roles[role] = fresh[role]
  baseline.title = header.title
  baseline.hint = header.hint
  baseline.visible = header.visible
  baseline.icon = header.icon
  baseline.roles = rolesJson(fresh)
  saveError.value = null
  status.value = 'idle'
}

/**
 * Запись пришла заново (`store.reloadRoutes` после любой правки вкладки) —
 * забираем серверные значения, но только пока автор ничего не набрал.
 */
watch(
  () => props.route,
  (route) => {
    if (!dirty.value) resetDraft(route)
  },
)

/* ── исполнители ролей: каталог харнессов + «— пропуск» ── */

onMounted(() => {
  store.ensureHarnesses()
})

/**
 * Выбор исполнителя — включённые записи каталога с командой (`argv`): роль без
 * своей команды берёт команду харнесса, и если её у харнесса нет (ручная
 * выдача), этап запустить нечем — в выбор такой ключ не входит. Ключ, уже
 * занятый ролью, добавляется всегда (выключенный харнесс не должен прятать
 * занятую им роль).
 */
const harnessOptions = computed<UiSelectOption<string>[]>(() => {
  const used = new Set(
    ROLE_KEYS.map((role) => roles[role]?.harness).filter((key): key is string => Boolean(key)),
  )
  const options: UiSelectOption<string>[] = [{ value: '', label: '— пропуск' }]
  for (const item of store.harnesses.value) {
    if (!runnableHarness(item) && !used.has(item.key)) continue
    options.push({ value: item.key, label: item.label || item.key })
  }
  return options
})

function roleHarness(role: RoleKey): string {
  return roles[role]?.harness ?? ''
}

function onRoleHarness(role: RoleKey, value: string | null): void {
  if (!value) {
    roles[role] = null
  } else {
    const previous = roles[role]
    roles[role] = { harness: value, argv: previous?.argv ?? null, prompt: previous?.prompt ?? '' }
  }
  scheduleFlush(true)
}

/** Аргументы раскрытой роли — `UiRecordList` живёт по строкам с устойчивым id. */
const roleArgs = ref<ArgRow[]>([])
const rolePrompt = ref('')

/** Ключ argv-черновика раскрытой роли — при смене роли список перечитывается. */
const openRoleKey = ref<string>('')

function syncRoleEditor(): void {
  const cell = roles[openRole.value]
  roleArgs.value = argRowsOf(cell?.argv ?? [])
  rolePrompt.value = cell?.prompt ?? ''
  openRoleKey.value = openRole.value
}

watch(openRole, syncRoleEditor, { immediate: true })

/**
 * Правки раскрытой роли пишутся в её черновик (и дальше в общий дебаунс):
 * пустой список аргументов — `null` («команда харнесса по умолчанию»), а не
 * пустой argv — сервер пустой массив не отличит от «наследуй», а различие
 * нужно карточке для подписи «как у харнесса».
 */
watch([roleArgs, rolePrompt], () => {
  const cell = roles[openRole.value]
  if (!cell || openRoleKey.value !== openRole.value) return
  const argv = roleArgs.value.map((row) => row.value)
  cell.argv = argv.length > 0 ? argv : null
  cell.prompt = rolePrompt.value
  scheduleFlush(false)
}, { deep: true })

/* ── проверки ролей: команда уходит на сервер только годная ── */

/** Причины по строкам раскрытой роли (`null` — строка годится). */
const roleArgProblems = computed(() => roleArgs.value.map((row) => commandProblemText(row.value)))
const rolePromptProblem = computed(() => commandProblemText(rolePrompt.value))
const roleEmptyArg = computed(() => roleArgs.value.some((row) => row.value.trim() === ''))

/** Почему раскрытая роль не уйдёт в `PATCH` — текст под её редактором. */
const roleBlock = computed<string | null>(() => {
  if (openRoleKey.value !== openRole.value) return null
  if (!roles[openRole.value]) return null
  if (roleEmptyArg.value) return 'пустой аргумент не сохранится: заполни строку или удали её'
  return roleArgProblems.value.find((problem) => problem !== null) ?? rolePromptProblem.value
})

const rolesChanged = computed(() => rolesJson(roles) !== baseline.roles)

/** `visible` сюда не входит: тумблер сохраняется сразу своим `PATCH`. */
const headerChanged = computed(
  () =>
    draft.title !== baseline.title ||
    draft.hint !== baseline.hint ||
    draft.icon !== baseline.icon,
)

const dirty = computed(() => headerChanged.value || rolesChanged.value)

/** Все четыре роли пропущены — сервер такой расклад не примет. */
const rolesAllSkipped = computed(() => ROLE_KEYS.every((role) => !roles[role]))

/**
 * Что реально потеряется при уходе: изменённый расклад, который на сервер не
 * уйдёт (все роли сняты или в команде ошибка) — им кормится сторож ухода.
 */
const unsaved = computed(() => rolesChanged.value && (rolesAllSkipped.value || roleBlock.value !== null))
watch(unsaved, (value) => emit('update:dirty', value), { immediate: true })

/* ── автосохранение: тот же порядок, что у карточки прямой выдачи ── */

let inFlight = false
let queued = false
let debounceTimer: ReturnType<typeof setTimeout> | null = null

function diffPatch(): RoutePatch {
  const patch: RoutePatch = {}
  if (draft.title !== baseline.title) patch.title = draft.title
  if (draft.hint !== baseline.hint) patch.hint = draft.hint
  if (draft.visible !== baseline.visible) patch.visible = draft.visible
  if (draft.icon !== baseline.icon) patch.icon = draft.icon
  if (rolesChanged.value && !rolesAllSkipped.value && roleBlock.value === null) {
    patch.roles = JSON.parse(rolesJson(roles)) as SwarmRoles
  }
  return patch
}

async function flush(): Promise<void> {
  const patch = diffPatch()
  if (Object.keys(patch).length === 0) return
  if (inFlight) {
    queued = true
    return
  }
  inFlight = true
  status.value = 'saving'
  const result = await store.patchRoute(props.route.key, patch)
  inFlight = false
  if (result) {
    if (patch.title !== undefined) baseline.title = patch.title
    if (patch.hint !== undefined) baseline.hint = patch.hint
    if (patch.visible !== undefined) baseline.visible = patch.visible
    if (patch.icon !== undefined) patch.icon === null ? (baseline.icon = null) : (baseline.icon = patch.icon)
    if (patch.roles !== undefined) baseline.roles = rolesJson(roles)
    saveError.value = null
    status.value = 'saved'
  } else {
    saveError.value = store.routesSettingsError.value
    status.value = 'error'
    if (patch.visible !== undefined) draft.visible = baseline.visible
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
  if (value === draft.visible) return
  draft.visible = value
  scheduleFlush(true)
}

/* Карточку сняли — досохраняем то, что ещё лежит в дебаунсе. */
onBeforeUnmount(() => {
  if (debounceTimer) {
    clearTimeout(debounceTimer)
    debounceTimer = null
  }
  void flush()
})

/* ── иконка: семь кнопок-глифов, как у остальных карточек ── */

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

/* ── список ролей и редактор раскрытой ── */

interface RoleTile {
  role: RoleKey
  stageCode: string
  stageLabel: string
  roleTitle: string
  /** Харнесс исполнителя; `null` — этап пропускается. */
  harness: string | null
  /** Команда одной строкой под плиткой: свой argv, а без него — команда харнесса. */
  commandLine: string
  /** Подпись пропущенной роли: между какими этапами проскочит карточка. */
  skipNote: string
}

function stageCodeOf(role: RoleKey): string {
  return PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role])?.code ?? ''
}

const roleTiles = computed<RoleTile[]>(() =>
  ROLE_KEYS.map((role, index) => {
    const cell = roles[role]
    const stage = PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role])
    const harness = cell
      ? store.harnesses.value.find((item) => item.key === cell.harness)
      : undefined
    const argv = cell?.argv ?? harness?.argv
    let skipNote = 'этап пропускается — карточка идёт дальше'
    if (!cell) {
      const prev = [...ROLE_KEYS.slice(0, index)].reverse().find((item) => roles[item])
      const next = ROLE_KEYS.slice(index + 1).find((item) => roles[item])
      if (prev && next) {
        skipNote = `этап пропускается — из ${stageCodeOf(prev)} карточка сразу идёт на ${stageCodeOf(next)}`
      } else if (next) {
        skipNote = `этап пропускается — карточка сразу идёт на ${stageCodeOf(next)}`
      } else if (prev) {
        skipNote = `этап пропускается — после ${stageCodeOf(prev)} карточка закрывается`
      }
    }
    return {
      role,
      stageCode: stage?.code ?? '',
      stageLabel: stage?.label ?? '',
      roleTitle: ROLE_TITLES[role],
      harness: cell?.harness ?? null,
      commandLine: argv && argv.length > 0 ? previewCommand(argv, props.route.key) : '',
      skipNote,
    }
  }),
)

function selectRole(role: RoleKey): void {
  openRole.value = role
}

const argColumns: UiRecordListColumn[] = [{ key: 'value', label: 'Аргумент', type: 'custom' }]

function createArgRow(): ArgRow {
  return { id: `arg-${(rowSeq += 1)}`, value: '' }
}

function rowKeyOf(row: ArgRow): string {
  return row.id
}

function hasBraces(value: string): boolean {
  return value.includes('{') || value.includes('}')
}

/**
 * Элементы команды раскрытой роли: свой argv или шаблон харнесса + промпт
 * последним аргументом. Пустой промпт — лаунчер сам добавляет протокол роя
 * (`SWARM_PROMPT` приходит в `GET /api/harnesses` → `store.swarmPrompt`),
 * промпт харнесса в рой не наследуется (`stage_launch._cell_prompt`).
 */
const roleCommandParts = computed<string[]>(() => {
  const cell = roles[openRole.value]
  if (!cell) return []
  const harness = store.harnesses.value.find((item) => item.key === cell.harness)
  const argv = cell.argv ?? harness?.argv ?? []
  const prompt = cell.prompt.trim() !== '' ? cell.prompt : store.swarmPrompt.value
  return prompt !== '' ? [...argv, prompt] : argv
})

/** Та же команда одной строкой — её забирает кнопка копирования у заголовка секции. */
const roleCommandPreview = computed(() => previewCommand(roleCommandParts.value, props.route.key))

/**
 * Предпросмотр кусками для подсветки, как в блоке «Чем запускается» карточки
 * конвейера: места подстановок (уже примерные значения) — акцентом, незнакомое
 * `{имя}` — красным с волной. Элементы соединены пробелом — как в команде.
 */
const rolePreviewChunks = computed<PlaceholderChunk[]>(() => {
  const chunks: PlaceholderChunk[] = []
  roleCommandParts.value.forEach((element, index) => {
    if (index > 0) chunks.push({ type: 'text', value: ' ' })
    chunks.push(...previewChunks(element, props.route.key))
  })
  return chunks
})

const roleUnknownNames = computed(() => unknownPlaceholders(roleCommandParts.value))

/** Своей команды у роли нет — под редактором пишется, откуда она берётся. */
const roleInherits = computed(() => {
  const cell = roles[openRole.value]
  return Boolean(cell) && cell!.argv === null
})
</script>

<template>
  <div class="listik-route-swarm">
    <header class="listik-route-swarm__head">
      <span class="listik-route-swarm__tile" aria-hidden="true">
        <RouteIcon :route="route" size="md" />
      </span>
      <div class="listik-route-swarm__head-main">
        <h2 class="listik-route-swarm__name">{{ route.title }}</h2>
        <p class="listik-route-swarm__keyline">
          Маршрут роя · ключ <code class="listik-mono">{{ route.key }}</code> · этапы запускает Listik
        </p>
      </div>
      <UiSwitch :model-value="draft.visible" @update:model-value="onVisible">В меню «Запустить»</UiSwitch>
    </header>

    <div class="listik-route-swarm__fields">
      <UiField label="Название в меню">
        <UiInput
          :model-value="draft.title"
          @update:model-value="onTitle"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
      <UiField label="Подпись под названием">
        <UiInput
          :model-value="draft.hint"
          @update:model-value="onHint"
          v-bind="{ onBlur: onBlurText }"
        />
      </UiField>
    </div>

    <section class="listik-route-swarm__icon">
      <h4 class="listik-route-swarm__label">Иконка в списках и на карточке</h4>
      <IconToggle
        :model-value="iconValue"
        :options="iconOptions"
        ariaLabel="Иконка маршрута"
        @update:model-value="onIcon"
      >
        <template #icon="{ option }">
          <span v-if="option.value === ''" class="listik-route-swarm__icon-none">Без иконки</span>
          <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
        </template>
      </IconToggle>
    </section>

    <UiAlert v-if="saveError" tone="danger" closable @close="saveError = null">
      <template #title>Сервер не принял правку</template>
      {{ saveError }}
    </UiAlert>

    <section class="listik-route-swarm__section">
      <div class="listik-route-swarm__section-head">
        <h4 class="listik-route-swarm__section-title">Состав конвейера</h4>
        <span class="listik-route-swarm__section-note">
          клик по этапу раскрывает его команду; «пропуск» — этап не запускается, карточка идёт дальше
        </span>
      </div>
      <ol class="listik-route-swarm__roles">
        <li
          v-for="tile in roleTiles"
          :key="tile.role"
          class="listik-route-swarm__role"
          :class="{ 'is-open': tile.role === openRole, 'is-skip': !tile.harness }"
        >
          <button
            type="button"
            class="listik-route-swarm__role-head"
            @click="selectRole(tile.role)"
          >
            <span class="listik-route-swarm__role-stage">{{ tile.stageCode }} · {{ tile.stageLabel }}</span>
          </button>
          <div class="listik-route-swarm__role-pick">
            <HarnessIcon v-if="tile.harness" :harness="tile.harness" size="sm" />
            <UiSelect
              class="listik-route-swarm__role-select"
              :model-value="roleHarness(tile.role)"
              :options="harnessOptions"
              size="sm"
              :ariaLabel="`Исполнитель роли ${tile.roleTitle}`"
              @update:model-value="(value) => onRoleHarness(tile.role, value)"
            />
          </div>
          <p v-if="!tile.harness" class="listik-route-swarm__role-skip">
            {{ tile.skipNote }}
          </p>
          <p v-else class="listik-mono listik-route-swarm__role-cmd">
            {{ tile.commandLine || '—' }}
          </p>
        </li>
      </ol>
      <p class="listik-route-swarm__hint">
        Хотя бы одна роль обязательна — иначе рою нечего запускать. Без s1 эпик не нарежется на
        порции: он пройдёт этапы целиком.
      </p>
    </section>

    <template v-if="roles[openRole]">
      <section class="listik-route-swarm__section">
        <div class="listik-route-swarm__section-head">
          <h4 class="listik-route-swarm__section-title">
            Команда роли · {{ roleTiles.find((tile) => tile.role === openRole)?.stageCode }} ·
            {{ harnessTitle(roles[openRole]!.harness) }}
          </h4>
          <span class="listik-route-swarm__section-note">
            по одному аргументу в строке — кавычки не нужны
          </span>
        </div>
        <UiRecordList
          v-model="roleArgs"
          :columns="argColumns"
          :row-key="rowKeyOf"
          :create-row="createArgRow"
          add-label="Добавить аргумент"
          empty-title="Своих аргументов нет — команда харнесса по умолчанию"
        >
          <template #cell-value="{ row, index, update }">
            <div class="listik-route-swarm__arg">
              <UiInput
                class="listik-route-swarm__arg-input"
                size="sm"
                :model-value="row.value"
                :invalid="Boolean(roleArgProblems[index])"
                @update:model-value="(value: string) => update(value)"
              />
              <p v-if="hasBraces(row.value)" class="listik-mono listik-route-swarm__echo">
                <RouteCommandText :text="row.value" />
              </p>
              <p v-if="roleArgProblems[index]" class="listik-route-swarm__problem">
                {{ roleArgProblems[index] }}
              </p>
            </div>
          </template>
        </UiRecordList>
        <p v-if="roleInherits" class="listik-route-swarm__hint">
          Свой команды у роли нет: этап запустится командой по умолчанию харнесса
          «{{ harnessTitle(roles[openRole]!.harness) }}». Набранные аргументы заменят её целиком.
        </p>
      </section>

      <section class="listik-route-swarm__section">
        <h4 class="listik-route-swarm__section-title">Промпт этапа — последний аргумент</h4>
        <UiTextarea
          v-model="rolePrompt"
          class="listik-route-swarm__prompt"
          :rows="4"
          :invalid="Boolean(rolePromptProblem)"
          placeholder="пусто — протокол роя («готово»/«вопрос»/«не смог»)"
        />
        <p v-if="rolePromptProblem" class="listik-route-swarm__problem">{{ rolePromptProblem }}</p>
        <RouteSubstitutions :route-key="route.key" :command="roleArgs.map((row) => row.value)" />
      </section>

      <section class="listik-route-swarm__section">
        <div class="listik-route-swarm__section-head">
          <h4 class="listik-route-swarm__section-title">Что выполнится на этапе</h4>
          <UiCopyButton v-if="roleCommandPreview" :value="roleCommandPreview" label="Команда этапа">
            <template #icon="{ copied }"><ListikIcon :name="copied ? 'check' : 'copy'" size="sm" /></template>
          </UiCopyButton>
        </div>
        <!-- Внутри `pre` нет ни одного переноса строки между узлами, как в блоке
             «Чем запускается» карточки конвейера: отступ шаблона утёк бы в
             команду на экране. -->
        <pre
          class="listik-mono listik-route-swarm__preview"
        ><span v-if="rolePreviewChunks.length === 0">—</span><template
          v-for="(chunk, at) in rolePreviewChunks"
          :key="at"
        ><span v-if="chunk.type === 'text'">{{ chunk.value }}</span><span
          v-else-if="chunk.type === 'placeholder'"
          class="listik-route-swarm__placeholder"
        >{{ chunk.value }}</span><span
          v-else
          class="listik-route-swarm__placeholder listik-route-swarm__placeholder--unknown"
          :title="`неизвестная подстановка: ${chunk.value}`"
        >{{ braced(chunk.value) }}</span></template></pre>
        <p v-if="roleUnknownNames.length > 0" class="listik-route-swarm__problem">
          неизвестная подстановка: {{ roleUnknownNames.join(', ') }}
        </p>
      </section>
    </template>

    <UiAlert tone="info">
      На старте этапа Listik сам делает <code class="listik-mono">claim</code> за харнесс роли —
      харнесс <code class="listik-mono">claim</code>, <code class="listik-mono">release</code> и
      <code class="listik-mono">stage</code> не вызывает. Сдача — последняя строка вывода «готово», вопрос и правки — строками выше; красный
      вердикт судьи возвращает карточку исполнителю.
    </UiAlert>

    <div class="listik-route-swarm__actions">
      <span class="listik-route-swarm__actions-note">
        Правки применятся к следующему запуску. Уже запущенные задачи не трогаются.
      </span>
      <UiSaveStatus :status="status" @retry="() => scheduleFlush(true)" />
      <p v-if="rolesAllSkipped" class="listik-route-swarm__reason is-problem">
        все роли пропущены — такой расклад сервер не примет
      </p>
      <p v-else-if="roleBlock" class="listik-route-swarm__reason is-problem">{{ roleBlock }}</p>
    </div>
  </div>
</template>

<style scoped>
.listik-route-swarm {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

/* ── шапка: плитка с глифом, название с ключом, тумблер видимости ── */

.listik-route-swarm__head {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.listik-route-swarm__tile {
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

.listik-route-swarm__head-main {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
}

.listik-route-swarm__name {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-tight);
  color: var(--ink-1);
}

.listik-route-swarm__keyline {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-swarm__fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

/* ── иконка ── */

.listik-route-swarm__icon {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.listik-route-swarm__label {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-swarm__icon-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

/* ── секции ── */

.listik-route-swarm__section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-swarm__section-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.listik-route-swarm__section-title {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: var(--weight-medium);
  color: var(--ink-2);
}

.listik-route-swarm__section-note {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

/* ── плитки ролей (макет `.roles`/`.role`): выбор исполнителя + пропуск ── */

.listik-route-swarm__roles {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-route-swarm__role {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding: var(--space-3);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  background: var(--surface-2);
}

.listik-route-swarm__role.is-open {
  border-color: var(--accent-500);
  box-shadow: var(--focus-ring);
}

/* Пропущенная роль — пунктирной плиткой без фона, как `.role--skip` в макете. */
.listik-route-swarm__role.is-skip {
  border-style: dashed;
  background: transparent;
}

.listik-route-swarm__role-head {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 6px;
  padding: 0;
  border: none;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;
}

.listik-route-swarm__role-head:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.listik-route-swarm__role-stage {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent-600);
}

.listik-route-swarm__role.is-skip .listik-route-swarm__role-stage {
  color: var(--ink-4);
}

.listik-route-swarm__role-pick {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: 2px;
}

.listik-route-swarm__role-select {
  flex: 1 1 auto;
  min-width: 0;
}

.listik-route-swarm__role-skip {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-4);
}

.listik-route-swarm__role-cmd {
  margin: 0;
  font-size: 11px;
  color: var(--ink-3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ── редактор команды роли ── */

.listik-route-swarm__arg {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
  width: 100%;
  min-width: 0;
}

.listik-route-swarm__arg :deep(.ui-input) {
  min-width: 0;
}

.listik-route-swarm__echo {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
  overflow-wrap: anywhere;
}

.listik-route-swarm__problem {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--danger-600);
}

.listik-route-swarm__hint {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-route-swarm__preview {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--surface-2);
  font-size: var(--text-sm);
  max-height: 12em;
  overflow-y: auto;
  overflow-wrap: break-word;
  white-space: pre-wrap;
}

/* Подсветка подстановок — та же пара стилей, что у `__placeholder` карточки
   конвейера: допустимая — акцентом, незнакомая — красным с волной. */
.listik-route-swarm__placeholder {
  color: var(--accent-600);
  font-weight: var(--weight-medium);
}

.listik-route-swarm__placeholder--unknown {
  color: var(--danger-600);
  text-decoration: underline wavy;
}

/* ── нижняя полоса: заметка + статус автосохранения + причина ── */

.listik-route-swarm__actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--hairline);
}

.listik-route-swarm__actions-note {
  flex: 1 1 auto;
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-route-swarm__reason {
  flex-basis: 100%;
  margin: 0;
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-route-swarm__reason.is-problem {
  color: var(--danger-600);
}
</style>
