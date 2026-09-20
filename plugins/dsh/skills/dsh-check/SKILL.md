---
name: dsh-check
description: Check whether DeepSeek Harness is ready to work — binary, the headless profile, the active model and provider route, credentials, and how many background jobs are already running.
when_to_use: Triggers — "is dsh working", "check the DeepSeek harness", "why doesn't dsh answer", "what model does dsh use". Run it on your own when a delegation failed at launch. This is about harness readiness, not about work in flight — "dsh is silent" after a background job is /dsh:dsh-jobs, and posing a task is /dsh:dsh-delegate.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh *)
---

Current state of DeepSeek Harness:

```
!`${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh check`
```

Report readiness to the human in one paragraph — ready or not, and on which model. Don't
retell the whole report. `check --json` returns the same data with keys `ready`, `binary`,
`profiles`, `provider`, `model`, `credentials`, `pi_ai_routes`, `running_jobs`.

## Reading the output

- **`ready: yes` needs the binary plus the `headless` profile** — that profile is what runs
  one task without a UI; without it delegation is impossible.
- `binary: not found` — dsh isn't installed, or the session started before `PATH` changed;
  `broken` — the binary exists but its version check fails.
- An empty `model` means the settings file was never created and dsh will use its own
  default.
- `pi-ai routes` lists the routes raised by the `llm-pi-ai:` section. A `—` is normal for
  the native `deepseek-official` provider and a breakage only if the provider in the `model`
  line was supposed to come from that section.
- **`credentials: absent` is not a failure by itself.** That field is the web-UI login; a
  harness running on an API key keeps the key in an environment variable named by
  `apiKeyEnv`, not in this store. Never read or print the contents of the credentials
  directory.

## What to fix and how

- **Binary missing right after an install** — this session's `PATH` is stale. Ask the human
  for a fresh terminal; don't go checking `brew`/`npm` yourself.
- **`MISSING_CREDENTIAL` on a run** — the variable named by `apiKeyEnv` is absent from the
  environment Claude Code started in, usually because the `export` was added after the
  session started. A restart from a fresh terminal fixes it. Never print the value.
- **Login not done** — authentication is interactive: suggest `dsh --profile web` and a
  re-check. On an API key no login is needed at all.
- **Wrong model** — the human changes it in `~/.dsh/settings.yaml`. Neither this skill nor
  delegation switches models.
- **Ready but the run returns nothing** — not a readiness problem: `transcript` shows what
  the harness actually did.

Diagnosis ends here: installing and authenticating are the human's actions, not yours. Once
ready, delegate via `/dsh:dsh-delegate`. Red lines and the full script contract:
`dsh-runtime`.
