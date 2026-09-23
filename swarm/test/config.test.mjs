import {test} from "node:test";
import assert from "node:assert/strict";
import {parseConfig, parseSwarmConfig, swarmConfigFor, ConfigError, HelpRequested,
  DEFAULT_QUESTION_TIMEOUT, HELP_TEXT} from "../config.mjs";

test("дефолты", () => {
  const config = parseConfig(["--project", "listik"]);
  assert.equal(config.listikBin, "listik");
  assert.equal(config.cliTimeout, 120);
  assert.equal(config.parallel, 3);
  assert.equal(config.portBase, 5170);
  assert.equal(config.portCount, 100);
  assert.equal(config.interval, 30);
  assert.equal(config.exitWhenIdle, false);
  assert.equal(config.actor, "agent:listik-swarm");
  assert.deepEqual(config.weights, {xhigh: 3, high: 2, medium: 1, low: 1, xlow: 1, direct: 1});
});

test("--weights частично переопределяет один уровень", () => {
  const config = parseConfig(["--project", "listik", "--weights", "xhigh=5"]);
  assert.deepEqual(config.weights, {xhigh: 5, high: 2, medium: 1, low: 1, xlow: 1, direct: 1});
});

test("--weights foo=1 — ошибка", () => {
  assert.throws(() => parseConfig(["--project", "listik", "--weights", "foo=1"]), ConfigError);
});

test("--parallel abc — ошибка", () => {
  assert.throws(() => parseConfig(["--project", "listik", "--parallel", "abc"]), ConfigError);
});

test("бюджет: дефолты 0, разбор флагов, ошибки валидации, HELP_TEXT", () => {
  const d = parseConfig(["--project", "listik"]);
  assert.equal(d.budgetMinutes, 0);
  assert.equal(d.maxLaunches, 0);

  assert.equal(parseConfig(["--project", "p", "--max-launches", "2"]).maxLaunches, 2);
  assert.equal(parseConfig(["--project", "p", "--budget-minutes", "1.5"]).budgetMinutes, 1.5);
  assert.equal(parseConfig(["--project", "p", "--budget-minutes", "90"]).budgetMinutes, 90);

  assert.throws(() => parseConfig(["--project", "p", "--budget-minutes", "x"]), ConfigError);
  assert.throws(() => parseConfig(["--project", "p", "--budget-minutes", "-1"]), ConfigError);
  assert.throws(() => parseConfig(["--project", "p", "--max-launches", "-1"]), ConfigError);
  assert.throws(() => parseConfig(["--project", "p", "--max-launches", "1.5"]), ConfigError);

  assert.equal(parseConfig(["--project", "p", "--parallel=-1"]).parallel, -1);

  assert.match(HELP_TEXT, /--budget-minutes/);
  assert.match(HELP_TEXT, /--max-launches/);
  assert.match(HELP_TEXT, /--exit-when-idle/);
  assert.equal(parseConfig(["--project", "p", "--exit-when-idle"]).exitWhenIdle, true);
});

test("без --project — ошибка", () => {
  assert.throws(() => parseConfig([]), ConfigError);
});

test("--help — HelpRequested", () => {
  assert.throws(() => parseConfig(["--help"]), HelpRequested);
});

test("--config задаёт configPath, без флага — null", () => {
  const withFlag = parseConfig(["--project", "p", "--config", "/x/y.json"]);
  assert.equal(withFlag.configPath, "/x/y.json");
  const withoutFlag = parseConfig(["--project", "p"]);
  assert.equal(withoutFlag.configPath, null);
});

// --- swarm.json: parseSwarmConfig / swarmConfigFor ---

const FULL_EXAMPLE = JSON.stringify({
  integration_timeout: 99,
  arbiter: ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"],
  projects: {
    listik: {
      arbiter_timeout: 42,
      integration: [
        ["python3", "-m", "unittest", "discover", "tests"],
        ["npm", "--prefix", "web", "run", "typecheck"],
      ],
    },
  },
});

test("parseSwarmConfig(null) — {}", () => {
  assert.deepEqual(parseSwarmConfig(null), {});
});

