/**
 * Проверка переходов по ссылкам в блоке «Связи» панели задачи
 * (`web/src/components/TaskDrawer.vue` → `open-other` → `store.openTask`).
 *
 * Поднимает mock-api в режиме `--links` (краевые связи: закрытая, удалённая,
 * чужой проект, id в другом регистре, взаимные ссылки, медленный ответ),
 * отдаёт собранный `web/dist` и гоняет сценарии в headless Chrome через CDP.
 *
 * Запуск: node scripts/verify-deps-links.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--links`).
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
const slowMs = 3000

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
    [join(root, 'scripts', 'mock-api.mjs'), String(port), '--links', `--slow-ms=${slowMs}`],
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
const chromePort = 9400 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-links-'))

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

/** Состояние панели задачи: id в шапке, заголовок, алерт, ссылки «Связей». */
const DRAWER_STATE = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  return {
    open: true,
    id: root.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    title: root.querySelector('.listik-drawer__title')?.textContent?.trim() ?? null,
    alert: root.querySelector('.ui-alert')?.textContent?.replace(/\\s+/g, ' ').trim() ?? null,
    links: [...root.querySelectorAll('button.listik-link')].map((b) => b.textContent.trim()),
    skeletons: root.querySelectorAll('.ui-skeleton').length,
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

  const state = () => evaluate(DRAWER_STATE)

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

  /** Клик по ссылкам в «Связях» панели: несколько id подряд — в одном тике, без перерисовки. */
  const clickLinks = (ids) =>
    evaluate(`(() => {
      const want = ${JSON.stringify(ids)};
      const buttons = [...document.querySelectorAll('.ui-drawer button.listik-link')];
      const clicked = [];
      for (const id of want) {
        const button = buttons.find((b) => b.textContent.trim() === id);
        if (!button) continue;
        button.click();
        clicked.push(id);
      }
      return clicked;
    })()`)

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
      dialogs: [...document.querySelectorAll('[role=dialog]')].map((el) => el.className),
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
  }

  const openMain = () => openCard('Карточка со связями', 'listik-links-main')

  // 1. Ссылка на закрытую задачу: она есть только в `dependencies` (в `blocked_by` закрытые не попадают).
  await record('сводка: закрытая связь', async () => {
    await openMain()
    const clicked = await clickLinks(['listik-links-done'])
    const ok = clicked.length === 1 && (await waitDrawer('listik-links-done'))
    return { ok, expect: 'listik-links-done', got: (await state()).id, clicked }
  })

  // 2. Тот же id двумя строками `deps` (blocks + waits-for) — в сводке должна быть одна ссылка.
  await record('сводка: дубль id двумя связями', async () => {
    await openMain()
    const links = (await state()).links
    const count = links.filter((link) => link === 'listik-links-done').length
    return { ok: count === 1, expect: 'одна ссылка listik-links-done', got: `${count} (${links.join(', ')})` }
  })

  // 3. Задача другого проекта: её нет ни на доске под фильтром, ни в выборке доски вообще.
  await record('сводка: задача другого проекта', async () => {
    await openMain()
    const clicked = await clickLinks(['other-links-far'])
    const ok = clicked.length === 1 && (await waitDrawer('other-links-far'))
    return { ok, expect: 'other-links-far', got: (await state()).id, clicked }
  })

  // 4. id в другом регистре: сервер ищет задачу по точному id, панель должна найти канонический.
  await record('связь с id в другом регистре', async () => {
    await openMain()
    const clicked = await clickLinks(['Listik-Links-Case'])
    const ok = clicked.length === 1 && (await waitDrawer('listik-links-case'))
    return { ok, expect: 'listik-links-case', got: (await state()).id, clicked }
  })

  // 5. Связь на удалённую задачу: панель не должна терять открытую карточку.
  await record('связь на удалённую задачу', async () => {
    await openMain()
    const clicked = await clickLinks(['listik-links-gone'])
    const failed = await waitFor(async () => {
      const current = await state()
      return current.alert?.includes('не найдена') ? current : null
    })
    const ok = clicked.length === 1 && Boolean(failed) && failed.id === 'listik-links-main'
    return {
      ok,
      expect: 'панель на listik-links-main + алерт «задача не найдена»',
      got: { id: failed?.id, alert: failed?.alert?.slice(0, 80) },
      clicked,
    }
  })

  // 6. Повторный клик по той же ссылке (двойной клик до перерисовки): должен остаться переход.
  await record('повторный клик по той же ссылке', async () => {
    await openMain()
    const clicked = await evaluate(`(() => {
      const button = [...document.querySelectorAll('.ui-drawer button.listik-link')]
        .find((b) => b.textContent.trim() === 'listik-links-done');
      if (!button) return 0;
      button.click(); button.click();
      return 2;
    })()`)
    const ok = clicked === 2 && (await waitDrawer('listik-links-done'))
    return { ok, expect: 'listik-links-done', got: (await state()).id, clicked }
  })

  // 7. Взаимные ссылки A↔B: переходы туда-обратно при открытой панели.
  await record('взаимные ссылки A↔B', async () => {
    await openCard('Взаимная ссылка A', 'listik-links-a')
    const opened = true
    await clickLinks(['listik-links-b'])
    const forward = await waitDrawer('listik-links-b')
    await clickLinks(['listik-links-a'])
    const back = await waitDrawer('listik-links-a')
    const ok = Boolean(opened) && Boolean(forward) && Boolean(back)
    return { ok, expect: 'A → B → A', got: { opened, forward, back } }
  })

  // 8. Гонка: два клика в одном тике — медленная ссылка первой, быстрая второй.
  //    Побеждать должен последний клик; «догоняющий» ответ медленной задачи — нет.
  await record('гонка: медленный ответ не перебивает последний клик', async () => {
    await openMain()
    const clicked = await clickLinks(['listik-links-slow', 'other-links-far'])
    const quick = await waitDrawer('other-links-far')
    await sleep(slowMs + 700)
    const after = await state()
    const ok = clicked.length === 2 && Boolean(quick) && after.id === 'other-links-far'
    return { ok, expect: 'other-links-far остаётся', got: after.id, clicked }
  })

  // 9. Гонка с закрытием панели: ссылка в пути, панель закрыли Escape и открыли другую задачу.
  await record('гонка: ответ в закрытую панель не открывает чужую задачу', async () => {
    await openMain()
    await clickLinks(['listik-links-slow'])
    await sleep(150)
    await pressEscape()
    const closed = await waitFor(async () => ((await state()).open === false ? true : null))
    await clickCard('Взаимная ссылка B')
    const opened = await waitDrawer('listik-links-b')
    await sleep(slowMs + 700)
    const after = await state()
    const ok = Boolean(closed) && Boolean(opened) && after.id === 'listik-links-b'
    return { ok, expect: 'listik-links-b остаётся', got: { closed, opened, id: after.id, title: after.title } }
  })

  // 10. Дерево связей — состояние прежней карточки: при переходе оно не должно оставаться.
  await record('дерево связей не липнет к следующей задаче', async () => {
    await openMain()
    const clicked = await evaluate(`(() => {
      const button = [...document.querySelectorAll('.ui-drawer button')]
        .find((b) => b.getAttribute('aria-label') === 'Дерево связей');
      if (!button) return false;
      button.click();
      return true;
    })()`)
    const shown = await waitFor(async () =>
      (await evaluate(`Boolean(document.querySelector('.ui-drawer .listik-dep-list'))`)) ? true : null,
    )
    await clickLinks(['other-links-far'])
    const moved = await waitDrawer('other-links-far')
    const stale = await evaluate(`Boolean(document.querySelector('.ui-drawer .listik-dep-list'))`)
    const ok = clicked && Boolean(shown) && Boolean(moved) && stale === false
    return { ok, expect: 'дерево скрыто на новой карточке', got: { clicked, shown, moved, stale } }
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
