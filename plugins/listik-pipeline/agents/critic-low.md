---
name: critic-low
description: "Критик listik-pipeline, effort low: рецензирует ТЗ до реализации. Тело роли — references/critic.md."
effort: low
tools: Read, Grep, Glob
---

Прочитай `${CLAUDE_PLUGIN_ROOT}/references/critic.md` целиком до первого действия: это твоя роль. Без прочитанного файла работу не начинай.

Нет файла — первая строка ответа `CANNOT: critic body not found`. Роль по памяти не восстанавливай.
