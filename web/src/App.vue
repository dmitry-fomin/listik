<script setup lang="ts">
/**
 * Экран доски Listik: шапка, вкладки видов, инбокс «Нужен ты», тулбар с чипами
 * здоровья и компактными глобальными фильтрами, один из четырёх видов (доска /
 * список / метрики), панель задачи справа, поиск (Cmd/Ctrl+K) и
 * состояния «сервер недоступен» / «нужен токен».
 *
 * Вся композиция — из компонентов кита; своё только то, чего в ките нет:
 * колонки канбана, карточка задачи, инбокс «нужен ты», тулбар, раскладка страницы.
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiButton,
  UiContainer,
  UiInput,
  UiModal,
  UiSaveStatus,
  UiTabs,
  UiToast,
  useToast,
  type SaveStatusValue,
  type UiTabItem,
} from '@zoloto585/facet'
import AppHeader from '@/components/AppHeader.vue'
import BoardToolbar from '@/components/BoardToolbar.vue'
import NeedsYouStrip from '@/components/NeedsYouStrip.vue'
import NewTaskModal from '@/components/NewTaskModal.vue'
import ProjectSettings from '@/components/ProjectSettings.vue'
import SearchPanel from '@/components/SearchPanel.vue'
import TaskDrawer from '@/components/TaskDrawer.vue'
import PhoneTaskSheet from '@/components/PhoneTaskSheet.vue'
import BoardView from '@/views/BoardView.vue'
import ListView from '@/views/ListView.vue'
import MetricsView from '@/views/MetricsView.vue'
import MobileTaskList from '@/components/MobileTaskList.vue'
import PhoneQueue from '@/components/PhoneQueue.vue'
import store, { type ViewKey } from '@/store/listik'
import { actorList } from '@/lib/facets'
import type { CommentKind, Task, TaskPatch } from '@/api/types'
import { formatTime, tasksCountLabel } from '@/lib/format'
import { useIsPhone } from '@/lib/viewport'

const toast = useToast()

const tokenInput = ref('')
const tokenError = ref<string | null>(null)
const drawerRef = ref<InstanceType<typeof TaskDrawer> | null>(null)
const drawerOpen = ref(false)

const createOpen = ref(false)

const tabs = computed<UiTabItem[]>(() => [
  { key: 'board', label: `Доска (${store.counts.value.total})` },
  { key: 'list', label: 'Список' },
  { key: 'metrics', label: 'Метрики' },
])

const saveStatus = computed<SaveStatusValue>(() => {
  if (store.pending.value || store.loading.value) return 'saving'
  if (store.lastError.value) return 'error'
  if (store.lastSyncAt.value) return 'saved'
  return 'idle'
})

const saveAt = computed(() =>
  store.lastSyncAt.value ? formatTime(store.lastSyncAt.value) : undefined,
)

async function refreshAll(): Promise<void> {
  await store.refresh()
  if (store.lastError.value) toast.danger(store.lastError.value)
  else toast.success('Данные обновлены')
}

/** Открывает панель и ставит фокус в строку журнала — только когда карточка
 *  задачи уже отрисована (иначе поле ввода ещё не существует в DOM). */
async function openTaskWithComment(task: Task, mode: 'comment' | 'answer' = 'comment'): Promise<void> {
  await store.openTask(task.id)
  await nextTick()
  if (!drawerOpen.value || store.detailLoading.value || store.detailError.value || store.detail.value?.id !== task.id) return
  if (mode === 'answer') drawerRef.value?.focusAnswer()
  else drawerRef.value?.focusComment()
}

async function releaseFromInbox(task: Task): Promise<void> {
  const ok = await store.releaseTask(task.id)
  if (ok) toast.success('Задача освобождена')
  else toast.danger(store.lastError.value ?? 'Не удалось освободить задачу')
}

function submitToken(): void {
  const value = tokenInput.value.trim()
  if (!value) {
    tokenError.value = 'Введите токен из config.toml (корень Listik), секция [auth] token'
    return
  }
  tokenError.value = null
  store.setToken(value)
  tokenInput.value = ''
}

async function createTask(body: Record<string, unknown>): Promise<void> {
  const ok = await store.createTask(body)
  if (ok) {
    toast.success('Задача создана')
    createOpen.value = false
  } else {
    toast.danger(store.lastError.value ?? 'Не удалось создать задачу')
  }
}

// ── запросы панели задачи ────────────────────────────────────────────────

