# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Listik: one server, one SQLite database, one API — a task tracker plus
long-term memory with hybrid (full-text + vector) search, used across all of Дмитрий's projects.
`bin/listik` is both the CLI and the API client. `README.md` has the full command reference;
`API.md` is the authoritative data/endpoint contract — read it before changing task/comment/dep
behavior. For the day-to-day task-tracking commands (`claim`/`heartbeat`/`stage`/`comment`/`done`)
used while working *any* task, see `AGENTS.md` — this file covers code architecture instead.

## Commands

Backend (pure stdlib Python, no build step, no package manager):

```sh
./bin/listik serve --daemon   # start server + board at http://127.0.0.1:8787
./bin/listik stop
./bin/listik status           # server/db/search health
./bin/listik init             # create/update the sqlite schema
./bin/listik <command> --json # machine-readable output, available on every command
./bin/listik projects <slug> --routing '<json>'   # show/set the project's harness routing
```

```sh
python3 -m unittest discover tests   # Python test suite
```

The tests run against a temporary sqlite database created per test; they need no running server
and no Ollama. Also verify backend/CLI changes by exercising the relevant `listik` subcommand
directly (optionally with `--local` to bypass the HTTP server and hit sqlite directly) and by
checking `listik.log`.

Frontend (`web/`, Vue 3 + TypeScript + Vite):

```sh
cd web
npm install
npm run dev         # vite dev server, proxies /api to the backend
npm run typecheck   # vue-tsc --noEmit
npm run build
npm run smoke        # web/scripts/smoke.mjs
```

`web/scripts/verify-*.mjs` (`verify-projects`, `verify-deps-links`, `verify-cold-start`,
`verify-route-clear`, `verify-detail-sse`, `verify-task-delete`) are additional scripted checks
used in place of a unit test suite for board behavior; most run against `scripts/mock-api.mjs`.
`smoke` needs a reachable app and API and exits non-zero with an error if `/api/health` is down.

Database migrations exist as two parallel mechanisms — don't confuse them:
- `listik/db.py` (`SCHEMA_VERSION`) runs a soft, in-process migration automatically on every
  `listik init`/`serve`; this is what actually keeps `listik.db` current.
- `alembic/` (`alembic upgrade head`, `sqlalchemy.url = sqlite:///listik.db`) is an explicit,
  separate migration path mirroring the same schema. New schema changes should get both an
  update to `db.SCHEMA`/`SCHEMA_VERSION` and a corresponding alembic revision.

## Architecture

- `bin/listik` — argparse CLI and API client in one file. Every command goes through `call()`:
  if the server is up (`client.is_up`) it makes an HTTP request; otherwise it falls back to
  `client.local_call()`, which hits sqlite directly so an agent is never blocked by a dead
  server. New subcommands need both code paths kept in sync.
- `listik/store.py` — the core business logic (largest module): tasks, comments, stages, board
  aggregation. Both `server.py` (HTTP) and `client.local_call` (direct-db fallback) call into it,
  so it's the single place task/board semantics live. `claim` refuses an open hard blocker, a
  different holder, or a busy worktree (the `(project, worktree)` write lock applies only to
  `s3-impl`/`s4-judge`/tasks with no stage).
- `listik/deps.py` — the dependency graph. "Blocked" is not a stored status but a computed state:
  hard links (`blocks`/`blocked-by`/`waits-for`) gate `claim`/`done`; soft links
  (`parent-child`/`relates-to`/`discovered-from`) are informational only. A hard link created by
  an agent without `--confirm` is written as `suggested-blocks` (soft, "предложенный блокер")
  instead, and only becomes hard once a human runs `dep confirm`; `expire_return_handoffs` lazily
  expires the post-red-verdict return window on `ready`/`claim`.
