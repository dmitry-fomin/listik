// Точка входа роя. Рой не вызывает модель: каждый тик читает состояние проекта
// заново из Listik субпроцессами `listik … --json` и решает жадно по правилам
// (`decide.mjs`), поэтому не хранит состояния между запусками — падение
// переживается перезапуском, повторный тик ничего не задваивает.
// Без `--exit-when-idle` процесс не завершается, когда запустить нечего:
// спит `--interval` и читает доску снова.
import fs from "node:fs";
import path from "node:path";
import {parseConfig, ConfigError, HelpRequested} from "./config.mjs";
import {isSoftQuestion} from "./decide.mjs";
import {Listik} from "./listik.mjs";
import {tick} from "./run.mjs";
import {open as openLog} from "./log.mjs";

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function configLine(config) {
  const {listikHost, listikPort, ...rest} = config;
  return `конфиг: ${JSON.stringify({...rest, listikHost, listikPort})}`;
}

export function waitingLine(result) {
  const parts = [];
  const report = result.report || {};
  for (const s of report.skipped || []) {
    const label = s.reason === "held" ? "держит другой"
      : s.reason === "frozen" ? "заморожена"
      : s.reason === "gated" ? "гейт"
      : s.reason === "budget" ? "бюджет"
      : "не влезла в партию";
    parts.push(`${s.id} ${label}`);
  }
  if (report.blocked) parts.push(`${report.blocked} стоят за блокерами`);
  return `ждут: ${parts.length ? parts.join(", ") : (result.open || []).join(", ")}`;
}

// п.8: причина вопроса — по последнему комментарию kind=="question". «рой: …» — своя
// короткая причина; иначе воркер сам о чём-то спросил.
export function questionReason(text) {
  if (typeof text !== "string") return "вопрос воркера";
  if (!text.startsWith("рой:")) {
    return isSoftQuestion(text) ? "вопрос воркера, есть дефолт" : "вопрос воркера";
  }
  if (text.includes("бюджет прогона исчерпан")) return "бюджет";
  if (text.includes("нет маршрута")) return "без маршрута";
  if (text.includes("нет write_scope")) return "без области";
  if (text.includes("процесс задачи завершился")) return "упала";
  if (text.includes("свободных портов")) return "нет портов";
  if (text.includes("задача зависла")) return "зависла";
  if (text.includes("не снят")) return "процесс не снят";
  if (text.includes("интеграционные тесты")) return "стоп роя";
  if (text.includes("отклонена")) return "не принята";
  if (text.includes("не влита")) return "не влита";
  if (text.includes("предел откатов")) return "предел откатов";
  return "вопрос воркера";
}

async function reportWaiting(listik, config, log, result) {
  const ids = result.needsOwnerOpen || [];
  for (const id of ids) {
    if (config.dryRun) {
      log.line(`${id} — ждёт человека`);
      continue;
    }
    try {
      const shown = await listik.show(id);
      const questions = (shown.comments || []).filter(c => c.kind === "question");
      const last = questions[questions.length - 1];
      log.line(`${id} — ${questionReason(last && last.text)}`);
    } catch (err) {
      log.line(`${id} — ждёт человека (show ошибка: ${err.code ?? "error"}/${err.message ?? err})`);
    }
  }
  log.line(waitingLine(result));
}

async function runOneTick(listik, config, log, runState) {
  try {
    return await tick(listik, config, log, runState);
  } catch (err) {
    log.line(`ошибка тика: ${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`);
    return {error: true};
  }
}

function budgetBound(n) {
  return n > 0 ? String(n) : "∞";
}

function spentShown(n) {
  return String(Math.round(n * 10) / 10);
}

function readLockPid(lockPath) {
  try {
    const pid = Number(fs.readFileSync(lockPath, "utf8").trim());
    return Number.isInteger(pid) && pid > 0 ? pid : null;
  } catch {
    return null;
  }
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return err.code === "EPERM";
  }
}

// Один рой на имя лока в каталоге логов. Мёртвый pid в файле — чужой прошлый
// прогон, файл забираем. Живой — отказ, чтобы два тика не запустили одну карточку.
function acquireLock(logDir, fileName, label) {
  fs.mkdirSync(logDir, {recursive: true});
  const lockPath = path.join(logDir, fileName);
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const fd = fs.openSync(lockPath, "wx");
      try {
        fs.writeSync(fd, `${process.pid}\n`);
      } finally {
        fs.closeSync(fd);
      }
      return () => {
        try {
          if (readLockPid(lockPath) === process.pid) fs.unlinkSync(lockPath);
        } catch {
          // файл уже снят
        }
      };
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
      const owner = readLockPid(lockPath);
      if (owner != null && pidAlive(owner)) {
        throw new ConfigError(
          `${label} уже запущен (pid ${owner}, ${lockPath})`);
      }
      try {
        fs.unlinkSync(lockPath);
      } catch (unlinkErr) {
        if (unlinkErr.code !== "ENOENT") throw unlinkErr;
      }
    }
  }
  throw new ConfigError(`${label}: не удалось занять ${lockPath}`);
}

