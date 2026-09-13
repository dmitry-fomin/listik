<script setup lang="ts">
/**
 * Dev-витрина: живая карта «прототип → кит + свои примитивы», без обращения
 * к API. Один длинный скролл секциями, ровно один UiPageHeader (см. `docs/
 * facet-prototype-map.md` — что чем сделано и почему).
 */
import { ref } from 'vue'
import {
  UiAlert,
  UiBadge,
  UiChip,
  UiDrawer,
  UiField,
  UiInput,
  UiModal,
  UiPageHeader,
  UiProgress,
  UiSegmented,
  UiSelect,
  UiSteps,
  UiStatusPill,
  UiTabs,
  UiTextarea,
  UiTimeline,
  UiButton,
  useColorScheme,
  type UiSegmentedOption,
} from '@zoloto585/facet'
import ProjectMark from '@/components/marks/ProjectMark.vue'
import HarnessIcon from '@/components/marks/HarnessIcon.vue'
import HealthDot from '@/components/marks/HealthDot.vue'
import TaskGlyph from '@/components/marks/TaskGlyph.vue'
import { PRIORITIES, TASK_TYPES } from '@/lib/dictionaries'
import CountGlyph from '@/components/marks/CountGlyph.vue'
import { useTheme } from '@/lib/theme'
import { HEALTH_TITLES } from '@/lib/health'
import {
  FIXTURE_HARNESSES,
  FIXTURE_HEALTH_LEGEND,
  FIXTURE_PROJECTS,
  FIXTURE_SELECT_OPTIONS,
  FIXTURE_STEPS,
  FIXTURE_TIMELINE,
  FIXTURE_VIEW_TABS,
} from './fixtures'

const { theme, toggleTheme } = useTheme()
const { colorScheme, setColorScheme, schemes } = useColorScheme()
const schemeOptions: UiSegmentedOption[] = schemes.map((scheme) => ({ value: scheme, label: scheme }))

const activeTab = ref('board')
const chipSelected = ref(false)
const drawerOpen = ref(false)
const modalOpen = ref(false)
const selectValue = ref<string | null>(null)
const inputValue = ref('')
const textareaValue = ref('')

const PRIORITY_VALUES = PRIORITIES.map((item) => item.value)
const TYPE_VALUES = TASK_TYPES.map((item) => item.value)

const badgeTones = ['neutral', 'accent', 'success', 'danger', 'warning', 'info'] as const
const pillTones = ['healthy', 'at-risk', 'dead', 'unknown', 'info', 'success', 'danger', 'warning', 'neutral'] as const
</script>

