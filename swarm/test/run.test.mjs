import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {execFileSync} from "node:child_process";
import {Listik} from "../listik.mjs";
import {tick} from "../run.mjs";
import {open as openLog} from "../log.mjs";
import {acquireProjectLock, noteCycles, projectsDue, questionReason, waitingLine} from "../main.mjs";

// Маршрут роя для каждого id фикстур: карточки не роя рой не видит вовсе.
const SWARM_ROUTES = [..."abcdefghijklmnopqrstuvwxyz".split(""), ...Array.from({length: 21}, (_, i) => "t" + i), "nr", "ns", "frozen", "canc", "cand", "done-plain", "done-port", "done1", "halt1", "n1", "new", "running1"].map(id => ({key: "r-" + id, driver: "swarm", icon: "low"}));

// `git` может отсутствовать на машине судьи — тогда блок watch+barrier пропускается целиком.
let gitAvailable = true;
try {
  execFileSync("git", ["--version"], {stdio: "ignore"});
} catch {
  gitAvailable = false;
}
const gitConfigDir = gitAvailable ? fs.mkdtempSync(path.join(os.tmpdir(), "swarm-run-gitcfg-")) : null;
if (gitAvailable) {
  const gitConfigFile = path.join(gitConfigDir, "gitconfig");
  fs.writeFileSync(gitConfigFile, "");
  Object.assign(process.env, {
    GIT_CONFIG_GLOBAL: gitConfigFile,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_EDITOR: "true",
    GIT_TERMINAL_PROMPT: "0",
  });
}

function sh(cwd, ...args) {
  return execFileSync("git", args, {cwd, encoding: "utf8"});
}

function initRepo() {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), "swarm-run-repo-"));
  sh(repo, "init", "-q", "-b", "main");
  sh(repo, "config", "user.name", "Test");
  sh(repo, "config", "user.email", "test@example.com");
  fs.writeFileSync(path.join(repo, "README.md"), "start\n");
  sh(repo, "add", "README.md");
  sh(repo, "commit", "-q", "-m", "start");
  return repo;
}

function addWorktree(repo, id) {
  const wtPath = path.join(repo, ".worktrees", id);
  fs.mkdirSync(path.join(repo, ".worktrees"), {recursive: true});
  sh(repo, "worktree", "add", "-q", "-b", `task/${id}`, wtPath, "HEAD");
  return wtPath;
}

const gitTest = gitAvailable ? test : test.skip;

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
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
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
  assert.deepEqual(result.needsOwner.sort(), ["ns"]);  // nr без маршрута — без вопроса

  // требование 7: строки действий и пишущие исходы — в stdout, не только в файл.
  assert.ok(log.stdout.some(l => l.startsWith("waves --apply: записано 1 рёбер") && l.includes("t1 → t2")));
  assert.equal(log.stdout.filter(l => l.startsWith("needs-owner ")).length, 1);
  assert.equal(log.stdout.filter(l => l.startsWith("worktree ") && l.includes("→ /wt/x")).length, 3);
  assert.equal(log.stdout.filter(l => l.startsWith("set ") && l.includes("labels += port:")).length, 3);
  assert.equal(log.stdout.filter(l => l.startsWith("запуск ")).length, 3);

  const subs = calls().map(c => c.sub);
  assert.equal(subs[0], "status");
  assert.equal(subs[1], "waves");
  assert.equal(subs[2], "list");
  assert.equal(subs[3], "routes");
  assert.equal(subs.filter(s => s === "needs-owner").length, 1);
  const perTaskOrder = subs.slice(4).filter(s => s !== "needs-owner" && s !== "projects");
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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

test("замороженная в waves[0] — ни worktree, ни set, ни launch для неё", async () => {
  const tasks = [task("frozen", {labels: ["frozen-by:t1"]}), task("t1")];
  const plan = {
    project: "proj", waves: [["frozen", "t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {},
  };
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.deepEqual(result.launched, ["t1"]);
  const frozenArgv = calls().filter(c => c.argv.includes("frozen"));
  assert.deepEqual(frozenArgv, []);

  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-frozen-summary-"));
  const realLog = openLog(logDir, "proj");
  const summaryText = realLog.summary(result.report);
  realLog.close();
  assert.match(summaryText, /frozen заморожена/);
});

test("--dry-run: ни одного пишущего вызова, waves без --apply, [dry-run] строки в stdout", async () => {
  const tasks = [task("t1"), task("nr")];
  const plan = {
    project: "proj", waves: [["t1"]], cycles: [], unroutable: ["nr"], unscoped: [], blocked: {},
  };
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
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
  // карточка без маршрута (nr) — ни запуска, ни вопроса человеку.
  assert.ok(!log.stdout.some(l => l.startsWith("[dry-run] needs-owner nr:")));
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: [
      {exitCode: 1, stdout: JSON.stringify({error: {
        code: "conflict",
        message: "ресурсные рёбра не записаны: в зависимостях цикл a → b → a",
      }})},
      {stdout: JSON.stringify({cycles: [["a", "b"]], waves: [], unroutable: [], unscoped: [], blocked: {}})},
    ],
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: [{id: "a", status: "open", launch_route: "r-a", launch_driver: "swarm"}, {id: "b", status: "open", launch_route: "r-b", launch_driver: "swarm"}]})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", launch_pid: 1, generation: 5})},
    launch: {stdout: JSON.stringify({id: "a", generation: 6})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "projects", "revoke", "launch"]);
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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
    ["status", "waves", "list", "routes", "show", "projects", "revoke", "show", "set", "launch"]);
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: null, launch_pid: 42, generation: 5})},
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "projects", "revoke", "needs-owner"]);
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "projects", "revoke", "needs-owner"]);
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "projects", "needs-owner"]);
});

test("надзор-тик: упала с answer позже — revoke «перезапуск разрешён человеком», launch", async () => {
  const tasks = [task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z", launch_exit_code: 1,
    labels: ["port:5170"],
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "projects", "revoke", "launch"]);
  const revokeCall = calls().find(c => c.sub === "revoke");
  const noteIdx = revokeCall.argv.indexOf("--note");
  assert.equal(revokeCall.argv[noteIdx + 1], "рой: перезапуск разрешён человеком");
});

test("надзор-тик: закрытая бежит дольше timeout — revoke «процесс закрытой задачи», launch/needs-owner нет", async () => {
  const tasks = [runningTask("a", {status: "done", launched_at: minsAgo(60), holder_at: minsAgo(60)})];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", generation: 3})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...supConfig, timeoutMinutes: 30}, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "projects", "revoke"]);
  const revokeCall = calls().find(c => c.sub === "revoke");
  const noteIdx = revokeCall.argv.indexOf("--note");
  assert.match(revokeCall.argv[noteIdx + 1], /процесс закрытой задачи/);
});

test("надзор-тик: бежит одна с needs_owner: true без событий вопроса, молчит час — show один раз, без revoke/launch/needs-owner", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(60), holder_at: minsAgo(60), needs_owner: true,
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);

  assert.deepEqual(calls().map(c => c.sub), ["status", "waves", "list", "routes", "show", "projects"]);
});

const SOFT_Q = "Какой формат?\nпо умолчанию: JSON";
const DEFAULT_ANSWER = "рой: ответа не было 30 мин — действует вариант по умолчанию: JSON";

function crashedFlagged(over = {}) {
  return task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: minsAgo(50),
    launch_exit_code: 1, needs_owner: true, labels: ["port:5170"], ...over,
  });
}

test("надзор-тик: упавшая мягкая 40 мин — answer, повторный show, revoke ответ по умолчанию, launch", async () => {
  const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(40), note: SOFT_Q};
  const aEvent = {kind: "answer", actor: "agent:listik-swarm", ts: minsAgo(0), note: DEFAULT_ANSWER};
  const tasks = [crashedFlagged()];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: [
      {stdout: JSON.stringify({id: "a", events: [qEvent]})},
      {stdout: JSON.stringify({id: "a", events: [qEvent, aEvent]})},
    ],
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T02:00:00Z", generation: 6})},
    launch: {stdout: JSON.stringify({id: "a", generation: 7})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, supConfig, log);

  const recorded = calls();
  const subs = recorded.map(c => c.sub);
  const show1 = subs.indexOf("show");
  const answerIdx = subs.indexOf("needs-owner");
  const show2 = subs.indexOf("show", answerIdx);
  const revokeIdx = subs.indexOf("revoke");
  const launchIdx = subs.indexOf("launch");
  assert.ok(show1 >= 0 && show1 < answerIdx && answerIdx < show2 && show2 < revokeIdx && revokeIdx < launchIdx);
  assert.deepEqual(recorded[answerIdx].argv, [
    "needs-owner", "a", "--clear", DEFAULT_ANSWER, "--json", "--actor", "agent:listik-swarm",
  ]);
  assert.equal(recorded[revokeIdx].argv[recorded[revokeIdx].argv.indexOf("--note") + 1],
    "рой: перезапуск — ответ по умолчанию");
  assert.ok(!subs.includes("set"));
  assert.ok(!subs.includes("stage"));
  assert.deepEqual(result.defaults, ["a"]);
  assert.deepEqual(result.restarted, ["a"]);
  assert.ok(log.stdout.some(l => l === "ответ по умолчанию a: JSON"));

  const summaryText = summaryOf(result.report);
  assert.match(summaryText, /дефолт 1 \(a\)/);
});

test("надзор-тик: упавшая мягкая 40 мин, второй show без answer роя — revoke/launch нет", async () => {
  const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(40), note: SOFT_Q};
  const tasks = [crashedFlagged()];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: [
      {stdout: JSON.stringify({id: "a", events: [qEvent]})},
      {stdout: JSON.stringify({id: "a", events: [qEvent]})},
    ],
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, supConfig, log);
  const subs = calls().map(c => c.sub);
  assert.ok(subs.includes("needs-owner"));
  assert.ok(!subs.includes("revoke"));
  assert.ok(!subs.includes("launch"));
  assert.deepEqual(result.defaults, ["a"]);
  assert.deepEqual(result.restarted, []);
});

test("надзор-тик: вопрос 6 мин при дефолтном timeout 30 — автоответа нет", async () => {
  const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(6), note: SOFT_Q};
  const tasks = [crashedFlagged()];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: [qEvent]})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);
  assert.ok(!calls().some(c => c.sub === "needs-owner" && c.argv.includes("--clear")));
});

test("надзор-тик: --dry-run автоответ — нет needs-owner, строка [dry-run] answer", async () => {
  const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(40), note: SOFT_Q};
  const tasks = [crashedFlagged()];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify(plan)},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: [qEvent]})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...supConfig, dryRun: true}, log);
  assert.ok(!calls().some(c => c.sub === "needs-owner"));
  assert.ok(log.stdout.some(l => l.startsWith("[dry-run] answer a:")));
});

