#!/usr/bin/env node
// Подставной арбитр для `arbiter.test.mjs`. argv: `<prompt-path> <files>` (`files` —
// список через запятую, как формирует `renderArgv`). Читает промпт из файла по
// `<prompt-path>`, пишет его копию в `$FAKE_ARBITER_PROMPT_FILE` (если задан, с
// суффиксом-счётчиком — несколько остановок пишут несколько файлов). Режим —
// `FAKE_ARBITER_MODE` (по умолчанию `ok`).
import fs from "node:fs";
import path from "node:path";
import {execFileSync} from "node:child_process";

const [promptPath, filesArg] = process.argv.slice(2);
const files = (filesArg || "").split(",").map(s => s.trim()).filter(Boolean);
const mode = process.env.FAKE_ARBITER_MODE || "ok";

// Для проверки инварианта «арбитр не получает LISTIK_*» — необязательный дамп окружения,
// активен только если тест явно задал файл-приёмник.
if (process.env.FAKE_ARBITER_ENV_FILE) {
  fs.writeFileSync(process.env.FAKE_ARBITER_ENV_FILE, JSON.stringify(process.env), "utf8");
}

const promptText = fs.readFileSync(promptPath, "utf8");
const promptCopyBase = process.env.FAKE_ARBITER_PROMPT_FILE;
if (promptCopyBase) {
  let n = 1;
  let target = promptCopyBase;
  while (fs.existsSync(target)) {
    n++;
    const ext = path.extname(promptCopyBase);
    target = `${promptCopyBase.slice(0, promptCopyBase.length - ext.length)}-${n}${ext}`;
  }
  fs.writeFileSync(target, promptText, "utf8");
}

// Убирает один блок маркеров diff3: текст между `<<<<<<< ` и `||||||| `, затем текст
// между `=======` и `>>>>>>> `, без маркеров и без блока базы.
function resolveMarkers(text) {
  const re = /<<<<<<< [^\n]*\n([\s\S]*?)\|\|\|\|\|\|\| [^\n]*\n[\s\S]*?=======\n([\s\S]*?)>>>>>>> [^\n]*\n/g;
  return text.replace(re, (_m, ours, theirs) => ours + theirs);
}

function pidFile() {
  return process.env.FAKE_ARBITER_PID_FILE || path.join(process.cwd(), ".arbiter-pid");
}

switch (mode) {
  case "leave":
    process.exit(0);
    break;

  case "fail":
    process.stdout.write("подставной арбитр: непротиворечивое слияние невозможно\n");
    process.exit(1);
    break;

  case "hang": {
    fs.writeFileSync(pidFile(), String(process.pid), "utf8");
    setTimeout(() => process.exit(0), 60000);
    break;
  }

  case "git":
    // Резолвит как "ok", но сам завершает ребейз — то, что арбитру запрещено делать.
    for (const f of files) {
      const text = fs.readFileSync(f, "utf8");
      fs.writeFileSync(f, resolveMarkers(text), "utf8");
    }
    execFileSync("git", ["add", ...files], {stdio: "ignore"});
    execFileSync("git", ["-c", "core.editor=true", "rebase", "--continue"], {stdio: "ignore"});
    process.exit(0);
    break;

  case "ok":
  default:
    for (const f of files) {
      const text = fs.readFileSync(f, "utf8");
      fs.writeFileSync(f, resolveMarkers(text), "utf8");
    }
    process.exit(0);
    break;
}
