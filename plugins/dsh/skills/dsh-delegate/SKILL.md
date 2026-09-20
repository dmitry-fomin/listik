---
name: dsh-delegate
description: Hand a task to DeepSeek Harness (dsh) — a second agentic harness that reads and greps the codebase itself in its own context. Use to sweep an unfamiliar subsystem, map it, find every occurrence, or get another model's take with file access. Runs in the background with a job-id; read-only by default.
when_to_use: Triggers — "delegate to dsh", "ask deepseek", "let dsh figure it out", "run dsh in the background", "hand out several dsh jobs". An explicit request is consent to launch. Also fits without dsh being named when a whole unfamiliar area must be swept and pulling it into context is expensive. Not for unrequested code edits, trivia, or syntax/API questions. Verifying an existing hypothesis is /dsh:dsh-second-opinion; managing running jobs is /dsh:dsh-jobs.
argument-hint: "[--permission read|bash|write] [--sync] [what dsh should do]"
context: fork
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh *)
---

Request: $ARGUMENTS

dsh is an agent with tools, not a model you ask a question: it reads files, greps, runs
commands in the working directory and obeys the same `CLAUDE.md`/`AGENTS.md` you do. The
point of delegating is that it spends its own context on the investigation, not yours.

This skill runs forked (`context: fork`): you are an isolated context working in the
background, and only your final message reaches the conversation. So **name the job-id in
that final message** — it is the human's handle for `/dsh:dsh-jobs`. The fork replaces the
`dsh:dsh-runner` subagent for this path; don't call another agent from here.

Script contract, job states and exit codes: skill `dsh-runtime`.

## Route

1. **Launch** — one line of stdout is the job-id:

   ```bash
   ${CLAUDE_PLUGIN_ROOT}/scripts/dsh-run.sh run --background --label "<topic>" [options] <<'TASK'
   <task text>
   TASK
   ```

2. **Wait for it** with one backgrounded Bash call per job (see `dsh-runtime`). You are
   already the background, so waiting here costs the conversation nothing.

3. **Collect** with `result <job-id>`. There is no session continuation in headless dsh:
   `resume` exits 2, and a follow-up is a fresh `run` carrying the context again.

**Synchronous route** only when the human asks to wait or the question is plainly small:
the same call without `--background`, foreground ceiling 540 s, so set the Bash timeout to
600000 ms.

## Several jobs at once

There is no one-run-at-a-time limit. Split independent work (different subsystems,
different questions) and launch the batch **in a single message, one Bash call per job** —
spread across messages they serialize and nothing runs in parallel.

- Split by boundary, not by volume: two runs over the same area buy two retellings.
- `--label` is mandatory past the first job.
- Keep a batch to 2–4 — you have to reconcile the answers in your own context, and each dsh
  answer is a whole final message, not a digest.
- Never run parallel `--permission write` into one directory. Several writers are fine only
  with separate `--cwd` and non-overlapping areas.

Reconcile the answers yourself and say where the runs agreed and where they diverged.

## What to put in the task

1. **The goal, not your hypothesis.** "Find out why N grows when M" beats "check whether
   I'm right that it's the cache" — a supplied hypothesis nearly always gets confirmed.
2. **The boundary of the area** — the directory or file list, plus an explicit ban on
   `.env`, `*.key`, `*.pem`, `credentials.json`. The task text is the only place that ban
   can be set, because dsh opens files on its own.
3. **The shape of the answer** — conclusion, files and lines, what was verified, what stayed
   unclear. It comes back as one final message.

## Permissions

One flag, three values (`--write` still works as an alias for `--permission write`):

| `--permission` | Sandbox | When |
| --- | --- | --- |
| `read` | `read-only` | default, any investigation |
| `bash` | `read-only` | same tier in dsh — commands run, the OS sandbox refuses writes |
| `write` | `workspace-write` | only if the human asked for a change in this message |

Never infer write access from a task merely looking like implementation: a read-only run
that hits the ban says so honestly, which is cheaper than an unrequested edit. A background
write job keeps editing files while you do other things, so launch one only when the human
knows it is running.

## Parsing flags out of the request

Cut flags out of the task text so they don't land in the prompt as content.

| In the request | Do |
| --- | --- |
| `--write`, "have it fix", "make the change" | `--permission write` |
| `--sync`, "wait for it", "I need it now" | drop `--background` |
| a directory or subsystem named | `--cwd <path>` |
| "take as long as it needs" | `--timeout 0` |

Model, provider and effort level are never your choice: the run uses the human's
`~/.dsh/settings.yaml`. Don't add `--model`, `--provider` or `--effort` even when the task
looks hard or the human said "think deeply".

## Handling the answer

- Show dsh's answer verbatim, marked as another harness's output rather than your
  conclusion or an established fact.
- **Compare, don't adopt.** Full agreement with your own hypothesis is a reason to
  re-check, not to relax; divergence is the valuable part — report it first.
- Suspect the answer is invented — run `transcript <job-id>` to see whether files were read.
- If dsh failed, report that instead of quietly finishing the task for it.

## If the job goes wrong

| What you see | Do |
| --- | --- |
| running far longer than expected | `logs <job-id>` — empty stderr while `running` is normal |
| the task turned out to be misphrased | `cancel <job-id>`, rephrase, relaunch |
| status `timeout` or `failed` | `result` still returns what arrived; the cause is in `logs` |
| status `orphaned` | the worker died with the machine; no answer is coming, relaunch |

Project red lines hold inside dsh too: no commits, pushes, recursive deletes or secrets. If
the task implies any of those, ask the human before delegating.
