/**
 * Проверка реакции открытой карточки (drawer) на события доски `/api/stream`
 * (listik-ch3v): событие по открытой задаче перечитывает её — комментарии,
 * история и этап в панели не остаются старыми до ручного обновления, — при этом
 * не сбрасываются прокрутка и несохранённый ввод; событие по чужой задаче
 * открытую карточку не перечитывает.
 *
 * Поднимает `scripts/mock-api.mjs`, отдаёт собранный `web/dist` и гоняет
 * сценарий в headless Chrome через CDP. Кадр в SSE шлёт служебная ручка мока
 * `POST /__event` — она же правит заглушку (заголовок задачи) и дописывает
 * комментарий, чтобы перечитывание было видно в панели, а `GET /__requests`
 * считает чтения `GET /api/tasks/{id}`: по счётчику и отличаем «перечитала» от
 * «показала то же самое».
 *
 * Запуск: node scripts/verify-detail-sse.mjs
 *   Нужен собранный `web/dist` (`npx vite build --configLoader runner`).
 *
 * Печатает JSON-отчёт: `cases[]` (имя, ok, что увидели) и `consoleErrors`.
 * Код возврата 1, если хоть один сценарий не прошёл.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { cdpTarget, connect, freePort, serveDist, sleep, startChrome, startMock } from './lib/browser-harness.mjs'
import { record as recordCase, waitFor as waitForCommon } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9400 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-detail-sse-'))

/** Открытая задача и её сосед: заголовки — из фикстур mock-api.mjs. */
const OPEN_ID = 'listik-web-a1b2'
const OPEN_TITLE = 'Собрать доску канбан для трекера'
const OPEN_TITLE_NEW = 'Доска: карточка перечитана по SSE'
const OTHER_ID = 'listik-api-c3d4'
const OTHER_TITLE = 'Отдать needs_you одной лентой'
const OTHER_TITLE_NEW = 'Чужая задача: карточку трогать не должны'
const DRAFT = 'черновик журнала: не потерять при обновлении'
const OPEN_COMMENT = 'SSE: новая запись в открытой карточке'
const OTHER_COMMENT = 'SSE: запись в чужой карточке'

/** Служебные ручки мока: сброс/чтение счётчиков и кадр в `/api/stream`. */
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

/* ── Сценарий ───────────────────────────────────────────────────────────── */

/** Состояние панели: задача, её текст, черновик, прокрутка, скелеты. */
const DRAWER_STATE = `(() => {
  const root = document.querySelector('.ui-drawer');
  if (!root) return { open: false };
  const body = root.querySelector('.ui-drawer__body');
  const input = root.querySelector('input[aria-label="Текст записи"]');
  return {
    open: true,
    id: root.querySelector('.listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    title: root.querySelector('.listik-drawer__title')?.textContent?.trim() ?? null,
    text: root.innerText,
    draft: input ? input.value : null,
    scrollTop: body ? Math.round(body.scrollTop) : null,
    scrollHeight: body ? body.scrollHeight : null,
    clientHeight: body ? body.clientHeight : null,
    skeletons: root.querySelectorAll('.ui-skeleton').length,
  };
})()`

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)
const waitFor = (check, timeout = 6000) => waitForCommon(check, timeout)
let mock = null
let staticServer = null
let chrome = null

