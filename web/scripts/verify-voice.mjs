/**
 * Проверка голосового ввода задачи на доске (порция b, listik-8hrq):
 * `web/src/components/VoiceCapture.vue` — кнопка «Голосом» рядом с «Новая задача»,
 * запись через стаб микрофона, расшифровка, черновик, создание одной кнопкой и
 * предзаполнение формы «Новая задача».
 *
 * Поднимает `scripts/mock-api.mjs` в двух режимах: без `--voice` (кнопки нет) и с
 * `--routes --assistant --voice` (полный голосовой поток, маркеры сценариев
 * `silence`, `transcribe-fail`, `draft-fail`, `noproject`, `notitle`,
 * `badproject` — неизвестный доске slug), отдаёт
 * собранный `web/dist` и гоняет сценарии в headless Chrome по CDP. Микрофон
 * подменяется стабом, внедрённым до загрузки страницы
 * (`Page.addScriptToEvaluateOnNewDocument`): `navigator.mediaDevices.getUserMedia`,
 * `MediaRecorder` и счётчики `window.__listikVoiceStats`.
 *
 * Счётчики запросов (transcribe/draft/create, last_create) — только через
 * служебную ручку мока `/__requests`: `scripts/lib/browser-harness.mjs` перехватывает
 * тела лишь у PATCH, а править `scripts/lib/` запрещено.
 *
 * Запуск: node scripts/verify-voice.mjs
 *   Нужен собранный `web/dist` (`npm run build`). Печатает JSON-отчёт
 *   `{cases: [{name, ok, got}], consoleErrors}`; код возврата 1, если хоть один
 *   сценарий не прошёл.
 */
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  cdpTarget,
  connect,
  freePort,
  serveDist,
  sleep,
  startChrome,
  startMock,
} from './lib/browser-harness.mjs'
import { record as recordCase, waitFor } from './lib/verify-helpers.mjs'

const root = fileURLToPath(new URL('..', import.meta.url))
const dist = join(root, 'dist')
const chromePath = process.env.CHROME_PATH ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const chromePort = 9200 + Math.floor(Math.random() * 400)
const profile = mkdtempSync(join(tmpdir(), 'listik-voice-'))

/**
 * Стаб браузерных API записи. Внедряется до загрузки страницы и живёт на каждой
 * навигации: `window.__listikVoiceCase` выбирает сценарий (маркер кладётся в blob),
 * `__listikVoiceStats` считает вызовы getUserMedia и остановки дорожек,
 * `__listikVoiceDelay` (мс) откладывает выдачу потока — так проверяется отпускание V
 * до разрешения микрофона.
 */
const STUB = `
(() => {
  window.__listikVoiceCase = 'ok';
  window.__listikVoiceDelay = 0;
  window.__listikVoiceStats = { getUserMedia: 0, tracksStopped: 0, started: 0, stopped: 0 };
  const stats = window.__listikVoiceStats;
  let mediaDevices = navigator.mediaDevices;
  if (!mediaDevices) {
    mediaDevices = {};
    try {
      Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: mediaDevices });
    } catch (error) {
      navigator.mediaDevices = mediaDevices;
    }
  }
  mediaDevices.getUserMedia = async () => {
    stats.getUserMedia += 1;
    if (window.__listikVoiceCase === 'denied') {
      throw new DOMException('Permission denied', 'NotAllowedError');
    }
    const delay = Number(window.__listikVoiceDelay) || 0;
    if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
    const track = { stop() { stats.tracksStopped += 1; } };
    return { getTracks() { return [track]; } };
  };
  class FakeMediaRecorder {
    static isTypeSupported() { return true; }
    constructor(stream, options) {
      this.stream = stream;
      this.mimeType = (options && options.mimeType) || 'audio/webm;codecs=opus';
      this.state = 'inactive';
    }
    start() {
      this.state = 'recording';
      stats.started += 1;
    }
    stop() {
      if (this.state === 'inactive') return;
      this.state = 'inactive';
      stats.stopped += 1;
      const blob = new Blob(['case:' + window.__listikVoiceCase], { type: this.mimeType });
      if (this.ondataavailable) this.ondataavailable({ data: blob });
      if (this.onstop) this.onstop();
    }
  }
  window.MediaRecorder = FakeMediaRecorder;
})()
`

