<script setup lang="ts">
/**
 * Страница настроек (`/settings/<раздел>`): слева навигация по разделам с
 * заметкой, справа — шапка раздела и его содержимое. Раньше это была модалка с
 * вкладками — теперь у каждого раздела свой адрес, и на него можно дать ссылку,
 * открыть в новой вкладке и вернуться «назад» браузера.
 *
 * Активный раздел не хранится: он целиком выводится из адреса
 * (`settingsSectionOf(currentPath)`), поэтому прямой заход на `/settings/routes`
 * сразу открывает «Маршруты».
 *
 * Шапка (`UiPageHeader`) — общая для страницы, но её содержимое принадлежит
 * разделу: заголовок и описание лежат в справочнике `sections`, а первичное
 * действие раздел отдаёт сам через `defineExpose({ openPrimaryAction })`. Кнопка
 * в слоте `#actions` — только витрина: по клику страница зовёт метод раздела, и
 * форма со всей логикой остаётся внутри него. Разделу без действия (сейчас
 * «Маршруты») кнопка не рисуется — слот пуст.
 *
 * Пункты навигации — настоящие ссылки (`href`), чтобы Cmd/Ctrl-клик открывал
 * новую вкладку. Ловушка кита: `UiAppNav` не делает `preventDefault` у пункта со
 * ссылкой (и не должен — переход по ссылке дело потребителя), поэтому обычный
 * клик перехвачен на обёртке: событие всплывает от `<a>`, мы ищем ближайшую
 * ссылку и ведём переход роутером. Событие `select` кита для перехода не
 * используется — иначе переход случился бы дважды.
 *
 * Заметка под навигацией — часть той же колонки, поэтому липнет вместе с ней и
 * на узком экране уезжает наверх. Тексты заметок — из макетов.
 *
 * Содержимое раздела под `v-if`, а не `v-show`: уход с раздела пересоздаёт его
 * компонент, и локальное состояние (открытые окна, плашки результата) не тащится
 * между разделами — так же, как раньше сбрасывалось при закрытии модалки.
 */
import { computed, ref } from 'vue'
import {
  UiAlert,
  UiAppNav,
  UiButton,
  UiContainer,
  UiPageHeader,
  type UiAppNavItem,
} from '@zoloto585/facet'
import ListikIcon from '@/components/ListikIcon.vue'
import ReposSection from '@/components/settings/ReposSection.vue'
import RoutesSettings from '@/components/RoutesSettings.vue'
import { currentPath, navigate, settingsSectionOf } from '@/lib/router'

/** Что раздел отдаёт странице наружу: сейчас — только своё первичное действие. */
interface SectionExpose {
  openPrimaryAction?: () => void
}

const items: UiAppNavItem[] = [
  { id: 'repos', label: 'Репозитории', href: '/settings/repos' },
  { id: 'routes', label: 'Маршруты', href: '/settings/routes' },
]

/** Иконка пункта навигации: кит icon-agnostic, глиф приходит слотом `#icon`. */
const navIcons: Record<string, string> = {
  repos: 'columns',
  routes: 'route-direct',
}

function navIcon(id: string): string {
  return navIcons[id] ?? 'dot'
}

const section = computed(() => settingsSectionOf(currentPath.value) ?? 'repos')

/**
 * Справочник разделов: заголовок, описание и подпись первичного действия.
 * Тексты — дословно из макетов `docs/design/settings`. Нет `actionLabel` —
 * слот `#actions` шапки остаётся пустым (так у «Маршрутов», пока порция `e`
 * не заведёт им своё действие).
 */
const sections: Record<string, { title: string; description: string; actionLabel?: string }> = {
  repos: {
    title: 'Репозитории',
    description:
      'Доска собирается по проектам из Listik: колонки и фильтр «Проект» видят ровно те репозитории, что не скрыты. Скрытие ничего не удаляет — задачи остаются в истории, поиске и памяти, доска просто перестаёт их показывать. Удаление убирает проект совсем; проект с задачами сервер не отдаст без отдельного подтверждения.',
    actionLabel: 'Добавить репозиторий',
  },
  routes: {
    title: 'Маршруты',
    description:
      'Маршрут — заготовка запуска задачи. Конвейер приходит из скила: у него правятся только название, подпись и иконка. Прямой маршрут Listik запускает сам, поэтому у него правится команда — и завести можно только такой.',
  },
}

const meta = computed(() => sections[section.value] ?? sections.repos)

/** Ссылки на разделы: каждый отдаёт своё действие через `defineExpose`. */
const reposRef = ref<SectionExpose | null>(null)
const routesRef = ref<SectionExpose | null>(null)
const activeSection = computed(() =>
  section.value === 'repos' ? reposRef.value : routesRef.value,
)

function openSectionAction(): void {
  activeSection.value?.openPrimaryAction?.()
}

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
        eyebrow="Настройки"
        :title="meta.title"
        :description="meta.description"
      >
        <template v-if="meta.actionLabel" #actions>
          <UiButton variant="primary" @click="openSectionAction">
            <template #icon><ListikIcon name="plus" size="xs" /></template>
            {{ meta.actionLabel }}
          </UiButton>
        </template>
      </UiPageHeader>

      <div class="listik-settings__body">
        <div class="listik-settings__nav" @click="onNavClick">
          <UiAppNav
            :items="items"
            :model-value="section"
            orientation="vertical"
            ariaLabel="Разделы настроек"
          >
            <template #icon="{ item }">
              <ListikIcon :name="navIcon(item.id)" size="sm" />
            </template>
          </UiAppNav>

          <UiAlert v-if="section === 'repos'" tone="info">
            <template #title>Всё хранится в одной базе</template>
            Репозитории и маршруты лежат в <code class="listik-mono">listik.db</code>.
            Перенести на другую машину — <code class="listik-mono">listik backup</code>.
          </UiAlert>
          <UiAlert v-else tone="info">
            Маршрут нельзя удалить и нельзя переставить в списке. Ненужный — выключи:
            он останется в базе, а из меню «Запустить» пропадёт.
          </UiAlert>
        </div>

        <div class="listik-settings__content">
          <ReposSection v-if="section === 'repos'" ref="reposRef" />
          <RoutesSettings v-else ref="routesRef" />
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
  /* Колонка разделов — ширина макета, собранная из шагов кита: своей ширины
     в токенах у сайдбара нет, а `max-content` теперь тянула бы её по длинной
     заметке. */
  grid-template-columns: calc(var(--space-24) * 3) minmax(0, 1fr);
  gap: var(--space-6);
  align-items: start;
}

/* Навигация липнет под шапкой вместе с заметкой: список раздела длинный, а
   разделов всего два — уводить их за верхний край экрана незачем. */
.listik-settings__nav {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
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
