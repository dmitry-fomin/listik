<script setup lang="ts">
/**
 * Настройки (модалка за кнопкой «Настройки» в AppHeader.vue), вкладки
 * «Репозитории», «Оформление» (цветовая гамма — переехала из шапки) и «ФИО»:
 * «Репозитории» — то, что раньше было единственным содержимым этого файла
 * (доска показывает ровно те проекты, что лежат в таблице `projects` и не
 * скрыты, поэтому добавление/удаление репозитория — операции над проектом, а
 * не фильтр по задачам), и «ФИО» — локальная подпись автора для этого браузера,
 * сервер её не хранит и нигде не использует (нет такого поля в API).
 *
 * Добавление и правка репозитория — одно окно (`UiFormModal`, listik-zmos):
 * сценарии отличаются набором полей (в создании ещё и slug) и адресом запроса,
 * поэтому черновик и обработка ошибок у них общие, а в списке не осталось ни
 * формы добавления сверху, ни инлайновой правки строки — строка только
 * показывает проект и несёт действия.
 *
 * Репозитории умеют:
 * - добавить каталог по пути (`POST /api/projects`): slug берётся из имени
 *   каталога, но его можно задать вручную — так проект ложится в категорию
 *   (`Zoloto585/my-repo`) и рядом с уже импортированными. Ответ сервера
 *   не выбрасывается: в нём итоговый `path` и `path_adjusted_from` (каталог
 *   лежал внутри репозитория и приведён к его корню) — плашка после добавления
 *   говорит, куда именно лёг проект. Путь абсолютный, от `~` или
 *   относительный — от корня проектов, не от cwd сервера (listik-i23u);
 * - править название и путь (`PATCH /api/projects/<slug>`, listik-y32w): каталог
 *   мог переехать, а подпись проекта — смениться. Slug в форме только показан:
 *   это ключ, по которому лежат задачи, и переименования у API нет. Путь сервер
 *   принимает и несуществующий — тогда проект помечается «нет каталога»;
 * - скрыть проект с доски (`archived=1`) — задачи остаются в истории и поиске,
 *   доска просто перестаёт его показывать; вернуть обратно — тем же тумблером;
 * - убрать проект совсем. Проект с задачами сервер без `force` не удалит:
 *   сначала подтверждение, потом второе — «удалить вместе с задачами».
 *
 * Раскладка строки списка своя: в ките нет строки «репозиторий + переключатель»,
 * а UiEntityCard даёт ровно нужный размер `sm` (иконка, заголовок, мета, слот
 * действий) — из него и собрано. Кнопки/бейджи/поля/вкладки — из кита.
 *
 * `store.projectsOpen` остался прежним именем — теперь это флаг «открыта модалка
 * настроек», а не только репозиториев; отдельного рефакторинга стора под две
 * вкладки не потребовалось, поле и так булево и ни к чему из репозиториев не привязано.
 */
import { computed, ref, watch } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiConfirmDialog,
  UiEmptyState,
  UiEntityCard,
  UiField,
  UiFormModal,
  UiInput,
  UiModal,
  UiSpinner,
  UiSwitch,
  UiTabs,
  UiTooltip,
  useColorScheme,
  type ColorScheme,
  type UiTabItem,
} from '@zoloto585/facet'
import IconToggle from './IconToggle.vue'
import ListikIcon from './ListikIcon.vue'
import store from '@/store/listik'
import type { ProjectPatch, ProjectRow } from '@/api/types'
import { projectGitHint, projectIsGit, projectMetaLabel, projectTitleLabel } from '@/lib/projects'

const ADMIN_NAME_KEY = 'listik.adminName'

const action = ref<string | null>(null)
/** Ответ `POST /api/projects` последнего удачного добавления — показываем, куда лёг проект. */
const addResult = ref<ProjectRow | null>(null)
const removeTarget = ref<ProjectRow | null>(null)
const forceOpen = ref(false)

/**
 * Одно окно на оба сценария: добавление и правка отличаются только набором
 * полей и адресом запроса, поэтому черновик у них общий. `formMode === null`
 * — окно закрыто; `formTarget` заполнен только в правке.
 */
