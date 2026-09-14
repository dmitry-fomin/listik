/**
 * Смоук-тест живой страницы через CDP: запускает headless Chrome, открывает
 * приложение, переключает все четыре вида, открывает окно «Новая задача»
 * и панель задачи, жмёт фильтры и печатает консольные ошибки страницы.
 *
 * Запуск: node scripts/smoke.mjs [url] [width]
 * Второй позиционный аргумент — ширина окна (по умолчанию 1440), высота
 * фиксирована (900). Требуется запущенный API (настоящий bin/listik serve
 * или scripts/mock-api.mjs) и отданное приложение (npm run dev / npm run preview).
 * Ширина < 768 включает эмуляцию телефона (CDP `Emulation.setDeviceMetricsOverride`,
 * 360×740, deviceScaleFactor 2, mobile: true) вместо реального окна Chrome —
 * отчёт при этом всегда содержит блок `phone` с полями, посчитанными только
 * по видимым элементам (`offsetParent !== null`).
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const url = process.argv[2] ?? 'http://localhost:5199/'
const width = Number.parseInt(process.argv[3] ?? '1440', 10) || 1440
const chromePath =
  process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const port = 9333 + Math.floor(Math.random() * 200)
const profile = mkdtempSync(join(tmpdir(), 'listik-smoke-'))

const emulatePhone = width < 768
const windowSize = emulatePhone ? '1200,900' : `${width},900`

const chrome = spawn(
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

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

async function target() {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/list`)
      const list = await response.json()
      const page = list.find((item) => item.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch {
      /* chrome ещё поднимается */
    }
    await sleep(250)
  }
  throw new Error('не дождались CDP-таргета Chrome')
}

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl)
    let nextId = 1
    const pending = new Map()
    const events = []
    socket.addEventListener('open', () =>
      resolve({
        socket,
        events,
        send(method, params) {
          const id = nextId++
          socket.send(JSON.stringify({ id, method, params }))
          return new Promise((res, rej) => pending.set(id, { res, rej }))
        },
      }),
    )
    socket.addEventListener('error', reject)
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data)
      if (message.id && pending.has(message.id)) {
        const { res, rej } = pending.get(message.id)
        pending.delete(message.id)
        if (message.error) rej(new Error(message.error.message))
        else res(message.result)
        return
      }
      if (message.method) events.push(message)
    })
  })
}

