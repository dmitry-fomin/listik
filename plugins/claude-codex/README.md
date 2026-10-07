# claude-codex

Линейка пресетов конвейера ТЗ → критика → код → приёмка, где работают только Claude (локальные субагенты
основной сессии) и Codex (OpenAI, через скил `codex:codex-delegate`). Пресеты вызываются как
`/claude-codex:<пресет>` или маршрутом карточки Listik `process:cc-<пресет>`.

Плагин ничего не копирует: общий протокол — ядро `plugins/feature-pipeline/references/pipeline-core.md`,
писатели ТЗ, исполнители и критик `pipeline-critic` — агенты `feature-pipeline:*`, внешние прогоны — скилы
`codex:*`, карточка и журнал — `listik:listik`. Своих агентов два — критики `claude-codex:pipeline-critic-xhigh`
и `claude-codex:pipeline-critic-medium` (Sonnet по умолчанию, модель задаётся параметром `model` в вызове).
Поэтому ставится только вместе с тремя плагинами того же маркетплейса:

```
/plugin marketplace add dmitry-fomin/listik
/plugin install listik@listik
/plugin install feature-pipeline@listik
/plugin install codex@listik
/plugin install claude-codex@listik
```

Нет хотя бы одного из них — пресет останавливается до первого действия (раздел «Внешние скилы» ядра).

## Пресеты

| Пресет | ТЗ | Критика (кворум) | Код | Приёмка |
| --- | --- | --- | --- | --- |
| `xhigh-pipeline` | Opus 5.5 xhigh | Sonnet 5.5 xhigh, Opus 5.5 high, GPT-6.1 Sol high; кворум — Sonnet и Sol, Opus в кворум не входит | Opus 5.5 xhigh | GPT-6 Astra high в Codex, коммитит судья |
| `high-pipeline` | Opus 5.5 high | Sonnet 5.5 high, Opus 5.5 medium, GPT-6.1 Sol high; кворум — Sonnet и Sol, Opus в кворум не входит | Opus 5.5 high | GPT-6 Astra high в Codex, коммитит судья |
| `medium-pipeline` | Opus 5.5 medium | Sonnet 5.5 medium, GPT-6.1 Sol medium; кворум — оба | Opus 5.5 medium | GPT-6 Astra high в Codex, коммитит судья |

xhigh и high — расклад автора 07.10.2026, medium — выведен по аналогии и утверждён автором.

## Обоснование: Hal

Метрика — `omniscienceHallucinationRate` Artificial Analysis (AA-Omniscience hallucination rate, меньше —
лучше). Для моделей Anthropic и GPT-6 Astra — снимок `plugins/feature-pipeline/references/models.json` от
2026-09-22; для GPT-6.1 Sol — живая страница AA, снято 2026-10-07 (тех же цифр раздел «sol-pipeline —
проверяющие от OpenAI» в `plugins/feature-pipeline/references/ROLES.md`).

| Модель (slug AA) | Роль | Hal |
| --- | --- | --- |
| `claude-opus-5-5-xhigh` | xhigh: ТЗ, код | 0.657 |
| `claude-opus-5-5-high` | high: ТЗ, код; xhigh: критик `opus` | 0.676 |
| `claude-opus-5-5-medium` | medium: ТЗ, код; high: критик `opus` | 0.684 |
| `claude-sonnet-5-5-xhigh` | xhigh: критик `sonnet` | 0.629 |
| `claude-sonnet-5-5-high` | high: критик `sonnet` | 0.646 |
| `claude-sonnet-5-5-medium` | medium: критик `sonnet` | 0.512 |
| `gpt-6-1-sol-high` | xhigh, high: критик `codex` | 0.494 |
| `gpt-6-1-sol-medium` | medium: критик `codex` | 0.516 |
| `gpt-6-astra-high` | все пресеты: судья | 0.448 |