/** Состояние панели, модалки «Новая задача» и доски одним вызовом. */
const STATE = `(() => {
  const text = (el) => (el ? el.textContent.replace(/\\s+/g, ' ').trim() : null);
  const trigger = document.querySelector('button[aria-label="Голосовой ввод задачи"]');
  // Открытость берём с триггера: поповер кита закрывается через свой стор и
  // ставит aria-expanded=false, но скрытая headless-страница не завершает
  // transitionend, и снятая панель ещё висит в DOM.
  const expanded = Boolean(trigger) && trigger.getAttribute('aria-expanded') === 'true';
  const panels = [...document.querySelectorAll('.listik-voice__panel')];
  const panel = expanded ? (panels[panels.length - 1] ?? null) : null;
  const buttonText = (el) => el.textContent.replace(/\\s+/g, ' ').trim();
  const drawerNodes = [...document.querySelectorAll('.ui-drawer')].filter(
    (el) => el.querySelector('.ui-drawer__title')?.textContent.includes('Новая задача'),
  );
  // Берём самый свежий узел: закрытые висят в DOM, а новый открывается поверх них.
  const drawerNode = drawerNodes[drawerNodes.length - 1] ?? null;
  // Закрытый дровер в скрытой headless-странице тоже висит в DOM с leave-классами.
  const drawerClosing = Boolean(
    drawerNode &&
      [...drawerNode.classList, ...(drawerNode.closest('.ui-drawer-backdrop')?.classList ?? [])].some(
        (cls) => cls.includes('leave'),
      ),
  );
  const drawer = drawerNode && !drawerClosing ? drawerNode : null;
  const inputs = drawer ? [...drawer.querySelectorAll('input.ui-input')] : [];
  const textareas = drawer ? [...drawer.querySelectorAll('textarea.ui-textarea')] : [];
  return {
    panelOpen: Boolean(panel),
    stage: panel ? panel.getAttribute('data-stage') : null,
    branch: panel ? panel.getAttribute('data-branch') : null,
    title: text(panel && panel.querySelector('.listik-voice__title')),
    timer: text(panel && panel.querySelector('.listik-voice__timer')),
    transcript: text(panel && panel.querySelector('.listik-voice__transcript')),
    draftTitle: text(panel && panel.querySelector('.listik-voice__draft-title')),
    draftDescription: text(panel && panel.querySelector('.listik-voice__draft-description')),
    criteria: panel
      ? [...panel.querySelectorAll('.listik-voice__draft-list li')].map((el) => el.textContent.trim())
      : [],
    warn: text(panel && panel.querySelector('.listik-voice__warn')),
    serverError: text(panel && panel.querySelector('.listik-voice__server-error')),
    createdId: text(panel && panel.querySelector('.listik-voice__created-id')),
    alert: text(panel && panel.querySelector('.ui-alert')),
    buttons: panel
      ? [...panel.querySelectorAll('button')].map((el) => ({ text: buttonText(el), disabled: el.disabled }))
      : [],
    trigger: Boolean(trigger),
    newTask: Boolean([...document.querySelectorAll('button')].find((el) => buttonText(el) === 'Новая задача')),
    modal: Boolean(drawer),
    modalTitle: inputs[0] ? inputs[0].value : null,
    modalSpecPath: inputs[1] ? inputs[1].value : null,
    modalDescription: textareas[0] ? textareas[0].value : null,
    modalAcceptance: textareas[1] ? textareas[1].value : null,
    modalType: drawer
      ? (drawer.querySelector('.listik-icon-toggle[aria-label="Тип задачи"] [role="radio"].is-on')?.getAttribute('aria-label') ?? null)
      : null,
    modalProject: text(drawer && drawer.querySelector('.ui-select__value')),
    modalRoute: drawer
      ? (drawer.querySelector('[role="radiogroup"][aria-label="Маршрут запуска"] [role="radio"].is-on')?.getAttribute('data-route-key') ?? null)
      : null,
    modalAutostart: drawer ? (drawer.querySelector('.ui-checkbox__input')?.checked ?? null) : null,
    cards: document.querySelectorAll('.listik-task-card').length,
  };
})()`

