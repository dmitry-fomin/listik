// Один тик: собрать вход из Listik (субпроцессами), решить (decide), выполнить
// действия, свести итог. Без состояния между тиками — всё читается заново.
import path from "node:path";
import fs from "node:fs";
import {decide, portOf, allocatePort, isRunning, OPEN_STATUSES, dueDefaults, restartCauseRu} from "./decide.mjs";
import {runBarrier, haltCards} from "./barrier.mjs";
import {ConfigError, parseSwarmConfig, swarmConfigFor, DEFAULT_QUESTION_TIMEOUT} from "./config.mjs";
import {limitText, rollbackVerdict} from "./rollback.mjs";
import * as git from "./git.mjs";

const MAIN_WORKTREE_MARKERS = new Set(["main", "master"]);

function errText(err) {
  return `${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`;
}

// Карточки, за которыми тик обязан посмотреть `show` перед `decide` (п.6): бегущие
// открытые и упавшие открытые без флага; плюс задачи роя с `needs_owner` (открытые
// или `done`) — чтобы классифицировать мягкий вопрос и автоответить по таймауту.
function needsEventsFetch(t) {
  if (t.needs_owner) {
    if (portOf(t) == null && !t.launched_by) return false;
    return OPEN_STATUSES.has(t.status) || t.status === "done";
  }
  if (!OPEN_STATUSES.has(t.status)) return false;
  if (isRunning(t)) return true;
  return !!t.launched_by && !!t.launch_finished_at;
}

function budgetBound(n) {
  return n > 0 ? String(n) : "∞";
}

function spentShown(n) {
  return String(Math.round(n * 10) / 10);
}

