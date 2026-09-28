// Общая git-фикстура тестов роя: изоляция окружения git + помощники репозитория.
// Импорт модуля сам выставляет GIT_* в process.env, поэтому его статический import
// первой строкой-импортом достаточно (swarm/git.mjs читает env в момент вызова,
// а не при загрузке).
import {execFileSync} from "node:child_process";
import {mkdtempSync, writeFileSync, mkdirSync} from "node:fs";
import {tmpdir} from "node:os";
import {join} from "node:path";

// `git` может отсутствовать на машине судьи — тогда блоки тестов пропускаются целиком.
let gitAvailable = true;
try {
  execFileSync("git", ["--version"], {stdio: "ignore"});
} catch {
  gitAvailable = false;
}

// Прод без TTY не должен вставать на `vi` — окружение субпроцессов воспроизводит это:
// приёмка не зависит от `EDITOR`/`GIT_EDITOR` машины судьи.
if (gitAvailable) {
  const emptyConfig = join(mkdtempSync(join(tmpdir(), "swarm-test-gitcfg-")), "gitconfig");
  writeFileSync(emptyConfig, "");
  Object.assign(process.env, {
    GIT_CONFIG_GLOBAL: emptyConfig,
    GIT_CONFIG_NOSYSTEM: "1",
    GIT_EDITOR: "true",
    GIT_TERMINAL_PROMPT: "0",
  });
}

export function sh(cwd, ...args) {
  return execFileSync("git", args, {cwd, encoding: "utf8"});
}

export function initRepo(prefix, files = {"README.md": "start\n"}, message = "start") {
  const repo = mkdtempSync(join(tmpdir(), prefix));
  sh(repo, "init", "-q", "-b", "main");
  sh(repo, "config", "user.name", "Test");
  sh(repo, "config", "user.email", "test@example.com");
  const names = Object.keys(files);
  for (const name of names) writeFileSync(join(repo, name), files[name]);
  sh(repo, "add", ...names);
  sh(repo, "commit", "-q", "-m", message);
  return repo;
}

export function addWorktree(repo, id) {
  const path = join(repo, ".worktrees", id);
  mkdirSync(join(repo, ".worktrees"), {recursive: true});
  sh(repo, "worktree", "add", "-q", "-b", `task/${id}`, path, "HEAD");
  return path;
}

export {gitAvailable};
