import {test} from "node:test";
import assert from "node:assert/strict";
import {parseConfig, ConfigError, HelpRequested} from "../config.mjs";

test("дефолты", () => {
  const config = parseConfig(["--project", "listik"]);
  assert.equal(config.listikBin, "listik");
  assert.equal(config.cliTimeout, 120);
  assert.equal(config.parallel, 3);
  assert.equal(config.portBase, 5170);
  assert.equal(config.portCount, 100);
  assert.equal(config.interval, 30);
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

test("без --project — ошибка", () => {
  assert.throws(() => parseConfig([]), ConfigError);
});

test("--help — HelpRequested", () => {
  assert.throws(() => parseConfig(["--help"]), HelpRequested);
});