type RepoFormMode = 'create' | 'edit'
const formMode = ref<RepoFormMode | null>(null)
const formTarget = ref<ProjectRow | null>(null)
const formPath = ref('')
const formSlug = ref('')
const formTitle = ref('')
/** Ошибка сервера показывается в самом окне: вкладку за оверлеем не видно. */
const formError = ref<string | null>(null)
/** Чем кончилась правка — плашка на вкладке, когда окно уже закрыто. */
const editResult = ref<ProjectRow | null>(null)

type SettingsTab = 'repos' | 'appearance' | 'name'

const settingsTabs: UiTabItem[] = [
  { key: 'repos', label: 'Репозитории' },
  { key: 'appearance', label: 'Оформление' },
  { key: 'name', label: 'ФИО' },
]
const activeTab = ref<SettingsTab>('repos')

const { colorScheme, setColorScheme, schemes } = useColorScheme()
const schemeOptions = computed(() => schemes.map((scheme) => ({ value: scheme, label: scheme })))

const adminName = ref('')

/** Как HOLDER_KEY в TaskDrawer.vue: приватный режим/заблокированный localStorage
 *  не должны валить настройки, имя просто не сохранится между визитами. */
function loadAdminName(): void {
  try {
    adminName.value = window.localStorage.getItem(ADMIN_NAME_KEY) ?? ''
  } catch {
    /* приватный режим — поле останется пустым */
  }
}

function saveAdminName(value: string): void {
  try {
    const trimmed = value.trim()
    if (trimmed) window.localStorage.setItem(ADMIN_NAME_KEY, trimmed)
    else window.localStorage.removeItem(ADMIN_NAME_KEY)
  } catch {
    /* приватный режим — не переживёт перезагрузку, не критично */
  }
}

loadAdminName()
watch(adminName, saveAdminName)

const emit = defineEmits<{
  changed: []
  close: []
}>()

const open = computed({
  get: () => store.projectsOpen.value,
  set: (value: boolean) => {
    store.projectsOpen.value = value
  },
})

const visible = computed(() => store.projects.value.filter((project) => !project.archived))
const hidden = computed(() => store.projects.value.filter((project) => project.archived))

/** Подсказка про slug — только в режиме создания: в правке slug не меняется. */
function slugHint(): string {
  const value = formSlug.value.trim()
  if (value) return `проект ляжет в «${value}»`
  const base = formPath.value.trim().replace(/\/+$/, '').split('/').pop()
  return base ? `slug будет «${base}» (имя каталога)` : 'slug по умолчанию — имя каталога'
}

async function toggleArchived(project: ProjectRow, archived: boolean): Promise<void> {
  action.value = `archive:${project.slug}`
  const ok = await store.setProjectArchived(project.slug, archived)
  action.value = null
  if (ok) emit('changed')
}

/** Открыто ли окно формы. Закрытие во время запроса игнорируем: UiFormModal
 *  сам держит оверлей, пока `loading`, — терять введённое нельзя. */
const formOpen = computed({
  get: () => formMode.value !== null,
  set: (value: boolean) => {
    if (!value) closeForm()
  },
})

const formBusy = computed(() => action.value === 'form')

function closeForm(): void {
  if (formBusy.value) return
  formMode.value = null
  formTarget.value = null
  formError.value = null
}

/**
 * Что реально уходит в `PATCH`: только изменённые поля. Пустое название — это
 * «показывать slug» (штатное значение), а пустой путь — «не менять»: стирать
 * каталог у проекта из формы правки незачем, а промах по полю обнулил бы путь.
 */
const editPatch = computed<ProjectPatch>(() => {
  const project = formTarget.value
  if (!project) return {}
  const patch: ProjectPatch = {}
  const title = formTitle.value.trim()
  const path = formPath.value.trim()
  if (title !== (project.title ?? '')) patch.title = title
  if (path && path !== (project.path ?? '')) patch.path = path
  return patch
})

function askAdd(): void {
  formMode.value = 'create'
  formTarget.value = null
  formPath.value = ''
  formSlug.value = ''
  formTitle.value = ''
  formError.value = null
}

function askEdit(project: ProjectRow): void {
  formMode.value = 'edit'
  formTarget.value = project
  formPath.value = project.path ?? ''
  formSlug.value = project.slug
  formTitle.value = project.title ?? ''
  formError.value = null
  editResult.value = null
}

