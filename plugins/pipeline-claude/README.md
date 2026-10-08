# pipeline-claude

Пресеты конвейера реализации задачи, где работает только Claude — локальные субагенты основной сессии, без
внешних харнессов. Каждый скил запускается только по явному имени — маршрутом карточки Listik (`launch_route` или
метка `process:<ключ>`) или когда человек назвал пресет.

## Установка

```
/plugin marketplace add dmitry-fomin/listik
/plugin install listik@listik
/plugin install pipeline-core@listik
/plugin install pipeline-claude@listik
```

## Скилы

Все — `pipeline-claude:<имя>`. «—» — этапа нет.

| Скил | Когда брать | ТЗ | Критика ТЗ | Код | Приёмка + коммит |
| --- | --- | --- | --- | --- | --- |
| `opus` | понятная работа в один заход, приёмка не нужна; $0 внешних | — | — | Opus medium (`pipeline-implementer-solo`, `model: opus`), сам коммитит | — |
| `high` | только Claude Code, без внешних харнессов; $0 внешних, усилие — от сессии (`claude --effort`) | Opus 5.5 (`pipeline-spec-writer-inherit`, `model: opus`) | Sonnet 5.5 + Opus 5.5 (`pipeline-critic-inherit`), кворум — оба | Opus 5.5 (`pipeline-implementer-inherit`, `model: opus`) | линзы Sonnet 5.5 ×3 (`pipeline-lens`), при находке — Sonnet 5.5 (`pipeline-judge-inherit`) |

Агенты, хук автоодобрения и ядро `pipeline-core.md` — в плагине `pipeline-core` (`plugins/pipeline-core/README.md`).