<template>
  <div class="listik-shell" style="padding: var(--space-8); gap: var(--space-8); max-width: var(--container-max); margin: 0 auto">
    <UiPageHeader title="Dev-витрина кита" description="Легенда прототипа и примитивы — без обращения к API.">
      <template #actions>
        <UiSegmented
          :model-value="colorScheme"
          :options="schemeOptions"
          size="sm"
          label="Цветовая гамма"
          @update:model-value="(value) => setColorScheme(value as typeof colorScheme)"
        />
        <UiButton size="sm" variant="ghost" @click="toggleTheme">
          {{ theme === 'dark' ? 'Светлая тема' : 'Тёмная тема' }}
        </UiButton>
      </template>
    </UiPageHeader>

    <!-- 1. Легенда -->
    <section class="listik-section">
      <h2 class="listik-section__title">Легенда</h2>

      <div class="listik-stack">
        <span class="listik-section__hint">приоритет</span>
        <div class="listik-row" style="gap: var(--space-5)">
          <span v-for="priority in PRIORITY_VALUES" :key="priority" class="listik-row">
            <TaskGlyph kind="priority" :value="priority" />
            <span>P{{ priority }} {{ PRIORITIES[priority].label }}</span>
          </span>
        </div>

        <span class="listik-section__hint">тип</span>
        <div class="listik-row" style="gap: var(--space-5)">
          <span v-for="type in TYPE_VALUES" :key="type" class="listik-row">
            <TaskGlyph kind="type" :value="type" />
            <span>{{ type }}</span>
          </span>
        </div>

        <span class="listik-section__hint">связи</span>
        <div class="listik-row" style="gap: var(--space-5)">
          <span class="listik-row">
            <CountGlyph icon="lock" :count="1" title="ждёт 1 блокер" tone="warning" />
            <span>ждёт N блокеров</span>
          </span>
          <span class="listik-row">
            <CountGlyph icon="key" :count="2" title="её ждут 2" tone="accent" />
            <span>её ждут N</span>
          </span>
          <span class="listik-row">
            <CountGlyph icon="branch" :count="4" title="детей открыто 4" tone="neutral" />
            <span>детей открыто N</span>
          </span>
        </div>

        <span class="listik-section__hint">здоровье</span>
        <div class="listik-row" style="gap: var(--space-5)">
          <span v-for="row in FIXTURE_HEALTH_LEGEND" :key="row.health" class="listik-row">
            <HealthDot :health="row.health" />
            <span>{{ HEALTH_TITLES[row.health] }} · {{ row.hint }}</span>
          </span>
        </div>

        <span class="listik-section__hint">проекты</span>
        <div class="listik-row" style="gap: var(--space-5)">
          <ProjectMark
            v-for="project in FIXTURE_PROJECTS"
            :key="project.slug"
            :project="project"
            with-title
          />
        </div>

        <span class="listik-section__hint">харнессы</span>
        <div class="listik-row" style="gap: var(--space-5)">
          <span v-for="harness in FIXTURE_HARNESSES" :key="harness" class="listik-row">
            <HarnessIcon :harness="harness" />
            <span>{{ harness }}</span>
          </span>
        </div>
      </div>
    </section>

    <!-- 2. Кит: статусы и метки -->
    <section class="listik-section">
      <h2 class="listik-section__title">Кит: статусы и метки</h2>
      <div class="listik-row" style="gap: var(--space-3)">
        <UiStatusPill v-for="tone in pillTones" :key="tone" :tone="tone" size="sm">{{ tone }}</UiStatusPill>
      </div>
      <div class="listik-row" style="gap: var(--space-3)">
        <UiBadge v-for="tone in badgeTones" :key="tone" :tone="tone" size="sm">{{ tone }}</UiBadge>
      </div>
      <div class="listik-row" style="gap: var(--space-3)">
        <UiChip label="Vue" />
        <UiChip label="TypeScript" />
        <UiChip label="● В работе" v-model:selected="chipSelected" />
      </div>
    </section>

    <!-- 3. Кит: навигация -->
    <section class="listik-section">
      <h2 class="listik-section__title">Кит: навигация</h2>
      <UiTabs v-model="activeTab" :tabs="FIXTURE_VIEW_TABS">
        <template v-for="tab in FIXTURE_VIEW_TABS" :key="tab.key" #[`panel-${tab.key}`]>
          <p class="listik-section__hint">панель «{{ tab.label }}» — заглушка</p>
        </template>
      </UiTabs>
    </section>

    <!-- 4. Кит: процесс -->
    <section class="listik-section">
      <h2 class="listik-section__title">Кит: процесс</h2>
      <UiSteps :items="FIXTURE_STEPS" :current-index="2" label="Шаги конвейера" />
      <UiProgress size="sm" :value="40" label="Порции" />
    </section>

    <!-- 5. Кит: лента -->
    <section class="listik-section">
      <h2 class="listik-section__title">Кит: лента</h2>
      <UiTimeline dense :items="FIXTURE_TIMELINE" />
    </section>

    <!-- 6. Кит: оверлеи -->
    <section class="listik-section">
      <h2 class="listik-section__title">Кит: оверлеи</h2>
      <div class="listik-row" style="gap: var(--space-3)">
        <UiButton @click="drawerOpen = true">Открыть дровер (lg)</UiButton>
        <UiButton variant="secondary" @click="modalOpen = true">Открыть модалку (lg)</UiButton>
      </div>
      <UiDrawer v-model="drawerOpen" title="Панель задачи" size="lg">
        <p class="listik-prose">Пусто — только чтобы увидеть ширину 720px из app.css.</p>
      </UiDrawer>
      <UiModal v-model="modalOpen" title="Модалка" size="lg">
        <p class="listik-prose">Пусто.</p>
      </UiModal>
    </section>

    <!-- 7. Кит: поля -->
    <section class="listik-section">
      <h2 class="listik-section__title">Кит: поля</h2>
      <UiAlert tone="info">
        Метка проекта рядом с UiSelect рендерится снаружи компонента — слота под опцию
        <code>ProjectMark</code> у UiSelect нет.
      </UiAlert>
      <div class="listik-row" style="gap: var(--space-3); align-items: flex-end">
        <UiField label="Проект">
          <div class="listik-row">
            <ProjectMark v-if="selectValue" :project="FIXTURE_PROJECTS.find((p) => p.slug === selectValue) ?? null" size="sm" />
            <UiSelect v-model="selectValue" :options="FIXTURE_SELECT_OPTIONS" placeholder="Выбрать…" />
          </div>
        </UiField>
        <UiField label="Поиск">
          <UiInput v-model="inputValue" placeholder="Название задачи…" />
        </UiField>
      </div>
      <UiField label="Описание">
        <UiTextarea v-model="textareaValue" autosize placeholder="Автосайз…" />
      </UiField>
    </section>
  </div>
</template>