test("полный пример разбирается, swarmConfigFor даёт нужные поля по проекту и дефолт", () => {
  const cfg = parseSwarmConfig(FULL_EXAMPLE);
  const listik = swarmConfigFor(cfg, "listik");
  assert.deepEqual(listik.integration, [
    ["python3", "-m", "unittest", "discover", "tests"],
    ["npm", "--prefix", "web", "run", "typecheck"],
  ]);
  assert.deepEqual(listik.arbiter, ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"]);
  assert.equal(listik.integrationTimeout, 99);
  assert.equal(listik.arbiterTimeout, 42);

  const other = swarmConfigFor(cfg, "other");
  assert.equal(other.integration, null);
  assert.deepEqual(other.arbiter, ["claude", "--dangerously-skip-permissions", "-p", "{prompt}"]);
  assert.equal(other.integrationTimeout, 99);
  assert.equal(other.arbiterTimeout, 1200);
});

test("объект без ключей таймаутов — дефолты 1800/1200", () => {
  const cfg = swarmConfigFor(parseSwarmConfig("{}"), "any");
  assert.equal(cfg.integrationTimeout, 1800);
  assert.equal(cfg.arbiterTimeout, 1200);
});

test('{"integration": []} — [] (не null)', () => {
  const cfg = swarmConfigFor(parseSwarmConfig(JSON.stringify({integration: []})), "any");
  assert.deepEqual(cfg.integration, []);
});

test("swarm.json: невалидные варианты — ConfigError", () => {
  const invalidJson = ["not json", "null", "[]"];
  for (const text of invalidJson) {
    assert.throws(() => parseSwarmConfig(text), ConfigError, text);
  }
  assert.throws(() => parseSwarmConfig(JSON.stringify({integration: "npm test"})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({integration: ["npm", "test"]})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({arbiter: ["claude", "-p", "prompt"]})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({integration_timeout: 0})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({integraton: ["x"]})), ConfigError);
});

test("verify разбирается и переопределяется по проекту; дефолты 1800/1", () => {
  const cfg = parseSwarmConfig(JSON.stringify({
    verify: [["echo", "top"]],
    verify_timeout: 10,
    verify_retries: 3,
    projects: {
      listik: {
        verify: [["echo", "proj"]],
        verify_timeout: 20,
        verify_retries: 0,
      },
    },
  }));
  const listik = swarmConfigFor(cfg, "listik");
  assert.deepEqual(listik.verify, [["echo", "proj"]]);
  assert.equal(listik.verifyTimeout, 20);
  assert.equal(listik.verifyRetries, 0);
  const other = swarmConfigFor(cfg, "other");
  assert.deepEqual(other.verify, [["echo", "top"]]);
  assert.equal(other.verifyTimeout, 10);
  assert.equal(other.verifyRetries, 3);

  const empty = swarmConfigFor(parseSwarmConfig("{}"), "any");
  assert.equal(empty.verify, null);
  assert.equal(empty.verifyTimeout, 1800);
  assert.equal(empty.verifyRetries, 1);
});

test('{"verify": []} — [] (не null)', () => {
  const cfg = swarmConfigFor(parseSwarmConfig(JSON.stringify({verify: []})), "any");
  assert.deepEqual(cfg.verify, []);
});

test("swarm.json: verify невалидные — ConfigError", () => {
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify: ["x"]})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify: null})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify_timeout: 0})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify_retries: -1})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verify_retries: 1.5})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({verfy: [["x"]]})), ConfigError);
});

