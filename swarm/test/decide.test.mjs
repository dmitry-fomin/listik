import {test} from "node:test";
import assert from "node:assert/strict";
import {decide, portOf, allocatePort} from "../decide.mjs";

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