- `listik/errors.py` — the single error format (`code`/`message`/`hint`) shared by the CLI, the
  server and the local fallback. "Not found" is raised as `errors.NotFound` (a `KeyError`
  subclass); a bare `KeyError` (a missing dict key, e.g. a card lacking a derived `*_title` field)
  stays `internal` — server handlers catch only `NotFound`, so such a bug reports 500/`internal`
  instead of a 404 telling the agent to "проверь идентификатор" (listik-xut1). `stage --to` with
  the current stage is a no-op that keeps the card, `stage_at` and holder, and stores the note as
  an event.
- `listik/search.py` + `listik/embed.py` — hybrid search: FTS5 (BM25) merged via RRF with vector
  similarity. A background thread in `server.py` recomputes embeddings for new/changed rows every
  45s via Ollama (`bge-m3`); if Ollama isn't running, search silently degrades to lexical-only
  rather than failing.
- `listik/server.py` — the HTTP API, implemented directly on `http.server.ThreadingHTTPServer`
  (no web framework/dependency). Serves both the JSON API and the built board (`web/dist`).
  One sqlite connection per thread (`threading.local`, closed with the request thread); a
  per-process generation counter reopens every connection after a `DatabaseError` or when a
  watcher thread (`start_db_watch`) notices that `listik.db`/`listik.db-wal` changed inode —
  the "database swapped under a running server" case (`_db_replaced` in `/api/health` and
  `listik status`).
- `listik/backup.py` — `listik backup` / `listik restore` via the sqlite backup API (a plain
  `cp` of a WAL database is inconsistent). `restore` refuses while the server is running unless
  `--stop`, keeps a `listik.db.bak-pre-restore-*` safety copy and removes stale `-wal`/`-shm`.
- `listik/mcp.py` — MCP server exposing `listik_*` tools; the same store/db as the CLI. Two
  transports: stdio (`claude mcp add listik -- bin/listik mcp`) and HTTP — `server.py` serves
  `POST /mcp` (Bearer token, no SSE) for a Listik deployed on another machine
  (`claude mcp add --transport http listik https://<host>/mcp --header "Authorization: Bearer …"`).
  Adding a tool: `TOOLS` + `call_tool`, and `WRITE_TOOLS` if it should push a board event (over
  stdio the process writes to sqlite past the server, so after a write tool it calls
  `POST /api/notify` in the background; over HTTP the server publishes the event itself).
- `listik/migrate.py` — despite the name, this is unrelated to database migrations. It
  inserts/updates the `<!-- BEGIN LISTIK --> / <!-- END LISTIK -->` block in *other* projects'
  `AGENTS.md`/`CLAUDE.md` (`listik init-projects`) so those projects' agents know to use Listik. The block body is read from `docs/harness-protocol.md` (`migrate.body()`) —
  change the protocol there, not in `migrate.py`.
- `listik/import_writerllm.py` (CLI: `listik import-from-bd`) — idempotent importer for the
  JSON/JSONL produced by `bd export` on WriterLLM's dolt-backed tracker; idempotency key is
  `(source, project, external_ref)`, `--update` writes a diff to the journal; tests in
  `tests/test_import_writerllm.py`.
- `web/` — the board UI. Every `Ui*` component must come from `@zoloto585/facet`
  (`node_modules/@zoloto585/facet`); read that package's `README.md` and this repo's
  `docs/facet-components.md` before adding or changing UI, and never hand-roll markup/CSS that
  duplicates a kit component.
- `config.toml` — server host/port, the board's auth token, and `[routing]` (harnesses per
  stage, transition kinds, the red-verdict return window); a project override lives in
  `[routing.projects.<slug>]` or the `projects.routing` column (`listik projects <slug>
  --routing`). `web/.env.example` documents the matching frontend env vars (`VITE_API_BASE`,
  `VITE_LISTIK_TOKEN`).

## This repo tracks its own development in Listik

Work on this codebase is itself queued in Listik (project `listik`). Read the task's
`spec_path`/acceptance before implementing, and move tasks through the pipeline stages
`s1-spec → s2-review → s3-impl → s4-judge → done` via `stage`, recording decisions with
`comment -k journal`. Working specs and step files live in `docs/specs/` locally and are not
committed (see `.gitignore`).

