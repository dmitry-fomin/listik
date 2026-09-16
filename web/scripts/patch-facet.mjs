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

/**
 * Патч UiDrawer.vue: дровер выезжает, но не уезжает.
 *
 * Панель лежит во вложенном `<Transition :name="ui-drawer-slide-<side>">` внутри подложки,
 * у которой `v-if="isOpen"` на внешнем `ui-drawer-fade`. На закрытии Vue сносит всё поддерево
 * подложки разом, вложенный переход свой leave не проигрывает, и стили
 * `.ui-drawer-slide-*-leave-to` не применяются — панель исчезает рывком под уходящей подложкой.
 *
 * Лечится штатным приёмом Vue для вложенных переходов: внешнему `Transition` задаём явный
 * `:duration`, чтобы он дождался ухода панели, а сам уход описываем правилами от родительского
 * класса `.ui-drawer-fade-leave-*`. Длительности и токены — те же, что у входа.
 * Скрипт идемпотентен и запускается из postinstall.
 */
const drawer = new URL('../node_modules/@zoloto585/facet/src/components/UiDrawer/UiDrawer.vue', import.meta.url)
if (!existsSync(drawer)) {
  console.log('patch-facet: UiDrawer не найден, пропускаю')
} else {
  const before = readFileSync(drawer, 'utf8')
  let after = before

  // 180ms — --duration-base у подложки; панель уходит за --duration-fast (120ms) и успевает.
  after = after.replace(
    '<Transition name="ui-drawer-fade">',
    '<Transition name="ui-drawer-fade" :duration="180">',
  )

  const leaveRules = `
/* Уход панели вместе с подложкой: вложенный Transition на закрытии не срабатывает,
   поэтому задаём его правилами от родительского класса (патч проекта Listik). */
.ui-drawer-fade-leave-active .ui-drawer {
  transition: transform var(--duration-fast) var(--ease-out);
}
.ui-drawer-fade-leave-to .ui-drawer--left {
  transform: translateX(-100%);
}
.ui-drawer-fade-leave-to .ui-drawer--right {
  transform: translateX(100%);
}
.ui-drawer-fade-leave-to .ui-drawer--top {
  transform: translateY(-100%);
}
.ui-drawer-fade-leave-to .ui-drawer--bottom {
  transform: translateY(100%);
}

@media (prefers-reduced-motion: reduce) {
  .ui-drawer-fade-leave-active .ui-drawer {
    transition: none;
  }
}
`
  if (!after.includes('.ui-drawer-fade-leave-active .ui-drawer')) {
    after = after.replace(/\n<\/style>\s*$/, `${leaveRules}</style>\n`)
  }

  if (after === before) {
    console.log('patch-facet: UiDrawer — патч уже на месте')
  } else {
    writeFileSync(drawer, after)
    console.log('patch-facet: UiDrawer — добавлен уход панели при закрытии')
  }
}
