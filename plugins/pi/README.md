# pi — pi bridge for Claude Code

Delegate a task to `pi` (`pi --mode rpc`) from inside Claude Code. Like the other bridges in
this repo, `pi` is a second **agent**: it reads files, greps, and walks the working
directory on its own, and it obeys the same `CLAUDE.md` / `AGENTS.md` your session does.

**Read-only by default.** Write access is a deliberate flag, never inferred from how a task
is phrased.

**Sessions have names.** A run can be given a session name and continued later by that
name, with the whole previous conversation still in the model's context — the feature this
bridge is built around.

## Where your code goes

`pi` sends the files it reads to whichever provider its configuration points at — `b.ai`
for the two channels wired in here, or any other provider you have set up. This plugin is a
bridge, not a sandbox: installing it means your working directory can leave your machine.
Read-only mode prevents writes, not reads. Decide whether that is acceptable for a given
repository *before* you delegate, and scope every task to the files it actually needs.

## Two channels

The model is chosen per run, so the bridge picks one. Two are wired in, and both are
addressable by a short channel name via `--model` or `--channel`:

| Channel | Model | Notes |
| --- | --- | --- |
| `glm` | `b-ai-glm/glm-5.3-flash` | the default: every run without `--model`/`--channel` goes here |
| `deepseek` | `b-ai-deepseek/deepseek-v4.1-flash` | second opinion from a different family — **prone to making things up**: it will invent a file, a flag or an API that does not exist and sound sure about it, so treat every concrete claim as a lead to verify, not as a fact |

`--model deepseek` and `--model b-ai-deepseek/deepseek-v4.1-flash` are the same thing: the
short name is expanded by the bridge, and a full `provider/model` id is passed through
untouched. `--channel` accepts only the short name. `PI_CLAUDE_DEFAULT_MODEL` moves the
default globally and takes a channel name too.

GLM sometimes stalls before the first token; the agent never switches to `deepseek` on its
own when that happens — it reports the timeout and lets you choose.

`resume` stays on the channel the session was started on — both when resuming by job id and
by session name — unless `--model`/`--channel` says otherwise. Switching channels
mid-session is allowed and keeps the same pi session, so the new model sees the whole
history.

## Requirements

