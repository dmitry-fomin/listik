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
 * Сценарии: 1–2 — подвал карточки доски; 3–7 — блок «Кто держит» панели
 * (listik-6g0q). Строка «делает» — только когда этап делает не сам держатель:
 * title роли, а за ним через пробел серый `detail` (effort) — только если
 * label ячейки не встречается словом в title (listik-erjx), без разделителя « · »;
 * строка «оркестратор» — всегда, когда поле заполнено:
 *   3. `listik-executor` — «делает» ровно «DeepSeek — код» без серого уточнения
 *      (label «DeepSeek» уже в title), держит Claude, оркестратор `claude`
 *      (совпадает с держателем, но строка есть);
 *   4. `listik-executor-same` — роль `spec` вендора `claude`, держит `claude`:
 *      «делает» нет; оркестратор `listik` — запасное значение при пустой подписи;
 *   5. `listik-routes-fresh` — оркестратора нет, строки «оркестратор» нет;
 *   6. `listik-executor-glm` — роль `critic` вендора `glm`, держит `agent:pi-glm`:
 *      «делает» нет, держит «pi · GLM»;
 *   7. `listik-executor-free` — роль `judge`, без держателя: «делает Проверка Судья»
 *      («Судья» — серый `.listik-drawer__executor-detail`), держит «никто»,
 *      тултип подвала «исполнитель этапа: Проверка (Судья)».
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
const SAME = 'listik-executor-same'
const SAME_TITLE = 'Этап делает сам держатель'
const GLM = 'listik-executor-glm'
const GLM_TITLE = 'Критик GLM держит сам'
const FREE = 'listik-executor-free'
const FREE_TITLE = 'Этап без держателя'
/** title и label роли `judge` в `low-pipeline` мока. */
const JUDGE_TITLE = 'Проверка'
const JUDGE_LABEL = 'Судья'

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
  let detail = null;
  for (const dt of drawer.querySelectorAll('.listik-dl dt')) {
    const dd = dt.nextElementSibling;
    const key = dt.textContent.trim();
    rows[key] = dd ? dd.textContent.replace(/\\s+/g, ' ').trim() : null;
    if (key === 'делает' && dd) {
      const el = dd.querySelector('.listik-drawer__executor-detail');
      detail = el
        ? { text: el.textContent.trim(), color: getComputedStyle(el).color, ddColor: getComputedStyle(dd).color }
        : null;
    }
  }
  return {
    open: true,
    id: drawer.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    rows,
    detail,
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

  /**
   * Открыть панель задачи карточкой доски и дождаться, пока `ready(rows)` не
   * станет истинным. Открытая панель делает фон inert — сначала закрыть её.
   */
  const openDrawer = async (title, id, ready) => {
    if ((await state()).open) {
      await pressEscape()
      await waitFor(async () => ((await state()).open === false ? true : null))
    }
    const clicked = await clickCard(title)
    const drawer = await waitFor(async () => {
      const seen = await state()
      return seen.id === id && ready(seen.rows) ? seen : null
    })
    const last = drawer ?? (await state())
    return { clicked, drawer, rows: last.rows ?? {}, detail: last.detail ?? null }
  }

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
      && foot.tooltip === `исполнитель этапа: ${IMPL_TITLE}`
    return {
      ok,
      expect: `«делает ${IMPL_TITLE}» + «· держит Claude» + тултип «исполнитель этапа: ${IMPL_TITLE}»`,
      got: foot ?? (await cardFoot(evaluate, CARD_TITLE)),
    }
  })

  // 2. Карточка без маршрута и этапа — старая подпись «без держателя».
  await record('карточка без маршрута: подвал как раньше', async () => {
    const foot = await cardFoot(evaluate, FRESH_TITLE)
    const ok = Boolean(foot) && foot.holder === 'без держателя' && foot.executor === null
    return { ok, expect: 'без держателя', got: foot }
  })

  // 3. Панель задачи: строка «делает» ровно с title роли — label «DeepSeek» уже
  //    словом в title, серого уточнения нет; перед «держит»; оркестратор `claude`
  //    виден, хотя совпадает с держателем.
  await record('панель задачи: строка «делает» с исполнителем этапа', async () => {
    const { clicked, drawer, rows, detail } = await openDrawer(CARD_TITLE, CARD, (seen) => seen['делает'] && seen['оркестратор'])
    const ok = Boolean(clicked)
      && Boolean(drawer)
      && rows['делает'] === IMPL_TITLE
      && detail === null
      && Boolean(rows['держит']?.includes('Claude'))
      && rows['оркестратор'] === 'claude'
    return {
      ok,
      expect: `делает «${IMPL_TITLE}» без .listik-drawer__executor-detail, держит «Claude …», оркестратор «claude»`,
      got: { clicked, делает: rows['делает'], detail, держит: rows['держит'], оркестратор: rows['оркестратор'] },
    }
  })

  // 4. Этап делает сам держатель (роль `spec` вендора `claude`, держит `claude`):
  //    строки «делает» нет; пустая подпись оркестратора — запасное значение ключа.
  await record('панель задачи: держатель сам делает этап — «делает» нет', async () => {
    const { clicked, drawer, rows } = await openDrawer(SAME_TITLE, SAME, (seen) => seen['держит'] && seen['оркестратор'])
    const ok = Boolean(clicked)
      && Boolean(drawer)
      && !('делает' in rows)
      && Boolean(rows['держит']?.includes('Claude'))
      && rows['оркестратор'] === 'listik'
    return {
      ok,
      expect: 'нет «делает», держит «Claude …», оркестратор «listik»',
      got: { clicked, делает: rows['делает'], держит: rows['держит'], оркестратор: rows['оркестратор'] },
    }
  })

  // 5. Карточка без оркестратора — строки «оркестратор» нет.
  await record('панель задачи: без оркестратора — строки нет', async () => {
    const { clicked, drawer, rows } = await openDrawer(FRESH_TITLE, FRESH, (seen) => seen['держит'])
    const ok = Boolean(clicked) && Boolean(drawer) && !('оркестратор' in rows)
    return {
      ok,
      expect: 'нет строки «оркестратор»',
      got: { clicked, оркестратор: rows['оркестратор'], держит: rows['держит'] },
    }
  })

  // 6. Роль `critic` вендора `glm`, держит `agent:pi-glm` — это она же: «делает» нет.
  await record('панель задачи: GLM-критик держит сам — «делает» нет', async () => {
    const { clicked, drawer, rows } = await openDrawer(GLM_TITLE, GLM, (seen) => seen['держит'])
    const ok = Boolean(clicked)
      && Boolean(drawer)
      && !('делает' in rows)
      && Boolean(rows['держит']?.includes('pi · GLM'))
    return {
      ok,
      expect: 'нет «делает», держит «pi · GLM …»',
      got: { clicked, делает: rows['делает'], держит: rows['держит'] },
    }
  })

  // 7. Этап без держателя — «делает» с title роли и серым label («Судья» нет
  //    в «Проверка»), «держит никто»; тултип подвала с уточнением в скобках.
  await record('панель задачи: этап без держателя — «делает» видна', async () => {
    const foot = await cardFoot(evaluate, FREE_TITLE)
    const { clicked, drawer, rows, detail } = await openDrawer(FREE_TITLE, FREE, (seen) => seen['делает'] && seen['держит'])
    const tooltip = `исполнитель этапа: ${JUDGE_TITLE} (${JUDGE_LABEL})`
    const ok = Boolean(clicked)
      && Boolean(drawer)
      && rows['делает'] === `${JUDGE_TITLE} ${JUDGE_LABEL}`
      && detail?.text === JUDGE_LABEL
      && Boolean(detail.color)
      && detail.color !== detail.ddColor
      && Boolean(rows['держит']?.includes('никто'))
      && foot?.tooltip === tooltip
    return {
      ok,
      expect: `делает «${JUDGE_TITLE} ${JUDGE_LABEL}» (серый «${JUDGE_LABEL}»), держит «никто», тултип «${tooltip}»`,
      got: { clicked, делает: rows['делает'], detail, держит: rows['держит'], tooltip: foot?.tooltip ?? null },
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
