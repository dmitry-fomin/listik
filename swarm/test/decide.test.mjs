import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import {fileURLToPath} from "node:url";
import {decide, portOf, allocatePort, isFrozen, REJECTED_MARK, isSoftQuestion, defaultLine,
  openQuestion, fromEvents, fromComments, dueDefaults} from "../decide.mjs";
import {DEFAULT_QUESTION_TIMEOUT} from "../config.mjs";

const config = {parallel: 3, weights: {xhigh: 3, high: 2, medium: 1, low: 1, xlow: 1, direct: 1}};

function task(id, over = {}) {
  return {
    id, status: "open", holder: "", needs_owner: false, launched_by: "", launch_finished_at: "",
    launch_route: "route-" + id, worktree: "", labels: [], ...over,
  };
}

function routesFor(ids, icon) {
  return ids.map(id => ({key: "route-" + id, icon}));
}

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
  const routes = [{key: "route-a", icon: "xhigh"}, {key: "route-b", icon: "low"}, {key: "route-c", icon: "low"}];
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a"]);
  assert.deepEqual(res.skipped.map(s => s.id), ["b", "c"]);
  assert.ok(res.skipped.every(s => s.reason === "capacity"));
});

test("2б: xhigh=3, parallel 3, [low, low, xhigh] — два low, xhigh пропущен", () => {
  const ids = ["a", "b", "c"];
  const tasks = ids.map(id => task(id));
  const plan = {waves: [ids], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "route-a", icon: "low"}, {key: "route-b", icon: "low"}, {key: "route-c", icon: "xhigh"}];
  const res = decide({plan, tasks, routes, config, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a", "b"]);
  assert.deepEqual(res.skipped.map(s => s.id), ["c"]);
});

test("3: parallel 2, единственный кандидат xhigh (вес 3) — берём одного, oversized", () => {
  const tasks = [task("a")];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "route-a", icon: "xhigh"}];
  const res = decide({plan, tasks, routes, config: {...config, parallel: 2}, now: new Date()});
  assert.deepEqual(res.launch.map(l => l.id), ["a"]);
  assert.equal(res.report.reason, "oversized");
});

test("4: одна бегущая — launch пуст, batch_running, running содержит её", () => {
  const running = task("a", {launched_by: "listik", launch_finished_at: null});
  const candidate = task("b");
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
  const res = decide({plan, tasks, routes: [], config, now: new Date()});
  assert.deepEqual(res.running.map(t => t.id), ["a"]);
  assert.deepEqual(res.launch, []);
});

test("6: launch_finished_at непуст — не бегущая, и не кандидат (launched_by непуст)", () => {
  const t = task("a", {launched_by: "listik", launch_finished_at: "2026-01-01T00:00:00Z"});
  const tasks = [t];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: [], config, now: new Date()});
  assert.deepEqual(res.running, []);
  assert.deepEqual(res.launch, []);
});

test("7: кандидат с holder — в skipped held, не в launch", () => {
  const t = task("a", {holder: "dmitry"});
  const tasks = [t];
  const plan = {waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: [], config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, [{id: "a", reason: "held"}]);
});

test("8: needsOwner для unroutable/unscoped с needs_owner ложным, не для true, не для закрытых", () => {
  const openUnroutable = task("a");
  const flaggedUnroutable = task("b", {needs_owner: true});
  const openUnscoped = task("c");
  const closedUnroutable = task("d", {status: "done"});
  const tasks = [openUnroutable, flaggedUnroutable, openUnscoped, closedUnroutable];
  const plan = {
    waves: [[]], cycles: [], blocked: {},
    unroutable: ["a", "b", "d"], unscoped: ["c"],
  };
  const res = decide({plan, tasks, routes: [], config, now: new Date()});
  assert.deepEqual(res.needsOwner.map(n => n.id).sort(), ["a", "c"]);
  const a = res.needsOwner.find(n => n.id === "a");
  assert.equal(a.reason, "unroutable");
  assert.match(a.text, /Рой маршрут не выбирает никогда\.$/);
  const c = res.needsOwner.find(n => n.id === "c");
  assert.equal(c.reason, "unscoped");
  assert.match(c.text, /listik set c write_scope=<пути через запятую>\.$/);
});

