---
name: low
description: "Pipeline preset pipeline-full:low: spec by Opus low, critique by DeepSeek V4.1 Flash and GLM 5.3 Flash in pi plus Haiku 5.5 high, code by devin on SWE-2 max, acceptance by three Haiku 5.5 max lenses, judge Grok 4.7 high only on a lens finding. Spec → critique → implementation → independent acceptance when the Max subscription quota is tight. Run only by explicit name: a Listik card route (launch_route or label process:<key>) or a human naming the preset; never pick it up by task similarity."
argument-hint: "[path to <id>.<X>.md or task description]"
license: MIT
---

# Pipeline pipeline-full:low

Subscription: spec (Opus low), critic Haiku 5.5 high, lenses Haiku 5.5 max (three local subagents). Outside: code
(devin `swe-2-max`), judge (Grok 4.7 high, only on a lens finding). Clean lenses — you commit.

**Invoke the skill `pipeline-core:core` and read the core at the path it names, in full, before the first action**
(in the repo: `plugins/pipeline-core/references/pipeline-core.md`). This file only adds who runs each stage and how.

Run only by explicit name: a Listik card route (`launch_route` or label `process:full-low`) or a human
(`/pipeline-full:low`). Launch = author's consent to send out: spec, checklist, read-only code to critics; the
working directory to the executor; the portion diff to the judge when called. Lenses (Haiku 5.5 max) are local.
Secrets: stage 1 keeps them out of the spec, portion write bounds out of the diff. Run commands from the main tree root.

## Roles

| Stage | Who | Model, effort | First report line |
| --- | --- | --- | --- |
| 1. Spec and checklists | `Agent` `pipeline-core:pipeline-spec-writer-low`, **`model: opus` in the call** | Opus low | `готово` / `вопрос` |
| 2. Spec critique | `deepseek`, `glm` via `pi:pi-delegate`; `haiku` via `Agent` `pipeline-core:pipeline-critic`, **`model: haiku`** | DeepSeek V4.1 Flash, GLM 5.3 Flash; Haiku 5.5 high | file per critic → your summary `review-<X>.md` («Блокирующие», «Существенные») |
| 3. Implementation | `devin:devin-delegate`, background, `--write` | `--thinking max` (`swe-2-max`) | `готово` / `не смог` / `вопрос` |
| 4. Acceptance: lenses | three `Agent` `pipeline-core:pipeline-lens`, **`model: haiku`**, round `r1` only | Haiku 5.5 max | file per lens, `чисто` / `находки` |
| 4. Acceptance: judge | `/grok:delegate`, background; on a lens finding, «Критичные инварианты», or `r2`+ | `--model grok-4.7 --effort high` | `зелёный` + hash / `красный` |

Acceptance is mandatory. devin's `готово` is a claim: lenses verify it, the judge on a finding.

## Required skills

Missing any — stop (`pipeline-core.md`, «Внешние скилы»). Never call scripts from the cache.

- [`pi:pi-delegate`](../../../pi/skills/pi-delegate/SKILL.md) (`plugins/pi/skills/pi-delegate/SKILL.md`) — stage 2; [`pi:pi-check`](../../../pi/skills/pi-check/SKILL.md) — readiness; [`pi:pi-jobs`](../../../pi/skills/pi-jobs/SKILL.md) — collect; [`pi:pi-runtime`](../../../pi/skills/pi-runtime/SKILL.md) — contract.
- [`devin:devin-delegate`](../../../devin/skills/devin-delegate/SKILL.md) (`plugins/devin/skills/devin-delegate/SKILL.md`) — stage 3; [`devin:devin-check`](../../../devin/skills/devin-check/SKILL.md) — readiness; [`devin:devin-jobs`](../../../devin/skills/devin-jobs/SKILL.md) — progress, collect; [`devin:devin-runtime`](../../../devin/skills/devin-runtime/SKILL.md) — contract, resume.
- `/grok:delegate` — stage 4 and preflight; `/grok:setup` — readiness if preflight failed not on balance; `/grok:status` — progress; `/grok:result` — fallback collect; `grok:grok-cli-runtime` — contract.
- [`listik:listik`](../../../listik/skills/listik/SKILL.md) (`plugins/listik/skills/listik/SKILL.md`) — card, stages, journal, questions.

## Prefix

