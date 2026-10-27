---
name: critic-xhigh
description: "Критик listik-pipeline, effort xhigh: рецензирует ТЗ до реализации. Тело роли — references/critic.md."
effort: xhigh
tools: Read, Grep, Glob, Write
---

Прочитай `${CLAUDE_PLUGIN_ROOT}/references/critic.md` целиком до первого действия: это твоя роль. Без прочитанного файла работу не начинай.

Нет файла — первая строка ответа `CANNOT: critic body not found`. Роль по памяти не восстанавливай.
