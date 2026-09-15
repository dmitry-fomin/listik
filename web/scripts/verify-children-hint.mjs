/**
 * Проверка строки «закрывать нельзя: открыты дети» в панели задачи
 * (`web/src/components/TaskDrawer.vue` → `childrenBlockClose`): предупреждение
 * видно только у незакрытой задачи с незакрытым ребёнком (listik-kwht).
 *
 * Поднимает mock-api в режиме `--hint` (карточка `listik-hint-hub` со мягкими
 * связями на шесть задач-случаев), отдаёт собранный `web/dist` и гоняет
 * сценарии в headless Chrome через CDP.
 *
 * Запуск: node scripts/verify-children-hint.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--hint`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя кейса — id задачи, ok, что увидели) и
 * `consoleErrors`. Код возврата 1, если хоть один кейс не прошёл.
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
const chromePort = 9800 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-hint-'))

/** Дословный текст строки — пункт 3 ТЗ: он не меняется. */
const HINT_TEXT = 'закрывать нельзя: открыты дети'
const HUB = { id: 'listik-hint-hub', title: 'Хаб подсказки про открытых детей' }

/** Ровно шесть кейсов; имя кейса — id задачи, `shown` — ждём строку или её отсутствие. */
const cases = [
  { id: 'listik-hint-closed', shown: false },
  { id: 'listik-hint-closed-kids', shown: false },
  { id: 'listik-hint-cancelled-kids', shown: false },
  { id: 'listik-hint-open-kids', shown: true },
  { id: 'listik-hint-open-no-kids', shown: false },
  { id: 'listik-hint-open-done-kids', shown: false },
]

/* ── Сценарии ───────────────────────────────────────────────────────────── */

/**
 * Состояние панели: id в шапке, контейнер строк и всё, что в DOM содержит текст
 * предупреждения (`hintNodes` считает и скрытые элементы — пункт 2 ТЗ).
 */
const DRAWER_STATE = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  const hint = ${JSON.stringify(HINT_TEXT)};
  return {
    open: true,
    id: root.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    reasons: Boolean(root.querySelector('.listik-drawer__reasons')),
    warnings: [...root.querySelectorAll('.listik-drawer__reason--warning')].map((el) => ({
      text: el.textContent.replace(/\\s+/g, ' ').trim(),
      icon: Boolean(el.querySelector('svg')),
    })),
    hintNodes: [root, ...root.querySelectorAll('*')].filter((el) => el.textContent.includes(hint)).length,
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
    mock = await startMock(apiPort, root, ['--hint'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(DRAWER_STATE)

  /** Панель открыта и показывает именно этот id: у закрытых задач ссылка — единственный вход. */
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

  /** Хаб открывается карточкой доски: с него начинается переход в каждую задачу. */
  async function openHub() {
    if ((await state()).open) {
      await pressEscape()
      await waitFor(async () => ((await state()).open === false ? true : null))
    }
    const clicked = await clickCard(HUB.title)
    if (!clicked) throw new Error(`на доске нет карточки ${HUB.id} (${HUB.title})`)
    const ok = await waitDrawer(HUB.id, 10000)
    if (!ok) throw new Error(`панель не открыла ${HUB.id}: ${JSON.stringify(await state())}`)
  }

  /** Переход по ссылке из «Связей»: нет ссылки — кейс падает, обходных путей нет. */
  async function clickHubLink(id) {
    const clicked = await evaluate(`(() => {
      const button = [...document.querySelectorAll('.ui-drawer button.listik-link')]
        .find((b) => b.textContent.trim() === ${JSON.stringify(id)});
      if (!button) return false;
      button.click();
      return true;
    })()`)
    if (!clicked) throw new Error(`в «Связях» панели ${HUB.id} нет ссылки на ${id} — мок --hint отдал не те связи`)
    const ok = await waitDrawer(id, 10000)
    if (!ok) throw new Error(`панель не открыла ${id}: ${JSON.stringify(await state())}`)
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

  for (const item of cases) {
    await record(item.id, async () => {
      await openHub()
      await clickHubLink(item.id)
      const seen = await state()
      const expect = item.shown
        ? `строка «${HINT_TEXT}» с иконкой и контейнер .listik-drawer__reasons`
        : `ни одного элемента с текстом «${HINT_TEXT}»`
      const got = { id: seen.id, reasons: seen.reasons, warnings: seen.warnings, hintNodes: seen.hintNodes }
      if (item.shown) {
        const warned = seen.warnings.some((row) => row.text === HINT_TEXT)
        const icon = seen.warnings.some((row) => row.text === HINT_TEXT && row.icon)
        return { ok: seen.id === item.id && seen.reasons && warned && icon, expect, got }
      }
      return { ok: seen.id === item.id && seen.hintNodes === 0, expect, got }
    })
  }

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