- [Claude Code](https://code.claude.com) v2.1.216 or later — earlier versions drop the
  plugin prefix from command names, so `/pi:pi-delegate` would not resolve
- `pi` on your `PATH` (or `PI_CLAUDE_BIN`), **with the `b-ai-glm`/`b-ai-deepseek` providers
  registered by your `pi` user extension** (`~/.pi/agent/extensions/b-ai.ts`) — without it,
  neither channel is reachable no matter how the CLI itself is configured
- `bash`
- **`python3` as a hard requirement** — every run goes through the RPC client
  (`pi-rpc.py`), and without python3 `run` refuses to start at all

Verify everything at once with `/pi:pi-check` after installing.

## Install

```
/plugin marketplace add dmitry-fomin/listik
/plugin install pi@listik
```

Then restart Claude Code — the subagent list is read at session start, so
`pi:pi-runner` only appears in a fresh session.

## What you get

| Command | What it does |
| --- | --- |
| `/pi:pi-delegate` | hand a task to `pi` — explore a subsystem, map a codebase, find every occurrence |
| `/pi:pi-check` | is the CLI ready: binary, version, rpc client, channels, background jobs |
| `/pi:pi-second-opinion` | check your own hypothesis against `pi`, which reads the relevant code itself |
| `/pi:pi-jobs` | what `pi` is running right now: status, progress, collect an answer, cancel a job |

Two internal pieces you never call directly: the `pi-runtime` skill (the calling contract,
preloaded into the subagent) and the `pi:pi-runner` subagent (a thin forwarder whose only
job is to run the script and return its stdout verbatim, so the CLI output never floods
your main context).

## How it works

A delegated task runs **in the background** and gets an id of its own. Claude launches it,
keeps working on your request, and polls the job the way it would poll any other agent:

```
/pi:pi-delegate  ──Bash──▶  pi-run.sh run --background  ──▶  job id
        │                                                       │
        │  keeps working on your task                detached worker: pi-rpc.py
        │                                                       │
        └── status / logs / cancel / result <job-id>  ◀─────────┘
```

The job outlives the tool call that started it, so a run that takes forty minutes is no
longer a problem: the Bash tool's ten-minute ceiling applies to the launch, not to `pi`.

The invariant everything rests on: **stdout of `pi-run.sh run` is exactly the model's final
answer, nothing else.** Diagnostics go to stderr. That is what makes "show the output
verbatim" a safe rule rather than a leak of progress spinners into your answer.

## The script

`scripts/pi-run.sh` is usable on its own:

```bash
scripts/pi-run.sh run [options] <<'EOF'
your task
EOF
```

| Subcommand | Purpose |
| --- | --- |
| `run` | one shot; prompt on stdin. With `--background`, stdout is a job id instead of the answer |
| `resume <name\|job-id>` | continue that session; prompt on stdin, same options as `run`. Exit 2 if there is no such session — the caller should start a fresh `run` |
| `check [--json] [--no-probe] [--probe-timeout <sec>]` | readiness report: binary, version, rpc client, python3, both channels. Without `--no-probe`/`--probe-timeout`, both channels are probed in parallel, up to 120s each; exit 1 only when neither channel answers |
| `status [--json] [--all] [--running] [job-id]` | jobs from this directory, or one job's card |
| `result <job-id> [--wait [sec]]` | collect a finished job; `--wait` polls for you |
| `logs <job-id> [--tail N]` | a running job's progress, rendered from the event stream: which tools it called and how they ended |
| `cancel <job-id\|--all>` | kill a job and its whole process tree (`kill` is a synonym) |
| `clean [--older-than <days>] [--all]` | drop finished jobs; running ones and pi's own session files are left alone |
| `sessions [--json]` | the session names this bridge knows — both as a table and as JSON |
| `transcript [job-id] [--session <name>]` | `cat` of the session's JSONL file |

| Option | Default | Meaning |
| --- | --- | --- |
| `--session <name>` | none | name the session (`run`) or find it (`resume`) |
| `--permission <read\|bash\|write>` | `read` | permission mode in one flag |
| `--write` / `--bash` | off | aliases for `--permission write` / `--permission bash` |
| `--model <channel\|provider/model>` | `glm` = `b-ai-glm/glm-5.3-flash` | short channel name or a full model id |
| `--channel <channel>` | `glm` | same as `--model`, short name only; not combined with `--model` |
| `--thinking <level>` | model default | `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| `--cwd <dir>` | current directory | working directory of the run |
| `--timeout <sec>` | 540 foreground, 7200 background | `0` removes the limit entirely |
| `--background` | off | detach, print a job id |
| `--label <text>` | none | short note so the job is recognisable in `status` |

Always use a **quoted** heredoc (`<<'EOF'`). Tasks nearly always contain code, and an
unquoted marker lets the shell expand `$` and backticks before the text ever reaches `pi`.

Exit codes: `0` success · `1` `check` not ready (neither channel answered) / nothing to
list · `2` bad invocation (no binary, empty prompt, unknown job, unknown or already-taken
session name) · `5` job still running · `6` timeout, cancelled, non-zero exit, or empty
answer.

Job states: `running` · `completed` · `timeout` · `canceled` · `failed` · `orphaned` (the
worker died with the machine or a `kill -9`). A job card prints the state twice — `status=`
is what the worker recorded, `actual_status=` corrects it against whether the process is
actually alive. Trust the second one.

## Named sessions

This is what the bridge adds on top of the job machinery:

```
> /pi:pi-delegate map the auth subsystem, call the session auth-map
задача ушла в pi: pi-20260916-232750-33141-12253 (сессия auth-map)

> уточни у pi, где там проверяется срок токена
scripts/pi-run.sh resume --session auth-map --background <<'TASK' …
```

Mechanically: `run --session <name>` opens a fresh pi session file and remembers the pair
"name → session file"; `resume --session <name>` (or `resume <job-id>`) looks that file up
and passes it back to pi, so the model sees the whole previous conversation. A session is
addressed by **file path**, not a `uuid` — `pi --session <uuid>` only resolves from the
directory the session started in, while `pi --session <path>` works from any directory, so
resuming across a changed working directory stays possible.

If the bridge's own state is lost, the name still resolves — it falls back to scanning
`~/.pi/agent/sessions` for a session file whose recorded name matches, and restores the
model that session used.

One name is one session: `run --session <name>` with a name already taken exits 2 and tells
you to `resume` instead.

## Permissions: three modes, not two

`pi` has no OS-level sandbox. The only genuine boundary is the `--tools` allowlist passed
to the RPC client. Hence three modes:

| `--permission` | Tools | What the agent can do |
| --- | --- | --- |
| `read` (default) | `read,grep,find,ls` | read/grep/find/list only — genuinely read-only |
| `bash` | `read,grep,find,ls,bash` | plus shell commands: `git log`, tests, builds |
| `write` | unrestricted | everything |

`--write` and `--bash` remain as aliases, because Listik routes and pipeline presets
already send them.

**`--permission bash` is not read-only.** With bash allowed, a model asked to create a file
will simply run `printf 'ok' > file` — observed, not hypothetical. That is why the default
mode denies bash too, and why bash is an explicit step up rather than part of the default.

## What it looks like

```
> /pi:pi-check
ready:            yes
binary:           /Users/you/.local/bin/pi (ok)
version:          0.85.1
rpc client:       /Users/you/.claude/plugins/.../pi-rpc.py (ok) · python3: /usr/bin/python3
default channel:  glm -> b-ai-glm/glm-5.3-flash
channels:         glm -> b-ai-glm/glm-5.3-flash (in catalog: yes; probe: timeout 30s, default)
                  deepseek -> b-ai-deepseek/deepseek-v4.1-flash (in catalog: yes; probe: answered in 4s)
default permission: read-only (edits and bash blocked) (--tools read,grep,find,ls)
background jobs running: 0
named sessions:   2
```

A collected task comes back as one final message — the model's own words, passed through
verbatim.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `PI_CLAUDE_BIN` | `pi` from `PATH` | full path to the CLI binary when it isn't on `PATH` |
| `PI_CLAUDE_DEFAULT_MODEL` | `b-ai-glm/glm-5.3-flash` | model used when `--model`/`--channel` is not given; accepts a channel name or a full model id |
| `PI_CLAUDE_STATE_DIR` | `${XDG_STATE_HOME:-~/.local/state}/pi-claude` | where background jobs and session names are kept |
| `PI_CLAUDE_SESSION` | `CLAUDE_SESSION_ID` | tags jobs so `status` scopes by session instead of by directory |

## Real differences from the opencode bridge

This plugin mirrors the `opencode` bridge in shape — same four commands, same subagent,
same job-management contract — but `pi`'s CLI contract differs in ways that changed the
implementation:

- **An RPC client on python3, not a JSON event stream parsed by the shell.** `pi --mode
  rpc` speaks a line-delimited RPC protocol; the bridge drives it through `pi-rpc.py`, which
  is the piece that actually talks to `pi` and prints the final answer. `python3` is
  therefore a hard requirement here, not a convenience.
- **`pi --rpc` does not exist — the flag is `--mode rpc`.** The RPC client builds the
  invocation itself; nothing in this plugin ever passes a bare `--rpc`.
- **Extensions cannot be disabled** because the `b.ai` providers (`b-ai-glm`,
  `b-ai-deepseek`) come from `~/.pi/agent/extensions/b-ai.ts`, a user extension — turning
  extensions off would remove both channels from the provider list.
- **Provider errors arrive as `stopReason: "error"` with exit code 0 from `pi`**, so the
  bridge reads the message the RPC stream carries, not the process exit code, to detect a
  failed run.
- **Readiness is determined by a probe, not `pi auth check`** — `pi auth check` reports
  `provider_not_found`/`not_ready` for these extension-registered providers even when a run
  on that channel succeeds, so `check` probes both channels with a real (tiny) prompt
  instead.
- **GLM sometimes stalls before the first token** — probe and run time out instead of
  hanging forever, and the agent never switches channels on its own when that happens.
- **Sessions resume by file path**, not by a `uuid` the CLI looks up in the current
  directory — this is what lets `resume` work after the working directory changes between
  runs.
- **`transcript` is `cat` of the session JSONL** — no export subcommand, no zstd, no dated
  rollout directories; the session file *is* the transcript.

## Two things worth knowing

**No approval channel in a headless run.** There is nobody to approve an escalation in a
background or scripted run; whatever tool the `--tools` allowlist denies is denied outright.
Re-run with `--permission bash` or `--permission write` if the task genuinely needs it.

**The secret guard cannot live in the prompt.** `pi` opens files by itself, so scanning the
task text proves nothing. Scope every task to the files it actually needs and say plainly
that `.env`, `*.key`, `*.pem`, and `credentials.json` are off limits. Read-only mode stops
writes, not reads — whoever writes the task owns this.

## Limits

- Background jobs leave their prompt, the raw event stream, and the answer as plain files
  under `PI_CLAUDE_STATE_DIR`. They are never pruned on their own — run `clean` if the
  prompts are sensitive. `clean` never touches `pi`'s own session files under
  `~/.pi/agent/sessions`.
- `status` and `cancel --all` scope to jobs started from the current working directory or a
  subdirectory of it; `status --all` shows every job on the machine. Set `PI_CLAUDE_SESSION`
  to scope by session instead — Claude Code doesn't put `CLAUDE_SESSION_ID` in the Bash
  tool's environment, so the directory is the reliable boundary.
- `cancel` sends `TERM` to the process tree and escalates to `KILL` after ten seconds — the
  whole tree has to go, RPC client included. Work it already wrote to disk in `--permission write` mode
  stays written — cancelling stops the agent, it doesn't roll anything back.
- `transcript` depends on the session file still existing on disk; a deleted session file is
  exit 2.

## A note on language

The skill bodies, the script's comments **and all of its runtime output** are written in
Russian — `/pi:pi-check` and every error message will greet you in Russian, as the sample
above shows. This README, the manifests, and every command, flag, and identifier are in
English. The skills work the same regardless of the language you talk to Claude in.

## Credits

The architecture — a routing command, a thin forwarding subagent, a runtime-contract skill,
and one script holding all deterministic logic — mirrors this repository's `opencode`
plugin, which in turn mirrors
[`dsh-plugin-claude-code`](https://github.com/dmitry-fomin/dsh-plugin-claude-code).

MIT licensed.
