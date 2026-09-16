/**
 * Проверка идентичности «я — …» на доске (шаг listik-xt69, порция c).
 *
 * Поднимает mock-api в режиме `--server-mode` (пользователи `ann`/`bob`, задачи
 * part-ann/part-bob/без владельца, фильтрация списков по заголовку
 * `X-Listik-Owner`), отдаёт собранный `web/dist` и гоняет сценарий в headless
 * Chrome через CDP. Мок пишет строку на каждый запрос (метод, путь, заголовок,
 * у пишущих — ключи тела) — по этому логу проверяются заголовки и то, что смена
 * имени ничего не пишет.
 *
 * Сценарий:
 *   1. в шапке есть селект «От чьего имени» с `ann`/`bob`, видна подпись
 *      «Представьтесь…», на доске есть карточки ann, bob и без владельца;
 *   2. открытая панель задачи `ann` + выбор `bob` в шапке: запросы идут с новым
 *      заголовком, чужих карточек на доске нет, у своих — бейдж владельца,
 *      панель осталась открытой;
 *   3. действие по чужой задаче из открытой панели: сервер отвечает 403, доска
 *      показывает его текст (с именем владельца), карточка не изменилась;
 *      там же — строка «владелец» в панели и поле «Владелец» формы новой задачи
 *      (предзаполнено тем, кем представились);
 *   4. перезагрузка: имя восстановлено из localStorage, первый `GET /api/board`
 *      идёт уже с заголовком и строго после `GET /api/health`;
 *   5. «все задачи»: ключ из localStorage удалён, чужие карточки снова видны;
 *   6. за весь прогон в логе нет ни одного пишущего запроса, кроме одного
 *      действия из шага 3.
 *   7. второй проход против мока без флага: ни селекта, ни подписи.
 *
 * Про шаг 3: в панели задачи нет кнопки «взять» — `TaskDrawer.vue` объявляет
 * событие `claim`, но нигде его не эмитит (обработчик `@claim` в App.vue
 * мёртвый), поэтому запрет на чужую задачу проверяется кнопкой «Heartbeat»:
 * контракт порции ставит `claim`/`heartbeat`/`stage` в один ряд — запрос с
 * `holder` на чужую задачу отвечает 403 с именем владельца.
 *
 * Запуск: node scripts/verify-owner.mjs
 *   Нужен собранный `web/dist` (`npm run build`). Мок и статику скрипт поднимает
 *   сам: лог мока нужен ему построчно, поэтому готовый url аргументом не берётся.
 *
 * Печатает JSON-отчёт: `cases[]` (имя, ok, что увидели) и `consoleErrors`.
 * Код возврата 1, если хоть один сценарий не прошёл.
 */
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { cdpTarget, connect, freePort, serveDist, sleep, startChrome } from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'

const ANN_CARD = 'Ann: первая задача'
const ANN_ID = 'listik-owner-ann1'

const report = { cases: [], mockLog: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)

/* ── мок со снятым stdout: строки лога — предмет проверки ─────────────────── */

