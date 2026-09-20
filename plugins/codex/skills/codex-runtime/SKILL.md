---
name: codex-runtime
description: "Contract of the codex bridge script — codex-run.sh subcommands and options, background jobs, Codex session resume, permission modes, timeouts, job states, exit codes, failure modes. Internal reference attached to the codex-runner subagent; read it when any codex-* skill needs the exact call."
user-invocable: false
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh *)
---

The only way to call OpenAI Codex CLI is `${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh`.
Calling bare `codex exec` loses the default permission mode, job bookkeeping and the exit
code contract.

**Invariant:** stdout of `run`/`resume` (without `--background`) and of `result` is exactly
codex's final answer and nothing else — it comes from `-o/--output-last-message`, not from
the process output. Everything else goes to stderr, so text on stderr is always a problem
signal.

## Commands

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh run --background --label "<topic>" <<'TASK'
<task text>
TASK
```

| Subcommand | Purpose |
| --- | --- |
| `run` | new session; prompt on stdin. With `--background` stdout is a job-id, without it the answer |
| `resume <job-id>` | continue that job's Codex session (`codex exec resume`); prompt on stdin. Inherits the original permission mode, `--model`, `--effort`, `--cwd`; takes `--background`, `--timeout`, `--label` |
| `check [--json]` | readiness: binary, `codex doctor`, active provider/model, credentials, jobs in flight |
| `status [--json] [--all] [--running] [job-id]` | no argument — jobs of the current cwd subtree; `--all` — every job on the machine; with an id — one job card |
| `result <job-id> [--wait [s]]` | fetch the answer; `--wait` waits the given seconds (default 300) |
| `logs <job-id> [--tail N]` | codex's own output as it runs: reasoning, tool calls, and how much answer accumulated |
| `cancel <job-id\|--all>` | kill the job and its whole process tree |
| `clean [--older-than <days>] [--all]` | drop finished jobs; never touches running ones |
| `transcript [job-id]` | print the Codex session JSONL — what the conversation actually was |

Every subcommand takes `-h`.

Options for `run`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--background` | off | detach, return a job-id instead of the answer |
| `--label <text>` | none | short tag; the only way jobs differ on sight in `status` |
| `--permission <read\|bash\|write>` | `read` | permission mode in one flag |
| `--write` | off | alias for `--permission write`, kept for Listik routes and pipeline presets |
| `--cwd <dir>` | current | working directory of the run and the sandbox boundary (`-C`) |
| `--timeout <s>` | 540 foreground, 7200 background | `0` removes the limit |
| `--model <id>` | user's `~/.codex/config.toml` | full model id (`-m`) |
| `--effort <level>` | user's setting | `model_reasoning_effort`; not validated — valid values are model-dependent |
| `--provider <route>` | user's setting | `[model_providers.<route>]` from the config; manual use only |

## Permissions

codex has no per-tool allowlist: its boundary is the filesystem sandbox passed as `-s`.

| `--permission` | Sandbox | Meaning |
| --- | --- | --- |
| `read` | `read-only` | default; commands still run, writes are denied |
| `bash` | `read-only` | accepted for parity with the other bridges — the same sandbox as `read`, because running commands is already allowed there |
| `write` | `workspace-write` | file edits inside `--cwd` |

Add `write` only when the human asked for a change in this message; never infer it from a
task merely looking like implementation. A background write job keeps editing files while
you do other things, so launch one only when the human knows it is running.

## Non-obvious rules

- **Background is the default choice.** `codex exec` blocks until the agent finishes, and a
  subsystem sweep easily outlives the 600 s Bash-call ceiling. Foreground is for questions
  answered inside the current turn. The background is entirely this script's doing (own
  process group, detached worker, meta file) — codex knows nothing about it.
- **There is no approval channel in a headless run.** An attempt to escalate rights from
  inside the task fails instead of prompting; rerun with `--permission write`.
- **Secrets are a prompt-side concern.** codex opens files on its own, so scope the task to
  the files it needs and explicitly forbid `.env`, `*.key`, `*.pem`, `credentials.json`.
