---
name: opencode-runtime
description: "Contract of the opencode bridge script — opencode-run.sh subcommands and options, background jobs, named sessions, permission modes, timeouts, job states, exit codes, failure modes. Internal reference attached to the opencode-runner subagent; read it when any opencode-* skill needs the exact call."
user-invocable: false
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/opencode-run.sh *)
---

The only way to call opencode is `${CLAUDE_PLUGIN_ROOT}/scripts/opencode-run.sh`. Calling
bare `opencode run` loses the default permission mode, job bookkeeping, the event-stream
parsing and session names.

**Invariant:** stdout of `run`/`resume` (without `--background`) and of `result` is exactly
opencode's final answer and nothing else. Everything else goes to stderr, so text on stderr
is always a problem signal.

## Commands

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/opencode-run.sh run --background --session "<name>" --label "<topic>" <<'TASK'
<task text>
TASK
```

| Subcommand | Purpose |
| --- | --- |
| `run` | new session; prompt on stdin. With `--background` stdout is a job-id, without it the answer |
| `resume <name\|job-id>` | continue an existing session; prompt on stdin, same flags as `run` |
| `check [--json]` | readiness: binary, version, both channels, provider credentials, event-stream parser, jobs in flight |
| `status [--json] [--all] [--running] [job-id]` | no argument — jobs of the current cwd subtree; `--all` — every job on the machine; with an id — one job card |
| `result <job-id> [--wait [s]]` | fetch the answer; `--wait` waits the given seconds (default 300) |
| `logs <job-id> [--tail N]` | event stream: which tools the run called, how much answer accumulated |
| `cancel <job-id\|--all>` | kill the job and its whole process tree |
| `clean [--older-than <days>] [--all]` | drop finished jobs; never touches running jobs or opencode's own sessions |
| `sessions [--json]` | sessions known by name: name, id, channel model, last update, directory |
| `transcript [job-id] [--session <name>]` | the whole session as JSON, straight from `opencode export` |

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
| `--model <channel\|provider/model>` | `glm` = `b.ai/glm-5.3-flash` | short channel name or a full `provider/model` id; a bare name that is neither is exit 2 |
| `--variant <level>` | model default | reasoning effort; provider-specific, so the script does not validate it against a list |
| `--agent <name>` | `build` | another opencode agent (`opencode agent list`) |

## Non-obvious rules

- **Background is the default choice.** `opencode run` blocks until the agent finishes, and
  a subsystem sweep easily outlives the 600 s Bash-call ceiling. Foreground is for questions
  answered inside the current turn.
- **`--permission bash` is not read-only.** opencode has no OS-level sandbox; with bash
  allowed the model writes files through plain `>`. The real boundary is the bash ban, the
  default.
- **Rights cannot be widened from inside the task.** A headless run has no interactive
  approval channel (that is why the script passes `--auto`); a denied tool is simply absent.
  Need edits — rerun with `--permission write`.
- **Secrets are a prompt-side concern.** opencode opens files on its own, so scope the task
  to the files it needs and explicitly forbid `.env`, `*.key`, `*.pem`, `credentials.json`.
- **Channel, variant and agent are the human's choice.** Runs go on `glm`
  (`b.ai/glm-5.3-flash`). Add `--model`/`--variant`/`--agent` only when the human named it,
  or when a pipeline preset passes it — model-per-role is part of the preset (rationale in
  `plugins/feature-pipeline/references/ROLES.md`).
- **DeepSeek confabulates** — it will confidently name files, flags and functions that do
  not exist. Treat `deepseek` answers as claims to verify against the code.
- **Parallel runs are supported**, including in one working directory — separate sessions,
  separate job directories, no shared lock. Exception: two `--permission write` runs in the
  same directory overwrite each other's edits, and two runs into one session interleave
  their transcripts.
- **Don't poll in a foreground Bash call.** Wait with one backgrounded call instead:

  ```bash
  until ! ${CLAUDE_PLUGIN_ROOT}/scripts/opencode-run.sh status <job-id> | grep -q '^actual_status=running'; do sleep 20; done
  ```

  Its completion notification is the "opencode finished" signal.

## Job states

| Status | Meaning |
| --- | --- |
| `running` | process alive, opencode working |
| `completed` | answer ready, fetch with `result` |
| `timeout` | hit its limit; `result` still returns the partial answer |
| `canceled` | killed via `cancel` |
| `failed` | opencode exited with an error; cause in `logs` |
| `orphaned` | process gone, outcome never written: reboot or `kill -9`. No answer is coming |

`status <id>` prints the status twice: `status=` is what the worker recorded,
`actual_status=` corrects it for process liveness. **Trust `actual_status`.** opencode
exiting is not yet `completed` — the worker still has to assemble the answer from the event
stream and write the outcome, and during those seconds the job is honestly `running`.

## Sessions

- A session has a **name**: `run --session <name>` passes `--title <name>` to opencode and
  the bridge records name → session id; `resume --session <name>` puts the prompt into the
  same opencode session, so the model sees the whole previous conversation.
- **opencode itself resolves sessions only by id** (`opencode run --session <name>` answers
  `Session not found`), so the name is the bridge's business. If its state is lost, the name
  still resolves — the bridge falls back to `opencode session list --format json` and
  matches on the title.
- One name = one session: `run --session <taken name>` is exit 2, and so is `resume` on a
  name that does not exist — fall back to a fresh `run` with the current text.
- `resume` inherits the directory, permission mode, channel, variant and agent of the
  original run unless flags override them. Switching channel mid-session keeps the history
  and the same opencode session.
- Job state (prompt, event stream, answer) is stored in plaintext in the state directory and
  never expires; `clean` removes it. opencode's own sessions live in its store and are
  removed with `opencode session delete`.

## How a run is wired

- opencode reads `AGENTS.md`/`CLAUDE.md` in the working directory itself — never restate
  project rules in the task.
- The answer is assembled from the event stream (`opencode run --format json`): the text
  parts of the **last** message. Intermediate remarks between tool calls are not part of the
  answer — they show up in `logs` and in full in `transcript`.
- Parsing the stream needs `python3` or `jq`. With neither, `check` reports
  `answer parser: none` and `run` refuses to start with exit 2.
- Two channels: `glm` (`b.ai/glm-5.3-flash`, default) and `deepseek`
  (`b.ai/deepseek-v4.1-flash`). `OPENCODE_DEFAULT_MODEL` moves the default (it takes a
  channel name too), `OPENCODE_BIN` points at the binary, `OPENCODE_CLAUDE_STATE_DIR` at the
  state directory.

## Exit codes

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | success: answer, job-id or report on stdout | pass it through verbatim |
| 1 | `check`: not ready; `status`/`sessions`: no records | an answer, not a failure |
| 2 | bad call: missing binary, empty prompt, bad option, unknown job-id, missing or taken session name, no JSON parser | fix the command, or fall back to `run` |
| 5 | background job still running | wait and retry `result` |
| 6 | timeout, cancel, non-zero opencode exit or empty answer | check `logs`, then `check` |

## Failure modes

| Symptom | Cause / action |
| --- | --- |
| Bash call cut at 600 s | run was started in foreground; restart with `--background` |
| `opencode not found in PATH` | not installed, or installed after the session started — new terminal or `OPENCODE_BIN` |
| `python3 or jq is required` | nothing to parse the event stream with; the human installs `jq` |
| `opencode: empty answer - run check` | no provider credentials, or the answer was blocked |
| `session '<name>' already exists` | `run` where `resume` was meant |
| `no session named '<name>'` | deleted or misspelled; `sessions` lists them, otherwise start a fresh `run` |
| status `orphaned` | worker died with the machine; no answer is coming, relaunch |
| answer says the write tool is missing | read-only mode worked as designed; rerun with `--permission write` if edits were actually requested |
| answer looks invented | check `transcript` for whether files were read at all |

## Red lines (apply inside every opencode run)

- Never commit, push or delete recursively on the strength of another harness's output.
- Never read, print or forward `.env`, `*.key`, `*.pem`, `credentials.json`. Naming an env
  var is fine, printing its value is not.
- Never install or authenticate on the human's behalf.
- Never substitute the default channel on your own.
