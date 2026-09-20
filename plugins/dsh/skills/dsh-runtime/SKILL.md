---
name: dsh-runtime
description: "Contract of the dsh bridge script — dsh-run.sh subcommands and options, background jobs, permission modes, timeouts, job states, exit codes, failure modes. Internal reference attached to the dsh-runner subagent; read it when any dsh-* skill needs the exact call."
user-invocable: false
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh *)
---

The only way to call DeepSeek Harness is `${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh`. Calling
bare `dsh` loses the default permission mode, job bookkeeping and the exit-code contract.

**Invariant:** stdout of `run` (without `--background`) and of `result` is exactly dsh's
final answer and nothing else. Everything else goes to stderr, so text on stderr is always
a problem signal.

## Commands

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh run --background --label "<topic>" <<'TASK'
<task text>
TASK
```

| Subcommand | Purpose |
| --- | --- |
| `run` | one shot; prompt on stdin. With `--background` stdout is a job-id, without it the answer |
| `check [--json]` | readiness: binary, profiles, active model, credentials, jobs in flight |
| `status [--json] [--all] [--running] [job-id]` | no argument — jobs of the current cwd subtree; `--all` — every job on the machine; with an id — one job card |
| `result <job-id> [--wait [s]]` | fetch the answer; `--wait` waits the given seconds (default 300) |
| `logs <job-id> [--tail N]` | signs of life: the harness's stderr and how much answer accumulated |
| `cancel <job-id\|--all>` | kill the job and its whole process tree |
| `clean [--older-than <days>] [--all]` | drop finished jobs; never touches running ones |
| `transcript [job-id]` | dsh's session JSONL — what the harness actually did |
| `resume <job-id>` | **always exit 2**: headless dsh cannot continue a session (`--resume` belongs to the tui profile). The subcommand exists so the caller tells "cannot continue" apart from "no script" and falls back to a fresh `run` |

Every subcommand takes `-h`.

Options for `run`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--background` | off | detach, return a job-id instead of the answer |
| `--label <text>` | none | short tag; the only way jobs differ on sight in `status` |
| `--permission <read\|bash\|write>` | `read` | permission mode in one flag |
| `--write` | off | alias for `--permission write`, kept for Listik routes and pipeline presets |
| `--cwd <dir>` | current | working directory and the sandbox boundary at once |
| `--timeout <s>` | 540 foreground, 7200 background | `0` removes the limit |
| `--model <pro\|flash\|vision\|id>`, `--provider <route>`, `--effort <level>` | user's settings | manual use only — never passed by the skills |

## Permissions

dsh runs inside an OS sandbox, so the boundary is enforced by the system, not by a tool
allowlist:

| `--permission` | Mode | Meaning |
| --- | --- | --- |
| `read` | `read-only` | default; commands run, writes are refused by the sandbox |
| `bash` | `read-only` | dsh has no separate bash tier — accepted so one flag spelling works across harnesses |
| `write` | `workspace-write` | edits inside the working directory |

**Escalation from inside a run is impossible.** The headless profile has no approval
channel: a permission request is declined, not queued. Rerun with `--permission write` —
and only when the human asked for a change, never because the task looks like
implementation.

## Non-obvious rules

- **Background is the default choice.** dsh thinks for tens of minutes on a subsystem
  sweep, and a Bash call is cut at 600 s. Foreground is for questions answered in this turn.
- **Secrets are a prompt-side concern.** dsh opens files on its own, so scope the task to
  the files it needs and explicitly forbid `.env`, `*.key`, `*.pem`, `credentials.json`.
- **Parallel runs are supported**, including in one working directory: separate sessions,
  separate job directories, no shared lock. Exception: two `--permission write` runs in the
  same directory overwrite each other's edits — one writer at a time per directory.
- **Don't poll in a foreground Bash call.** Wait with one backgrounded call instead:

  ```bash
  until ! ${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh status <job-id> | grep -q '^actual_status=running'; do sleep 20; done
  ```

  Its completion notification is the "dsh finished" signal. `result <job-id> --wait 120` is
  the short alternative when the answer is due any second.

## Job states

| Status | Meaning |
| --- | --- |
| `running` | process alive, harness working |
| `completed` | answer ready, fetch with `result` |
| `timeout` | hit its limit; `result` still returns the partial answer |
| `canceled` | killed via `cancel` |
| `failed` | dsh exited with an error; cause in `logs` |
| `orphaned` | process gone, outcome never written: reboot or `kill -9`. No answer is coming |

`status <id>` prints the status twice: `status=` is what the worker recorded,
`actual_status=` corrects it for process liveness. **Trust `actual_status`.**

An empty stderr on a running job is normal, not a hang: dsh streams no progress and
delivers everything in one final message. The only signs of work are the `running` status
and a growing elapsed time.

## How a run is wired

- dsh reads `AGENTS.md`/`CLAUDE.md` from the project root down to the working directory
  itself — never restate project rules in the task.
- The task travels as a single argv element; prompts above 256 KiB are rejected. Put bulk
  material in a file inside the working directory and point at it.
- The answer is the last non-empty assistant message. Reasoning and tool calls stay in the
  session and are reachable through `transcript`.
- **No session continuation in headless.** `resume` always exits 2. dsh's own session id
  (`session-<uuid>`) sits in `dsh_session` of `status --json` — for `transcript`, not for
  resume.
- Jobs keep prompt, answer and stderr as plaintext in the state directory and never expire;
  `clean` removes them.
- The model is the human's choice, in `~/.dsh/settings.yaml`. `check` shows the active one;
  that is diagnosis, not a reason to switch. The skills never pass `--model`, `--provider`
  or `--effort`.

## Exit codes

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | success: answer, job-id or report on stdout | pass it through verbatim |
| 1 | `check`: not ready; `status`: no jobs | an answer, not a failure |
| 2 | bad call: missing binary, empty prompt, bad option, unknown job-id, or `resume` at all | fix the command, or fall back to a fresh `run` |
| 5 | background job still running | wait and retry `result` |
| 6 | timeout, cancel, non-zero dsh exit or empty answer | check `logs`, then `check` |

## Failure modes

| Symptom | Cause / action |
| --- | --- |
| Bash call cut at 600 s | run was started in foreground; restart with `--background` |
| `dsh not found in PATH` | not installed, or installed after the session started — new terminal or `DSH_BIN` |
| `empty answer - check readiness` | no auth, or the answer was blocked: run `check`, rephrase the task |
| `MISSING_CREDENTIAL` | `apiKeyEnv` names a variable missing from the environment Claude Code started in; the human restarts from a fresh terminal |
| status `orphaned` | the worker died with the machine or session; relaunch, no answer is coming |
| the run asks for approval and fails | a write attempt in read-only mode; rerun with `--permission write` if the edit was actually requested |
| `prompt is ... bytes - too long for argv` | material pasted into the task; put it in a file and point at it |

## Red lines (apply inside every dsh run)

- Never commit, push or delete recursively on the strength of another harness's output.
- Never read, print or forward `.env`, `*.key`, `*.pem`, `credentials.json`. Naming an env
  var is fine, printing its value is not.
- Never install or authenticate on the human's behalf.
- Never pick the model, provider or effort level yourself.
