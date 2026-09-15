/**
 * Проверка строки «worktree · branch» блока «Холодный старт» панели задачи
 * (`web/src/components/TaskDrawer.vue` + справочник `worktreeState` в
 * `web/src/lib/dictionaries.ts`). Три состояния строки:
 *
 * - указаны дерево/ветка — зелёная (`success`) с путём и веткой;
 * - работа в основной ветке (`listik set <id> worktree=main`, она же `master`) —
 *   жёлтая (`warning`) с текстом «работа в main»/«работа в master»;
 * - ничего не указано — красная (`danger`) с текстом «рабочее дерево не указано».
 *
 * Счётчик «Холодный старт N из M» считает жёлтое состояние заполненным полем:
 * у трёх первых задач среза он 7 из 7, у карточки без дерева — 6 из 7.
 *
 * Поднимает mock-api в режиме `--cold` (четыре задачи, отличающиеся только
 * `worktree`/`branch`), отдаёт собранный `web/dist` и гоняет сценарии в headless
 * Chrome через CDP.
 *
 * Запуск: node scripts/verify-cold-start.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--cold`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя, ok, что увидели) и `consoleErrors`.
 * Код возврата 1, если хоть один сценарий не прошёл.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { cdpTarget, connect, freePort, serveDist, startChrome, startMock } from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9500 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-cold-'))

/* ── Сценарии ───────────────────────────────────────────────────────────── */

