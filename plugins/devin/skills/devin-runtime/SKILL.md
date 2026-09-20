---
name: devin-runtime
description: "Contract of the devin bridge script — devin-run.sh subcommands and options, background jobs, named sessions, permission modes, the OS sandbox, workspace trust, job states, exit codes, failure modes. Internal reference attached to the devin-runner subagent; read it when any devin-* skill needs the exact call."
user-invocable: false
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh *)
---

The only way to call devin is `${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh`. Calling bare
`devin` loses the default permission mode, job bookkeeping, session names and the
prompt-file handling.

**Invariant:** stdout of `run`/`resume` (without `--background`) and of `result` is exactly
devin's final answer and nothing else. Everything else goes to stderr, so text on stderr is
always a problem signal.

## Commands

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh run --background --session "<name>" --label "<topic>" <<'TASK'
<task text>
TASK
```

| Subcommand | Purpose |
| --- | --- |
| `run` | new session; prompt on stdin. With `--background` stdout is a job-id, without it the answer |
| `resume <name\|job-id\|devin-session-id>` | continue an existing session; prompt on stdin, same flags as `run` |
| `check [--json] [--no-probe] [--probe-timeout <s>]` | readiness: binary, version, python3, session database, model catalogue, and a real probe run |
| `status [--json] [--all] [--running] [job-id]` | no argument — jobs of the current cwd subtree; `--all` — every job on the machine; with an id — one job card |
| `result <job-id> [--wait [s]]` | fetch the answer; `--wait` waits the given seconds (default 300) |
| `logs <job-id> [--tail N]` | job lifecycle plus the turns of the devin session: which tools ran, what came back |
| `cancel <job-id\|--all>` | kill the job and its whole process tree (`kill` is a synonym) |
| `clean [--older-than <days>] [--all]` | drop finished jobs; never touches running jobs or devin's own sessions |
| `sessions [--json]` | sessions known by name: name, devin session id, model, last update, directory |
| `transcript [job-id] [--session <name>]` | print the devin conversation as JSONL — what actually happened |

Every subcommand takes `-h`.

Options for `run`/`resume`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--background` | off | detach, return a job-id instead of the answer |
| `--label <text>` | none | short tag; the only way jobs differ on sight in `status` |
| `--session <name>` | none | `run` creates it, `resume` finds it |
| `--permission <read\|bash\|write>` | `read` | permission mode in one flag |
| `--write` / `--bash` | off | aliases for `--permission write` / `--permission bash`, kept for Listik routes and pipeline presets |
| `--thinking <medium\|high\|max>` | `medium` | effort level = model `swe-2-<level>`; this list only |
| `--channel <swe>` | `swe` | the only channel; the flag exists so presets can pass it |
| `--sandbox` | off | devin's OS sandbox for the exec tool (macOS seatbelt / Linux bwrap) |
| `--trust-workspace` | off | pass `--respect-workspace-trust false`; only for a directory never trusted interactively |
| `--cwd <dir>` | current | working directory of the run |
| `--timeout <s>` | 540 foreground, 7200 background | `0` removes the limit |

## Non-obvious rules

- **Background is the default choice.** A devin run blocks until the agent finishes, and a
  subsystem sweep easily outlives the 600 s Bash-call ceiling. Foreground is for questions
  answered inside the current turn.
- **Quote the heredoc marker** (`<<'TASK'`): task text is almost always code, and an
  unquoted marker lets the shell expand `$` and backticks. The prompt reaches devin as a
  file (`--prompt-file`), so multiline text with code survives intact and has no size limit
  here.
- **One channel, one decision to make: effort.** `swe` is SWE-2, free, 262K context.
  `--thinking` picks `swe-2-medium` (default), `swe-2-high` or `swe-2-max`. Raise it only
  when the human asked or a pipeline preset requires it.
- **`--permission bash` is wider on devin than on the other bridges.** It maps to devin's
  `smart` mode, which also auto-approves edits it judges safe — devin has no "commands but
  no edits" mode. If the working tree must stay untouched, stay on `read`.
- **The OS sandbox is off by default.** `--sandbox` confines the exec tool to the workspace;
  it is a real boundary, not a formality, and it breaks tasks that legitimately write
  outside the working directory. Turn it on deliberately.
- **Secrets are a prompt-side concern.** devin opens files on its own, so scope the task to
  the files it needs and explicitly forbid `.env`, `*.key`, `*.pem`, `credentials.json`.
- **Parallel runs are supported**, including in one working directory — separate sessions,
  separate job directories, no shared lock. Exception: two `--permission write` runs in the
  same directory overwrite each other's edits, and two runs into one session interleave
  their transcripts.
- **Don't poll in a foreground Bash call.** Wait with one backgrounded call instead:

  ```bash
  until ! ${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh status <job-id> | grep -q '^actual_status=running'; do sleep 20; done
  ```

  Its completion notification is the "devin finished" signal.