async function main() {
  const client = await connect(await target())
  await client.send('Runtime.enable')
  await client.send('Log.enable')
  await client.send('Page.enable')
  await client.send('Network.enable')
  if (emulatePhone) {
    await client.send('Emulation.setDeviceMetricsOverride', {
      width,
      height: 740,
      deviceScaleFactor: 2,
      mobile: true,
    })
  }
  await client.send('Page.navigate', { url })
  await sleep(3500)

  const evaluate = async (expression) => {
    const result = await client.send('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (result.exceptionDetails) {
      throw new Error(`ошибка в браузере: ${result.exceptionDetails.text}`)
    }
    return result.result.value
  }

  const report = {}
  report.title = await evaluate('document.title')
  report.columns = await evaluate('document.querySelectorAll(".listik-column").length')
  report.cards = await evaluate('document.querySelectorAll(".listik-task-card").length')
  report.needsYou = await evaluate('document.querySelectorAll(".listik-inbox-card").length')
  report.toolbar = await evaluate('document.querySelectorAll(".listik-toolbar .ui-chip").length')
  report.filterBarOnBoard = await evaluate('document.querySelectorAll(".ui-filter-bar").length')
  report.board = {
    columns: await evaluate('document.querySelectorAll(".listik-column").length'),
    cards: await evaluate('document.querySelectorAll(".listik-task-card").length'),
    doneRail: await evaluate('document.querySelectorAll(".listik-done-rail").length > 0'),
    rail: await evaluate('document.querySelectorAll(".listik-rail__tr").length'),
    intakeCollapsed: await evaluate('document.querySelectorAll(".listik-column__rail").length > 0'),
  }

  report.views = await evaluate(`(async () => {
    const out = {}
    const buttons = [...document.querySelectorAll('.ui-tabs__list [role="tab"]')]
    for (const label of ['Список', 'Метрики', 'Доска']) {
      const button = buttons.find((item) => item.textContent.trim().startsWith(label))
      if (!button) { out[label] = 'кнопки нет'; continue }
      button.click()
      await new Promise((resolve) => setTimeout(resolve, 900))
      out[label] = {
        таблица: document.querySelectorAll('.ui-data-table').length,
        лента: document.querySelectorAll('.ui-timeline').length,
        графики: document.querySelectorAll('.ui-bar-chart, .ui-h-bar-list, .ui-heatmap').length,
        доска: document.querySelectorAll('.listik-column').length,
        filterBar: document.querySelectorAll('.ui-filter-bar').length,
      }
    }
    return out
  })()`)

  report.newTask = await evaluate(`(async () => {
    const buttons = [...document.querySelectorAll('.ui-button')]
    const button = buttons.find((btn) => btn.textContent.trim().includes('Новая задача'))
    if (!button) return 'кнопки нет'
    button.click()
    await new Promise((resolve) => setTimeout(resolve, 700))
    const result = {
      modal: document.querySelectorAll('.ui-modal').length > 0,
      routeCells: document.querySelectorAll('.listik-route__cell').length,
      routeOff: document.querySelectorAll('.listik-route__cell.is-off').length,
      typeOptions: document.querySelectorAll('[aria-label="Тип задачи"] .ui-segmented__item').length,
    }
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 400))
    result.closedByEscape = document.querySelectorAll('.ui-modal').length === 0
    return result
  })()`)

  report.drawer = await evaluate(`(async () => {
    const card = document.querySelector('.listik-task-card')
    if (!card) return 'карточек нет'
    card.click()
    await new Promise((resolve) => setTimeout(resolve, 1200))
    const drawer = document.querySelector('.ui-drawer')
    const text = drawer ? drawer.textContent : ''
    return {
      открылась: Boolean(drawer),
      ширина: drawer ? Math.round(drawer.getBoundingClientRect().width) : 0,
      шагов: document.querySelectorAll('.ui-step').length,
      кнопок: drawer ? drawer.querySelectorAll('.ui-button').length : 0,
      лент: document.querySelectorAll('.ui-drawer .ui-timeline').length,
      естьРазделПроцесса: text.includes('Где стоит процесс'),
      естьХолодныйСтарт: text.includes('Холодный старт'),
      естьКтоДержит: text.includes('Кто держит'),
      естьЖурнал: text.includes('Журнал и вердикты'),
      естьСвязи: text.includes('Связи'),
      coldRows: document.querySelectorAll('.listik-cold__row').length,
      splitButton: document.querySelectorAll('.ui-split-button').length > 0,
    }
  })()`)

  report.layout = await evaluate(`(async () => {
    const viewport = { width: innerWidth, height: innerHeight }
    const desktopOverflow = document.documentElement.scrollWidth
    const board = document.querySelector('.listik-board')
    const scroll = document.querySelector('.listik-board-scroll') || board
    const boardScrollable = board ? board.scrollWidth > board.clientWidth : false
    const stageColumns = [
      ...document.querySelectorAll(
        '.listik-column[data-stage="s1-spec"], .listik-column[data-stage="s2-review"], ' +
        '.listik-column[data-stage="s3-impl"], .listik-column[data-stage="s4-judge"]',
      ),
    ]
    const columnsVisible = stageColumns.filter((column) => column.getBoundingClientRect().right <= innerWidth).length
    const boardOverflow = scroll ? scroll.scrollWidth - scroll.clientWidth : 0
    const s4 = document.querySelector('.listik-column[data-stage="s4-judge"]')
    const s4Right = s4 ? s4.getBoundingClientRect().right - innerWidth : null
    return {
      viewport,
      desktopOverflow,
      безГоризонтальнойПрокрутки: desktopOverflow <= viewport.width,
      boardScrollable,
      columnsVisible,
      boardOverflow,
      s4Right,
    }
  })()`)

  // report.drawer открывает панель кликом по карточке и не закрывает её; кит на
  // время панели ставит body { overflow: hidden } — измерять экран в этом
  // состоянии нельзя. Закрываем перед report.phone, оставляя report.layout как есть.
  await evaluate(`(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 600))
    if (document.querySelectorAll('.ui-drawer').length > 0) {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await new Promise((resolve) => setTimeout(resolve, 600))
    }
    return null
  })()`)

  // Клик по строке «Списка» открывает ту же панель задачи, что и карточка на
  // доске. Заодно проверяем обратное: чекбокс выбора строку не открывает, а
  // кнопка-id открывает по-прежнему. В конце возвращаемся на «Доску», чтобы не
  // менять состояние для следующих блоков отчёта.
  report.listRow = await evaluate(`(async () => {
    const tabs = [...document.querySelectorAll('.ui-tabs__list [role="tab"]')]
    const listTab = tabs.find((item) => item.textContent.trim().startsWith('Список'))
    if (!listTab) return 'вкладки «Список» нет'
    listTab.click()
    await new Promise((resolve) => setTimeout(resolve, 1200))

    const rows = [...document.querySelectorAll('.ui-data-table tbody tr.ui-data-table__row')]
    const row = rows[0]
    if (!row) return { rows: 0 }

    const out = { rows: rows.length }
    const idCell = row.querySelector('td:nth-child(2)')
    out.id = idCell ? idCell.innerText.trim() : null

    const checkbox = row.querySelector('td input[type="checkbox"]')
    if (checkbox) {
      checkbox.click()
      await new Promise((resolve) => setTimeout(resolve, 500))
      out.checkboxSelectedWithoutDrawer = document.querySelectorAll('.ui-drawer').length === 0
      checkbox.click()
      await new Promise((resolve) => setTimeout(resolve, 400))
    }

    const cell = [...row.cells].find(
      (item) => !item.querySelector('input, button') && item.innerText.trim().length > 0,
    )
    out.clickedCell = cell ? cell.innerText.trim() : null
    if (cell) cell.click()
    await new Promise((resolve) => setTimeout(resolve, 1200))

    const drawer = document.querySelector('.ui-drawer')
    out.drawerOpen = Boolean(drawer)
    const titleEl = drawer ? drawer.querySelector('.listik-drawer__title') : null
    out.drawerTitle = titleEl ? titleEl.innerText.trim() : null
    out.titleInClickedCell = Boolean(
      out.drawerTitle && out.clickedCell && out.clickedCell.includes(out.drawerTitle),
    )
    out.drawerHasId = drawer && out.id ? drawer.innerText.includes(out.id) : null

    const idButton = row.querySelector('td button')
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 600))
    out.closed = document.querySelectorAll('.ui-drawer').length === 0
    if (idButton) {
      idButton.click()
      await new Promise((resolve) => setTimeout(resolve, 1200))
      out.idButtonOpens = document.querySelectorAll('.ui-drawer').length > 0
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
      await new Promise((resolve) => setTimeout(resolve, 600))
    }

    const boardTab = tabs.find((item) => item.textContent.trim().startsWith('Доска'))
    if (boardTab) {
      boardTab.click()
      await new Promise((resolve) => setTimeout(resolve, 1000))
    }
    return out
  })()`)

  report.phone = await evaluate(`(async () => {
    const isVisible = (el) => el.offsetParent !== null
    window.scrollTo(0, 0)
    const rows = [...document.querySelectorAll('.listik-mobile-task')].filter(isVisible)
    const firstRow = rows[0] ?? null
    const firstRowStyled = firstRow
      ? (({ display, borderTopStyle, paddingTop }) => ({ display, borderTopStyle, paddingTop }))(getComputedStyle(firstRow))
      : null
    const firstRowTop = firstRow ? Math.round(firstRow.getBoundingClientRect().top) : null
    const rowsFullyVisible = rows.filter((row) => row.getBoundingClientRect().bottom <= innerHeight).length
    const compactPill = document.querySelector('.listik-shell__only-compact')
    const headerButtons = [...document.querySelectorAll('.ui-app-header .ui-button')]
      .filter(isVisible)
      .map((button) => button.innerText.trim() || button.getAttribute('aria-label') || 'icon')

    const projectSelectEl = document.querySelector('.listik-phone-toolbar [aria-label="Проект"]')
    const searchEl = document.querySelector('.listik-phone-toolbar [aria-label="Поиск по задачам"]')
    const containerEl = document.querySelector('.listik-shell > .ui-container')
    const containerBox = containerEl
      ? { width: getComputedStyle(containerEl).width, left: Math.round(containerEl.getBoundingClientRect().left) }
      : null
    const badgeEl = document.querySelector('.listik-phone-queue__head .ui-badge')
    const moreEl = document.querySelector('.listik-phone-queue__more')
    const moreVisible = Boolean(moreEl && isVisible(moreEl))
    // Читаем badge/more до клика — после клика DOM (счётчики, подпись кнопки)
    // уже отражает следующую страницу, а не текущее состояние.
    const queueBadge = badgeEl ? badgeEl.innerText : null
    const moreLabel = moreEl ? moreEl.innerText : null
    let rowsAfterMore = null
    if (moreVisible) {
      moreEl.click()
      await new Promise((resolve) => setTimeout(resolve, 900))
      rowsAfterMore = [...document.querySelectorAll('.listik-mobile-task')].filter(isVisible).length
    }

    return {
      drawerOpen: document.querySelectorAll('.ui-drawer').length > 0,
      innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      bodyOverflowX: getComputedStyle(document.body).overflowX,
      compactPillVisible: Boolean(compactPill && isVisible(compactPill)),
      rowsVisible: rows.length,
      firstRowStyled,
      firstRowTop,
      rowsFullyVisible,
      tabsVisible: Boolean(document.querySelector('.ui-tabs') && isVisible(document.querySelector('.ui-tabs'))),
      inboxVisible: Boolean(
        document.querySelector('[aria-label="Нужен ты"]') &&
          isVisible(document.querySelector('[aria-label="Нужен ты"]')),
      ),
      chipsVisible: [...document.querySelectorAll('.listik-toolbar .ui-chip')].filter(isVisible).length,
      headerButtons,
      projectSelectVisible: Boolean(projectSelectEl && projectSelectEl.getBoundingClientRect().width > 0),
      searchVisible: Boolean(searchEl && searchEl.getBoundingClientRect().width > 0),
      clientWidth: document.documentElement.clientWidth,
      containerBox,
      queueBadge,
      moreVisible,
      moreLabel,
      rowsAfterMore,
    }
  })()`)

  report.phone.sheet = await evaluate(`(async () => {
    const isVisible = (el) => el.offsetParent !== null
    const hasQueue = Boolean(document.querySelector('.listik-phone-queue'))
    const row = [...document.querySelectorAll('.listik-mobile-task')].find(isVisible)
    if (!hasQueue || !row) return null

    window.scrollTo(0, 300)
    await new Promise((resolve) => setTimeout(resolve, 200))
    const scrollBefore = window.scrollY

    row.click()
    await new Promise((resolve) => setTimeout(resolve, 1500))

    const sheetEl = document.querySelector('.listik-phone-sheet')
    const drawerEl = document.querySelector('.ui-drawer')
    const titleEl = document.querySelector('.ui-drawer .listik-drawer__title')
    const rect = drawerEl ? drawerEl.getBoundingClientRect() : null

    const result = {
      open: Boolean(sheetEl),
      title: titleEl ? titleEl.innerText : null,
      drawerWidth: rect ? Math.round(rect.width) : null,
      drawerScrollWidth: drawerEl ? drawerEl.scrollWidth : null,
      sections: [...document.querySelectorAll('.listik-phone-sheet .listik-section__title')]
        .filter(isVisible)
        .map((el) => el.innerText.split('\\n')[0].trim()),
      actionButtons: document.querySelectorAll('.ui-drawer .ui-button, .ui-drawer .ui-split-button').length,
      inputs: document.querySelectorAll('.ui-drawer input, .ui-drawer textarea, .ui-drawer select').length,
      timelineItems: document.querySelectorAll('.ui-drawer .ui-timeline__item').length,
    }

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await new Promise((resolve) => setTimeout(resolve, 600))

    result.closed = document.querySelectorAll('.ui-drawer').length === 0
    result.scrollAfter = window.scrollY
    result.scrollRestored = result.scrollAfter === scrollBefore
    result.rowsAfterClose = [...document.querySelectorAll('.listik-mobile-task')].filter(isVisible).length

    return result
  })()`)

  report.phone.requests = (() => {
    const counters = {
      board: 0,
      ready: 0,
      blocked: 0,
      stats: 0,
      timeline: 0,
      tasksList: 0,
      taskDetail: 0,
      meta: 0,
      health: 0,
    }
    for (const event of client.events) {
      if (event.method !== 'Network.requestWillBeSent') continue
      const request = event.params?.request
      if (!request?.url) continue
      let pathname
      try {
        pathname = new URL(request.url).pathname
      } catch {
        continue
      }
      const method = request.method
      if (pathname === '/api/board') counters.board += 1
      else if (pathname === '/api/ready') counters.ready += 1
      else if (pathname === '/api/blocked') counters.blocked += 1
      else if (pathname === '/api/stats') counters.stats += 1
      else if (pathname === '/api/timeline') counters.timeline += 1
      else if (pathname === '/api/tasks' && method === 'GET') counters.tasksList += 1
      else if (pathname.startsWith('/api/tasks/') && method === 'GET') counters.taskDetail += 1
      else if (pathname === '/api/meta') counters.meta += 1
      else if (pathname === '/api/health') counters.health += 1
    }
    return counters
  })()

  report.errors = client.events
    .filter((event) => event.method === 'Log.entryAdded' && event.params.entry.level === 'error')
    .map((event) => event.params.entry.text)
  report.console = client.events
    .filter((event) => event.method === 'Runtime.exceptionThrown')
    .map((event) => event.params.exceptionDetails.text)

  console.log(JSON.stringify(report, null, 2))
  client.socket.close()
}

try {
  await main()
} finally {
  chrome.kill('SIGKILL')
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  } catch {
    /* Chrome ещё дописывает профиль — не повод падать */
  }
}