test("надзор-тик: done с needs_owner и port — show; без порта и launched_by — нет; cancelled — нет", async () => {
  const tasks = [
    task("done-port", {status: "done", needs_owner: true, labels: ["port:5170"]}),
    task("done-plain", {status: "done", needs_owner: true}),
    task("canc", {status: "cancelled", needs_owner: true, labels: ["port:5170"], launched_by: "x"}),
  ];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "done-port", events: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);
  const shows = calls().filter(c => c.sub === "show");
  assert.equal(shows.length, 1);
  assert.ok(shows[0].argv.includes("done-port"));
});

test("надзор-тик: упавшая, answer роя позже завершения — revoke ответ по умолчанию, launch", async () => {
  const tasks = [task("a", {
    launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    launch_exit_code: 1, labels: ["port:5170"],
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: [
      {kind: "answer", actor: "agent:listik-swarm", ts: "2026-01-01T01:00:00Z"},
    ]})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T02:00:00Z", generation: 6})},
    launch: {stdout: JSON.stringify({id: "a", generation: 7})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, supConfig, log);
  const recorded = calls();
  const revokeCall = recorded.find(c => c.sub === "revoke");
  assert.equal(revokeCall.argv[revokeCall.argv.indexOf("--note") + 1],
    "рой: перезапуск — ответ по умолчанию");
  const revokeIdx = recorded.findIndex(c => c.sub === "revoke");
  const launchIdx = recorded.findIndex(c => c.sub === "launch");
  assert.ok(revokeIdx >= 0 && launchIdx > revokeIdx);
});

test("надзор-тик: --dry-run на зависла — ни revoke, ни launch (событие всё же читаем show)", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(30), holder_at: minsAgo(30), labels: ["port:5170"], generation: 4,
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify(plan)},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
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
    code = await main(["--project", "proj", "--listik", FAKE_BIN, "--log-dir", logDir,
      "--interval", "1", "--exit-when-idle"]);
  } finally {
    process.stdout.write = origWrite;
  }
  assert.equal(code, 2);
  assert.equal(calls().filter(c => c.sub === "show").length, 1);
  const logPath = chunks[0].trim();
  const logText = fs.readFileSync(logPath, "utf8");
  assert.match(logText, /a — упала/);
});

test("main: без --exit-when-idle ждёт и запускает, когда карточка стала готова", {timeout: 15000}, async () => {
  const plan = {project: "proj", waves: [["a"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const waiting = task("a", {needs_owner: true});
  const ready = task("a");
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: [
      {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks: [waiting]})},
      {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks: [ready]})},
    ],
    routes: {stdout: JSON.stringify({ok: true, routes})},
    show: {stdout: JSON.stringify({id: "a", comments: [{
      author: "agent:listik-swarm", kind: "question",
      text: "рой: у задачи нет write_scope — какие файлы она правит?",
      created_at: "2026-01-01T00:00:00Z",
    }]})},
    worktree: {stdout: JSON.stringify({path: "/wt/a", branch: "b", status: "created"})},
    set: {stdout: JSON.stringify({id: "a", labels: []})},
    launch: {stdout: JSON.stringify({id: "a", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-stay-"));
  const {code, chunks} = await runMain(mainArgv(logDir, ["--max-launches", "1"]));
  assert.equal(code, 5);
  assert.equal(calls().filter(c => c.sub === "launch").length, 1);
  const logText = fs.readFileSync(chunks[0].trim(), "utf8");
  assert.match(logText, /жду: запустить нечего, открыто 1/);
  assert.match(logText, /a — без области/);
  assert.ok(!logText.includes("итог: закрыто"));
});

test("acquireProjectLock: живой pid отказывает, мёртвый забирается", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-lock-"));
  const release = acquireProjectLock(dir, "proj");
  assert.throws(() => acquireProjectLock(dir, "proj"), /уже запущен/);
  release();
  const lockPath = path.join(dir, "swarm-proj.pid");
  fs.writeFileSync(lockPath, "99999999\n");
  const releaseStale = acquireProjectLock(dir, "proj");
  assert.equal(fs.readFileSync(lockPath, "utf8").trim(), String(process.pid));
  releaseStale();
  assert.equal(fs.existsSync(lockPath), false);
});

// --- порция c: наблюдатель (watch) + барьер (runBarrier) внутри тика ---

function statusFor(dataDir) {
  return {stdout: JSON.stringify({
    server: "up", bin_path: "/bin/listik", data_dir: dataDir, db_path: `${dataDir}/listik.db`,
    url: "http://x",
  })};
}

for (const [label, swarm, expected] of [
  ["с моделью", {enabled: true, running: true, model: "glm-x"}, "model=glm-x"],
  ["без swarm", undefined, "model=-"],
]) {
  test(`тик: строка сервера показывает модель ${label}`, async () => {
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
    const status = JSON.parse(statusFor(dataDir).stdout);
    if (swarm) status.swarm = swarm;
    const responses = {
      status: {stdout: JSON.stringify(status)},
      waves: {stdout: JSON.stringify({waves: {project: "proj", waves: [], cycles: [], unroutable: [], unscoped: [], blocked: {}}, added: [], removed: [], kept: 0})},
      list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})},
      routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
      projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    };
    setupFake(responses);
    const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
    const log = makeLog();
    await tick(listik, {...baseConfig, logDir: fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-log-"))}, log);
    assert.ok(log.lines.some(line => line.includes(expected)), log.lines.join("\n"));
  });
}