test("9: cycles непуст — launch и needsOwner пусты", () => {
  const tasks = [task("a")];
  const plan = {waves: [["a"]], cycles: [["a", "b"]], unroutable: ["a"], unscoped: [], blocked: {}};
  const res = decide({plan, tasks, routes: [], config, now: new Date()});
  assert.deepEqual(res.cycles, [["a", "b"]]);
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.needsOwner, []);
});

test("10: задача из waves[0], которой нет в tasks — пропускается без ошибки", () => {
  const plan = {waves: [["ghost"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks: [], routes: [], config, now: new Date()});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, []);
});

test("11: маршрут без icon и маршрут не в routes — вес 1", () => {
  const a = task("a", {launch_route: "no-icon"});
  const b = task("b", {launch_route: "missing-route"});
  const tasks = [a, b];
  const plan = {waves: [["a", "b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "no-icon", icon: null}];
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

// --- порция c: надзор за бегущими и упавшими ---

const supConfig = {
  ...config, actor: "agent:listik-swarm", staleMinutes: 20, timeoutMinutes: 0, maxRestarts: 1,
  portBase: 5170, portCount: 100,
};
const minsAgo = (now, m) => new Date(now.getTime() - m * 60000).toISOString();

function runningTask(id, over = {}) {
  return task(id, {launched_by: "agent:listik-swarm", launch_finished_at: null, ...over});
}

function decideRunning(t, {plan, config: cfg = supConfig, now = new Date(), events} = {}) {
  const p = plan ?? {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  return decide({plan: p, tasks: [t], routes: [], config: cfg, now, events});
}

test("надзор: зависла (30 мин молчания > staleMinutes 20), port:5170, без revoke — restart stale", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 25), labels: ["port:5170"],
  });
  const res = decideRunning(t, {now});
  assert.deepEqual(res.restart, [{id: "a", reason: "stale", restarts: 0, generation: undefined, port: 5170}]);
  assert.deepEqual(res.report.stale, ["a"]);
});

test("надзор: та же, но holder_at 5 мин назад — не молчит, ничего", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 5), labels: ["port:5170"],
  });
  const res = decideRunning(t, {now});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
  assert.deepEqual(res.report.stale, []);
});

test("надзор: событие от чужого актора 2 мин назад — не stale; то же от роя (revoke) — stale", () => {
  const now = new Date();
  const base = {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  };
  const notStale = decideRunning(runningTask("a", base), {
    now, events: {a: [{kind: "stage", actor: "agent:claude", ts: minsAgo(now, 2)}]},
  });
  assert.equal(notStale.report.stale.includes("a"), false);

  const stillStale = decideRunning(runningTask("a", base), {
    now, events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 2), note: "рой: x"}]},
  });
  assert.equal(stillStale.report.stale.includes("a"), true);
});

test("надзор: holder_at пуст, launched_at 25 мин назад — stale", () => {
  const now = new Date();
  const t = runningTask("a", {launched_at: minsAgo(now, 25), labels: ["port:5170"]});
  const res = decideRunning(t, {now});
  assert.deepEqual(res.report.stale, ["a"]);
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
  const res = decideRunning(t, {now, events});
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
      now, events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25), note}]},
    });
    assert.equal(res.restart.length, 1, note);
    assert.equal(res.giveUp.length, 0, note);
  }
});