/** Кнопка отправки одна на два сценария — что делать, решает режим окна. */
function submitForm(): Promise<void> {
  if (formMode.value === 'edit') return submitEdit()
  return submitAdd()
}

async function submitAdd(): Promise<void> {
  if (formBusy.value) return
  const path = formPath.value.trim()
  if (!path) {
    // «Сохранить» в ките не блокируется — пустой путь ловим здесь, иначе
    // кнопка молчала бы, и было бы непонятно, чего форма ждёт.
    formError.value = 'Укажите путь к каталогу репозитория.'
    return
  }
  action.value = 'form'
  addResult.value = null
  editResult.value = null
  const project = await store.addProject({
    path,
    slug: formSlug.value.trim(),
    title: formTitle.value.trim(),
  })
  action.value = null
  if (project) {
    // Ответ сервера не выбрасываем: в нём итоговый путь (и path_adjusted_from,
    // если каталог привели к корню git) — пользователь должен видеть, куда попал проект.
    addResult.value = project
    closeForm()
    emit('changed')
    return
  }
  formError.value = takeStoreError('Не получилось добавить репозиторий')
}

async function submitEdit(): Promise<void> {
  const project = formTarget.value
  if (!project || formBusy.value) return
  const patch = editPatch.value
  if (Object.keys(patch).length === 0) {
    // Поля не тронули: PATCH с пустым телом ничего не сохранит, а «Сохранить»
    // не должна молчать — просто закрываем форму.
    closeForm()
    return
  }
  action.value = 'form'
  addResult.value = null
  const saved = await store.updateProject(project.slug, patch)
  action.value = null
  if (saved) {
    editResult.value = saved
    closeForm()
    emit('changed')
    return
  }
  formError.value = takeStoreError('Не получилось сохранить проект')
}

/**
 * Ошибку показывает само окно: вкладка «Репозитории» под оверлеем, и её плашка
 * была бы не видна, пока пользователь не закроет окно. В сторе текст уже
 * лежит — забираем его и гасим, чтобы не задвоить.
 */
function takeStoreError(fallback: string): string {
  const message = store.projectsError.value ?? fallback
  store.projectsError.value = null
  return message
}

function askRemove(project: ProjectRow): void {
  removeTarget.value = project
  forceOpen.value = false
}

/** Удаление: сначала обычное (сервер откажет, если есть задачи), потом — с задачами. */
async function confirmRemove(force: boolean): Promise<void> {
  const project = removeTarget.value
  if (!project) return
  action.value = `remove:${project.slug}`
  const ok = await store.removeProject(project.slug, force)
  action.value = null
  if (ok) {
    removeTarget.value = null
    forceOpen.value = false
    emit('changed')
  } else {
    // сервер объяснил, почему нельзя (409: у проекта есть задачи) —
    // предлагаем осознанный второй шаг вместо молчаливой ошибки
    forceOpen.value = true
  }
}

watch(
  () => store.projectsOpen.value,
  (value) => {
    if (!value) {
      removeTarget.value = null
      forceOpen.value = false
      addResult.value = null
      formMode.value = null
      formTarget.value = null
      formError.value = null
      editResult.value = null
      activeTab.value = 'repos'
      emit('close')
    }
  },
)
</script>

