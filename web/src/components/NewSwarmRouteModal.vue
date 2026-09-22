<script setup lang="ts">
/**
 * NewSwarmRouteModal — окно «Завести маршрут роя» раздела настроек «Маршруты»
 * (`RoutesSettings.vue`) и пикера «Новой задачи». Раскладка — по макету
 * `docs/design/settings/Окно «Завести маршрут роя»-html/NewSwarmRoute.dc.html`.
 *
 * Черновик: название, ключ (подстановка из названия, пока поле не тронуто),
 * подпись, иконка и состав этапов — у каждой роли выбор исполнителя из
 * каталога харнессов (`store.ensureHarnesses`) или «— пропустить этап».
 * Команда роли здесь не вводится: ячейки уходят `{harness}` — argv и промпт
 * роли по умолчанию берутся из карточки харнесса, а правятся потом в карточке
 * маршрута (`RouteSwarmCard`).
 *
 * Отправка — один `POST /api/routes` через `store.createRoute`
 * (`kind: 'swarm'`); успех отдаётся событием `created`, ошибка остаётся в окне
 * (`409` — с припиской про занятый ключ).
 */
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  UiAlert,
  UiField,
  UiFormModal,
  UiInput,
  UiSelect,
  UiSwitch,
  type UiSelectOption,
} from '@zoloto585/facet'
import IconToggle, { type IconToggleOption } from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import HarnessIcon from './marks/HarnessIcon.vue'
import store from '@/store/listik'
import type { RouteIconKey, SwarmRoleCell, SwarmRoles, SwarmRouteDef } from '@/api/types'
import { PIPELINE_STAGES, ROUTE_ICONS } from '@/lib/dictionaries'
import { runnableHarness } from '@/lib/harness'
import { ROLE_KEYS, ROLE_STAGE, ROLE_TITLES, type RoleKey } from '@/lib/pipelines'

const emit = defineEmits<{ created: [route: SwarmRouteDef]; close: [] }>()

/** Окно закрывается «Отменой», крестиком или Escape — сообщаем наружу, чтобы снять `v-if`. */
const open = ref(true)
watch(open, (value) => {
  if (!value) emit('close')
})

/* ── исполнители ролей: каталог харнессов ── */

onMounted(() => {
  store.ensureHarnesses()
})

const SKIP = ''

/**
 * Кандидаты исполнителей: включённые харнессы с командой `argv` — роль без
 * своей команды берёт шаблон харнесса, и если его нет (ручная выдача,
 * `kind=manual`), этап запустить нечем.
 */
const runnable = computed(() => store.harnesses.value.filter(runnableHarness))

const harnessOptions = computed<UiSelectOption<string>[]>(() => [
  { value: SKIP, label: '— пропустить этап' },
  ...runnable.value.map((item) => ({ value: item.key, label: item.label || item.key })),
])

/**
 * Исполнитель роли по умолчанию — первый харнесс каталога с командой: этапы
 * больше не ограничены списком разрешённых, claim возьмёт любой из каталога.
 * Пустой каталог — роль остаётся «пропустить этап», выбор за пользователем.
 */
function defaultHarness(_role: RoleKey): string {
  return runnable.value[0]?.key ?? SKIP
}

/**
 * Черновой выбор по ролям: ключ харнесса или `SKIP` («пропустить этап»).
 * Пока каталог не загружен, в селекте нет ни одного исполнителя — сид
 * `SKIP` держит показанное и сохраняемое значение одинаковыми; реальные
 * дефолты подставит вотчер каталога ниже.
 */
const roleHarness = reactive<Record<RoleKey, string>>({
  spec: SKIP,
  critic: SKIP,
  impl: SKIP,
  judge: SKIP,
})

/**
 * Роли, которые пользователь тронул до прихода каталога, дефолты не затирают:
 * `immediate`-вотчер сработает и на уже загруженном списке, и на догрузке —
 * но поздний ответ не должен переписывать чужой выбор (в т.ч. «пропустить»).
 */
const roleTouched = reactive<Record<RoleKey, boolean>>({
  spec: false,
  critic: false,
  impl: false,
  judge: false,
})

function onRolePick(role: RoleKey, value: string | null): void {
  roleTouched[role] = true
  roleHarness[role] = value ?? SKIP
}

/** Каталог подъехал — подставляем исполнителей по умолчанию по макету. */
let defaultsApplied = false
watch(
  () => store.harnesses.value,
  (items) => {
    if (defaultsApplied || items.length === 0) return
    defaultsApplied = true
    for (const role of ROLE_KEYS) {
      if (!roleTouched[role]) roleHarness[role] = defaultHarness(role)
    }
  },
  // `immediate`: каталог мог загрузиться до монтирования окна — тогда без него
  // первый непустой список пропустится, и роли останутся на `claude`.
  { immediate: true },
)