gitTest("watch+barrier (а): running пуст — watch раньше comment MERGED_MARK, партия после слияния", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  fs.writeFileSync(path.join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");

  const tasks = [
    task("t1", {status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:5170"]}),
    task("t2", {}),
  ];
  const plan = {project: "proj", waves: [["t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify({integration: []}));
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-integration-log-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: {stdout: JSON.stringify({id: "t1", comments: [], write_scope: []})},
    comment: {stdout: JSON.stringify({id: "t1"})},
    worktree: {stdout: JSON.stringify({path: "/wt/t2", branch: "b", status: "created"})},
    set: {stdout: JSON.stringify({id: "t2", labels: []})},
    launch: {stdout: JSON.stringify({id: "t2", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...baseConfig, logDir}, log);

  const subs = calls().map(c => c.sub);
  assert.ok(subs.includes("watch"));
  assert.ok(subs.includes("comment"));

  const watchLine = log.lines.findIndex(l => l.startsWith("watch:"));
  const mergedLine = log.lines.findIndex(l => l.startsWith("влито "));
  const launchLine = log.lines.findIndex(l => l.startsWith("запуск "));
  assert.ok(watchLine >= 0 && mergedLine >= 0 && launchLine >= 0);
  assert.ok(watchLine < mergedLine, "watch раньше влито");
  assert.ok(mergedLine < launchLine, "влито раньше запуск");
});

gitTest("watch+barrier (л): разморозка в тике — comment UNFROZEN_MARK, worktree, launch по порядку",
  async () => {
    const repo = initRepo();
    const treeT1 = addWorktree(repo, "t1");
    fs.writeFileSync(path.join(treeT1, "a.txt"), "a\n");
    sh(treeT1, "add", "a.txt");
    sh(treeT1, "commit", "-q", "-m", "t1");
    sh(repo, "merge", "-q", "--ff-only", "task/t1");
    const sha = sh(repo, "rev-parse", "HEAD").trim();
    const mergedRecord = {sha, branch: "task/t1", base: sha, files: ["a.txt"], declared: [], outside: []};

    const tasks = [
      task("t1", {status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:5170"]}),
      task("t2", {status: "open", launch_route: "r-t2", labels: ["frozen-by:t1"]}),
    ];
    const plan = {project: "proj", waves: [["t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
    const routes = SWARM_ROUTES;
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
    fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify({integration: []}));
    const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-integration-log-"));
    const responses = {
      status: statusFor(dataDir),
      waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
      list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
      routes: {stdout: JSON.stringify({ok: true, routes})},
      projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
      watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
      show: [
        {stdout: JSON.stringify({id: "t1", comments: [
          {author: "agent:listik-swarm",
            text: `рой: влито: ${JSON.stringify(mergedRecord)}`,
            created_at: "2026-01-01T00:00:00Z"},
        ], write_scope: []})},
        {stdout: JSON.stringify({id: "t2", comments: [], labels: ["frozen-by:t1"]})}, // разморозка
        {stdout: JSON.stringify({id: "t2", labels: []})}, // run.mjs: метка порта перед launch
      ],
      comment: {stdout: JSON.stringify({id: "x"})},
      set: {stdout: JSON.stringify({id: "t2", labels: []})},
      worktree: {stdout: JSON.stringify({path: "/wt/t2", branch: "b", status: "created"})},
      launch: {stdout: JSON.stringify({id: "t2", generation: 1})},
    };
    const {calls} = setupFake(responses);
    const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
    const logDirReal = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-run-log-"));
    const realLog = openLog(logDirReal, "proj");
    await tick(listik, {...baseConfig, logDir}, realLog);
    realLog.close();
    const logText = fs.readFileSync(realLog.path, "utf8");

    const subArgs = calls().map(c => ({sub: c.sub, argv: c.argv}));
    const commentT2 = subArgs.findIndex(c => c.sub === "comment" && c.argv.includes("t2") &&
      c.argv.some(a => typeof a === "string" && a.startsWith("рой: разморожена:")));
    const worktreeT2 = subArgs.findIndex(c => c.sub === "worktree" && c.argv.includes("t2"));
    const launchT2 = subArgs.findIndex(c => c.sub === "launch" && c.argv.includes("t2"));

    assert.ok(commentT2 >= 0, "ожидался comment t2 c UNFROZEN_MARK");
    assert.ok(worktreeT2 >= 0, "ожидался worktree t2");
    assert.ok(launchT2 >= 0, "ожидался launch t2");
    assert.ok(commentT2 < worktreeT2, "comment t2 раньше worktree t2");
    assert.ok(worktreeT2 < launchT2, "worktree t2 раньше launch t2");

    assert.match(logText, /разморожено 1 \(t2\)/);
    assert.match(logText, /интеграция зелёная/);
  });

gitTest("watch+barrier (б): running непуст — барьер не зовётся, comment нет, HEAD не менялся", async () => {
  const repo = initRepo();
  const before = sh(repo, "rev-parse", "HEAD").trim();

  const tasks = [runningTask("running1", {
    labels: ["port:5170"], launched_at: new Date().toISOString(), holder_at: new Date().toISOString(),
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: {stdout: JSON.stringify({id: "running1", events: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, baseConfig, log);

  const subs = calls().map(c => c.sub);
  assert.ok(subs.includes("watch"));
  assert.ok(!subs.includes("comment"));
  assert.equal(sh(repo, "rev-parse", "HEAD").trim(), before);
});

gitTest("watch+barrier (в): swarm.json с ошибкой — лог = err.message, launch не вызван, reason config", async () => {
  const repo = initRepo();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), "{не json");

  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.ok(log.lines.some(l => l.startsWith("swarm.json:")));
  assert.equal(log.lines.filter(l => l.includes("swarm.json:")).length, 1);
  assert.deepEqual(result.launched, []);
  assert.equal(result.report.reason, "config");
  assert.ok(calls().map(c => c.sub).includes("watch"));
});

gitTest("watch+barrier (г): gate unmerged — launch не вызван, report.unmerged длины 1", async () => {
  const repo = initRepo();
  const tree = addWorktree(repo, "t1");
  fs.writeFileSync(path.join(tree, "a.txt"), "a\n");
  sh(tree, "add", "a.txt");
  sh(tree, "commit", "-q", "-m", "t1");
  fs.writeFileSync(path.join(tree, "untracked.txt"), "x\n");

  const tasks = [
    task("t1", {status: "done", worktree: tree, branch: "task/t1", labels: ["port:5170"]}),
    task("t2", {}),
  ];
  const plan = {project: "proj", waves: [["t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: {stdout: JSON.stringify({id: "t1", comments: [], write_scope: []})},
    "needs-owner": {stdout: JSON.stringify({id: "t1"})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.deepEqual(result.launched, []);
  assert.equal(result.report.unmerged.length, 1);
  assert.ok(!calls().map(c => c.sub).includes("launch"));
});

test("watch+barrier (д): plan.cycles непуст — watch/comment/merge не вызывались", async () => {
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({
      waves: {project: "proj", waves: [], cycles: [["a", "b"]], unroutable: [], unscoped: [], blocked: {}},
      added: [], removed: [], kept: 0,
    })},
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: [{id: "a", status: "open", launch_route: "r-a", launch_driver: "swarm"}, {id: "b", status: "open", launch_route: "r-b", launch_driver: "swarm"}]})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.deepEqual(result.cycles, [["a", "b"]]);
  const subs = calls().map(c => c.sub);
  assert.ok(!subs.includes("watch"));
  assert.ok(!subs.includes("comment"));
  assert.ok(!subs.includes("projects"));
});

test("watch+barrier (е): пустой path проекта — в argv нет watch", async () => {
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, baseConfig, log);

  const subs = calls().map(c => c.sub);
  assert.ok(!subs.includes("watch"));
  assert.ok(log.lines.some(l => l.includes("у проекта нет каталога")));
});

gitTest("watch+barrier (ж): dryRun непустой path — watch с --dry-run, лог watch:", async () => {
  const repo = initRepo();
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify(plan)},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...baseConfig, dryRun: true}, log);

  const watchCall = calls().find(c => c.sub === "watch");
  assert.ok(watchCall.argv.includes("--dry-run"));
  assert.ok(log.lines.some(l => l.startsWith("watch:")));
});

gitTest("watch+barrier (з): watch бросает ListikError — лог watch ошибка:, тик не падает, партия всё ещё запускается", async () => {
  const repo = initRepo();
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {exitCode: 1, stdout: JSON.stringify({error: {code: "cli", message: "boom"}})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.ok(log.lines.some(l => l.startsWith("watch ошибка:")));
  assert.notEqual(result.error, true);
  assert.deepEqual(result.launched, ["t1"]);
});

gitTest("watch+barrier (и): freeze ok:true — повторный list, t2 не в launch, в skipped frozen", async () => {
  const repo = initRepo();
  const tasksInitial = [task("t1", {}), task("t2", {})];
  const tasksAfter = [task("t1", {}), task("t2", {labels: ["frozen-by:t1"], launched_by: ""})];
  const plan = {project: "proj", waves: [["t1", "t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: [
      {stdout: JSON.stringify({total: tasksInitial.length, limit: 1000, offset: 0, tasks: tasksInitial})},
      {stdout: JSON.stringify({total: tasksAfter.length, limit: 1000, offset: 0, tasks: tasksAfter})},
    ],
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [
      {action: "freeze", task: "t2", owner: "t1", files: ["f.txt"], ok: true, generation: 1},
    ], probes: []})},
    "needs-owner": {stdout: JSON.stringify({id: "x"})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  const listCalls = calls().filter(c => c.sub === "list");
  assert.equal(listCalls.length, 2);
  assert.ok(!result.launched.includes("t2"));
  assert.ok(result.report.skipped.some(s => s.id === "t2" && s.reason === "frozen"));
  assert.ok(log.lines.some(l => l.startsWith("watch: заморожена t2")));
});

gitTest("watch+barrier (к): decisions ok:false без top-level error — не бросает, launch не гейтится", async () => {
  const repo = initRepo();
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {exitCode: 1, stdout: JSON.stringify({tasks: {}, decisions: [
      {action: "freeze", task: "t1", ok: false, error: "unsupported"},
    ], probes: []})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);

  assert.ok(log.lines.some(l => l.startsWith("watch: ошибка заморозки")));
  assert.deepEqual(result.launched, ["t1"]);
});

gitTest("watch+barrier: второй list после freeze бросает — тик не падает", async () => {
  const repo = initRepo();
  const tasksInitial = [task("t1", {}), task("t2", {})];
  const plan = {project: "proj", waves: [["t1", "t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: [
      {stdout: JSON.stringify({total: tasksInitial.length, limit: 1000, offset: 0, tasks: tasksInitial})},
      {exitCode: 1, stdout: JSON.stringify({error: {code: "cli", message: "boom"}})},
    ],
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [
      {action: "freeze", task: "t2", owner: "t1", files: ["f.txt"], ok: true, generation: 1},
    ], probes: []})},
    worktree: {stdout: JSON.stringify({path: "/wt/x", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.notEqual(result.error, true);
  assert.ok(log.lines.some(l => l.startsWith("list после watch ошибка:")));
});

gitTest("watch+barrier: running + swarm:halt — report.halt id, launch нет", async () => {
  const repo = initRepo();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify({integration: []}));
  const tasks = [
    runningTask("running1", {
      labels: ["port:5170"], launched_at: new Date().toISOString(),
      holder_at: new Date().toISOString(),
    }),
    task("halt1", {labels: ["swarm:halt"], launch_route: null}),
    task("n1", {}),
  ];
  const plan = {project: "proj", waves: [["n1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: {stdout: JSON.stringify({id: "running1", events: []})},
  };
  setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.equal(result.report.halt, "halt1");
  assert.equal(result.report.reason, "halt");
  assert.deepEqual(result.launched, []);
  assert.deepEqual(result.barrier.halt, ["halt1"]);
});

// --- порция a: сводка барьера, questionReason, waitingLine ---

function summaryOf(report) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-summary-only-"));
  const realLog = openLog(dir, "proj");
  const text = realLog.summary({
    project: "proj", waveSize: 0, running: [], launch: [], restart: [], crashed: [],
    giveUp: [], stopOnly: [], needsOwner: [], skipped: [], blocked: 0, wavesLeft: 0,
    ...report,
  });
  realLog.close();
  return text;
}

test("сводка: без новых полей — нули и прочерки", () => {
  const text = summaryOf({});
  assert.match(text, /влито 0 \(\) · не влиты 0 \(\) · не приняты 0 \(\) · дефолт 0 \(\) · разморожено 0 \(\) · интеграция — · стоп — · бюджет есть$/);
});

test("сводка: merged/integration red/halt", () => {
  const text = summaryOf({merged: ["a"], integration: "red", halt: "listik-x"});
  assert.match(text, /влито 1 \(a\)/);
  assert.match(text, /интеграция красная/);
  assert.match(text, /стоп listik-x · бюджет есть$/);
});

test("сводка: merged двух id через запятую", () => {
  const text = summaryOf({merged: ["a", "b"]});
  assert.match(text, /влито 2 \(a, b\)/);
});

test("questionReason: интеграционные тесты → стоп роя; не влита → не влита", () => {
  assert.equal(questionReason("рой: интеграционные тесты красные — разберись"), "стоп роя");
  assert.equal(questionReason("рой: не влита — конфликт слияния"), "не влита");
});

test("questionReason: мягкий вопрос — есть дефолт; жёсткий воркерский — вопрос воркера", () => {
  assert.equal(questionReason("Какой формат?\nпо умолчанию: JSON"), "вопрос воркера, есть дефолт");
  assert.equal(questionReason("Какой формат?"), "вопрос воркера");
});

test("сводка: rejected — не приняты N (ids)", () => {
  const text = summaryOf({rejected: ["a"]});
  assert.match(text, /не приняты 1 \(a\)/);
});

test("сводка: дефолт 1 (a)", () => {
  const text = summaryOf({defaults: ["a"]});
  assert.match(text, /дефолт 1 \(a\)/);
});

test("questionReason: отклонена раньше не влита → не принята", () => {
  assert.equal(questionReason("рой: не влита — отклонена 2 раз: тесты красные"), "не принята");
  assert.equal(questionReason("рой: не влита — в дереве незакоммиченные правки"), "не влита");
});

test("waitingLine: frozen → заморожена, gated → гейт, held по-прежнему держит другой", () => {
  const result = {report: {skipped: [
    {id: "a", reason: "frozen"}, {id: "b", reason: "gated"}, {id: "c", reason: "held"},
    {id: "d", reason: "capacity"},
  ]}, open: []};
  const line = waitingLine(result);
  assert.match(line, /a заморожена/);
  assert.match(line, /b гейт/);
  assert.match(line, /c держит другой/);
  assert.match(line, /d не влезла в партию/);
});

gitTest("тик: барьер отклоняет единственную закрытую — list+show после comment, revoke+launch в том же тике",
  async () => {
    const repo = initRepo();
    const treeT1 = addWorktree(repo, "t1");
    fs.writeFileSync(path.join(treeT1, "a.txt"), "a\n");
    sh(treeT1, "add", "a.txt");
    sh(treeT1, "commit", "-q", "-m", "t1");

    const doneTask = task("t1", {
      status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:5170"],
      launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    });
    const openTask = task("t1", {
      status: "open", worktree: treeT1, branch: "task/t1", labels: ["port:5170"],
      launched_by: "agent:listik-swarm", launch_finished_at: "2026-01-01T00:00:00Z",
    });
    const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
    fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify({
      integration: [],
      verify: [[process.execPath, "-e", "process.exit(1)"]],
    }));
    const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-verify-log-"));
    const responses = {
      status: statusFor(dataDir),
      waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
      list: [
        {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks: [doneTask]})},
        {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks: [openTask]})},
      ],
      routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
      projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
      watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
      show: [
        {stdout: JSON.stringify({id: "t1", comments: [], write_scope: [], labels: ["port:5170"]})},
        {stdout: JSON.stringify({
          id: "t1",
          events: [{
            kind: "comment", actor: "agent:listik-swarm", ts: "2026-01-01T01:00:00Z",
            note: "рой: не принята: {\"reason\":\"red\"}",
          }],
        })},
      ],
      comment: {stdout: JSON.stringify({id: "t1"})},
      set: {stdout: JSON.stringify({id: "t1"})},
      revoke: {stdout: JSON.stringify({
        id: "t1", launch_finished_at: "2026-01-01T02:00:00Z", launch_pid: 1, generation: 2,
      })},
      launch: {stdout: JSON.stringify({id: "t1", generation: 3})},
    };
    const {calls} = setupFake(responses);
    const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
    const log = makeLog();
    const result = await tick(listik, {...baseConfig, logDir}, log);

    const recorded = calls();
    const subs = recorded.map(c => c.sub);
    assert.ok(subs.filter(s => s === "list").length >= 2, "ожидался второй list");
    const commentIdx = recorded.findIndex(c => c.sub === "comment");
    assert.ok(commentIdx >= 0, "ожидался comment отклонения");
    const showAfter = recorded.findIndex((c, i) => i > commentIdx && c.sub === "show" && c.argv.includes("t1"));
    assert.ok(showAfter > commentIdx, "show t1 после comment отклонения");
    const revokeCall = recorded.find(c => c.sub === "revoke");
    assert.ok(revokeCall);
    assert.equal(
      revokeCall.argv[revokeCall.argv.indexOf("--note") + 1],
      "рой: перезапуск — не принята (верификатор)",
    );
    assert.ok(subs.includes("launch"));
    assert.ok(!subs.includes("needs-owner"));
    assert.deepEqual(result.restarted, ["t1"]);
    assert.deepEqual(result.barrier.rejected, ["t1"]);
  });

gitTest("надзор-тик: swarm.json question_timeout 5, вопрос 6 мин — текст 5 мин", async () => {
  const repo = initRepo();
  const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(6), note: SOFT_Q};
  const answerText = "рой: ответа не было 5 мин — действует вариант по умолчанию: JSON";
  const aEvent = {kind: "answer", actor: "agent:listik-swarm", ts: minsAgo(0), note: answerText};
  const tasks = [crashedFlagged()];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify({
    integration: [], question_timeout: 5,
  }));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: [
      {stdout: JSON.stringify({id: "a", events: [qEvent]})},
      {stdout: JSON.stringify({id: "a", events: [qEvent, aEvent]})},
    ],
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T02:00:00Z", generation: 6})},
    launch: {stdout: JSON.stringify({id: "a", generation: 7})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...supConfig, logDir: fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-qto-"))}, log);
  const answerCall = calls().find(c => c.sub === "needs-owner" && c.argv.includes("--clear"));
  assert.ok(answerCall);
  assert.equal(answerCall.argv[answerCall.argv.indexOf("--clear") + 1], answerText);
});

// --- порция d: бюджет прогона ---

test("бюджет-тик: без runState — launch как раньше, budget.exhausted false", async () => {
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.equal(result.budget.exhausted, false);
  assert.deepEqual(result.launched, ["t1"]);
  assert.ok(calls().some(c => c.sub === "launch"));
});

test("бюджет-тик: runState 2 часа назад, budgetMinutes 60 — launch нет, exhausted", async () => {
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const runState = {startedAt: new Date(Date.now() - 2 * 60 * 60 * 1000), launches: 0};
  const result = await tick(listik, {...baseConfig, budgetMinutes: 60}, log, runState);
  assert.equal(result.budget.exhausted, true);
  assert.equal(result.budget.launches, 0);
  assert.deepEqual(result.launched, []);
  assert.ok(result.report.skipped.some(s => s.id === "t1" && s.reason === "budget"));
  assert.ok(log.lines.some(l => l.startsWith("бюджет: минут")));
  assert.ok(!calls().some(c => c.sub === "launch"));
});

test("бюджет-тик: runState.launches 1, maxLaunches 1 — launch нет, exhausted", async () => {
  const tasks = [task("t1")];
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const runState = {startedAt: new Date(), launches: 1};
  const result = await tick(listik, {...baseConfig, maxLaunches: 1}, log, runState);
  assert.equal(result.budget.exhausted, true);
  assert.deepEqual(result.launched, []);
  assert.ok(!calls().some(c => c.sub === "launch"));
});

test("бюджет-тик: maxLaunches 2, launches 1, три кандидата — ровно один launch", async () => {
  const tasks = [task("t1"), task("t2"), task("t3")];
  const plan = {
    project: "proj", waves: [["t1", "t2", "t3"]], cycles: [], unroutable: [], unscoped: [], blocked: {},
  };
  const routes = [
    {key: "r-t1", kind: "swarm", icon: "low"}, {key: "r-t2", kind: "swarm", icon: "low"}, {key: "r-t3", kind: "swarm", icon: "low"},
  ];
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 3, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    worktree: {stdout: JSON.stringify({path: "/wt/x", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "x", labels: []})},
    set: {stdout: JSON.stringify({id: "x", labels: []})},
    launch: {stdout: JSON.stringify({id: "x", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const runState = {startedAt: new Date(), launches: 1};
  const result = await tick(listik, {...baseConfig, maxLaunches: 2}, log, runState);
  assert.equal(result.budget.exhausted, false);
  assert.equal(result.launched.length, 1);
  assert.equal(calls().filter(c => c.sub === "launch").length, 1);
});

test("бюджет-тик: exhausted + зависшая с портом — revoke бюджет, needs-owner, launch нет", async () => {
  const tasks = [runningTask("a", {
    launched_at: minsAgo(30), holder_at: minsAgo(30), labels: ["port:5170"],
    launch_log: "/logs/a.log",
  })];
  const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    show: {stdout: JSON.stringify({id: "a", events: []})},
    revoke: {stdout: JSON.stringify({id: "a", launch_finished_at: "2026-01-01T00:00:00Z", generation: 5})},
    "needs-owner": {stdout: JSON.stringify({id: "a"})},
    launch: {stdout: JSON.stringify({id: "a", generation: 6})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const runState = {startedAt: new Date(), launches: 1};
  await tick(listik, {...supConfig, maxLaunches: 1}, log, runState);
  const recorded = calls();
  assert.ok(!recorded.some(c => c.sub === "launch"));
  const revokeCall = recorded.find(c => c.sub === "revoke");
  assert.ok(revokeCall);
  assert.equal(
    revokeCall.argv[revokeCall.argv.indexOf("--note") + 1],
    "рой: бюджет прогона исчерпан — процесс снят (зависла)",
  );
  const noCall = recorded.find(c => c.sub === "needs-owner");
  assert.ok(noCall);
  const text = noCall.argv.find((a, i) => noCall.argv[i - 1] !== "--note" && typeof a === "string"
    && a.includes("бюджет прогона исчерпан"))
    || noCall.argv[noCall.argv.length - 2];
  assert.match(String(text), /бюджет прогона исчерпан/);
});

gitTest("бюджет-тик: exhausted + закрытая с деревом — барьер вливает, launch нет", async () => {
  const repo = initRepo();
  const treeT1 = addWorktree(repo, "t1");
  fs.writeFileSync(path.join(treeT1, "a.txt"), "a\n");
  sh(treeT1, "add", "a.txt");
  sh(treeT1, "commit", "-q", "-m", "t1");

  const tasks = [
    task("t1", {status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:5170"]}),
    task("t2", {}),
  ];
  const plan = {project: "proj", waves: [["t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify({integration: []}));
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-budget-log-"));
  const responses = {
    status: statusFor(dataDir),
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: {stdout: JSON.stringify({id: "t1", comments: [], write_scope: []})},
    comment: {stdout: JSON.stringify({id: "t1"})},
    worktree: {stdout: JSON.stringify({path: "/wt/t2", branch: "b", status: "created"})},
    set: {stdout: JSON.stringify({id: "t2", labels: []})},
    launch: {stdout: JSON.stringify({id: "t2", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const runState = {startedAt: new Date(Date.now() - 2 * 60 * 60 * 1000), launches: 0};
  const result = await tick(listik, {...baseConfig, logDir, budgetMinutes: 60}, log, runState);
  assert.ok(result.barrier.merged.includes("t1"));
  assert.deepEqual(result.launched, []);
  assert.ok(!calls().some(c => c.sub === "launch"));
  assert.equal(result.budget.exhausted, true);
});

test("waitingLine: skipped budget → id бюджет", () => {
  const line = waitingLine({report: {skipped: [{id: "a", reason: "budget"}]}, open: []});
  assert.match(line, /a бюджет/);
});

test("questionReason: бюджет прогона исчерпан → бюджет, даже если в скобках зависла", () => {
  assert.equal(
    questionReason("рой: бюджет прогона исчерпан — перезапуск не делаю (зависла). Разбери лог /x"),
    "бюджет",
  );
});

test("сводка: budget exhausted → бюджет исчерпан", () => {
  const text = summaryOf({budget: {exhausted: true, launchesLeft: 0}});
  assert.match(text, /бюджет исчерпан$/);
});

function mainArgv(logDir, extra) {
  return ["--project", "proj", "--listik", FAKE_BIN, "--log-dir", logDir, "--interval", "0.05",
    ...extra];
}

async function runMain(argv) {
  const {main} = await import("../main.mjs");
  const chunks = [];
  const origWrite = process.stdout.write.bind(process.stdout);
  process.stdout.write = (chunk, ...rest) => { chunks.push(String(chunk)); return true; };
  let code;
  try {
    code = await main(argv);
  } finally {
    process.stdout.write = origWrite;
  }
  return {code, chunks};
}

test("main: --max-launches 1, два кандидата — код 5, launch один раз", {timeout: 15000}, async () => {
  const plan = {
    project: "proj", waves: [["t1", "t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {},
  };
  const routes = SWARM_ROUTES;
  const openBoth = [task("t1"), task("t2")];
  const t1Done = [task("t1", {status: "done"}), task("t2")];
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: [
      {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: openBoth})},
      {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: t1Done})},
    ],
    routes: {stdout: JSON.stringify({ok: true, routes})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: [], events: [], comments: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-budget-main-"));
  const {code, chunks} = await runMain(mainArgv(logDir, ["--max-launches", "1"]));
  assert.equal(code, 5);
  assert.equal(calls().filter(c => c.sub === "launch").length, 1);
  const logPath = chunks[0].trim();
  const logText = fs.readFileSync(logPath, "utf8");
  assert.match(logText, /итог: бюджет исчерпан — минут .+ из ∞, запусков 1 из 1/);
});

test("main: --max-launches 1, во втором тике running — цикла продолжается, потом код 5",
  {timeout: 15000}, async () => {
    const plan = {
      project: "proj", waves: [["t1", "t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {},
    };
    const routes = SWARM_ROUTES;
    const openBoth = [task("t1"), task("t2")];
    const t1Running = [
      task("t1", {launched_by: "agent:listik-swarm", launch_finished_at: null, labels: ["port:5170"]}),
      task("t2"),
    ];
    const t1Done = [task("t1", {status: "done"}), task("t2")];
    const responses = {
      status: statusUp,
      projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
      waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
      list: [
        {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: openBoth})},
        {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: t1Running})},
        {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: t1Done})},
      ],
      routes: {stdout: JSON.stringify({ok: true, routes})},
      worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
      show: {stdout: JSON.stringify({id: "t1", labels: ["port:5170"], events: [], comments: []})},
      set: {stdout: JSON.stringify({id: "t1", labels: []})},
      launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
    };
    const {calls} = setupFake(responses);
    const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-budget-run-"));
    const {code} = await runMain(mainArgv(logDir, ["--max-launches", "1"]));
    assert.equal(code, 5);
    assert.equal(calls().filter(c => c.sub === "launch").length, 1);
    assert.ok(calls().filter(c => c.sub === "list").length >= 3);
  });

test("main: --max-launches 1, один кандидат после done — код 0", {timeout: 15000}, async () => {
  const plan = {project: "proj", waves: [["t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: [
      {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks: [task("t1")]})},
      {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks: [task("t1", {status: "done"})]})},
    ],
    routes: {stdout: JSON.stringify({ok: true, routes})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: [], events: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-budget-zero-"));
  const {code} = await runMain(mainArgv(logDir, ["--max-launches", "1"]));
  assert.equal(code, 0);
});

test("main: --once --max-launches 1, два кандидата — один launch, код 0", {timeout: 10000}, async () => {
  const plan = {
    project: "proj", waves: [["t1", "t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {},
  };
  const routes = SWARM_ROUTES;
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: [task("t1"), task("t2")]})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "t1", labels: []})},
    set: {stdout: JSON.stringify({id: "t1", labels: []})},
    launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
  };
  const {calls} = setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-budget-once-"));
  const {code} = await runMain(mainArgv(logDir, ["--max-launches", "1", "--once"]));
  assert.equal(code, 0);
  assert.equal(calls().filter(c => c.sub === "launch").length, 1);
});

test("сводка: откаты без новых полей — нули перед влито", () => {
  const text = summaryOf({});
  const frag = "откаты 0 () · на откаты 0 мин · по пределу 0 ()";
  assert.ok(text.includes(frag));
  assert.ok(text.indexOf(frag) < text.indexOf("влито"));
  assert.equal(
    text.slice(text.indexOf("влито")),
    "влито 0 () · не влиты 0 () · не приняты 0 () · дефолт 0 () · разморожено 0 () · " +
      "интеграция — · стоп — · бюджет есть",
  );
});

test("сводка: откаты, минуты и парковка по пределу", () => {
  const text = summaryOf({rollbacks: ["a", "b"], rollbackMinutes: 17, parked: ["b"]});
  assert.ok(text.includes("откаты 2 (a, b) · на откаты 17 мин · по пределу 1 (b)"));
});

test("questionReason: предел откатов", () => {
  assert.equal(
    questionReason("рой: предел откатов — задача заморожена 3 раз …"),
    "предел откатов",
  );
});

// --- порция b: предел откатов в тике ---

const FREEZE_AT = "2026-01-01T00:10:00.000Z";
const LAUNCH_AT = "2026-01-01T00:00:00.000Z";

function rollbackPlan(wave) {
  return {project: "proj", waves: [wave], cycles: [], unroutable: [], unscoped: [], blocked: {}};
}

function frozenT2(over = {}) {
  return task("t2", {
    launched_by: "", launch_finished_at: "", needs_owner: false,
    labels: ["frozen-by:t1"], ...over,
  });
}

function freezeMark(at, generation, files = ["a.txt"]) {
  return {
    author: "agent:listik-swarm",
    kind: "journal",
    text: "рой: заморожена: " + JSON.stringify({
      owner: "t1", files, worktree: "/wt/t2", branch: "task/t2", generation, next: "wait",
    }),
    created_at: at,
  };
}

function launchMark(at, generation) {
  return {
    author: "agent:listik",
    kind: "journal",
    text: `автостарт: маршрут r-t2, pid 1, лог /tmp/t2.log, поколение ${generation}, запуск d1`,
    created_at: at,
  };
}

// Последняя заморозка — в FREEZE_AT, журнал запуска поколения lastGen−1 ровно за 10 минут.
function freezeCard(count, {needs_owner = false, lastGen = count, withLaunch = true} = {}) {
  const comments = [];
  if (withLaunch) comments.push(launchMark(LAUNCH_AT, lastGen - 1));
  for (let i = 0; i < count; i++) {
    const gen = lastGen - (count - 1 - i);
    const at = new Date(Date.parse(FREEZE_AT) - (count - 1 - i) * 60000).toISOString();
    comments.push(freezeMark(at, gen));
  }
  return {id: "t2", needs_owner, comments, labels: ["frozen-by:t1"], write_scope: [], events: []};
}

function freezeDecision(over = {}) {
  return {action: "freeze", task: "t2", owner: "t1", files: ["a.txt"], ok: true, generation: 4, ...over};
}

function watchOf(decisions) {
  return {stdout: JSON.stringify({tasks: {}, decisions, probes: []})};
}

function showOut(card) {
  return {stdout: JSON.stringify(card)};
}

function prepareRollback({json, tasks, plan, routes = SWARM_ROUTES, watch, show, list, dryRun = false, extra = {}}) {
  const repo = initRepo();
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), typeof json === "string" ? json : JSON.stringify(json));
  const wavesBody = dryRun ? plan : {waves: plan, added: [], removed: [], kept: 0};
  const responses = {
    status: statusFor(dataDir),
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    waves: {stdout: JSON.stringify(wavesBody)},
    list: list || {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    watch,
    ...extra,
  };
  if (show) responses.show = show;
  return {repo, dataDir, responses};
}

async function tickPrepared(responses, configExtra = {}) {
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, ...configExtra}, log);
  return {calls, log, result};
}

function needsOwnerText(calls, id) {
  const call = calls().find(c => c.sub === "needs-owner" && c.argv.includes(id));
  if (!call) return null;
  return call.argv[call.argv.indexOf(id) + 1];
}

gitTest("предел (а): три заморозки при max_freezes 2 — парковка, повтор тика без заморозки молчит",
  async () => {
    const tasks = [frozenT2()];
    const plan = rollbackPlan(["t2"]);
    const {responses} = prepareRollback({
      json: {integration: [], max_freezes: 2},
      tasks, plan,
      watch: [
        watchOf([freezeDecision()]),
        watchOf([]),
      ],
      show: showOut(freezeCard(3, {lastGen: 4})),
      extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
    });
    const {calls, log, result} = await tickPrepared(responses);

    const text = needsOwnerText(calls, "t2");
    assert.ok(text, "ожидался needs-owner t2");
    assert.ok(text.startsWith("рой: предел откатов"));
    assert.ok(text.includes("t1"));
    assert.ok(text.includes("a.txt"));
    assert.ok(text.includes("max_freezes = 2"));
    assert.ok(text.includes("listik needs-owner t2 --clear"));

    const recorded = calls();
    const watchIdx = recorded.findIndex(c => c.sub === "watch");
    const showIdx = recorded.findIndex(c => c.sub === "show" && c.argv.includes("t2"));
    const parkIdx = recorded.findIndex(c => c.sub === "needs-owner" && c.argv.includes("t2"));
    assert.ok(watchIdx >= 0 && showIdx > watchIdx && parkIdx > showIdx, "show t2 после watch и до needs-owner");
    assert.ok(log.lines.some(l => l.includes(
      "откат t2 (владелец t1): заморозка 3 из 2 в окне, всего 3, ~10 мин")));
    assert.ok(log.lines.some(l => l.includes("needs-owner t2: freeze_limit")));
    assert.deepEqual(result.rollbacks, [
      {id: "t2", owner: "t1", minutes: 10, count: 3, total: 3, parked: true},
    ]);
    assert.deepEqual(result.report.parked, ["t2"]);
    assert.equal(result.report.rollbackMinutes, 10);
    assert.ok(result.needsOwner.includes("t2"));
    assert.ok(!recorded.some(c => ["revoke", "launch", "set"].includes(c.sub) && c.argv.includes("t2")));
    const summaryText = summaryOf(result.report);
    assert.ok(summaryText.includes("откаты 1 (t2) · на откаты 10 мин · по пределу 1 (t2)"));

    const again = await tick(new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5}),
      baseConfig, log);
    assert.equal(calls().filter(c => c.sub === "needs-owner").length, 1);
    assert.equal(calls().filter(c => c.sub === "show").length, 2);
    assert.deepEqual(again.rollbacks, []);
  });

gitTest("предел (б): две заморозки при пороге 2 — needs-owner не вызывается", async () => {
  const tasks = [frozenT2()];
  const {responses} = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan: rollbackPlan(["t2"]),
    watch: watchOf([freezeDecision()]),
    show: showOut(freezeCard(2)),
    extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
  });
  const {calls, log, result} = await tickPrepared(responses);
  assert.equal(calls().filter(c => c.sub === "needs-owner").length, 0);
  assert.equal(result.rollbacks[0].parked, false);
  assert.equal(result.rollbacks[0].count, 2);
  assert.deepEqual(result.report.parked, []);
  assert.ok(log.lines.some(l => l.includes("откат t2 (владелец t1): заморозка 2 из 2")));
});

gitTest("предел (в): после парковки t2 запускается t3, gate как без парковки", async () => {
  const tasks = [frozenT2(), task("t3")];
  const {responses} = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan: rollbackPlan(["t2", "t3"]),
    routes: [{key: "r-t3", kind: "swarm", icon: "low"}],
    watch: watchOf([freezeDecision()]),
    show: [showOut(freezeCard(3, {lastGen: 4})), showOut({id: "t3", labels: []})],
    extra: {
      "needs-owner": {stdout: JSON.stringify({id: "t2"})},
      worktree: {stdout: JSON.stringify({path: "/wt/t3", branch: "b", status: "created"})},
      set: {stdout: JSON.stringify({id: "t3", labels: []})},
      launch: {stdout: JSON.stringify({id: "t3", generation: 1})},
    },
  });
  const {calls, result} = await tickPrepared(responses);
  assert.ok(calls().some(c => c.sub === "launch" && c.argv.includes("t3")));
  assert.ok(!calls().some(c => ["launch", "revoke", "set"].includes(c.sub) && c.argv.includes("t2")));
  assert.equal(result.report.gate, null);
  assert.deepEqual(result.report.parked, ["t2"]);
});

gitTest("предел (г): ok false и report — ни show, ни needs-owner, rollbacks пуст", async () => {
  const tasks = [frozenT2()];
  const {responses} = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan: rollbackPlan([]),
    watch: watchOf([
      {action: "freeze", task: "t2", owner: "t1", files: ["a.txt"], ok: false, error: "x"},
      {action: "report", task: "t3", owner: "t1", files: ["b.txt"]},
    ]),
  });
  const {calls, result} = await tickPrepared(responses);
  assert.equal(calls().filter(c => c.sub === "show").length, 0);
  assert.equal(calls().filter(c => c.sub === "needs-owner").length, 0);
  assert.deepEqual(result.rollbacks, []);
});

