# dsh — DeepSeek Harness bridge for Claude Code

Delegate a task to DeepSeek Harness (`dsh`) from inside
Claude Code. Unlike sending a prompt to another model, `dsh` is a second **agent**: it reads
files, greps, and runs commands in the working directory on its own, and it obeys the same
`CLAUDE.md` / `AGENTS.md` your session does.

The point is not a second opinion in the abstract — it is a second agent that walks the
codebase in its own context window instead of yours.

**Read-only by default.** Write access is a deliberate flag, never inferred from how a task
is phrased.

## Where your code goes

`dsh` sends the files it reads to whichever provider it is configured for — DeepSeek's own
API by default, or any other route you set up (see *Providers and keys* below). This plugin
is a bridge, not a sandbox: installing it means your working directory can leave your
machine. Read-only mode prevents writes, not reads. Decide whether that is acceptable for a
given repository *before* you delegate, and scope every task to the files it actually needs.

## Providers and keys

The harness is not tied to a DeepSeek account. Two adapters ship in its base bundle and both
read `~/.dsh/settings.yaml`, hot-reloaded:

- `llm-deepseek` owns the native route `deepseek-official`. Its key is a *reference*:
  ```yaml
  llm-deepseek:
    apiKeyEnv: DEEPSEEK_HARNESS_API_KEY   # default: DEEPSEEK_API_KEY
  ```
- `llm-pi-ai` is a multi-provider adapter, mounted dormant until an `llm-pi-ai:` section
  gives it routes. Catalog routes (`anthropic`, `openai`, `openrouter`, `zai`, `google`, …)
  need nothing but a key reference; a private gateway is declared outright with `api`,
  `baseURL`, and a `models` list.
  ```yaml
  llm-pi-ai:
    providers:
      openrouter:
        apiKeyEnv: OPENROUTER_API_KEY
  agent-default-model:
    provider: openrouter
    model: deepseek/deepseek-v4-pro
    reasoningEffort: xhigh
  ```

No secret is stored in that file: `apiKeyEnv` names an environment variable resolved per
request. The variable has to be exported in the environment Claude Code itself was launched
from — an `export` added to your shell profile after the session started is not there yet,
and the run fails with `MISSING_CREDENTIAL` until you restart from a fresh terminal.

`/dsh:dsh-check` prints the active provider and the routes the `llm-pi-ai:` section raised.
**The model is yours to choose, in this file.** The skills and the `dsh-runner` subagent
never override it per run: they pass the prompt and let the harness use your settings. The
script still accepts `--provider <route>`, `--model <id>` and `--effort <level>` for manual
use; the `pro|flash|vision` aliases expand to the DeepSeek catalog and are therefore
refused on any other route unless you define `DSH_MODEL_PRO`, `DSH_MODEL_FLASH`, or
`DSH_MODEL_VISION`.

## Requirements

- [Claude Code](https://code.claude.com) v2.1.216 or later — earlier versions drop the
  plugin prefix from command names, so `/dsh:dsh-delegate` would not resolve
- DeepSeek Harness on your `PATH`, authenticated, with the `headless` profile available:
  ```
  npm install -g @deepseek-ai/dsh
  dsh --profile web        # sign in through the web UI once — or configure an API key
  ```
  Signing in is one way to authenticate; an API key reference in `~/.dsh/settings.yaml`
  is the other, and it is what lets the harness run on a provider of your choosing.
  Note the name collision: Homebrew's `dsh` is an unrelated tool (Dancer's shell). The one
  this plugin drives is the npm package `@deepseek-ai/dsh`, which is pre-release software —
  its flags may still move.
- `bash`, plus `zstd` if you want to read session transcripts

Verify everything at once with `/dsh:dsh-check` after installing.

## Install

```
/plugin marketplace add dmitry-fomin/listik
/plugin install dsh@listik
```

Then restart Claude Code — the subagent list is read at session start, so `dsh:dsh-runner`
only appears in a fresh session.

## What you get

| Command | What it does |
| --- | --- |
| `/dsh:dsh-delegate` | hand a task to `dsh` — explore a subsystem, map a codebase, find every occurrence |
| `/dsh:dsh-check` | is the harness ready: binary, profiles, active model, credentials |
| `/dsh:dsh-second-opinion` | check your own hypothesis against `dsh`, which reads the relevant code itself |
| `/dsh:dsh-jobs` | what `dsh` is running right now: status, progress, collect an answer, cancel a job |

