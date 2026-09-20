---
name: codex-check
description: Check whether OpenAI Codex CLI is ready to work — binary, codex doctor, the active provider and model, credentials, and how many background jobs are already running.
when_to_use: Triggers — "is codex working", "check codex cli", "why doesn't codex answer", "what model does codex use". Run it on your own when a delegation failed at launch. This is about harness readiness, not about work in flight — "codex is silent" after a background job is /codex:codex-jobs, and posing a task is /codex:codex-delegate.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh *)
---

Current state of OpenAI Codex CLI:

```
!`${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh check`
```

Report readiness to the human in one paragraph — ready or not, and the active
provider/model. Don't retell the whole table. `check --json` returns the same data with
keys `ready`, `overall_status`, `auth_status`, `model`, `provider`, `app_server_status`,
`running_jobs`.

## Reading the output

- `ready` is `yes` only when the binary answers *and* `codex doctor` reports
  `overallStatus: ok`; the lines below it say which part broke.
- `binary: not found` — codex isn't installed, or the session started before `PATH`
  changed; `broken` — the binary exists but `--version` fails.
- `credentials` — the text in parentheses is `codex doctor`'s own summary. If `key env var`
  names a variable and auth still fails, the variable was not exported in the environment
  Claude Code itself started from (an `export` after session start does not reach it):
  a restart from a new terminal fixes it. Never print the variable's value.
- `model`/`app-server` come from `~/.codex/config.toml` and codex's background app-server.
  Empty model means `codex doctor` could not read the config.
- **`codex doctor` not `ok` with no clear reason** — run `codex doctor` directly (no
  `--json`, no `--summary`) and read the human-readable report with its `remediation`
  hints, which this one-line summary drops.
- **Ready but a run comes back empty** — that is not a readiness problem: look at
  `transcript` to see what codex actually did.

The model and provider are the human's to change in `~/.codex/config.toml`; installing and
authenticating (`codex login`) are the human's actions too. Diagnosis ends here — don't go
checking `brew`/`npm` on your own. Once ready, delegate via `/codex:codex-delegate`. Red
lines and the full script contract: `codex-runtime`.
