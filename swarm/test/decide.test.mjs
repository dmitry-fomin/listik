import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {fileURLToPath} from "node:url";
import {decide, portOf, allocatePort, isFrozen, REJECTED_MARK, isSoftQuestion, defaultLine,
  openQuestion, fromEvents, fromComments, dueDefaults, restartNote, isCountedRestart,
  restartCauseRu} from "../decide.mjs";
import {textSlicedStuck} from "../decide.mjs";
import {DEFAULT_QUESTION_TIMEOUT} from "../config.mjs";

// Маршрут роя для каждого id фикстур: карточки не роя рой не видит вовсе.
const SWARM_ROUTES = [..."abcdefghijklmnopqrstuvwxyz".split(""), ...Array.from({length: 21}, (_, i) => "t" + i), "nr", "ns", "frozen", "canc", "cand", "done-plain", "done-port", "done1", "halt1", "n1", "new", "running1"].map(id => ({key: "route-" + id, kind: "swarm", icon: "low"}));

const config = {parallel: 3, weights: {xhigh: 3, high: 2, medium: 1, low: 1, xlow: 1, direct: 1}};

function task(id, over = {}) {
  return {
    id, status: "open", holder: "", needs_owner: false, launched_by: "", launch_finished_at: "",
    launch_route: "route-" + id, worktree: "", labels: [], ...over,
  };
}

function routesFor(ids, icon) {
  return ids.map(id => ({key: "route-" + id, kind: "swarm", icon}));
}

test("нарезанный родитель режима роя не запускается, режим скила в выборку не попадает", () => {
  const sliced = task("p", {launch_driver: "swarm", has_portions: true, launch_route: "swarm"});
  const skill = task("s", {launch_driver: "skill", has_portions: true, launch_route: "skill"});
  const cancelled = task("c", {
    launch_driver: "swarm", portions_cancelled_only: true, launch_route: "swarm",
  });
  const plan = {waves: [["p", "s", "c"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [
    {key: "swarm", icon: "low", kind: "swarm"},
    {key: "skill", icon: "low", kind: "pipeline"},
  ];
  const res = decide({plan, tasks: [sliced, skill, cancelled], routes, config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped.filter(s => s.reason === "sliced").map(s => s.id), ["p", "c"]);
  assert.ok(!res.skipped.some(s => s.id === "s"));
});

test("1: три независимые задачи, parallel 3, вес 1 — все три в launch", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = routesFor(ids, "low");
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ids);
});

test("2а: xhigh=3, parallel 3, [xhigh, low, low] — берём xhigh, остальные capacity", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "route-a", kind: "swarm", icon: "xhigh"}, {key: "route-b", kind: "swarm", icon: "low"}, {key: "route-c", kind: "swarm", icon: "low"}];
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a"]);
  assert.deepEqual(res.skipped.map(s => s.id), ["b", "c"]);
  assert.ok(res.skipped.every(s => s.reason === "capacity"));
});

test("2б: xhigh=3, parallel 3, [low, low, xhigh] — два low, xhigh пропущен", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "route-a", kind: "swarm", icon: "low"}, {key: "route-b", kind: "swarm", icon: "low"}, {key: "route-c", kind: "swarm", icon: "xhigh"}];
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a", "b"]);
  assert.deepEqual(res.skipped.map(s => s.id), ["c"]);
});

test("3: parallel 2, единственный кандидат xhigh (вес 3) — берём одного, oversized", () => {
  const tasks = [task("a")];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "route-a", kind: "swarm", icon: "xhigh"}];
  const res = decide({plan, tasks, routes, config: {...config, parallel: 2}, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a"]);
  assert.equal(res.report.reason, "oversized");
});

test("4: одна бегущая — launch пуст, batch_running, running содержит её", () => {
  const running = task("a", {launched_by: "listik", launch_finished_at: null});
  const candidate = task("b", {launch_driver: "swarm"});
  const tasks = [running, candidate];
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = routesFor(["a", "b"], "low");
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.equal(res.report.reason, "batch_running");
  assert.deepEqual(res.running.map(t => t.id), ["a"]);
});

test("5: закрытая карточка с launched_by и пустым launch_finished_at — в running", () => {
  const done = task("a", {status: "done", launched_by: "listik", launch_finished_at: ""});
  const tasks = [done];
  const plan = {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: SWARM_ROUTES, config, now: new Date()});
  assert.deepEqual(res.running.map(t => t.id), ["a"]);
  assert.deepEqual(res.launch, []);
});

test("6: launch_finished_at непуст — не бегущая, и не кандидат (launched_by непуст)", () => {
  const t = task("a", {launched_by: "listik", launch_finished_at: "2026-01-01T00:00:00Z"});
  const tasks = [t];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: SWARM_ROUTES, config, now: new Date()});
  assert.deepEqual(res.running, []);
  assert.deepEqual(res.launch, []);
});

test("7: кандидат с holder — в skipped held, не в launch", () => {
  const t = task("a", {holder: "dmitry"});
  const tasks = [t];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: routesFor(["a"]), config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, [{id: "a", reason: "held"}]);
});

