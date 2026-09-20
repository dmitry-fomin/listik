#!/usr/bin/env node
// Подставной `listik`: пишет полученный argv в файл (LISTIK_FAKE_CALLS_FILE) и
// отвечает по подкоманде канонным JSON из фикстуры (LISTIK_FAKE_RESPONSES_FILE),
// {sleepMs, exitCode, stdout} по ключу — первый позиционный аргумент после
// глобальных --host/--port, если он есть, иначе первый аргумент вообще.
import fs from "node:fs";

const argv = process.argv.slice(2);
const callsFile = process.env.LISTIK_FAKE_CALLS_FILE;
const responsesFile = process.env.LISTIK_FAKE_RESPONSES_FILE;

// Первый аргумент, не являющийся --host/--port и не значением после них.
let sub = null;
for (let i = 0; i < argv.length; i++) {
  if (argv[i] === "--host" || argv[i] === "--port") {
    i++;
    continue;
  }
  sub = argv[i];
  break;
}

// Счётчик предыдущих вызовов этой подкоманды — по уже записанным строкам файла
// вызовов (каждый вызов — отдельный процесс, поэтому в памяти не посчитать).
let priorCalls = 0;
if (callsFile && fs.existsSync(callsFile)) {
  for (const l of fs.readFileSync(callsFile, "utf8").split("\n")) {
    if (!l) continue;
    try {
      if (JSON.parse(l).sub === sub) priorCalls++;
    } catch { /* ignore */ }
  }
}

if (callsFile) {
  fs.appendFileSync(callsFile, JSON.stringify({sub, argv}) + "\n");
}

const responses = responsesFile ? JSON.parse(fs.readFileSync(responsesFile, "utf8")) : {};

const responseQueue = responses[sub];
let entry;
if (Array.isArray(responseQueue)) {
  entry = responseQueue[Math.min(priorCalls, responseQueue.length - 1)];
} else {
  entry = responseQueue;
}

if (!entry) {
  process.stderr.write(`fake-listik: no fixture for ${sub}\n`);
  process.exit(9);
}

async function main() {
  if (entry.sleepMs) {
    await new Promise(r => setTimeout(r, entry.sleepMs));
  }
  if (entry.stdout !== undefined) {
    process.stdout.write(entry.stdout);
  }
  process.exit(entry.exitCode || 0);
}

main();