export function acquireProjectLock(logDir, project) {
  return acquireLock(logDir, `swarm-${project}.pid`, `рой проекта ${project}`);
}

export function acquireDispatcherLock(logDir) {
  return acquireLock(logDir, "swarm.pid", "рой");
}

const OPEN_FOR_SWARM = new Set(["open", "in_progress", "blocked", "review"]);

// Проекты, которым нужен тик: открытая карточка, вопрос человеку или закрытая,
// чьё дерево ещё не снято. Старые done без дерева рой не трогает.
export function projectsDue(tasks) {
  const slugs = new Set();
  for (const t of tasks || []) {
    if (!t || typeof t.project !== "string" || !t.project) continue;
    if (OPEN_FOR_SWARM.has(t.status) || t.needs_owner || (t.status === "done" && t.worktree)) {
      slugs.add(t.project);
    }
  }
  return [...slugs].sort();
}

function listCut(page) {
  const tasks = (page && page.tasks) || [];
  return typeof page?.total === "number" && page.total > tasks.length;
}

async function dueProjects(listik, log) {
  const status = await listik.status();
  if (!status || status.server !== "up") {
    if (status && status.server === "unauthorized") {
      log.line(`сервер отвечает, но токен CLI не принят — проверь, какой listik и какой ` +
        `каталог данных: ${status.bin_path}, ${status.data_dir}`);
    } else {
      log.line(`сервер: ${status ? status.server : "down"}`);
    }
    return null;
  }
  const open = await listik.listAcross({closed: false});
  const closed = await listik.listAcross({closed: true});
  // Страница закрытых почти всегда короче всей истории: дерево только что
  // закрытой карточки попадает в самые свежие. Обрезанный список открытых —
  // другое: часть живых карточек мы бы не увидели.
  if (listCut(open)) {
    log.line("открытых задач больше страницы — смотрю все проекты");
    const rows = await listik.projects();
    if (!Array.isArray(rows)) return [];
    return rows.map(p => p && p.slug).filter(s => typeof s === "string" && s).sort();
  }
  return projectsDue([...(open && open.tasks || []), ...(closed && closed.tasks || [])]);
}

function scopedLog(log, slug) {
  const withSlug = (text) => `${slug}: ${text}`;
  return {
    path: log.path,
    line: (text) => log.line(withSlug(text)),
    action: (text) => log.action(withSlug(text)),
    summary: (report) => log.summary(report),
    close() {},
  };
}

function cyclesDesc(cycles) {
  return cycles.map(c => [...c, c[0]].join(" → ")).join("; ");
}

// «циклы: …» — когда набор циклов сменился с прошлого тика проекта. `last` — null в
// --once/--dry-run (пишется всегда), иначе {value} с описанием прошлого тика.
export function noteCycles(log, result, last) {
  if (result.error || result.serverDown) return;
  const desc = result.cycles && result.cycles.length ? cyclesDesc(result.cycles) : null;
  if (desc && (!last || last.value !== desc)) log.line(`циклы: ${desc}`);
  if (last) last.value = desc;
}

function projectState(states, slug) {
  let state = states.get(slug);
  if (!state) {
    state = {startedAt: new Date(), launches: 0};
    states.set(slug, state);
  }
  return state;
}

function tickIdle(result) {
  const launchedNone = !result.launched || result.launched.length === 0;
  const restartedNone = !result.restarted || result.restarted.length === 0;
  const runningEmpty = !result.running || result.running.length === 0;
  return launchedNone && restartedNone && runningEmpty;
}

function idleSignature(result) {
  const ids = (list) => (list || []).slice().sort().join(",");
  return `${ids(result.open)}|${ids(result.needsOwnerOpen)}`;
}

async function tickDue(listik, config, log, states, holdProject, lastCycles = null) {
  let slugs;
  try {
    slugs = await dueProjects(listik, log);
  } catch (err) {
    log.line(`ошибка списка проектов: ${err.code ?? "error"}/${err.message ?? err}`);
    return {error: true, results: []};
  }
  if (slugs == null) return {serverDown: true, results: []};
  const results = [];
  for (const slug of slugs) {
    if (!config.dryRun && !holdProject(slug)) continue;
    const state = projectState(states, slug);
    const slugLog = scopedLog(log, slug);
    const result = await runOneTick(listik, {...config, project: slug}, slugLog, state);
    let last = null;
    if (lastCycles) {
      last = lastCycles.get(slug) || {value: null};
      lastCycles.set(slug, last);
    }
    noteCycles(slugLog, result, last);
    state.launches += (result.launched || []).length + (result.restarted || []).length;
    results.push({slug, result});
    if (result.serverDown) return {serverDown: true, results};
  }
  return {results};
}