<!-- BEGIN LISTIK -->
## Listik — harness protocol

Listik is the single work queue and journal: `L=~/Projects/Listik/bin/listik`.

**Identify yourself.** Pass `--actor agent:<harness> --harness <harness>` on every command
(names from `listik actors`; `--holder` uses the same name). Don't rely on `export LISTIK_ACTOR`:
each shell call is a new process, and a leftover value may belong to a parent agent. Without
`--actor` writes are attributed to `$USER` (the human) and `dep add` creates a hard blocker.

**Who writes to the card.** The one who took the task writes to it: `claim` is the first action,
`heartbeat` keeps it alive, `comment -k journal` records the result, `comment -k verdict` is the
judge's own. The orchestrator only issues the card (`stage <id> --holder <next>`), watches that it
was taken, merges and closes. It does not claim, heartbeat, comment or write verdicts for another
harness: a card held by a writer who never claimed is indistinguishable from an abandoned run.
The board tells the two apart — `holder_taken=false` / `not_taken=true` means «выдана, но не
взята» (a holder was assigned, its own `claim` never came; after `board.assign_warn_minutes`,
15 min by default, the card shows up in «нужен ты»).

### Rules

1. Start with `ready`, `search`, then `show <id>`.
2. Never take a blocked task. If the blocker is abandoned, take the blocker or mark it `needs-owner`.
3. `claim <id> --holder <harness>` immediately after picking a task.
4. On work longer than 10 min, `heartbeat` every 10–15 min with a short note.
5. Change stages only with `stage`, never by editing the card text.
6. Comment kinds: `review` for remarks, `journal` for decisions/progress, `verdict` for verdicts.
7. Questions for the human go to `needs-owner <id> "exact question"`, not just the chat. When the human
   launched the work through your session (you orchestrate other harnesses or subagents), also put the full question
   text in the chat — both places, never a one-line summary; relay executors' questions verbatim. Questions
   never stall the pipeline: keep working other tasks, apply the answer (`needs-owner --clear`) when it comes.
8. Before handoff check `spec_path`, acceptance, worktree/branch, deps and journal.
9. Finish with `done <id> -r "short verifiable result"`.
10. Don't rewrite someone else's card — comment on it instead.

### Holder on transitions

`ready --harness <you>` lists only stages your harness is routed to. `claim` refuses (and says why)
on an open blocker, another holder, or a busy worktree; `--force` can't take another's card.

- **handoff** (`s2→s3`, `s4→done`): without `--holder` the server clears the holder and the next
  harness does `ready` → `claim`. With an explicit `--holder` the same transition issues the card:
  `stage <id> --to s3-impl --holder <next>` puts that harness in the holder and writes the assignment
  (`claim` event, author — the issuer), so the card is «выдана, но не взята» until its own
  `claim`/`heartbeat`.
- **sticky** (`s1→s2`, `s3→s4`): the holder stays. Same session continues as is; to pass to another
  harness run `stage <id> --holder <next>` — this only issues the card (it becomes «выдана, но не
  взята», `holder_taken=false`), the receiver starts with `claim <id> --holder <self>` (idempotent:
  the holder is already there, but the first claim of the holder itself is written to the history
  and turns the card into «взята»), then `heartbeat`. A `heartbeat` within the first 10 minutes after
  the assignment is written to the history as well: the card is not taken yet, so the usual throttle
  does not apply.
- **re-issue at the same stage** (`stage <id> --to <current> --holder <next>` — a new round after
  `release`, e.g. a red verdict): the stage, `stage_at` and the `stage` event stay as they were, but
  the holder is set and a fresh assignment is written. «Взята» is counted from the last assignment,
  so an old `claim` of the same harness from the previous round does not make the new round taken
  until it claims or heartbeats again. Without `--holder` this call remains a quiet no-op.