export async function tick(listik, config, log, runState = null) {
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

  const logWavesApply = (p) => {
    if (!p.applyResult) return;
    const {added, removed, kept} = p.applyResult;
    log.action(`waves --apply: записано ${added.length} рёбер ` +
      `(${added.map(([a, b]) => `${a} → ${b}`).join(", ")}), снято ${removed.length} ` +
      `(${removed.map(([a, b]) => `${a} → ${b}`).join(", ")}), без изменений ${kept}`);
  };
  let plan = await listik.waves(config.project, {apply: !config.dryRun});
  logWavesApply(plan);
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
  let rescopeResult = null;
  let questionTimeout = DEFAULT_QUESTION_TIMEOUT;
  const rollbacks = [];
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
          try {
            const relist = await listik.list(config.project);
            tasks = relist.tasks || [];
          } catch (err) {
            log.line(`list после watch ошибка: ${errText(err)}`);
          }
        }
      }

      let swarmConfig = null;
      let configErrored = false;
      const cfgPath = config.configPath ?? path.join(status.data_dir, "swarm.json");
      try {
        let text = null;
        try {
          text = fs.readFileSync(cfgPath, "utf8");
        } catch (err) {
          if (err.code !== "ENOENT") throw err;
          text = null;
        }
        const parsed = parseSwarmConfig(text);
        swarmConfig = swarmConfigFor(parsed, config.project);
        questionTimeout = swarmConfig.questionTimeout;
      } catch (err) {
        if (err instanceof ConfigError) {
          log.line(err.message);
          gate = {reason: "config", ids: []};
          configErrored = true;
        } else {
          throw err;
        }
      }

      // Предел откатов — сразу после swarm.json, до барьера. Гейт не ставим.
      // ponytail: парковка не повторяется до следующей заморозки; проверка кандидатов перед launch — если дыра проявится
      const watched = (watchRes && watchRes.decisions) || [];
      const maxFreezes = swarmConfig ? swarmConfig.maxFreezes : null;
      let limitUncheckedLogged = false;
      for (const d of watched) {
        if (d.action !== "freeze" || d.ok === false) continue;
        let card;
        try {
          card = await listik.show(d.task);
        } catch (err) {
          log.line(`show ${d.task} ошибка: ${errText(err)}`);
          continue;
        }
        const v = rollbackVerdict(card, maxFreezes);
        if (config.dryRun || d.dry_run) {
          if (maxFreezes != null && v.count + 1 > maxFreezes) {
            log.action(`[dry-run] по пределу ${d.task}: заморозок в окне ${v.count}, ` +
              `стало бы ${v.count + 1} (порог ${maxFreezes})`);
          } else {
            const shownMax = maxFreezes == null ? "?" : maxFreezes;
            log.line(`[dry-run] откат ${d.task} (владелец ${d.owner}): заморозок в окне ${v.count}, ` +
              `стало бы ${v.count + 1} из ${shownMax}`);
          }
          continue;
        }
        const mins = v.last && v.last.minutes != null ? v.last.minutes : 0;
        const shownMax = maxFreezes == null ? "?" : maxFreezes;
        log.line(`откат ${d.task} (владелец ${d.owner}): заморозка ${v.count} из ${shownMax} в окне, ` +
          `всего ${v.total}, ~${mins} мин снятого поколения`);
        if (maxFreezes == null && !limitUncheckedLogged) {
          log.line("предел откатов не проверен: swarm.json с ошибкой");
          limitUncheckedLogged = true;
        }
        const entry = {
          id: d.task, owner: d.owner, minutes: mins, count: v.count, total: v.total, parked: false,
        };
        if (v.exceeded && card.needs_owner) {
          log.line(`по пределу ${d.task}: needs_owner уже стоит`);
        } else if (v.exceeded) {
          try {
            await listik.needsOwner(d.task, limitText(d.task, v, maxFreezes));
            log.action(`needs-owner ${d.task}: freeze_limit`);
            entry.parked = true;
            const parkedTask = tasks.find(t => t.id === d.task);
            if (parkedTask) parkedTask.needs_owner = true;
          } catch (err) {
            log.line(`needs-owner ${d.task} ошибка: ${errText(err)}`);
          }
        }
        rollbacks.push(entry);
      }

      const runningNow = tasks.filter(isRunning);
      if (runningNow.length) {
        const haltIds = haltCards(tasks);
        if (haltIds.length) gate = {reason: "halt", ids: haltIds};
      } else if (!configErrored) {
        barrierResult = await runBarrier({
          listik, git, fs, config, swarmConfig, log, tasks, projectPath, now: new Date(),
          swarmJsonPath: cfgPath,
        });
        gate = barrierResult.gate;

        if (!config.dryRun && barrierResult.rejected && barrierResult.rejected.length) {
          try {
            const relist = await listik.list(config.project);
            tasks = relist.tasks || [];
          } catch (err) {
            log.line(`list после отклонения ошибка: ${errText(err)}`);
          }
          for (const id of barrierResult.rejected) {
            try {
              const shown = await listik.show(id);
              events[id] = shown.events || [];
            } catch (err) {
              log.line(`show ${id} ошибка: ${errText(err)}`);
            }
          }
        }

        // После влитой волны — уточнить области и граф; ошибка тик не роняет.
        const mergedNow = barrierResult.mergedNow || [];
        if (mergedNow.length && config.dryRun) {
          log.action(`[dry-run] rescope ${config.project}: влито ${mergedNow.join(", ")}`);
        } else if (mergedNow.length && swarmConfig.rescope === false) {
          log.line("rescope: выключен в swarm.json");
          rescopeResult = {ok: false, skipped: "disabled"};
        } else if (mergedNow.length) {
          let res = null;
          try {
            res = await listik.rescope(config.project, {timeoutSec: swarmConfig.rescopeTimeout});
          } catch (err) {
            log.line(`rescope ошибка: ${errText(err)}`);
            rescopeResult = {ok: false, error: err.code ?? "error"};
          }
          if (res) {
            const scopes = res.applied?.scopes ?? [];
            const added = res.applied?.edges?.added ?? [];
            const removed = res.applied?.edges?.removed ?? [];
            const cycles = res.cycles ?? [];
            const drift = res.drift ?? {};
            const unspecced = Object.keys(res.unspecced ?? {});
            log.action(`rescope ${config.project}: ТЗ прочитано ${res.extracted ?? 0}, ` +
              `областей записано ${scopes.length} (${scopes.join(", ")}), ` +
              `рёбер +${added.length} −${removed.length}, качество ТЗ: расхождений у ` +
              `${drift.tasks_with_drift ?? 0} из ${drift.tasks_total ?? 0} задач ` +
              `(${drift.outside_files ?? 0} файлов вне области)`);
            if (cycles.length) {
              const desc = cycles.map(c => [...c, c[0]].join(" → ")).join("; ");
              log.line(`rescope: цикл ${desc} — рёбра не записаны`);
            }
            if (unspecced.length) log.line(`rescope: без ТЗ: ${unspecced.join(", ")}`);
            if (res.unscoped && res.unscoped.length) {
              log.line(`rescope: без области: ${res.unscoped.join(", ")}`);
            }
            const refreshed = !!(scopes.length || added.length || removed.length);
            if (refreshed) {
              try {
                plan = await listik.waves(config.project, {apply: true});
                logWavesApply(plan);
              } catch (err) {
                log.line(`waves после rescope ошибка: ${errText(err)}`);
              }
              try {
                tasks = (await listik.list(config.project)).tasks || [];
              } catch (err) {
                log.line(`list после rescope ошибка: ${errText(err)}`);
              }
            }
            rescopeResult = {
              ok: true, extracted: res.extracted ?? 0, scopes, edgesAdded: added.length,
              edgesRemoved: removed.length, cycles, unspecced, refreshed,
            };
          }
        }
      }
    }
  }

  const now = new Date();
  const budgetMinutes = config.budgetMinutes || 0;
  const maxLaunches = config.maxLaunches || 0;
  const spentMinutes = runState
    ? (now.getTime() - runState.startedAt.getTime()) / 60000
    : 0;
  const launchesSoFar = runState ? runState.launches : 0;
  const exhausted = (budgetMinutes > 0 && spentMinutes >= budgetMinutes)
    || (maxLaunches > 0 && launchesSoFar >= maxLaunches);
  const launchesLeft = maxLaunches > 0 ? Math.max(maxLaunches - launchesSoFar, 0) : null;
  log.line(`бюджет: минут ${spentShown(spentMinutes)}/${budgetBound(budgetMinutes)}, ` +
    `запусков ${launchesSoFar}/${budgetBound(maxLaunches)}`);

  const tickConfig = {...config, questionTimeout, budgetExhausted: exhausted, launchesLeft};
  const due = dueDefaults({tasks, events, config: tickConfig, now});
  const defaultsDone = [];
  for (const item of due) {
    const text = `рой: ответа не было ${item.minutes} мин — действует вариант по умолчанию: ${item.line}`;
    if (config.dryRun) {
      log.action(`[dry-run] answer ${item.id}: ${item.line}`);
      defaultsDone.push(item.id);
      continue;
    }
    try {
      await listik.answer(item.id, text);
      log.action(`ответ по умолчанию ${item.id}: ${item.line}`);
      defaultsDone.push(item.id);
    } catch (err) {
      log.line(`answer ${item.id} ошибка: ${errText(err)}`);
      continue;
    }
    try {
      const shown = await listik.show(item.id);
      events[item.id] = shown.events || [];
    } catch (err) {
      log.line(`show ${item.id} ошибка: ${errText(err)}`);
    }
    const answered = tasks.find(t => t.id === item.id);
    if (answered) answered.needs_owner = false;
  }

  const decision = decide({plan, tasks, routes, config: tickConfig, now, events, gate});

  if (decision.cycles.length) {
    const desc = decision.cycles.map(c => [...c, c[0]].join(" → ")).join("; ");
    log.line(`циклы: ${desc}`);
    return {serverDown: false, server: status.server, cycles: decision.cycles};
  }

  const taskById = new Map(tasks.map(t => [t.id, t]));

  const needsOwnerDone = rollbacks.filter(r => r.parked).map(r => r.id);
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
    const isBudget = item.reason === "budget";
    const causeRu = restartCauseRu(item.cause);
    const revokeNote = isBudget
      ? `рой: бюджет прогона исчерпан — процесс снят (${causeRu})`
      : `рой: ${item.reason}, предел перезапусков`;
    const revokeLog = isBudget
      ? `revoke ${item.id}: бюджет исчерпан (${causeRu})`
      : `revoke ${item.id}: предел перезапусков (${item.reason})`;
    if (config.dryRun) {
      log.action(`[dry-run] revoke ${item.id}: ${revokeNote}`);
      log.action(`[dry-run] needs-owner ${item.id}: ${item.text}`);
      continue;
    }
    try {
      await listik.revoke(item.id, revokeNote);
      log.action(revokeLog);
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
      : item.reason === "rejected"
        ? "рой: перезапуск — не принята (верификатор)"
        : item.reason === "defaulted"
          ? "рой: перезапуск — ответ по умолчанию"
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
  const barrierHalt = barrierResult
    ? barrierResult.halt
    : (gate && gate.reason === "halt" ? (gate.ids || []) : []);
  const barrierUnfrozen = barrierResult ? barrierResult.unfrozen : [];
  const barrierIntegration = barrierResult ? barrierResult.integration : null;
  const barrierCleaned = barrierResult ? barrierResult.cleaned : [];
  const barrierRejected = barrierResult ? (barrierResult.rejected || []) : [];

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
    rejected: barrierRejected,
    defaults: defaultsDone,
    unfrozen: barrierUnfrozen,
    integration: barrierIntegration,
    halt: barrierHalt.length ? barrierHalt[0] : null,
    rollbacks: rollbacks.map(r => r.id),
    rollbackMinutes: rollbacks.reduce((sum, r) => sum + r.minutes, 0),
    parked: rollbacks.filter(r => r.parked).map(r => r.id),
  };
  log.summary(report);

  return {
    serverDown: false,
    server: status.server,
    cycles: [],
    launched,
    needsOwner: needsOwnerDone,
    defaults: defaultsDone,
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
      rejected: barrierRejected,
      unfrozen: barrierUnfrozen,
      integration: barrierIntegration,
      cleaned: barrierCleaned,
      halt: barrierHalt,
      gate,
    },
    watch: watchSummary,
    rescope: rescopeResult,
    rollbacks,
    budget: {exhausted, spentMinutes, launches: launchesSoFar},
  };
}
