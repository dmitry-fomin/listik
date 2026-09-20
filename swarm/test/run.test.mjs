import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {Listik} from "../listik.mjs";
import {tick} from "../run.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const FAKE_BIN = path.join(here, "fixtures", "fake-listik.mjs");

function setupFake(responses) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-run-test-"));
  const callsFile = path.join(dir, "calls.log");
  const responsesFile = path.join(dir, "responses.json");
  fs.writeFileSync(responsesFile, JSON.stringify(responses));
  process.env.LISTIK_FAKE_CALLS_FILE = callsFile;
  process.env.LISTIK_FAKE_RESPONSES_FILE = responsesFile;
  return {
    dir,
    calls() {
      return fs.readFileSync(callsFile, "utf8").trim().split("\n").filter(Boolean)
        .map(l => JSON.parse(l));
    },
  };
}

function makeLog() {
  const lines = [];
  const stdout = [];
  return {
    lines, stdout,
    line: (t) => lines.push(t),
    action: (t) => { lines.push(t); stdout.push(t); },
    summary: (r) => { lines.push(JSON.stringify(r)); stdout.push(JSON.stringify(r)); },
  };
}

const baseConfig = {
  project: "proj", parallel: 3, weights: {xhigh: 3, high: 2, medium: 1, low: 1, xlow: 1, direct: 1},
  portBase: 5170, portCount: 100, dryRun: false, actor: "agent:listik-swarm",
};

const statusUp = {stdout: JSON.stringify({
  server: "up", bin_path: "/bin/listik", data_dir: "/data", db_path: "/data/listik.db", url: "http://x",
})};

function task(id, over = {}) {
  return {
    id, status: "open", holder: "", needs_owner: false, launched_by: "", launch_finished_at: "",
    launch_route: "r-" + id, worktree: "", labels: [], ...over,
  };
}

