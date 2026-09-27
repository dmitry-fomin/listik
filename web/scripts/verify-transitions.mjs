/**
 * Подписи переходов конвейера берутся из routing сервера, а не из таблицы доски
 * (шаг listik-cvm8, порция b): рельса над колонками (`BoardView.vue`,
 * `.listik-rail__tr`), степпер панели задачи (`TaskDrawer.vue`, `UiSteps` —
 * «кто работал», стрелка перехода, «та же сессия»/«новый держатель»), подсказка
 * под кнопками (`.listik-drawer__reason`) и заголовок события `stage` в ленте
 * (тип — из `event.transition`, а не из заметки).
 *
 * Поднимает mock-api в режиме `--transitions` (у проекта `listik` переопределены
 * `s1-spec:s2-review` → `sticky` и `s3-impl:s4-judge` → `handoff`, общая
 * `meta.routing` — дефолт сервера; задачи проекта `plain`, которого нет в
 * `meta.projects`), отдаёт собранный `web/dist` и открывает доску в headless Chrome
 * через CDP.
 *
 * Запуск: node scripts/verify-transitions.mjs [url]
 *   Без аргумента сам поднимает мок и статику — нужен собранный `web/dist`
 *   (`npx vite build --configLoader runner`). С аргументом работает с уже
 *   отданной страницей (и предполагает, что её API отвечает как мок `--transitions`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя кейса, ok, что ждали и что увидели),
 * `consoleErrors` и `error` — только при исключении в самом скрипте. Код возврата 1,
 * если хоть один кейс не прошёл, в консоли были ошибки или скрипт упал.
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
const profile = mkdtempSync(join(tmpdir(), 'listik-transitions-'))

/** Задачи мока `--transitions`, чьи панели открывает проверка. */
const TASKS = {
  web: { id: 'listik-web-a1b2', title: 'Собрать доску канбан для трекера' },
  api: { id: 'listik-api-c3d4', title: 'Отдать needs_you одной лентой' },
  plain: { id: 'listik-transitions-plain', title: 'Задача проекта без строки в meta' },
  worked: { id: 'listik-transitions-worked', title: 'Кто работал: проект с переопределением' },
  workedPlain: { id: 'listik-transitions-worked-plain', title: 'Кто работал: общая таблица' },
  hint: { id: 'listik-transitions-hint', title: 'Подсказка перехода: общая таблица' },
}

/** Подписи рельсы по порядку колонок: текст и есть ли класс `is-sticky`. */
const RAIL = `(() => [...document.querySelectorAll('.listik-rail__tr')].map((el) => ({
  text: el.textContent.replace(/\\s+/g, ' ').trim(),
  sticky: el.classList.contains('is-sticky'),
})))()`