```
STEPS=docs/specs/steps
BASE=<id>        # без карточки — adhoc-<ГГГГ-ММ-ДД>-<слаг>
WT=.             # в треке — абсолютный путь дерева трека
```
`<X>` — portion letter, `<R>` — acceptance round, `DJOB`/`JOB` — executor/judge job ids (`DJOB` from `devin:devin-delegate`).

## Stop factor

`pipeline-core.md`, «Стоп-фактор». Any role unavailable (preflight failed, not installed/logged in, launch failed,
wrong model), at preflight or any stage — only:

1. journal `стоп: <роль> — <харнесс> недоступен: <причина>` (executor: `стоп: исполнитель — devin недоступен: <причина>`);
2. `listik needs-owner <id> "<вопрос>"`;
3. the same question in chat.

No substitute harness, model, local subagent or own hand, headless too. Re-running the same harness and model in
the same role is allowed (e.g. a new devin session after a failed `resume`). Stage 2 critics follow quorum instead.

## Preflight — before stage 1

- Executor: `devin:devin-check`; effort fixed at `--thinking max`.
- Critics: `pi:pi-check` full probe, `ok` of channels `deepseek` and `glm`; neither — quorum stop per core.
- Judge: a real run via `/grok:delegate` without `--background`; anything but `ok` (402 included) — stop, ask the author with the error verbatim:

  ```
  /grok:delegate --no-web --model grok-4.7 --effort low Ответь ровно одним словом: ok
  ```

- Lenses: agent `pipeline-core:pipeline-lens` visible in the session, else stop per `pipeline-core.md`, «Приёмка линзами» (`стоп: линза — агент pipeline-core:pipeline-lens не найден`).

## Stages

### 1. Spec and checklists — `pipeline-spec-writer-low`, `model: opus`

