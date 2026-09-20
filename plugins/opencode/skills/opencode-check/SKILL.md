---
name: opencode-check
description: "Check whether opencode is ready to work — binary, version, both channels (glm = b.ai/glm-5.3-flash by default, deepseek = b.ai/deepseek-v4.1-flash), provider credentials, what parses the event stream, and how many background jobs are already running."
when_to_use: "Triggers — \"is opencode working\", \"check opencode\", \"why doesn't opencode answer\", \"what model does opencode use\". Run it on your own when a delegation failed at launch. This is about harness readiness, not about work in flight — \"opencode is silent\" after a background job is /opencode:opencode-jobs, and posing a task is /opencode:opencode-delegate."
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/opencode-run.sh *)
---

Current state of opencode:

```
!`${CLAUDE_PLUGIN_ROOT}/scripts/opencode-run.sh check || true`
```

Report readiness to the human in one paragraph — default channel, the other channel,
credentials, jobs in flight. Don't retell the whole report. `check --json` returns the same
data with keys `ready`, `binary_status`, `model_status`, `channels[].status`, `auth_status`,
`json_parser`, `running_jobs`, `state_dir`.

## Reading the output

- `binary: not found` — opencode isn't installed, or the session started before `PATH`
  changed; `broken` — the binary exists but `--version` fails.
- `not in the model catalog` means the provider isn't wired up or the model was renamed, not
  that the model does not exist; `could not check` means `opencode models` did not answer
  (usually no network). Only `deepseek` unavailable still leaves `ready: yes` — but don't
  route a run to that channel.
- `credentials: not found` — the provider key is missing; the human adds it with
  `opencode providers` (aka `opencode auth`). Never print a key value.
- `answer parser: none` is fatal: the answer is assembled from the `--format json` event
  stream, so without `python3` or `jq` runs refuse to start. `brew install jq` is the only
  external requirement of this bridge.
- Ready but a run comes back empty — that is not a readiness problem: look at
  `transcript <job-id>` to see what opencode actually did.

Diagnosis ends here: installing and authenticating are the human's actions, not yours.
Don't go checking `brew`/`npm`/`curl` on your own. Once ready, delegate via
`/opencode:opencode-delegate`. Red lines and the full script contract: `opencode-runtime`.
