import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {Listik} from "../listik.mjs";
import {tick} from "../run.mjs";
import {open as openLog} from "../log.mjs";

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

// --- порция c: надзор — тик revoke/launch/needs-owner ---

const supConfig = {...baseConfig, staleMinutes: 20, timeoutMinutes: 0, maxRestarts: 1};
const minsAgo = (m) => new Date(Date.now() - m * 60000).toISOString();

function runningTask(id, over = {}) {
  return task(id, {launched_by: "agent:listik-swarm", launch_finished_at: null, ...over});
}

test("надзор-тик: зависла с меткой port:5170 — show, revoke, launch (без set)", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(30), holder_at: minsAgo(30), labels: ["port:5170"],
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", launch_pid: 1, generation: 5})},
    launch: {stdout: JSON.stringify({id: "a", generation: 6})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "revoke", "launch"]);
  const revokeCall = calls().find(c => c.sub === "revoke");
  const noteIdx = revokeCall.argv.indexOf("--note");
  assert.equal(revokeCall.argv[noteIdx + 1], "рой: перезапуск — stale");
  const launchCall = calls().find(c => c.sub === "launch");
  const envIdx = launchCall.argv.indexOf("--env");
  assert.equal(launchCall.argv[envIdx + 1], "LISTIK_DEV_PORT=5170");
  assert.deepEqual(result.restarted, ["a"]);
});

test("надзор-тик: зависла без метки — show, revoke, show, set, launch", async () => {
  const tasks = [runningTask("a", {launched_at: minsAgo(30), holder_at: minsAgo(30)})];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: [
      {stdout: JSON.stringify({id: "a", events: []})},
      {stdout: JSON.stringify({id: "a", labels: []})},
    ],
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", launch_pid: 1, generation: 5})},
    set: {stdout: JSON.stringify({id: "a", labels: ["port:5170"]})},
    launch: {stdout: JSON.stringify({id: "a", generation: 6})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub),
    ["status", "waves", "list", "routes", "show", "revoke", "show", "set", "launch"]);
  const launchCall = calls().find(c => c.sub === "launch");
  const envIdx = launchCall.argv.indexOf("--env");
  assert.equal(launchCall.argv[envIdx + 1], "LISTIK_DEV_PORT=5170");
});

test("надзор-тик: revoke отвечает пустым launch_finished_at — launch не зовётся, needs-owner «процесс не снят»", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(30), holder_at: minsAgo(30), labels: ["port:5170"],
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: null, launch_pid: 42, generation: 5})},
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "revoke", "needs-owner"]);
  const noCall = calls().find(c => c.sub === "launch");
  assert.equal(noCall, undefined);
  assert.ok(log.stdout.some(l => l.startsWith("needs-owner a:")));
});

test("надзор-тик: revoke-перезапуск уже есть — revoke предел, needs-owner «зависла», launch нет", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(30), holder_at: minsAgo(30), labels: ["port:5170"],
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", events: [
      {kind: "revoke", actor: "agent:listik-swarm", ts: minsAgo(25),
        note: "рой: перезапуск — stale; запуск …, pid 1, процесс снят"},
    ]})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", generation: 6})},
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "revoke", "needs-owner"]);
  const revokeCall = calls().find(c => c.sub === "revoke");
  const noteIdx = revokeCall.argv.indexOf("--note");
  assert.equal(revokeCall.argv[noteIdx + 1], "рой: stale, предел перезапусков");
  assert.ok(log.stdout.some(l => l.startsWith("needs-owner a:")));
});

test("надзор-тик: упала — needs-owner «процесс задачи завершился», revoke/launch нет", async () => {
  const tasks = [task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z", launch_exit_code: 1,
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "needs-owner"]);
});

