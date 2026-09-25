import {test} from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {open, skipLabel, needsOwnerLabel} from "../log.mjs";

test("skipLabel: подписи причин пропуска", () => {
  const cases = [
    [{reason: "sliced"}, "нарезана, ждёт порции"],
    [{reason: "capacity"}, "не влезла в партию"],
    [{reason: "foo"}, "foo"],
    [{reason: "no_launched_at"}, "no_launched_at"],
    [{reason: "held", holder: "dsh"}, "держит dsh"],
    [{reason: "held"}, "держит другой"],
    [{reason: "frozen"}, "заморожена"],
    [{reason: "gated"}, "гейт"],
    [{reason: "budget"}, "бюджет"],
    [{reason: "unroutable"}, "без маршрута"],
  ];
  for (const [s, want] of cases) assert.equal(skipLabel(s), want, s.reason);
});

test("needsOwnerLabel: подписи причин вопроса", () => {
  assert.equal(needsOwnerLabel({reason: "sliced_stuck"}), "порции не запускаются");
  assert.equal(needsOwnerLabel({reason: "unscoped"}), "без области");
  assert.equal(needsOwnerLabel({reason: "unroutable"}), "без маршрута");
  assert.equal(needsOwnerLabel({reason: "bar"}), "bar");
});

test("summary: честные причины пропуска и вопроса", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "listik-swarm-log-"));
  const log = open(dir, "p");
  const write = process.stdout.write;
  process.stdout.write = () => true;
  let line;
  try {
    line = log.summary({
      project: "p", waveSize: 0,
      skipped: [{id: "a", reason: "sliced"}],
      needsOwner: [{id: "b", reason: "sliced_stuck"}],
    });
  } finally {
    process.stdout.write = write;
    log.close();
  }
  assert.ok(line.includes("a нарезана, ждёт порции"));
  assert.ok(line.includes("b порции не запускаются"));
  assert.ok(!line.includes("a не влезла"));
});