- **Model, provider and effort are the human's choice.** A run goes on the settings in
  `~/.codex/config.toml`; `check` reports them as diagnostics, not as an invitation to
  switch. The one exception is a call from a feature-pipeline preset, which must pass the
  `--model` and `--effort` it requires, because model-per-role is part of the preset
  (rationale in `plugins/feature-pipeline/references/ROLES.md`). Never add `--provider`
  for that exception.
- **Parallel runs are supported**, including in one working directory — separate sessions,
  separate job directories, no shared lock. Exception: two `--permission write` runs in the
  same directory overwrite each other's edits, so keep writes to one job per directory.
- **Don't poll in a foreground Bash call.** Wait with one backgrounded call instead:

  ```bash
  until ! ${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh status <job-id> | grep -q '^actual_status=running'; do sleep 20; done
  ```

  Its completion notification is the "codex finished" signal. When the answer is due within
  a minute or two, `result <job-id> --wait 120` is the short alternative.

## Job states

| Status | Meaning |
| --- | --- |
| `running` | process alive, codex working |
| `completed` | answer ready, fetch with `result` |
| `timeout` | hit its limit; `result` still returns the partial answer |
| `canceled` | killed via `cancel` |
| `failed` | codex exited with an error; cause in `logs` |
| `orphaned` | process gone, outcome never written: reboot or `kill -9`. No answer is coming |

`status <id>` prints the status twice: `status=` is what the worker recorded,
`actual_status=` corrects it for process liveness. **Trust `actual_status`.**

## Resuming a Codex session

The Codex session id (a UUID) is written to the job's meta as `codex_session` and shown by
`status`/`status --json` once codex has flushed the rollout file — that is, after the job
finished.

```bash
${CLAUDE_PLUGIN_ROOT}/scripts/codex-run.sh resume <job-id> --background --label "<follow-up>" <<'TASK'
<follow-up text: the author's answers, or the red acceptance items verbatim>
TASK
```

The new job gets its own job-id; `codex_session` stays the same and `resumed_from` points
at the old id. Exit 2 — no session id, the job is still `running`, or codex could not find
the session — means **fall back to a fresh `run`** with the current task text. Don't invent
another syntax.

## How a run is wired

- codex reads `AGENTS.md`/`CLAUDE.md` in the working directory itself — never restate
  project rules in the task.
- The prompt goes to stdin of `codex exec`, so this bridge sets no size limit on it.
- The answer is the file behind `-o/--output-last-message`: exactly the agent's final
  message. Reasoning and tool calls stay in codex's own output, visible through `logs`;
  the full session is `transcript`.
- Job state (prompt, codex's output, answer) is stored in plaintext in the state directory
  and never expires; `clean` removes it.
- `transcript` matches the session file by working directory and, given a job-id, by the
  job's time window — without a job-id it may return your own interactive codex session and
  says so on stderr.

## Exit codes

| Code | Meaning | Action |
| --- | --- | --- |
| 0 | success: answer, job-id or report on stdout | pass it through verbatim |
| 1 | `check`: codex not ready; `status`: no jobs | an answer, not a failure |
| 2 | bad call: missing binary, empty prompt, bad option, unknown job-id, no session to resume | fix the command, or fall back to `run` |
| 5 | background job still running | wait and retry `result` |
| 6 | timeout, cancel, non-zero codex exit or empty answer | check `logs`, then `check` |

## Failure modes

| Symptom | Cause / action |
| --- | --- |
| Bash call cut at 600 s | run was started in foreground; restart with `--background` |
| `codex not found in PATH` | not installed, or installed after the session started — new terminal or `CODEX_BIN` |
| `empty answer - check readiness` | no auth, or the answer was blocked: run `check`, then rephrase |
| job asks for approval and fails | a write attempt in read-only mode; rerun with `--permission write` if edits were actually requested |
| status `orphaned` | the worker died with the machine or a `kill -9`; no answer is coming, relaunch |
| answer looks invented | check `transcript` for whether files were read at all |
| `transcript` finds no session | the run used a different `CODEX_HOME`, or has not finished yet |

## Red lines (apply inside every codex run)

- Never commit, push or delete recursively on the strength of another harness's output.
- Never read, print or forward `.env`, `*.key`, `*.pem`, `credentials.json`. Naming an env
  var is fine, printing its value is not.
- Never install or authenticate on the human's behalf.
- Never switch the model, provider or effort on your own.
