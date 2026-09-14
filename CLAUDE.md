# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Listik replaces `bd` (beads): one server, one SQLite database, one API — a task tracker plus
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

`web/scripts/verify-changes.mjs` and `verify-projects.mjs` are additional scripted checks used
in place of a unit test suite for board behavior.

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
  Adding a tool: `TOOLS` + `call_tool`, and `WRITE_TOOLS` if it should push a board event.
- `listik/migrate.py` — despite the name, this is unrelated to database migrations. It
  inserts/updates the `<!-- BEGIN LISTIK --> / <!-- END LISTIK -->` block in *other* projects'
  `AGENTS.md`/`CLAUDE.md` (`listik init-projects`) so those projects' agents know to use Listik
  instead of `bd`. The block body is read from `docs/harness-protocol.md` (`migrate.body()`) —
  change the protocol there, not in `migrate.py`.
- `listik/import_beads.py` — one-time importer walking `.beads/issues.jsonl` under `~/Projects`;
  imported tasks carry `source=beads` and keep their old ID in `external_ref`.
  `listik/import_writerllm.py` (CLI: `listik import-writerllm`) — idempotent importer for the
  JSON/JSONL produced by `bd export` on WriterLLM's dolt-backed beads tracker (outside
  `~/Projects`, so `import_beads` can't see it); idempotency key is
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
## Трекер задач — Listik, а не beads

Задачи ведутся в Listik: `~/Projects/Listik/bin/listik`. Каталог `.beads` в проекте —
архив: его никто не обновляет, писать туда нельзя (новые задачи должны попадать на общую
доску, а не в мёртвый трекер).

Полные правила: `~/Projects/Listik/AGENTS.md`, контракт данных: `~/Projects/Listik/API.md`.

## Listik — harness protocol

Listik is the single work queue and journal: `L=~/Projects/Listik/bin/listik`.

**Identify yourself.** Pass `--actor agent:<harness> --harness <harness>` on every command
(names from `listik actors`; `--holder` uses the same name). Don't rely on `export LISTIK_ACTOR`:
each shell call is a new process, and a leftover value may belong to a parent agent. Without
`--actor` writes are attributed to `$USER` (the human) and `dep add` creates a hard blocker.

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

- **handoff** (`s2→s3`, `s4→done`): the server clears the holder; next harness does `ready` → `claim`.
- **sticky** (`s1→s2`, `s3→s4`): the holder stays. Same session continues as is; to pass to another
  harness run `stage <id> --holder <next>`, and the receiver runs `claim <id> --holder <self>`
  (idempotent), then `heartbeat`.
- **FAIL verdict return** keeps the holder: a judge holding under its own name does `release <id>`
  so the implementer can `claim`; in a single session just continue.

### Stages

**s1-spec** — read `show`, `context <id> --stage s1-spec`, existing `spec_path`. Write the Markdown
spec, acceptance checklist, one child card per portion (`new "…порция b" --parent <id шага>
--spec … --checklist … --review …`), soft links (`dep link`/`relates-to`). `dep add <portion>
<blocker>` from an agent is only a suggestion; the human confirms with `dep confirm`. Never use
`--confirm`. Next: `stage` → `s2-review` (sticky).

**s2-review** — read `context <id> --stage s2-review`. Change nothing in code or spec; only
`comment -k review` (and `review_path` if set). Next: `stage` → `s3-impl` (handoff).

**s3-impl** — read `context <id> --stage s3-impl --portion "<portion>"` and always `show <id>`
(answers to questions; after a FAIL return the last verdict is your list of fixes). Edit code in the card's
worktree/branch, run checks, log in `comment -k journal`. Don't commit. Next: `stage` → `s4-judge`
(sticky; other judge harness: `stage <id> --holder <judge>`).

**s4-judge** — read `context <id> --stage s4-judge` and the checklist; if the card arrives with
another holder, first `claim <id> --holder <self>`. Never edit code. Write `comment -k verdict`
whose first line is exactly `VERDICT: PASS` or `VERDICT: FAIL` — the server reads only that line and
rejects any other format.
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
$L context <id> --stage <stage> [--portion "<portion>"]
$L dep add <id> <blocker>              # suggestion (hard only via human)
$L dep confirm <id> <blocker>          # human confirms
$L claim <id> --holder <who>
$L heartbeat <id> --holder <who> --note "what I'm doing"
$L stage <id>                          # next stage
$L stage <id> --holder <next>          # sticky pass to another harness
$L comment <id> "text" -k journal|review
$L comment <id> "VERDICT: PASS" -k verdict
$L comment <id> $'VERDICT: FAIL\n1. <item> — <problem> — <file:line> — <fix>' -k verdict
$L needs-owner <id> "question"
$L needs-owner <id> --clear "answer"
$L release <id>
$L done <id> -r "short verifiable result"
```

Cold start: everything needed is in `show <id>` (holder, stage, Q&A, journal, verdicts,
`spec_path`, worktree/branch) and `context` — no chat history required.
<!-- END LISTIK -->
