# codex — OpenAI Codex CLI bridge for Claude Code

Delegate a task to OpenAI Codex CLI (`codex exec`) from inside
Claude Code. Unlike sending a prompt to another model, `codex` is a second **agent**: it
reads files, greps, and runs commands in the working directory on its own, and it obeys the
same `CLAUDE.md` / `AGENTS.md` your session does.

The point is not a second opinion in the abstract — it is a second agent that walks the
codebase in its own context window instead of yours.

**Read-only by default.** Write access is a deliberate flag, never inferred from how a task
is phrased.

## Where your code goes

`codex` sends the files it reads to whichever provider `~/.codex/config.toml` points at —
OpenAI by default, or any other route configured under `[model_providers.<name>]`. This
plugin is a bridge, not a sandbox: installing it means your working directory can leave your
machine. Read-only mode prevents writes, not reads. Decide whether that is acceptable for a
given repository *before* you delegate, and scope every task to the files it actually needs.

## Providers and keys

The CLI is not tied to an OpenAI-only account. `~/.codex/config.toml` picks the active
route:

```toml
model_provider = "my-provider"
model = "my-model-id"
model_reasoning_effort = "high"

[model_providers.my-provider]
name = "My Provider"
base_url = "https://api.my-provider.example/v1"
wire_api = "responses"
env_key = "MY_PROVIDER_API_KEY"
```

No secret is stored in that file: `env_key` names an environment variable resolved per
request. The variable has to be exported in the environment Claude Code itself was launched
from — an `export` added to your shell profile after the session started is not there yet.

`/codex:codex-check` prints the active provider, model, and auth status from
`codex doctor --json`. **The model is yours to choose, in this file.** The skills and the
`codex-runner` subagent never override it per run: they pass the prompt and let the CLI use
your settings. The script still accepts `--provider <route>`, `--model <id>` and
`--effort <level>` for manual use; unlike the DeepSeek bridge, these need no alias table or
settings-file overlay — they pass straight through as `-m` and `-c key="value"` on top of
`~/.codex/config.toml`.

## Requirements