- `model: opus` on every launch, edit mode included.
- Task: spec path **or** author text; `$STEPS`; papers name `<id>`. In a track add the **track boundary** (directory or layer, author's words). Do not retell portion/bounds/checklist rules.
- Always show the agent's assumptions to the author.
- `вопрос` → question protocol; `готово` → journal `шаг <id>: старт, пресет pipeline-full:low, порций <K>`.

### 2. Spec critique — DeepSeek + GLM + Haiku

- One message, one shared task, all background: `deepseek` and `glm` — `pi:pi-delegate`, channels `deepseek` and `glm`, `--permission read`, collect `result` via `pi:pi-jobs`; `haiku` — `Agent` `pipeline-core:pipeline-critic`, **`model: haiku` in the call**.
- Quorum: a valid answer from `deepseek` or `glm`; Haiku never counts. A dropped critic is not a stop while quorum holds.
- No quorum — journal `стоп: критика — кворум не набран: <кто не дал годного ответа и почему>`, `needs-owner`, same question in chat. Never replace `deepseek`/`glm` with Haiku, Sonnet or another Anthropic critic.
- Everything else (secrets, launch, task, 15-minute window, collection, validity, retry, merge into `$STEPS/$BASE.review-<X>.md`) — `pipeline-core.md`, «Критика ТЗ».
- Decide per «Решение по сводке»: each item `принять`/`отклонить`/`автору` in `$STEPS/$BASE.decisions-<X>.md`; only `автору` to the author; journal line.
- Any `принять`/`автору` → resume the spec writer via `SendMessage` with paths to `$STEPS/$BASE.decisions-<X>.md` and `$STEPS/$BASE.review-<X>.md`; after `/clear` — new launch in edit mode (`model: opus`, paths to portion, checklist, decisions, summary).
- One critique per portion; the revised spec is not re-critiqued.

### 3. Implementation — `devin:devin-delegate`

Hand over the card (devin claims it itself): `listik stage <P> --to s3-impl --holder devin --actor agent:claude --harness claude`.

**Invoke `devin:devin-delegate`** with `--write`, `--thinking max`, `--cwd $WT`, `--label "$BASE <X>"`,
`--session "$BASE-<X>"`, background, and the portion path **as a string** (never the contents). Task:

```
Implement the spec portion <path $STEPS/$BASE.<X>.md, literal>. Read it in full; «Границы правки» is a hard
constraint. Understand the code before editing. Run the project's checks (tests, linter, types). Do not commit; no
git add/stash/checkout/reset. Do not touch .env, *.key, *.pem, credentials.json. Do not adapt tests to the code or
silence guards. Unclear or contradictory requirement — do not choose for the author: do what does not depend on the
answer, reply with first line `вопрос` and questions, each with options, one marked «предпочитаю». Reply ≤30 lines,
first line `готово`, `не смог` or `вопрос`; then: done per requirement / not done with reason / open questions and
discrepancies / changed files / checks, one line each. Any requirement unmet — `не смог`. No listings or logs.

Listik, card <P> (literal id): report on it, not only in the tree; it is your card, your claim proves you started.
listik
First action: listik claim <P> --holder devin --actor agent:devin --harness devin
Every 10–15 minutes of work:
  listik heartbeat <P> --holder devin --note "<что делаешь>" --actor agent:devin --harness devin
Before replying:
  listik comment <P> "<первая строка отчёта и суть: файлы, проверки>" -k journal --actor agent:devin --harness devin
On `не смог` or `вопрос` — the same journal, then release <P>.
```

- Job id → journal `порция <X>: devin <job-id>`, `DJOB=`. Wait/collect per `devin:devin-delegate`, progress via `devin:devin-jobs`. devin's reply verbatim is the stage report.
- Session → journal `порция <X>: devin сессия <id>` (the name is the id).
- Retry (`pipeline-core.md`, «Шаг 0 — конфиг»): after `вопрос` continue the session with the author's answers verbatim; after red — with «Красные пункты приёмки» verbatim: `resume --session "<имя>" --background --label "…"` per `devin:devin-runtime`.
- Fallback when `resume` returns exit 2 (no session) or another error: new run via `devin:devin-delegate`, session `$BASE-<X>-2`, …; text = current task + «Ответы автора» or «Красные пункты приёмки» verbatim + «Changes from the previous round are already in the working tree — do not revert or rewrite them, build on top.»; journal the fallback and the new `порция <X>: devin сессия <имя>`.
- `не смог` → retry with its wording within budget; failure, timeout, empty reply → per `devin:devin-jobs`, as `не смог`; `готово` → stage 4.

### 4. Acceptance — Haiku lenses, Grok 4.7 high judge on findings

**`r1` — lenses**, strictly per `pipeline-core.md`, «Приёмка линзами» (card to yourself, diff package, secret
check, fingerprints, three `pipeline-core:pipeline-lens` with `model: haiku` in one message, collection, branching).
`r2`+ skip lenses.

**Lenses clean, no «Критичные инварианты»** — no judge: commit per core, post `VERDICT: PASS` with the hash (core
command), check `git -C "$WT" log --oneline -1`, continue as after green.

**Lens findings, «Критичные инварианты», or `r2`+ — judge.**

- `r2`+: diff package per `pipeline-core.md`, «Пакет диффа для приёмки».
- Hand over the card; the judge claims and writes the verdict itself: `listik stage <P> --holder grok --actor agent:claude --harness claude`
- Never hint that something is not a defect; you resolve findings from its report.
- Write the task below, paths substituted, to `$STEPS/$BASE.judge-<X>.r<R>.task.md`; never put task text on the command line.
- **Invoke `/grok:delegate`** `--background --write --no-web --model grok-4.7 --effort high`, cwd `$WT`, then one line: `Task is in <absolute path>: read and execute it.` On `r1` the task lists the three lens files.
- Wait via `/grok:status` for phase `done`/`failed`/`cancelled` (not for `running` to vanish), ≤3 hours; over — stop, journal line, ask the author.
- Read `$STEPS/$BASE.judge-<X>.r<R>.md` after `done`; `/grok:result` only if it is missing or empty, from the launch directory.

Judge task (file `$STEPS/$BASE.judge-<X>.r<R>.task.md`):

```
You are the acceptance of one spec portion and its final authority. Never edit code: your result is a verdict and,
if green, a commit. Write your whole reply yourself to <absolute path $STEPS/$BASE.judge-<X>.r<R>.md> — the only
file you may create. Do not run `listik done`: the orchestrator closes the card.

<блок «Правила судьи: дерево и живые пункты» из pipeline-core-accept.md — вставь дословно>

Paths (literal): acceptance checklist, portion file, diff package; on a repeat round also the previous round's dump —
review the difference, not the whole portion. First round with lens findings: also `…lens-<X>.scope.md`,
`…lens-<X>.holes.md`, `…lens-<X>.intent.md`.

Read the portion and the checklist. Verify every checklist item by fact — open code, run the command, read output;
"looks done" does not count. In the diff package hunt for cut corners: tests adapted to the code, weakened asserts,
skips and xfail, disabled lint rules, silenced guards, edits outside the portion write bounds, off-topic edits. Run
the checklist's «Проверки порции» — the minimum. Run the full suite too if the diff touches widely shared code,
build/test config or dependencies, or a narrow run leaves doubt; say in one line whether and why. No executable code
changed (docs, texts, comments) — no tests, one line «код не менялся». No such section — set the minimum by the same rule.

Lens findings are signals, not verdicts. Verify each by fact (command, output, `path:line`) and mark it: `чинить` —
portion defect, red-list item; `вынести` — real problem outside the portion requirements or bounds, line
`вынести: <заголовок> — <суть, path:line>`, does not block green; `отбросить` — not confirmed, line
`отбросить: <находка> — <почему>`. Clean lenses do not replace your own full check.

Non-empty «Критичные инварианты» — for each item run a concrete breaking scenario (concurrent requests, redelivery,
negative value, …) with an observable result: log, test DB state, API response. Retelling code is not a check.

Checklist has a negative control — run it and show the refusal in command output; no refusal — red item. Portion
adds or fixes a check (barrier, `claim`, gate, guard, validation, API refusal) without a negative control in the
checklist — build and run the refusal scenario yourself.

Every number, SHA and quote in verdict and report is a copy from command output of this round: hash from
`git rev-parse --short HEAD` after the commit, counts from the test runner, locations as `path:line`. Check every
executor number or hash against your own output.

Verdict on the first line:
- `зелёный` — all items closed, checks green, no cut corners. Then commit the portion yourself.
  `git add` only the portion's new and modified paths; never a path removed via `git rm` (fails with
  `did not match any files`, stages **nothing**). Commit `git commit -m "<сообщение>" -- <все пути
  порции, включая удалённые>`. Check `git show --stat --name-status HEAD` — exactly the portion paths.
  Incomplete — a second commit on top; `git commit --amend` is forbidden; both hashes on the second line of
  the verdict, comma-separated, the last one final. No `-A` or `.`. Do not commit step papers (spec directory,
  `*.journal.md`). Do not touch .env, *.key, *.pem, credentials.json.
  Forbidden: push, reset, stash, file checkout, clean, rebase, amend, --no-verify.
- `красный` — no commit. Numbered list: checklist item or cut corner, what is wrong and what exactly to finish.
  A write-bounds finding starts with `ГРАНИЦЫ:`. Items go to the executor verbatim: name the defect, not an impression.

Reply ≤40 lines: first line `зелёный` with the commit hash or `красный`; then checklist items with outcomes / cut
corners / checks, one line each / deferred or moved. No listings or logs.

Listik, card <P> (literal id): claim it and write the verdict yourself — nobody does it for you.
listik
First action: listik claim <P> --holder grok --actor agent:grok --harness grok
Verdict right after the reply; first line of the argument exactly `VERDICT: PASS` or `VERDICT: FAIL`
(the server reads only it), then the commit hash or the red items verbatim, one per line:
  listik comment <P> $'VERDICT: PASS\n<hash7>' -k verdict --actor agent:grok --harness grok
  listik comment <P> $'VERDICT: FAIL\n<пункты дословно>' -k verdict --actor agent:grok --harness grok
Red verdict — also release <P>.
```

Job id → journal and `JOB=`. Verdict:

- **зелёный** — check `git -C "$WT" log --oneline -1` (no commit — ask the author); journal `порция <X>: готово (коммит <hash7>)`, deferred/moved items as separate lines. Next portion from stage 3 if its critique already ran in parallel (`pipeline-core.md`, «Цикл»), else stage 2.
- **`вынести:`** in an `r1` report (green or red) — a card each `--discovered-from <P>` per `pipeline-core.md`, «Приёмка линзами», id to the journal. **`отбросить:`** — journal only.
- **красный** — `K+1` per core (lens findings alone do not count); back to stage 3 with the items verbatim, within budget, by session continuation; new run only as fallback.
- **`failed`, `402`, empty reply** — no verdict, not red: journal line, ask the author; the portion stays open, do not start the next.

## Preset pitfalls

| Symptom | Action |
| --- | --- |
| devin returned analysis, tree clean | `--write` missing: rerun; the old reply is not a report |
| devin not on `swe-2-max` | `--thinking max` missing: rerun; the old reply is not a report |
| Background job not found by status | call status/collect from the launch directory |
| Lens ran not on Haiku | rerun it with `model: haiku` |
| Lens changed the tree (fingerprints differ) | stop per core: `needs-owner`, ask the author, commit nothing, no judge |