function onceCode(bundle) {
  if (bundle.error) return 4;
  if (bundle.serverDown) return 3;
  const results = bundle.results || [];
  if (results.some(r => r.result && r.result.error)) return 4;
  if (results.some(r => r.result && r.result.cycles && r.result.cycles.length)) return 1;
  return 0;
}

async function runAllLoop(listik, config, log, states, holdProject, signalExitOf) {
  let lastIdle = null;
  const budgetNoted = new Set();
  const lastCycles = new Map();
  for (;;) {
    const bundle = await tickDue(listik, config, log, states, holdProject, lastCycles);
    if (signalExitOf() != null) return signalExitOf();
    if (bundle.serverDown || bundle.error) {
      await sleep(config.interval * 1000);
      if (signalExitOf() != null) return signalExitOf();
      continue;
    }
    let busy = false;
    const idleParts = [];
    for (const {slug, result} of bundle.results) {
      if (result.error || (result.cycles && result.cycles.length)) {
        busy = true;
        continue;
      }
      const exhausted = result.budget && result.budget.exhausted;
      const idle = tickIdle(result);
      if (exhausted && idle && !budgetNoted.has(slug)) {
        budgetNoted.add(slug);
        log.line(`${slug}: бюджет исчерпан, остальные проекты рой не бросает`);
      }
      if (!idle) {
        busy = true;
        budgetNoted.delete(slug);
      }
      idleParts.push(`${slug}:${(result.open || []).slice().sort().join(",")}`);
    }
    if (!busy) {
      if (config.exitWhenIdle) {
        const anyOpen = bundle.results.some(r => (r.result.open || []).length > 0);
        return anyOpen ? 2 : 0;
      }
      const sig = idleParts.join(";") || "empty";
      if (sig !== lastIdle) {
        lastIdle = sig;
        if (!bundle.results.length) {
          log.line(`жду: запускать нечего, следующий тик через ${config.interval} с`);
        } else {
          log.line(`жду: запустить нечего, проектов ${bundle.results.length}, ` +
            `следующий тик через ${config.interval} с`);
        }
      }
    } else {
      lastIdle = null;
    }
    await sleep(config.interval * 1000);
    if (signalExitOf() != null) return signalExitOf();
  }
}

