---
name: listik-product
description: Implement and validate the Listik product specification in dependency order across the Python API/CLI, SQLite schema, harness workflow, and Vue board. Use when working on docs/specs/listik-product.md or one of its step files.
---

# Listik product implementation

Use this skill for work tracked by the Listik queue itself. The source of truth is
`docs/specs/listik-product.md`; its step files under `docs/specs/steps/` define the acceptance
criteria. Keep implementation incremental: finish and verify step 01 before depending on it in
steps 02–04, then build the UI and wrapper steps.

## Start of a work session

Set `LISTIK_ACTOR=agent:codex`, ensure the local server is running (`bin/listik serve --daemon`),
and run `bin/listik ready`, `bin/listik search "<feature>"`, and `bin/listik show <id>` before
claiming a task. Claim immediately with an explicit holder and note. Send a heartbeat every
10–15 minutes. Record decisions and handoffs with `comment -k journal`; use `review` and
`verdict` for those respective records. Ask the owner with `needs-owner`, and close with
`done -r` only after the step acceptance checks pass.

If no matching card exists, create one in project `listik` with `spec_path` pointing at the
relevant step file, then claim it. Do not invent hard blockers from model guesses; use a soft
relation or a documented suggestion until the owner confirms it.

## Implementation rules

- Read `API.md`, the relevant step file, and existing tests before changing behavior.
- Preserve compatibility for existing tasks, `search`, `show`, and direct CLI fallback when the
  server is unavailable. Prefer SQLite migrations that are safe on the existing database.
- Keep document indexing derived from file content hashes; never copy an entire Markdown spec
  into every task row. Context output must be deterministic in both text and JSON forms.
- Keep routing, dependency readiness, and stage transitions server-side. UI actions call the API
  and surface its errors; they do not maintain a second source of truth.
- For Vue work, read `web/node_modules/@zoloto585/facet/README.md` and
  `docs/facet-components.md` before using a component. Search purpose comments for unknown
  component names and enable strict template checking in `web/tsconfig.json`.
- Do not add a dependency unless the existing Python standard library or installed web toolchain
  cannot satisfy the requirement.

## Verification

Run focused Python tests or scripts for the changed API/CLI behavior, then from `web/` run
`npm run typecheck`, `npm run build`, and the applicable smoke script. For mobile or board work,
exercise the 1024px and 360px viewports when the browser harness is available. Include command
results and any environmental limitation in the Listik journal before handoff.

Relevant references:

- Product scope: `docs/specs/listik-product.md`
- Step acceptance: `docs/specs/steps/`
- API contract: `API.md`
- Board component notes: `docs/facet-components.md`
