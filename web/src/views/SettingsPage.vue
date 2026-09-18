<script setup lang="ts">
/**
 * Страница настроек (`/settings/<раздел>`): слева навигация по разделам, справа
 * содержимое. Раньше это была модалка с вкладками — теперь у каждого раздела
 * свой адрес, и на него можно дать ссылку, открыть в новой вкладке и вернуться
 * «назад» браузера.
 *
 * Активный раздел не хранится: он целиком выводится из адреса
 * (`settingsSectionOf(currentPath)`), поэтому прямой заход на `/settings/routes`
 * сразу открывает «Маршруты».
 *
 * Пункты навигации — настоящие ссылки (`href`), чтобы Cmd/Ctrl-клик открывал
 * новую вкладку. Ловушка кита: `UiAppNav` не делает `preventDefault` у пункта со
 * ссылкой (и не должен — переход по ссылке дело потребителя), поэтому обычный
 * клик перехвачен на обёртке: событие всплывает от `<a>`, мы ищем ближайшую
 * ссылку и ведём переход роутером. Событие `select` кита для перехода не
 * используется — иначе переход случился бы дважды.
 *
 * Содержимое раздела под `v-if`, а не `v-show`: уход с раздела пересоздаёт его
 * компонент, и локальное состояние (открытые окна, плашки результата) не тащится
 * между разделами — так же, как раньше сбрасывалось при закрытии модалки.
 */
import { computed } from 'vue'
import { UiAppNav, UiContainer, UiPageHeader, type UiAppNavItem } from '@zoloto585/facet'
import ReposSection from '@/components/settings/ReposSection.vue'
import RoutesSettings from '@/components/RoutesSettings.vue'
import { currentPath, navigate, settingsSectionOf } from '@/lib/router'

const items: UiAppNavItem[] = [
  { id: 'repos', label: 'Репозитории', href: '/settings/repos' },
  { id: 'routes', label: 'Маршруты', href: '/settings/routes' },
]

const section = computed(() => settingsSectionOf(currentPath.value) ?? 'repos')

/** Клик по пункту навигации: обычный ведём роутером, с модификатором — браузеру. */
function onNavClick(event: MouseEvent): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  const target = event.target as HTMLElement | null
  const link = target?.closest?.('a[href]') as HTMLAnchorElement | null
  if (!link) return
  event.preventDefault()
  void navigate(link.getAttribute('href') ?? '/')
}
</script>

<template>
  <UiContainer>
    <div class="listik-settings">
      <UiPageHeader
        title="Настройки"
        description="Репозитории на доске и маршруты запуска задач."
      />

      <div class="listik-settings__body">
        <div class="listik-settings__nav" @click="onNavClick">
          <UiAppNav
            :items="items"
            :model-value="section"
            orientation="vertical"
            ariaLabel="Разделы настроек"
          />
        </div>

        <div class="listik-settings__content">
          <ReposSection v-if="section === 'repos'" />
          <RoutesSettings v-else />
        </div>
      </div>
    </div>
  </UiContainer>
</template>

<style scoped>
.listik-settings {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  padding-block: var(--space-6);
  min-width: 0;
}

.listik-settings__body {
  display: grid;
  /* Колонка разделов по самому длинному пункту: своей ширины в токенах кита нет,
     а `max-content` не требует ни px, ни процентов. */
  grid-template-columns: max-content minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

/* Навигация липнет под шапкой: список раздела длинный, а разделов всего два —
   уводить их за верхний край экрана незачем. */
.listik-settings__nav {
  position: sticky;
  top: calc(var(--header-h) + var(--space-4));
  min-width: 0;
}

.listik-settings__content {
  min-width: 0;
}

/* На узком экране колонки схлопываются в одну, навигация уходит наверх и
   перестаёт липнуть (брейкпоинт литеральными px — как везде в проекте). */
@media (max-width: 720px) {
  .listik-settings__body {
    grid-template-columns: minmax(0, 1fr);
  }

  .listik-settings__nav {
    position: static;
  }
}
</style>