test("7б: has_portions прячет родителя роя; карточка скила и конвейер без снимка в выборку не попадают", () => {
  const snapSwarm = task("a", {has_portions: true, launch_driver: "swarm"});
  const snapSkill = task("b", {has_portions: true, launch_driver: "skill"});
  // Снимка нет — смотрим маршрут: конвейер не рой, даже с лишним полем способа исполнения (см. route-c).
  const routePipeline = task("c", {has_portions: true});
  const tasks = [snapSwarm, snapSkill, routePipeline];
  const plan = {waves: [["a", "b", "c"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [
    {key: "route-a", kind: "swarm", icon: "low"},
    {key: "route-b", kind: "swarm", icon: "low"},
    {key: "route-c", icon: "low", kind: "pipeline", driver: "swarm"},
  ];
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped.map(s => [s.id, s.reason]), [["a", "sliced"]]);
});

test("8: unroutable — без вопроса; unscoped — needsOwner с needs_owner ложным, не для true", () => {
  const openUnroutable = task("a");
  const flaggedUnscoped = task("b", {needs_owner: true});
  const openUnscoped = task("c");
  const tasks = [openUnroutable, flaggedUnscoped, openUnscoped];
  const plan = {waves: [[]], cycles: [], blocked: {}, unroutable: ["a"], unscoped: ["b", "c"]};
  const res = decide({plan, tasks, routes: routesFor(["a", "b", "c"]), config, now: new Date()});
  assert.deepEqual(res.needsOwner.map(n => n.id), ["c"]);
  const c = res.needsOwner[0];
  assert.equal(c.reason, "unscoped");
  assert.match(c.text, /listik set c write_scope=<пути через запятую>\.$/);
});

test("8а: карточка без launch_route в волне не запускается", () => {
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks: [task("a", {launch_route: null})], routes: SWARM_ROUTES, config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, []);
  assert.deepEqual(res.needsOwner, []);
});

test("8б: карточку держит не рой — ни вопроса unscoped, ни ответа по умолчанию", () => {
  const held = task("a", {holder: "claude", status: "in_progress"});
  const heldScoped = task("b", {holder: "claude", status: "in_progress"});
  const ours = task("c", {holder: "grok", launched_by: "agent:listik-swarm", status: "in_progress"});
  const plan = {waves: [[]], cycles: [], blocked: {}, unroutable: [], unscoped: ["a", "b", "c"]};
  const res = decide({plan, tasks: [held, heldScoped, ours], routes: routesFor(["a", "b", "c"]), config, now: new Date()});
  assert.deepEqual(res.needsOwner.map(n => n.id), ["c"]);

  const now = new Date("2026-09-24T12:00:00Z");
  const q = {kind: "question", ts: "2026-09-24T10:00:00Z", text: "вопрос?\nпо умолчанию: да"};
  const events = {
    a: [{kind: "question", to_value: q.text, from_value: "0", ts: q.ts}],
    c: [{kind: "question", to_value: q.text, from_value: "0", ts: q.ts}],
  };
  const flagged = [
    task("a", {holder: "claude", status: "in_progress", needs_owner: true}),
    task("c", {holder: "grok", launched_by: "agent:listik-swarm", status: "in_progress", needs_owner: true}),
  ];
  const due = dueDefaults({tasks: flagged, events, config: {questionTimeout: 30}, now});
  assert.ok(!due.some(d => d.id === "a"));
});

test("9: cycles непуст — launch и needsOwner пусты", () => {
  const tasks = [task("a"), task("b")];
  const plan = {waves: [["a"]], cycles: [["a", "b"]], unroutable: ["a"], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: routesFor(["a", "b"]), config, now: new Date()});
  assert.deepEqual(res.cycles, [["a", "b"]]);
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.needsOwner, []);
  assert.equal("unroutable" in res.report, false);
});

test("10: задача из waves[0], которой нет в tasks — пропускается без ошибки", () => {
  const plan = {waves: [["ghost"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks: [], routes: SWARM_ROUTES, config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, []);
});

test("11: маршрут без icon и маршрут не в routes — вес 1", () => {
  const a = task("a", {launch_route: "no-icon"});
  const b = task("b", {launch_route: "missing-route", launch_driver: "swarm"});
  const tasks = [a, b];
  const plan = {waves: [["a", "b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "no-icon", icon: null, kind: "swarm"}];
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a", "b"]);
  assert.ok(res.launch.every(l => l.weight === 1));
});

test("порты: метка есть — её номер", () => {
  const t = task("a", {labels: ["port:5199"]});
  assert.equal(portOf(t), 5199);
});

test("порты: занятые 5170,5171 у открытых — allocate 5172", () => {
  const t1 = task("a", {labels: ["port:5170"]});
  const t2 = task("b", {labels: ["port:5171"]});
  const t3 = task("c");
  const res = allocatePort([t1, t2, t3], t3, 5170, 100);
  assert.equal(res, 5172);
});

test("порты: 5170 у закрытой карточки — свободен", () => {
  const closed = task("a", {status: "done", labels: ["port:5170"]});
  const t = task("b");
  const res = allocatePort([closed, t], t, 5170, 100);
  assert.equal(res, 5170);
});

test("порты: диапазон исчерпан — null", () => {
  const used = [0, 1].map(i => task("t" + i, {labels: [`port:${5170 + i}`]}));
  const t = task("new");
  const res = allocatePort([...used, t], t, 5170, 2);
  assert.equal(res, null);
});

test("порты: метка port:abc игнорируется", () => {
  const t = task("a", {labels: ["port:abc"]});
  assert.equal(portOf(t), null);
});

// --- надзор за бегущими и упавшими ---

const supConfig = {
  ...config, actor: "agent:listik-swarm", timeoutMinutes: 0, maxRestarts: 1,
  portBase: 5170, portCount: 100,
};
const minsAgo = (now, m) => new Date(now.getTime() - m * 60000).toISOString();

function runningTask(id, over = {}) {
  return task(id, {launched_by: "agent:listik-swarm", launch_finished_at: null, ...over});
}

function decideRunning(t, {plan, config: cfg = supConfig, now = new Date(), events} = {}) {
  const p = plan ?? {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  return decide({plan: p, tasks: [t], routes: SWARM_ROUTES, config: cfg, now, events});
}

test("надзор: режим роя молчит 30 мин — не stale; timeoutMinutes его всё же снимает", () => {
  const now = new Date();
  const swarm = runningTask("a", {
    launch_driver: "swarm",
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const quiet = decideRunning(swarm, {now});
  assert.deepEqual(quiet.restart, []);
  assert.deepEqual(quiet.giveUp, []);

  const timed = decideRunning(swarm, {
    now, config: {...supConfig, timeoutMinutes: 20},
  });
  assert.equal(timed.restart[0].reason, "timeout");
});

test("надзор: бегущая карточка роя молчит 10 часов при timeoutMinutes 0 — restart/giveUp/stopOnly пусты, в report нет stale/silence", () => {
  const now = new Date();
  const t = runningTask("a", {
    launch_driver: "swarm", launched_at: minsAgo(now, 600), labels: ["port:5170"],
  });
  const res = decideRunning(t, {now});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
  assert.deepEqual(res.stopOnly, []);
  assert.equal("stale" in res.report, false);
  assert.equal("silence" in res.report, false);
});

test("надзор: просрочена (30 мин > timeoutMinutes 20), port:5170, без revoke — restart timeout", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 25), labels: ["port:5170"],
  });
  const res = decideRunning(t, {now, config: {...supConfig, timeoutMinutes: 20}});
  assert.deepEqual(res.restart, [{id: "a", reason: "timeout", restarts: 0, generation: undefined, port: 5170}]);
});

test("надзор: timeoutMinutes 60, launched_at 61 мин назад, holder_at свежий — restart timeout", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 61), holder_at: minsAgo(now, 1), labels: ["port:5170"],
  });
  const res = decideRunning(t, {now, config: {...supConfig, timeoutMinutes: 60}});
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "timeout");

  const resOff = decideRunning(t, {now, config: {...supConfig, timeoutMinutes: 0}});
  assert.deepEqual(resOff.restart, []);
});

