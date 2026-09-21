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
import {questionReason, waitingLine} from "../main.mjs";

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
  const routes = [{key: "r-t1", icon: "low"}, {key: "r-t2", icon: "low"}, {key: "r-t3", icon: "low"}];
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
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

test("замороженная в waves[0] — ни worktree, ни set, ни launch для неё", async () => {
  const tasks = [task("frozen", {labels: ["frozen-by:t1"]}), task("t1")];
  const plan = {
    project: "proj", waves: [["frozen", "t1"]], cycles: [], unroutable: [], unscoped: [], blocked: {},
  };
  const routes = [{key: "r-frozen", icon: "low"}, {key: "r-t1", icon: "low"}];
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
  const routes = [{key: "r-t1", icon: "low"}];
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    projects: {stdout: JSON.stringify([{slug: "proj", path: ""}])},
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

// --- порция c: наблюдатель (watch) + барьер (runBarrier) внутри тика ---

function statusFor(dataDir) {
  return {stdout: JSON.stringify({
    server: "up", bin_path: "/bin/listik", data_dir: dataDir, db_path: `${dataDir}/listik.db`,
    url: "http://x",
  })};
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
  const routes = [{key: "r-t2", icon: "low"}];
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
    const routes = [{key: "r-t2", icon: "low"}];
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
  const routes = [{key: "r-t1", icon: "low"}];
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
  const routes = [{key: "r-t2", icon: "low"}];
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
    list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})},
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
  const routes = [{key: "r-t1", icon: "low"}];
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
  const routes = [{key: "r-t1", icon: "low"}];
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
  const routes = [{key: "r-t1", icon: "low"}, {key: "r-t2", icon: "low"}];
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
  const routes = [{key: "r-t1", icon: "low"}];
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
  const routes = [{key: "r-t1", icon: "low"}, {key: "r-t2", icon: "low"}];
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
    task("halt1", {labels: ["swarm:halt"]}),
    task("n1", {}),
  ];
  const plan = {project: "proj", waves: [["n1"]], cycles: [], unroutable: [], unscoped: [], blocked: {}};
  const routes = [{key: "r-n1", icon: "low"}];
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
  assert.match(text, /влито 0 \(\) · не влиты 0 \(\) · не приняты 0 \(\) · дефолт 0 \(\) · разморожено 0 \(\) · интеграция — · стоп —$/);
});

test("сводка: merged/integration red/halt", () => {
  const text = summaryOf({merged: ["a"], integration: "red", halt: "listik-x"});
  assert.match(text, /влито 1 \(a\)/);
  assert.match(text, /интеграция красная/);
  assert.match(text, /стоп listik-x$/);
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
      routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
    routes: {stdout: JSON.stringify({ok: true, routes: []})},
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