/* ── черновик шапки ── */

const title = ref('')
const key = ref('')
const keyTouched = ref(false)
const hint = ref('')
const icon = ref<RouteIconKey | null>('medium')
const visible = ref(true)

const KEY_RE = /^[a-z0-9][a-z0-9-]*$/

function draftKeyFromTitle(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

watch(title, (value) => {
  if (keyTouched.value) return
  const draft = draftKeyFromTitle(value)
  key.value = KEY_RE.test(draft) ? draft : ''
})

function onKeyInput(value: string): void {
  keyTouched.value = true
  key.value = value
}

/* ── проверки ── */

const titleError = computed(() => (title.value.trim() === '' ? 'название не заполнено' : null))

const keyError = computed(() => {
  const value = key.value
  if (!value) return 'ключ не заполнен'
  if (!KEY_RE.test(value)) {
    return 'ключ: строчные латинские буквы, цифры и дефис, начинается с буквы или цифры'
  }
  return null
})

const rolesError = computed(() =>
  ROLE_KEYS.every((role) => roleHarness[role] === SKIP)
    ? 'хотя бы одна роль обязательна — иначе рою нечего запускать'
    : null,
)

const submitting = ref(false)
const serverError = ref<string | null>(null)

const canSubmit = computed(
  () => !submitting.value && !titleError.value && !keyError.value && !rolesError.value,
)

const submitDisabled = computed(() => !canSubmit.value)

function syncSubmitDisabled(): void {
  const anchor = document.querySelector('.listik-new-swarm__anchor')
  const form = anchor?.closest('form') ?? null
  if (!form) return
  const button = document.querySelector<HTMLButtonElement>(`button[form="${form.id}"]`)
  if (!button) return
  if (submitDisabled.value) button.setAttribute('submit-disabled', '')
  else button.removeAttribute('submit-disabled')
}

onMounted(syncSubmitDisabled)
watch(submitDisabled, syncSubmitDisabled, { flush: 'post' })

/* ── иконка ── */

const iconOptions = computed<IconToggleOption<string>[]>(() => [
  ...ROUTE_ICONS.map((item) => ({ value: item.value as string, label: item.hint })),
  { value: '', label: 'Без иконки' },
])
const iconValue = computed(() => icon.value ?? '')

function glyphFor(value: string): string | null {
  return ROUTE_ICONS.find((item) => item.value === value)?.icon ?? null
}

function onIcon(value: string): void {
  icon.value = value === '' ? null : (value as RouteIconKey)
}

/* ── роли ── */

interface RoleRow {
  role: RoleKey
  stageCode: string
  stageLabel: string
  roleTitle: string
  note: string
}

const ROLE_NOTES: Record<RoleKey, string> = {
  spec: 'spec_path + чек-лист + порции',
  critic: 'видит только ТЗ и чек-лист',
  impl: 'код в worktree, тесты, журнал',
  judge: 'вердикт по чек-листу, коммит',
}

const roleRows = computed<RoleRow[]>(() =>
  ROLE_KEYS.map((role) => {
    const stage = PIPELINE_STAGES.find((item) => item.value === ROLE_STAGE[role])
    return {
      role,
      stageCode: stage?.code ?? '',
      stageLabel: stage?.label ?? '',
      roleTitle: ROLE_TITLES[role],
      note: ROLE_NOTES[role],
    }
  }),
)

/* ── отправка ── */

async function submit(): Promise<void> {
  if (!canSubmit.value) return
  submitting.value = true
  serverError.value = null
  const roles: SwarmRoles = {}
  for (const role of ROLE_KEYS) {
    const harness = roleHarness[role]
    roles[role] = harness === SKIP ? null : ({ harness } satisfies SwarmRoleCell)
  }
  const payload = {
    kind: 'swarm' as const,
    key: key.value,
    title: title.value.trim(),
    hint: hint.value,
    icon: icon.value,
    roles,
    visible: visible.value,
  }
  const created = await store.createRoute(payload)
  submitting.value = false
  if (!created) {
    const message = store.routesSettingsError.value ?? 'Не получилось завести маршрут'
    store.routesSettingsError.value = null
    serverError.value = store.routeCreateConflict.value
      ? `${message}\nКлюч занят — измените поле «Ключ»`
      : message
    return
  }
  emit('created', created as SwarmRouteDef)
}
</script>

<template>
  <UiFormModal
    v-model="open"
    title="Завести маршрут роя"
    submit-label="Завести маршрут"
    cancel-label="Отмена"
    :loading="submitting"
    @submit="submit"
  >
    <p class="listik-new-swarm__lead listik-new-swarm__anchor">
      Рой водит карточку по этапам сам: на каждый поднимает отдельный процесс с командой роли и
      делает claim за её харнесс. Состав выбирается прямо здесь.
    </p>

    <UiAlert v-if="serverError" tone="danger">
      <template #title>Сервер не принял</template>
      <span class="listik-new-swarm__error">{{ serverError }}</span>
    </UiAlert>

    <div class="listik-new-swarm__row">
      <UiField label="Название в меню" required :error="titleError">
        <UiInput v-model="title" placeholder="например, рой · максимум" />
      </UiField>

      <UiField
        label="Ключ"
        required
        hint="Потом его не переименовать: на него ссылаются уже запущенные задачи"
        :error="keyError"
      >
        <UiInput
          class="listik-mono"
          :model-value="key"
          placeholder="swarm-xhigh"
          @update:model-value="onKeyInput"
        />
      </UiField>
    </div>

    <div class="listik-new-swarm__row">
      <UiField label="Подпись под названием">
        <UiInput v-model="hint" placeholder="необязательно" />
      </UiField>

      <UiField label="Видимость">
        <UiSwitch v-model="visible">В меню «Запустить»</UiSwitch>
      </UiField>
    </div>

    <UiField label="Иконка">
      <IconToggle
        :model-value="iconValue"
        :options="iconOptions"
        ariaLabel="Иконка маршрута"
        @update:model-value="onIcon"
      >
        <template #icon="{ option }">
          <span v-if="option.value === ''" class="listik-new-swarm__icon-none">Без иконки</span>
          <ListikIcon v-else :name="glyphFor(option.value) ?? ''" size="sm" />
        </template>
      </IconToggle>
    </UiField>

    <UiField
      label="Состав этапов"
      required
      hint="кто исполняет роль; «пропуск» — этап не запускается"
      :error="rolesError"
    >
      <ul class="listik-new-swarm__roles">
        <li v-for="row in roleRows" :key="row.role" class="listik-new-swarm__role">
          <div class="listik-new-swarm__role-head">
            <span class="listik-new-swarm__role-stage">{{ row.stageCode }} · {{ row.stageLabel }}</span>
            <span class="listik-new-swarm__role-note">{{ row.note }}</span>
          </div>
          <div class="listik-new-swarm__role-pick">
            <HarnessIcon
              v-if="roleHarness[row.role] !== SKIP"
              :harness="roleHarness[row.role]"
              size="sm"
            />
            <UiSelect
              :model-value="roleHarness[row.role]"
              :options="harnessOptions"
              size="sm"
              :ariaLabel="`Исполнитель роли ${row.roleTitle}`"
              @update:model-value="(value: string | null) => onRolePick(row.role, value)"
            />
          </div>
        </li>
      </ul>
    </UiField>

    <p class="listik-new-swarm__note">
      Команда каждой роли — argv + промпт последним аргументом: argv по умолчанию — шаблон
      харнесса, а пустой промпт заменяется протоколом роя («готово»/«вопрос»/«не смог»).
      Всё это потом меняют в карточке маршрута. Харнессы заводятся в разделе «Харнессы».
    </p>
  </UiFormModal>
</template>

<style scoped>
.listik-new-swarm__lead {
  margin: 0;
  font-size: var(--text-sm);
  line-height: 1.5;
  color: var(--ink-3);
}

.listik-new-swarm__error {
  white-space: pre-line;
}

.listik-new-swarm__row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: var(--space-4);
}

@media (max-width: 560px) {
  .listik-new-swarm__row {
    grid-template-columns: minmax(0, 1fr);
  }
}

.listik-new-swarm__icon-none {
  font-size: var(--text-sm);
  color: var(--ink-3);
}

.listik-new-swarm__roles {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
}

.listik-new-swarm__role {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--hairline);
  border-radius: var(--radius-lg);
  background: var(--surface-2);
}

.listik-new-swarm__role-head {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
}

.listik-new-swarm__role-stage {
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--accent-600);
}

.listik-new-swarm__role-note {
  font-size: var(--text-xs);
  color: var(--ink-3);
}

.listik-new-swarm__role-pick {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.listik-new-swarm__note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.5;
  color: var(--ink-3);
}
</style>
