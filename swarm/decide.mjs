// Решения роя — чистые функции, ни одного вызова наружу (spawn/fs/Date.now()):
// время приходит аргументом `now`, вход/выход — обычные объекты.
const OPEN_STATUSES = new Set(["open", "in_progress", "blocked", "review"]);

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

function isRunning(t) {
  return !!t.launched_by && !t.launch_finished_at;
}

function iconFor(task, routeByKey) {
  const route = routeByKey.get(task.launch_route);
  return route ? (route.icon ?? null) : null;
}

function runningInfo(task, routeByKey) {
  return {id: task.id, route: task.launch_route, worktree: task.worktree, port: portOf(task)};
}

export function decide({plan, tasks, routes, config, now}) {
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
    return {cycles, needsOwner: [], launch: [], running, skipped: [], open, report};
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

  if (running.length) {
    reason = "batch_running";
  } else {
    const wave0 = (plan.waves && plan.waves[0]) || [];
    const candidates = [];
    for (const id of wave0) {
      const t = openById.get(id);
      if (!t) continue;
      if (t.launched_by) continue;
      if (t.needs_owner) continue;
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

  const report = {
    project: config.project, waveSize, wavesLeft,
    running: running.map(t => runningInfo(t, routeByKey)),
    launch: launch.map(l => l.id),
    needsOwner: needsOwner.map(n => ({id: n.id, reason: n.reason})),
    skipped,
    blocked: blockedCount,
    unroutable: plan.unroutable || [], unscoped: plan.unscoped || [],
    reason: reason ?? null,
  };

  return {cycles: [], needsOwner, launch, running, skipped, open, report};
}