gitTest("предел (д): max_freezes 0 паркует; needs_owner уже стоит — второй вопрос не пишется", async () => {
  const parked = prepareRollback({
    json: {integration: [], max_freezes: 0},
    tasks: [frozenT2()], plan: rollbackPlan(["t2"]),
    watch: watchOf([freezeDecision({generation: 1})]),
    show: showOut(freezeCard(1, {lastGen: 1})),
    extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
  });
  const first = await tickPrepared(parked.responses);
  assert.equal(first.calls().filter(c => c.sub === "needs-owner").length, 1);
  assert.equal(first.result.rollbacks[0].parked, true);
  assert.equal(first.result.rollbacks[0].count, 1);

  const standing = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks: [frozenT2()], plan: rollbackPlan(["t2"]),
    watch: watchOf([freezeDecision()]),
    show: showOut(freezeCard(3, {lastGen: 4, needs_owner: true})),
    extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
  });
  const second = await tickPrepared(standing.responses);
  assert.equal(second.calls().filter(c => c.sub === "needs-owner").length, 0);
  assert.ok(second.log.lines.some(l => l.includes("по пределу t2: needs_owner уже стоит")));
  assert.equal(second.result.rollbacks[0].parked, false);
});

gitTest("предел (е): сломанный swarm.json — порог неизвестен, needs-owner нет, gate config", async () => {
  const tasks = [frozenT2()];
  const {responses} = prepareRollback({
    json: {max_freezes: -1},
    tasks, plan: rollbackPlan(["t2"]),
    watch: watchOf([freezeDecision({generation: 1})]),
    show: showOut(freezeCard(1, {lastGen: 1, withLaunch: false})),
    extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
  });
  const {calls, log, result} = await tickPrepared(responses);
  assert.equal(calls().filter(c => c.sub === "needs-owner").length, 0);
  assert.ok(log.lines.some(l => l.includes("предел откатов не проверен")));
  assert.ok(log.lines.some(l => l.includes("заморозка 1 из ? в окне")));
  assert.equal(result.rollbacks.length, 1);
  assert.equal(result.rollbacks[0].parked, false);
  assert.equal(result.barrier.gate.reason, "config");
  assert.equal(result.report.gate.reason, "config");
});