async function startLoggingMock(port, args) {
  const lines = []
  const child = spawn(process.execPath, [join(root, 'scripts', 'mock-api.mjs'), String(port), ...args], {
    stdio: ['ignore', 'pipe', 'inherit'],
  })
  let tail = ''
  child.stdout.setEncoding('utf8')
  child.stdout.on('data', (chunk) => {
    tail += chunk
    const parts = tail.split('\n')
    tail = parts.pop() ?? ''
    for (const line of parts) if (line.trim()) lines.push(line.trim())
  })
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`)
      if (response.ok) return { child, lines }
    } catch {
      /* мок ещё поднимается */
    }
    await sleep(100)
  }
  child.kill()
  throw new Error('mock-api не поднялся')
}

/** Строка лога → разбор: метод, путь, имя из заголовка, ключи тела. */
function parseLine(line) {
  const match = /^(\w+) (\S+) x-listik-owner: (\S+)(?: body: (\S*))?$/.exec(line)
  if (!match) return null
  return {
    method: match[1],
    path: match[2],
    owner: match[3] === '-' ? null : match[3],
    body: match[4] && match[4] !== '-' ? match[4].split(',') : [],
  }
}

const parsed = (lines, from = 0) => lines.slice(from).map(parseLine).filter(Boolean)

/* ── состояние страницы ───────────────────────────────────────────────────── */

/** Шапка: селект «я — …», статичная подпись, карточки доски с бейджами. */
const PAGE_STATE = `(() => {
  const trigger = document.querySelector('[aria-label="От чьего имени"]');
  const chips = [...document.querySelectorAll('.ui-chip')].map((el) => el.textContent.replace(/\\s+/g, ' ').trim());
  const cards = [...document.querySelectorAll('.listik-task-card')].map((el) => ({
    title: el.querySelector('.listik-task-card__title')?.textContent?.trim() ?? '',
    badges: [...el.querySelectorAll('.ui-badge')].map((b) => b.textContent.trim()),
  }));
  return {
    hasOwnerSelect: Boolean(trigger),
    ownerValue: trigger ? trigger.querySelector('.ui-select__value')?.textContent?.trim() ?? null : null,
    hasHint: chips.some((text) => text.startsWith('Представьтесь')),
    options: [...document.querySelectorAll('[role="listbox"] [role="option"]')].map((el) =>
      el.textContent.replace(/\\s+/g, ' ').trim(),
    ),
    drawerId: document.querySelector('.ui-drawer .listik-drawer__id .listik-mono')?.textContent?.trim() ?? null,
    toasts: [...document.querySelectorAll('.ui-toast')].map((el) => el.textContent.replace(/\\s+/g, ' ').trim()),
    storedOwner: localStorage.getItem('listik.owner'),
    cards,
  };
})()`

let mock = null
let staticServer = null
let chrome = null
let profile = null

try {
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error('нет web/dist — соберите: npm run build')
  }
  profile = mkdtempSync(join(tmpdir(), 'listik-owner-'))
  const apiPort = await freePort()
  const pagePort = await freePort()
  const chromePort = 9800 + Math.floor(Math.random() * 190)
  const started = await startLoggingMock(apiPort, ['--server-mode'])
  mock = started.child
  const lines = started.lines
  staticServer = await serveDist(pagePort, apiPort, dist)
  const url = `http://127.0.0.1:${pagePort}/?token=mock-token`

  chrome = startChrome(chromePath, chromePort, profile)
  const { socket, ready, send, evaluate, consoleErrors } = connect(await cdpTarget(chromePort))
  await ready
  await send('Runtime.enable')
  await send('Page.enable')

  const state = () => evaluate(PAGE_STATE)

  const openOwnerSelect = () =>
    evaluate(`(() => {
      const trigger = document.querySelector('[aria-label="От чьего имени"]');
      if (!trigger) return false;
      trigger.click();
      return true;
    })()`)

  const pickOwnerOption = (label) =>
    evaluate(`(() => {
      const option = [...document.querySelectorAll('[role="listbox"] [role="option"]')]
        .find((el) => el.textContent.replace(/\\s+/g, ' ').trim() === ${JSON.stringify(label)});
      if (!option) return false;
      option.click();
      return true;
    })()`)

  const chooseOwner = async (label) => {
    if (!(await openOwnerSelect())) return false
    const opened = await waitFor(async () => ((await state()).options.length > 0 ? true : null))
    if (!opened) return false
    return pickOwnerOption(label)
  }

  const clickCard = (title) =>
    evaluate(`(() => {
      const card = [...document.querySelectorAll('.listik-task-card')]
        .find((el) => el.getAttribute('aria-label') === 'Открыть задачу ' + ${JSON.stringify(title)});
      if (!card) return false;
      card.click();
      return true;
    })()`)

  const clickHeartbeat = () =>
    evaluate(`(() => {
      const button = document.querySelector('.ui-drawer [aria-label="Heartbeat"]');
      if (!button || button.disabled) return false;
      button.click();
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
      text: document.body.innerText.replace(/\\s+/g, ' ').slice(0, 400),
    }))()`)
    throw new Error(`доска не отрисовалась: ${JSON.stringify(diagnostics)} ${JSON.stringify(consoleErrors)}`)
  }

  // 1. Шапка: селект с именами, подпись «Представьтесь…», все задачи видно.
  await record('шапка: селект «я — …» с ann/bob и подпись, на доске задачи всех владельцев', async () => {
    const before = await state()
    await openOwnerSelect()
    const opened = await waitFor(async () => {
      const seen = await state()
      return seen.options.length > 0 ? seen : null
    })
    // Панель селекта закрываем обратно — дальше кликаем по доске.
    await evaluate(`document.querySelector('[aria-label="От чьего имени"]')?.click()`)
    const titles = before.cards.map((card) => card.title)
    const ok = before.hasOwnerSelect
      && before.hasHint
      && Boolean(opened)
      && opened.options.includes('ann')
      && opened.options.includes('bob')
      && opened.options.includes('все задачи')
      && titles.includes(ANN_CARD)
      && titles.includes('Bob: первая задача')
      && titles.includes('Ничья задача раз')
      && before.cards.find((card) => card.title === ANN_CARD)?.badges.includes('ann') === true
    return {
      ok,
      expect: 'селект с ann/bob и «все задачи», подпись «Представьтесь…», карточки ann/bob/без владельца',
      got: { hasOwnerSelect: before.hasOwnerSelect, hasHint: before.hasHint, options: opened?.options, cards: before.cards },
    }
  })

  // 2. Открытая панель + смена имени: заголовок в запросах, фильтрация, панель жива.
  let switchAt = 0
  await record('смена «я — …» при открытой панели: запросы с заголовком, чужих карточек нет', async () => {
    await clickCard(ANN_CARD)
    const opened = await waitFor(async () => ((await state()).drawerId === ANN_ID ? true : null))
    if (!opened) throw new Error(`панель не открыла ${ANN_ID}`)
    switchAt = lines.length
    const picked = await chooseOwner('bob')
    const after = await waitFor(async () => {
      const seen = await state()
      const titles = seen.cards.map((card) => card.title)
      return !titles.includes(ANN_CARD) && titles.includes('Bob: первая задача') ? seen : null
    }, 10000)
    const boardCalls = parsed(lines, switchAt).filter((item) => item.method === 'GET' && item.path.startsWith('/api/board'))
    const titles = after?.cards.map((card) => card.title) ?? []
    const ok = Boolean(picked)
      && Boolean(after)
      && boardCalls.length > 0
      && boardCalls.every((item) => item.owner === 'bob')
      && !titles.includes(ANN_CARD)
      && titles.includes('Ничья задача раз')
      && after.cards.find((card) => card.title === 'Bob: первая задача')?.badges.includes('bob') === true
      && after.hasHint === false
      && after.ownerValue === 'bob'
      && after.drawerId === ANN_ID
    return {
      ok,
      expect: 'GET /api/board с x-listik-owner: bob, карточек ann нет, бейдж bob, панель ann осталась открытой',
      got: { picked, boardCalls, cards: after?.cards, drawerId: after?.drawerId, ownerValue: after?.ownerValue },
    }
  })

  // 3. Действие по чужой задаче из открытой панели: 403 с именем владельца.
  let actionAt = 0
  await record('действие по чужой задаче: тост с именем владельца, карточка не изменилась', async () => {
    actionAt = lines.length
    const clicked = await clickHeartbeat()
    const toasted = await waitFor(async () => {
      const seen = await state()
      const toast = seen.toasts.find((text) => text.includes('ann'))
      return toast ? { seen, toast } : null
    }, 10000)
    const writes = parsed(lines, actionAt).filter((item) => item.method !== 'GET')
    const response = await fetch(`http://127.0.0.1:${apiPort}/api/tasks/${ANN_ID}`)
    const task = (await response.json())?.data ?? {}
    const ok = Boolean(clicked)
      && Boolean(toasted)
      && toasted.toast.includes('принадлежит ann')
      && writes.length === 1
      && writes[0].path === `/api/tasks/${ANN_ID}/heartbeat`
      && writes[0].owner === 'bob'
      && !writes[0].body.includes('owner')
      && task.owner === 'ann'
      && task.holder === null
      && toasted.seen.drawerId === ANN_ID
    return {
      ok,
      expect: 'один POST …/heartbeat с заголовком bob и без owner в теле, тост с именем ann, задача не изменилась',
      got: { clicked, toast: toasted?.toast, writes, owner: task.owner, holder: task.holder },
    }
  })

  // 3b. Строка «Владелец» в открытой панели задачи: селект с текущим владельцем.
  await record('панель задачи: строка «владелец» с селектом и текущим значением', async () => {
    const seen = await evaluate(`(() => {
      const rows = [...document.querySelectorAll('.ui-drawer dt')];
      const dt = rows.find((el) => el.textContent.trim() === 'владелец');
      const select = document.querySelector('.ui-drawer [aria-label="Владелец задачи"]');
      return {
        hasRow: Boolean(dt),
        value: select?.querySelector('.ui-select__value')?.textContent?.trim() ?? null,
      };
    })()`)
    const ok = seen.hasRow && seen.value === 'ann'
    return { ok, expect: 'строка «владелец» и селект со значением ann', got: seen }
  })

  // 3c. Форма «Новая задача»: обязательное поле «Владелец», предзаполнено «я — …».
  await record('форма новой задачи: поле «Владелец» предзаполнено представившимся', async () => {
    for (const type of ['rawKeyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent', { type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 })
    }
    await waitFor(async () => ((await state()).drawerId === null ? true : null))
    const clicked = await evaluate(`(() => {
      const button = [...document.querySelectorAll('button')]
        .find((el) => el.textContent.replace(/\\s+/g, ' ').trim() === 'Новая задача');
      if (!button) return false;
      button.click();
      return true;
    })()`)
    const seen = await waitFor(async () => {
      const current = await evaluate(`(() => {
        const field = [...document.querySelectorAll('.ui-field')]
          .find((el) => el.textContent.replace(/\\s+/g, ' ').trim().startsWith('Владелец'));
        if (!field) return null;
        return { value: field.querySelector('.ui-select__value')?.textContent?.trim() ?? null };
      })()`)
      return current
    }, 10000)
    // Форму закрываем: она ничего не отправляет, но дальше идёт перезагрузка.
    for (const type of ['rawKeyDown', 'keyUp']) {
      await send('Input.dispatchKeyEvent', { type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 })
    }
    const ok = Boolean(clicked) && seen?.value === 'bob'
    return { ok, expect: 'поле «Владелец» со значением bob (кем представились)', got: { clicked, ...seen } }
  })

  // 4. Перезагрузка: имя из localStorage, первый board уже с заголовком и после health.
  await record('после перезагрузки: имя восстановлено, первый board идёт после health и с заголовком', async () => {
    const reloadAt = lines.length
    await send('Page.navigate', { url })
    const seen = await waitFor(async () => {
      const current = await state()
      return current.cards.length > 0 && current.ownerValue === 'bob' ? current : null
    }, 15000)
    const calls = parsed(lines, reloadAt)
    const healthIndex = calls.findIndex((item) => item.path.startsWith('/api/health'))
    const boardIndex = calls.findIndex((item) => item.path.startsWith('/api/board'))
    const firstBoard = boardIndex === -1 ? null : calls[boardIndex]
    const ok = Boolean(seen)
      && seen.storedOwner === 'bob'
      && seen.ownerValue === 'bob'
      && healthIndex !== -1
      && boardIndex > healthIndex
      && firstBoard?.owner === 'bob'
      // `/api/stream` — единственное исключение: EventSource заголовков не умеет
      // (поток не фильтруется, события лишь планируют обычный refresh).
      && calls.filter((item) => !item.path.startsWith('/api/stream')).every((item) => item.owner === 'bob')
    return {
      ok,
      expect: 'localStorage[listik.owner]=bob, селект «bob», health раньше board, все запросы (кроме SSE) с заголовком bob',
      got: { storedOwner: seen?.storedOwner, ownerValue: seen?.ownerValue, healthIndex, boardIndex, calls: calls.slice(0, 8) },
    }
  })

  // 5. «Все задачи»: ключ из localStorage удалён, чужие карточки снова видны.
  await record('«все задачи»: ключ удалён, карточки ann снова на доске', async () => {
    const picked = await chooseOwner('все задачи')
    const seen = await waitFor(async () => {
      const current = await state()
      return current.cards.some((card) => card.title === ANN_CARD) ? current : null
    }, 10000)
    const ok = Boolean(picked)
      && Boolean(seen)
      && seen.storedOwner === null
      && seen.hasHint === true
      && seen.cards.some((card) => card.title === 'Bob: первая задача')
    return {
      ok,
      expect: 'localStorage без ключа listik.owner, видно и ann, и bob, вернулась подпись «Представьтесь…»',
      got: { picked, storedOwner: seen?.storedOwner, hasHint: seen?.hasHint, cards: seen?.cards.map((c) => c.title) },
    }
  })

  // 6. Инвариант: смена имени ничего не писала — за весь прогон один пишущий запрос.
  await record('за весь прогон один пишущий запрос — тот самый heartbeat', async () => {
    const writes = parsed(lines).filter((item) => item.method !== 'GET')
    const ok = writes.length === 1 && writes[0].path === `/api/tasks/${ANN_ID}/heartbeat`
    return { ok, expect: 'ни PATCH, ни POST кроме одного действия из шага 3', got: writes }
  })

  report.mockLog = lines.slice()
  report.consoleErrors = consoleErrors.slice()
  socket.close()
  chrome.kill()
  chrome = null
  staticServer.close()
  staticServer = null
  mock.kill()
  mock = null

  /* ── второй проход: мок без флага, локальный режим ────────────────────────── */

  const localApiPort = await freePort()
  const localPagePort = await freePort()
  const localChromePort = 9600 + Math.floor(Math.random() * 190)
  const localStarted = await startLoggingMock(localApiPort, [])
  mock = localStarted.child
  staticServer = await serveDist(localPagePort, localApiPort, dist)
  chrome = startChrome(chromePath, localChromePort, profile)
  const local = connect(await cdpTarget(localChromePort))
  await local.ready
  await local.send('Runtime.enable')
  await local.send('Page.enable')
  await local.send('Page.navigate', { url: `http://127.0.0.1:${localPagePort}/?token=mock-token` })
  await waitFor(
    async () => ((await local.evaluate(`document.querySelectorAll('.listik-task-card').length`)) > 0 ? true : null),
    15000,
  )

  await record('локальный режим: ни селекта «я — …», ни подписи, ни бейджей владельца', async () => {
    const seen = await local.evaluate(PAGE_STATE)
    const drawer = await local.evaluate(`(() => {
      const card = document.querySelector('.listik-task-card');
      if (card) card.click();
      return true;
    })()`)
    await sleep(600)
    const owners = await local.evaluate(`(() => ({
      hasOwnerRow: [...document.querySelectorAll('.ui-drawer dt')].some((el) => el.textContent.trim() === 'владелец'),
      hasOwnerField: Boolean(document.querySelector('[aria-label="Владелец задачи"]')),
    }))()`)
    const ok = seen.hasOwnerSelect === false
      && seen.hasHint === false
      && seen.cards.every((card) => !card.badges.includes('ann') && !card.badges.includes('bob'))
      && Boolean(drawer)
      && owners.hasOwnerRow === false
      && owners.hasOwnerField === false
    return {
      ok,
      expect: 'в локальном режиме новых элементов нет: ни селекта, ни подписи, ни строки «владелец»',
      got: { ...seen, ...owners, cards: seen.cards.map((card) => card.title) },
    }
  })

  report.consoleErrors = [...report.consoleErrors, ...local.consoleErrors]
  local.socket.close()
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  chrome?.kill()
  staticServer?.close()
  mock?.kill()
  if (profile) {
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
    } catch {
      /* временный профиль уберёт система */
    }
  }
}

console.log(JSON.stringify(report, null, 2))
const failed = report.cases.filter((item) => !item.ok).length
if (report.error || failed > 0) process.exitCode = 1
