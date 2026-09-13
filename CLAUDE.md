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
- `listik/mcp.py` — stdio MCP server exposing `listik_*` tools (registered via
  `claude mcp add listik -- bin/listik mcp`); the same store/db as the CLI, just a third
  transport.
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