const report = { cases: [], consoleErrors: [] }
const record = (name, run) => recordCase(report, name, run)
let mockNoVoice = null
let mockVoice = null
let staticNoVoice = null
let staticVoice = null
let chrome = null
let voiceApiPort = 0
let socket = null

try {
  if (!existsSync(join(dist, 'index.html'))) {
    throw new Error('нет web/dist — соберите: npm run build')
  }

  const noVoiceApiPort = await freePort()
  const noVoicePagePort = await freePort()
  voiceApiPort = await freePort()
  const voicePagePort = await freePort()

  mockNoVoice = await startMock(noVoiceApiPort, root, ['--routes', '--assistant'])
  staticNoVoice = await serveDist(noVoicePagePort, noVoiceApiPort, dist)
  mockVoice = await startMock(voiceApiPort, root, ['--routes', '--assistant', '--voice'])
  staticVoice = await serveDist(voicePagePort, voiceApiPort, dist)

  const noVoiceUrl = `http://127.0.0.1:${noVoicePagePort}/?token=mock-token`
  const voiceUrl = `http://127.0.0.1:${voicePagePort}/?token=mock-token`

  chrome = startChrome(chromePath, chromePort, profile)
  const session = connect(await cdpTarget(chromePort))
  socket = session.socket
  const { ready, send, evaluate, consoleErrors } = session
  await ready
  await send('Runtime.enable')
  await send('Page.enable')
  await send('Page.addScriptToEvaluateOnNewDocument', { source: STUB })

  /* ── хелперы страницы ─────────────────────────────────────────────────── */

  const state = () => evaluate(STATE)

  const clickTrigger = () => evaluate(`(() => {
    const button = document.querySelector('button[aria-label="Голосовой ввод задачи"]');
    if (!button) return false;
    button.click();
    return true;
  })()`)

  const clickPanelButton = (label) => evaluate(`(() => {
    const trigger = document.querySelector('button[aria-label="Голосовой ввод задачи"]');
    const panels = [...document.querySelectorAll('.listik-voice__panel')];
    const panel = trigger?.getAttribute('aria-expanded') === 'true' ? panels[panels.length - 1] : null;
    if (!panel) return false;
    const button = [...panel.querySelectorAll('button')]
      .find((el) => el.textContent.replace(/\\s+/g, ' ').trim() === ${JSON.stringify(label)});
    if (!button) return false;
    button.click();
    return true;
  })()`)

  const setCase = (name) => evaluate(`(window.__listikVoiceCase = ${JSON.stringify(name)}, true)`)
  const voiceStats = () => evaluate('window.__listikVoiceStats')

  async function apiRequests() {
    const response = await fetch(`http://127.0.0.1:${voiceApiPort}/__requests`)
    return (await response.json()).data
  }

  async function resetRequests() {
    await fetch(`http://127.0.0.1:${voiceApiPort}/__requests`, { method: 'POST' })
  }

  async function dispatchKey(type, key, code, vk) {
    await send('Input.dispatchKeyEvent', {
      type,
      key,
      code,
      windowsVirtualKeyCode: vk,
      nativeVirtualKeyCode: vk,
    })
  }

  const keyDownV = () => dispatchKey('rawKeyDown', 'v', 'KeyV', 86)
  const keyUpV = () => dispatchKey('keyUp', 'v', 'KeyV', 86)
  async function tapKey(key, code, vk) {
    await dispatchKey('rawKeyDown', key, code, vk)
    await dispatchKey('keyUp', key, code, vk)
  }

  const boardReady = () =>
    waitFor(async () => ((await evaluate(`document.querySelectorAll('.listik-task-card').length`)) > 0 ? true : null), 15000)

  /** Закрыть всё открытое (Esc ×2) и дождаться покоя: панель/popover и дровер. */
  async function resetUi() {
    // Задержка выдачи микрофона — только у сценария про отпускание V; гасим её здесь.
    await evaluate('(window.__listikVoiceDelay = 0, true)')
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await tapKey('Escape', 'Escape', 27)
      await sleep(120)
    }
    // Фокус мог остаться в закрытом (но висящем из-за transitionend) дровере.
    await evaluate('document.activeElement && document.activeElement.blur && document.activeElement.blur()')
    const calm = await waitFor(async () => {
      const seen = await state()
      return !seen.panelOpen && !seen.modal ? true : null
    }, 3000)
    if (!calm) throw new Error(`интерфейс не вернулся в покой: ${JSON.stringify(await state())}`)
  }

  /** Клик по кнопке → «Готово» → ждём ветку ошибки. */
  async function goToError() {
    if (!(await clickTrigger())) throw new Error('кнопка «Голосом» не найдена')
    const recording = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 5000)
    if (!recording) throw new Error(`запись не началась: ${JSON.stringify(await state())}`)
    if (!(await clickPanelButton('Готово'))) throw new Error('кнопка «Готово» не найдена')
    const errored = await waitFor(async () => {
      const seen = await state()
      return seen.stage === 'error' ? seen : null
    }, 10000)
    if (!errored) throw new Error(`ветка ошибки не появилась: ${JSON.stringify(await state())}`)
    return errored
  }

  /** Клик → «Готово» → ждём черновик. */
  async function goToDraft() {
    if (!(await clickTrigger())) throw new Error('кнопка «Голосом» не найдена')
    const recording = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 5000)
    if (!recording) throw new Error(`запись не началась: ${JSON.stringify(await state())}`)
    if (!(await clickPanelButton('Готово'))) throw new Error('кнопка «Готово» не найдена')
    const draft = await waitFor(async () => ((await state()).stage === 'draft' ? true : null), 10000)
    if (!draft) throw new Error(`черновик не собрался: ${JSON.stringify(await state())}`)
  }

  /* ── сценарии ──────────────────────────────────────────────────────────── */

  await send('Page.navigate', { url: noVoiceUrl })
  await record('кнопки нет без голоса: «Новая задача» есть, «Голосом» нет', async () => {
    if (!(await boardReady())) throw new Error('доска не отрисовалась на моке без --voice')
    // Статус помощника приходит после монтирования BoardView — даём запросу доехать.
    await sleep(1000)
    const seen = await state()
    return {
      ok: !seen.trigger && seen.newTask,
      expect: 'элемента с aria-label="Голосовой ввод задачи" нет, «Новая задача» на месте',
      got: { trigger: seen.trigger, newTask: seen.newTask },
    }
  })

  await send('Page.navigate', { url: voiceUrl })
  if (!(await boardReady())) throw new Error('доска не отрисовалась на моке с --voice')
  const triggerReady = await waitFor(async () => ((await state()).trigger ? true : null), 10000)
  if (!triggerReady) throw new Error('кнопка «Голосом» не появилась')

  await record('кнопка есть: найден aria-label «Голосовой ввод задачи»', async () => {
    const seen = await state()
    return { ok: seen.trigger, expect: 'кнопка с доступным именем найдена', got: { trigger: seen.trigger } }
  })

  await record('запись: панель «Слушаю» и таймер 0:0X', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    if (!(await clickTrigger())) throw new Error('кнопка «Голосом» не найдена')
    const seen = await waitFor(async () => {
      const current = await state()
      return current.stage === 'recording' && current.timer ? current : null
    }, 5000)
    return {
      ok: Boolean(seen) && seen.title === 'Слушаю' && /^0:0\d$/.test(seen.timer ?? ''),
      expect: 'панель «Слушаю», таймер вида 0:0X',
      got: { stage: seen?.stage ?? null, title: seen?.title ?? null, timer: seen?.timer ?? null },
    }
  })

  await record('черновик: заголовок и описание, «Создать задачу» активна', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    await goToDraft()
    const seen = await state()
    const create = seen.buttons.find((item) => item.text === 'Создать задачу')
    return {
      ok: Boolean(seen.draftTitle) && Boolean(seen.draftDescription) && Boolean(create) && create.disabled === false,
      expect: 'видны заголовок и описание черновика, «Создать задачу» не заблокирована',
      got: { draftTitle: seen.draftTitle, draftDescription: seen.draftDescription, create: create ?? null },
    }
  })

  await record('создание одной кнопкой: voice.create === 1, окно формы не открывалось', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    await goToDraft()
    if (!(await clickPanelButton('Создать задачу'))) throw new Error('кнопка «Создать задачу» не найдена')
    const created = await waitFor(async () => ((await state()).stage === 'created' ? true : null), 10000)
    const data = await apiRequests()
    const body = data.last_create ?? {}
    const seen = await state()
    return {
      ok:
        Boolean(created) &&
        data.voice.create === 1 &&
        Boolean(body.title) &&
        Boolean(body.project) &&
        body.autostart === false &&
        !('holder' in body) &&
        !('stage' in body) &&
        !seen.modal,
      expect: 'одна карточка, last_create с title/project, autostart:false, без holder/stage, форма закрыта',
      got: { created: Boolean(created), counts: data.voice, last_create: body, modal: seen.modal },
    }
  })

  await record('двойной клик: второй клик и клик после успеха не создают вторую задачу', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    await goToDraft()
    await evaluate(`(() => {
      const panels = [...document.querySelectorAll('.listik-voice__panel')];
      const panel = panels[panels.length - 1];
      const button = [...panel.querySelectorAll('button')]
        .find((el) => el.textContent.replace(/\\s+/g, ' ').trim() === 'Создать задачу');
      button.click();
      button.click();
      return true;
    })()`)
    await waitFor(async () => ((await state()).stage === 'created' ? true : null), 10000)
    const afterDouble = (await apiRequests()).voice.create
    const extraClick = await clickPanelButton('Создать задачу')
    await sleep(400)
    const afterExtra = (await apiRequests()).voice.create
    return {
      ok: afterDouble === 1 && afterExtra === 1,
      expect: 'voice.create === 1 и после второго клика, и после клика по уже исчезнувшей кнопке',
      got: { afterDouble, afterExtra, extraClick },
    }
  })

  await record('открыть форму: поля из черновика, маршрут low-pipeline, автостарт выключен', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    await goToDraft()
    const before = await state()
    if (!(await clickPanelButton('Открыть форму'))) throw new Error('кнопка «Открыть форму» не найдена')
    const opened = await waitFor(async () => ((await state()).modal ? true : null), 5000)
    if (!opened) throw new Error(`форма «Новая задача» не открылась: ${JSON.stringify(await state())}`)
    const filled = await state()
    // Смена типа в форме не должна сбрасывать маршрут из черновика.
    await evaluate(`(() => {
      const drawers = [...document.querySelectorAll('.ui-drawer')].filter(
        (el) => el.querySelector('.ui-drawer__title')?.textContent.includes('Новая задача'),
      );
      const drawer = drawers[drawers.length - 1];
      const radio = drawer.querySelector('.listik-icon-toggle[aria-label="Тип задачи"] [role="radio"][aria-label="баг"]');
      if (radio) radio.click();
      return Boolean(radio);
    })()`)
    await sleep(200)
    const after = await state()
    const acceptanceLines = (filled.modalAcceptance ?? '').split(String.fromCharCode(10)).filter(Boolean)
    const expectedCriteria = before.criteria
    return {
      ok:
        filled.modalTitle === before.draftTitle &&
        filled.modalDescription === before.draftDescription &&
        expectedCriteria.length > 0 &&
        acceptanceLines.length === expectedCriteria.length &&
        expectedCriteria.every((item) => acceptanceLines.includes(item)) &&
        filled.modalType === 'задача' &&
        filled.modalProject === 'Listik' &&
        filled.modalRoute === 'low-pipeline' &&
        filled.modalAutostart === false &&
        after.modalRoute === 'low-pipeline',
      expect: 'заполнены заголовок/описание/критерии, тип task, проект listik, маршрут low-pipeline, автостарт выключен; после смены типа маршрут не сброшен',
      got: {
        filled: {
          title: filled.modalTitle,
          description: filled.modalDescription,
          acceptance: acceptanceLines,
          type: filled.modalType,
          project: filled.modalProject,
          route: filled.modalRoute,
          autostart: filled.modalAutostart,
        },
        afterTypeRoute: after.modalRoute,
        expectedCriteria,
        expectedTitle: before.draftTitle,
      },
    }
  })

  await record('клавиатура: KeyV, Enter и Esc', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    await keyDownV()
    const held = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 3000)
    await keyUpV()
    const released = await waitFor(async () => ((await state()).stage === 'draft' ? true : null), 10000)

    await resetUi()
    await setCase('ok')
    await resetRequests()
    await keyDownV()
    const heldEnter = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 3000)
    await tapKey('Enter', 'Enter', 13)
    await keyUpV()
    const entered = await waitFor(async () => ((await state()).stage === 'draft' ? true : null), 10000)

    await resetUi()
    await setCase('ok')
    await resetRequests()
    await keyDownV()
    const heldEsc = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 3000)
    await tapKey('Escape', 'Escape', 27)
    await keyUpV()
    await sleep(400)
    const data = await apiRequests()
    const stats = await voiceStats()
    return {
      ok:
        Boolean(held && released && heldEnter && entered && heldEsc) &&
        data.voice.transcribe === 0 &&
        data.voice.draft === 0 &&
        stats.tracksStopped > 0,
      expect: 'удержание начинает запись, отпускание/Enter её заканчивают, Esc отменяет без запросов и гасит дорожки',
      got: {
        held: Boolean(held),
        released: Boolean(released),
        heldEnter: Boolean(heldEnter),
        entered: Boolean(entered),
        heldEsc: Boolean(heldEsc),
        counts: data.voice,
        tracksStopped: stats.tracksStopped,
      },
    }
  })

  await record('отпускание V до выдачи потока: запись заканчивается сама, дорожка погашена', async () => {
    await resetUi()
    await setCase('ok')
    await resetRequests()
    // Микрофон отдаётся с задержкой — успеваем отпустить V до resolve getUserMedia.
    await evaluate('(window.__listikVoiceDelay = 1500, true)')
    const before = await voiceStats()
    await keyDownV()
    const held = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 3000)
    await keyUpV()
    // Сразу после отпускания поток ещё не выдан: панель обязана остаться «Слушаю»,
    // но не навсегда — как только поток придёт, запись закончится сама.
    const releasedBeforeStream = (await state()).stage
    const finished = await waitFor(async () => {
      const current = await state()
      return current.stage === 'draft' || current.stage === 'created' || current.stage === 'error' ? current : null
    }, 10000)
    await sleep(300)
    const after = await voiceStats()
    await evaluate('(window.__listikVoiceDelay = 0, true)')
    return {
      ok:
        Boolean(held) &&
        releasedBeforeStream === 'recording' &&
        finished?.stage === 'draft' &&
        after.stopped > before.stopped &&
        after.tracksStopped > before.tracksStopped,
      expect: 'после отпускания V до выдачи потока панель сама уходит из «Слушаю» в черновик, MediaRecorder и дорожка остановлены',
      got: {
        held: Boolean(held),
        releasedBeforeStream,
        finishedStage: finished?.stage ?? null,
        before,
        after,
      },
    }
  })

  await record('silence: «Ничего не расслышал», панель закрывается, доска жива', async () => {
    await resetUi()
    await setCase('silence')
    await resetRequests()
    const seen = await goToError()
    const closed = await clickTrigger()
    const gone = await waitFor(async () => (!(await state()).panelOpen ? true : null), 3000)
    const cards = (await state()).cards
    return {
      ok:
        seen.branch === 'silence' &&
        (seen.alert ?? '').includes('Ничего не расслышал') &&
        closed &&
        gone &&
        cards > 0,
      expect: 'ветка silence, панель закрывается кнопкой-триггером, доска отрисована',
      got: { branch: seen.branch, alert: seen.alert, closed, gone, cards },
    }
  })

  await record('transcribe-fail: текст ветки, строка сервера и «Повторить» без новой записи', async () => {
    await resetUi()
    await setCase('transcribe-fail')
    await resetRequests()
    const seen = await goToError()
    const startedBefore = (await voiceStats()).started
    if (!(await clickPanelButton('Повторить'))) throw new Error('кнопка «Повторить» не найдена')
    const second = await waitFor(async () => ((await apiRequests()).voice.transcribe === 2 ? true : null), 10000)
    const startedAfter = (await voiceStats()).started
    return {
      ok:
        seen.branch === 'transcribe' &&
        (seen.alert ?? '').includes('Расшифровка не удалась') &&
        Boolean(seen.serverError) &&
        Boolean(second) &&
        startedAfter === startedBefore,
      expect: 'ветка transcribe, отдельная строка сообщения сервера, «Повторить» шлёт второй transcribe без новой записи',
      got: { branch: seen.branch, alert: seen.alert, serverError: seen.serverError, startedBefore, startedAfter },
    }
  })

  await record('draft-fail: «Открыть форму с текстом» кладёт расшифровку в описание', async () => {
    await resetUi()
    await setCase('draft-fail')
    await resetRequests()
    const seen = await goToError()
    if (!(await clickPanelButton('Открыть форму с текстом'))) throw new Error('кнопка «Открыть форму с текстом» не найдена')
    const opened = await waitFor(async () => ((await state()).modal ? true : null), 5000)
    const modal = await state()
    // Расшифровка детерминирована маркером: мок отвечает «Расшифровка: case:<маркер>».
    const expectedTranscript = 'Расшифровка: case:draft-fail'
    return {
      ok:
        seen.branch === 'draft' &&
        (seen.alert ?? '').includes('Черновик не собрался') &&
        Boolean(opened) &&
        modal.modalDescription === expectedTranscript,
      expect: 'ветка draft, форма открыта, «Описание · ТЗ» равно тексту расшифровки',
      got: { branch: seen.branch, alert: seen.alert, opened: Boolean(opened), description: modal.modalDescription, expectedTranscript },
    }
  })

  await record('denied: «Микрофон не разрешён», ни одного transcribe', async () => {
    await resetUi()
    await setCase('denied')
    await resetRequests()
    if (!(await clickTrigger())) throw new Error('кнопка «Голосом» не найдена')
    const seen = await waitFor(async () => {
      const current = await state()
      return current.stage === 'error' && current.branch === 'mic' ? current : null
    }, 5000)
    await sleep(300)
    const data = await apiRequests()
    return {
      ok: Boolean(seen) && (seen.alert ?? '').includes('Микрофон не разрешён') && data.voice.transcribe === 0,
      expect: 'ветка mic, к серверу не ушло ни одной записи',
      got: { branch: seen?.branch ?? null, alert: seen?.alert ?? null, counts: data.voice },
    }
  })

  await record('noproject: кнопка создания заблокирована, клик не создаёт задачу', async () => {
    await resetUi()
    await setCase('noproject')
    await resetRequests()
    await goToDraft()
    const seen = await state()
    const create = seen.buttons.find((item) => item.text === 'Создать задачу')
    await clickPanelButton('Создать задачу')
    await sleep(300)
    const data = await apiRequests()
    return {
      ok:
        create?.disabled === true &&
        (seen.warn ?? '').includes('проект не распознан — откройте форму') &&
        data.voice.create === 0,
      expect: 'текст «проект не распознан — откройте форму», кнопка disabled, POST /api/tasks не ушёл',
      got: { create: create ?? null, warn: seen.warn, counts: data.voice },
    }
  })

  await record('notitle: кнопка создания заблокирована, клик не создаёт задачу', async () => {
    await resetUi()
    await setCase('notitle')
    await resetRequests()
    await goToDraft()
    const seen = await state()
    const create = seen.buttons.find((item) => item.text === 'Создать задачу')
    await clickPanelButton('Создать задачу')
    await sleep(300)
    const data = await apiRequests()
    return {
      ok:
        create?.disabled === true &&
        (seen.warn ?? '').includes('заголовок не распознан — откройте форму') &&
        data.voice.create === 0,
      expect: 'текст «заголовок не распознан — откройте форму», кнопка disabled, POST /api/tasks не ушёл',
      got: { create: create ?? null, warn: seen.warn, counts: data.voice },
    }
  })

  await record('badproject: неизвестный slug — кнопка заблокирована, клик не создаёт задачу', async () => {
    await resetUi()
    await setCase('badproject')
    await resetRequests()
    await goToDraft()
    const seen = await state()
    const create = seen.buttons.find((item) => item.text === 'Создать задачу')
    await clickPanelButton('Создать задачу')
    await sleep(300)
    const data = await apiRequests()
    return {
      ok:
        create?.disabled === true &&
        (seen.warn ?? '').includes('проект не распознан — откройте форму') &&
        data.voice.create === 0,
      expect: 'текст «проект не распознан — откройте форму», кнопка disabled, POST /api/tasks не ушёл',
      got: { create: create ?? null, warn: seen.warn, counts: data.voice, last_create: data.last_create },
    }
  })

  await record('записать заново: из silence начинается новая запись', async () => {
    await resetUi()
    await setCase('silence')
    await resetRequests()
    await goToError()
    if (!(await clickPanelButton('Записать заново'))) throw new Error('кнопка «Записать заново» не найдена')
    const recording = await waitFor(async () => ((await state()).stage === 'recording' ? true : null), 5000)
    return {
      ok: Boolean(recording),
      expect: 'панель снова «Слушаю»',
      got: { stage: (await state()).stage },
    }
  })

  report.consoleErrors = consoleErrors
  report.cases.push({
    name: 'consoleErrors пуст',
    ok: consoleErrors.length === 0,
    expect: 'ни одного необработанного исключения или console.error',
    got: consoleErrors,
  })

  socket.close()
} catch (error) {
  report.error = String(error?.stack ?? error)
} finally {
  chrome?.kill()
  staticNoVoice?.close()
  staticVoice?.close()
  mockNoVoice?.kill()
  mockVoice?.kill()
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 })
  } catch {
    /* временный профиль уберёт система */
  }
}

report.consoleErrors = report.consoleErrors.length ? report.consoleErrors : []
console.log(JSON.stringify(report, null, 2))
const failed = report.cases.filter((item) => !item.ok).length
if (report.error || failed > 0) process.exitCode = 1
