# Шаг 05. Desktop/tablet UI по прототипам

## Цель

Перенести смысл прототипов в production Vue 3, используя Facet и существующие views Listik.

## Объём

* шапка Listik: сервер/live status, тема и гамма;
* tabs Board/List/Timeline/Metrics;
* полоса «Нужен ты», фильтры проекта/harness/status и поиск;
* board columns s1/s2/s3/s4 с health и stage duration;
* состояния карточки: ready, blocked, abandoned, silent, needs-owner;
* TaskDrawer с этапами, handoff/sticky, heartbeat, release, links, timeline и journal;
* NewTask с process, spec_path, acceptance и базовой декомпозицией.

Перед реализацией читать `web/node_modules/@zoloto585/facet/README.md` и
`docs/facet-components.md`. Использовать `UiDataTable`, `UiDrawer`, `UiStatusPill`, `UiBadge`,
`UiFilterBar`, `UiTimeline`, `UiSteps`, `UiEntityCard` и другие канонические компоненты по
purpose-комментариям. Для канбана допустим нативный DnD с клавиатурной альтернативой; этап
меняется серверной командой.

## Ограничения

Одна тема/гамма, Vue >= 3.5, strict templates. Ширина 1024 px — обязательный smoke viewport.
Не копировать HTML прототипов и не дублировать CSS Facet вручную.

## Критерии приёмки

* На 1024 px видны ключевые колонки и понятен горизонтальный/сокращённый режим.
* Drawer открывает полную карточку и не теряет фильтры доски.
* Все действия, меняющие состояние, показывают серверную ошибку и обновляют данные.
* Состояния health визуально различимы, но строка/карточка не закрашивается целиком по статусу.
