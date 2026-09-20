# devin — devin bridge for Claude Code

Delegate a task to `devin` (the terminal agent from Cognition) from inside Claude Code.
Like the other bridges in this repo, `devin` is a second **agent**: it reads files, greps,
and walks the working directory on its own, and it obeys the same `CLAUDE.md` / `AGENTS.md`
your session does.

**Read-only by default.** Write access is a deliberate flag, never inferred from how a task
is phrased.

**Sessions have names.** A run can be given a session name and continued later by that
name, with the whole previous conversation still in the model's context.

This bridge talks to the **local** `devin` CLI, the one that runs the agent on your machine
— not to a cloud Devin session over `api.devin.ai`.

## Where your code goes

`devin` sends the files it reads to Cognition's backend. This plugin is a bridge, not a
sandbox: installing it means your working directory can leave your machine. Read-only mode
prevents writes, not reads. Decide whether that is acceptable for a given repository
*before* you delegate, and scope every task to the files it actually needs.

## One channel, three effort levels

| Channel | Model family | Effort levels |
| --- | --- | --- |
| `swe` (the only one) | SWE-2 — 262K context, free on a devin account | `--thinking medium` (default), `high`, `max` → `swe-2-medium`, `swe-2-high`, `swe-2-max` |

There is no second channel: `--channel` accepts `swe` and nothing else, and any
`--thinking` value outside the three above is exit 2. `DEVIN_CLAUDE_DEFAULT_THINKING` moves
the default effort level globally.

A resumed session keeps its model unless `--thinking` says otherwise — that is devin's own
behaviour, and the bridge relies on it instead of re-sending a model on every turn.

## Requirements