test("надзор: уже один revoke-перезапуск, maxRestarts 1 — giveUp с текстом освобождения", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const events = {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25),
    note: "рой: перезапуск — stale; запуск …, pid 1, процесс снят"}]};
  const res = decideRunning(t, {now, events, config: {...supConfig, timeoutMinutes: 20}});
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
  assert.match(res.giveUp[0].text, /listik release a/);
  assert.match(res.giveUp[0].text, /needs-owner a --clear/);
});

test("надзор: revoke с другой пометкой (предел/разрешение) в счёт не идёт — всё ещё restart", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  for (const note of ["рой: stale, предел перезапусков", "рой: перезапуск разрешён человеком"]) {
    const res = decideRunning(t, {
      now, config: {...supConfig, timeoutMinutes: 20},
      events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25), note}]},
    });
    assert.equal(res.restart.length, 1, note);
    assert.equal(res.giveUp.length, 0, note);
  }
});

test("надзор: restartNote причин timeout/rejected/defaulted считается, answered — нет", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const cfg = {...supConfig, timeoutMinutes: 20};  // maxRestarts 1
  for (const reason of ["timeout", "rejected", "defaulted"]) {
    const res = decideRunning(t, {
      now, config: cfg,
      events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25),
        note: restartNote(reason)}]},
    });
    assert.deepEqual(res.restart, [], reason);
    assert.equal(res.giveUp.length, 1, reason);
  }
  const res = decideRunning(t, {
    now, config: cfg,
    events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25),
      note: restartNote("answered")}]},
  });
  assert.equal(res.restart.length, 1);
  assert.equal(res.giveUp.length, 0);
});

test("надзор: старая заметка «рой: перезапуск — timeout» при maxRestarts 1 — giveUp", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const res = decideRunning(t, {
    now, config: {...supConfig, timeoutMinutes: 20},
    events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25),
      note: "рой: перезапуск — timeout"}]},
  });
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
});

test("isCountedRestart: префикс «перезапуск —» считается, человеческая и не-строка нет", () => {
  for (const note of [
    "рой: перезапуск — timeout",
    "рой: перезапуск — stale",
    "рой: перезапуск — что-то новое",
    restartNote("timeout"), restartNote("rejected"), restartNote("defaulted"),
  ]) {
    assert.equal(isCountedRestart(note), true, String(note));
  }
  for (const note of [
    "рой: перезапуск разрешён человеком", restartNote("answered"),
    "рой: stale, предел перезапусков", undefined, null, 42,
  ]) {
    assert.equal(isCountedRestart(note), false, String(note));
  }
});

test("restartNote: точные тексты причин, неизвестная — через restartCauseRu", () => {
  assert.equal(restartNote("answered"), "рой: перезапуск разрешён человеком");
  assert.equal(restartNote("rejected"), "рой: перезапуск — не принята (верификатор)");
  assert.equal(restartNote("defaulted"), "рой: перезапуск — ответ по умолчанию");
  assert.equal(restartNote("timeout"), "рой: перезапуск — таймаут");
  assert.equal(restartNote("stale"), "рой: перезапуск — " + restartCauseRu("stale"));
});

test("надзор: revoke от agent:claude — не считается в restarts", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const res = decideRunning(t, {
    now, config: {...supConfig, timeoutMinutes: 20},
    events: {a: [{kind: "revoke", actor: "agent:claude", ts: minsAgo(now, 25),
      note: "рой: перезапуск — stale"}]},
  });
  assert.equal(res.restart[0].restarts, 0);
});

test("надзор: актор Agent:Listik-Swarm в конфиге сравнивается нормализованно", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const res = decideRunning(t, {
    now, config: {...supConfig, actor: "Agent:Listik-Swarm", maxRestarts: 1, timeoutMinutes: 20},
    events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25),
      note: "рой: перезапуск — stale"}]},
  });
  assert.equal(res.restart.length, 0);
  assert.equal(res.giveUp.length, 1);
});

test("надзор: просроченная без метки порта — свободный порт есть → restart; свободных нет → giveUp no_port", () => {
  const now = new Date();
  const t = runningTask("a", {launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30)});
  const resFree = decideRunning(t, {now, config: {...supConfig, timeoutMinutes: 20}});
  assert.equal(resFree.restart[0].port, 5170);

  const full = [];
  for (let p = 5170; p < 5270; p++) full.push(task(`o${p}`, {labels: [`port:${p}`]}));
  const res = decide({
    plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}},
    tasks: [t, ...full], routes: SWARM_ROUTES, config: {...supConfig, timeoutMinutes: 20}, now,
  });
  assert.equal(res.restart.length, 0);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].reason, "no_port");
});

