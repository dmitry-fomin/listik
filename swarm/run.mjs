// Один тик: собрать вход из Listik (субпроцессами), решить (decide), выполнить
// действия, свести итог. Без состояния между тиками — всё читается заново.
import {decide, portOf, allocatePort} from "./decide.mjs";

const MAIN_WORKTREE_MARKERS = new Set(["main", "master"]);

function errText(err) {
  return `${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`;
}

export async function tick(listik, config, log) {
  const status = await listik.status();
  if (status.server !== "up") {
    if (status.server === "unauthorized") {
      log.line(`сервер отвечает, но токен CLI не принят — проверь, какой listik и какой ` +
        `каталог данных: ${status.bin_path}, ${status.data_dir}`);
    } else {
      log.line(`сервер: ${status.server}`);
    }
    return {serverDown: true, server: status.server, cycles: []};
  }
  log.line(`сервер: bin_path=${status.bin_path} data_dir=${status.data_dir} ` +
    `db_path=${status.db_path} url=${status.url}`);

  const plan = await listik.waves(config.project, {apply: !config.dryRun});
  if (plan.applyResult) {
    const {added, removed, kept} = plan.applyResult;
    log.action(`waves --apply: записано ${added.length} рёбер ` +
      `(${added.map(([a, b]) => `${a} → ${b}`).join(", ")}), снято ${removed.length} ` +
      `(${removed.map(([a, b]) => `${a} → ${b}`).join(", ")}), без изменений ${kept}`);
  }
  const listRes = await listik.list(config.project);
  const tasks = listRes.tasks || [];
  const routesRes = await listik.routes();
  const routes = routesRes.routes || [];

  const decision = decide({plan, tasks, routes, config, now: new Date()});

  if (decision.cycles.length) {
    const desc = decision.cycles.map(c => [...c, c[0]].join(" → ")).join("; ");
    log.line(`циклы: ${desc}`);
    return {serverDown: false, server: status.server, cycles: decision.cycles};
  }

  const taskById = new Map(tasks.map(t => [t.id, t]));

  const needsOwnerDone = [];
  for (const item of decision.needsOwner) {
    if (config.dryRun) {
      log.action(`[dry-run] needs-owner ${item.id}: ${item.text}`);
      continue;
    }
    try {
      await listik.needsOwner(item.id, item.text);
      log.action(`needs-owner ${item.id}: ${item.reason}`);
      needsOwnerDone.push(item.id);
      const t = taskById.get(item.id);
      if (t) t.needs_owner = true;
    } catch (err) {
      log.line(`needs-owner ${item.id} ошибка: ${errText(err)}`);
    }
  }

  const launched = [];
  for (const item of decision.launch) {
    const task = taskById.get(item.id);
    if (!task) continue;

    const marker = (task.worktree || "").trim().toLowerCase();
    if (MAIN_WORKTREE_MARKERS.has(marker)) {
      if (config.dryRun) log.action(`[dry-run] worktree ${item.id}: пропущен, дерево main/master`);
    } else if (config.dryRun) {
      log.action(`[dry-run] worktree ${item.id}`);
    } else {
      try {
        const wt = await listik.worktree(item.id);
        task.worktree = wt.path;
        log.action(`worktree ${item.id} → ${wt.path} (${wt.status})`);
      } catch (err) {
        log.line(`worktree ${item.id} ошибка: ${errText(err)}`);
        if (!task.needs_owner) {
          try {
            await listik.needsOwner(item.id, `рой: не удалось завести рабочее дерево: ${err.message}`);
            log.action(`needs-owner ${item.id}: worktree_failed`);
            needsOwnerDone.push(item.id);
            task.needs_owner = true;
          } catch (err2) {
            log.line(`needs-owner ${item.id} ошибка: ${errText(err2)}`);
          }
        }
        continue;
      }
    }

    const port = allocatePort(tasks, task, config.portBase, config.portCount);
    if (port == null) {
      log.line(`порты кончились: ${item.id}`);
      continue;
    }

    if (portOf(task) == null) {
      if (config.dryRun) {
        log.action(`[dry-run] set ${item.id} labels += port:${port}`);
        task.labels = [...(task.labels || []), `port:${port}`];
      } else {
        const fresh = await listik.show(item.id);
        const labels = [...(fresh.labels || []), `port:${port}`];
        await listik.setLabels(item.id, labels);
        task.labels = labels;
        log.action(`set ${item.id} labels += port:${port}`);
      }
    }

    if (config.dryRun) {
      log.action(`[dry-run] launch ${item.id} → дерево ${task.worktree ?? "-"}, порт ${port}`);
      launched.push(item.id);
      continue;
    }

    try {
      const result = await listik.launch(item.id, {LISTIK_DEV_PORT: String(port)});
      log.action(`запуск ${item.id} → дерево ${task.worktree ?? "-"}, порт ${port}, ` +
        `поколение ${result.generation}`);
      launched.push(item.id);
      task.launched_by = config.actor;
    } catch (err) {
      if (typeof err.message === "string" && err.message.includes("уже запущена")) {
        log.action(`уже запущена (параллельный рой?): ${item.id}`);
      } else {
        log.line(`launch ${item.id} ошибка: ${errText(err)}`);
      }
    }
  }

  const report = {
    ...decision.report,
    launch: launched,
    needsOwner: decision.report.needsOwner.filter(n => needsOwnerDone.includes(n.id)),
    skipped: decision.report.skipped.map(s =>
      s.reason === "held" ? {...s, holder: (taskById.get(s.id) || {}).holder} : s),
  };
  log.summary(report);

  return {
    serverDown: false,
    server: status.server,
    cycles: [],
    launched,
    needsOwner: needsOwnerDone,
    running: decision.running.map(t => t.id),
    open: decision.open.map(t => t.id),
    report,
  };
}