- [Claude Code](https://code.claude.com) v2.1.216 or later — earlier versions drop the
  plugin prefix from command names, so `/codex:codex-delegate` would not resolve
- [OpenAI Codex CLI](https://github.com/openai/codex) on your `PATH`, authenticated:
  ```
  npm install -g @openai/codex     # or: brew install codex
  codex login                      # or: codex login --with-api-key
  ```
  An API key referenced from `~/.codex/config.toml` (`env_key`) is the non-interactive way
  to authenticate — the one this bridge is built around.
- `bash`; `jq` is used for `check` and `transcript` if present, with a `python3` fallback
  and a last-resort `grep` fallback when neither is installed

Verify everything at once with `/codex:codex-check` after installing.

## Install

```
/plugin marketplace add dmitry-fomin/listik
/plugin install codex@listik
```

Then restart Claude Code — the subagent list is read at session start, so `codex:codex-runner`
only appears in a fresh session.

## What you get

| Command | What it does |
| --- | --- |
| `/codex:codex-delegate` | hand a task to `codex` — explore a subsystem, map a codebase, find every occurrence |
| `/codex:codex-check` | is the CLI ready: binary, `codex doctor`, active model, credentials |
| `/codex:codex-second-opinion` | check your own hypothesis against `codex`, which reads the relevant code itself |
| `/codex:codex-jobs` | what `codex` is running right now: status, progress, collect an answer, cancel a job |

Two internal pieces you never call directly: the `codex-runtime` skill (the calling
contract, preloaded into the subagent) and the `codex:codex-runner` subagent (a thin
forwarder whose only job is to run the script and return its stdout verbatim, so the CLI
output never floods your main context).

## How it works

A delegated task runs **in the background** and gets an id of its own. Claude launches it,
keeps working on your request, and polls the job the way it would poll any other agent:

```
/codex:codex-delegate  ──Bash──▶  codex-run.sh run --background  ──▶  job id
        │                                                               │
        │  keeps working on your task                        detached worker: codex exec
        │                                                               │
        └── status / logs / cancel / result <job-id>  ◀─────────────────┘
```

The job outlives the tool call that started it, so a run that takes forty minutes is no
longer a problem: the Bash tool's ten-minute ceiling applies to the launch, not to `codex`.
The `codex:codex-runner` subagent is still there for the synchronous route and for
collecting a large answer without flooding the main context.

The invariant everything rests on: **stdout of `codex-run.sh run` is exactly the model's
final answer, nothing else.** Diagnostics go to stderr. That is what makes "show the output
verbatim" a safe rule rather than a leak of progress spinners into your answer.

## The script

`scripts/codex-run.sh` is usable on its own:

```bash
scripts/codex-run.sh run [options] <<'EOF'
your task
EOF
```

| Subcommand | Purpose |
| --- | --- |
| `run` | one shot; prompt on stdin. With `--background`, stdout is a job id instead of the answer |
| `check [--json]` | readiness report from `codex doctor` plus how many jobs are running; exit 1 when not ready |
| `status [--json] [--all] [--running] [job-id]` | jobs from this directory, or one job's card |
| `result <job-id> [--wait [sec]]` | collect a finished job; `--wait` polls for you |
| `logs <job-id> [--tail N]` | a running job's progress: codex's own transcript so far, bytes of answer accumulated |
| `cancel <job-id\|--all>` | kill a job and its whole process tree |
| `clean [--older-than <days>] [--all]` | drop finished jobs; running ones are left alone |
| `transcript [job-id]` | the codex session's JSONL for that working directory — what it actually did |

| Option | Default | Meaning |
| --- | --- | --- |
| `--write` | off | allow writes to the working directory (`-s workspace-write`) |
| `--model <name>` | user's setting | manual use only — the skills never pass it (`-m`) |
| `--effort <level>` | user's setting | manual use only; not validated against a fixed list — valid values are model-dependent (`-c model_reasoning_effort="<level>"`) |
| `--provider <route>` | user's setting | manual use only — switch `[model_providers.<name>]` route for one run (`-c model_provider="<route>"`) |
| `--cwd <dir>` | current directory | working directory and sandbox boundary (`-C`) |
| `--timeout <sec>` | 540 foreground, 7200 background | `0` removes the limit entirely |
| `--background` | off | detach, print a job id |
| `--label <text>` | none | short note so the job is recognisable in `status` |

Always use a **quoted** heredoc (`<<'EOF'`). Tasks nearly always contain code, and an
unquoted marker lets the shell expand `$` and backticks before the text ever reaches
`codex`.

Exit codes: `0` success · `1` `check` not ready / no jobs · `2` bad invocation · `5` job
still running · `6` timeout, cancelled, non-zero exit, or empty answer.

Job states: `running` · `completed` · `timeout` · `canceled` · `failed` · `orphaned` (the
worker died with the machine or a `kill -9`). A job card prints the state twice —
`status=` is what the worker recorded, `actual_status=` corrects it against whether the
process is actually alive. Trust the second one.

## What it looks like

```
> /codex:codex-check
готовность:   yes
бинарь:       /Users/you/.local/bin/codex (ok)
версия:       codex-cli 0.154.0
codex doctor: ok
модель:       my-provider / my-model-id
app-server:   ok
учётные данные: ok (auth is provided by the active model provider)
фоновых задач в работе: 0
```

Delegating looks like this — the launch returns immediately, the answer is collected later:

```
> /codex:codex-delegate map the auth subsystem
задача ушла в codex: codex-20260912-133830-17492 (карта подсистемы auth)

> что там codex
codex-20260912-133830-17492    running    6м12с   gpt-6-astra   карта подсистемы auth
```

A collected task comes back as one final message — the CLI's own words, passed through
verbatim.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `CODEX_BIN` | `codex` from `PATH` | full path to the CLI binary when it isn't on `PATH` |
| `CODEX_HOME` | `~/.codex` | codex home: config, credentials, session transcripts (same variable `codex` itself reads) |
| `CODEX_CLAUDE_STATE_DIR` | `${XDG_STATE_HOME:-~/.local/state}/codex-claude` | where background jobs are kept |
| `CODEX_CLAUDE_SESSION` | `CLAUDE_SESSION_ID` | tags jobs so `status` scopes by session instead of by directory |

## Real differences from the `dsh` (DeepSeek Harness) bridge

This plugin mirrors [`dsh-plugin-claude-code`](https://github.com/dmitry-fomin/dsh-plugin-claude-code)
one-for-one in shape — same five skills, same subagent, same job-management contract — but
`codex exec`'s own CLI contract differs from `dsh`'s in ways that changed the implementation:

- **Not a difference, but worth knowing:** `codex exec` blocks until the agent finishes,
  exactly like `dsh`. Neither CLI has a background mode of its own — both bridges emulate
  it themselves (detached worker, own process group, job-id directory, meta file), and that
  code is carried over from the DeepSeek bridge almost unchanged.
- **Prompt via stdin, not argv.** `codex exec` reads the prompt from its positional
  argument, or from stdin if none is given. `dsh` only reads a positional argument, which
  forced the DeepSeek bridge to enforce a 256 KiB cap to stay under `ARG_MAX`. This bridge
  sends the prompt on stdin instead — the natural fit for `codex exec` — and sets no size
  cap of its own.
- **A dedicated flag for the clean answer.** `codex exec -o/--output-last-message <file>`
  writes exactly the agent's final message to a file, nothing else. `dsh` has no such flag;
  its bridge treats the whole of stdout as the answer. This bridge always passes `-o` and
  reads the answer back from that file — codex's own stdout/stderr (which *does* stream
  reasoning and tool calls in human-readable mode) goes to a separate log file used only by
  `logs`.
- **`codex doctor --json` instead of hand-rolled diagnostics.** `dsh check` assembles
  readiness from several separate signals (binary, profile list, credential file presence,
  settings.yaml parsing). Codex already computes this itself; `check` here is mostly a
  thin JSON-field extraction over `codex doctor --json` (via `jq`, with a `python3` and
  then a `grep` fallback).
- **Model/provider/effort overrides need no settings overlay.** `dsh --patch` does not
  override the model — the DeepSeek bridge works around this by generating a whole
  replacement `settings.yaml` and repointing the settings plugin at it. `codex exec -c
  key=value` genuinely overrides the given key on top of `~/.codex/config.toml`, so no
  overlay file or alias table (`pro`/`flash`/`vision`) is needed at all — `--model`,
  `--provider`, and `--effort` here are direct `-m` / `-c model_provider=...` / `-c
  model_reasoning_effort=...` passthroughs.
- **`--effort` is not validated against a fixed list.** DeepSeek's `low|medium|high|xhigh|max`
  is a fixed catalog. Codex's `model_reasoning_effort` is model-dependent — the local
  model catalog shows `low`, `medium`, `high`, `xhigh`, `max`, and `ultra` across different
  models — so the script passes the value through rather than gatekeeping it.
- **`transcript` reads a genuinely plain format, but cwd alone isn't enough to identify a
  job.** `dsh` keeps sessions as `session.jsonl.zstd` under a directory slug built from the
  working directory's path, which needs `zstd` to decompress. Codex keeps sessions as plain
  `rollout-*.jsonl` files organized by *date* (`~/.codex/sessions/<year>/<month>/<day>/`),
  with the working directory recorded as `payload.cwd` in each file's first
  (`session_meta`) line — no extra tool needed to read it. But an interactive `codex` TUI
  session run from the same directory writes to the same place with the same `cwd`, so
  matching on `cwd` alone can return your own conversation instead of the job's. With a
  job-id, `transcript` narrows the match to files whose mtime falls inside that job's
  `started_epoch`…`finished_epoch` window (small grace on both ends), which a same-folder
  interactive session run before or after the job won't satisfy. Without a job-id it falls
  back to "newest session for this cwd" and says so on stderr, since there is nothing left
  to disambiguate with.
- **`logs` shows more than a heartbeat.** `dsh` stays completely silent until it is done —
  the DeepSeek bridge's `logs` only reports whether the process is alive. `codex exec` in
  human-readable mode streams its reasoning and tool calls to its own stdout as it runs, so
  `logs` here can show genuine progress, not just liveness.
- **No model-alias catalog.** `dsh`'s `--model pro|flash|vision` aliases exist because the
  DeepSeek catalog has a small fixed set of names, expanded per-provider with
  `DSH_MODEL_PRO`/`FLASH`/`VISION` overrides. Codex has no equivalent shorthand worth
  building — `--model` here always takes a full model id.

## Two things worth knowing

**No approval channel in `codex exec`.** Same as `dsh`'s headless profile: a non-interactive
run has nobody to approve an escalation, so an attempt to exceed the sandbox (`read-only` by
default) fails rather than prompting. Re-run with `--write` if the task genuinely needs it.

**The secret guard cannot live in the prompt.** `codex` opens files by itself, so scanning
the task text proves nothing. Scope every task to the files it actually needs and say
plainly that `.env`, `*.key`, `*.pem`, and `credentials.json` are off limits. Read-only mode
stops writes, not reads — whoever writes the task owns this.

## Limits

- One task per run. `codex exec` has no follow-up turn in this bridge's usage, so repeat the
  context in a new task instead of continuing a conversation. (Codex itself has `resume` and
  `fork` for interactive sessions; this bridge does not use them.)
- Background jobs leave their prompt, answer, and codex's own log as plain files under
  `CODEX_CLAUDE_STATE_DIR` (mode 700). They are never pruned on their own — run `clean` if
  the prompts are sensitive.
- `status` and `cancel --all` scope to jobs started from the current working directory or a
  subdirectory of it; `status --all` shows every job on the machine. Set
  `CODEX_CLAUDE_SESSION` to scope by session instead — Claude Code doesn't put
  `CLAUDE_SESSION_ID` in the Bash tool's environment, so the directory is the reliable
  boundary.
- `cancel` sends `TERM` to the process tree and escalates to `KILL` after ten seconds. Work
  `codex` already wrote to disk in `--write` mode stays written — cancelling stops the
  agent, it doesn't roll anything back.
- `transcript` depends on codex having written a session file for that exact working
  directory under the active `CODEX_HOME` — a run against `--ephemeral` (not used by this
  bridge) or a different `CODEX_HOME` won't be found.

## A note on language

The skill bodies, the script's comments **and all of its runtime output** are written in
Russian — `/codex:codex-check` and every error message will greet you in Russian, as the
sample above shows. This README, the manifests, and every command, flag, and identifier are
in English. The skills work the same regardless of the language you talk to Claude in.

## Credits

The architecture — a routing command, a thin forwarding subagent, a runtime-contract skill,
and one script holding all deterministic logic — mirrors
[`dsh-plugin-claude-code`](https://github.com/dmitry-fomin/dsh-plugin-claude-code), which in
turn follows the shape of the community `grok` plugin for Claude Code.

MIT licensed.