test("надзор: needs_owner true у просроченной — ни restart, ни giveUp", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
    needs_owner: true,
  });
  const res = decideRunning(t, {now, config: {...supConfig, timeoutMinutes: 20}});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
});

test("надзор: закрытая бегущая молчит час — restart/giveUp/crashed пусты; с timeout — stopOnly", () => {
  const now = new Date();
  const t = runningTask("a", {status: "done", launched_at: minsAgo(now, 60), holder_at: minsAgo(now, 60)});
  const res = decideRunning(t, {now});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
  assert.deepEqual(res.crashed, []);
  assert.deepEqual(res.stopOnly, []);

  const res2 = decideRunning(t, {now, config: {...supConfig, timeoutMinutes: 30}});
  assert.deepEqual(res2.stopOnly, [{id: "a", reason: "timeout"}]);
});

test("надзор: упавшая открытая без answer — crashed с кодом, поколением, логом", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, generation: 3, launch_log: "/logs/a.log",
  });
  const res = decideRunning(t, {plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}}});
  assert.equal(res.crashed.length, 1);
  assert.match(res.crashed[0].text, /код 1/);
  assert.match(res.crashed[0].text, /поколение 3/);
  assert.match(res.crashed[0].text, /\/logs\/a\.log/);
  assert.deepEqual(res.restart, []);

  const closed = {...t, status: "done"};
  const resClosed = decideRunning(closed, {plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}}});
  assert.deepEqual(resClosed.crashed, []);
  assert.deepEqual(resClosed.restart, []);
});

test("надзор: упавшая с answer позже launch_finished_at — restart answered даже при restarts >= maxRestarts", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "revoke", actor: "agent:listik-swarm", ts: "2025-01-01T00:00:00Z", note: "рой: перезапуск — stale"},
    {kind: "answer", actor: "dmitry", ts: "2026-01-01T01:00:00Z"},
  ]};
  const res = decideRunning(t, {
    plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}},
    config: {...supConfig, maxRestarts: 0}, events,
  });
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "answered");
  assert.deepEqual(res.crashed, []);
});

test("надзор: упавшая с answer в ту же секунду, что launch_finished_at — restart answered", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [{kind: "answer", actor: "dmitry", ts: "2026-01-01T00:00:00Z"}]};
  const res = decideRunning(t, {
    plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}}, events,
  });
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "answered");
  assert.deepEqual(res.crashed, []);
});

test("надзор: упавшая с answer раньше launch_finished_at — crashed, exit_code null — «код неизвестен»", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: null, needs_owner: false,
  });
  const events = {a: [{kind: "answer", actor: "dmitry", ts: "2025-01-01T00:00:00Z"}]};
  const res = decideRunning(t, {
    plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}}, events,
  });
  assert.equal(res.crashed.length, 1);
  assert.match(res.crashed[0].text, /код неизвестен/);
});

// --- заморозка (frozen-by:) и гейт ---

test("isFrozen: первая метка frozen-by: → значение, иначе null", () => {
  assert.equal(isFrozen(task("a", {labels: ["frozen-by:t1"]})), "t1");
  assert.equal(isFrozen(task("a", {labels: []})), null);
});

test("заморозка: задача с frozen-by: отсекается до расчёта ёмкости, ёмкость не съедена", () => {
  const frozen = task("a", {labels: ["frozen-by:t1"]});
  const normal = task("b");
  const tasks = [frozen, normal];
  const plan = {waves: [["a", "b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = routesFor(["a", "b"], "low");
  const res = decide({plan, tasks, routes, config: {...config, parallel: 1}, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["b"]);
  assert.ok(res.skipped.some(s => s.id === "a" && s.reason === "frozen"));
});

test("гейт: launch пуст, кандидаты в skipped gated, report.reason и report.gate", () => {
  const t9 = task("t9");
  const tasks = [t9];
  const plan = {waves: [["t9"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "unmerged", ids: ["t9"]};
  const res = decide({plan, tasks, routes: routesFor(["t9"]), config, now: new Date(), gate});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, [{id: "t9", reason: "gated"}]);
  assert.equal(res.report.reason, "unmerged");
  assert.deepEqual(res.report.gate, gate);
});

test("гейт с ids: [] — то же поведение, report.reason = config", () => {
  const t = task("a");
  const tasks = [t];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "config", ids: []};
  const res = decide({plan, tasks, routes: routesFor(["a"]), config, now: new Date(), gate});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, [{id: "a", reason: "gated"}]);
  assert.equal(res.report.reason, "config");
});

test("гейт: карточка с holder — held, свободная — gated; launch пуст", () => {
  const held = task("h", {holder: "claude"});
  const free = task("f");
  const plan = {waves: [["h", "f"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "config", ids: []};
  const res = decide({plan, tasks: [held, free], routes: routesFor(["h", "f"]), config,
    now: new Date(), gate});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, [{id: "h", reason: "held"}, {id: "f", reason: "gated"}]);
});

test("бегущая без гейта: wave0 не обходится — карточка с holder не в skipped", () => {
  const now = new Date();
  const running = runningTask("a", {launched_at: now.toISOString()});
  const held = task("h", {holder: "claude"});
  const plan = {waves: [["h"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks: [running, held], routes: routesFor(["a", "h"]), config, now});
  assert.deepEqual(res.launch, []);
  assert.equal(res.report.reason, "batch_running");
  assert.deepEqual(res.skipped, []);
});

test("гейт вместе с надзором: кандидат волны gated, бегущая restart по timeout, crashed отдельно", () => {
  const now = new Date();
  const candidate = task("b", {launch_driver: "swarm"});
  const timed = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const crashedTask = task("c", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, generation: 3, launch_log: "/logs/c.log",
  });
  const tasks = [candidate, timed, crashedTask];
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "unmerged", ids: []};
  const res = decide({plan, tasks, routes: SWARM_ROUTES,
    config: {...supConfig, timeoutMinutes: 20}, now, gate});
  assert.deepEqual(res.launch, []);
  assert.ok(res.skipped.some(s => s.id === "b" && s.reason === "gated"));
  assert.ok(res.restart.some(r => r.id === "a"));
  assert.ok(res.crashed.some(c => c.id === "c"));
  assert.equal(res.report.reason, "unmerged");
});

test("надзор: упавшая с needs_owner true — ничего; упавшая не в running, партия запускается", () => {
  const crashedFlagged = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z", needs_owner: true,
  });
  const candidate = task("b", {launch_driver: "swarm"});
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks: [crashedFlagged, candidate], routes: SWARM_ROUTES, config: supConfig, now: new Date()});
  assert.deepEqual(res.crashed, []);
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.running, []);
  assert.deepEqual(res.launch.map(l => l.id), ["b"]);
});

