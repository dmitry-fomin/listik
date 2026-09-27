/**
 * Строка выдачи палитры поиска — четыре колонки (listik-vjzq): эмблема проекта
 * (`ProjectMark`), иконка типа (`TaskGlyph kind="type"`), «id · заголовок» со
 * сниппетом под ним и иконка статуса готовности (`TaskGlyph kind="status"`,
 * подпись в aria-label/тултипе). Текстового хвоста «проект · этап · исполнитель»
 * справа нет — у палитры это `.ui-command-palette__item-hint`, его в строке
 * быть не должно, равно как слов «без исполнителя».
 *
 * Поднимает mock-api в обычном режиме (`/api/search` отдаёт одну задачу
 * `listik-web-a1b2`, проект `listik`, тип `feature` → фолбэк «задача», статус
 * `in_progress` → «в работе»), отдаёт собранный `web/dist` и гоняет сценарий в
 * headless Chrome через CDP на общих помощниках `scripts/lib/*`.
 *
 * Запуск: node scripts/verify-search-row.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок).
 *
 * Печатает JSON-отчёт: `cases[]` (имя, ok, ожидание, что увидели) и
 * `consoleErrors`. Код возврата 1, если хоть один кейс не прошёл, есть ошибки
 * консоли или сценарий упал целиком.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { cdpTarget, connect, freePort, serveDist, sleep, startChrome, startMock } from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9400 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-search-row-'))

/** Ожидания по фикстуре мока: задача `listik-web-a1b2` «Собрать доску канбан для трекера». */
const EXPECT = {
  pmark: 'Li',
  pmarkTitle: 'listik',
  typeTitle: 'задача',
  statusTitle: 'в работе',
  label: 'listik-web-a1b2 · Собрать доску канбан для трекера',
  description: 'найден в описании задачи',
}

/**
 * Срез первой строки выдачи: эмблема, глифы (подпись из aria-label), текст
 * тела, хвост-подсказка и порядок колонок по верхним детям <li>.
 */
const ROW = `(() => {
  const item = document.querySelector('.ui-command-palette__item');
  if (!item) return null;
  const q = (sel) => item.querySelector(sel);
  return {
    pmark: q('.listik-pmark')?.textContent?.trim() ?? null,
    pmarkTitle: q('.listik-pmark')?.getAttribute('title') ?? null,
    glyphs: [...item.querySelectorAll('.listik-glyph')].map((el) => ({
      title: el.getAttribute('aria-label'),
      svg: el.querySelector('svg') !== null,
    })),
    label: q('.search-result-row__label')?.textContent?.trim() ?? null,
    description: q('.search-result-row__description')?.textContent?.trim() ?? null,
    hint: q('.ui-command-palette__item-hint')?.textContent ?? null,
    order: [...item.children].map((el) => el.classList[0] ?? el.tagName),
    text: item.textContent.replace(/\\s+/g, ' ').trim(),
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
    mock = await startMock(apiPort, root)
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)
  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Page.navigate', { url })
  await sleep(800)

  // Открыть палитру кнопкой шапки и набрать запрос (v-model ← событие input).
  await evaluate(`(() => {
    const button = document.querySelector('button[aria-label="Поиск по задачам"]');
    if (!button) return false;
    button.click();
    return true;
  })()`)
  const inputReady = await waitFor(async () =>
    evaluate(`(() => Boolean(document.querySelector('.ui-command-palette__input')))()`),
  )
  if (!inputReady) throw new Error('палитра не открылась')
  await evaluate(`(() => {
    const input = document.querySelector('.ui-command-palette__input');
    input.value = 'трекера';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  })()`)

  const row = await waitFor(async () => evaluate(ROW))
  if (!row) throw new Error('строка выдачи не появилась')

  await record('эмблема проекта — первая колонка', async () => ({
    ok: row.pmark === EXPECT.pmark && row.pmarkTitle === EXPECT.pmarkTitle && row.order[0] === 'listik-row',
    expect: { pmark: EXPECT.pmark, title: EXPECT.pmarkTitle, first: 'listik-row' },
    got: { pmark: row.pmark, title: row.pmarkTitle, first: row.order[0] },
  }))

  await record('тип задачи иконкой — вторая колонка', async () => ({
    ok: row.glyphs[0]?.title === EXPECT.typeTitle && row.glyphs[0]?.svg === true && row.order[1] === 'ui-tooltip__trigger',
    expect: EXPECT.typeTitle,
    got: { glyph: row.glyphs[0] ?? null, second: row.order[1] ?? null },
  }))

  await record('id · заголовок и суть — третья колонка', async () => ({
    ok: row.label === EXPECT.label && (row.description ?? '').includes(EXPECT.description),
    expect: EXPECT,
    got: { label: row.label, description: row.description },
  }))

  await record('статус готовности иконкой — четвёртая колонка', async () => ({
    ok: row.glyphs[1]?.title === EXPECT.statusTitle && row.glyphs[1]?.svg === true,
    expect: EXPECT.statusTitle,
    got: row.glyphs[1] ?? null,
  }))

  await record('текстового хвоста «проект · статус · исполнитель» нет', async () => ({
    ok: row.hint === null && !row.text.includes('без исполнителя') && !row.text.includes('s3-impl'),
    expect: 'нет .ui-command-palette__item-hint, «без исполнителя», кода этапа',
    got: { hint: row.hint, text: row.text },
  }))

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