test("надзор-тик: упала с answer позже — revoke «перезапуск разрешён человеком», launch", async () => {
  const tasks = [task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z", launch_exit_code: 1,
    labels: ["port:5170"],
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", events: [
      {kind: "answer", actor: "dmitry", ts: "2026-01-01T01:00:00Z"},
    ]})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T02:00:00Z", generation: 6})},
    launch: {stdout: JSON.stringify({id: "a", generation: 7})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "revoke", "launch"]);
  const revokeCall = calls().find(c => c.sub === "revoke");
  const noteIdx = revokeCall.argv.indexOf("--note");
  assert.equal(revokeCall.argv[noteIdx + 1], "рой: перезапуск разрешён человеком");
});

test("надзор-тик: закрытая бежит дольше timeout — revoke «процесс закрытой задачи», launch/needs-owner нет", async () => {
  const tasks = [runningTask("a", {status: "done", launched_at: minsAgo(60), holder_at: minsAgo(60)})];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", generation: 3})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...supConfig, timeoutMinutes: 30}, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "revoke"]);
  const revokeCall = calls().find(c => c.sub === "revoke");
  const noteIdx = revokeCall.argv.indexOf("--note");
  assert.match(revokeCall.argv[noteIdx + 1], /процесс закрытой задачи/);
});

test("надзор-тик: бежит одна с needs_owner: true, молчит час — ни show, ни revoke, ни launch, ни needs-owner", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(60), holder_at: minsAgo(60), needs_owner: true,
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes"]);
});

test("надзор-тик: --dry-run на зависла — ни revoke, ни launch (событие всё же читаем show)", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(30), holder_at: minsAgo(30), labels: ["port:5170"], generation: 4,
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify(plan)},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...supConfig, dryRun: true}, log);

  const subs = calls().map(c => c.sub);
  assert.ok(!subs.includes("revoke"));
  assert.ok(!subs.includes("launch"));
  assert.ok(log.stdout.some(l => l.startsWith("[dry-run] revoke a:")));

  // регрессия: в dry-run `restart[]` должен нести {id, reason, generation}, как в рабочей
  // ветке — иначе сводка (log.mjs) печатает "undefined undefined → поколение undefined".
  assert.deepEqual(result.report.restart, [{id: "a", reason: "stale", generation: 4}]);

  // Прогоняем report через настоящий log.mjs (не через фейковый makeLog, который лишь
  // JSON.stringify'ит отчёт) — так регрессия форматирования действительно ловится.
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-summary-test-"));
  const realLog = openLog(logDir, "proj");
  const summaryText = realLog.summary(result.report);
  realLog.close();
  assert.match(summaryText, /перезапущено 1 \(a stale → поколение 4\)/);
  assert.ok(!summaryText.includes("undefined undefined"));
});

// --- порция c: выход цикла (main.mjs) при needs_owner: true ---

test("main: карточка needs_owner true с «рой: процесс задачи завершился…» — один show при выходе, «a — упала» в логе", async () => {
  const {main} = await import("../main.mjs");
  const tasks = [task("a", {needs_owner: true})];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
    show: {stdout: JSON.stringify({id: "a", comments: [
      {id: 1, author: "agent:listik-swarm", kind: "question", text:
        "рой: процесс задачи завершился (код 1, поколение 2), а карточка не закрыта — лог /log/a.log. " +
        "Разбери и сними флаг (listik needs-owner a --clear \"…\"), тогда рой перезапустит её новым поколением.",
        created_at: "2026-01-01T00:00:00Z"},
    ]})},
  };
  const {calls} = setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-log-"));
  const chunks = [];
  const origWrite = process.stdout.write.bind(process.stdout);
  process.stdout.write = (chunk, ...rest) => { chunks.push(String(chunk)); return true; };
  let code;
  try {
    code = await main(["--project", "proj", "--listik", FAKE_BIN, "--log-dir", logDir, "--interval", "1"]);
  } finally {
    process.stdout.write = origWrite;
  }
  assert.equal(code, 2);
  assert.equal(calls().filter(c => c.sub === "show").length, 1);
  const logPath = chunks[0].trim();
  const logText = fs.readFileSync(logPath, "utf8");
  assert.match(logText, /a — упала/);
});
