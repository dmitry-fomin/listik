/**
 * Проверка пункта «без маршрута» в панели задачи (`web/src/components/TaskDrawer.vue`).
 *
 * Поднимает mock-api в режиме `--routes` (записи `routes.json` и карточка
 * `listik-routes-error` с отказавшим автостартом: `launch_error`, флаг «нужен
 * человек», метки `harness:`/`process:`), отдаёт собранный `web/dist` и гоняет
 * сценарий в headless Chrome через CDP.
 *
 * Сценарий: у заведённой задачи матрица маршрута та же, что при создании
 * (`RoutePicker`, иконки и роли); пункт «без маршрута» кликом сразу шлёт
 * одно поле `route: ''` (как `listik set launch_route=`), а метки маршрута
 * и ошибку автостарта уносит сервер — ровно то, что делает `store.update_task`
 * (доска метки не считает).
 *
 * Запуск: node scripts/verify-route-clear.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--routes`).
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
const CARD = 'listik-routes-error'
const FRESH = 'listik-routes-fresh'
const ROUTE = 'low-pipeline'

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

/** Тела PATCH, дошедшие до мока: по ним видно, что именно отправила доска. */
const patches = []

/**
 * Статика собранного приложения: SPA-фолбэк на index.html плюс прокси `/api/*`
 * на мок. Прокси, а не `VITE_API_BASE`, — чтобы страница и API были одного
 * origin, как в проде. Тело PATCH запоминаем перед отправкой наверх.
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
      const upstream = (body) => {
        const proxy = httpRequest(
          {
            host: '127.0.0.1',
            port: apiPort,
            path: request.url,
            method: request.method,
            headers: { ...request.headers, host: `127.0.0.1:${apiPort}` },
          },
          (answer) => {
            response.writeHead(answer.statusCode ?? 502, answer.headers)
            answer.pipe(response)
          },
        )
        proxy.on('error', () => {
          response.writeHead(502, { 'Content-Type': 'application/json; charset=utf-8' })
          response.end(JSON.stringify({ ok: false, error: 'mock-api недоступен' }))
        })
        if (body === undefined) request.pipe(proxy)
        else proxy.end(body)
      }
      if (request.method === 'PATCH') {
        // Тело нужно разобрать целиком — и в отчёт, и наверх: поток отдаём прокси.
        const chunks = []
        request.on('data', (chunk) => chunks.push(chunk))
        request.on('end', () => {
          const raw = Buffer.concat(chunks)
          let parsed = raw.toString('utf8')
          try {
            parsed = JSON.parse(parsed)
          } catch {
            /* не JSON — в отчёт уйдёт строкой */
          }
          patches.push({ path, body: parsed })
          upstream(raw)
        })
        return
      }
      upstream()
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
  const child = spawn(process.execPath, [join(root, 'scripts', 'mock-api.mjs'), String(port), '--routes'], {
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

/* ── CDP ────────────────────────────────────────────────────────────────── */

const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9400 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-routes-'))

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

/** Состояние блока «Маршрут запуска»: матрица, выбранный ключ, алерт, метки. */
const ROUTE_STATE = `(() => {
  const drawer = document.querySelector('.ui-drawer');
  if (!drawer) return { open: false };
  const picker = drawer.querySelector('[role="radiogroup"][aria-label="Маршрут запуска"]');
  const section = picker ? picker.closest('.listik-section') : null;
  const selected = picker ? picker.querySelector('[role="radio"].is-on') : null;
  const save = section
    ? [...section.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Сохранить маршрут')
    : null;
  const radios = picker
    ? [...picker.querySelectorAll('[role="radio"]')].map((el) => ({
        key: el.hasAttribute('data-route-clear') ? '' : el.getAttribute('data-route-key'),
        on: el.classList.contains('is-on'),
        label: (el.querySelector('.listik-pipelines__row-name')?.textContent
          ?? el.textContent)?.replace(/\\s+/g, ' ').trim(),
      }))
    : [];
  return {
    open: true,
    id: drawer.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    sectionTitle: section?.querySelector('.listik-section__title')?.textContent?.trim() ?? null,
    hasPicker: Boolean(picker),
    selectedKey: selected
      ? (selected.hasAttribute('data-route-clear') ? '' : selected.getAttribute('data-route-key'))
      : null,
    selectedLabel: radios.find((item) => item.on)?.label ?? null,
    hasClear: radios.some((item) => item.key === '' || item.label === 'без маршрута'),
    hasSave: Boolean(save),
    icons: picker ? picker.querySelectorAll('.listik-route-icon').length : 0,
    alert: section?.querySelector('.ui-alert')?.textContent?.replace(/\\s+/g, ' ').trim() ?? null,
    labels: [...drawer.querySelectorAll('.ui-badge')].map((b) => b.textContent.trim()),
    routes: radios,
  };
})()`

const report = { cases: [], patches: [], consoleErrors: [] }
let mock = null
let staticServer = null
let chrome = null
let apiPort = 0
const page = process.argv[2] ?? null

try {
  let url = page
  if (!url) {
    if (!existsSync(join(dist, 'index.html'))) {
      throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
    }
    apiPort = await freePort()
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

  const state = () => evaluate(ROUTE_STATE)

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

  /** Открыть задачу карточкой доски: если панель уже открыта, фон inert — сначала закрыть. */
  const openCard = async (title, id) => {
    if ((await state()).open) {
      await pressEscape()
      await waitFor(async () => ((await state()).open === false ? true : null))
    }
    await clickCard(title)
    const ok = await waitDrawer(id)
    if (!ok) throw new Error(`панель не открыла ${id}: ${JSON.stringify(await state())}`)
  }

  const clickRoute = (key) =>
    evaluate(`(() => {
      const picker = document.querySelector('.ui-drawer [role="radiogroup"][aria-label="Маршрут запуска"]');
      if (!picker) return false;
      const button = ${JSON.stringify(key) === '""'}
        ? picker.querySelector('[data-route-clear]')
        : [...picker.querySelectorAll('[role="radio"]')]
            .find((el) => el.getAttribute('data-route-key') === ${JSON.stringify(key)});
      if (!button) return false;
      button.click();
      return true;
    })()`)

  /** Сценарий не роняет прогон: его ошибка попадает в отчёт как провал. */
  const record = async (name, run) => {
    try {
      report.cases.push({ name, ...(await run()) })
    } catch (error) {
      report.cases.push({ name, ok: false, got: String(error?.message ?? error) })
    }
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
      token: localStorage.getItem('listik.token'),
      text: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 400),
      cards: document.querySelectorAll('.listik-task-card').length,
    }))()`)
    throw new Error(`доска не отрисовалась: ${JSON.stringify(diagnostics)} ${JSON.stringify(consoleErrors)}`)
  }

  // 1. Заведённая задача: матрица маршрута с иконками, текущий пресет выбран,
  //    кнопки «Сохранить» нет, ошибка автостарта и метки на месте.
  await record('карточка с отказом автостарта: матрица маршрута показана', async () => {
    await openCard('Автостарт упал: маршрут можно сменить', CARD)
    const current = await waitFor(async () => {
      const seen = await state()
      return seen.hasPicker && seen.selectedKey === ROUTE ? seen : null
    })
    const ok = Boolean(current)
      && current.sectionTitle === 'Маршрут запуска'
      && current.selectedLabel === 'Низкий — конвейер'
      && current.hasClear === true
      && current.hasSave === false
      && current.icons > 0
      && Boolean(current.alert?.includes('маршрута low-pipeline нет в routes.json'))
      && current.labels.includes('harness:claude')
      && current.labels.includes('process:low-pipeline')
    return {
      ok,
      expect: 'матрица с текущим маршрутом и иконками, без кнопки сохранения, алерт, метки harness:/process:',
      got: current ? { ...current, labels: current.labels.join(', ') } : null,
    }
  })

  // 2. Клик по «без маршрута» сразу шлёт PATCH с одним полем `route: ''`.
  await record('клик по «без маршрута» сразу шлёт route: «»', async () => {
    const before = patches.length
    const clicked = await clickRoute('')
    const sent = await waitFor(async () => (patches.length > before ? patches[patches.length - 1] : null))
    const body = sent?.body ?? {}
    const ok = Boolean(clicked)
      && Boolean(sent)
      && sent.path.includes(`/api/tasks/${CARD}`)
      && body.route === ''
      && body.labels === undefined
    return {
      ok,
      expect: `клик сразу PATCH /api/tasks/${CARD} ровно с route:'' — метки harness:/process: считает сервер`,
      got: { clicked, patch: sent },
    }
  })

  // 3. Карточка после сохранения: выбран «без маршрута», ошибка снята, метки уехали.
  await record('карточка после сохранения: без маршрута и без ошибки', async () => {
    const after = await waitFor(async () => {
      const current = await state()
      return current.selectedKey === '' && current.alert === null ? current : null
    })
    const ok = Boolean(after)
      && after.selectedKey === ''
      && after.selectedLabel === 'без маршрута'
      && after.alert === null
      && after.hasSave === false
      && after.labels.includes('frontend')
      && !after.labels.some((label) => /^(harness|process):/.test(label))
    return {
      ok,
      expect: 'выбран «без маршрута», алерта нет, метки маршрута сняты',
      got: after ? { ...after, labels: after.labels.join(', ') } : null,
    }
  })

  // 4. Мок (он повторяет `store.update_task`) подтверждает состояние задачи.
  await record('состояние задачи на сервере: маршрут и отказ автостарта сняты', async () => {
    if (!apiPort) return { ok: false, got: 'прогон с готовым url: состояние мока не проверить' }
    const response = await fetch(`http://127.0.0.1:${apiPort}/api/tasks/${CARD}`)
    const payload = await response.json()
    const task = payload?.data ?? {}
    const ok = task.launch_route === null
      && task.launch_error === null
      && task.needs_owner === false
      && Array.isArray(task.labels)
      && !task.labels.some((label) => /^(harness|process):/.test(label))
    return {
      ok,
      expect: 'launch_route=null, launch_error=null, needs_owner=false, метки без harness:/process:',
      got: {
        launch_route: task.launch_route,
        launch_error: task.launch_error,
        needs_owner: task.needs_owner,
        labels: task.labels,
      },
    }
  })

  // 5. Задача, заведённая без маршрута: пункт «без маршрута» выбран сразу.
  await record('задача без маршрута: пункт выбран сразу', async () => {
    await openCard('Заведена без маршрута', FRESH)
    const current = await waitFor(async () => {
      const seen = await state()
      return seen.hasPicker && seen.selectedKey === '' ? seen : null
    })
    const ok = Boolean(current)
      && current.selectedLabel === 'без маршрута'
      && current.hasSave === false
    return {
      ok,
      expect: 'в матрице выбран «без маршрута», кнопки сохранения нет',
      got: current
        ? { selectedKey: current.selectedKey, selectedLabel: current.selectedLabel, hasSave: current.hasSave }
        : await state(),
    }
  })

  // 6. Клик по пресету сразу шлёт его ключ — без отдельной кнопки «Сохранить».
  await record('клик по пресету сразу сохраняет маршрут', async () => {
    const before = patches.length
    const clicked = await clickRoute('high-pipeline')
    const sent = await waitFor(async () => (patches.length > before ? patches[patches.length - 1] : null))
    const body = sent?.body ?? {}
    const after = await waitFor(async () => {
      const current = await state()
      return current.selectedKey === 'high-pipeline' ? current : null
    })
    const ok = Boolean(clicked)
      && Boolean(sent)
      && sent.path.includes(`/api/tasks/${FRESH}`)
      && body.route === 'high-pipeline'
      && body.labels === undefined
      && after?.selectedLabel === 'Высокий — конвейер'
    return {
      ok,
      expect: `клик сразу PATCH /api/tasks/${FRESH} с route:'high-pipeline', пресет выбран`,
      got: { clicked, patch: sent, selectedKey: after?.selectedKey, selectedLabel: after?.selectedLabel },
    }
  })

  report.patches = patches
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