const emptyPlan = {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};

test("надзор: comment роя REJECTED_MARK позже launch_finished_at — restart rejected, crashed пуст", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "comment", actor: "agent:listik-swarm", ts: "2026-01-01T01:00:00Z",
      note: `${REJECTED_MARK} {"reason":"red"}`},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "rejected");
  assert.deepEqual(res.crashed, []);
});

test("надзор: REJECTED_MARK от другого актора — crashed, restart пуст", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "comment", actor: "agent:claude", ts: "2026-01-01T01:00:00Z",
      note: `${REJECTED_MARK} {"reason":"red"}`},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.deepEqual(res.restart, []);
  assert.equal(res.crashed.length, 1);
});

test("надзор: REJECTED_MARK раньше launch_finished_at — crashed", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "comment", actor: "agent:listik-swarm", ts: "2025-01-01T00:00:00Z",
      note: `${REJECTED_MARK} {"reason":"red"}`},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.deepEqual(res.restart, []);
  assert.equal(res.crashed.length, 1);
});

test("надзор: needs_owner true с REJECTED_MARK — ни restart, ни crashed", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: true, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "comment", actor: "agent:listik-swarm", ts: "2026-01-01T01:00:00Z",
      note: `${REJECTED_MARK} {"reason":"red"}`},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.crashed, []);
});

// --- мягкий вопрос ---

const SOFT_Q = "Какой формат?\nпо умолчанию: JSON";

test("isSoftQuestion/defaultLine: таблица", () => {
  const rows = [
    [SOFT_Q, true, "JSON"],
    ["по умолчанию: да", true, "да"],
    ["По умолчанию: да", false, null],
    ["вопрос по умолчанию: да", false, null],
    ["рой: у задачи нет маршрута … по умолчанию: high", false, null],
    ["  рой:\nпо умолчанию: x", false, "x"],
    ["", false, null],
    [null, false, null],
    [42, false, null],
  ];
  for (const [text, soft, line] of rows) {
    assert.equal(isSoftQuestion(text), soft, String(text));
    assert.equal(defaultLine(text), line, String(text));
  }
});

test("openQuestion: вопрос; ответ закрывает; новый вопрос после ответа; порядок не важен; comment/revoke игнор", () => {
  const q1 = {kind: "question", ts: "2026-01-01T00:00:00Z", text: SOFT_Q};
  const a1 = {kind: "answer", ts: "2026-01-01T00:10:00Z", text: "XML"};
  const q2 = {kind: "question", ts: "2026-01-01T00:20:00Z", text: "ещё?\nпо умолчанию: YAML"};
  const noise = [
    {kind: "comment", ts: "2026-01-01T00:05:00Z", text: `${REJECTED_MARK} {}`},
    {kind: "revoke", ts: "2026-01-01T00:06:00Z", text: "рой: перезапуск — stale"},
  ];
  assert.deepEqual(openQuestion([q1]), {ts: q1.ts, text: q1.text});
  assert.equal(openQuestion([q1, a1]), null);
  assert.deepEqual(openQuestion([q1, a1, q2]), {ts: q2.ts, text: q2.text});
  assert.deepEqual(openQuestion([q2, a1, q1]), {ts: q2.ts, text: q2.text});
  assert.deepEqual(openQuestion([...noise, q1]), {ts: q1.ts, text: q1.text});
  assert.equal(openQuestion(fromEvents([
    {kind: "comment", ts: "2026-01-01T00:00:00Z", note: `${REJECTED_MARK} {}`},
    {kind: "question", ts: "2026-01-01T00:01:00Z", note: SOFT_Q},
    {kind: "revoke", ts: "2026-01-01T00:02:00Z", note: "рой: x"},
  ])).text, SOFT_Q);
  assert.equal(openQuestion(fromComments([
    {kind: "journal", created_at: "2026-01-01T00:00:00Z", text: "x"},
    {kind: "question", created_at: "2026-01-01T00:01:00Z", text: SOFT_Q},
  ])).text, SOFT_Q);
});

test("надзор: бегущая с мягким вопросом, 30 мин при timeoutMinutes 20 — restart timeout", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
    needs_owner: true,
  });
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 30), note: SOFT_Q}]};
  const res = decideRunning(t, {now, events, config: {...supConfig, timeoutMinutes: 20}});
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "timeout");
});

function crashedSoft(over = {}) {
  return task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: true, labels: ["port:5170"], ...over,
  });
}

