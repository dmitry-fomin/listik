/**
 * Проверка статуса подзадачи в дереве «родитель → эта задача → дети» панели задачи
 * (`web/src/components/TaskDrawer.vue`, ряд `listik-dep-tree__row--child`,
 * бейдж `listik-dep-tree__status`) — шаг listik-zr05, порция h.
 *
 * Поднимает mock-api в режиме `--epic` (эпик `listik-epic-hub` и четыре подзадачи во
 * всех статусах, поле `children` в карточке эпика), отдаёт собранный `web/dist` и
 * открывает панель эпика в headless Chrome через CDP.
 *
 * Запуск: node scripts/verify-epic.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--epic`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя кейса — id подзадачи, ok, что ждали и что
 * увидели) и `consoleErrors`. Код возврата 1, если хоть один кейс не прошёл или в
 * консоли были ошибки.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { PIPELINE_STAGES, statusTitle } from '../src/lib/dictionaries.ts'
import { cdpTarget, connect, freePort, serveDist, startChrome, startMock } from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9800 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-epic-'))

const HUB = { id: 'listik-epic-hub', title: 'Эпик: проверка подзадач' }

/** Четыре подзадачи мока `--epic`: `stage` — этап карточки, `null` — этапа нет. */
const cases = [
  { id: 'listik-epic-open', title: 'Подзадача открыта', status: 'open', stage: 's1-spec' },
  { id: 'listik-epic-work', title: 'Подзадача в работе', status: 'in_progress', stage: 's3-impl' },
  { id: 'listik-epic-done', title: 'Подзадача готова', status: 'done', stage: 'done' },
  { id: 'listik-epic-cancel', title: 'Подзадача отменена', status: 'cancelled', stage: null },
]

/**
 * Бейдж этапа ряда — как в панели: есть, только когда у этапа есть код (`stageCode`);
 * у этапа `done` кода в словаре нет — бейджа нет вовсе, а не пустой.
 */
const stageBadges = (stage) => {
  const code = PIPELINE_STAGES.find((step) => step.value === stage)?.code
  return code ? [code] : []
}

/** Ряды детей в дереве панели: id, заголовок, подпись статуса и прочие бейджи (этап). */
const TREE_ROWS = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  const text = (el) => el?.textContent?.replace(/\\s+/g, ' ').trim() ?? null;
  return {
    open: true,
    id: text(root.querySelector('.listik-drawer__id .listik-mono')),
    rows: [...root.querySelectorAll('.listik-dep-tree__row--child')].map((row) => ({
      id: text(row.querySelector('.listik-mono')),
      title: text(row.querySelector('.listik-dep-tree__title')),
      status: [...row.querySelectorAll('.listik-dep-tree__status')].map(text),
      badges: [...row.querySelectorAll('.ui-badge:not(.listik-dep-tree__status)')].map(text),
    })),
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
    mock = await startMock(apiPort, root, ['--epic'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  await send('Page.navigate', { url })
  const boardReady = await waitFor(
    async () => ((await evaluate(`document.querySelectorAll('.listik-task-card').length`)) > 0 ? true : null),
    15000,
  )
  if (!boardReady) throw new Error(`доска не отрисовалась: ${JSON.stringify(consoleErrors)}`)

  const clicked = await evaluate(`(() => {
    const card = [...document.querySelectorAll('.listik-task-card')]
      .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(HUB.title)});
    if (!card) return false;
    card.click();
    return true;
  })()`)
  if (!clicked) throw new Error(`на доске нет карточки ${HUB.id} (${HUB.title})`)
  // Ждём панель эпика с деревом из всех четырёх детей: `children` приходит с карточкой.
  const tree = await waitFor(async () => {
    const seen = await evaluate(TREE_ROWS)
    return seen.id === HUB.id && seen.rows?.length >= cases.length ? seen : null
  }, 10000)
  if (!tree) throw new Error(`панель ${HUB.id} не показала дерево детей: ${JSON.stringify(await evaluate(TREE_ROWS))}`)

  for (const item of cases) {
    await record(item.id, async () => {
      const row = tree.rows.find((r) => r.id === item.id) ?? null
      const expect = {
        id: item.id,
        title: item.title,
        status: [statusTitle(item.status)],
        badges: stageBadges(item.stage),
      }
      const ok = Boolean(row)
        && row.title === expect.title
        && JSON.stringify(row.status) === JSON.stringify(expect.status)
        && JSON.stringify(row.badges) === JSON.stringify(expect.badges)
      return { ok, expect, got: row }
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
if (report.error || failed > 0 || report.consoleErrors.length > 0) process.exitCode = 1