- **FAIL verdict return** keeps the holder: the judge claims `s4-judge` under its own name and writes
  the verdict itself; if it held the card under another name it does `release <id>` so the implementer
  can `claim`; in a single session just continue.

**Direct autostart** (a `kind: direct` route launched by Listik): the server has already issued the card
to you — stage `s1-spec`, holder = your harness. Your very first action is `claim <id> --holder <self>`,
before reading code. Right before the first code edit run `stage <id> --to s3-impl` yourself; finish with
`done`.

### Stages

**s1-spec** — read `show`, `context <id> --stage s1-spec`, existing `spec_path`. Write the Markdown
spec, acceptance checklist, one child card per portion (`new "…порция b" --parent <id шага>
--spec … --checklist … --review …`), soft links (`dep link`/`relates-to`). `dep add <portion>
<blocker>` from an agent is only a suggestion; the human confirms with `dep confirm`. Never use
`--confirm`. Next: `stage` → `s2-review` (sticky).

**s2-review** — read `context <id> --stage s2-review`. Change nothing in code or spec; only
`comment -k review` (and `review_path` if set). Next: `stage` → `s3-impl` (handoff).

**s3-impl** — read `context <id> --stage s3-impl --portion "<portion>"` and always `show <id>`
(answers to questions; after a FAIL return the last verdict is your list of fixes). The first action is
`claim <id> --holder <self>`, then `heartbeat` every 10–15 min while working. Edit code in the card's
worktree/branch, run checks, log in `comment -k journal` (progress and result). Don't commit. Found a
separate problem along the way — don't fix it here: file a card with `new "…" --discovered-from <id>`,
so the source card shows it in its links. Next: `stage` → `s4-judge` (sticky; other judge harness:
`stage <id> --holder <judge>`).

**s4-judge** — the judge takes the card itself (`claim <id> --holder <self>` if it arrived with another
holder), reads `context <id> --stage s4-judge` and the checklist, and never edits code. Write
`comment -k verdict` whose first line is exactly `VERDICT: PASS` or `VERDICT: FAIL` — the server reads
only that line and rejects any other format.
- PASS: nothing else is required. Commit, then `done <id> -r "…"`.
- FAIL: below the first line list the fixes, one per line: checklist item — what is wrong —
  file:line — what to do. This list is all the implementer gets. The server returns the card to
  `s3-impl` with the same holder; `release <id>` if you held it under your own name. If the implementer doesn't `heartbeat`/`claim` within the return window
  (24 h default), the holder is cleared and the task returns to `ready`.

### Commands

```sh
L=~/Projects/Listik/bin/listik
# append to every command: --actor agent:<who> --harness <who>

$L ready --harness <who>
$L search "gist of the task"
$L show <id>
$L new "…" --parent <id шага>          # portion of a step (parent-child)
$L new "…" --discovered-from <id>      # found while working on <id>: soft link right away
$L context <id> --stage <stage> [--portion "<portion>"]
$L dep add <id> <blocker>              # suggestion (hard only via human)
$L dep confirm <id> <blocker>          # human confirms
$L claim <id> --holder <who>
$L heartbeat <id> --holder <who> --note "what I'm doing"
$L stage <id>                          # next stage
$L stage <id> --holder <next>          # issue the card: holder is set, the receiver claims it
$L stage <id> --to <same stage> --holder <next>   # re-issue after release: same stage, fresh assignment
$L comment <id> "text" -k journal|review
$L comment <id> "VERDICT: PASS" -k verdict
$L comment <id> $'VERDICT: FAIL\n1. <item> — <problem> — <file:line> — <fix>' -k verdict
$L needs-owner <id> "question"
$L needs-owner <id> --clear "answer"
$L release <id>
$L done <id> -r "short verifiable result"
```

Cold start: everything needed is in `show <id>` (holder, stage, Q&A, journal, verdicts,
`spec_path`, worktree/branch, `holder_taken`/`not_taken`) and `context` — no chat history required.
<!-- END LISTIK -->