test("надзор: упавшая с мягким вопросом 5 мин назад — crashed/restart пусты, dueDefaults пуст", () => {
  const now = new Date();
  const t = crashedSoft();
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 5), note: SOFT_Q}]};
  const cfg = {...supConfig, questionTimeout: 30};
  const res = decideRunning(t, {now, events, config: cfg, plan: emptyPlan});
  assert.deepEqual(res.crashed, []);
  assert.deepEqual(res.restart, []);
  assert.deepEqual(dueDefaults({tasks: [t], events, config: cfg, now}), []);
});

test("dueDefaults: мягкий 40 мин → {id, line, minutes}; decide без поля defaults", () => {
  const now = new Date();
  const t = crashedSoft();
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 40), note: SOFT_Q}]};
  const cfg = {...supConfig, questionTimeout: 30};
  const due = dueDefaults({tasks: [t], events, config: cfg, now});
  assert.deepEqual(due, [{id: "a", line: "JSON", minutes: 30}]);
  const res = decideRunning(t, {now, events, config: cfg, plan: emptyPlan});
  assert.equal("defaults" in res, false);
  assert.deepEqual(dueDefaults({tasks: [t], events, config: cfg, now}), due);
});

test("dueDefaults: done с мягким 40 мин — да; cancelled — нет; жёсткий — нет; timeout 0 — нет; рой: — нет", () => {
  const now = new Date();
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 40), note: SOFT_Q}]};
  const cfg = {...supConfig, questionTimeout: 30};
  const done = crashedSoft({status: "done"});
  assert.deepEqual(dueDefaults({tasks: [done], events, config: cfg, now}),
    [{id: "a", line: "JSON", minutes: 30}]);
  const cancelled = crashedSoft({status: "cancelled"});
  assert.deepEqual(dueDefaults({tasks: [cancelled], events, config: cfg, now}), []);
  const hard = crashedSoft();
  const hardEvents = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 40),
    note: "Какой формат?"}]};
  assert.deepEqual(dueDefaults({tasks: [hard], events: hardEvents, config: cfg, now}), []);
  assert.deepEqual(dueDefaults({tasks: [hard], events, config: {...cfg, questionTimeout: 0}, now}), []);
  const swarmQ = {a: [{kind: "question", actor: "agent:listik-swarm", ts: minsAgo(now, 40),
    note: "рой: у задачи нет маршрута (launch_route) — каким маршрутом её делать? по умолчанию: high-pipeline"}]};
  assert.deepEqual(dueDefaults({tasks: [hard], events: swarmQ, config: cfg, now}), []);
});

test("dueDefaults: вопрос 40 мин, ответ человека 10 мин, потом жёсткий вопрос — пусто", () => {
  const now = new Date();
  const t = crashedSoft();
  const events = {a: [
    {kind: "question", actor: "agent:fake", ts: minsAgo(now, 40), note: SOFT_Q},
    {kind: "answer", actor: "dmitry", ts: minsAgo(now, 10), note: "XML"},
    {kind: "question", actor: "agent:fake", ts: minsAgo(now, 5), note: "Какой формат?"},
  ]};
  assert.deepEqual(dueDefaults({tasks: [t], events, config: {...supConfig, questionTimeout: 30}, now}), []);
});

test("надзор: упавшая, needs_owner false, answer роя позже завершения — restart defaulted", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "answer", actor: "agent:listik-swarm", ts: "2026-01-01T01:00:00Z", note: "рой: ответа не было"},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "defaulted");
});

test("надзор: упавшая, answer человека позже завершения — restart answered", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "answer", actor: "dmitry", ts: "2026-01-01T01:00:00Z"},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.equal(res.restart[0].reason, "answered");
});

test("надзор: answer роя и comment REJECTED_MARK позже завершения — restart rejected", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    needs_owner: false, labels: ["port:5170"],
  });
  const events = {a: [
    {kind: "answer", actor: "agent:listik-swarm", ts: "2026-01-01T01:00:00Z", note: "рой: x"},
    {kind: "comment", actor: "agent:listik-swarm", ts: "2026-01-01T01:01:00Z",
      note: `${REJECTED_MARK} {"reason":"red"}`},
  ]};
  const res = decideRunning(t, {plan: emptyPlan, events});
  assert.equal(res.restart[0].reason, "rejected");
});

test("dueDefaults без questionTimeout использует DEFAULT_QUESTION_TIMEOUT; литерала 30 в decide нет", () => {
  const now = new Date();
  const t = crashedSoft();
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 40), note: SOFT_Q}]};
  const due = dueDefaults({tasks: [t], events, config: {}, now});
  assert.deepEqual(due, [{id: "a", line: "JSON", minutes: DEFAULT_QUESTION_TIMEOUT}]);
  assert.equal(DEFAULT_QUESTION_TIMEOUT, 30);
  const src = fs.readFileSync(fileURLToPath(new URL("../decide.mjs", import.meta.url)), "utf8");
  assert.match(src, /DEFAULT_QUESTION_TIMEOUT/);
  assert.match(src, /from "\.\/config\.mjs"/);
  assert.doesNotMatch(src, /questionTimeout[^\n]*=[^\n]*30/);
});

// --- бюджет прогона ---

test("бюджет: launchesLeft 1, три кандидата веса 1 — один launch, два skipped budget", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = routesFor(ids, "low");
  const res = decide({
    plan, tasks, routes, config: {...config, launchesLeft: 1}, now: new Date(),
  });
  assert.deepEqual(res.launch.map(l => l.id), ["a"]);
  assert.deepEqual(res.skipped, [{id: "b", reason: "budget"}, {id: "c", reason: "budget"}]);
  assert.deepEqual(res.report.budget, {exhausted: false, launchesLeft: 1});
});

test("бюджет: launchesLeft 0 — launch пуст, все skipped budget, reason budget", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = routesFor(ids, "low");
  const res = decide({
    plan, tasks, routes, config: {...config, launchesLeft: 0}, now: new Date(),
  });
  assert.deepEqual(res.launch, []);
  assert.ok(res.skipped.every(s => s.reason === "budget"));
  assert.deepEqual(res.skipped.map(s => s.id), ids);
  assert.equal(res.report.reason, "budget");
  assert.deepEqual(res.report.budget, {exhausted: false, launchesLeft: 0});
});

