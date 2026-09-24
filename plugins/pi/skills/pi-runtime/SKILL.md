---
name: pi-runtime
description: Contract of the pi bridge script — pi-run.sh subcommands and options, background jobs, named sessions, permission modes, timeouts, job states, exit codes, failure modes. Internal reference attached to the pi-runner subagent; read it when any pi-* skill needs the exact call.
user-invocable: false
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pi-run.sh *)
---

The only way to call pi is `${CLAUDE_PLUGIN_ROOT}/scripts/pi-run.sh`. Calling bare `pi`
loses the default permission mode, job bookkeeping, the RPC client and session names.

**Invariant:** stdout of `run`/`resume` (without `--background`) and of `result` is exactly
pi's final answer and nothing else. Everything else goes to stderr, so text on stderr is
always a problem signal.

## Commands

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/pi-run.sh run --background --session "<name>" --label "<topic>" <<'TASK'
<task text>
TASK
```

| Subcommand | Purpose |
| --- | --- |
| `run` | new session; prompt on stdin. With `--background` stdout is a job-id, without it the answer |
| `resume <name\|job-id>` | continue an existing session; prompt on stdin, same flags as `run` |
| `check [--json] [--no-probe] [--probe-timeout <s>]` | readiness: binary, version, rpc client, python3, both channels. Probes run in parallel, up to 120 s per channel; exit 1 only if no channel answered |
| `status [--json] [--all] [--running] [job-id]` | no argument — jobs of the current cwd subtree; `--all` — every job on the machine; with an id — one job card |
| `result <job-id> [--wait [s]]` | fetch the answer; `--wait` waits the given seconds (default 300) |
| `logs <job-id> [--tail N]` | event stream: which tools the run called, how much answer accumulated |
| `cancel <job-id\|--all>` | kill the job and its whole process tree (`kill` is a synonym) |
| `clean [--older-than <days>] [--all]` | drop finished jobs; never touches running jobs or pi's own session files |
| `sessions [--json]` | sessions known by name: name, id, channel model, last update, directory |
| `transcript [job-id] [--session <name>]` | print pi's session JSONL — what the conversation actually was |

Every subcommand takes `-h`.

Options for `run`/`resume`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--background` | off | detach, return a job-id instead of the answer |
| `--label <text>` | none | short tag; the only way jobs differ on sight in `status` |
| `--session <name>` | none | `run` creates it, `resume` finds it |
| `--permission <read\|bash\|write>` | `read` | permission mode in one flag |
| `--write` / `--bash` | off | aliases for `--permission write` / `--permission bash`, kept for Listik routes and pipeline presets |
| `--cwd <dir>` | current | working directory of the run |
| `--timeout <s>` | 540 foreground, 7200 background | `0` removes the limit |
| `--model <channel\|provider/model>` | `glm` = `b-ai-glm/glm-5.3-flash` | short channel name or a full `provider/model` id |
| `--channel <channel>` | `glm` | short channel name only; never combine with `--model` |
| `--thinking <level>` | model default | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` — this list only |

## Non-obvious rules

- **Background is the default choice.** A pi run blocks until the agent finishes, and a
  subsystem sweep easily outlives the 600 s Bash-call ceiling. Foreground is for questions
  answered inside the current turn.
- **Quote the heredoc marker** (`<<'TASK'`): task text is almost always code, and an
  unquoted marker lets the shell expand `$` and backticks. The prompt goes to the RPC
  client on stdin, so it has no size limit here.
- **`--permission bash` is not read-only.** pi has no OS-level sandbox; with bash allowed
  the model writes files through plain `>`. The real boundary is the bash ban, the default.
- **Secrets are a prompt-side concern.** pi opens files on its own, so scope the task to
  the files it needs and explicitly forbid `.env`, `*.key`, `*.pem`, `credentials.json`.
- **Channel and thinking level are the human's choice.** Runs go on `glm`
  (`b-ai-glm/glm-5.3-flash`). Add `--channel`/`--model`/`--thinking` only when the human
  named it, or when a pipeline preset passes it — model-per-role is part of the preset.
  A default-channel timeout is *not* an exception: report it and offer the choice instead
  of silently switching.
- **DeepSeek confabulates** — it will confidently name files, flags and functions that do
  not exist. Treat `deepseek` answers as claims to verify against the code.
- **Parallel runs are supported**, including in one working directory — separate sessions,
  separate job directories, no shared lock. Exception: two `--permission write` runs in the same
  directory overwrite each other's edits, and two runs into one session interleave their
  transcripts.
- **Don't poll in a foreground Bash call.** Wait with one backgrounded call instead:

  ```bash
  until ! ${CLAUDE_PLUGIN_ROOT}/scripts/pi-run.sh status <job-id> | grep -q '^actual_status=running'; do sleep 20; done
  ```

  Its completion notification is the "pi finished" signal.

## Job states

| Status | Meaning |
| --- | --- |
| `running` | process alive, pi working |
| `completed` | answer ready, fetch with `result` |
| `timeout` | hit its limit; `result` still returns the partial answer |
| `canceled` | killed via `cancel` |
| `failed` | pi exited with an error; cause in `logs` |
| `orphaned` | process gone, outcome never written: reboot or `kill -9`. No answer is coming |

`status <id>` prints the status twice: `status=` is what the worker recorded,
`actual_status=` corrects it for process liveness. **Trust `actual_status`.** pi exiting is
not yet `completed` — the worker still has to collect the answer, and during those seconds
the job is honestly `running`.

## Sessions

- A session is addressed by its **file path**, not a uuid, so `resume` survives a change of
  working directory between calls. Fallback lookup by name scans `~/.pi/agent/sessions`.
- `resume` inherits the directory, permission mode, channel and thinking level of the
  original run unless flags override them. Switching channel mid-session keeps the history.
- One name = one session: `run --session <taken name>` is exit 2, and so is `resume` on a
  name that does not exist — fall back to a fresh `run` with the current text.
- Job state (prompt, event stream, answer) is stored in plaintext in the state directory
  and never expires; `clean` removes it. pi's own session files live separately in
  `~/.pi/agent/sessions` and `clean` leaves them alone.

## How a run is wired

- pi reads `AGENTS.md`/`CLAUDE.md` in the working directory itself — never restate project
  rules in the task.
- Each run is `pi-rpc.py` (python3 required) talking to `pi --mode rpc` and printing the
  final assistant message from the event stream.
- A provider error arrives as `stop_reason=error` — that is job status `failed`, not a
  script crash; the text is in `logs`.
- `~/.pi/agent/extensions/b-ai.ts` registers the `b-ai-glm` / `b-ai-deepseek` providers.
  Disabling it removes both channels. `PI_CLAUDE_DEFAULT_MODEL` moves the default,
  `PI_CLAUDE_BIN` points at the binary, `PI_CLAUDE_STATE_DIR` at the state directory.
- **`pi auth check` lies for these providers** (`not_ready` / `provider_not_found`) even
  when runs succeed, because they are registered by an extension rather than by static
  config. Readiness is decided by a probe run, never by `auth check`.

## Exit codes

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | success: answer, job-id or report on stdout | pass it through verbatim |
| 1 | `check`: no channel answered; `status`/`sessions`: no records | an answer, not a failure |
| 2 | bad call: missing binary, empty prompt, bad option, unknown job-id, missing or taken session name | fix the command, or fall back to `run` |
| 5 | background job still running | wait and retry `result` |
| 6 | timeout, cancel, non-zero pi exit or empty answer | check `logs`, then `check` |

## Failure modes

| Symptom | Cause / action |
| --- | --- |
| Bash call cut at 600 s | run was started in foreground; restart with `--background` |
| `pi not found in PATH` | not installed, or installed after the session started — new terminal or `PI_CLAUDE_BIN` |
| `python3 is required` | hard requirement of `pi-rpc.py`; the human installs it |
| default channel timed out before the first token | GLM sometimes stalls past 120 s — report and offer, do not switch channels yourself |
| `stop_reason=error` | provider error: 401 usually a key problem, 404 a wrong model name |
| answer says the write tool is missing | read-only mode worked as designed; rerun with `--permission write` if edits were actually requested |
| answer looks invented | check `transcript` for whether files were read at all |

## Red lines (apply inside every pi run)

- Never commit, push or delete recursively on the strength of another harness's output.
  Exception: a pipeline judge (`nano-pipeline`) commits the portion it verified itself on a
  green verdict — see `pi-delegate`, «Project red lines».
- Never read, print or forward `.env`, `*.key`, `*.pem`, `credentials.json`. Naming an env
  var is fine, printing its value is not.
- Never install or authenticate on the human's behalf.
- Never substitute the default channel on your own.
