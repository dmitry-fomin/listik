import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { createHash } from 'node:crypto'
import { connect, freePort } from './browser-harness.mjs'

const TIMEOUT_PREFIX = 'таймаут теста'

/** Гонка с очищаемым таймером: старая версия модуля не должна подвешивать тест. */
async function within(promise, label, ms = 2000) {
  let timer
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${TIMEOUT_PREFIX}: ${label}`)), ms)
  })
  try {
    return await Promise.race([promise, timeout])
  } finally {
    clearTimeout(timer)
  }
}

const notTimeout = (error) => !String(error?.message).startsWith(TIMEOUT_PREFIX)

const closedError = (method) => (error) =>
  error instanceof Error &&
  notTimeout(error) &&
  error.message.includes(method) &&
  /[а-яё]/i.test(error.message) &&
  /закрыт|ошибка/i.test(error.message)

/** Минимальный WebSocket-сервер: рукопожатие и сырой сокет в onData. */
async function startServer(t, onData = () => {}) {
  const sockets = new Set()
  const server = createServer()
  server.on('upgrade', (request, socket) => {
    sockets.add(socket)
    socket.on('error', () => {})
    const accept = createHash('sha1')
      .update(request.headers['sec-websocket-key'] + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11')
      .digest('base64')
    socket.write(
      'HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n' +
        `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
    )
    socket.on('data', () => onData(socket))
  })
  const port = await freePort()
  await new Promise((resolve) => server.listen(port, '127.0.0.1', resolve))
  t.after(() => {
    for (const socket of sockets) socket.destroy()
    server.close()
  })
  return `ws://127.0.0.1:${port}/`
}

function open(t, url) {
  const client = connect(url)
  t.after(() => client.socket.close())
  return client
}

/** Неприкрытый текстовый кадр сервер→клиент (payload < 126 байт). */
const textFrame = (text) => {
  const payload = Buffer.from(text)
  return Buffer.concat([Buffer.from([0x81, payload.length]), payload])
}

test('обрыв посреди запроса отклоняет send', async (t) => {
  const url = await startServer(t, (socket) => socket.destroy())
  const client = open(t, url)
  await within(client.ready, 'ready')
  await assert.rejects(
    within(client.send('Runtime.evaluate'), 'Runtime.evaluate'),
    closedError('Runtime.evaluate'),
  )
})

test('обрыв отклоняет все ожидающие запросы', async (t) => {
  const url = await startServer(t, (socket) => socket.destroy())
  const client = open(t, url)
  await within(client.ready, 'ready')
  const first = within(client.send('Runtime.evaluate'), 'первый')
  const second = within(client.send('Page.navigate'), 'второй')
  await Promise.all([
    assert.rejects(first, closedError('Runtime.evaluate')),
    assert.rejects(second, closedError('Page.navigate')),
  ])
})

test('send после закрытия отклоняется сразу', async (t) => {
  const url = await startServer(t, (socket) => socket.destroy())
  const client = open(t, url)
  await within(client.ready, 'ready')
  await assert.rejects(
    within(client.send('Runtime.evaluate'), 'Runtime.evaluate'),
    closedError('Runtime.evaluate'),
  )
  await assert.rejects(within(client.send('Page.enable'), 'Page.enable'), closedError('Page.enable'))
})

test('нормальный ответ разрешает send', async (t) => {
  const url = await startServer(t, (socket) => socket.write(textFrame('{"id":1,"result":{"ok":true}}')))
  const client = open(t, url)
  await within(client.ready, 'ready')
  assert.deepEqual(await within(client.send('Runtime.evaluate'), 'ответ'), { ok: true })
})

test('send в CONNECTING отклоняется', async (t) => {
  const url = await startServer(t)
  const client = open(t, url)
  client.ready.catch(() => {})
  await assert.rejects(within(client.send('Page.enable'), 'Page.enable'), notTimeout)
  await within(client.ready, 'ready').catch(() => {})
})
