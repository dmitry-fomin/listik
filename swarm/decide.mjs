// Решения роя — чистые функции, ни одного вызова наружу (spawn/fs/Date.now()):
// время приходит аргументом `now`, вход/выход — обычные объекты.
export const OPEN_STATUSES = new Set(["open", "in_progress", "blocked", "review"]);

const TEXT_UNROUTABLE = "рой: у задачи нет маршрута (launch_route) — каким маршрутом её делать? " +
  "Рой маршрут не выбирает никогда.";
const textUnscoped = (id) => "рой: у задачи нет write_scope — какие файлы и каталоги она правит? " +
  `Без области записи планировщик не ставит её в волну: listik set ${id} write_scope=<пути через запятую>.`;

export function portOf(task) {
  const labels = task.labels || [];
  for (const label of labels) {
    if (typeof label === "string" && label.startsWith("port:")) {
      const n = Number(label.slice("port:".length));
      return Number.isFinite(n) ? n : null;
    }
  }
  return null;
}

export function allocatePort(tasks, task, base, count) {
  const existing = portOf(task);
  if (existing != null) return existing;
  const used = new Set();
  for (const t of tasks) {
    if (!OPEN_STATUSES.has(t.status)) continue;
    const p = portOf(t);
    if (p != null) used.add(p);
  }
  for (let p = base; p < base + count; p++) {
    if (!used.has(p)) return p;
  }
  return null;
}

export function isRunning(t) {
  return !!t.launched_by && !t.launch_finished_at;
}

// Заморозка барьера (порции b–d): метка `frozen-by:<x>` на задаче. Первая найденная.
export function isFrozen(task) {
  const labels = task.labels || [];
  for (const label of labels) {
    if (typeof label === "string" && label.startsWith("frozen-by:")) {
      return label.slice("frozen-by:".length);
    }
  }
  return null;
}

function iconFor(task, routeByKey) {
  const route = routeByKey.get(task.launch_route);
  return route ? (route.icon ?? null) : null;
}

function runningInfo(task, routeByKey) {
  return {id: task.id, route: task.launch_route, worktree: task.worktree, port: portOf(task)};
}

// Надзор (порция c): по одному и тому же актору «свой» и «чужой» revoke различаются
// только нормализованным написанием — здесь достаточно нижнего регистра и схлопнутых
// пробелов (полный alias-разбор — `listik/actors.py`, недоступен процессу роя).
const REVOKE_RESTART_PREFIX = "рой: перезапуск —";