gitTest("предел (ж): dry-run печатает порог и не пишет; ошибка show не роняет тик", async () => {
  const tasks = [frozenT2()];
  const plan = rollbackPlan(["t2"]);
  const decision = freezeDecision({dry_run: true});
  const {responses} = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan, dryRun: true,
    watch: watchOf([decision]),
    show: showOut(freezeCard(2)),
    extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
  });
  const {calls, log, result} = await tickPrepared(responses, {dryRun: true});
  assert.ok(log.stdout.some(l => l ===
    "[dry-run] по пределу t2: заморозок в окне 2, стало бы 3 (порог 2)"));
  assert.equal(calls().filter(c => c.sub === "needs-owner").length, 0);
  assert.ok(calls().every(c => !c.argv.includes("--actor")));
  assert.deepEqual(result.rollbacks, []);

  const broken = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan, dryRun: true,
    watch: watchOf([decision]),
    show: {exitCode: 1, stdout: JSON.stringify({error: {code: "cli", message: "boom"}})},
  });
  const failed = await tickPrepared(broken.responses, {dryRun: true});
  assert.notEqual(failed.result.error, true);
  assert.ok(failed.log.lines.some(l => l.startsWith("show t2 ошибка:")));
  assert.deepEqual(failed.result.rollbacks, []);
  assert.equal(failed.calls().filter(c => c.sub === "needs-owner").length, 0);
});

