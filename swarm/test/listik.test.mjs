import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {fileURLToPath} from "node:url";
import {Listik, ListikError} from "../listik.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const FAKE_BIN = path.join(here, "fixtures", "fake-listik.mjs");

function setupFake(responses) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-test-"));
  const callsFile = path.join(dir, "calls.log");
  const responsesFile = path.join(dir, "responses.json");
  fs.writeFileSync(responsesFile, JSON.stringify(responses));
  process.env.LISTIK_FAKE_CALLS_FILE = callsFile;
  process.env.LISTIK_FAKE_RESPONSES_FILE = responsesFile;
  return {
    callsFile,
    calls() {
      return fs.readFileSync(callsFile, "utf8").trim().split("\n").filter(Boolean)
        .map(l => JSON.parse(l).argv);
    },
  };
}

test("launch: argv содержит --env, --json, --actor", async () => {
  const {calls} = setupFake({launch: {stdout: JSON.stringify({id: "a", generation: 1})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.launch("a", {LISTIK_DEV_PORT: "5170"});
  const [argv] = calls();
  assert.deepEqual(argv, ["launch", "a", "--env", "LISTIK_DEV_PORT=5170", "--json", "--actor", "agent:listik-swarm"]);
});

test("--port стоит до подкоманды", async () => {
  const {calls} = setupFake({status: {stdout: JSON.stringify({server: "up"})}});
  const listik = new Listik({bin: FAKE_BIN, port: 1234, cliTimeout: 5});
  await listik.status();
  const [argv] = calls();
  assert.deepEqual(argv.slice(0, 2), ["--port", "1234"]);
});

test("list зовётся с -a", async () => {
  const {calls} = setupFake({list: {stdout: JSON.stringify({total: 0, limit: 1000, offset: 0, tasks: []})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  await listik.list("proj");
  const [argv] = calls();
  assert.ok(argv.includes("-a"));
});

test("ненулевой код с JSON {error} — ListikError с кодом", async () => {
  setupFake({show: {exitCode: 1, stdout: JSON.stringify({error: {code: "not_found", message: "нет", hint: "h"}})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  await assert.rejects(() => listik.show("x"), (err) => {
    assert.ok(err instanceof ListikError);
    assert.equal(err.code, "not_found");
    return true;
  });
});

test("ненулевой код с JSON без error (status down) — успех", async () => {
  setupFake({status: {exitCode: 1, stdout: JSON.stringify({server: "down"})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  const res = await listik.status();
  assert.equal(res.server, "down");
});

test("waves без --apply при цикле (ненулевой код) — успех, план с cycles", async () => {
  setupFake({waves: {exitCode: 1, stdout: JSON.stringify({cycles: [["a", "b"]], waves: []})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  const plan = await listik.waves("proj", {apply: false});
  assert.deepEqual(plan.cycles, [["a", "b"]]);
});

test("waves --apply конфликт цикла — второй вызов без --apply, план из него", async () => {
  const {calls} = setupFake({
    waves: [
      {exitCode: 1, stdout: JSON.stringify({error: {
        code: "conflict",
        message: "ресурсные рёбра не записаны: в зависимостях цикл a → b → a",
      }})},
      {stdout: JSON.stringify({cycles: [["a", "b"]], waves: []})},
    ],
  });
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 5});
  const plan = await listik.waves("proj", {apply: true});
  assert.deepEqual(plan.cycles, [["a", "b"]]);
  const argvs = calls();
  assert.equal(argvs.length, 2);
  assert.ok(argvs[0].includes("--apply"));
  assert.ok(!argvs[1].includes("--apply"));
});

test("projects: без --actor, возвращает массив с slug/path", async () => {
  const {calls} = setupFake({
    projects: {stdout: JSON.stringify([{slug: "listik", path: "/repo"}, {slug: "other", path: "/o"}])},
  });
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.projects();
  const [argv] = calls();
  assert.deepEqual(argv, ["projects", "--json"]);
  assert.ok(Array.isArray(res));
  assert.equal(res[0].slug, "listik");
  assert.equal(res[0].path, "/repo");
});

test("comment: -k journal и --actor", async () => {
  const {calls} = setupFake({comment: {stdout: JSON.stringify({id: "a"})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.comment("a", "текст");
  const [argv] = calls();
  assert.deepEqual(argv, ["comment", "a", "текст", "-k", "journal", "--json", "--actor", "agent:listik-swarm"]);
});

test("create: -t question, -l swarm:halt, --discovered-from, возвращает id", async () => {
  const {calls} = setupFake({new: {stdout: JSON.stringify({id: "listik-xyz"})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.create({
    title: "халт", project: "listik", type: "question",
    labels: ["swarm:halt"], discoveredFrom: "listik-abc",
  });
  const [argv] = calls();
  assert.deepEqual(argv, [
    "new", "халт", "-p", "listik", "-t", "question", "-l", "swarm:halt",
    "--discovered-from", "listik-abc", "--json", "--actor", "agent:listik-swarm",
  ]);
  assert.equal(res.id, "listik-xyz");
});

test("create: без labels/discoveredFrom/description — в argv нет этих флагов", async () => {
  const {calls} = setupFake({new: {stdout: JSON.stringify({id: "listik-xyz"})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.create({title: "халт", project: "listik", type: "question"});
  const [argv] = calls();
  assert.deepEqual(argv, ["new", "халт", "-p", "listik", "-t", "question", "--json", "--actor", "agent:listik-swarm"]);
});

test("set: worktree= (пустое) и labels=a,b", async () => {
  const {calls} = setupFake({set: {stdout: JSON.stringify({id: "a"})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.set("a", {worktree: null, labels: ["a", "b"]});
  const [argv] = calls();
  assert.deepEqual(argv, ["set", "a", "worktree=", "labels=a,b", "--json", "--actor", "agent:listik-swarm"]);
});

test("watch: --project, --json, --actor, нет --task", async () => {
  const {calls} = setupFake({watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.watch("proj");
  const [argv] = calls();
  assert.deepEqual(argv, ["watch", "--project", "proj", "--json", "--actor", "agent:listik-swarm"]);
  assert.ok(!argv.includes("--task"));
});

test("watch: dryRun — есть --dry-run, нет --actor", async () => {
  const {calls} = setupFake({watch: {stdout: JSON.stringify({tasks: {}, decisions: [], probes: []})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.watch("proj", {dryRun: true});
  const [argv] = calls();
  assert.deepEqual(argv, ["watch", "--project", "proj", "--dry-run", "--json"]);
  assert.ok(!argv.includes("--actor"));
});

test("watch: ненулевой код с JSON скана без error (будущий код 1) — метод возвращает объект", async () => {
  setupFake({watch: {exitCode: 1, stdout: JSON.stringify({
    tasks: {}, decisions: [{action: "freeze", task: "t1", ok: false, error: "unsupported"}], probes: [],
  })}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.watch("proj");
  assert.equal(res.decisions[0].ok, false);
});

test("answer: needs-owner --clear, --json, --actor", async () => {
  const {calls} = setupFake({"needs-owner": {stdout: JSON.stringify({id: "a"})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await listik.answer("a", "t");
  const [argv] = calls();
  assert.deepEqual(argv, ["needs-owner", "a", "--clear", "t", "--json", "--actor", "agent:listik-swarm"]);
});

test("таймаут — ListikError c code timeout", async () => {
  setupFake({status: {sleepMs: 2000, stdout: JSON.stringify({server: "up"})}});
  const listik = new Listik({bin: FAKE_BIN, cliTimeout: 1});
  await assert.rejects(() => listik.status(), (err) => {
    assert.ok(err instanceof ListikError);
    assert.equal(err.code, "timeout");
    return true;
  });
});

test("rescope: argv — rescope --project proj --apply --json --actor", async () => {
  const {calls} = setupFake({rescope: {stdout: JSON.stringify({
    project: "proj", applied: {scopes: [], edges: null}, cycles: []})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.rescope("proj");
  const [argv] = calls();
  assert.deepEqual(argv, ["rescope", "--project", "proj", "--apply", "--json", "--actor", "agent:listik-swarm"]);
  for (const flag of ["--task", "--drift", "--dry-run"]) assert.ok(!argv.includes(flag), flag);
  assert.equal(res.project, "proj");
});

test("rescope: ненулевой код с JSON без error (цикл) — объект с cycles", async () => {
  setupFake({rescope: {exitCode: 1, stdout: JSON.stringify({
    cycles: [["a", "b"]], applied: {scopes: ["a"], edges: null}})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.rescope("proj");
  assert.equal(res.cycles.length, 1);
});

test("rescope: ненулевой код с {error} — ListikError server_error", async () => {
  setupFake({rescope: {exitCode: 1, stdout: JSON.stringify({error: {
    code: "server_error", message: "модель роя не настроена", hint: ""}})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  await assert.rejects(() => listik.rescope("proj"), (err) => {
    assert.ok(err instanceof ListikError);
    assert.equal(err.code, "server_error");
    return true;
  });
});

test("rescope: timeoutSec перекрывает cliTimeout", async () => {
  setupFake({rescope: {sleepMs: 1500, stdout: "{}"}});
  const slowOk = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 1});
  await slowOk.rescope("proj", {timeoutSec: 5});

  setupFake({rescope: {sleepMs: 2500, stdout: "{}"}});
  const tight = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 60});
  await assert.rejects(() => tight.rescope("proj", {timeoutSec: 1}), (err) => {
    assert.ok(err instanceof ListikError);
    assert.equal(err.code, "timeout");
    assert.ok(err.message.includes("1s"), err.message);
    return true;
  });
});

test("arbiterCheck: argv — arbiter-check --input FILE --json, без --actor", async () => {
  const {calls} = setupFake({"arbiter-check": {stdout: JSON.stringify({ok: true, skipped: "не настроен", files: []})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.arbiterCheck("/p.json");
  const [argv] = calls();
  assert.deepEqual(argv, ["arbiter-check", "--input", "/p.json", "--json"]);
  assert.equal(res.skipped, "не настроен");
});

test("arbiterCheck: таймаут по умолчанию 600 с", async () => {
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const seen = [];
  listik._call = async (sub, opts) => { seen.push({sub, opts}); return {}; };
  await listik.arbiterCheck("/p.json");
  assert.deepEqual(seen, [{sub: ["arbiter-check", "--input", "/p.json"], opts: {timeoutSec: 600}}]);
});

test("arbiterCheck: ненулевой код с JSON без error (reject) — объект", async () => {
  setupFake({"arbiter-check": {exitCode: 1, stdout: JSON.stringify({ok: false, model: "m", skipped: null,
    files: [{path: "f.txt", verdict: "reject", reason: "task_kept=0.10", answers: {}}]})}});
  const listik = new Listik({bin: FAKE_BIN, actor: "agent:listik-swarm", cliTimeout: 5});
  const res = await listik.arbiterCheck("/p.json");
  assert.equal(res.ok, false);
  assert.equal(res.files[0].verdict, "reject");
});
