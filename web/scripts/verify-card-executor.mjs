/**
 * Проверка подписи «делает …» — планового исполнителя этапа из ролей маршрута
 * (`web/src/lib/executors.ts`, listik-kwm3): на карточке доски и в панели задачи
 * показывается тот, кто реально делает этап, а не держатель-оркестратор.
 *
 * Поднимает mock-api в режиме `--routes` (карточка `listik-executor` на s3-impl
 * конвейера `low-pipeline` с держателем `claude`, плюс `listik-routes-fresh`
 * без маршрута), отдаёт собранный `web/dist` и гоняет сценарии в headless
 * Chrome через CDP.
 *
 * Запуск: node scripts/verify-card-executor.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--routes`).
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
const chromePort = 9400 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-executor-'))
const CARD = 'listik-executor'
const CARD_TITLE = 'Этап делает роль маршрута'
const FRESH = 'listik-routes-fresh'
const FRESH_TITLE = 'Заведена без маршрута'
/** title роли `impl` в `low-pipeline` мока — должен попасть в «делает …». */
const IMPL_TITLE = 'DeepSeek — код'

/* ── Сценарии ───────────────────────────────────────────────────────────── */

/** Подвал карточки по её aria-label: подпись держателя/исполнителя и тултип. */
const cardFoot = (evaluate, title) =>
  evaluate(`(() => {
    const card = [...document.querySelectorAll('.listik-task-card')]
      .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)});
    if (!card) return null;
    const clean = (el) => el?.textContent?.replace(/\\s+/g, ' ').trim() ?? null;
    const executor = card.querySelector('.listik-task-card__executor');
    return {
      holder: clean(card.querySelector('.listik-task-card__holder')),
      executor: clean(executor),
      tooltip: executor?.getAttribute('title') ?? null,
    };
  })()`)

/** Панель задачи: id в шапке и пары dt→dd всех «listik-dl» (в т.ч. «делает»). */
const DRAWER_STATE = `(() => {
  const drawer = document.querySelector('.ui-drawer');
  if (!drawer) return { open: false };
  const rows = {};
  for (const dt of drawer.querySelectorAll('.listik-dl dt')) {
    const dd = dt.nextElementSibling;
    rows[dt.textContent.trim()] = dd ? dd.textContent.replace(/\\s+/g, ' ').trim() : null;
  }
  return {
    open: true,
    id: drawer.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    rows,
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
    mock = await startMock(apiPort, root, ['--routes'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(DRAWER_STATE)

  const clickCard = (title) =>
    evaluate(`(() => {
      const card = [...document.querySelectorAll('.listik-task-card')]
        .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)});
      if (!card) return false;
      card.click();
      return true;
    })()`)

  await send('Page.navigate', { url })
  // Ждём, пока доска отрисует карточки (мок отвечает сразу, запас — на шрифты и SSE).
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

  // 1. Карточка на s3-impl конвейера: в подвале «делает <роль impl>», а держатель
  //    ушёл в muted-хвост — голого «Claude» как единственной подписи нет.
  await record('карточка с маршрутом: подвал показывает исполнителя роли', async () => {
    const foot = await waitFor(async () => {
      const seen = await cardFoot(evaluate, CARD_TITLE)
      return seen?.executor?.includes(IMPL_TITLE) ? seen : null
    })
    const ok = Boolean(foot)
      && foot.executor.startsWith('делает')
      && foot.holder.includes('держит Claude')
      && !foot.holder.startsWith('Claude')
      && Boolean(foot.tooltip?.startsWith('исполнитель этапа:'))
    return {
      ok,
      expect: `«делает ${IMPL_TITLE}» + «· держит Claude» + тултип «исполнитель этапа: …»`,
      got: foot ?? (await cardFoot(evaluate, CARD_TITLE)),
    }
  })

  // 2. Карточка без маршрута и этапа — старая подпись «без держателя».
  await record('карточка без маршрута: подвал как раньше', async () => {
    const foot = await cardFoot(evaluate, FRESH_TITLE)
    const ok = Boolean(foot) && foot.holder === 'без держателя' && foot.executor === null
    return { ok, expect: 'без держателя', got: foot }
  })

  // 3. Панель задачи: строка «делает» с title роли перед «держит».
  await record('панель задачи: строка «делает» с исполнителем этапа', async () => {
    const clicked = await clickCard(CARD_TITLE)
    const drawer = await waitFor(async () => {
      const seen = await state()
      return seen.id === CARD && seen.rows['делает'] ? seen : null
    })
    const rows = drawer?.rows ?? (await state()).rows
    const ok = Boolean(clicked)
      && Boolean(drawer)
      && rows['делает']?.includes(IMPL_TITLE)
      && Boolean(rows['держит']?.includes('Claude'))
    return {
      ok,
      expect: `делает «${IMPL_TITLE}», держит «Claude …»`,
      got: { clicked, делает: rows?.['делает'], держит: rows?.['держит'] },
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