/** Панель задачи: id, шаги степпера (подпись и описание), подсказка, строки ленты. */
const DRAWER_STATE = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  const text = (el) => el?.textContent?.replace(/\\s+/g, ' ').trim() ?? null;
  return {
    open: true,
    id: text(root.querySelector('.listik-drawer__id .listik-mono')),
    steps: [...root.querySelectorAll('.ui-step')].map((step) => ({
      label: text(step.querySelector('.ui-step__label')),
      desc: text(step.querySelector('.ui-step__desc')),
    })),
    reason: text(root.querySelector('.listik-drawer__reason')),
    feed: [...root.querySelectorAll('.listik-feed-row')].map((row) => ({
      text: text(row.querySelector('.listik-feed-row__text')),
      subtext: text(row.querySelector('.listik-feed-row__subtext')),
    })),
  };
})()`

/** Описание шага степпера с подписью, начинающейся на `code · ` (s1…s4). */
const stepDesc = (drawer, code) => drawer.steps.find((step) => step.label?.startsWith(`${code} · `))?.desc ?? null

/** «Кто работал» — часть описания пройденного шага до первого ` · `. */
const workedPart = (desc) => (desc == null ? null : desc.split(' · ')[0])

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b)

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
    mock = await startMock(apiPort, root, ['--transitions'])
    staticServer = await serveDist(pagePort, apiPort, dist)
    url = `http://127.0.0.1:${pagePort}/?token=mock-token`
  }

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(DRAWER_STATE)

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

  /**
   * Открыть панель задачи кликом по карточке доски и дождаться её степпера и ленты.
   * Открытая панель делает фон inert — сначала закрыть её.
   */
  const openDrawer = async ({ id, title }) => {
    if ((await state()).open) {
      await pressEscape()
      await waitFor(async () => ((await state()).open === false ? true : null))
    }
    const clicked = await evaluate(`(() => {
      const card = [...document.querySelectorAll('.listik-task-card')]
        .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)});
      if (!card) return false;
      card.click();
      return true;
    })()`)
    if (!clicked) throw new Error(`на доске нет карточки ${id} (${title})`)
    const drawer = await waitFor(async () => {
      const seen = await state()
      return seen.id === id && seen.steps?.length === 5 && seen.feed?.length > 0 && seen.reason ? seen : null
    }, 10000)
    if (!drawer) throw new Error(`панель ${id} не открылась: ${JSON.stringify(await state())}`)
    return drawer
  }

  await send('Page.navigate', { url })
  // Доска отрисована и рельса подписана — meta (routing) уже пришла.
  const boardReady = await waitFor(async () => {
    const cards = await evaluate(`document.querySelectorAll('.listik-task-card').length`)
    const rail = await evaluate(RAIL)
    return cards > 0 && rail.length === 4 ? true : null
  }, 15000)
  if (!boardReady) throw new Error(`доска не отрисовалась: ${JSON.stringify(consoleErrors)}`)

  await record('rail-all', async () => {
    const rail = await evaluate(RAIL)
    const expect = { texts: ['handoff', 'handoff', 'sticky', 'handoff'], sticky: [false, false, true, false] }
    const got = { texts: rail.map((item) => item.text), sticky: rail.map((item) => item.sticky) }
    return { ok: same(got, expect), expect, got }
  })

  await record('drawer-listik-upcoming', async () => {
    const drawer = await openDrawer(TASKS.api)
    const expect = ['та же сессия', 'новый держатель', 'новый держатель']
    const got = ['s2', 's3', 's4'].map((code) => stepDesc(drawer, code))
    return { ok: same(got, expect), expect, got }
  })

  await record('drawer-plain-upcoming', async () => {
    const drawer = await openDrawer(TASKS.plain)
    const expect = ['новый держатель', 'новый держатель', 'та же сессия']
    const got = ['s2', 's3', 's4'].map((code) => stepDesc(drawer, code))
    return { ok: same(got, expect), expect, got }
  })

  // Одна панель `listik-web-a1b2` на три кейса: стрелки, заголовки событий, подсказка.
  const web = await openDrawer(TASKS.web)

  await record('drawer-past-arrows', async () => {
    const got = { s1: stepDesc(web, 's1'), s2: stepDesc(web, 's2') }
    const ok = Boolean(got.s1?.endsWith('sticky →')) && Boolean(got.s2?.endsWith('handoff →'))
    return { ok, expect: { s1: '… sticky →', s2: '… handoff →' }, got }
  })

  await record('event-titles', async () => {
    const find = (title) => web.feed.find((row) => row.text === title) ?? null
    const review = find('этап s2-review → s3-impl · handoff')
    const judge = find('этап s4-judge → s3-impl · sticky-return')
    const legacy = find('этап s1-spec → s2-review')
    const ok = review?.subtext === 'перешёл к реализации' && Boolean(judge) && Boolean(legacy)
    return {
      ok,
      expect: [
        { text: 'этап s2-review → s3-impl · handoff', subtext: 'перешёл к реализации' },
        { text: 'этап s4-judge → s3-impl · sticky-return' },
        { text: 'этап s1-spec → s2-review' },
      ],
      got: web.feed.filter((row) => row.text?.startsWith('stage ')),
    }
  })

  await record('worked-listik', async () => {
    const drawer = await openDrawer(TASKS.worked)
    const expect = ['я', 'я', '—']
    const got = ['s1', 's2', 's3'].map((code) => workedPart(stepDesc(drawer, code)))
    return { ok: same(got, expect), expect, got }
  })

  await record('worked-plain', async () => {
    const drawer = await openDrawer(TASKS.workedPlain)
    const expect = ['я', '—', '—']
    const got = ['s1', 's2', 's3'].map((code) => workedPart(stepDesc(drawer, code)))
    return { ok: same(got, expect), expect, got }
  })

  await record('hint-handoff', async () => {
    const got = web.reason
    const ok = Boolean(got?.startsWith('взять нельзя:')) && !got.includes(', переход')
    return { ok, expect: 'начинается с «взять нельзя:», без «, переход»', got }
  })

  await record('hint-sticky', async () => {
    const drawer = await openDrawer(TASKS.hint)
    const expect = ', переход s3 → s4 sticky — следующий этап идёт в той же сессии'
    const got = drawer.reason
    return { ok: Boolean(got?.includes(expect)), expect, got }
  })

  await record('rail-listik', async () => {
    if ((await state()).open) {
      await pressEscape()
      await waitFor(async () => ((await state()).open === false ? true : null))
    }
    const chip = await evaluate(`(() => {
      const label = [...document.querySelectorAll('.ui-chip__label')]
        .find((el) => el.textContent.trim() === 'Все проекты');
      if (!label) return false;
      label.click();
      return true;
    })()`)
    const picked = chip && (await waitFor(async () => evaluate(`(() => {
      const item = [...document.querySelectorAll('.ui-command-palette__item')]
        .find((el) => el.querySelector('.ui-command-palette__item-label')?.textContent.trim() === 'Listik');
      if (!item) return false;
      item.click();
      return true;
    })()`)))
    const expect = ['sticky', 'handoff', 'handoff', 'handoff']
    const rail = picked
      ? await waitFor(async () => {
          const texts = (await evaluate(RAIL)).map((item) => item.text)
          return same(texts, expect) ? texts : null
        })
      : null
    const got = rail ?? (await evaluate(RAIL)).map((item) => item.text)
    return { ok: Boolean(chip && picked) && same(got, expect), expect, got: { chip, picked: Boolean(picked), texts: got } }
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
if (report.error || failed > 0 || report.consoleErrors.length > 0) process.exitCode = 1