`/dsh:dsh-delegate` and `/dsh:dsh-second-opinion` run forked (`context: fork`): the skill
is its own background context, so the launch, the wait and the collected answer never land
in your main conversation — only the skill's final message does, with the job id in it.

Two internal pieces you never call directly: the `dsh-runtime` skill (the calling contract,
preloaded into the subagent) and the `dsh:dsh-runner` subagent (a thin forwarder whose only
job is to run the script and return its stdout verbatim — still available for callers that
want a subagent rather than a forked skill).

## How it works

A delegated task runs **in the background** and gets an id of its own. Claude launches it,
keeps working on your request, and polls the job the way it would poll any other agent:

```
/dsh:dsh-delegate  ──Bash──▶  dsh-run.sh run --background  ──▶  job id
       │                                                          │
       │  keeps working on your task                    detached worker: dsh --profile headless
       │                                                          │
       └── status / logs / cancel / result <job-id>  ◀────────────┘
```

The job outlives the tool call that started it, so a run that takes forty minutes is no
longer a problem: the Bash tool's ten-minute ceiling applies to the launch, not to `dsh`.
The `dsh:dsh-runner` subagent is still there for callers that hand out work as subagents;
the delegation skills use a forked context instead.

The invariant everything rests on: **stdout of `dsh-run.sh run` is exactly the model's final
answer, nothing else.** Diagnostics go to stderr. That is what makes "show the output
verbatim" a safe rule rather than a leak of progress spinners into your answer.

## The script

`scripts/dsh-run.sh` is usable on its own:

```bash
scripts/dsh-run.sh run [options] <<'EOF'
your task
EOF
```

| Subcommand | Purpose |
| --- | --- |
| `run` | one shot; prompt on stdin. With `--background`, stdout is a job id instead of the answer |
| `check [--json]` | readiness report plus how many jobs are running; exit 1 when not ready |
| `status [--json] [--all] [--running] [job-id]` | jobs from this directory, or one job's card |
| `result <job-id> [--wait [sec]]` | collect a finished job; `--wait` polls for you |
| `logs <job-id> [--tail N]` | a running job's progress: the harness's stderr, bytes of answer so far |
| `cancel <job-id\|--all>` | kill a job and its whole process tree |
| `clean [--older-than <days>] [--all]` | drop finished jobs; running ones are left alone |
| `transcript [job-id]` | full JSONL session — what the harness actually did |
| `resume <job-id>` | always exits 2: the headless profile cannot continue a session (`--resume` exists only on tui). The caller should start a fresh `run` |

| Option | Default | Meaning |
| --- | --- | --- |
| `--permission <read\|bash\|write>` | `read` | permission mode in one flag |
| `--write` | off | alias for `--permission write` |
| `--model pro\|flash\|vision\|<name>` | user's setting | manual use only — the skills never pass it |
| `--effort low\|medium\|high\|xhigh\|max` | user's setting | manual use only; works with or without `--model` |
| `--provider <route>` | user's setting, else `deepseek-official` | manual use only — switch route for one run |
| `--cwd <dir>` | current directory | working directory and sandbox boundary |
| `--timeout <sec>` | 540 foreground, 7200 background | `0` removes the limit entirely |
| `--background` | off | detach, print a job id |
| `--label <text>` | none | short note so the job is recognisable in `status` |

Always use a **quoted** heredoc (`<<'EOF'`). Tasks nearly always contain code, and an
unquoted marker lets the shell expand `$` and backticks before the text ever reaches `dsh`.

Permission modes map onto the harness's own sandbox:

| `--permission` | Sandbox mode | What the agent can do |
| --- | --- | --- |
| `read` (default) | `read-only` | read, grep, run commands — writes are refused by the OS sandbox |
| `bash` | `read-only` | the same tier; dsh has no separate "commands but no edits" mode, so `bash` is accepted for one flag spelling across harnesses |
| `write` | `workspace-write` | edits inside the working directory |

`--write` remains as an alias, because Listik routes and pipeline presets already send it.
**Read-only is enforced by the sandbox, not by a tool allowlist:** a run asked to write
gets `file access denied under read-only mode` from the system and says so in its answer.

Exit codes: `0` success · `1` `check` not ready / no jobs · `2` bad invocation · `5` job
still running · `6` timeout, cancelled, non-zero exit, or empty answer.