- [Claude Code](https://code.claude.com) v2.1.216 or later — earlier versions drop the
  plugin prefix from command names, so `/devin:devin-delegate` would not resolve
- `devin` on your `PATH` (or `DEVIN_CLAUDE_BIN`), already authenticated (`devin auth`)
- `bash`
- `python3` — not needed to *run* devin, but the bridge reads devin's own sqlite session
  database through it. Without python3 you lose session ids, `logs` turns and `transcript`
- the working directory must be **trusted** by devin: a non-interactive run refuses an
  untrusted workspace (see below)

Verify everything at once with `/devin:devin-check` after installing.

## Install

```
/plugin marketplace add dmitry-fomin/listik
/plugin install devin@listik
```

## What you get

| Command / agent | What it is for |
| --- | --- |
| `/devin:devin-delegate` | hand a task over: background run, job-id, named session |
| `/devin:devin-second-opinion` | check your own hypothesis against devin, strictly read-only |
| `/devin:devin-jobs` | what is running, fetch an answer, cancel, clean up, list sessions |
| `/devin:devin-check` | is devin ready: binary, version, catalogue, a real probe run |
| `devin-runner` (subagent) | thin pass-through wrapper: one task in, devin's stdout out |
| `devin-runtime` (internal skill) | the script contract every other skill reads |

## How it works

Everything deterministic lives in one script, `scripts/devin-run.sh`; the skills only decide
*what* to call. The chain is:

```
/devin:devin-delegate  ──►  devin-run.sh run --background  ──►  devin … -p  ──►  job-id
                                                                       │
/devin:devin-jobs      ──►  devin-run.sh result <job-id>  ◄────────────┘
```

**The invariant the whole bridge rests on:** stdout of `run`/`resume` (foreground) and of
`result` is exactly devin's final answer and nothing else. Progress, diagnostics and launch
errors go to stderr or into the job's files, so text on stderr is always a problem signal.

devin has no background mode of its own — the job-id, the detached worker, the process tree
and the state directory are entirely the script's.

## The script

```
devin-run.sh check [--json] [--no-probe] [--probe-timeout <sec>]
devin-run.sh run [options] < prompt.txt
devin-run.sh resume <session-name|job-id|devin-session-id> [options] < prompt.txt
devin-run.sh status [--json] [--all] [--running] [job-id]
devin-run.sh result <job-id> [--wait [sec]]
devin-run.sh logs <job-id> [--tail <lines>]
devin-run.sh cancel <job-id|--all>        (kill is a synonym)
devin-run.sh clean [--older-than <days>] [--all]
devin-run.sh sessions [--json]
devin-run.sh transcript <job-id> | transcript --session <name>
```

Options of `run` / `resume`:

| Option | Default | Meaning |
| --- | --- | --- |
| `--session <name>` | none | `run` creates the name, `resume` finds it |
| `--permission read\|bash\|write` | `read` | permission mode in one flag (`--write`/`--bash` are aliases) |
| `--thinking medium\|high\|max` | `medium` | effort level → model `swe-2-<level>` |
| `--channel swe` | `swe` | the only channel; the flag exists so presets can pass it |
| `--sandbox` | off | devin's OS sandbox for the exec tool |
| `--trust-workspace` | off | pass `--respect-workspace-trust false` to devin |
| `--cwd <dir>` | current | working directory of the run |
| `--timeout <sec>` | 540 foreground, 7200 background | `0` removes the limit |
| `--background` | off | detach and print a job-id instead of the answer |
| `--label <text>` | none | short tag, the only way jobs differ on sight in `status` |

The prompt always arrives on stdin. Use a quoted heredoc (`<<'TASK'`) so the shell does not
expand `$` and backticks in the task text.

Exit codes: `0` success · `1` not ready / nothing to list · `2` bad call · `5` job still
running · `6` timeout, cancel, non-zero devin exit or empty answer.

## Named sessions

```bash
scripts/devin-run.sh run --background --session auth-map --label "map the auth layer" <<'TASK'
Map the authentication layer: entry points, where the session is validated, what is stored.
TASK
# … later, same session, full history:
scripts/devin-run.sh resume --session auth-map --background --label "follow-up" <<'TASK'
Now list every place that trusts the session without re-validating it.
TASK
```

A devin session id is a readable slug (`lilac-helenium`), not a uuid; `sessions` shows the
mapping from your name to it. `resume` also takes a bare devin session id, which is how you
continue a session you started interactively in a terminal.

The bridge learns the id by reading devin's own session database — `devin list` is an
interactive TUI, unusable from a script, and it resets the caller's working directory.

## Permissions: three modes over devin's four

devin's ladder is `auto` ⊂ `accept-edits` ⊂ `smart` ⊂ `dangerous`, and it puts *edits*
before *commands* — the opposite of the ladder the other bridges in this repo use. The
mapping:

| `--permission` | devin `--permission-mode` | Effect |
| --- | --- | --- |
| `read` (default) | `auto` | only read-only tools are auto-approved |
| `bash` | `smart` | commands **and** edits a fast model judges safe |
| `write` | `dangerous` | everything auto-approved |

So on devin `--permission bash` is **wider** than "commands but no edits": there is no mode
in between. If the working tree must stay untouched, stay on `read`.

In non-interactive mode there is nobody to confirm a tool call, so a tool the mode does not
auto-approve is rejected outright — devin warns on stderr and ends the turn with no text.
The bridge surfaces that as exit 6 with a readable message instead of a silent empty
answer:

```
$ devin-run.sh run --permission read <<< 'Write hello into /tmp/probe.txt'
error: devin returned an empty answer - warning: rejected a tool call that requires
confirmation. Running in non-interactive mode. …: in --permission read a tool it is not
allowed to run ends the turn without text; rerun with --permission bash or write if the
task really needs it
```

## The OS sandbox and workspace trust

Two devin-specific switches, both **off by default and both deliberate**:

- `--sandbox` turns on devin's process sandbox (macOS seatbelt / Linux bwrap+seccomp) for
  the exec tool. It is off by default because devin has to be able to write code; with it
  on, commands can write only inside the workspace. `status` records `sandbox=on|off` and
  the full `cmdline`, so what a job actually ran with is checkable after the fact.
- `--trust-workspace` passes `--respect-workspace-trust false`. devin refuses to run
  non-interactively in a directory nobody has trusted (`Refusing to run in an untrusted
  workspace`), because the trust prompt cannot be shown. The bridge does **not** disable
  that check on its own: trusting a directory is a human decision. When a run fails on it,
  the error text says so and names both ways out.

## What it looks like

```
$ scripts/devin-run.sh check
ready:              yes
binary:             /Users/dmitry.fomin/.local/bin/devin (ok)
version:            devin 3000.10.31 (b98cc431)
python3:            /opt/homebrew/opt/python@3.13/libexec/bin/python3
sessions db:        /Users/dmitry.fomin/.local/share/devin/cli/sessions.db (ok)
channel:            swe -> swe-2; effort levels: medium high max
default model:      swe-2-medium (in catalog: yes)
probe:              answered in 5s
default permission: read-only (edits and commands blocked) (devin --permission-mode auto)
OS sandbox:         off by default (turn on with --sandbox)
workspace trust:    respected; --trust-workspace passes --respect-workspace-trust false
background jobs running: 0
named sessions:     1
state directory:    /Users/dmitry.fomin/.local/state/devin-claude
```

`ready: yes` requires a probe run that actually answered — the binary being present is not
enough. `check --json` returns the same data as one object.

`logs <job-id>` shows the job's own lifecycle plus the turns of the devin session:

```
status:  completed (0m12s)
session: lilac-helenium
answer:  658 bytes
--- job events ---
{"ts":"…","type":"job_start","text":"swe-2-medium, permission-mode auto, sandbox off, cwd …"}
{"ts":"…","type":"session","text":"devin session lilac-helenium"}
{"ts":"…","type":"job_end","text":"exit 0, completed, answer 658 bytes"}
--- last 40 turns of devin session lilac-helenium ---
[user] In two sentences: what is the file listik/deps.py responsible for …
[tool] read {"file_path": "…/listik/deps.py"}
[tool-result] <file-view path="…/listik/deps.py" start_line="1" end_line="435 …
[assistant] `listik/deps.py` is the task dependency graph: …
```

## Environment variables

| Variable | Meaning |
| --- | --- |
| `DEVIN_CLAUDE_BIN` | path to the `devin` binary (a broken value is an error, not a fallback to `PATH`) |
| `DEVIN_CLAUDE_STATE_DIR` | where jobs and session names live (default `~/.local/state/devin-claude`) |
| `DEVIN_CLAUDE_HOME` | devin's own data directory (default `~/.local/share/devin/cli`) |
| `DEVIN_CLAUDE_SESSIONS_DB` | devin's session database, if it is not under `DEVIN_CLAUDE_HOME` |
| `DEVIN_CLAUDE_DEFAULT_THINKING` | default effort level (`medium`) |
| `DEVIN_CLAUDE_SESSION` | scope `status`/`cancel --all` by Claude Code session instead of directory |

## Real differences from the pi bridge

1. **No RPC client and no event parser.** `devin … -p` already prints the final answer and
   nothing else, so the invariant comes from the binary; the script only does job
   management around it.
2. **The prompt goes in a file** (`--prompt-file`), not on the command line — multiline text
   with code, backticks and `$` survives untouched.
3. **No working-directory flag.** `--cwd` is implemented by `cd`, because devin has none.
4. **Sessions are read out of devin's sqlite database**, since `devin list` is an
   interactive TUI. Parallel runs in one directory are told apart by the exact prompt text
   and by the ids other jobs have already claimed.
5. **There is an OS sandbox** (`--sandbox`) — the other bridges have no such boundary at
   all — and a **workspace-trust check** that can make a run fail before the model is ever
   reached.
6. **One channel.** Nothing in the bridge picks a model; the only choice is the effort
   level.

## Limits

- Background jobs leave their prompt, their event log and the answer as plain files under
  `DEVIN_CLAUDE_STATE_DIR`. They are never pruned on their own — run `clean` if the prompts
  are sensitive. `clean` never touches devin's own sessions (`devin rm <session-id>` does).
- `status` and `cancel --all` scope to jobs started from the current working directory or a
  subdirectory of it; `status --all` shows every job on the machine. Set
  `DEVIN_CLAUDE_SESSION` to scope by session instead — Claude Code doesn't put
  `CLAUDE_SESSION_ID` in the Bash tool's environment, so the directory is the reliable
  boundary.
- `cancel` sends `TERM` to the process tree and escalates to `KILL` after ten seconds. Work
  already written to disk in `--permission write` mode stays written — cancelling stops the
  agent, it doesn't roll anything back.
- `logs` and `transcript` depend on devin's session database; delete the session and they
  are exit 2.
- The bridge never reads or writes devin's configuration and never authenticates for you.

## A note on language

The skill bodies, this README and every runtime message of the script are in English; the
comments inside the script are in Russian, like the other bridges in this repository. The
skills work the same regardless of the language you talk to Claude in.

## Credits

The architecture — a routing command, a thin forwarding subagent, a runtime-contract skill,
and one script holding all deterministic logic — mirrors this repository's `pi` plugin,
which in turn mirrors
[`dsh-plugin-claude-code`](https://github.com/dmitry-fomin/dsh-plugin-claude-code).

MIT licensed.