/** Состояние панели задачи: строка «worktree · branch», её тон и счётчик холодного старта. */
const COLD_STATE = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  const section = [...root.querySelectorAll('.listik-section')].find(
    (el) => el.querySelector('.listik-section__title')?.textContent?.trim() === 'Холодный старт',
  );
  if (!section) return { open: true, cold: false };
  const rows = [...section.querySelectorAll('.listik-cold__row')].map((row) => {
    const dot = row.querySelector('.listik-cold__dot');
    return {
      key: row.querySelector('.listik-cold__key')?.textContent?.trim() ?? '',
      value: row.querySelector('.listik-cold__value')?.textContent?.trim() ?? '',
      tone: dot ? [...dot.classList].find((cls) => cls.startsWith('listik-cold__dot--')) ?? null : null,
    };
  });
  const badge = section.querySelector('.ui-badge');
  const badgeTone = badge
    ? [...badge.classList].find((cls) => cls.startsWith('ui-badge--') && !['ui-badge--sm', 'ui-badge--md'].includes(cls))
    : null;
  return {
    open: true,
    cold: true,
    id: root.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    rows,
    counter: badge?.textContent?.replace(/\\s+/g, ' ').trim() ?? null,
    badgeTone: badgeTone ?? null,
  };
})()`

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)
let mock = null
let staticServer = null
let chrome = null
const page = process.argv[2] ?? null

try {
  let url = page
  if (!url) {
    if (!existsSync(join(dist, 'index.html'))) {
      throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
    }
    const apiPort = await freePort()
    const pagePort = await freePort()
    mock = await startMock(apiPort, root, ['--cold'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(COLD_STATE)

  const waitDrawer = (id, timeout) => waitFor(async () => ((await state()).id === id ? true : null), timeout)

  const clickCard = (title) =>
    evaluate(`(() => {
      const card = [...document.querySelectorAll('.listik-task-card')]
        .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)});
      if (!card) return false;
      card.click();
      return true;
    })()`)

  async function pressEscape() {
    for (const type of ['rawKeyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent', {
        type,
        key: 'Escape',
        code: 'Escape',
        windowsVirtualKeyCode: 27,
        nativeVirtualKeyCode: 27,
      })
    }
  }

  await send('Page.navigate', { url })
  const boardReady = await waitFor(
    async () => ((await evaluate(`document.querySelectorAll('.listik-task-card').length`)) > 0 ? true : null),
    15000,
  )
  if (!boardReady) {
    const diagnostics = await evaluate(`(() => ({
      url: location.href,
      text: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 400),
      cards: document.querySelectorAll('.listik-task-card').length,
    }))()`)
    throw new Error(`доска не отрисовалась: ${JSON.stringify(diagnostics)} ${JSON.stringify(consoleErrors)}`)
  }

  /** Открыть задачу карточкой доски: если панель уже открыта, фон inert — сначала закрыть. */
  const openCard = async (title, id) => {
    if ((await state()).open) {
      await pressEscape()
      await waitFor(async () => ((await state()).open === false ? true : null))
    }
    await clickCard(title)
    const ok = await waitDrawer(id)
    if (!ok) throw new Error(`панель не открыла ${id}: ${JSON.stringify(await state())}`)
    return state()
  }

  const worktreeRow = (drawn) => drawn.rows.find((row) => row.key === 'worktree · branch') ?? null

  // 1. Указаны дерево и ветка — зелёная строка с путём и веткой, счётчик полный.
  await record('дерево и ветка — зелёная строка с путём и веткой', async () => {
    const drawn = await openCard('Холодный старт: дерево и ветка', 'listik-cold-branch')
    const row = worktreeRow(drawn)
    const ok = row?.tone === 'listik-cold__dot--success'
      && row.value === '/Users/dmitry.fomin/Projects/Listik-wt/listik-cold-branch · task/listik-cold-branch'
      && drawn.counter === '7 из 7'
      && drawn.badgeTone === 'ui-badge--success'
    return {
      ok,
      expect: 'зелёная строка, «путь · ветка», счётчик 7 из 7',
      got: { row, counter: drawn.counter, badgeTone: drawn.badgeTone },
    }
  })

  // 2. Работа в основной ветке (маркер `worktree=main`) — жёлтая строка «работа в main»,
  //    и жёлтое состояние считается заполненным полем холодного старта.
  await record('работа в main — жёлтая строка, счётчик считает её заполненной', async () => {
    const drawn = await openCard('Холодный старт: работа в main', 'listik-cold-main')
    const row = worktreeRow(drawn)
    const ok = row?.tone === 'listik-cold__dot--warning'
      && row.value === 'работа в main'
      && drawn.counter === '7 из 7'
      && drawn.badgeTone === 'ui-badge--success'
    return {
      ok,
      expect: 'жёлтая строка «работа в main», счётчик 7 из 7',
      got: { row, counter: drawn.counter, badgeTone: drawn.badgeTone },
    }
  })

  // 3. Основная ветка может называться master — подпись идёт по фактическому маркеру.
  await record('работа в master — жёлтая строка с подписью master', async () => {
    const drawn = await openCard('Холодный старт: работа в master', 'listik-cold-master')
    const row = worktreeRow(drawn)
    const ok = row?.tone === 'listik-cold__dot--warning'
      && row.value === 'работа в master'
      && drawn.counter === '7 из 7'
    return { ok, expect: 'жёлтая строка «работа в master»', got: { row, counter: drawn.counter } }
  })

  // 4. Ни дерева, ни ветки — красная строка и красный счётчик, поле не заполнено.
  await record('дерево не указано — красная строка и счётчик 6 из 7', async () => {
    const drawn = await openCard('Холодный старт: дерево не указано', 'listik-cold-none')
    const row = worktreeRow(drawn)
    const ok = row?.tone === 'listik-cold__dot--danger'
      && row.value === 'рабочее дерево не указано'
      && drawn.counter === '6 из 7'
      && drawn.badgeTone === 'ui-badge--danger'
    return {
      ok,
      expect: 'красная строка «рабочее дерево не указано», счётчик 6 из 7',
      got: { row, counter: drawn.counter, badgeTone: drawn.badgeTone },
    }
  })

  // 5. Точка-статус каждой строки — своя разметка `.listik-cold__dot`, пилюли кита в строках нет.
  await record('точка-статус строки — .listik-cold__dot вместо UiStatusPill', async () => {
    const drawn = await openCard('Холодный старт: работа в main', 'listik-cold-main')
    const dots = await evaluate(
      `document.querySelectorAll('.ui-drawer .listik-cold__row .listik-cold__dot').length`,
    )
    const pills = await evaluate(
      `document.querySelectorAll('.ui-drawer .listik-cold__row .ui-status-pill').length`,
    )
    const ok = dots === drawn.rows.length && drawn.rows.length > 0 && pills === 0
    return {
      ok,
      expect: `${drawn.rows.length} точек на ${drawn.rows.length} строк, пилюль 0`,
      got: { dots, pills },
    }
  })

  // 6. У последней строки нет своей полосы (под ней — разделитель блока, а не вторая
  //    полоса), у остальных она есть; обрезка значения слева не переставляет символы:
  //    у длинного пути и у строки-состояния первый символ остаётся левее последнего.
  const valueOrder = `(() => {
    const row = [...document.querySelectorAll('.ui-drawer .listik-cold__row')]
      .find((el) => el.querySelector('.listik-cold__key')?.textContent?.trim() === 'worktree · branch');
    const value = row?.querySelector('.listik-cold__value');
    if (!value) return null;
    const text = document.createTreeWalker(value, NodeFilter.SHOW_TEXT).nextNode();
    if (!text?.length) return null;
    const edge = (from, to) => {
      const range = document.createRange();
      range.setStart(text, from);
      range.setEnd(text, to);
      return range.getBoundingClientRect();
    };
    return {
      text: text.textContent,
      truncated: value.scrollWidth > value.clientWidth,
      firstLeft: edge(0, 1).left,
      lastLeft: edge(text.length - 1, text.length).left,
    };
  })()`

  const orderOk = (order) => Boolean(order) && order.firstLeft < order.lastLeft

  await record('последняя строка без полосы, обрезка слева не переставляет символы', async () => {
    const drawn = await openCard('Холодный старт: дерево и ветка', 'listik-cold-branch')
    const borders = await evaluate(
      `[...document.querySelectorAll('.ui-drawer .listik-cold__row')]
        .map((row) => getComputedStyle(row).borderBottomWidth)`,
    )
    const path = await evaluate(valueOrder)
    const stateRow = await openCard('Холодный старт: работа в main', 'listik-cold-main').then(() => evaluate(valueOrder))
    const borderOk = borders.length === drawn.rows.length
      && borders.slice(0, -1).every((width) => width !== '0px')
      && borders[borders.length - 1] === '0px'
    const ok = borderOk && path?.truncated === true && orderOk(path) && orderOk(stateRow)
    return {
      ok,
      expect: `полоса у всех строк кроме последней (${drawn.rows.length}), путь обрезан слева, порядок символов прежний`,
      got: { borders, path, state: stateRow },
    }
  })

  report.consoleErrors = consoleErrors
  socket.close()
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  chrome?.kill()
  staticServer?.close()
  mock?.kill()
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  } catch {
    /* временный профиль уберёт система */
  }
}

console.log(JSON.stringify(report, null, 2))
const failed = report.cases.filter((item) => !item.ok).length
if (report.error || failed > 0) process.exitCode = 1