export async function main(argv) {
  let config;
  try {
    config = parseConfig(argv);
  } catch (err) {
    if (err instanceof HelpRequested) {
      process.stdout.write(err.message);
      return 0;
    }
    if (err instanceof ConfigError) {
      process.stderr.write(`${err.message}\n`);
      return 2;
    }
    throw err;
  }

  const log = openLog(config.logDir, config.project || "all");
  process.stdout.on("error", (err) => {
    if (err.code !== "EPIPE") throw err;
  });
  process.stdout.write(`${log.path}\n`);
  log.line(configLine(config));

  let releaseLock = () => {};
  const extraReleases = [];
  if (!config.dryRun) {
    try {
      releaseLock = config.allProjects
        ? acquireDispatcherLock(config.logDir)
        : acquireProjectLock(config.logDir, config.project);
    } catch (err) {
      if (err instanceof ConfigError) {
        process.stderr.write(`${err.message}\n`);
        log.line(err.message);
        return 2;
      }
      throw err;
    }
  }
  const heldProjects = new Set();
  const skippedLocks = new Set();
  const holdProject = (slug) => {
    if (heldProjects.has(slug)) return true;
    try {
      extraReleases.push(acquireProjectLock(config.logDir, slug));
      heldProjects.add(slug);
      skippedLocks.delete(slug);
      return true;
    } catch (err) {
      if (err instanceof ConfigError) {
        if (!skippedLocks.has(slug)) {
          skippedLocks.add(slug);
          log.line(err.message);
        }
        return false;
      }
      throw err;
    }
  };
  const releaseAll = () => {
    for (const release of extraReleases.splice(0)) {
      try {
        release();
      } catch {
        // файл уже снят
      }
    }
    releaseLock();
  };

  const listik = new Listik({
    bin: config.listikBin,
    host: config.listikHost,
    port: config.listikPort,
    actor: config.actor,
    cliTimeout: config.cliTimeout,
  });

  let signalExit = null;
  const onSignal = (name, code) => () => {
    log.line(`остановлен сигналом ${name}`);
    signalExit = code;
  };
  process.once("SIGINT", onSignal("SIGINT", 130));
  process.once("SIGTERM", onSignal("SIGTERM", 143));
  const onHangup = () => {
    log.line("SIGHUP — терминал закрыт, рой продолжает");
  };
  process.on("SIGHUP", onHangup);

  const runState = {startedAt: new Date(), launches: 0};

  try {
  if (config.allProjects) {
    const states = new Map();
    if (config.once || config.dryRun) {
      const bundle = await tickDue(listik, config, log, states, holdProject);
      if (signalExit != null) return signalExit;
      return onceCode(bundle);
    }
    return await runAllLoop(listik, config, log, states, holdProject, () => signalExit);
  }

  if (config.once || config.dryRun) {
    const result = await runOneTick(listik, config, log, runState);
    noteCycles(log, result, null);
    if (signalExit != null) return signalExit;
    if (result.error) return 4;
    if (result.serverDown) return 3;
    if (result.cycles && result.cycles.length) return 1;
    return 0;
  }

  let firstOpen = null;
  let totalRestarts = 0;
  let totalRollbacks = 0;
  let totalRollbackMinutes = 0;
  let lastIdle = null;
  const parkedIds = new Set();
  const lastCycles = {value: null};

  for (;;) {
    const result = await runOneTick(listik, config, log, runState);
    noteCycles(log, result, lastCycles);
    if (signalExit != null) return signalExit;

    if (result.serverDown || result.error || (result.cycles && result.cycles.length)) {
      await sleep(config.interval * 1000);
      if (signalExit != null) return signalExit;
      continue;
    }

    if (firstOpen == null) firstOpen = result.open || [];
    totalRestarts += (result.restarted || []).length;
    const tickRollbacks = result.rollbacks || [];
    totalRollbacks += tickRollbacks.length;
    totalRollbackMinutes += tickRollbacks.reduce((sum, r) => sum + (r.minutes || 0), 0);
    for (const r of tickRollbacks) {
      if (r.parked) parkedIds.add(r.id);
    }
    runState.launches += (result.launched || []).length + (result.restarted || []).length;

    const launchedNone = !result.launched || result.launched.length === 0;
    const restartedNone = !result.restarted || result.restarted.length === 0;
    const runningEmpty = !result.running || result.running.length === 0;
    const exhausted = result.budget && result.budget.exhausted;

    if (exhausted && runningEmpty) {
      const openList = result.open || [];
      if (openList.length === 0) return 0;
      await reportWaiting(listik, config, log, result);
      const lastOpen = new Set(openList);
      const closed = firstOpen.filter(id => !lastOpen.has(id));
      const leftToHuman = (result.needsOwnerOpen || []).length;
      const spent = result.budget.spentMinutes ?? 0;
      const launches = result.budget.launches ?? runState.launches;
      log.line(`итог: бюджет исчерпан — минут ${spentShown(spent)} из ${budgetBound(config.budgetMinutes)}, ` +
        `запусков ${launches} из ${budgetBound(config.maxLaunches)}, ` +
        `закрыто ${closed.length} (${closed.join(", ")}), оставлено человеку ${leftToHuman}`);
      return 5;
    }

    if (launchedNone && restartedNone && runningEmpty) {
      const openList = result.open || [];
      if (config.exitWhenIdle) {
        if (openList.length === 0) return 0;
        await reportWaiting(listik, config, log, result);
        const lastOpen = new Set(openList);
        const closed = firstOpen.filter(id => !lastOpen.has(id));
        const leftToHuman = (result.needsOwnerOpen || []).length;
        log.line(`итог: закрыто ${closed.length} (${closed.join(", ")}), ` +
          `перезапусков ${totalRestarts}, откатов ${totalRollbacks} ` +
          `(на откаты ${totalRollbackMinutes} мин), по пределу ${parkedIds.size} ` +
          `(${[...parkedIds].join(", ")}), оставлено человеку ${leftToHuman}`);
        return 2;
      }
      const sig = idleSignature(result);
      if (sig !== lastIdle) {
        lastIdle = sig;
        if (openList.length === 0) {
          log.line(`жду: открытых задач нет, следующий тик через ${config.interval} с`);
        } else {
          await reportWaiting(listik, config, log, result);
          log.line(`жду: запустить нечего, открыто ${openList.length}, ` +
            `следующий тик через ${config.interval} с`);
        }
      }
      await sleep(config.interval * 1000);
      if (signalExit != null) return signalExit;
      continue;
    }
    lastIdle = null;

    await sleep(config.interval * 1000);
    if (signalExit != null) return signalExit;
  }
  } finally {
    process.removeListener("SIGHUP", onHangup);
    releaseAll();
  }
}
