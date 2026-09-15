## Что это

`Listik` (`~/Projects/Listik/bin/listik`) — единственный трекер задач по всем проектам.
Здесь же долговременная память и гибридный поиск
(полнотекст + векторы) по всей истории задач.

Полный контракт данных — `docs/API.md`, ежедневные команды — `README.md`.

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

## Зависимости

Связи: `$L dep add <id> <блокер>` (задача ждёт блокер), `$L dep rm <id> <блокер>`.
Жёсткие (`blocks`, `blocked-by`, `waits-for`) запрещают брать и закрывать задачу;
мягкие (`parent-child`, `relates-to`, `discovered-from`) — только «прочитай сначала».
Проверить конкретную задачу: `listik show <id>` → поле `deps_state` (`verdict`, `reasons`,
`blocked_by`, `waiting_for`) или MCP-инструмент `listik_can_take`.

## Тонкости

- `--json` есть у всех команд — для машинной обработки.
- `listik` в MCP: `claude mcp add listik -- /Users/dmitry.fomin/Projects/Listik/bin/listik mcp`
  (18 инструментов, имена `listik_*`).

## UI-кит @zoloto585/facet

Все Ui*-компоненты в этом проекте — из `@zoloto585/facet` (node_modules/@zoloto585/facet или
workspace-пакет `packages/facet`, смотря как подключено). Перед тем как использовать компонент:
1. Прочитать `node_modules/@zoloto585/facet/README.md` целиком — quickstart, темы/гаммы,
   мотор-контракт `data-motion`, миграции в `CHANGELOG.md` того же пакета.
2. Если не знаете точное каноническое имя нужного компонента — искать по смыслу задачи, а не
   угадывать по аналогии с другими UI-китами:
   `grep -ril "ключевое слово задачи" node_modules/@zoloto585/facet/src/components`
3. Прочитать purpose-комментарий (`/** ... */`) в начале файла найденного компонента — пропсы/слоты
   описаны там словами, точные типы — в самом `<script setup lang="ts">`.
4. Не дублировать разметку/CSS компонента кита вручную — если нужна кнопка/бейдж/поповер/т.п.,
   это `UiButton`/`UiBadge`/`UiPopover`/…, а не рукописный аналог.

Краткая выжимка по компонентам, которые нужны доске, с точными пропсами и ловушками —
`docs/facet-components.md`.