<template>
  <UiModal v-model="open" title="Настройки" size="lg">
    <UiTabs
      :model-value="activeTab"
      :tabs="settingsTabs"
      @update:model-value="(value) => (activeTab = value as SettingsTab)"
    >
      <template #panel-repos>
        <div class="listik-projects">
          <p class="listik-prose">
            Доска собирается по проектам из Listik: её колонки и фильтры видят ровно те
            репозитории, что не скрыты. Скрытие не удаляет задачи — они остаются в истории
            и в поиске, доска просто перестаёт их показывать.
          </p>

          <UiAlert v-if="store.projectsError.value" tone="warning" closable>
            <template #title>Не получилось</template>
            {{ store.projectsError.value }}
          </UiAlert>

          <div class="listik-projects__actions">
            <UiButton variant="primary" @click="askAdd">
              <template #icon><ListikIcon name="plus" size="xs" /></template>
              Добавить репозиторий
            </UiButton>
          </div>

          <UiAlert v-if="addResult" tone="success" closable @close="addResult = null">
            <template #title>
              {{ addResult.created ? 'Репозиторий добавлен' : 'Репозиторий обновлён' }}
            </template>
            Проект <code class="listik-mono">{{ addResult.slug }}</code>:
            <code class="listik-mono">{{ addResult.path ?? 'без каталога' }}</code>.
            <template v-if="addResult.path_adjusted_from">
              Исходный путь <code class="listik-mono">{{ addResult.path_adjusted_from }}</code>
              лежит внутри репозитория — приведён к его корню.
            </template>
            <template v-else-if="addResult.git">Это корень git-репозитория.</template>
          </UiAlert>

          <UiAlert
            v-if="editResult"
            :tone="editResult.path_exists === false ? 'warning' : 'success'"
            closable
            @close="editResult = null"
          >
            <template #title>
              {{ editResult.path_exists === false ? 'Сохранено, но каталога нет' : 'Репозиторий обновлён' }}
            </template>
            Проект <code class="listik-mono">{{ editResult.slug }}</code>:
            <code class="listik-mono">{{ editResult.path ?? 'без каталога' }}</code>.
            <template v-if="editResult.path_exists === false">
              Такого каталога на диске нет — проект остаётся на доске с пометкой «нет каталога».
            </template>
          </UiAlert>

          <div
            v-if="store.projectsLoading.value && store.projects.value.length === 0"
            class="listik-projects__loading"
          >
            <UiSpinner size="sm" label="Читаю репозитории" />
          </div>

          <UiEmptyState
            v-else-if="store.projects.value.length === 0"
            compact
            title="Репозиториев пока нет"
            description="Добавьте каталог проекта — он появится на доске и в фильтре «Проект»."
          >
            <template #icon><ListikIcon name="columns" size="lg" /></template>
          </UiEmptyState>

          <template v-else>
            <section class="listik-projects__list">
              <h3 class="listik-section__title">
                <ListikIcon name="columns" size="md" />
                На доске
                <UiBadge tone="neutral" size="sm">{{ visible.length }}</UiBadge>
              </h3>

              <UiEmptyState
                v-if="visible.length === 0"
                compact
                title="Все репозитории скрыты"
                description="Доска пуста: верните нужные тумблером в списке скрытых ниже."
              />

              <ul v-else class="listik-projects__rows">
                <li v-for="project in visible" :key="project.slug">
                  <UiEntityCard
                    :title="projectTitleLabel(project)"
                    size="sm"
                    :meta="projectMetaLabel(project)"
                    :loading="action === `archive:${project.slug}` || action === `remove:${project.slug}`"
                  >
                    <template #avatar>
                      <ListikIcon name="columns" size="sm" />
                    </template>
                    <template #actions>
                      <UiBadge v-if="project.path_exists === false" tone="warning" size="sm">нет каталога</UiBadge>
                      <UiTooltip v-else-if="projectIsGit(project)" :text="projectGitHint(project)">
                        <UiBadge tone="info" size="sm">git</UiBadge>
                      </UiTooltip>
                      <UiTooltip text="Убрать с доски: задачи останутся в истории и поиске">
                        <UiSwitch
                          :model-value="false"
                          v-bind="{ 'aria-label': `Скрыть проект ${project.slug}` }"
                          @update:model-value="(value: boolean) => toggleArchived(project, value)"
                        />
                      </UiTooltip>
                      <UiTooltip text="Название и путь к каталогу">
                        <UiButton
                          size="sm"
                          variant="ghost"
                          v-bind="{ 'aria-label': `Изменить проект ${project.slug}` }"
                          @click="askEdit(project)"
                        >
                          <template #icon><ListikIcon name="edit" size="xs" /></template>
                        </UiButton>
                      </UiTooltip>
                      <UiButton
                        size="sm"
                        variant="ghost"
                        v-bind="{ 'aria-label': `Удалить проект ${project.slug}` }"
                        @click="askRemove(project)"
                      >
                        <template #icon><ListikIcon name="close" size="xs" /></template>
                      </UiButton>
                    </template>
                  </UiEntityCard>
                </li>
              </ul>
            </section>

            <section v-if="hidden.length > 0" class="listik-projects__list">
              <h3 class="listik-section__title">
                <ListikIcon name="clock" size="md" />
                Скрыты с доски
                <UiBadge tone="neutral" size="sm">{{ hidden.length }}</UiBadge>
              </h3>
              <ul class="listik-projects__rows">
                <li v-for="project in hidden" :key="project.slug">
                  <UiEntityCard
                    :title="projectTitleLabel(project)"
                    size="sm"
                    :meta="projectMetaLabel(project)"
                    :loading="action === `archive:${project.slug}` || action === `remove:${project.slug}`"
                  >
                    <template #avatar>
                      <ListikIcon name="columns" size="sm" />
                    </template>
                    <template #actions>
                      <UiBadge v-if="project.path_exists === false" tone="warning" size="sm">нет каталога</UiBadge>
                      <UiTooltip text="Вернуть на доску">
                        <!-- UiSwitch отдаёт новое значение тумблера: здесь он включён,
                             клик присылает `false` — это и есть целевое `archived`.
                             Инвертировать его нельзя: вернуть проект было невозможно
                             (listik-54be). -->
                        <UiSwitch
                          :model-value="true"
                          v-bind="{ 'aria-label': `Вернуть проект ${project.slug}` }"
                          @update:model-value="(value: boolean) => toggleArchived(project, value)"
                        />
                      </UiTooltip>
                      <UiTooltip text="Название и путь к каталогу">
                        <UiButton
                          size="sm"
                          variant="ghost"
                          v-bind="{ 'aria-label': `Изменить проект ${project.slug}` }"
                          @click="askEdit(project)"
                        >
                          <template #icon><ListikIcon name="edit" size="xs" /></template>
                        </UiButton>
                      </UiTooltip>
                      <UiButton
                        size="sm"
                        variant="ghost"
                        v-bind="{ 'aria-label': `Удалить проект ${project.slug}` }"
                        @click="askRemove(project)"
                      >
                        <template #icon><ListikIcon name="close" size="xs" /></template>
                      </UiButton>
                    </template>
                  </UiEntityCard>
                </li>
              </ul>
            </section>
          </template>
        </div>
      </template>

      <template #panel-appearance>
        <div class="listik-projects__name">
          <p class="listik-prose">Цветовая гамма интерфейса — сохраняется в этом браузере.</p>
          <IconToggle
            :model-value="colorScheme"
            :options="schemeOptions"
            size="md"
            v-bind="{ 'aria-label': 'Цветовая гамма' }"
            @update:model-value="(value) => setColorScheme(value as ColorScheme)"
          >
            <template #icon="{ option }">
              <span
                class="listik-scheme-swatch"
                :style="{ background: `var(--listik-scheme-${option.value})` }"
                aria-hidden="true"
              />
            </template>
          </IconToggle>
        </div>
      </template>

      <template #panel-name>
        <div class="listik-projects__name">
          <p class="listik-prose">
            Имя для подписи — только в этом браузере (<code class="listik-mono">localStorage</code>):
            у Listik нет такого поля на сервере, и никуда, кроме этого поля, оно не уходит.
          </p>
          <UiInput
            v-model="adminName"
            placeholder="Как вас подписывать"
            v-bind="{ 'aria-label': 'ФИО' }"
          />
        </div>
      </template>
    </UiTabs>

    <template #footer>
      <UiButton variant="ghost" @click="open = false">Закрыть</UiButton>
    </template>
  </UiModal>

  <!-- Добавление и правка репозитория — одно окно вровень с настройками (тот же
       приём, что у подтверждений ниже): форма живёт, пока идёт запрос, и несёт
       свои ошибки — за оверлеем плашку вкладки не видно. Различаются только
       набор полей (в правке slug уже занят и не меняется) и адрес запроса. -->
  <UiFormModal
    v-model="formOpen"
    :title="formMode === 'edit' ? 'Изменить репозиторий' : 'Добавить репозиторий'"
    :loading="formBusy"
    :submit-label="formMode === 'edit' ? 'Сохранить' : 'Добавить'"
    cancel-label="Отмена"
    @submit="submitForm"
    @cancel="closeForm"
  >
    <p v-if="formMode === 'edit'" class="listik-prose">
      Slug <code class="listik-mono">{{ formTarget?.slug }}</code> — ключ проекта: задачи лежат
      по нему, поэтому он не меняется.
    </p>

    <UiAlert v-if="formError" tone="danger">
      <template #title>
        {{ formMode === 'edit' ? 'Не получилось сохранить' : 'Не получилось добавить' }}
      </template>
      {{ formError }}
    </UiAlert>

    <template v-if="formMode === 'create'">
      <UiField
        label="Путь к каталогу"
        required
        hint="Абсолютный, от ~ или относительный — от корня проектов, а не от каталога сервера."
      >
        <UiInput
          v-model="formPath"
          placeholder="путь к каталогу, например ~/Projects/Zoloto585/new-repo"
        />
      </UiField>

      <UiField label="Slug" :hint="slugHint()">
        <UiInput v-model="formSlug" placeholder="slug (необязательно)" />
      </UiField>

      <UiField
        label="Название"
        hint="Подпись проекта в фильтре и в знаке проекта. Пусто — показываем slug."
      >
        <UiInput v-model="formTitle" placeholder="название (необязательно)" />
      </UiField>

      <span class="listik-section__hint">
        git remote и ветка подтянутся сами, если это git-репозиторий.
        <template v-if="store.projectsRoot.value">
          · корень проектов: <code class="listik-mono">{{ store.projectsRoot.value }}</code>
        </template>
      </span>
    </template>

    <template v-else>
      <UiField
        label="Название"
        hint="Подпись проекта в фильтре и в знаке проекта. Пусто — показываем slug."
      >
        <UiInput v-model="formTitle" placeholder="название проекта" />
      </UiField>

      <UiField
        label="Путь к каталогу"
        hint="Абсолютный, от ~ или относительный — от корня проектов. Пусто — оставить прежний: стереть каталог из этой формы нельзя."
      >
        <UiInput
          v-model="formPath"
          placeholder="путь к каталогу, например ~/Projects/Zoloto585/new-repo"
        />
      </UiField>

      <span class="listik-section__hint">
        При правке каталог не проверяется: если пути нет, проект остаётся на доске с пометкой
        «нет каталога».
        <template v-if="store.projectsRoot.value">
          · корень проектов: <code class="listik-mono">{{ store.projectsRoot.value }}</code>
        </template>
      </span>
    </template>
  </UiFormModal>

  <!-- Подтверждения — сосед модалки, а не её содержимое: оба оверлея
       телепортируются в body, и вложенность ломала бы порядок и фокус. -->
  <UiConfirmDialog
    :model-value="Boolean(removeTarget) && !forceOpen"
    tone="danger"
    title="Убрать репозиторий из Listik?"
    :description="`Проект ${removeTarget?.slug ?? ''} исчезнет из настроек и с доски. Если у него есть задачи, сервер откажет: тогда их можно удалить вместе с проектом.`"
    confirm-label="Убрать"
    cancel-label="Отмена"
    :loading="action === `remove:${removeTarget?.slug ?? ''}`"
    @update:model-value="(value: boolean) => { if (!value) removeTarget = null }"
    @confirm="confirmRemove(false)"
    @cancel="removeTarget = null"
  />

  <UiConfirmDialog
    :model-value="forceOpen && Boolean(removeTarget)"
    tone="danger"
    title="Удалить вместе с задачами?"
    :description="`У проекта ${removeTarget?.slug ?? ''} ${removeTarget?.n_tasks ?? 0} задач. Они будут удалены безвозвратно — вместе с комментариями, связями и историей. Если нужна история, лучше скрыть проект с доски.`"
    confirm-label="Удалить с задачами"
    cancel-label="Оставить"
    :loading="action === `remove:${removeTarget?.slug ?? ''}`"
    @update:model-value="(value: boolean) => { if (!value) { forceOpen = false; removeTarget = null } }"
    @confirm="confirmRemove(true)"
    @cancel="forceOpen = false"
  />
</template>

<style scoped>
.listik-projects {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
  min-width: 0;
}

/* Кнопка добавления не должна растягиваться на всю ширину колонки настроек. */
.listik-projects__actions {
  display: flex;
  justify-content: flex-start;
}

.listik-projects__list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.listik-projects__rows {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 340px;
  overflow-y: auto;
}

.listik-projects__loading {
  display: flex;
  justify-content: center;
  padding: var(--space-6);
}

.listik-projects__name {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

</style>