gitTest("предел (з): ошибка show — тик не падает, партия запущена, rollbacks пуст", async () => {
  const tasks = [task("t1", {labels: ["port:5170"]}), frozenT2()];
  const {responses} = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan: rollbackPlan(["t1", "t2"]),
    routes: [{key: "r-t1", kind: "swarm", icon: "low"}],
    watch: watchOf([freezeDecision()]),
    show: {exitCode: 1, stdout: JSON.stringify({error: {code: "cli", message: "boom"}})},
    extra: {
      worktree: {stdout: JSON.stringify({path: "/wt/t1", branch: "b", status: "created"})},
      launch: {stdout: JSON.stringify({id: "t1", generation: 1})},
    },
  });
  const {calls, log, result} = await tickPrepared(responses);
  assert.notEqual(result.error, true);
  assert.ok(log.lines.some(l => l.startsWith("show t2 ошибка:")));
  assert.deepEqual(result.rollbacks, []);
  assert.deepEqual(result.launched, ["t1"]);
  assert.ok(calls().some(c => c.sub === "launch" && c.argv.includes("t1")));
});

gitTest("предел (и): main на двух тиках — код 2, один needs-owner, итог с откатами",
  {timeout: 20000}, async () => {
    const repo = initRepo();
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
    fs.writeFileSync(path.join(dataDir, "swarm.json"),
      JSON.stringify({integration: [], max_freezes: 2}));
    const t1Running = task("t1", {
      launched_by: "listik", launch_finished_at: null, needs_owner: false,
      labels: ["port:5170"], launched_at: new Date().toISOString(),
    });
    const t2Frozen = frozenT2();
    const t1Waiting = task("t1", {
      launched_by: "listik", launch_finished_at: "2026-01-01T00:20:00Z", launch_exit_code: 1,
      needs_owner: true, labels: ["port:5170"],
    });
    const t2Parked = task("t2", {
      launched_by: "", needs_owner: true, labels: ["frozen-by:t1"],
    });
    const plan1 = rollbackPlan(["t2"]);
    const plan2 = rollbackPlan([]);
    const card = freezeCard(3, {lastGen: 4});
    const responses = {
      status: statusFor(dataDir),
      projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
      waves: [
        {stdout: JSON.stringify({waves: plan1, added: [], removed: [], kept: 0})},
        {stdout: JSON.stringify({waves: plan2, added: [], removed: [], kept: 0})},
      ],
      list: [
        {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: [t1Running, t2Frozen]})},
        {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: [t1Running, t2Frozen]})},
        {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: [t1Waiting, t2Parked]})},
      ],
      routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
      watch: [
        watchOf([freezeDecision()]),
        watchOf([]),
      ],
      show: [
        showOut({id: "t1", events: [], comments: []}),
        showOut(card),
        showOut({id: "t2", events: [], comments: []}),
        showOut({id: "t1", events: [], comments: []}),
        showOut({id: "t1", comments: [{
          author: "agent:listik-swarm", kind: "question",
          text: "рой: процесс задачи завершился (код 1, поколение 2), а карточка не закрыта.",
          created_at: "2026-01-01T00:20:00Z",
        }]}),
        showOut({id: "t2", comments: [{
          author: "agent:listik-swarm", kind: "question",
          text: "рой: предел откатов — задача заморожена 3 раз подряд",
          created_at: "2026-01-01T00:11:00Z",
        }]}),
      ],
      "needs-owner": {stdout: JSON.stringify({id: "t2"})},
    };
    const {calls} = setupFake(responses);
    const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-rollback-main-"));
    const {code, chunks} = await runMain(
      ["--project", "proj", "--listik", FAKE_BIN, "--log-dir", logDir, "--interval", "1",
        "--exit-when-idle"]);
    assert.equal(code, 2);
    const parks = calls().filter(c => c.sub === "needs-owner");
    assert.equal(parks.length, 1);
    assert.ok(parks[0].argv.includes("t2"));
    for (const sub of ["comment", "revoke", "launch", "set", "worktree"]) {
      assert.equal(calls().filter(c => c.sub === sub).length, 0, sub);
    }
    const logText = fs.readFileSync(chunks[0].trim(), "utf8");
    assert.ok(logText.includes("t2 — предел откатов"));
    assert.ok(logText.includes("t1 — упала"));
    assert.ok(logText.includes(
      "итог: закрыто 0 (), перезапусков 0, откатов 1 (на откаты 10 мин), " +
      "по пределу 1 (t2), оставлено человеку 2"));
  });

// --- listik-3wul, порция b: rescope --apply после влитой волны ---

const RESCOPE_OK = {project: "proj", extracted: 1, tasks: {}, unspecced: {},
  unscoped: [], invalid: {}, drift: {records: 1, ignored: 0, tasks_with_drift: 1, tasks_total: 2,
    outside_files: 0, ratio: 0.5}, edges: [], cycles: [], cycles_from: null,
  applied: {scopes: ["t2"], edges: {added: [["t1", "t2"]], removed: [], kept: 0}}};

