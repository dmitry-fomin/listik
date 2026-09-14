# Рейтинги моделей — снимки и откуда они берутся

Справочник для выбора модели под роль в пайплайне. Ничего не выдумываем по памяти:
цифры — из снимков, снимки — из скриптов рядом.

Каталог живёт внутри плагина `feature-pipeline` — здесь же `ROLES.md`, который
обосновывает расстановку моделей по ролям его конвейеров. Скилы плагина ссылаются
на эти файлы как `${CLAUDE_PLUGIN_ROOT}/references/<файл>`.

## Обновить

```bash
# из каталога этого файла: снимки пишутся рядом со скриптами
python3 fetch_aa.py          # Artificial Analysis: качество + цены + скорость
python3 fetch_openrouter.py  # OpenRouter: реальный прайс доступа
```

Оба без ключей. Перед любым разговором «какую модель подставить» — смотри дату
в поле `fetched` внутри `models.json`; старше пары недель — перекачай.

## Artificial Analysis (`models.csv` / `models.json`)

Официальный API (`/api/v2/data/llms/models`) требует ключ — отдаёт `401`.
Обход: **любая карточка модели** `https://artificialanalysis.ai/models/<slug>` встраивает
в RSC-пейлоад (`self.__next_f.push`) **полный каталог** — на снимке 646 записей,
включая варианты effort (`claude-opus-5-low/medium/high/xhigh`), цены, бенчмарки, скорость.

Важно: страница-список `/models` отдаёт только ~26 моделей, а `?models=<список>` фильтрует
на клиенте и на пейлоад не влияет. Поэтому ходим именно на карточку.

Что лежит в каждой записи: `intelligenceIndex`, разбивка по бенчам (`terminalbenchV40`,
`terminalbenchV21`, `scicode`, `livecodebench`, `lcr`, `tau2`, `gpqa`, `hle`, `aime25`,
`gdpval`, `omniscience` + `omniscienceBreakdown`), цены (вход/выход/кэш/blended),
`performanceByPromptType` (скорость и TTFT по длине промпта), `intelligenceIndexCostPerTask`
($ на задачу — честнее цены за токен), `contextWindowTokens`, даты, лицензия.

`models.csv` — плоская выжимка ключевых колонок; `models.json` — всё сырьё.

Хрупкость: парсер держится за формат RSC-пейлоада Next.js. Сломается вёрстка — скрипт
упадёт с «подозрительно мало моделей», а не отдаст тихо мусор.

## OpenRouter (`openrouter.csv` / `openrouter.json`)

`https://openrouter.ai/api/v1/models` — публично, без ключа. Цены за 1M токенов
(вход, выход, чтение/запись кэша, reasoning), контекст, модальности, batch-варианты.

## Другие источники, если понадобится глубже

| Источник | Как забрать |
| --- | --- |
| Aider polyglot leaderboard | `raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml` — YAML, отдаётся `200` |
| LMArena (человеческие предпочтения) | датасеты на HuggingFace, `huggingface.co/api/datasets/lmarena-ai/...` |
| SWE-bench / Terminal-Bench | лидерборды на сайтах проектов, стабильного JSON-эндпоинта нет — парсить страницу |
| Artificial Analysis API | ключ по запросу на сайте; тогда `/api/v2/data/llms/models` вместо парсинга |