test("question_timeout: дефолт 30, проектное переопределение, 0 и 0.05 ок, -1 и строка — ConfigError", () => {
  assert.equal(DEFAULT_QUESTION_TIMEOUT, 30);
  const empty = swarmConfigFor(parseSwarmConfig("{}"), "any");
  assert.equal(empty.questionTimeout, 30);

  const cfg = parseSwarmConfig(JSON.stringify({
    question_timeout: 10,
    projects: {listik: {question_timeout: 5}},
  }));
  assert.equal(swarmConfigFor(cfg, "listik").questionTimeout, 5);
  assert.equal(swarmConfigFor(cfg, "other").questionTimeout, 10);

  assert.equal(swarmConfigFor(parseSwarmConfig(JSON.stringify({question_timeout: 0})), "any").questionTimeout, 0);
  assert.equal(swarmConfigFor(parseSwarmConfig(JSON.stringify({question_timeout: 0.05})), "any").questionTimeout, 0.05);

  assert.throws(() => parseSwarmConfig(JSON.stringify({question_timeout: -1})), ConfigError);
  assert.throws(() => parseSwarmConfig(JSON.stringify({question_timeout: "30"})), ConfigError);
});

test("max_freezes: дефолт 2, ноль не дефолт, проект бьёт верхний уровень", () => {
  assert.equal(swarmConfigFor(parseSwarmConfig(null), "p").maxFreezes, 2);
  assert.equal(
    swarmConfigFor(parseSwarmConfig(JSON.stringify({max_freezes: 0})), "p").maxFreezes,
    0,
  );
  const cfg = parseSwarmConfig(JSON.stringify({
    max_freezes: 5,
    projects: {p: {max_freezes: 1}},
  }));
  assert.equal(swarmConfigFor(cfg, "p").maxFreezes, 1);
  assert.equal(swarmConfigFor(cfg, "other").maxFreezes, 5);

  const full = swarmConfigFor(parseSwarmConfig(FULL_EXAMPLE), "listik");
  assert.equal(full.maxFreezes, 2);
  assert.equal(full.integrationTimeout, 99);
  assert.equal(full.arbiterTimeout, 42);
  assert.equal(full.verify, null);
  assert.equal(full.questionTimeout, 30);
});

test("max_freezes: -1, дробь, строка и null — ConfigError", () => {
  for (const bad of [-1, 1.5, "2", null]) {
    assert.throws(
      () => parseSwarmConfig(JSON.stringify({max_freezes: bad})),
      err => err instanceof ConfigError &&
        err.message === "swarm.json: max_freezes ожидал целое число >= 0",
      `max_freezes ${JSON.stringify(bad)}`,
    );
  }
});

test("rescope: дефолт true, false с верхнего уровня и из проекта", () => {
  assert.equal(swarmConfigFor(parseSwarmConfig("{}"), "p").rescope, true);
  assert.equal(swarmConfigFor(parseSwarmConfig(JSON.stringify({rescope: false})), "p").rescope, false);
  const parsed = parseSwarmConfig(JSON.stringify({projects: {p: {rescope: false}}}));
  assert.equal(swarmConfigFor(parsed, "p").rescope, false);
  assert.equal(swarmConfigFor(parsed, "q").rescope, true);
});

test("rescope: строка, null, число — ConfigError, в том числе в projects.p", () => {
  for (const bad of ["no", null, 1]) {
    assert.throws(
      () => parseSwarmConfig(JSON.stringify({rescope: bad})),
      (err) => err instanceof ConfigError &&
        err.message.startsWith("swarm.json: rescope ожидал true или false"),
      `rescope ${JSON.stringify(bad)}`,
    );
    assert.throws(
      () => parseSwarmConfig(JSON.stringify({projects: {p: {rescope: bad}}})),
      (err) => err instanceof ConfigError && err.message.includes("projects.p.rescope"),
      `projects.p.rescope ${JSON.stringify(bad)}`,
    );
  }
});

test("rescope_timeout: число, дефолт 3600, 0 и строка — ConfigError", () => {
  assert.equal(swarmConfigFor(parseSwarmConfig(JSON.stringify({rescope_timeout: 42})), "p").rescopeTimeout, 42);
  assert.equal(swarmConfigFor(parseSwarmConfig("{}"), "p").rescopeTimeout, 3600);
  for (const bad of [0, "x"]) {
    assert.throws(
      () => parseSwarmConfig(JSON.stringify({rescope_timeout: bad})),
      (err) => err instanceof ConfigError && err.message.includes("rescope_timeout ожидал число > 0"),
      `rescope_timeout ${JSON.stringify(bad)}`,
    );
  }
});
