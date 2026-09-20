---
name: pi-check
description: Check whether pi is ready to work — binary, version, rpc client and python3, both channels (glm = b-ai-glm/glm-5.3-flash by default, deepseek = b-ai-deepseek/deepseek-v4.1-flash), and how many background jobs are already running.
when_to_use: Triggers — "is pi working", "check pi", "why doesn't pi answer", "what channel does pi use". Run it on your own when a delegation failed at launch. This is about harness readiness, not about work in flight — "pi is silent" after a background job is /pi:pi-jobs, and posing a task is /pi:pi-delegate.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pi-run.sh *)
---

Quick probe (shortened ceiling so the injection doesn't hit the execution limit):

```
!`${CLAUDE_PLUGIN_ROOT}/scripts/pi-run.sh check --probe-timeout 30 || true`
```

Full probe is `check` without `--probe-timeout` (up to 120 s per channel) — offer it if a
channel timed out above. `check --json` returns the same data with keys `ready`,
`default_channel`, `probe_timeout`, `channels[].probe` (`ok|timeout|error|skipped`),
`state_dir`.

Report readiness to the human in one paragraph — default channel, probe result per
channel, jobs in flight. Don't retell the whole table.

## Reading the output

- **`ready: yes` needs only one channel to answer**, so a `glm` timeout next to a
  responding `deepseek` does not mean the harness is down.
- `binary: not found` — pi isn't installed, or the session started before `PATH` changed;
  `broken` — the binary exists but its version check fails.
- `rpc client: file missing` means a damaged plugin; `python3: missing` means runs won't
  start at all (hard requirement of `pi-rpc.py`).
- `in catalog: no` means "not among the models with configured auth", not "no such model":
  the provider isn't wired up or was renamed. The `~/.pi/agent/extensions/b-ai.ts`
  extension is the human's to set up.
- Probe `timeout` — the channel stalled before the first token. Not a bridge failure: say
  so and offer a retry or the other channel. **The human picks; never substitute the
  channel yourself.**
- Probe `error` — read the text next to it: 401 is usually a key problem, 404 a wrong
  model name.
- A non-standard `state directory` means `PI_CLAUDE_STATE_DIR` is set; a non-standard
  default channel means `PI_CLAUDE_DEFAULT_MODEL` is.
- **`pi auth check` lies for these providers** (`not_ready` / `provider_not_found`) even
  when runs succeed, because they come from an extension rather than static config.
  Readiness here is decided by the probe run, never by `auth check`.

Diagnosis ends here: installing and authenticating are the human's actions, not yours.
Don't go checking `brew`/`npm`/`curl` on your own. Once ready, delegate via
`/pi:pi-delegate`. Red lines and the full script contract: `pi-runtime`.