async function onPatch(payload: { id: string; body: Record<string, unknown>; label: string }): Promise<void> {
  const ok = await store.patchTask(payload.id, payload.body as TaskPatch, payload.label)
  if (ok) toast.success('Изменения сохранены')
  else toast.danger(store.lastError.value ?? 'Не удалось сохранить изменения')
}

async function onClaim(payload: {
  id: string
  holder: string
  note?: string
  force?: boolean
}): Promise<void> {
  const ok = await store.claimTask(payload.id, payload.holder, payload.note, payload.force)
  if (ok) {
    toast[payload.force ? 'warning' : 'success'](
      payload.force
        ? `${payload.id} взята в работу в обход блокеров (запись в истории)`
        : `${payload.id} взята в работу: ${payload.holder}`,
    )
  } else {
    toast.danger(store.lastError.value ?? 'Не удалось взять задачу')
  }
}

async function onHeartbeat(payload: { id: string; holder: string; note?: string }): Promise<void> {
  const ok = await store.heartbeatTask(payload.id, payload.holder, payload.note)
  if (ok) toast.success('Heartbeat отправлен')
  else toast.danger(store.lastError.value ?? 'Heartbeat не прошёл')
}

async function onStage(payload: { id: string; holder?: string; note?: string }): Promise<void> {
  const ok = await store.nextStage(payload.id, payload.holder, payload.note)
  if (ok) toast.success('Этап переключён')
  else toast.danger(store.lastError.value ?? 'Не удалось переключить этап')
}

async function onNeedsOwner(payload: { id: string; value: boolean; note?: string }): Promise<void> {
  const note = payload.note?.trim()
  if (!payload.value && note) {
    const ok = await store.answerQuestion(payload.id, note)
    if (ok) toast.success('Ответ отправлен')
    else toast.danger(store.lastError.value ?? 'Не удалось отправить ответ')
    return
  }
  const ok = await store.setNeedsOwner(payload.id, payload.value, payload.note)
  if (ok) {
    if (payload.value) toast.info(note ? 'Вопрос автору записан' : 'Задача поднята в «нужен ты»')
    else toast.success('Флаг «нужен автор» снят')
  } else {
    toast.danger(store.lastError.value ?? 'Не удалось изменить флаг')
  }
}

async function onRelease(payload: { id: string }): Promise<void> {
  const ok = await store.releaseTask(payload.id)
  if (ok) toast.success('Задача освобождена')
  else toast.danger(store.lastError.value ?? 'Не удалось освободить задачу')
}

async function onDone(payload: { id: string; result: string }): Promise<void> {
  const ok = await store.doneTask(payload.id, payload.result)
  if (ok) toast.success('Задача закрыта')
  else toast.danger(store.lastError.value ?? 'Не удалось закрыть задачу')
}

async function onRemove(payload: { id: string }): Promise<void> {
  const ok = await store.removeTask(payload.id)
  if (ok) toast.success(`Задача ${payload.id} удалена`)
  else toast.danger(store.lastError.value ?? 'Не удалось удалить задачу')
}

async function onComment(payload: { id: string; text: string; kind: CommentKind; author?: string }): Promise<void> {
  const ok = await store.addComment(payload.id, payload.text, payload.kind, payload.author)
  if (ok) toast.success('Комментарий добавлен')
  else toast.danger(store.lastError.value ?? 'Комментарий не отправлен')
}

async function onDep(payload: { id: string; dependsOn: string }): Promise<void> {
  const ok = await store.addDependency(payload.id, payload.dependsOn)
  if (ok) toast.success('Связь добавлена')
  else toast.danger(store.lastError.value ?? 'Не удалось добавить связь')
}

// ── жизненный цикл ───────────────────────────────────────────────────────

watch(
  () => store.openTaskId.value,
  (value) => {
    drawerOpen.value = Boolean(value)
  },
  { immediate: true },
)

watch(drawerOpen, (open) => {
  if (!open) store.closeTask()
})

const isPhone = useIsPhone()
store.phone.value = isPhone.value
watch(isPhone, (value) => store.setPhone(value))

/** Клик по марке в шапке: закрыть карточку и вернуться на доску. */
function goHome(): void {
  store.closeTask()
  void store.setView('board')
}

onMounted(() => {
  store.init()
})

onBeforeUnmount(() => {
  store.dispose()
})
</script>

