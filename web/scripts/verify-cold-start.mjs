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
    const pill = row.querySelector('.ui-status-pill');
    return {
      key: row.querySelector('.listik-cold__key')?.textContent?.trim() ?? '',
      value: row.querySelector('.listik-cold__value')?.textContent?.trim() ?? '',
      tone: pill ? [...pill.classList].find((cls) => cls.startsWith('ui-status-pill--')) ?? null : null,
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
    const ok = row?.tone === 'ui-status-pill--success'
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
    const ok = row?.tone === 'ui-status-pill--warning'
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
    const ok = row?.tone === 'ui-status-pill--warning'
      && row.value === 'работа в master'
      && drawn.counter === '7 из 7'
    return { ok, expect: 'жёлтая строка «работа в master»', got: { row, counter: drawn.counter } }
  })

  // 4. Ни дерева, ни ветки — красная строка и красный счётчик, поле не заполнено.
  await record('дерево не указано — красная строка и счётчик 6 из 7', async () => {
    const drawn = await openCard('Холодный старт: дерево не указано', 'listik-cold-none')
    const row = worktreeRow(drawn)
    const ok = row?.tone === 'ui-status-pill--danger'
      && row.value === 'рабочее дерево не указано'
      && drawn.counter === '6 из 7'
      && drawn.badgeTone === 'ui-badge--danger'
    return {
      ok,
      expect: 'красная строка «рабочее дерево не указано», счётчик 6 из 7',
      got: { row, counter: drawn.counter, badgeTone: drawn.badgeTone },
    }
  })

  // 5. Точка-статус каждой строки — компонент кита, а не своя разметка.
  await record('точка-статус строки — UiStatusPill кита', async () => {
    const drawn = await openCard('Холодный старт: работа в main', 'listik-cold-main')
    const pills = await evaluate(
      `document.querySelectorAll('.ui-drawer .listik-cold__row .ui-status-pill').length`,
    )
    const ok = pills === drawn.rows.length && drawn.rows.length > 0
    return { ok, expect: `${drawn.rows.length} пилюль на ${drawn.rows.length} строк`, got: pills }
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