Job states: `running` · `completed` · `timeout` · `canceled` · `failed` · `orphaned` (the
worker died with the machine or a `kill -9`). A job card prints the state twice —
`status=` is what the worker recorded, `actual_status=` corrects it against whether the
process is actually alive. Trust the second one.

## What it looks like

```
> /dsh:dsh-check
ready:        yes
binary:       /opt/homebrew/bin/dsh (ok)
version:      0.1.5-rc.1
profiles:     headless,tui,web
model:        deepseek-official / deepseek-v4-pro (effort: max)
pi-ai routes: —
credentials:  present
default permission: read-only (sandbox blocks writes)
background jobs running: 0
DSH_HOME:     /Users/you/.dsh
```

Delegating looks like this — the launch returns immediately, the answer is collected later:

```
> /dsh:dsh-delegate map the auth subsystem
handed to dsh: dsh-20260826-133830-17492 (auth subsystem map)

> what's deepseek doing
dsh-20260826-133830-17492    running    6m12s   —   auth subsystem map
```

A collected task comes back as one final message — the harness's own words, passed through
verbatim. Asked to review this very script, it answered with a numbered list of defects,
each with a line number and the scenario that triggers it; nine of them were real and got
fixed before this release.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DSH_BIN` | `dsh` from `PATH` | full path to the harness binary when it isn't on `PATH` |
| `DSH_HOME` | `~/.dsh` | harness home: settings, credentials, session transcripts |
| `DSH_CLAUDE_STATE_DIR` | `${XDG_STATE_HOME:-~/.local/state}/dsh-claude` | where background jobs are kept |
| `DSH_CLAUDE_SESSION` | `CLAUDE_SESSION_ID` | tags jobs so `status` scopes by session instead of by directory |
| `DSH_MODEL_PRO` / `DSH_MODEL_FLASH` / `DSH_MODEL_VISION` | DeepSeek catalog ids | what the `pro`/`flash`/`vision` aliases expand to; required for those aliases on a non-DeepSeek route |

## Two things worth knowing

**Choosing the model is not what you'd expect.** `dsh --patch` does *not* override the
model: the user layer in `~/.dsh/settings.yaml` is applied on top of any overlay. The script
works around this by repointing the settings plugin at a generated document. Because that
repoints the whole document, the generated one is a *copy* of your settings with only
`agent-default-model:` rewritten — the `llm-deepseek:` and `llm-pi-ai:` sections have to
survive, or a run with `--model` would silently fall back to the default key and the native
route. This is why the flags exist at all — but the skills never use them: set the model in
`~/.dsh/settings.yaml` and every run picks it up.

**The secret guard cannot live in the prompt.** `dsh` opens files by itself, so scanning the
task text proves nothing. Scope every task to the files it actually needs and say plainly
that `.env`, `*.key`, `*.pem`, and `credentials.json` are off limits. Read-only mode stops
writes, not reads — whoever writes the task owns this.

## Limits

- One task per run. The `headless` profile has no follow-up turn, so repeat the context in a
  new task instead of continuing a conversation.
- The task travels as a single argv element; prompts above 256 KiB are rejected. Put bulk
  material in a file inside the working directory and point at it.
- Background jobs leave their prompt, answer, and stderr as plain files under
  `DSH_CLAUDE_STATE_DIR` (mode 700). They are never pruned on their own — run `clean` if the
  prompts are sensitive.
- `status` and `cancel --all` scope to jobs started from the current working directory or a
  subdirectory of it; `status --all` shows every job on the machine. Set `DSH_CLAUDE_SESSION`
  to scope by session instead — Claude Code doesn't put `CLAUDE_SESSION_ID` in the Bash
  tool's environment, so the directory is the reliable boundary.
- `cancel` sends `TERM` to the process tree and escalates to `KILL` after ten seconds. Work
  `dsh` already wrote to disk in write mode stays written — cancelling stops the agent,
  it doesn't roll anything back.
- In headless mode there is no approval channel, so a request to escalate permissions is
  declined rather than queued. Re-run with `--permission write` if the task genuinely needs
  it.

## A note on language

The skills, this README and all of the script's runtime output are in English. Only the
comments inside `scripts/dsh-run.sh` are in Russian — they are addressed to whoever
maintains the script. The skills work the same regardless of the language you talk to
Claude in.

## Credits

The architecture — a routing command, a thin forwarding subagent, a runtime-contract skill,
and one script holding all deterministic logic — follows the shape of the community
`grok` plugin for Claude Code.

MIT licensed.
