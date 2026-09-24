# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Listik: one server, one SQLite database, one API — a task tracker plus
long-term memory with hybrid (full-text + vector) search, used across all of Дмитрий's projects.
`bin/listik` is both the CLI and the API client. `README.md` has the full command reference;
`docs/API.md` is the authoritative data/endpoint contract — read it before changing task/comment/dep
behavior. For the day-to-day task-tracking commands (`claim`/`heartbeat`/`stage`/`comment`/`done`)
used while working *any* task, see `AGENTS.md` — this file covers code architecture instead.

## Commands

Backend (pure stdlib Python, no build step, no package manager):

```sh
listik serve --daemon   # start server + board at http://127.0.0.1:8787
listik stop
listik status           # server/db/search health
listik init             # create/update the sqlite schema
listik <command> --json # machine-readable output, available on every command
listik projects <slug> --routing '<json>'   # show/set the project's harness routing
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
`verify-route-clear`, `verify-detail-sse`, `verify-task-delete`, `verify-assistant-apply`,
`verify-markdown`, `verify-routes-settings`) are additional scripted checks
used in place of a unit test suite for board behavior; most run against `scripts/mock-api.mjs`
(`verify-routes-settings` is the exception — it needs a live server, like `verify-projects`).
`smoke` needs a reachable app and API and exits non-zero with an error if `/api/health` is down.

Database migrations exist as two parallel mechanisms — don't confuse them:
- `listik/db.py` (`SCHEMA_VERSION`) runs a soft, in-process migration automatically on every
  `listik init`/`serve`; this is what actually keeps `listik.db` current.
- `alembic/` is an explicit, separate migration path mirroring the same schema. Set `LISTIK_DB`
  to a temporary SQLite file before `alembic upgrade head`; without it Alembic refuses to run.
  New schema changes should get both an update to `db.SCHEMA`/`SCHEMA_VERSION` and a
  corresponding alembic revision.

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
- `listik/routes_store.py` (+ `listik/skills.py`) — the `routes` table backing task launch
  presets: pipeline conveyor pipelines and direct harnesses, plus the command each launches.
  `routes.json` (root of the repo) is the install-time seed only — it fills an empty table once
  at `init`/serve, and nothing re-reads or reconciles it; the table is what both the server and
  CLI actually read and write (`GET/PATCH/POST/DELETE /api/routes`, `/api/routes/reorder`,
  `/api/routes/sync`, `/api/routes/launchers`, `listik routes`). Pipeline roles (`roles`) live in
  the db and are edited from the board's route settings and over HTTP (`PATCH`/`POST /api/routes`,
  listik-syu8); `kind`/`key`/`harness`/`position` stay unwritable. A role cell is `{provider, label, title}`
  plus the optional `skill` (a launcher skill, `плагин:скил` — e.g. `pi:pi-delegate`) and its
  flat `params`; the same validation (`routes._validate_roles`) serves the seed file and the HTTP
  path. `listik/skills.py` is a
  read-only catalogue of two things — `plugins/feature-pipeline/skills/*/SKILL.md` (title/hint/
  path) used to prefill new pipeline routes and flag routes whose skill directory disappeared,
  and the launcher skills (`plugins/*/skills/*-delegate`) offered to role cells — never a source
  of roles itself.
- `listik/backup.py` — `listik backup` / `listik restore` via the sqlite backup API (a plain
  `cp` of a WAL database is inconsistent). `restore` refuses while the server is running unless
  `--stop`, keeps a `listik.db.bak-pre-restore-*` safety copy and removes stale `-wal`/`-shm`.
- `listik/mcp.py` — MCP server exposing `listik_*` tools; the same store/db as the CLI. Two
  transports: stdio (`claude mcp add listik -- listik mcp`) and HTTP — `server.py` serves
  `POST /mcp` (Bearer token, no SSE) for a Listik deployed on another machine
  (`claude mcp add --transport http listik https://<host>/mcp --header "Authorization: Bearer …"`).
  Adding a tool: `TOOLS` + `call_tool`, and `WRITE_TOOLS` if it should push a board event (over
  stdio the process writes to sqlite past the server, so after a write tool it calls
  `POST /api/notify` in the background; over HTTP the server publishes the event itself).
- `listik/migrate.py` — despite the name, this is unrelated to database migrations. It
  inserts/updates the `<!-- BEGIN LISTIK --> / <!-- END LISTIK -->` block in *other* projects'
  `AGENTS.md`/`CLAUDE.md` (`listik init-projects`) so those projects' agents know to use Listik.
  The bodies differ per file (`migrate.body_for`): `AGENTS.md` gets the full protocol read from
  `docs/harness-protocol.md` (`migrate.body()`) — change the protocol there, not in `migrate.py`;
  the same text also ships as the skill `.agents/skills/listik/SKILL.md` (`migrate.SKILL_REL`) for
  harnesses that read `.agents/skills`, and `test_migrate` requires the two to be equal;
  `init-projects` symlinks each project's `.agents/skills/listik` to that skill in the install
  (`migrate.skill_source()`, via `app/current`) and gitignores the link, and `listik worktree`
  repeats the link in the task tree, hidden via `info/exclude`;
  `CLAUDE.md` gets only `CLAUDE_BODY`, a pointer to the `listik:listik` skill.
- `bin/listik-swarm` + `swarm/` — the swarm: drives waves of tasks to completion
  without a human re-running `launch` for each one. When `[swarm] enabled` is true in
  `config.toml` (`listik swarm on`, or the installer's question), `listik serve` keeps one
  process running for every project that has work and restarts it if it exits; the tick
  interval is 30s. `--project` still limits a manual run to one project. Node, stdlib only, no model calls of its own
  (the one exception is the merge-conflict arbiter below); talks to Listik exclusively through
  the installed `listik` CLI (`bin/listik … --json`), never `listik/` directly, and keeps no
  state between ticks — a crash is survived by restarting, a re-run sees the real card state and
  never double-launches. `config.mjs` (flags/defaults), `decide.mjs` (pure: input objects in,
  actions out — `node --test swarm/test/decide.test.mjs`), `run.mjs` (one tick: fetch via
  `listik.mjs`, decide, execute, log), `main.mjs` (loop/exit codes), `log.mjs` (file + stdout).
  `barrier.mjs` — the wave barrier: once nobody is running, it rebases and `merge --ff-only`s
  each done task's branch into the project's main tree one at a time, records the merge as a
  `рой: влито:` journal comment, unfreezes `frozen-by:` tasks whose owner just merged, runs the
  configured integration commands and removes merged trees, or opens a `swarm:halt` stop card
  when integration is unset/red; `git.mjs` is its git layer (`rebase`, `merge --ff-only`,
  worktree/branch removal, `--no-optional-locks` throughout); `arbiter.mjs` is the one place in
  the whole swarm that calls a model — only for a closed task's merge-time rebase conflict, via
  the `arbiter` command configured in `swarm.json` (`<data dir>/swarm.json`, path from `listik
  status`, re-read every tick: `integration`/`arbiter` commands, their timeouts, per-project
  overrides under `projects.<slug>`). End-to-end proof against a real server, real git and a fake
  worker is `tests/test_swarm_e2e.py`, plus `tests/test_swarm_barrier_e2e.py` for the barrier
  itself (both part of `discover tests`; skipped without `node`/`git`).
- `listik/import_writerllm.py` (CLI: `listik import-from-bd`) — idempotent importer for the
  JSON/JSONL produced by `bd export` on WriterLLM's dolt-backed tracker; idempotency key is
  `(source, project, external_ref)`, `--update` writes a diff to the journal; tests in
  `tests/test_import_writerllm.py`.
- `web/` — the board UI. Every `Ui*` component must come from `@zoloto585/facet`
  (`node_modules/@zoloto585/facet`); read that package's `README.md` and this repo's
  `docs/facet-components.md` before adding or changing UI, and never hand-roll markup/CSS that
  duplicates a kit component.
- `config.toml` — server host/port, the board's auth token, and `[routing]` (transition
  kinds, the red-verdict return window); a project override lives in
  `[routing.projects.<slug>]` or the `projects.routing` column (`listik projects <slug>
  --routing`). `web/.env.example` documents the matching frontend env vars (`VITE_API_BASE`,
  `VITE_LISTIK_TOKEN`).

## This repo tracks its own development in Listik

Work on this codebase is itself queued in Listik (project `listik`). Read the task's
`spec_path`/acceptance before implementing, and move tasks through the pipeline stages
`s1-spec → s2-review → s3-impl → s4-judge → done` via `stage`, recording decisions with
`comment -k journal`. Working specs and step files live in `docs/specs/` locally and are not
committed (see `.gitignore`).

## Project plugins (skills)

The skills used to run this project's work live in this repo and are made exactly for it:
- `plugins/feature-pipeline/` — the pipeline presets (`feature-pipeline:*`: skills, agents, hooks, references);
- `plugins/listik/` — the `listik:listik` task-protocol skill;
- `plugins/dsh/` — DeepSeek Harness bridge (`dsh:dsh-delegate` and related skills);
- `plugins/codex/` — OpenAI Codex CLI bridge (`codex:codex-delegate` and related skills);
- `plugins/opencode/` — opencode CLI bridge, two channels: GLM 5.3 Flash and DeepSeek v4.1 Flash (`opencode:opencode-delegate` and related skills);
- `plugins/pi/` — pi CLI bridge (`pi --mode rpc`), two channels: GLM 5.3 Flash and DeepSeek v4.1 Flash (`pi:pi-delegate` and related skills);
- `plugins/second-opinion/` — independent LLM review (`second-opinion:ask`).

Edit skills only there — never the copies under `~/.claude/plugins/cache/` or
`~/.claude/plugins/marketplaces/`, which are overwritten on plugin update.

<!-- BEGIN LISTIK -->
## Listik

Задачи этого проекта ведутся в Listik. Любое действие с задачами — завести задачу, записать
проблему, найденную по ходу другой работы, задать вопрос человеку, взять, передать, перевести
этап или закрыть — делай через скил `listik:listik`: вызови его до первой команды. Не заводи
TODO в чате или в файлах вместо карточки. Скила нет — полный протокол в блоке Listik файла
`AGENTS.md` этого проекта.
<!-- END LISTIK -->