try {
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error('нет web/dist — соберите: npx vite build --configLoader runner')
  }
  const apiPort = await freePort()
  const pagePort = await freePort()
  mock = await startMock(apiPort, root, [])
  staticServer = await serveDist(pagePort, apiPort, dist)
  const url = `http://127.0.0.1:${pagePort}/?token=mock-token`

  chrome = startChrome(chromePath, chromePort, profile)

  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(DRAWER_STATE)

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

  // Открываем карточку и ждём, пока она доедет вместе с подпиской на поток.
  const opened = (await cardExists(OPEN_TITLE)) && (await clickCard(OPEN_TITLE))
  const drawerReady = await waitFor(async () => {
    const current = await state()
    return current.open && current.id === OPEN_ID ? current : null
  })
  if (!opened || !drawerReady) throw new Error(`панель не открыла ${OPEN_ID}: ${JSON.stringify(await state())}`)

  const streamed = await waitFor(async () => {
    const counters = await mockCall(apiPort, '/__requests')
    return counters.streams > 0 ? counters.streams : null
  }, 8000)

  await record('поток событий подключён', async () => ({
    ok: Boolean(streamed),
    expect: 'подписчиков /api/stream ≥ 1',
    got: streamed ?? 0,
  }))

  // ── A. Событие по открытой задаче: перечитывает, не сбрасывая прокрутку и ввод ──
  await record('событие по открытой задаче перечитывает карточку', async () => {
    await mockCall(apiPort, '/__requests', {})
    // Прокрутка в середину и несохранённый черновик в поле журнала — то, что
    // наивное «открыть заново» (detail = null + скелет) как раз и теряет.
    const prepared = await evaluate(`(() => {
      const body = document.querySelector('.ui-drawer .ui-drawer__body');
      const input = document.querySelector('.ui-drawer input[aria-label="Текст записи"]');
      if (!body || !input) return null;
      body.scrollTop = Math.floor((body.scrollHeight - body.clientHeight) / 2);
      const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
      setter.call(input, ${JSON.stringify(DRAFT)});
      input.dispatchEvent(new Event('input', { bubbles: true }));
      // Скелет в панели — это и есть сброс прокрутки: следим, не мелькнёт ли он.
      window.__skeletonSeen = 0;
      window.__skeletonObserver?.disconnect();
      const root = document.querySelector('.ui-drawer');
      const observer = new MutationObserver(() => {
        if (root.querySelector('.ui-skeleton')) window.__skeletonSeen += 1;
      });
      observer.observe(root, { childList: true, subtree: true });
      window.__skeletonObserver = observer;
      return {
        scrollTop: Math.round(body.scrollTop),
        scrollHeight: body.scrollHeight,
        clientHeight: body.clientHeight,
        draft: input.value,
      };
    })()`)
    if (!prepared) throw new Error('в панели нет тела или поля журнала')
    await sleep(30)
    const baseline = (await mockCall(apiPort, '/__requests')).detail_reads[OPEN_ID] ?? 0

    const sent = await mockCall(apiPort, '/__event', {
      kind: 'task',
      payload: { id: OPEN_ID, action: 'comment' },
      patch: { title: OPEN_TITLE_NEW },
      comment: { kind: 'journal', text: OPEN_COMMENT },
    })

    const after = await waitFor(async () => {
      const current = await state()
      return current.title === OPEN_TITLE_NEW && current.text.includes(OPEN_COMMENT) ? current : null
    })

    const counters = await mockCall(apiPort, '/__requests')
    const reads = (counters.detail_reads[OPEN_ID] ?? 0) - baseline
    const skeletons = await evaluate('window.__skeletonSeen ?? -1')
    const checks = {
      'кадр ушёл подписчику': sent.clients >= 1,
      'в панели новая запись журнала': Boolean(after) && after.text.includes(OPEN_COMMENT),
      'заголовок карточки обновлён': Boolean(after) && after.title === OPEN_TITLE_NEW,
      'карточка перечитана запросом': reads >= 1,
      'панель прокручиваема': prepared.scrollHeight > prepared.clientHeight,
      'прокрутка не сброшена': Boolean(after) && prepared.scrollTop > 0 && after.scrollTop === prepared.scrollTop,
      'черновик не потерян': Boolean(after) && after.draft === DRAFT,
      'без скелета-заглушки': skeletons === 0,
      'та же задача в панели': Boolean(after) && after.id === OPEN_ID,
    }
    return {
      ok: Object.values(checks).every(Boolean),
      checks,
      expect: `панель ${OPEN_ID} показывает «${OPEN_TITLE_NEW}» и запись «${OPEN_COMMENT}»`,
      got: {
        sent: sent.clients,
        reads,
        title: after?.title ?? null,
        scrollTop: `${prepared.scrollTop} → ${after?.scrollTop ?? null}`,
        scrollable: prepared.scrollHeight > prepared.clientHeight,
        draft: after?.draft ?? null,
        skeletons,
      },
    }
  })

  // ── B. Событие по чужой задаче: открытую карточку не перечитывает ─────────
  await record('событие по чужой задаче карточку не перечитывает', async () => {
    await mockCall(apiPort, '/__requests', {})
    const before = await state()
    // Контроль: чужая карточка есть на доске — её переименование будет видно.
    const otherOnBoard = await cardExists(OTHER_TITLE)
    const sent = await mockCall(apiPort, '/__event', {
      kind: 'task',
      payload: { id: OTHER_ID, action: 'comment' },
      patch: { title: OTHER_TITLE_NEW },
      comment: { kind: 'journal', text: OTHER_COMMENT },
    })
    // Контроль: кадр дошёл и доска его применила — чужая карточка переехала.
    const boardMoved = await waitFor(async () => ((await cardExists(OTHER_TITLE_NEW)) ? true : null))
    await sleep(700)
    const after = await state()
    const counters = await mockCall(apiPort, '/__requests')
    const reads = counters.detail_reads[OPEN_ID] ?? 0
    const checks = {
      'кадр ушёл подписчику': sent.clients >= 1,
      'чужая карточка есть на доске до события': otherOnBoard === true,
      'доска обновила чужую карточку': boardMoved === true,
      'открытая карточка не перечитана': reads === 0,
      'в панели прежняя задача': after.id === OPEN_ID && after.title === before.title,
      'чужой записи в панели нет': !after.text.includes(OTHER_COMMENT),
      'черновик не тронут': after.draft === DRAFT,
    }
    return {
      ok: Object.values(checks).every(Boolean),
      checks,
      expect: `панель осталась на «${before.title}», чтений ${OPEN_ID}: 0`,
      got: { sent: sent.clients, reads, otherOnBoard, boardMoved, title: after.title, draft: after.draft },
    }
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
