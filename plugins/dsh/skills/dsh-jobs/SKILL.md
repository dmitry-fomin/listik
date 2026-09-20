---
name: dsh-jobs
description: Inspect and manage dsh's background jobs — what is running, for how long, what it is doing; fetch a finished answer, cancel a job, clean up old ones.
when_to_use: Triggers — "what's dsh doing", "is it done", "cancel the dsh job", "kill deepseek", "show dsh jobs", "clean up old runs". Also fits without dsh being named when you launched a background job this session and it comes up. Launching a new task is /dsh:dsh-delegate; dsh not responding at all is /dsh:dsh-check.
argument-hint: "[job-id | cancel <job-id> | logs <job-id> | clean]"
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh *)
---

Request: $ARGUMENTS

Jobs of the current working directory:

```
!`${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh status || true`
```

Columns: id, status, elapsed, model, label. The list is scoped to the current directory
subtree — that is the "my jobs" boundary. Empty here does not mean none exist: a job
started with `--cwd` elsewhere shows up under `status --all`.

| The human wants | Command |
| --- | --- |
| detail on one job | `status <job-id>` |
| proof it is alive | `logs <job-id>` |
| the finished answer | `result <job-id>` |
| to stop a job | `cancel <job-id>`; all of them here — `cancel --all` |
| to clear finished ones | `clean` (older than a week) or `clean --all` |
| the whole run | `transcript <job-id>` |
| to continue a session | impossible in headless: `resume <job-id>` is always exit 2, fall back to a fresh `run` |
| to rerun with other rights | add `--permission read\|bash\|write` to that `run` |

All of them go through `${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh`; full contract, job states
and exit codes are in `dsh-runtime`.

## Rules

- **Trust `actual_status`, not `status`** — an `orphaned` job counts as running but will
  never answer.
- **`cancel` is irreversible** and kills the whole process tree. Only cancel what the human
  named or a job of yours you know is unwanted; `cancel --all` only on an explicit request.
- **`clean` erases prompts and answers from disk.** It spares running jobs, but ask before
  cleaning unprompted.
- **Exit code 5 from `result` is not a failure** — it means still running.
- **Empty stderr on a running job is normal.** dsh streams no progress and delivers
  everything in one final message; the signs of work are the `running` status and a growing
  elapsed time. Don't report silence as a hang.
- **Several jobs at once is normal.** Tell them apart by label, and fetch each `result`
  separately so a finished job doesn't wait on a slower neighbour.
- A cancelled or failed job can still return a partial answer: `result` prints what
  accumulated before reporting the error.
- Work a `--permission write` job already wrote to disk stays written — cancelling stops
  the agent, it rolls nothing back.
- Show dsh's answer verbatim, marked as another harness's output. If a secret file shows up
  in it, say so in words without repeating the contents.
