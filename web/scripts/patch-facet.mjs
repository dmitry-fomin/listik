/**
 * Патч кита @zoloto585/facet для typecheck.
 *
 * В UiSectionNav.vue импортируется `ref`, который не используется, а в нашем tsconfig
 * включён `noUnusedLocals` — vue-tsc ругается на файл внутри node_modules и валит
 * `npm run typecheck`. Кит поставляется исходниками, значит правка применима.
 * Скрипт идемпотентен и запускается из postinstall: после переустановки пакета
 * патч накладывается заново.
 */
import { readFileSync, writeFileSync, existsSync } from 'node:fs'

const target = new URL('../node_modules/@zoloto585/facet/src/components/UiSectionNav/UiSectionNav.vue', import.meta.url)
if (!existsSync(target)) {
  console.log('patch-facet: файл кита не найден, пропускаю')
  process.exit(0)
}
const src = readFileSync(target, 'utf8')
const next = src.replace(/import\s*\{([^}]*)\}\s*from\s*'vue'/, (all, names) => {
  const kept = names.split(',').map((n) => n.trim()).filter((n) => n && n !== 'ref')
  return `import { ${kept.join(', ')} } from 'vue'`
})
if (next === src) {
  console.log('patch-facet: патч уже на месте')
} else {
  writeFileSync(target, next)
  console.log('patch-facet: неиспользуемый импорт ref убран')
}
