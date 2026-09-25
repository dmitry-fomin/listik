---
name: devin-check
description: "Check whether devin is ready to work — binary, version, python3, its session database, the SWE-2 model catalogue and a real probe run, plus how many background jobs are already running."
when_to_use: Triggers — "is devin working", "check devin", "why doesn't devin answer", "what model does devin use". Run it on your own when a delegation failed at launch. This is about harness readiness, not about work in flight — "devin is silent" after a background job is /devin:devin-jobs, and posing a task is /devin:devin-delegate.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh *)
---

Quick probe (shortened ceiling so the injection doesn't hit the execution limit):

```
!`${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh check --probe-timeout 30 || true`
```

Full probe is `check` without `--probe-timeout` (up to 120 s). `check --json` returns the
same data with keys `ready`, `default_model`, `probe` (`ok|timeout|error|skipped`),
`sandbox_default`, `state_dir`.

Report readiness to the human in one paragraph — model, probe result, jobs in flight.
Don't retell the whole table.

## Reading the output

- **The probe is the verdict**: `ready: yes` requires a real run that answered, so a
  `timeout` line means the harness is not usable right now even if the binary is fine.
- `binary: not found` — devin isn't installed, or the session started before `PATH`
  changed; `broken` — the binary exists but `devin version` fails.
- `sessions db: missing` — `status`/`logs`/`transcript` lose the devin side of a run
  (session ids and turns); runs themselves still work.
- `python3: missing` degrades the same parts: the bridge reads devin's sqlite session
  database through python3.
- `in catalog: no` means the model is not in `devin models list` — usually an account
  without SWE-2 rather than a wrong name; the probe is still the final word.
- `OS sandbox: off by default` is the intended state, not a misconfiguration: devin has to
  be able to write code. `--sandbox` per run turns it on.
- `workspace trust: skipped` is the intended state: the bridge always passes
  `--respect-workspace-trust false`, so a fresh worktree runs without being trusted first.
- A non-standard `state directory` means `DEVIN_CLAUDE_STATE_DIR` is set.

Diagnosis ends here: installing and authenticating are the human's
actions, not yours. Don't go checking `brew`/`npm`/`curl` on your own. Once ready, delegate
via `/devin:devin-delegate`. Red lines and the full script contract: `devin-runtime`.