function normActor(raw) {
  return (raw || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function tsMs(v) {
  if (!v) return null;
  const n = Date.parse(v);
  return Number.isFinite(n) ? n : null;
}

function minutesSince(now, thenMs) {
  return (now.getTime() - thenMs) / 60000;
}

function fmtMin(n) {
  return String(Math.round(n * 10) / 10);
}

function lastActivityMs(task, taskEvents, actorNorm, launchedAtMs) {
  const times = [];
  if (launchedAtMs != null) times.push(launchedAtMs);
  const holderAtMs = tsMs(task.holder_at);
  if (holderAtMs != null) times.push(holderAtMs);
  for (const ev of taskEvents || []) {
    const evTs = tsMs(ev.ts);
    if (evTs == null) continue;
    if (launchedAtMs != null && !(evTs > launchedAtMs)) continue;
    if (normActor(ev.actor) === actorNorm) continue;
    times.push(evTs);
  }
  return times.length ? Math.max(...times) : null;
}

function restartCount(taskEvents, actorNorm) {
  let n = 0;
  for (const ev of taskEvents || []) {
    if (ev.kind !== "revoke") continue;
    if (normActor(ev.actor) !== actorNorm) continue;
    if (typeof ev.note === "string" && ev.note.startsWith(REVOKE_RESTART_PREFIX)) n++;
  }
  return n;
}

function noPortText(id) {
  return "рой: задача зависла, но перезапустить нечем — свободных портов в диапазоне нет; " +
    "процесс снят. Освободи порты (закрой задачи или расширь --port-count), потом: " +
    `listik release ${id}; listik needs-owner ${id} --clear "…"`;
}

function giveUpLimitText(id, reason, staleMinutes, timeoutMinutes, silenceMin, runMin, restarts, launchLog) {
  const cause = reason === "timeout"
    ? `бежит дольше ${fmtMin(runMin)} мин`
    : `нет активности ${fmtMin(silenceMin)} мин`;
  return `рой: задача зависла (${cause}) после ${restarts} перезапусков — процесс снят, больше ` +
    `не перезапускаю. Разбери лог ${launchLog ?? ""}; чтобы рой взял её снова: ` +
    `listik release ${id} (держатель остаётся после отзыва), затем listik needs-owner ${id} ` +
    `--clear "…" — тогда она вернётся в партию.`;
}

function crashedText(id, exitCode, generation, launchLog) {
  const code = exitCode == null ? "неизвестен" : exitCode;
  return `рой: процесс задачи завершился (код ${code}, поколение ${generation}), а карточка не ` +
    `закрыта — лог ${launchLog ?? ""}. Разбери и сними флаг (listik needs-owner ${id} --clear ` +
    `"…"), тогда рой перезапустит её новым поколением.`;
}

function superviseRunning({running, open, openById, events, tasks, config, now}) {
  const actorNorm = normActor(config.actor);
  const staleMinutes = config.staleMinutes ?? 20;
  const timeoutMinutes = config.timeoutMinutes ?? 0;
  const maxRestarts = config.maxRestarts ?? 1;

  const restart = [];
  const giveUp = [];
  const stopOnly = [];
  const stale = [];
  const skipped = [];
  const silence = [];

  for (const t of running) {
    const taskEvents = (events || {})[t.id] || [];
    const launchedAtMs = tsMs(t.launched_at);

    if (!openById.has(t.id)) {
      // Закрытая бегущая (п.4): только timeout, никогда stale, никогда restart/giveUp.
      if (launchedAtMs == null) continue;
      const runMin = minutesSince(now, launchedAtMs);
      if (timeoutMinutes > 0 && runMin > timeoutMinutes) {
        stopOnly.push({id: t.id, reason: "timeout"});
      }
      continue;
    }

    if (t.needs_owner) continue;
    if (launchedAtMs == null) {
      skipped.push({id: t.id, reason: "no_launched_at"});
      continue;
    }

    const lastAct = lastActivityMs(t, taskEvents, actorNorm, launchedAtMs);
    const silenceMin = minutesSince(now, lastAct);
    const runMin = minutesSince(now, launchedAtMs);
    if (silenceMin > staleMinutes * 3 / 4) {
      silence.push({id: t.id, minutes: Math.round(silenceMin * 10) / 10});
    }
    const isStale = silenceMin > staleMinutes;
    const isTimeout = timeoutMinutes > 0 && runMin > timeoutMinutes;
    if (isStale) stale.push(t.id);
    if (!isStale && !isTimeout) continue;

    const reason = isTimeout ? "timeout" : "stale";
    const restarts = restartCount(taskEvents, actorNorm);
    const port = portOf(t) ?? allocatePort(tasks, t, config.portBase, config.portCount);
    if (port == null) {
      giveUp.push({id: t.id, reason: "no_port", restarts, text: noPortText(t.id)});
      continue;
    }
    if (restarts < maxRestarts) {
      restart.push({id: t.id, reason, restarts, generation: t.generation, port});
    } else {
      const text = giveUpLimitText(t.id, reason, staleMinutes, timeoutMinutes, silenceMin, runMin,
        restarts, t.launch_log);
      giveUp.push({id: t.id, reason, restarts, text});
    }
  }

  return {restart, giveUp, stopOnly, stale, skipped, silence};
}

function superviseCrashed({open, events, tasks, config}) {
  const actorNorm = normActor(config.actor);
  const restart = [];
  const giveUp = [];
  const crashed = [];

  for (const t of open) {
    if (!t.launched_by || !t.launch_finished_at || t.needs_owner) continue;
    const taskEvents = (events || {})[t.id] || [];
    const finishedMs = tsMs(t.launch_finished_at);
    const answered = taskEvents.some(ev => {
      if (ev.kind !== "answer") return false;
      const evTs = tsMs(ev.ts);
      return evTs != null && finishedMs != null && evTs > finishedMs;
    });

    if (answered) {
      const restarts = restartCount(taskEvents, actorNorm);
      const port = portOf(t) ?? allocatePort(tasks, t, config.portBase, config.portCount);
      if (port == null) {
        giveUp.push({id: t.id, reason: "no_port", restarts, text: noPortText(t.id)});
      } else {
        restart.push({id: t.id, reason: "answered", restarts, generation: t.generation, port});
      }
    } else {
      crashed.push({
        id: t.id, exitCode: t.launch_exit_code ?? null, log: t.launch_log ?? null,
        text: crashedText(t.id, t.launch_exit_code, t.generation, t.launch_log),
      });
    }
  }

  return {restart, giveUp, crashed};
}

export function decide({plan, tasks, routes, config, now, events, gate = null}) {
  const open = tasks.filter(t => OPEN_STATUSES.has(t.status));
  const openById = new Map(open.map(t => [t.id, t]));
  const routeByKey = new Map((routes || []).map(r => [r.key, r]));
  const running = tasks.filter(isRunning);
  const cycles = plan.cycles || [];
  const waveSize = ((plan.waves && plan.waves[0]) || []).length;
  const wavesLeft = Math.max(((plan.waves || []).length) - 1, 0);
  const blockedCount = Object.keys(plan.blocked || {}).length;

  if (cycles.length) {
    const report = {
      project: config.project, waveSize, wavesLeft,
      running: running.map(t => runningInfo(t, routeByKey)),
      launch: [], needsOwner: [], skipped: [],
      blocked: blockedCount,
      unroutable: plan.unroutable || [], unscoped: plan.unscoped || [],
      reason: "cycle",
    };
    return {
      cycles, needsOwner: [], launch: [], running, skipped: [], open, report,
      restart: [], giveUp: [], crashed: [], stopOnly: [],
    };
  }

  const needsOwner = [];
  for (const id of plan.unroutable || []) {
    const t = openById.get(id);
    if (t && !t.needs_owner) {
      needsOwner.push({id, reason: "unroutable", text: TEXT_UNROUTABLE});
    }
  }
  for (const id of plan.unscoped || []) {
    const t = openById.get(id);
    if (t && !t.needs_owner) {
      needsOwner.push({id, reason: "unscoped", text: textUnscoped(id)});
    }
  }

  const skipped = [];
  let launch = [];
  let reason;

  if (gate != null) {
    const wave0 = (plan.waves && plan.waves[0]) || [];
    for (const id of wave0) {
      const t = openById.get(id);
      if (!t) continue;
      if (t.launched_by) continue;
      if (t.needs_owner) continue;
      const frozenBy = isFrozen(t);
      if (frozenBy) {
        skipped.push({id, reason: "frozen"});
        continue;
      }
      skipped.push({id, reason: "gated"});
    }
    reason = gate.reason;
  } else if (running.length) {
    reason = "batch_running";
  } else {
    const wave0 = (plan.waves && plan.waves[0]) || [];
    const candidates = [];
    for (const id of wave0) {
      const t = openById.get(id);
      if (!t) continue;
      if (t.launched_by) continue;
      if (t.needs_owner) continue;
      const frozenBy = isFrozen(t);
      if (frozenBy) {
        skipped.push({id, reason: "frozen"});
        continue;
      }
      if (t.holder) {
        skipped.push({id, reason: "held"});
        continue;
      }
      candidates.push(t);
    }

    let capacity = config.parallel;
    const capacitySkipped = [];
    for (const t of candidates) {
      const weight = config.weights[iconFor(t, routeByKey)] ?? 1;
      if (weight <= capacity) {
        launch.push({id: t.id, route: t.launch_route, weight});
        capacity -= weight;
      } else {
        capacitySkipped.push({id: t.id, reason: "capacity"});
      }
    }
    if (launch.length === 0 && candidates.length > 0) {
      const first = candidates[0];
      const weight = config.weights[iconFor(first, routeByKey)] ?? 1;
      if (weight > config.parallel) {
        launch = [{id: first.id, route: first.launch_route, weight}];
        reason = "oversized";
        const idx = capacitySkipped.findIndex(s => s.id === first.id);
        if (idx >= 0) capacitySkipped.splice(idx, 1);
      }
    }
    skipped.push(...capacitySkipped);
  }

  const runningSup = superviseRunning({running, open, openById, events, tasks, config, now});
  const crashedSup = superviseCrashed({open, events, tasks, config});
  const restart = [...runningSup.restart, ...crashedSup.restart];
  const giveUp = [...runningSup.giveUp, ...crashedSup.giveUp];
  const crashed = crashedSup.crashed;
  const stopOnly = runningSup.stopOnly;
  skipped.push(...runningSup.skipped);

  const report = {
    project: config.project, waveSize, wavesLeft,
    running: running.map(t => runningInfo(t, routeByKey)),
    launch: launch.map(l => l.id),
    needsOwner: needsOwner.map(n => ({id: n.id, reason: n.reason})),
    skipped,
    blocked: blockedCount,
    unroutable: plan.unroutable || [], unscoped: plan.unscoped || [],
    reason: reason ?? null,
    gate: gate ?? null,
    restart: restart.map(r => r.id),
    giveUp: giveUp.map(g => g.id),
    crashed: crashed.map(c => c.id),
    stopOnly: stopOnly.map(s => s.id),
    stale: runningSup.stale,
    silence: runningSup.silence,
  };

  return {cycles: [], needsOwner, launch, running, skipped, open, report, restart, giveUp, crashed, stopOnly};
}