test("тик: план из трёх + без маршрута + без области, никто не бежит — полная последовательность", async () => {
  const tasks = [task("t1"), task("t2"), task("t3"), task("nr"), task("ns")];
  const plan = {
    project: "proj", waves: [["t1", "t2", "t3"]], cycles: [],
    unroutable: ["nr"], unscoped: ["ns"], blocked: {},
  };
  const routes = [{key: "r-t1", icon: "low"}, {key: "r-t2", icon: "low"}, {key: "r-t3", icon: "low"}];
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [["t1", "t2"]], removed: [], kept: 1})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    "needs-owner": {stdout: JSON.stringify({id: "x"})},
    worktree: {stdout: JSON.stringify({path: "/wt/x", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "x", labels: []})},
    set: {stdout: JSON.stringify({id: "x", labels: []})},
    launch: {stdout: JSON.stringify({id: "x", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.deepEqual(result.launched.sort(), ["t1", "t2", "t3"]);
  assert.deepEqual(result.needsOwner.sort(), ["nr", "ns"]);

  // требование 7: строки действий и пишущие исходы — в stdout, не только в файл.
  assert.ok(log.stdout.some(l => l.startsWith("waves --apply: записано 1 рёбер") && l.includes("t1 → t2")));
  assert.equal(log.stdout.filter(l => l.startsWith("needs-owner ")).length, 2);
  assert.equal(log.stdout.filter(l => l.startsWith("worktree ") && l.includes("→ /wt/x")).length, 3);
  assert.equal(log.stdout.filter(l => l.startsWith("set ") && l.includes("labels += port:")).length, 3);
  assert.equal(log.stdout.filter(l => l.startsWith("запуск ")).length, 3);

  const subs = calls().map(c => c.sub);
  assert.equal(subs[0], "status");
  assert.equal(subs[1], "waves");
  assert.equal(subs[2], "list");
  assert.equal(subs[3], "routes");
  assert.equal(subs.filter(s => s === "needs-owner").length, 2);
  const perTaskOrder = subs.slice(4).filter(s => s !== "needs-owner");
  assert.deepEqual(perTaskOrder, [
    "worktree", "show", "set", "launch",
    "worktree", "show", "set", "launch",
    "worktree", "show", "set", "launch",
  ]);

  const launchCalls = calls().filter(c => c.sub === "launch");
  const ports = launchCalls.map(c => {
    const i = c.argv.indexOf("--env");
    return c.argv[i + 1];
  });
  assert.deepEqual(ports.sort(), ["LISTIK_DEV_PORT=5170", "LISTIK_DEV_PORT=5171", "LISTIK_DEV_PORT=5172"]);
});

test("повторный тик: те же три бегут, needs_owner уже стоит — ни одного пишущего вызова", async () => {
  const tasks = [
    task("t1", {launched_by: "agent:listik-swarm", launch_finished_at: null}),
    task("nr", {needs_owner: true}),
    task("ns", {needs_owner: true}),
  ];
  const plan = {
    project: "proj", waves: [[]], cycles: [], unroutable: ["nr"], unscoped: ["ns"], blocked: {},
  };
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.deepEqual(result.launched, []);
  assert.deepEqual(result.needsOwner, []);
  const writeSubs = calls().map(c => c.sub).filter(s => ["needs-owner", "worktree", "set", "launch"].includes(s));
  assert.deepEqual(writeSubs, []);
});

test("одна закрыта (done), но launch_finished_at пуст, вторая — кандидат: launch нет", async () => {
  const tasks = [
    task("done1", {status: "done", launched_by: "agent:listik-swarm", launch_finished_at: null}),
    task("cand", {}),
  ];
  const plan = {project: "proj", waves: [["cand"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.deepEqual(result.launched, []);
  assert.deepEqual(result.running, ["done1"]);
  const writeSubs = calls().map(c => c.sub).filter(s => ["needs-owner", "worktree", "set", "launch"].includes(s));
  assert.deepEqual(writeSubs, []);
});

test("--dry-run: ни одного пишущего вызова, waves без --apply, [dry-run] строки в stdout", async () => {
  const tasks = [task("t1"), task("nr")];
  const plan = {
    project: "proj", waves: [["t1"]], cycles: [], unroutable: ["nr"], unscoped: [], blocked: {},
  };
  const routes = [{key: "r-t1", icon: "low"}];
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify(plan)},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, dryRun: true}, log);

  assert.deepEqual(result.launched, ["t1"]);
  const wavesCall = calls().find(c => c.sub === "waves");
  assert.ok(!wavesCall.argv.includes("--apply"));
  const writeSubs = calls().map(c => c.sub).filter(s => ["needs-owner", "worktree", "set", "launch"].includes(s));
  assert.deepEqual(writeSubs, []);

  // требование 7: строки [dry-run] тоже видны в stdout, не только в файле лога.
  assert.ok(log.stdout.some(l => l.startsWith("[dry-run] needs-owner nr:")));
  assert.ok(log.stdout.some(l => l === "[dry-run] worktree t1"));
  assert.ok(log.stdout.some(l => l.startsWith("[dry-run] set t1 labels += port:")));
  assert.ok(log.stdout.some(l => l.startsWith("[dry-run] launch t1 →")));
});

test("status down — ни одного вызова после status, serverDown=true", async () => {
  const responses = {status: {stdout: JSON.stringify({server: "down"})}};
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.equal(result.serverDown, true);
  assert.deepEqual(calls().map(c => c.sub), ["status"]);
});

test("status unauthorized — ни одного вызова после status, serverDown=true", async () => {
  const responses = {status: {stdout: JSON.stringify({server: "unauthorized", bin_path: "b", data_dir: "d"})}};
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.equal(result.serverDown, true);
  assert.deepEqual(calls().map(c => c.sub), ["status"]);
});

test("waves --apply конфликт цикла — cycles, пишущих вызовов нет", async () => {
  const responses = {
    status: statusUp,
    waves: [
      {exitCode: 1, stdout: JSON.stringify({error: {
        code: "conflict",
        message: "ресурсные рёбра не записаны: в зависимостях цикл a → b → a",
      }})},
      {stdout: JSON.stringify({cycles: [["a", "b"]], waves: [], unroutable: [], unscoped: [], blocked: {}})},
    ],
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.deepEqual(result.cycles, [["a", "b"]]);
  const writeSubs = calls().map(c => c.sub).filter(s => ["needs-owner", "worktree", "set", "launch"].includes(s));
  assert.deepEqual(writeSubs, []);
});