<template>
  <div class="listik-shell" :class="{ 'listik-shell--phone': store.phone.value }">
    <AppHeader
      :health="store.health.value"
      :live="store.live.value"
      :loading="store.loading.value"
      :last-sync-at="store.lastSyncAt.value"
      :phone="store.phone.value"
      @refresh="refreshAll"
      @projects="store.openProjects()"
      @home="goHome"
    />

    <UiContainer>
      <div class="listik-shell__top">
        <UiAlert v-if="store.connectionLost.value" tone="danger">
          <template #title>Сервер Listik недоступен</template>
          Запустите сервер командой <code class="listik-mono">./bin/listik serve</code> из корня
          <code class="listik-mono">Listik</code> и нажмите «Обновить».
          <div class="listik-row" style="margin-top: var(--space-3)">
            <UiButton size="sm" variant="secondary" @click="refreshAll">Повторить запрос</UiButton>
          </div>
        </UiAlert>

        <UiAlert v-else-if="store.lastError.value" tone="warning" closable>
          <template #title>Последняя операция завершилась ошибкой</template>
          {{ store.lastError.value }}
        </UiAlert>

        <template v-if="store.phone.value">
          <PhoneQueue />
        </template>
        <template v-else>
          <UiAlert v-if="store.cycles.value.length > 0" tone="danger">
            <template #title>Циклы в зависимостях</template>
            Задачи ждут друг друга по кругу — сами они не разблокируются:
            <span class="listik-mono">{{ store.cycles.value.map((cycle) => cycle.join(' → ')).join('; ') }}</span>
          </UiAlert>

          <div class="listik-row" style="justify-content: space-between">
            <UiTabs
              :model-value="store.view.value"
              :tabs="tabs"
              @update:model-value="(value) => store.setView(value as ViewKey)"
            />
            <div class="listik-row">
              <UiSaveStatus :status="saveStatus" :at="saveAt" />
              <UiBadge tone="neutral" size="sm">{{ tasksCountLabel(store.counts.value.total) }}</UiBadge>
            </div>
          </div>

          <div v-if="store.view.value === 'board'" class="listik-stack">
            <NeedsYouStrip
              :tasks="store.inbox.value"
              @open="store.openTask"
              @answer="(task) => openTaskWithComment(task, 'answer')"
              @release="releaseFromInbox"
            />

            <BoardToolbar />
          </div>

          <template v-if="store.view.value === 'board'">
            <MobileTaskList />
            <div class="listik-board-only">
              <BoardView :projects="store.meta.value?.projects" @create="createOpen = true" />
            </div>
          </template>
          <ListView v-else-if="store.view.value === 'list'" />
          <MetricsView v-else />
        </template>

        <SearchPanel />
      </div>
    </UiContainer>

    <TaskDrawer
      v-if="!store.phone.value"
      ref="drawerRef"
      v-model="drawerOpen"
      :task="store.detail.value"
      :loading="store.detailLoading.value"
      :error="store.detailError.value"
      :pending="store.pending.value"
      :actors="actorList(store.meta.value)"
      :projects="store.meta.value?.projects"
      :load-tree="store.loadDepTree"
      @reload="store.reloadDetail()"
      @open-other="store.openTask"
      @patch="onPatch"
      @claim="onClaim"
      @heartbeat="onHeartbeat"
      @stage="onStage"
      @needs-owner="onNeedsOwner"
      @release="onRelease"
      @done="onDone"
      @remove="onRemove"
      @comment="onComment"
      @dep="onDep"
    />
    <PhoneTaskSheet
      v-else
      v-model="drawerOpen"
      :task="store.detail.value"
      :loading="store.detailLoading.value"
      :error="store.detailError.value"
      :projects="store.meta.value?.projects"
      @reload="store.reloadDetail()"
    />

    <UiModal v-model="store.needsToken.value" title="Нужен токен Listik" size="sm" :closable="false">
      <div class="listik-token-form">
        <p class="listik-prose">
          Токен лежит в <code class="listik-mono">config.toml</code> в корне Listik, секция
          <code class="listik-mono">[auth] token</code>. Он сохранится в
          <code class="listik-mono">localStorage['listik.token']</code> и будет уходить заголовком
          <code class="listik-mono">Authorization: Bearer</code>.
        </p>
        <UiInput v-model="tokenInput" placeholder="токен" :error="tokenError" v-bind="{ 'aria-label': 'Токен доступа' }" />
        <UiButton variant="primary" @click="submitToken">Сохранить токен</UiButton>
      </div>
    </UiModal>

    <ProjectSettings />

    <NewTaskModal
      v-model="createOpen"
      :projects="store.meta.value?.projects ?? []"
      :pending="store.pending.value === 'create'"
      @submit="createTask"
    />

    <UiToast />

    <footer class="listik-shell__footer">Listik · версия {{ store.health.value?.version ?? '—' }}</footer>
  </div>
</template>
