# claude-codex

Линейка пресетов конвейера ТЗ → критика → код → приёмка, где работают только Claude (локальные субагенты
основной сессии) и Codex (OpenAI, через скил `codex:codex-delegate`). Пресеты вызываются как
`/claude-codex:<пресет>` или маршрутом карточки Listik `process:cc-<пресет>`.

Плагин ничего не копирует: общий протокол — ядро `plugins/feature-pipeline/references/pipeline-core.md`,
писатели ТЗ, исполнители и критик `pipeline-critic` — агенты `feature-pipeline:*`, внешние прогоны — скилы
`codex:*`, карточка и журнал — `listik:listik`. Своих агентов три — критики `claude-codex:pipeline-critic-xhigh`
и `claude-codex:pipeline-critic-medium` (Sonnet по умолчанию, модель задаётся параметром `model` в вызове) и
исполнитель `claude-codex:pipeline-implementer-low` (Opus low, `model` в вызове не передаётся).
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

Маршруты Listik этих пресетов — `cc-<имя>` (`cc-xhigh-pipeline` … `cc-nano-pipeline`): ключ маршрута уникален, а имена скилов совпадают с feature-pipeline; команда маршрута зовёт `/claude-codex:<имя>`.

| Пресет | ТЗ | Критика (кворум) | Код | Приёмка |
| --- | --- | --- | --- | --- |
| `xhigh-pipeline` | Opus 5.5 xhigh | Sonnet 5.5 xhigh, Opus 5.5 high, GPT-6.1 Sol high; кворум — Sonnet и Sol, Opus в кворум не входит | Opus 5.5 xhigh | GPT-6 Astra high в Codex, коммитит судья |
| `high-pipeline` | Opus 5.5 high | Sonnet 5.5 high, Opus 5.5 medium, GPT-6.1 Sol high; кворум — Sonnet и Sol, Opus в кворум не входит | Opus 5.5 high | GPT-6 Astra high в Codex, коммитит судья |
| `medium-pipeline` | Opus 5.5 medium | Sonnet 5.5 medium, GPT-6.1 Sol medium; кворум — оба | Opus 5.5 medium | GPT-6 Astra high в Codex, коммитит судья |
| `low-pipeline` | Opus 5.5 low | GPT-6.1 Sol medium; кворум — он один | Opus 5.5 low | GPT-6 Astra medium в Codex, коммитит судья |
| `xlow-pipeline` | — | — | Opus 5.5 medium (два хода) | GPT-6 Astra medium в Codex, коммитит судья |
| `nano-pipeline` | — | — | Opus 5.5 medium (два хода) | GPT-6 Astra medium в Codex, коммитит судья |

xhigh и high — расклад автора 07.10.2026, medium — выведен по аналогии и утверждён автором.
low/xlow/nano утверждены автором 07.10.2026 — код пишет Opus (low у low, medium у xlow и nano), судья — Codex Astra
medium. xlow и nano по составу совпадают; nano — для самых мелких правок (одна-две правки в одном месте).

## Обоснование: Hal

Метрика — `omniscienceHallucinationRate` Artificial Analysis (AA-Omniscience hallucination rate, меньше —
лучше). Для моделей Anthropic и GPT-6 Astra — снимок `plugins/feature-pipeline/references/models.json` от
2026-09-29 (поле `fetched`; 2026-09-22 — дата релиза Opus 5.5 в нём, а не снимка); для GPT-6.1 Sol — живая страница AA, снято 2026-10-07 (тех же цифр раздел «sol-pipeline —
проверяющие от OpenAI» в `plugins/feature-pipeline/references/ROLES.md`).

| Модель (slug AA) | Роль | Hal |
| --- | --- | --- |
| `claude-opus-5-5-xhigh` | xhigh: ТЗ, код | 0.657 |
| `claude-opus-5-5-high` | high: ТЗ, код; xhigh: критик `opus` | 0.676 |
| `claude-opus-5-5-medium` | medium: ТЗ, код; high: критик `opus`; xlow, nano: код | 0.684 |
| `claude-opus-5-5-low` | low: ТЗ, код | 0.676 |
| `claude-sonnet-5-5-xhigh` | xhigh: критик `sonnet` | 0.629 |
| `claude-sonnet-5-5-high` | high: критик `sonnet` | 0.646 |
| `claude-sonnet-5-5-medium` | medium: критик `sonnet` | 0.512 |
| `gpt-6-1-sol-high` | xhigh, high: критик `codex` | 0.494 |
| `gpt-6-1-sol-medium` | medium, low: критик `codex` | 0.516 |
| `gpt-6-astra-high` | xhigh, high, medium: судья | 0.448 |
| `gpt-6-astra-medium` | low, xlow, nano: судья | 0.465 |