## Job states

| Status | Meaning |
| --- | --- |
| `running` | process alive, devin working |
| `completed` | answer ready, fetch with `result` |
| `timeout` | hit its limit; `result` still returns the partial answer |
| `canceled` | killed via `cancel` |
| `failed` | devin exited with an error; cause in `logs` |
| `orphaned` | process gone, outcome never written: reboot or `kill -9`. No answer is coming |

`status <id>` prints the status twice: `status=` is what the worker recorded,
`actual_status=` corrects it for process liveness. **Trust `actual_status`.** devin exiting
is not yet `completed` — the worker still has to record the outcome, and during those
seconds the job is honestly `running`.

## Sessions

- A devin session id is a human-readable slug (`lilac-helenium`), not a uuid. `--session
  <name>` maps your own name onto it; `sessions` shows both.
- `resume` accepts a session name, a job-id, or a bare devin session id — the last one lets
  you continue a session a human started interactively.
- `resume` inherits the directory, permission mode, effort level and sandbox flag of the
  original run unless flags override them. The model of a resumed session is kept by devin
  itself unless `--thinking` changes it.
- One name = one session: `run --session <taken name>` is exit 2, and so is `resume` on a
  name that does not exist — fall back to a fresh `run` with the current text.
- Job state (prompt, events, answer) is stored in plaintext in the state directory and
  never expires; `clean` removes it. devin's own sessions live in its sqlite database and
  `clean` leaves them alone (`devin rm <session-id>` deletes one).

## How a run is wired

- devin reads `AGENTS.md`/`CLAUDE.md` in the working directory itself — never restate
  project rules in the task.
- Each run is one `devin --model swe-2-… --permission-mode … --prompt-file … -p`, started
  in the working directory: **devin has no working-directory flag**, so `--cwd` is a `cd`.
- devin has no background mode of its own; the job-id, the worker and the process tree are
  entirely the script's.
- `devin list` is an interactive TUI and unusable from a script. The bridge reads session
  ids from devin's own sqlite database (`~/.local/share/devin/cli/sessions.db`,
  read-only) — that is also where `logs` and `transcript` get the turns.
- `DEVIN_CLAUDE_BIN` points at the binary, `DEVIN_CLAUDE_STATE_DIR` at the state directory,
  `DEVIN_CLAUDE_SESSIONS_DB` at devin's session database, `DEVIN_CLAUDE_DEFAULT_THINKING`
  moves the default effort level.

## Permission modes on devin

devin's own ladder is `auto` ⊂ `accept-edits` ⊂ `smart` ⊂ `dangerous`, and it puts edits
*before* commands — the opposite of ours. The bridge maps:

| `--permission` | devin `--permission-mode` | Effect |
| --- | --- | --- |
| `read` | `auto` | only read-only tools are auto-approved |
| `bash` | `smart` | commands **and** edits a fast model judges safe |
| `write` | `dangerous` | everything auto-approved |

In non-interactive (`-p`) mode there is nobody to confirm a tool, so a tool that is not
auto-approved is rejected outright: devin warns on stderr and **ends the turn with no
text**. The bridge reports that as exit 6 "empty answer", not as a silent success.

## Exit codes

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | success: answer, job-id or report on stdout | pass it through verbatim |
| 1 | `check`: devin not ready; `status`/`sessions`: no records | an answer, not a failure |
| 2 | bad call: missing binary, empty prompt, bad option, unknown job-id, missing or taken session name | fix the command, or fall back to `run` |
| 5 | background job still running | wait and retry `result` |
| 6 | timeout, cancel, non-zero devin exit or empty answer | check `logs`, then `check` |

## Failure modes

| Symptom | Cause / action |
| --- | --- |
| Bash call cut at 600 s | run was started in foreground; restart with `--background` |
| `devin not found in PATH` | not installed, or installed after the session started — new terminal or `DEVIN_CLAUDE_BIN` |
| `Refusing to run in an untrusted workspace` | the directory was never trusted: the human runs `devin` there once interactively, or you rerun with `--trust-workspace` — say which you did |
| exit 6 "empty answer" | a tool was blocked by the permission mode; rerun with the rights the task actually needs, or accept the limit |
| `rejected a tool call that requires confirmation` on stderr | same cause, seen from devin's side |
| job stuck in `running` with no output | check `logs`: no turns in the session yet means the run has not reached the model |
| answer looks invented | check `transcript` for whether files were read at all |

## Red lines (apply inside every devin run)

- Never commit, push or delete recursively on the strength of another harness's output.
- Never read, print or forward `.env`, `*.key`, `*.pem`, `credentials.json`. Naming an env
  var is fine, printing its value is not.
- Never install or authenticate on the human's behalf.
- Never turn workspace trust off on your own initiative — `--trust-workspace` needs the
  human's word, because it is the check that keeps an agent out of a directory nobody
  vouched for.
