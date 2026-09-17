# opencode — opencode bridge for Claude Code

Delegate a task to [opencode](https://opencode.ai) from inside Claude Code. Unlike sending
a prompt to another model, `opencode` is a second **agent**: it reads files, greps, and
walks the working directory on its own, and it obeys the same `CLAUDE.md` / `AGENTS.md`
your session does.

The point is not a second opinion in the abstract — it is a second agent that walks the
codebase in its own context window instead of yours.

**Read-only by default.** Write access is a deliberate flag, never inferred from how a task
is phrased.

**Sessions have names.** A run can be given a session name and continued later by that
name, with the whole previous conversation still in the model's context — the feature this
bridge is built around.

## Where your code goes

`opencode` sends the files it reads to whichever provider its configuration points at —
`b.ai` for the default model here, or any other provider you have set up. This plugin is a
bridge, not a sandbox: installing it means your working directory can leave your machine.
Read-only mode prevents writes, not reads. Decide whether that is acceptable for a given
repository *before* you delegate, and scope every task to the files it actually needs.

## Two channels

Unlike `codex`, `opencode` has no single "active model" in its settings — the model is
chosen per run, so the bridge picks one. Two are wired in, and both are addressable by a
short channel name:

| Channel | Model | Notes |
| --- | --- | --- |
| `glm` | `b.ai/glm-5.3-flash` | the default: every run without `--model` goes here |
| `deepseek` | `b.ai/deepseek-v4.1-flash` | second opinion from a different family — **prone to making things up**: it will invent a file, a flag or an API that does not exist and sound sure about it, so treat every concrete claim as a lead to verify, not as a fact |

`--model deepseek` and `--model b.ai/deepseek-v4.1-flash` are the same thing: the short name
is expanded by the bridge, and a full `provider/model` id is passed through untouched, so
any other model your provider offers is available without patching the script. A bare name
that is neither a channel nor a `provider/model` id is refused with exit 2 — it is almost
always a typo in the channel name.

`OPENCODE_DEFAULT_MODEL` moves the default globally and takes a channel name too.
`/opencode:opencode-check` prints both channels with their availability and marks the
default one.

`resume` stays on the channel the session was started on — both when resuming by job id and
by session name — unless `--model` says otherwise. Switching channels mid-session is
allowed and keeps the same opencode session, so the new model sees the whole history.

## Requirements

- [Claude Code](https://code.claude.com) v2.1.216 or later — earlier versions drop the
  plugin prefix from command names, so `/opencode:opencode-delegate` would not resolve
- [opencode](https://opencode.ai) on your `PATH`, with a provider configured:
  ```
  curl -fsSL https://opencode.ai/install | bash
  opencode providers        # aka `opencode auth` — add a provider key
  ```
- `bash`, and **`python3` or `jq`** — the answer is assembled from opencode's JSON event
  stream, and one of the two is required to parse it. This is a hard requirement, not a
  nicety: without either, `check` reports `разбор ответа: нет` and `run` refuses to start.

Verify everything at once with `/opencode:opencode-check` after installing.

## Install

```
/plugin marketplace add dmitry-fomin/listik
/plugin install opencode@listik
```

Then restart Claude Code — the subagent list is read at session start, so
`opencode:opencode-runner` only appears in a fresh session.

## What you get

| Command | What it does |
| --- | --- |
| `/opencode:opencode-delegate` | hand a task to `opencode` — explore a subsystem, map a codebase, find every occurrence |
| `/opencode:opencode-check` | is the CLI ready: binary, version, model, credentials, background jobs |
| `/opencode:opencode-second-opinion` | check your own hypothesis against `opencode`, which reads the relevant code itself |
| `/opencode:opencode-jobs` | what `opencode` is running right now: status, progress, collect an answer, cancel a job |

Two internal pieces you never call directly: the `opencode-runtime` skill (the calling
contract, preloaded into the subagent) and the `opencode:opencode-runner` subagent (a thin
forwarder whose only job is to run the script and return its stdout verbatim, so the CLI
output never floods your main context).

## How it works

A delegated task runs **in the background** and gets an id of its own. Claude launches it,
keeps working on your request, and polls the job the way it would poll any other agent:

```
/opencode:opencode-delegate  ──Bash──▶  opencode-run.sh run --background  ──▶  job id
        │                                                                       │
        │  keeps working on your task                        detached worker: opencode run
        │                                                                       │
        └── status / logs / cancel / result <job-id>  ◀─────────────────────────┘
```

The job outlives the tool call that started it, so a run that takes forty minutes is no
longer a problem: the Bash tool's ten-minute ceiling applies to the launch, not to
`opencode`.

The invariant everything rests on: **stdout of `opencode-run.sh run` is exactly the model's
final answer, nothing else.** Diagnostics go to stderr. That is what makes "show the output
verbatim" a safe rule rather than a leak of progress spinners into your answer.

## The script

`scripts/opencode-run.sh` is usable on its own:

```bash
scripts/opencode-run.sh run [options] <<'EOF'
your task
EOF
```

| Subcommand | Purpose |
| --- | --- |
| `run` | one shot; prompt on stdin. With `--background`, stdout is a job id instead of the answer |
| `resume <name\|job-id>` | continue that session; prompt on stdin, same options as `run`. Exit 2 if there is no such session — the caller should start a fresh `run` |
| `check [--json]` | readiness report: binary, version, both channels, credentials, JSON parser, running jobs |
| `status [--json] [--all] [--running] [job-id]` | jobs from this directory, or one job's card |
| `result <job-id> [--wait [sec]]` | collect a finished job; `--wait` polls for you |
| `logs <job-id> [--tail N]` | a running job's progress, rendered from the event stream: which tools it called and how they ended |
| `cancel <job-id\|--all>` | kill a job and its whole process tree |
| `clean [--older-than <days>] [--all]` | drop finished jobs; running ones and opencode's own sessions are left alone |
| `sessions [--json]` | the session names this bridge knows — both as a table and as JSON: name, id, model, last use, directory |
| `transcript [job-id] [--session <name>]` | the whole session as JSON, straight from `opencode export` |

| Option | Default | Meaning |
| --- | --- | --- |
| `--session <name>` | none | name the session (`run`) or find it (`resume`) |
| `--write` | off | full access: file edits and bash |
| `--bash` | off | allow bash, keep edits denied |
| `--model <channel\|provider/model>` | `glm` = `b.ai/glm-5.3-flash` | `glm` or `deepseek`, or a full model id; used manually and by `feature-pipeline` presets, which pin it on purpose |
| `--variant <level>` | model default | provider-specific reasoning effort; not validated against a fixed list |
| `--agent <name>` | `build` | another opencode agent (`opencode agent list`) |
| `--cwd <dir>` | current directory | working directory of the run |
| `--timeout <sec>` | 540 foreground, 7200 background | `0` removes the limit entirely |
| `--background` | off | detach, print a job id |
| `--label <text>` | none | short note so the job is recognisable in `status` |

Always use a **quoted** heredoc (`<<'EOF'`). Tasks nearly always contain code, and an
unquoted marker lets the shell expand `$` and backticks before the text ever reaches
`opencode`.

Exit codes: `0` success · `1` `check` not ready / nothing to list · `2` bad invocation (no
binary, empty prompt, unknown job, unknown or already-taken session name) · `5` job still
running · `6` timeout, cancelled, non-zero exit, or empty answer.

Job states: `running` · `completed` · `timeout` · `canceled` · `failed` · `orphaned` (the
worker died with the machine or a `kill -9`). A job card prints the state twice —
`status=` is what the worker recorded, `actual_status=` corrects it against whether the
process is actually alive. Trust the second one.

`opencode` exiting is not yet `completed`: the worker still has to assemble the answer from
the event stream and write the outcome into the card. For those seconds the job stays
`running` on purpose — `orphaned` means the *worker* is gone, not that the run finished.

## Named sessions

This is what the bridge adds on top of the job machinery:

```
> /opencode:opencode-delegate map the auth subsystem, call the session auth-map
задача ушла в opencode: opencode-20260916-232750-33141-12253 (сессия auth-map)

> уточни у opencode, где там проверяется срок токена
scripts/opencode-run.sh resume --session auth-map --background <<'TASK' …
```

Mechanically: `run --session <name>` passes `--title <name>` to `opencode run` and records
the resulting session id; `resume --session <name>` looks the id up and passes
`--session <ses_…>`, so the model sees the whole previous conversation. `transcript` shows
it directly — one session id, messages in sequence.

`opencode` itself resolves sessions **only by id**: `opencode run --session <name>` answers
`Session not found`. The name→id mapping therefore lives in this bridge
(`$OPENCODE_CLAUDE_STATE_DIR/sessions`). If that state is lost, the name still resolves —
the bridge falls back to `opencode session list --format json` and matches on the session
title.

One name is one session: `run --session <name>` with a name already taken exits 2 and tells
you to `resume` instead.

## Permissions: three modes, not two

opencode has no OS-level sandbox (this is the real difference from `codex -s read-only`).
The only genuine boundary is `OPENCODE_PERMISSION`: a denied tool is not in the model's
toolset at all. Hence three modes:

| Mode | `OPENCODE_PERMISSION` | What the agent can do |
| --- | --- | --- |
| default | `{"edit":"deny","bash":"deny"}` | read/grep/glob/list only — genuinely read-only |
| `--bash` | `{"edit":"deny"}` | plus shell commands: `git log`, tests, builds |
| `--write` | `{"edit":"allow","bash":"allow"}` | everything |

**`--bash` is not read-only.** With bash allowed, a model asked to create a file will
simply run `printf 'ok' > file` — observed, not hypothetical. That is why the default mode
denies bash too, and why `--bash` is a separate, explicit flag rather than part of the
default.

Runs always pass `--auto` to opencode: a headless run has nobody to approve an escalation,
so without it an "ask" permission would hang. The boundary is the deny list, not the
absence of `--auto`.

## What it looks like

```
> /opencode:opencode-check
готовность:   yes
бинарь:       /Users/you/.opencode/bin/opencode (ok)
версия:       1.18.31
модель:       b.ai/glm-5.3-flash (доступна)
каналы:       glm → b.ai/glm-5.3-flash (доступна, по умолчанию)
              deepseek → b.ai/deepseek-v4.1-flash (доступна)
провайдер:    b.ai (учётные данные: есть)
агент:        build (по умолчанию только чтение (правка и bash запрещены))
разбор ответа: python3
фоновых задач в работе: 0
именованных сессий: 2
```

A collected task comes back as one final message — the model's own words, passed through
verbatim.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENCODE_BIN` | `opencode` from `PATH` | full path to the CLI binary when it isn't on `PATH` |
| `OPENCODE_DEFAULT_MODEL` | `b.ai/glm-5.3-flash` | model used when `--model` is not given; accepts a channel name (`glm`, `deepseek`) or a full model id |
| `OPENCODE_DEFAULT_AGENT` | `build` | opencode agent used when `--agent` is not given |
| `OPENCODE_CLAUDE_STATE_DIR` | `${XDG_STATE_HOME:-~/.local/state}/opencode-claude` | where background jobs and session names are kept |
| `OPENCODE_CLAUDE_SESSION` | `CLAUDE_SESSION_ID` | tags jobs so `status` scopes by session instead of by directory |

## Real differences from the `codex` and `dsh` bridges

This plugin mirrors the `codex` bridge one-for-one in shape — same five skills, same
subagent, same job-management contract — but opencode's CLI contract differs in ways that
changed the implementation:

- **No `--output-last-message` equivalent.** `codex exec -o <file>` writes exactly the
  final message and nothing else. opencode has no such flag, so the bridge runs it with
  `--format json` and assembles the answer from the event stream: the text parts of the
  **last** message. Intermediate remarks between tool calls stay out of the answer and show
  up in `logs` and `transcript` instead. This is why `python3` or `jq` is a hard
  requirement here and only a convenience in the codex bridge.
- **Sessions are named, and the naming lives in the bridge.** codex resumes by rollout
  UUID, discovered by matching `cwd` and a time window against files under
  `~/.codex/sessions`. opencode keeps sessions in its own store with a title, and
  `opencode session list --format json` gives id, title and directory — no file archaeology
  needed. But the CLI resolves `--session` by id only, so the name→id mapping is the
  bridge's job, with the session list as a fallback when local state is gone.
- **`transcript` is a CLI call, not a file hunt.** `opencode export <ses_…>` prints the
  whole session as JSON on stdout ("Exporting session…" goes to stderr). No `zstd` like
  `dsh`, no dated rollout directories like `codex`.
- **Permissions instead of a sandbox.** codex has `-s read-only` / `-s workspace-write`
  enforced by the OS. opencode has none, so read-only here means denying the `edit` and
  `bash` tool families via `OPENCODE_PERMISSION` — see the section above, including why
  `--bash` exists as a separate mode.
- **The model has to be chosen by the bridge.** codex inherits the user's
  `~/.codex/config.toml`; opencode takes the model per run, so a default
  (`b.ai/glm-5.3-flash`) and the channel names on top of it (`glm`, `deepseek`) are part of
  this bridge's contract.
- **`--variant` instead of `--effort`, no `--provider`.** opencode's reasoning-effort knob
  is `--variant`, and the provider is already part of the model id (`b.ai/glm-5.3-flash`),
  so there is no separate provider flag.
- **Not a difference, but worth knowing:** `opencode run` blocks until the agent finishes,
  exactly like `codex exec` and `dsh`. Neither CLI has a background mode of its own — all
  three bridges emulate it themselves (detached worker, own process group, job-id
  directory, meta file), and that code is carried over almost unchanged.

## Two things worth knowing

**No approval channel in a headless run.** The bridge passes `--auto`, so nothing waits for
a human; whatever is denied is denied outright. An attempt to exceed the current mode fails
and the model says so rather than prompting. Re-run with `--bash` or `--write` if the task
genuinely needs it.

**The secret guard cannot live in the prompt.** `opencode` opens files by itself, so
scanning the task text proves nothing. Scope every task to the files it actually needs and
say plainly that `.env`, `*.key`, `*.pem`, and `credentials.json` are off limits. Read-only
mode stops writes, not reads — whoever writes the task owns this.

## Limits

- Background jobs leave their prompt, the raw event stream, and the answer as plain files
  under `OPENCODE_CLAUDE_STATE_DIR` (mode 700). They are never pruned on their own — run
  `clean` if the prompts are sensitive. `clean` does not touch opencode's own sessions;
  those are removed with `opencode session delete`.
- `status` and `cancel --all` scope to jobs started from the current working directory or a
  subdirectory of it; `status --all` shows every job on the machine. Set
  `OPENCODE_CLAUDE_SESSION` to scope by session instead — Claude Code doesn't put
  `CLAUDE_SESSION_ID` in the Bash tool's environment, so the directory is the reliable
  boundary.
- `cancel` sends `TERM` to the process tree and escalates to `KILL` after ten seconds
  (opencode starts a local server per run, so the whole tree has to go). Work it already
  wrote to disk in `--write` mode stays written — cancelling stops the agent, it doesn't
  roll anything back.
- Session names are stored one file per name, under a slug of the name; two different names
  that slugify identically will not be confused (the original name is stored and compared),
  but the second one falls back to the session-list lookup.
- `transcript` depends on the session still existing in opencode's store; a deleted session
  is exit 2.

## A note on language

The skill bodies, the script's comments **and all of its runtime output** are written in
Russian — `/opencode:opencode-check` and every error message will greet you in Russian, as
the sample above shows. This README, the manifests, and every command, flag, and identifier
are in English. The skills work the same regardless of the language you talk to Claude in.

## Credits

The architecture — a routing command, a thin forwarding subagent, a runtime-contract skill,
and one script holding all deterministic logic — mirrors this repository's `codex` plugin,
which in turn mirrors
[`dsh-plugin-claude-code`](https://github.com/dmitry-fomin/dsh-plugin-claude-code).

MIT licensed.
