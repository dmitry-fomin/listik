/**
 * Проверка кнопки удаления задачи в панели (`TaskDrawer` → `DELETE /api/tasks/{id}`).
 *
 * Поднимает `scripts/mock-api.mjs`, отдаёт собранный `web/dist` и гоняет
 * сценарий в headless Chrome через CDP. Ничего в боевой базе не трогает.
 *
 * Запуск: node scripts/verify-task-delete.mjs
 *   Нужен собранный `web/dist` (`npx vite build --configLoader runner`).
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

const OPEN_ID = 'listik-web-a1b2'
const OPEN_TITLE = 'Собрать доску канбан для трекера'
const OTHER_ID = 'listik-api-c3d4'
const OTHER_TITLE = 'Отдать needs_you одной лентой'

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
    if (path.startsWith('/api/') || path.startsWith('/__')) {
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
  const child = spawn(process.execPath, [join(root, 'scripts', 'mock-api.mjs'), String(port)], {
    stdio: 'inherit',
  })
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

async function mockCall(apiPort, path, body) {
  const response = await fetch(`http://127.0.0.1:${apiPort}${path}`, {
    method: body === undefined ? 'GET' : 'POST',
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json()
  if (!payload.ok) throw new Error(`${path}: ${payload.error ?? 'ошибка мока'}`)
  return payload.data
}

const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9400 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-task-delete-'))

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

const DRAWER_STATE = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  return {
    open: true,
    id: root.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    title: root.querySelector('.listik-drawer__title')?.textContent?.trim() ?? null,
  };
})()`

const DIALOG_STATE = `(() => {
  const dialog = [...document.querySelectorAll('[role=alertdialog]')].find((el) =>
    (el.textContent || '').includes('Удалить задачу'),
  );
  if (!dialog) return { open: false };
  return {
    open: true,
    text: dialog.textContent.replace(/\\s+/g, ' ').trim(),
    confirm: [...dialog.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Удалить'),
    cancel: [...dialog.querySelectorAll('button')].some((b) => b.textContent.trim() === 'Отмена'),
  };
})()`

const report = { cases: [], consoleErrors: [] }
let mock = null
let staticServer = null
let chrome = null

try {
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
  }
  const apiPort = await freePort()
  const pagePort = await freePort()
  mock = await startMock(apiPort)
  staticServer = await serveDist(pagePort, apiPort)
  const url = `http://127.0.0.1:${pagePort}/?token=mock-token`

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

  const state = () => evaluate(DRAWER_STATE)
  const dialog = () => evaluate(DIALOG_STATE)

  async function waitFor(check, timeout = 6000) {
    const until = Date.now() + timeout
    let last = null
    while (Date.now() < until) {
      last = await check()
      if (last) return last
      await sleep(50)
    }
    return last
  }

  const cardExists = (title) =>
    evaluate(
      `[...document.querySelectorAll('.listik-task-card')]` +
        `.some((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)})`,
    )

  const clickCard = (title) =>
    evaluate(`(() => {
      const card = [...document.querySelectorAll('.listik-task-card')]
        .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)});
      if (!card) return false;
      card.click();
      return true;
    })()`)

  const clickRemove = (id) =>
    evaluate(`(() => {
      const btn = [...document.querySelectorAll('button')].find(
        (b) => (b.getAttribute('aria-label') || '') === 'Удалить задачу ' + ${JSON.stringify(id)},
      );
      if (!btn) return false;
      btn.click();
      return true;
    })()`)

  const clickDialog = (label) =>
    evaluate(`(() => {
      const dialog = [...document.querySelectorAll('[role=alertdialog]')].find((el) =>
        (el.textContent || '').includes('Удалить задачу'),
      );
      const btn = [...(dialog?.querySelectorAll('button') ?? [])].find((b) => b.textContent.trim() === ${JSON.stringify(label)});
      if (!btn) return false;
      btn.click();
      return true;
    })()`)

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
      token: localStorage.getItem('listik.token'),
      text: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 400),
      cards: document.querySelectorAll('.listik-task-card').length,
    }))()`)
    throw new Error(`доска не отрисовалась: ${JSON.stringify(diagnostics)} ${JSON.stringify(consoleErrors)}`)
  }

  const opened = (await cardExists(OPEN_TITLE)) && (await clickCard(OPEN_TITLE))
  const drawerReady = await waitFor(async () => {
    const current = await state()
    return current.open && current.id === OPEN_ID ? current : null
  })
  if (!opened || !drawerReady) throw new Error(`панель не открыла ${OPEN_ID}: ${JSON.stringify(await state())}`)

  await record('в панели есть кнопка удаления', async () => {
    const present = await evaluate(
      `[...document.querySelectorAll('button')].some((b) => (b.getAttribute('aria-label') || '') === 'Удалить задачу ${OPEN_ID}')`,
    )
    return { ok: present, expect: `кнопка aria-label="Удалить задачу ${OPEN_ID}"`, got: present }
  })

  await record('отмена оставляет задачу на месте', async () => {
    const clicked = await clickRemove(OPEN_ID)
    const shown = await waitFor(async () => {
      const current = await dialog()
      return current.open ? current : null
    })
    const cancelled = shown ? await clickDialog('Отмена') : false
    const closed = await waitFor(async () => {
      const current = await dialog()
      return current.open ? null : true
    })
    const drawer = await state()
    const onBoard = await cardExists(OPEN_TITLE)
    const checks = {
      'кнопка нажата': clicked,
      'диалог открылся': Boolean(shown),
      'есть подтверждение и отмена': Boolean(shown?.confirm && shown?.cancel),
      'отмена нажата': cancelled,
      'диалог закрылся': Boolean(closed),
      'панель осталась': drawer.open && drawer.id === OPEN_ID,
      'карточка на доске': onBoard,
    }
    return { ok: Object.values(checks).every(Boolean), checks }
  })

  await record('подтверждение удаляет задачу и закрывает панель', async () => {
    const clicked = await clickRemove(OPEN_ID)
    const shown = await waitFor(async () => {
      const current = await dialog()
      return current.open ? current : null
    })
    const confirmed = shown ? await clickDialog('Удалить') : false
    const drawerGone = await waitFor(async () => {
      const current = await state()
      return current.open ? null : true
    }, 8000)
    const cardGone = await waitFor(async () => ((await cardExists(OPEN_TITLE)) ? null : true), 8000)
    const toast = await evaluate(
      `[...document.querySelectorAll('.ui-toast, [class*=toast]')].map((el) => el.textContent).join(' ')`,
    )
    const checks = {
      'кнопка нажата': clicked,
      'диалог открылся': Boolean(shown),
      'подтверждение нажато': confirmed,
      'панель закрылась': Boolean(drawerGone),
      'карточки на доске нет': Boolean(cardGone),
    }
    return {
      ok: Object.values(checks).every(Boolean),
      checks,
      got: { toast: String(toast).slice(0, 160) },
    }
  })

  await record('SSE action=deleted закрывает открытую панель', async () => {
    const openedOther = (await cardExists(OTHER_TITLE)) && (await clickCard(OTHER_TITLE))
    const ready = await waitFor(async () => {
      const current = await state()
      return current.open && current.id === OTHER_ID ? current : null
    })
    const streamed = await waitFor(async () => {
      const counters = await mockCall(apiPort, '/__requests')
      return counters.streams > 0 ? counters.streams : null
    }, 8000)
    const sent = await mockCall(apiPort, '/__event', {
      kind: 'task',
      payload: { id: OTHER_ID, action: 'deleted' },
    })
    const drawerGone = await waitFor(async () => {
      const current = await state()
      return current.open ? null : true
    }, 8000)
    const cardGone = await waitFor(async () => ((await cardExists(OTHER_TITLE)) ? null : true), 8000)
    const checks = {
      'открыли другую задачу': Boolean(openedOther && ready),
      'поток подключён': Boolean(streamed),
      'кадр ушёл подписчику': sent.clients >= 1,
      'панель закрылась': Boolean(drawerGone),
      'карточки на доске нет': Boolean(cardGone),
    }
    return { ok: Object.values(checks).every(Boolean), checks, got: { clients: sent.clients } }
  })

  report.consoleErrors = consoleErrors
  socket.close()
} catch (error) {
  report.error = String(error?.message ?? error)
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
