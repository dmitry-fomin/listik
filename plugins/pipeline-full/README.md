# pipeline-full

Пресеты конвейера реализации задачи: ТЗ → критика ТЗ → код → приёмка с коммитом. Основной контекст
только маршрутизирует, носит вопросы автору и ведёт журнал; код пишет исполнитель, коммитит судья
(в `high-pipeline` и `xhigh-pipeline` при чистых линзах приёмки порцию коммитит оркестратор).
Каждый скил запускается только по явному имени — маршрутом карточки Listik (`launch_route` или
метка `process:<ключ>`) или когда человек назвал пресет.

## Установка

```
/plugin marketplace add dmitry-fomin/listik
/plugin install listik@listik
/plugin install pipeline-core@listik
/plugin install pipeline-full@listik
```

## Скилы

Все — `pipeline-full:<имя>`. «—» — этапа нет.

| Скил | Когда брать | ТЗ | Критика ТЗ | Код | Приёмка + коммит |
| --- | --- | --- | --- | --- | --- |
| `xhigh` | ошибка дороже прогона; ~$4 на задачу | Opus 5.5 xhigh (`pipeline-spec-writer-xhigh`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + DeepSeek V4.1 Flash в pi + devin SWE-2 max, кворум, сводит оркестратор | Opus 5.5 xhigh (`pipeline-implementer-xhigh`) | линзы GLM 5.3 Flash ×3 в pi, при находке — Grok 4.7 xhigh |
| `high` | расклад по умолчанию; ~$4 | Opus 5.5 high (`pipeline-spec-writer`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + DeepSeek V4.1 Flash в pi + devin SWE-2 max, кворум, сводит оркестратор | Opus 5.5 high (`pipeline-implementer-high`, `model: opus`) | линзы GLM 5.3 Flash ×3 в pi, при находке — Grok 4.7 xhigh |
| `medium` | работа понятная, хватит пониженного усилия; ~$3 | Opus 5.5 medium (`pipeline-spec-writer-medium`, `model: opus`) | Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`) + DeepSeek V4.1 Flash в pi, кворум, сводит оркестратор | Opus 5.5 medium (`pipeline-implementer`, `model: opus`) | Grok 4.7 high |
| `low` | поджимает лимит Max: код вне квоты; ~$3 | Opus 5.5 low (`pipeline-spec-writer-low`, `model: opus`) | DeepSeek V4.1 Flash + GLM 5.3 Flash в pi, кворум, сводит оркестратор | devin SWE-2 max (`devin:devin-delegate --thinking max`) | Grok 4.7 high |
| `xlow` | задача в один прогон, нужна независимая приёмка | — | — | devin SWE-2 max (`devin:devin-delegate --thinking max`) | Grok 4.7 high |
| `nano` | то же, дешевле; при пустом `write_scope` сперва ход на чтении за границами правки | — | — | devin SWE-2 high (`devin:devin-delegate --thinking high`) | GLM 5.3 Flash (`pi:pi-delegate --channel glm`) |
| `cross` | автор ТЗ и исполнитель на разных вендорах: Devin пишет ТЗ, GLM в pi — код | Devin (SWE-2, max) | DeepSeek V4.1 Flash в pi + Sonnet 5.5 high (`pipeline-critic`, `model: sonnet`), кворум, сводит оркестратор | GLM 5.3 Flash в pi | Grok 4.7 xhigh |

Агенты, хук автоодобрения и ядро `pipeline-core.md` — в плагине `pipeline-core` (`plugins/pipeline-core/README.md`).