// Кейс watch+barrier (а) с реальным слиянием t1; `merge: false` — t1 открыта без дерева.
function rescopeScenario({swarmJson = {integration: []}, rescope, merge = true, extraTasks = [],
  waves = null} = {}) {
  const repo = initRepo();
  let t1 = task("t1", {});
  if (merge) {
    const treeT1 = addWorktree(repo, "t1");
    fs.writeFileSync(path.join(treeT1, "a.txt"), "a\n");
    sh(treeT1, "add", "a.txt");
    sh(treeT1, "commit", "-q", "-m", "t1");
    t1 = task("t1", {status: "done", worktree: treeT1, branch: "task/t1", labels: ["port:5170"]});
  }
  const tasks = [t1, task("t2", {}), ...extraTasks];
  const wavesOut = (w) => ({stdout: JSON.stringify({
    waves: {project: "proj", waves: w, cycles: [], unroutable: [], unscoped: [], blocked: {}},
    added: [], removed: [], kept: 0})});
  const routes = tasks.map(t => ({key: "r-" + t.id, kind: "swarm", icon: "low"}));
  const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
  fs.writeFileSync(path.join(dataDir, "swarm.json"), JSON.stringify(swarmJson));
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-integration-log-"));
  const responses = {
    status: statusFor(dataDir),
    waves: waves ? waves.map(wavesOut) : wavesOut([["t2"]]),
    list: {stdout: JSON.stringify({total: tasks.length, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes})},
    projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
    watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
    show: {stdout: JSON.stringify({id: "t1", comments: [], write_scope: []})},
    comment: {stdout: JSON.stringify({id: "t1"})},
    worktree: {stdout: JSON.stringify({path: "/wt/t2", branch: "b", status: "created"})},
    set: {stdout: JSON.stringify({id: "t2", labels: []})},
    launch: {stdout: JSON.stringify({id: "t2", generation: 1})},
  };
  if (rescope !== undefined) responses.rescope = rescope;
  return {responses, logDir};
}

function launchedIds(calls) {
  return calls.filter(c => c.sub === "launch").map(c => c.argv[1]);
}

const NO_RESCOPE_SIDE = ["needs-owner", "revoke"];

gitTest("rescope (а): влито → rescope → перечитано → запуск", async () => {
  const {responses, logDir} = rescopeScenario({rescope: {stdout: JSON.stringify(RESCOPE_OK)}});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir}, log);

  const all = calls();
  const rescopes = all.filter(c => c.sub === "rescope");
  assert.equal(rescopes.length, 1);
  const argv = rescopes[0].argv;
  const at = argv.indexOf("--project");
  assert.equal(argv[at + 1], "proj");
  assert.ok(argv.includes("--apply"));
  const actorAt = argv.indexOf("--actor");
  assert.equal(argv[actorAt + 1], "agent:listik-swarm");
  assert.ok(!argv.includes("--task") && !argv.includes("--drift"));

  const idx = (pred) => all.findIndex(pred);
  const lastIdx = (sub) => all.map(c => c.sub).lastIndexOf(sub);
  const iComment = idx(c => c.sub === "comment" && c.argv.some(a => a.startsWith("рой: влито:")));
  const iRescope = idx(c => c.sub === "rescope");
  const wavesIdx = all.map((c, i) => c.sub === "waves" ? i : -1).filter(i => i >= 0);
  const iLaunch = idx(c => c.sub === "launch" && c.argv.includes("t2"));
  assert.equal(wavesIdx.length, 2);
  for (const i of wavesIdx) assert.ok(all[i].argv.includes("--apply"));
  assert.ok(all.filter(c => c.sub === "list").length >= 2);
  assert.ok(iComment >= 0 && iComment < iRescope, "comment влито раньше rescope");
  assert.ok(iRescope < wavesIdx[1], "rescope раньше второго waves");
  assert.ok(wavesIdx[1] < lastIdx("list"), "второй waves раньше второго list");
  assert.ok(iRescope < lastIdx("list"));
  assert.ok(lastIdx("list") < iLaunch, "list раньше launch t2");

  const want = "rescope proj: ТЗ прочитано 1, областей записано 1 (t2), рёбер +1 −0, " +
    "качество ТЗ: расхождений у 1 из 2 задач (0 файлов вне области)";
  assert.ok(log.lines.some(l => l.startsWith(want)));
  assert.ok(log.stdout.some(l => l.startsWith(want)));
  assert.deepEqual(result.rescope, {ok: true, extracted: 1, scopes: ["t2"], edgesAdded: 1,
    edgesRemoved: 0, cycles: [], unspecced: [], refreshed: true});
  assert.ok(result.launched.includes("t2"));
  for (const sub of NO_RESCOPE_SIDE) assert.equal(all.filter(c => c.sub === sub).length, 0, sub);
});

gitTest("rescope (б): ничего не записано — волны не перечитываются", async () => {
  const res = {...RESCOPE_OK, applied: {scopes: [], edges: {added: [], removed: [], kept: 1}}};
  const {responses, logDir} = rescopeScenario({rescope: {stdout: JSON.stringify(res)}});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir}, log);
  assert.equal(calls().filter(c => c.sub === "waves").length, 1);
  assert.equal(result.rescope.refreshed, false);
  assert.ok(log.lines.some(l => l.includes("областей записано 0 (), рёбер +0 −0")));
});

gitTest("rescope (в): выключен в swarm.json — не зовётся, launch есть", async () => {
  const {responses, logDir} = rescopeScenario({swarmJson: {integration: [], rescope: false},
    rescope: {stdout: JSON.stringify(RESCOPE_OK)}});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir}, log);
  assert.equal(calls().filter(c => c.sub === "rescope").length, 0);
  assert.ok(log.lines.includes("rescope: выключен в swarm.json"));
  assert.deepEqual(result.rescope, {ok: false, skipped: "disabled"});
  assert.ok(launchedIds(calls()).includes("t2"));
});

gitTest("rescope (г): нечего вливать — не зовётся", async () => {
  const {responses, logDir} = rescopeScenario({merge: false,
    rescope: {stdout: JSON.stringify(RESCOPE_OK)}});
  responses.waves = {stdout: JSON.stringify({waves: {project: "proj", waves: [["t1", "t2"]],
    cycles: [], unroutable: [], unscoped: [], blocked: {}}, added: [], removed: [], kept: 0})};
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir}, log);
  assert.equal(calls().filter(c => c.sub === "rescope").length, 0);
  assert.equal(result.rescope, null);
  assert.ok(!log.lines.some(l => l.includes("rescope")));
});

gitTest("rescope (д): ошибка прохода не роняет тик", async () => {
  const {responses, logDir} = rescopeScenario({rescope: {exitCode: 1, stdout: JSON.stringify({
    error: {code: "server_error", message: "модель роя не настроена: нет [swarm].api_key", hint: ""},
  })}});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir}, log);
  assert.ok(log.lines.some(l => l.startsWith("rescope ошибка: server_error/")));
  assert.equal(calls().filter(c => c.sub === "waves").length, 1);
  assert.ok(launchedIds(calls()).includes("t2"));
  assert.deepEqual(result.rescope, {ok: false, error: "server_error"});
  assert.equal(result.barrier.gate, null);
  for (const sub of NO_RESCOPE_SIDE) assert.equal(calls().filter(c => c.sub === sub).length, 0, sub);
});

gitTest("rescope (е): цикл от модели — области записаны, рёбра нет, волны перечитаны", async () => {
  const res = {...RESCOPE_OK, cycles: [["t1", "t2"]], cycles_from: "model",
    applied: {scopes: ["t2"], edges: null}};
  const {responses, logDir} = rescopeScenario({rescope: {exitCode: 1, stdout: JSON.stringify(res)}});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir}, log);
  assert.ok(log.lines.includes("rescope: цикл t1 → t2 → t1 — рёбра не записаны"));
  assert.ok(log.lines.some(l => l.includes("областей записано 1 (t2), рёбер +0 −0")));
  assert.equal(calls().filter(c => c.sub === "waves").length, 2);
  assert.deepEqual(result.rescope.cycles, [["t1", "t2"]]);
  assert.ok(launchedIds(calls()).includes("t2"));
  assert.equal(result.barrier.gate, null);
  for (const sub of NO_RESCOPE_SIDE) assert.equal(calls().filter(c => c.sub === sub).length, 0, sub);
});

gitTest("rescope (ж): перечитанный план меняет партию", async () => {
  const {responses, logDir} = rescopeScenario({rescope: {stdout: JSON.stringify(RESCOPE_OK)},
    extraTasks: [task("t3", {})], waves: [[["t2", "t3"]], [["t3"], ["t2"]]]});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  await tick(listik, {...baseConfig, logDir}, log);
  const launched = launchedIds(calls());
  assert.ok(launched.includes("t3"));
  assert.ok(!launched.includes("t2"));
});

gitTest("rescope (з): таймаут из swarm.json, а не cliTimeout", async () => {
  {
    const {responses, logDir} = rescopeScenario({swarmJson: {integration: [], rescope_timeout: 5},
      rescope: {sleepMs: 1500, stdout: JSON.stringify(RESCOPE_OK)}});
    setupFake(responses);
    const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 1});
    const log = makeLog();
    const result = await tick(listik, {...baseConfig, logDir}, log);
    assert.ok(log.lines.some(l => l.startsWith("rescope proj:")));
    assert.equal(result.rescope.ok, true);
  }
  {
    const {responses, logDir} = rescopeScenario({swarmJson: {integration: [], rescope_timeout: 1},
      rescope: {sleepMs: 2500, stdout: JSON.stringify(RESCOPE_OK)}});
    const {calls} = setupFake(responses);
    const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 1});
    const log = makeLog();
    const result = await tick(listik, {...baseConfig, logDir}, log);
    assert.ok(log.lines.some(l => l.startsWith("rescope ошибка: timeout/")));
    assert.ok(launchedIds(calls()).includes("t2"));
    assert.equal(result.barrier.gate, null);
    for (const sub of NO_RESCOPE_SIDE) assert.equal(calls().filter(c => c.sub === sub).length, 0, sub);
  }
});

gitTest("rescope (и): dry-run — не зовётся", async () => {
  const {responses, logDir} = rescopeScenario({rescope: {stdout: JSON.stringify(RESCOPE_OK)}});
  const {calls} = setupFake(responses);
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, {...baseConfig, logDir, dryRun: true}, log);
  assert.equal(calls().filter(c => c.sub === "rescope").length, 0);
  assert.equal(result.rescope, null);
});

test("projectsDue: открытые, вопрос и дерево закрытой; старый done мимо", () => {
  assert.deepEqual(projectsDue([
    {id: "a", project: "alpha", status: "open"},
    {id: "b", project: "beta", status: "done", needs_owner: true},
    {id: "c", project: "gamma", status: "done", worktree: "/wt/c"},
    {id: "d", project: "delta", status: "done", worktree: "", launched_by: "listik"},
    {id: "e", project: "", status: "open"},
  ]), ["alpha", "beta", "gamma"]);
});

