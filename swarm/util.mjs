// Общие мелкие помощники роя. Без импортов из других файлов swarm/ — чтобы не завести циклов.

const MAIN_WORKTREE_MARKERS = new Set(["main", "master"]);

export function errText(err) {
  return `${err.code ?? "error"}/${err.message ?? err}/${err.hint ?? ""}`;
}

export function budgetBound(n) {
  return n > 0 ? String(n) : "∞";
}

export function spentShown(n) {
  return String(Math.round(n * 10) / 10);
}

export function cyclesDesc(cycles) {
  return cycles.map(c => [...c, c[0]].join(" → ")).join("; ");
}

export function isMainWorktree(worktree) {
  return MAIN_WORKTREE_MARKERS.has((worktree || "").trim().toLowerCase());
}

export function taskBranch(task) {
  return (task.branch || "").trim() || `task/${task.id}`;
}

export function tokenRejectedText(status) {
  return `сервер отвечает, но токен CLI не принят — проверь, какой listik и какой ` +
    `каталог данных: ${status.bin_path}, ${status.data_dir}`;
}