test("бюджет: launchesLeft null и без поля — все три в launch", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = routesFor(ids, "low");
  const resNull = decide({
    plan, tasks, routes, config: {...config, launchesLeft: null}, now: new Date(),
  });
  const resMissing = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(resNull.launch.map(l => l.id), ids);
  assert.deepEqual(resMissing.launch.map(l => l.id), ids);
  assert.deepEqual(resMissing.report.budget, {exhausted: false, launchesLeft: null});
});

test("бюджет: budgetExhausted + просроченная — restart пуст, giveUp budget, порт не выделялся", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
    launch_log: "/logs/a.log",
  });
  const res = decideRunning(t, {now, config: {...supConfig, budgetExhausted: true, timeoutMinutes: 20}});
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].id, "a");
  assert.equal(res.giveUp[0].reason, "budget");
  assert.equal(res.giveUp[0].cause, "timeout");
  assert.equal(res.giveUp[0].port, undefined);
  assert.match(res.giveUp[0].text, /бюджет прогона исчерпан/);
  assert.match(res.giveUp[0].text, /таймаут/);
  assert.match(res.giveUp[0].text, /\/logs\/a\.log/);
  assert.deepEqual(res.report.budget, {exhausted: true, launchesLeft: null});
});

test("бюджет: budgetExhausted + упавшая с answer — giveUp budget, restart пуст", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, labels: ["port:5170"], launch_log: "/logs/a.log",
  });
  const events = {a: [{kind: "answer", actor: "dmitry", ts: "2026-01-01T01:00:00Z"}]};
  const res = decideRunning(t, {
    plan: emptyPlan, events, config: {...supConfig, budgetExhausted: true},
  });
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].reason, "budget");
  assert.equal(res.giveUp[0].cause, "answered");
  assert.match(res.giveUp[0].text, /бюджет прогона исчерпан/);
  assert.match(res.giveUp[0].text, /ответ/);
});

test("бюджет: budgetExhausted + упавшая без ответа — crashed как раньше", () => {
  const t = task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, generation: 3, launch_log: "/logs/a.log",
  });
  const res = decideRunning(t, {
    plan: emptyPlan, config: {...supConfig, budgetExhausted: true},
  });
  assert.equal(res.crashed.length, 1);
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
});

test("бюджет: budgetExhausted + закрытая бегущая дольше timeout — stopOnly", () => {
  const now = new Date();
  const t = runningTask("a", {
    status: "done", launched_at: minsAgo(now, 60), holder_at: minsAgo(now, 60),
  });
  const res = decideRunning(t, {
    now, config: {...supConfig, timeoutMinutes: 30, budgetExhausted: true},
  });
  assert.deepEqual(res.stopOnly, [{id: "a", reason: "timeout"}]);
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
});

test("бюджет: budgetExhausted не трогает dueDefaults", () => {
  const now = new Date();
  const t = crashedSoft();
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 40), note: SOFT_Q}]};
  const cfg = {...supConfig, questionTimeout: 30, budgetExhausted: true};
  assert.deepEqual(dueDefaults({tasks: [t], events, config: cfg, now}),
    [{id: "a", line: "JSON", minutes: 30}]);
  const res = decideRunning(t, {now, events, config: cfg, plan: emptyPlan});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.crashed, []);
});

test("бюджет: gate unmerged + budgetExhausted — report.reason unmerged", () => {
  const now = new Date();
  const candidate = task("b", {launch_driver: "swarm"});
  const timed = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const tasks = [candidate, timed];
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "unmerged", ids: []};
  const res = decide({
    plan, tasks, routes: SWARM_ROUTES,
    config: {...supConfig, budgetExhausted: true, timeoutMinutes: 20}, now, gate,
  });
  assert.deepEqual(res.launch, []);
  assert.ok(res.skipped.some(s => s.id === "b" && s.reason === "gated"));
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].reason, "budget");
  assert.equal(res.report.reason, "unmerged");
  assert.deepEqual(res.report.budget, {exhausted: true, launchesLeft: null});
});

// --- listik-eps7, порция b ---

test("порты: нечисловая метка пропускается, берётся следующая", () => {
  assert.equal(portOf(task("a", {labels: ["port:x", "port:5173"]})), 5173);
  assert.equal(portOf(task("a", {labels: ["port:x"]})), null);
  assert.equal(portOf(task("a", {labels: ["port:"]})), null);
});

test("defaultLine: несколько маркеров — действует последний", () => {
  const text = "по умолчанию: A\nили так\nпо умолчанию: B";
  assert.equal(defaultLine(text), "B");
  assert.equal(isSoftQuestion(text), true);
  assert.equal(isSoftQuestion("рой: вопрос\nпо умолчанию: A\nпо умолчанию: B"), false);
});

// Зависшая нарезка (listik-zr05, порция f).
const stuckRoutes = [{key: "swarm", icon: "low", kind: "swarm"}, {key: "skill", icon: "low", kind: "pipeline"}];
const stuck = (id, over = {}) => task(id, {
  launch_driver: "swarm", launch_route: "swarm", has_portions: true, portions_stuck: true, ...over,
});
const stuckPlan = (over = {}) => ({waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}, ...over});
const recordsFor = (res, id) =>
  res.needsOwner.filter(n => n.id === id).length + res.skipped.filter(s => s.id === id).length;

test("sliced_stuck: текст вопроса дословно, две строки, без по умолчанию", () => {
  const text = textSlicedStuck("dg-1");
  assert.equal(text,
    "рой: задача нарезана, но ни одна порция не запускается (нет маршрута или этапа).\n" +
    "Принять порции: listik portions adopt dg-1. Начать заново с s1-spec (порции снимутся, " +
    "текст сохранится): listik restart dg-1 --stage s1-spec.");
  assert.equal(text.split("\n").length, 2);
  assert.ok(!text.includes("по умолчанию:"));
});

