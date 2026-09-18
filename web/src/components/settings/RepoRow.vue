<script setup lang="ts">
/**
 * Строка репозитория в разделе «Репозитории» (`ReposSection.vue`).
 *
 * Строки «На доске» и «Скрыты с доски» отличаются только значением тумблера и
 * его подписью, поэтому разметка у них одна — этот компонент (раньше она была
 * продублирована в разделе).
 *
 * Мета-строка собрана слотом `#meta` из подписанных фрагментов, а не одной
 * строкой через ` · `: в настройках строка — это карточка репозитория, и
 * человек должен видеть, что есть что (ключ для CLI, ветка, путь, задачи).
 * Знак проекта (`ProjectMark`) здесь намеренно не используется: монограмма с
 * цветом принадлежит доске, а строка и так несёт название и slug.
 */
import { UiBadge, UiButton, UiEntityCard, UiSwitch, UiTooltip } from '@zoloto585/facet'
import ListikIcon from '../ListikIcon.vue'
import type { ProjectRow } from '@/api/types'
import { projectGitHint, projectIsGit, projectTasksLabel, projectTitleLabel } from '@/lib/projects'

defineProps<{
  project: ProjectRow
  /** Строка из секции «Скрыты с доски»: тумблер включён, клик возвращает на доску. */
  hidden: boolean
  /** Идёт запрос по этой строке (скрытие/возврат/удаление) — карточка в скелетоне. */
  busy: boolean
}>()

defineEmits<{
  /** Новое значение `archived` — ровно то, что прислал тумблер. */
  toggle: [archived: boolean]
  edit: []
  remove: []
}>()
</script>

<template>
  <UiEntityCard :title="projectTitleLabel(project)" size="md" :loading="busy">
    <template #avatar>
      <ListikIcon name="columns" size="sm" />
    </template>

    <template #meta>
      <span class="listik-repo__facts">
        <span class="listik-repo__fact">
          <span class="listik-repo__fact-label">ключ</span>
          <code class="listik-mono">{{ project.slug }}</code>
        </span>

        <UiTooltip v-if="projectIsGit(project)" :text="projectGitHint(project)">
          <span class="listik-repo__fact">
            <span v-if="project.git_branch" class="listik-repo__fact-label">ветка</span>
            <span class="listik-repo__fact-value">{{ project.git_branch || 'git' }}</span>
          </span>
        </UiTooltip>

        <span class="listik-repo__fact">
          <span class="listik-repo__fact-label">путь</span>
          <!-- Состояние каталога показывает бейдж в действиях, поэтому здесь
               только сам путь, без хвоста «— каталога нет». -->
          <code v-if="project.path" class="listik-mono">{{ project.path }}</code>
          <span v-else class="listik-repo__fact-value">каталог не указан</span>
        </span>

        <span class="listik-repo__fact">
          <span class="listik-repo__fact-label">задачи</span>
          <span class="listik-repo__fact-value">{{ projectTasksLabel(project) }}</span>
        </span>
      </span>
    </template>

    <template #actions>
      <UiBadge v-if="project.path_exists === false" tone="warning" size="sm">нет каталога</UiBadge>

      <UiTooltip
        :text="hidden ? 'Вернуть на доску' : 'Убрать с доски: задачи останутся в истории и поиске'"
      >
        <!-- UiSwitch отдаёт новое значение тумблера: у скрытого он включён,
             клик присылает `false` — это и есть целевое `archived`.
             Инвертировать его нельзя: вернуть проект было невозможно
             (listik-54be). -->
        <UiSwitch
          :model-value="hidden"
          v-bind="{
            'aria-label': hidden
              ? `Вернуть проект ${project.slug}`
              : `Скрыть проект ${project.slug}`,
          }"
          @update:model-value="(value: boolean) => $emit('toggle', value)"
        />
      </UiTooltip>

      <UiTooltip text="Название и путь к каталогу">
        <UiButton
          size="sm"
          variant="ghost"
          v-bind="{ 'aria-label': `Изменить проект ${project.slug}` }"
          @click="$emit('edit')"
        >
          <template #icon><ListikIcon name="edit" size="xs" /></template>
        </UiButton>
      </UiTooltip>

      <UiButton
        size="sm"
        variant="ghost"
        v-bind="{ 'aria-label': `Удалить проект ${project.slug}` }"
        @click="$emit('remove')"
      >
        <template #icon><ListikIcon name="close" size="xs" /></template>
      </UiButton>
    </template>
  </UiEntityCard>
</template>

<style scoped>
/* Мета-строка кита однострочная (nowrap), а здесь фрагментов четыре и путь
   бывает длинным — своя раскладка с переносом. */
.listik-repo__facts {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-1) var(--space-4);
  min-width: 0;
  white-space: normal;
}

.listik-repo__fact {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-1);
  min-width: 0;
}

.listik-repo__fact-label {
  color: var(--ink-3);
}

.listik-repo__fact-value,
.listik-repo__fact .listik-mono {
  color: var(--ink-2);
  overflow-wrap: anywhere;
}
</style>
