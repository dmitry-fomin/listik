import { spawn } from 'node:child_process'
import { createServer, request as httpRequest } from 'node:http'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

export function freePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer()
    probe.on('error', reject)
    probe.listen(0, '127.0.0.1', () => {
      const { port } = probe.address()
      probe.close(() => resolve(port))
    })
  })
}

export function serveDist(port, apiPort, dist, { proxyPrefixes = ['/api/'], onRequestBody } = {}) {
  const types = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.woff2': 'font/woff2',
  }
  const server = createServer((request, response) => {
    const path = decodeURIComponent((request.url ?? '/').split('?')[0])
    if (proxyPrefixes.some((prefix) => path.startsWith(prefix))) {
      const forward = (body) => {
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
        if (body === undefined) request.pipe(proxy)
        else proxy.end(body)
      }
      if (onRequestBody && request.method === 'PATCH') {
        const chunks = []
        request.on('data', (chunk) => chunks.push(chunk))
        request.on('end', () => {
          const raw = Buffer.concat(chunks)
          onRequestBody({ path, raw, request })
          forward(raw)
        })
      } else {
        forward()
      }
      return
    }
    const file = join(dist, normalize(path).replace(/^(\.\.[/\\])+/, ''))
    const target = path !== '/' && existsSync(file) && statSync(file).isFile() ? file : join(dist, 'index.html')
    response.writeHead(200, { 'Content-Type': types[extname(target)] ?? 'application/octet-stream' })
    createReadStream(target).pipe(response)
  })
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)))
}

export async function startMock(port, root, args = []) {
  const child = spawn(process.execPath, [join(root, 'scripts', 'mock-api.mjs'), String(port), ...args], {
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

export function startChrome(chromePath, port, profile, windowSize = '1600,1100') {
  return spawn(
    chromePath,
    [
      '--headless=old',
      '--disable-gpu',
      `--window-size=${windowSize}`,
      '--no-sandbox',
      '--disable-dev-shm-usage',
      `--remote-debugging-port=${port}`,
      `--user-data-dir=${profile}`,
      'about:blank',
    ],
    { stdio: 'ignore' },
  )
}

export async function cdpTarget(port) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`)
      const page = (await response.json()).find((item) => item.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch {
      /* chrome ещё поднимается */
    }
    await sleep(250)
  }
  throw new Error('не дождались CDP-таргета Chrome')
}

export function connect(url) {
  const socket = new WebSocket(url)
  const pending = new Map()
  const consoleErrors = []
  const events = []
  let nextId = 1
  const ready = new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })
  socket.addEventListener('message', (message) => {
    const data = JSON.parse(message.data)
    if (data.method) events.push(data)
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
  return { socket, ready, send, evaluate, consoleErrors, events }
}
