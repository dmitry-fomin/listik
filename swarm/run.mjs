// Один тик: собрать вход из Listik (субпроцессами), решить (decide), выполнить
// действия, свести итог. Без состояния между тиками — всё читается заново.
import path from "node:path";
import fs from "node:fs";
import {decide, portOf, allocatePort, isRunning, OPEN_STATUSES} from "./decide.mjs";
import {runBarrier, haltCards} from "./barrier.mjs";
import {ConfigError, parseSwarmConfig, swarmConfigFor} from "./config.mjs";
import * as git from "./git.mjs";

const MAIN_WORKTREE_MARKERS = new Set(["main", "master"]);

function errText(err) {
  return `${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`;
}

// Карточки, за которыми тик обязан посмотреть `show` перед `decide` (п.6): бегущие
// открытые и упавшие открытые, у которых `needs_owner` ложен. Закрытые и карточки
// с поднятым флагом «нужен ты» — не смотрим (свежих событий для решения не нужно).
function needsEventsFetch(t) {
  if (!OPEN_STATUSES.has(t.status) || t.needs_owner) return false;
  if (isRunning(t)) return true;
  return !!t.launched_by && !!t.launch_finished_at;
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
  let tasks = listRes.tasks || [];
  const routesRes = await listik.routes();
  const routes = routesRes.routes || [];

  const events = {};
  for (const t of tasks) {
    if (!needsEventsFetch(t)) continue;
    try {
      const shown = await listik.show(t.id);
      events[t.id] = shown.events || [];
    } catch (err) {
      log.line(`show ${t.id} ошибка: ${errText(err)}`);
    }
  }

  let gate = null;
  let barrierResult = null;
  let watchSummary = null;
  const cyclesPending = !!(plan.cycles && plan.cycles.length);

  if (!cyclesPending) {
    const projectsRes = await listik.projects();
    const projectEntry = (projectsRes || []).find(p => p.slug === config.project);
    const projectPath = projectEntry && projectEntry.path ? projectEntry.path : null;

    if (!projectPath) {
      log.line("у проекта нет каталога — барьер не работает");
    } else {
      // Наблюдатель — всегда, независимо от running/halt/конфига барьера.
      let watchRes = null;
      try {
        watchRes = config.dryRun
          ? await listik.watch(config.project, {dryRun: true})
          : await listik.watch(config.project);
      } catch (err) {
        log.line(`watch ошибка: ${errText(err)}`);
        watchSummary = {ok: false, frozen: [], errors: [], probes: 0, truncated: false};
      }

      if (watchRes) {
        const tasksObj = watchRes.tasks || {};
        const decisions = watchRes.decisions || [];
        const probes = watchRes.probes || [];
        const N = Object.keys(tasksObj).length;
        const M = Object.values(tasksObj).filter(t => t && t.live).length;
        const P = probes.length;
        const K = decisions.filter(d => d.action === "freeze" && d.ok !== false).length;
        const E = decisions.filter(d => d.ok === false).length;
        log.line(`watch: задач ${N}, живых ${M}, проб ${P}, заморожено ${K}, ошибок ${E}`);
        for (const d of decisions) {
          if (d.action === "report") {
            log.line(`watch: только отчёт: ${d.task} не в работе, конфликт с ${d.owner}`);
            continue;
          }
          if (d.action === "freeze") {
            if (d.ok === false) {
              log.line(`watch: ошибка заморозки ${d.task}: ${d.error}`);
              continue;
            }
            const files = (d.files || []).join(", ");
            if (d.dry_run) log.line(`watch: заморозила бы ${d.task} (владелец ${d.owner}): ${files}`);
            else if (d.resumed) log.line(`watch: заморозка доделана ${d.task} (владелец ${d.owner}): ${files}`);
            else log.line(`watch: заморожена ${d.task} (владелец ${d.owner}): ${files}`);
          }
        }
        if (watchRes.truncated) {
          log.line("watch: задач больше 1000, активное множество неполное");
        }

        watchSummary = {
          ok: true,
          frozen: decisions.filter(d => d.action === "freeze").map(d => d.task),
          errors: decisions.filter(d => d.ok === false).map(d => d.task),
          probes: P,
          truncated: !!watchRes.truncated,
        };

        if (!config.dryRun && decisions.some(d => d.action === "freeze" && d.ok !== false)) {
          const relist = await listik.list(config.project);
          tasks = relist.tasks || [];
        }
      }

      let swarmConfig = null;
      let configErrored = false;
      try {
        const cfgPath = config.configPath ?? path.join(status.data_dir, "swarm.json");
        let text = null;
        try {
          text = fs.readFileSync(cfgPath, "utf8");
        } catch (err) {
          if (err.code !== "ENOENT") throw err;
          text = null;
        }
        const parsed = parseSwarmConfig(text);
        swarmConfig = swarmConfigFor(parsed, config.project);
      } catch (err) {
        if (err instanceof ConfigError) {
          log.line(err.message);
          gate = {reason: "config", ids: []};
          configErrored = true;
        } else {
          throw err;
        }
      }

      const runningNow = tasks.filter(isRunning);
      if (runningNow.length) {
        const haltIds = haltCards(tasks);
        if (haltIds.length) gate = {reason: "halt", ids: haltIds};
      } else if (!configErrored) {
        barrierResult = await runBarrier({
          listik, git, fs, config, swarmConfig, log, tasks, projectPath, now: new Date(),
        });
        gate = barrierResult.gate;
      }
    }
  }

  const decision = decide({plan, tasks, routes, config, now: new Date(), events, gate});

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

  // п.6.2: упавшие открытые без разрешения человека — needs-owner, revoke/launch не зовутся.
  for (const item of decision.crashed) {
    if (config.dryRun) {
      log.action(`[dry-run] needs-owner ${item.id}: ${item.text}`);
      continue;
    }
    try {
      await listik.needsOwner(item.id, item.text);
      log.action(`needs-owner ${item.id}: crashed`);
      needsOwnerDone.push(item.id);
      const t = taskById.get(item.id);
      if (t) t.needs_owner = true;
    } catch (err) {
      log.line(`needs-owner ${item.id} ошибка: ${errText(err)}`);
    }
  }

  // п.6.3: закрытые бегущие дольше timeoutMinutes — только снять процесс.
  const stoppedClosed = [];
  for (const item of decision.stopOnly) {
    const note = `рой: процесс закрытой задачи бежит дольше ${config.timeoutMinutes} мин — снят`;
    if (config.dryRun) {
      log.action(`[dry-run] revoke ${item.id}: ${note}`);
      continue;
    }
    try {
      await listik.revoke(item.id, note);
      log.action(`revoke ${item.id}: закрытая, таймаут`);
      stoppedClosed.push(item.id);
    } catch (err) {
      log.line(`revoke ${item.id} ошибка: ${errText(err)}`);
    }
  }

  // п.6.4: предел перезапусков (или нет свободных портов) — снять процесс и поставить флаг.
  for (const item of decision.giveUp) {
    if (config.dryRun) {
      log.action(`[dry-run] revoke ${item.id}: рой: ${item.reason}, предел перезапусков`);
      log.action(`[dry-run] needs-owner ${item.id}: ${item.text}`);
      continue;
    }
    try {
      await listik.revoke(item.id, `рой: ${item.reason}, предел перезапусков`);
      log.action(`revoke ${item.id}: предел перезапусков (${item.reason})`);
    } catch (err) {
      log.line(`revoke ${item.id} ошибка: ${errText(err)}`);
    }
    try {
      await listik.needsOwner(item.id, item.text);
      log.action(`needs-owner ${item.id}: give_up`);
      needsOwnerDone.push(item.id);
      const t = taskById.get(item.id);
      if (t) t.needs_owner = true;
    } catch (err) {
      log.line(`needs-owner ${item.id} ошибка: ${errText(err)}`);
    }
  }

  // п.6.5: зависшие/просроченные/разрешённые человеком — revoke, затем launch тем же
  // маршрутом (тот же актор перехватывает claim предшественника), если процесс подтверждённо снят.
  const restarted = [];
  for (const item of decision.restart) {
    const note = item.reason === "answered"
      ? "рой: перезапуск разрешён человеком"
      : `рой: перезапуск — ${item.reason}`;
    if (config.dryRun) {
      log.action(`[dry-run] revoke ${item.id}: ${note}`);
      log.action(`[dry-run] launch ${item.id} → порт ${item.port}`);
      restarted.push({id: item.id, reason: item.reason, generation: item.generation});
      continue;
    }
    let revoked;
    try {
      revoked = await listik.revoke(item.id, note);
      log.action(`revoke ${item.id}: ${item.reason}`);
    } catch (err) {
      log.line(`revoke ${item.id} ошибка: ${errText(err)}`);
      continue;
    }
    if (!revoked.launch_finished_at) {
      try {
        await listik.needsOwner(item.id, `рой: полномочия отозваны, но процесс ` +
          `${revoked.launch_pid ?? "—"} не снят — сними его сам (kill), затем ` +
          `listik release ${item.id} и listik needs-owner ${item.id} --clear "…"`);
        log.action(`needs-owner ${item.id}: process_not_stopped`);
        needsOwnerDone.push(item.id);
        const t = taskById.get(item.id);
        if (t) t.needs_owner = true;
      } catch (err) {
        log.line(`needs-owner ${item.id} ошибка: ${errText(err)}`);
      }
      continue;
    }
    const task = taskById.get(item.id);
    const port = item.port;
    try {
      if (task && portOf(task) == null) {
        const fresh = await listik.show(item.id);
        const labels = [...(fresh.labels || []), `port:${port}`];
        await listik.setLabels(item.id, labels);
        if (task) task.labels = labels;
        log.action(`set ${item.id} labels += port:${port}`);
      }
      const result = await listik.launch(item.id, {LISTIK_DEV_PORT: String(port)});
      log.action(`перезапуск ${item.id} → порт ${port}, поколение ${result.generation}`);
      restarted.push({id: item.id, reason: item.reason, generation: result.generation});
    } catch (err) {
      log.line(`launch ${item.id} ошибка: ${errText(err)}`);
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

  const crashedDone = decision.crashed
    .filter(c => needsOwnerDone.includes(c.id))
    .map(c => ({id: c.id, exitCode: c.exitCode}));
  const giveUpDone = decision.giveUp.filter(g => needsOwnerDone.includes(g.id)).map(g => g.id);

  const barrierMerged = barrierResult ? barrierResult.merged : [];
  const barrierUnmerged = barrierResult ? barrierResult.unmerged : [];
  const barrierHalt = barrierResult ? barrierResult.halt : [];

  const report = {
    ...decision.report,
    launch: launched,
    needsOwner: decision.report.needsOwner.filter(n => needsOwnerDone.includes(n.id)),
    skipped: decision.report.skipped.map(s =>
      s.reason === "held" ? {...s, holder: (taskById.get(s.id) || {}).holder} : s),
    restart: restarted,
    giveUp: giveUpDone,
    crashed: crashedDone,
    stopOnly: stoppedClosed,
    merged: barrierMerged,
    unmerged: barrierUnmerged,
    halt: barrierHalt.length ? barrierHalt[0] : null,
  };
  log.summary(report);

  return {
    serverDown: false,
    server: status.server,
    cycles: [],
    launched,
    needsOwner: needsOwnerDone,
    restarted: restarted.map(r => r.id),
    running: decision.running.map(t => t.id),
    open: decision.open.map(t => t.id),
    needsOwnerOpen: decision.open.filter(t => t.needs_owner).map(t => t.id),
    blocked: plan.blocked || {},
    report,
    barrier: {
      merged: barrierMerged,
      mergedNow: barrierResult ? barrierResult.mergedNow : [],
      unmerged: barrierUnmerged,
      halt: barrierHalt,
      gate,
    },
    watch: watchSummary,
  };
}