test("main: без --project тикает каждый проект с работой и выходит по --once", async () => {
  const emptyPlan = {waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const responses = {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "alpha", path: ""}, {slug: "beta", path: ""}])},
    waves: {stdout: JSON.stringify({waves: emptyPlan, added: [], removed: [], kept: 0})},
    list: [
      {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks: [
        {id: "a", project: "alpha", status: "open"},
        {id: "b", project: "beta", status: "in_progress"},
      ]})},
      {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})},
      {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})},
    ],
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
  };
  const {calls} = setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-all-"));
  const {code} = await runMain(["--listik", FAKE_BIN, "--log-dir", logDir, "--once"]);
  assert.equal(code, 0);
  const waved = calls().filter(c => c.sub === "waves").map(c => c.argv[c.argv.indexOf("--project") + 1]);
  assert.deepEqual(waved, ["alpha", "beta"]);
  assert.equal(calls().some(c => c.argv.includes("--project") && c.argv.includes("all")), true);
});

test("main: без работы по проектам --once не зовёт waves", async () => {
  const responses = {
    status: statusUp,
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})},
  };
  const {calls} = setupFake(responses);
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-all-idle-"));
  const {code, chunks} = await runMain(["--listik", FAKE_BIN, "--log-dir", logDir, "--once"]);
  assert.equal(code, 0);
  assert.equal(calls().filter(c => c.sub === "waves").length, 0);
  assert.equal(calls().filter(c => c.sub === "list").length, 2);
  const logText = fs.readFileSync(chunks[0].trim(), "utf8");
  assert.match(logText, /"allProjects":true/);
});

// --- listik-eps7, порция b ---

const CYCLE_PLAN = {project: "proj", waves: [], cycles: [["a", "b"]], unroutable: [], unscoped: [], blocked: {}};

function errOut(message) {
  return {exitCode: 1, stdout: JSON.stringify({error: {code: "cli", message, hint: ""}})};
}

gitTest("К1: барьер поставил needs-owner поверх просроченного мягкого вопроса — show заново, автоответа нет",
  async () => {
    const repo = initRepo();
    const tree = addWorktree(repo, "t1");
    fs.writeFileSync(path.join(tree, "a.txt"), "a\n");
    sh(tree, "add", "a.txt");
    sh(tree, "commit", "-q", "-m", "t1");
    fs.writeFileSync(path.join(tree, "untracked.txt"), "x\n");

    const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(40), note: SOFT_Q};
    const softC = {author: "agent:fake", kind: "question", text: SOFT_Q, created_at: minsAgo(40)};
    const swarmQ = {kind: "question", actor: "agent:listik-swarm", ts: minsAgo(0),
      note: "рой: не влита — в дереве незакоммиченные правки"};
    const tasks = [task("t1", {
      status: "done", worktree: tree, branch: "task/t1", labels: ["port:5170"], needs_owner: true,
    })];
    const plan = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
    const dataDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-data-"));
    const responses = {
      status: statusFor(dataDir),
      waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
      list: {stdout: JSON.stringify({total: 1, limit: 1000, offset: 0, tasks})},
      routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
      projects: {stdout: JSON.stringify([{slug: "proj", path: repo}])},
      watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})},
      show: [
        {stdout: JSON.stringify({id: "t1", events: [qEvent], comments: [softC], write_scope: []})},
        {stdout: JSON.stringify({id: "t1", events: [qEvent], comments: [softC], write_scope: []})},
        {stdout: JSON.stringify({id: "t1", events: [qEvent, swarmQ], comments: [softC], write_scope: []})},
      ],
      "needs-owner": {stdout: JSON.stringify({id: "t1"})},
    };
    const {calls} = setupFake(responses);
    const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
    const log = makeLog();
    const result = await tick(listik, {...baseConfig, questionTimeout: 30}, log);

    assert.deepEqual(result.report.unmerged, ["t1"]);
    assert.deepEqual(result.defaults, []);
    const subs = calls();
    assert.ok(!subs.some(c => c.sub === "needs-owner" && c.argv.includes("--clear")));
    const flagIdx = subs.findIndex(c => c.sub === "needs-owner" && c.argv.includes("t1"));
    assert.ok(flagIdx >= 0, "барьер ставит needs-owner");
    assert.equal(subs.slice(flagIdx).filter(c => c.sub === "show" && c.argv.includes("t1")).length, 1);
  });

gitTest("К1: предел откатов припарковал, свежий show упал — события убраны, автоответа нет", async () => {
  const qEvent = {kind: "question", actor: "agent:fake", ts: minsAgo(40), note: SOFT_Q};
  const tasks = [frozenT2({needs_owner: true, launched_by: "agent:listik-swarm"})];
  const {responses} = prepareRollback({
    json: {integration: [], max_freezes: 2},
    tasks, plan: rollbackPlan([]),
    watch: watchOf([freezeDecision()]),
    show: [
      showOut({id: "t2", events: [qEvent], comments: []}),
      showOut(freezeCard(3)),
      errOut("boom"),
    ],
    extra: {"needs-owner": {stdout: JSON.stringify({id: "t2"})}},
  });
  const {calls, log, result} = await tickPrepared(responses);
  assert.deepEqual(result.report.parked, ["t2"]);
  assert.equal(calls().filter(c => c.sub === "show").length, 3);
  assert.ok(log.lines.some(l => l.startsWith("show t2 ошибка:")));
  assert.deepEqual(result.defaults, []);
  assert.ok(!calls().some(c => c.sub === "needs-owner" && c.argv.includes("--clear")));
});

function batchResponses(extra) {
  const tasks = [task("t1"), task("t2")];
  const plan = {project: "proj", waves: [["t1", "t2"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  return {
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 2, limit: 1000, offset: 0, tasks})},
    routes: {stdout: JSON.stringify({ok: true, routes: [{key: "r-t1", kind: "swarm", icon: "low"}, {key: "r-t2", kind: "swarm", icon: "low"}]})},
    worktree: {stdout: JSON.stringify({path: "/wt/x", branch: "b", status: "created"})},
    show: {stdout: JSON.stringify({id: "x", labels: []})},
    set: {stdout: JSON.stringify({id: "x", labels: []})},
    launch: {stdout: JSON.stringify({id: "x", generation: 1})},
    ...extra,
  };
}

test("партия: show для метки port: упал — задача пропущена, вторая запущена", async () => {
  const {calls} = setupFake(batchResponses({show: [errOut("boom"), {stdout: JSON.stringify({labels: []})}]}));
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.ok(log.lines.some(l => l.startsWith("show t1 ошибка:")));
  assert.deepEqual(result.launched, ["t2"]);
  assert.ok(result.report);
  assert.ok(!calls().some(c => c.sub === "launch" && c.argv.includes("t1")));
});

test("партия: set для метки port: упал — задача пропущена, вторая запущена", async () => {
  const {calls} = setupFake(batchResponses({set: [errOut("boom"), {stdout: JSON.stringify({labels: []})}]}));
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.ok(log.lines.some(l => l.startsWith("set t1 ошибка:")));
  assert.deepEqual(result.launched, ["t2"]);
  assert.ok(!calls().some(c => c.sub === "launch" && c.argv.includes("t1")));
});

test("бюджет-тик: без runState maxLaunches 1 не режет партию", async () => {
  const {calls} = setupFake(batchResponses({}));
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const result = await tick(listik, {...baseConfig, maxLaunches: 1}, makeLog());
  assert.equal(result.launched.length, 2);
  assert.equal(calls().filter(c => c.sub === "launch").length, 2);
});

test("циклы: tick строку не пишет, noteCycles — только при смене набора", async () => {
  setupFake({
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: CYCLE_PLAN, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: [task("a"), task("b")]})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
  });
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const log = makeLog();
  const result = await tick(listik, baseConfig, log);
  assert.deepEqual(result.cycles, [["a", "b"]]);
  assert.ok(!log.lines.some(l => l.startsWith("циклы:")));

  const out = makeLog();
  const last = {value: null};
  const cyc = {cycles: [["a", "b"]]};
  noteCycles(out, cyc, last);
  noteCycles(out, cyc, last);
  noteCycles(out, {cycles: []}, last);
  noteCycles(out, cyc, last);
  noteCycles(out, {cycles: [["a", "c"]]}, last);
  noteCycles(out, {error: true}, last);
  noteCycles(out, {cycles: [["a", "c"]]}, last);
  assert.deepEqual(out.lines, ["циклы: a → b → a", "циклы: a → b → a", "циклы: a → c → a"]);

  const once = makeLog();
  noteCycles(once, cyc, null);
  noteCycles(once, cyc, null);
  assert.equal(once.lines.length, 2);
});

test("main: цикл четыре тика подряд — «циклы:» один раз, после исчезновения — ещё раз",
  {timeout: 20000}, async () => {
    const empty = {project: "proj", waves: [[]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
    const w = (plan) => ({stdout: JSON.stringify({waves: plan, added: [], removed: [], kept: 0})});
    const {calls} = setupFake({
      status: statusUp,
      projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
      waves: [w(CYCLE_PLAN), w(CYCLE_PLAN), w(empty), w(CYCLE_PLAN)],
      list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: [task("a"), task("b")]})},
      routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
    });
    const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-cycles-"));
    const stopper = setInterval(() => {
      let n = 0;
      try {
        n = calls().filter(c => c.sub === "waves").length;
      } catch { /* файла ещё нет */ }
      if (n >= 6) process.emit("SIGINT");
    }, 100);
    let code, chunks;
    try {
      ({code, chunks} = await runMain(
        ["--project", "proj", "--listik", FAKE_BIN, "--log-dir", logDir, "--interval", "1"]));
    } finally {
      clearInterval(stopper);
    }
    assert.equal(code, 130);
    const logText = fs.readFileSync(chunks[0].trim(), "utf8");
    assert.equal(logText.split("\n").filter(l => l.includes("циклы: a → b → a")).length, 2);
  });

test("main --once: цикл — строка «циклы:» и код 1", {timeout: 15000}, async () => {
  setupFake({
    status: statusUp,
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
    waves: {stdout: JSON.stringify({waves: CYCLE_PLAN, added: [], removed: [], kept: 0})},
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: [{id: "a", status: "open", launch_route: "r-a", launch_driver: "swarm"}, {id: "b", status: "open", launch_route: "r-b", launch_driver: "swarm"}]})},
    routes: {stdout: JSON.stringify({ok: true, routes: SWARM_ROUTES})},
  });
  const logDir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-cycles-once-"));
  const {code, chunks} = await runMain(
    ["--project", "proj", "--listik", FAKE_BIN, "--log-dir", logDir, "--once"]);
  assert.equal(code, 1);
  const logText = fs.readFileSync(chunks[0].trim(), "utf8");
  assert.ok(logText.includes("циклы: a → b → a"));
});

test("questionReason: зависшая нарезка → порции не запускаются", async () => {
  const {questionReason} = await import("../main.mjs");
  const {textSlicedStuck} = await import("../decide.mjs");
  assert.equal(questionReason(textSlicedStuck("x")), "порции не запускаются");
});