test("sliced_stuck: карточка роя вне волн, никто не бежит — вопрос, не пропуск", () => {
  const res = decide({plan: stuckPlan(), tasks: [stuck("e")], routes: stuckRoutes, config, now: new Date()});
  assert.deepEqual(res.needsOwner, [{id: "e", reason: "sliced_stuck", text: textSlicedStuck("e")}]);
  assert.ok(!res.skipped.some(s => s.id === "e"));
  assert.deepEqual(res.report.needsOwner, [{id: "e", reason: "sliced_stuck"}]);
});

test("sliced_stuck: при поднятом гейте вопрос есть", () => {
  const res = decide({plan: stuckPlan(), tasks: [stuck("e")], routes: stuckRoutes, config,
    now: new Date(), gate: {reason: "config"}});
  assert.deepEqual(res.needsOwner.map(n => [n.id, n.reason]), [["e", "sliced_stuck"]]);
});

test("sliced_stuck: держатель или launched_by — ни вопроса, ни пропуска", () => {
  const tasks = [stuck("h", {holder: "dsh"}), stuck("l", {launched_by: "swarm", launch_finished_at: "2026-09-25T00:00:00Z"})];
  const res = decide({plan: stuckPlan(), tasks, routes: stuckRoutes, config, now: new Date()});
  assert.equal(recordsFor(res, "h"), 0);
  assert.equal(recordsFor(res, "l"), 0);
});

test("sliced_stuck: карточка из волны 0 при гейте — ровно одна запись", () => {
  const res = decide({plan: stuckPlan({waves: [["w"]]}), tasks: [stuck("w")], routes: stuckRoutes,
    config, now: new Date(), gate: {reason: "config"}});
  assert.equal(recordsFor(res, "w"), 1);
  assert.equal(res.needsOwner[0].reason, "sliced_stuck");
});

test("sliced_stuck: карточка из unscoped — ровно одна запись в needsOwner", () => {
  const res = decide({plan: stuckPlan({unscoped: ["u"]}), tasks: [stuck("u")], routes: stuckRoutes,
    config, now: new Date()});
  assert.equal(res.needsOwner.filter(n => n.id === "u").length, 1);
  assert.equal(recordsFor(res, "u"), 1);
});

test("sliced_stuck: кто-то бежит — пропуск sliced, без вопроса", () => {
  const runner = task("r", {launched_by: "swarm", launch_finished_at: "", launched_at: new Date().toISOString(), launch_driver: "swarm"});
  const res = decide({plan: stuckPlan(), tasks: [stuck("e"), runner], routes: stuckRoutes, config,
    now: new Date()});
  assert.deepEqual(res.skipped.filter(s => s.id === "e"), [{id: "e", reason: "sliced"}]);
  assert.ok(!res.needsOwner.some(n => n.id === "e"));
});

test("has_portions без portions_stuck в волне 0 — skipped sliced, как раньше", () => {
  const t = stuck("p", {portions_stuck: false});
  const res = decide({plan: stuckPlan({waves: [["p"]]}), tasks: [t], routes: stuckRoutes, config,
    now: new Date()});
  assert.deepEqual(res.skipped, [{id: "p", reason: "sliced"}]);
  assert.deepEqual(res.needsOwner, []);
});

test("portions_stuck у карточки скила в волне 0 — в выборку не попадает", () => {
  const t = stuck("s", {launch_driver: "skill", launch_route: "skill"});
  const res = decide({plan: stuckPlan({waves: [["s"]]}), tasks: [t], routes: stuckRoutes, config,
    now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, []);
  assert.equal(recordsFor(res, "s"), 0);
});

test("portions_stuck и needs_owner — ни вопроса, ни пропуска", () => {
  const res = decide({plan: stuckPlan({waves: [["n"]]}), tasks: [stuck("n", {needs_owner: true})],
    routes: stuckRoutes, config, now: new Date()});
  assert.equal(recordsFor(res, "n"), 0);
});

test("sliced_stuck: карточка и в unscoped, и в волне 0 — ровно одна запись (с гейтом и без)", () => {
  for (const gate of [{reason: "config"}, null]) {
    const res = decide({plan: stuckPlan({unscoped: ["x"], waves: [["x"]]}), tasks: [stuck("x")],
      routes: stuckRoutes, config, now: new Date(), gate});
    assert.equal(recordsFor(res, "x"), 1, JSON.stringify(gate));
    assert.deepEqual(res.needsOwner.map(n => [n.id, n.reason]), [["x", "unscoped"]]);
  }
});

test("рой берёт только маршруты роя: пайплайн скила, прямой харнесс, без маршрута — вне выборки", () => {
  const tasks = [task("a", {launch_route: "shiki-pow"}), task("b", {launch_route: "high-pipeline"}),
    task("c", {launch_route: "grok"}), task("d", {launch_route: null}), task("e", {launch_route: "grok"})];
  const routes = [{key: "shiki-pow", kind: "swarm", icon: "low"},
    {key: "high-pipeline", kind: "pipeline", icon: "low"},
    {key: "grok", kind: "direct", icon: "low"}];
  const plan = {waves: [["a", "b", "c", "d"]], cycles: [["b", "c"], ["a", "b"]], unroutable: ["d"], unscoped: ["e"],
    blocked: {e: ["b"]}};
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.cycles, []);
  assert.deepEqual(res.launch.map(l => l.id), ["a"]);
  assert.deepEqual(res.skipped, []);
  assert.deepEqual(res.needsOwner, []);
  assert.equal("unroutable" in res.report, false);
  assert.deepEqual(res.report.unscoped, []);
  assert.equal(res.report.blocked, 0);
});
