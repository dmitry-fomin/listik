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
import { spawn } from 'node:child_process'
import { createServer, request as httpRequest } from 'node:http'
import { createReadStream, existsSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

/**
 * Статика собранного приложения: SPA-фолбэк на index.html плюс прокси `/api/*`
 * на мок. Прокси, а не `VITE_API_BASE`, — чтобы страница и API были одного
 * origin, как в проде (сборка отдаётся тем же сервером Listik).
 */
function serveDist(port, apiPort) {
  const types = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.woff2': 'font/woff2',
  }
  const server = createServer((request, response) => {
    const path = decodeURIComponent((request.url ?? '/').split('?')[0])
    if (path.startsWith('/api/')) {
      const proxy = httpRequest(
        {
          host: '127.0.0.1',
          port: apiPort,
          path: request.url,
          method: request.method,
          headers: { ...request.headers, host: `127.0.0.1:${apiPort}` },
        },
        (upstream) => {
          response.writeHead(upstream.statusCode ?? 502, upstream.headers)
          upstream.pipe(response)
        },
      )
      proxy.on('error', () => {
        response.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({ ok: false, error: 'mock-api недоступен' }))
      })
      request.pipe(proxy)
      return
    }
    const file = join(dist, normalize(path).replace(/^(\.\.[/\\])+/, ''))
    const target = path !== '/' && existsSync(file) && statSync(file).isFile() ? file : join(dist, 'index.html')
    response.writeHead(200, { 'Content-Type': types[extname(target)] ?? 'application/octet-stream' })
    createReadStream(target).pipe(response)
  })
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)))
}

async function startMock(port) {
  const child = spawn(
    process.execPath,
    [join(root, 'scripts', 'mock-api.mjs'), String(port), '--cold'],
    { stdio: 'inherit' },
  )
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`)
      if (response.ok) return child
    } catch {
      /* мок ещё поднимается */
    }
    await sleep(100)
  }
  child.kill()
  throw new Error('mock-api не поднялся')
}

/* ── CDP ────────────────────────────────────────────────────────────────── */

const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9500 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-cold-'))

async function cdpTarget() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${chromePort}/json/list`)
      const page = (await response.json()).find((item) => item.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch {
      /* chrome ещё поднимается */
    }
    await sleep(250)
  }
  throw new Error('не дождались CDP-таргета Chrome')
}

function connect(url) {
  const socket = new WebSocket(url)
  const pending = new Map()
  const consoleErrors = []
  let nextId = 1
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  socket.addEventListener('message', (message) => {
    const data = JSON.parse(message.data)
    if (data.id && pending.has(data.id)) {
      const { resolve, reject } = pending.get(data.id)
      pending.delete(data.id)
      data.error ? reject(new Error(JSON.stringify(data.error))) : resolve(data.result)
      return
    }
    if (data.method === 'Runtime.exceptionThrown') {
      consoleErrors.push(data.params?.exceptionDetails?.exception?.description ?? 'исключение')
    }
    if (data.method === 'Runtime.consoleAPICalled' && data.params?.type === 'error') {
      consoleErrors.push((data.params.args ?? []).map((arg) => arg.value ?? arg.description).join(' '))
    }
  })
  const send = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = nextId++
      pending.set(id, { resolve, reject })
      socket.send(JSON.stringify({ id, method, params }))
    })
  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
    return result.result.value
  }
  return { socket, ready, send, evaluate, consoleErrors }
}

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
    mock = await startMock(apiPort)
    staticServer = await serveDist(pagePort, apiPort)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = spawn(
    chromePath,
    [
      '--headless=old',
      '--disable-gpu',
      '--window-size=1600,1100',
      '--no-sandbox',
      '--disable-dev-shm-usage',
      `--remote-debugging-port=${chromePort}`,
      `--user-data-dir=${profile}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget())
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(COLD_STATE)

  async function waitFor(check, timeout = 5000) {
    const until = Date.now() + timeout
    let last = null
    while (Date.now() < until) {
      last = await check()
      if (last) return last
      await sleep(50)
    }
    return last
  }

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

  /** Сценарий не роняет прогон: его ошибка попадает в отчёт как провал. */
  const record = async (name, run) => {
    try {
      report.cases.push({ name, ...(await run()) })
    } catch (error) {
      report.cases.push({ name, ok: false, got: String(error?.message ?? error) })
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