test("надзор: revoke от agent:claude — не считается в restarts", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const res = decideRunning(t, {
    now, events: {a: [{kind: "revoke", actor: "agent:claude", ts: minsAgo(now, 25),
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
    now, config: {...supConfig, actor: "Agent:Listik-Swarm", maxRestarts: 1},
    events: {a: [{kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(now, 25),
      note: "рой: перезапуск — stale"}]},
  });
  assert.equal(res.restart.length, 0);
  assert.equal(res.giveUp.length, 1);
});

test("надзор: зависшая без метки порта — свободный порт есть → restart; свободных нет → giveUp no_port", () => {
  const now = new Date();
  const t = runningTask("a", {launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30)});
  const resFree = decideRunning(t, {now});
  assert.equal(resFree.restart[0].port, 5170);

  const full = [];
  for (let p = 5170; p < 5270; p++) full.push(task(`o${p}`, {labels: [`port:${p}`]}));
  const res = decide({
    plan: {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}},
    tasks: [t, ...full], routes: [], config: supConfig, now,
  });
  assert.equal(res.restart.length, 0);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].reason, "no_port");
});

test("надзор: needs_owner true у зависшей — ни restart, ни giveUp, report.stale тоже пуст", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
    needs_owner: true,
  });
  const res = decideRunning(t, {now});
  assert.deepEqual(res.restart, []);
  assert.deepEqual(res.giveUp, []);
  assert.deepEqual(res.report.stale, []);
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

// --- порция a: заморозка (frozen-by:) и гейт ---

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
  const res = decide({plan, tasks, routes: [], config, now: new Date(), gate});
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
  const res = decide({plan, tasks, routes: [], config, now: new Date(), gate});
  assert.deepEqual(res.launch, []);
  assert.deepEqual(res.skipped, [{id: "a", reason: "gated"}]);
  assert.equal(res.report.reason, "config");
});

test("гейт вместе с надзором: кандидат волны gated, stale running restart, crashed отдельно", () => {
  const now = new Date();
  const candidate = task("b");
  const stale = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const crashedTask = task("c", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, needs_owner: false, generation: 3, launch_log: "/logs/c.log",
  });
  const tasks = [candidate, stale, crashedTask];
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "unmerged", ids: []};
  const res = decide({plan, tasks, routes: [], config: supConfig, now, gate});
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
  const candidate = task("b");
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const res = decide({plan, tasks: [crashedFlagged, candidate], routes: [], config: supConfig, now: new Date()});
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

// --- порция b: мягкий вопрос ---

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

test("надзор: бегущая с мягким вопросом, молчит 30 мин при staleMinutes 20 — restart stale", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
    needs_owner: true,
  });
  const events = {a: [{kind: "question", actor: "agent:fake", ts: minsAgo(now, 30), note: SOFT_Q}]};
  const res = decideRunning(t, {now, events});
  assert.equal(res.restart.length, 1);
  assert.equal(res.restart[0].reason, "stale");
  assert.deepEqual(res.report.stale, ["a"]);
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

// --- порция d: бюджет прогона ---

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

test("бюджет: budgetExhausted + зависшая — restart пуст, giveUp budget, порт не выделялся", () => {
  const now = new Date();
  const t = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
    launch_log: "/logs/a.log",
  });
  const res = decideRunning(t, {now, config: {...supConfig, budgetExhausted: true}});
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].id, "a");
  assert.equal(res.giveUp[0].reason, "budget");
  assert.equal(res.giveUp[0].cause, "stale");
  assert.equal(res.giveUp[0].port, undefined);
  assert.match(res.giveUp[0].text, /бюджет прогона исчерпан/);
  assert.match(res.giveUp[0].text, /зависла/);
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
  const candidate = task("b");
  const stale = runningTask("a", {
    launched_at: minsAgo(now, 30), holder_at: minsAgo(now, 30), labels: ["port:5170"],
  });
  const tasks = [candidate, stale];
  const plan = {waves: [["b"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const gate = {reason: "unmerged", ids: []};
  const res = decide({
    plan, tasks, routes: [], config: {...supConfig, budgetExhausted: true}, now, gate,
  });
  assert.deepEqual(res.launch, []);
  assert.ok(res.skipped.some(s => s.id === "b" && s.reason === "gated"));
  assert.deepEqual(res.restart, []);
  assert.equal(res.giveUp.length, 1);
  assert.equal(res.giveUp[0].reason, "budget");
  assert.equal(res.report.reason, "unmerged");
  assert.deepEqual(res.report.budget, {exhausted: true, launchesLeft: null});
});
