---
name: devin-second-opinion
description: "Check your own hypothesis against devin, which reads the relevant code itself — for when retelling the context would be expensive or would frame the question toward your conclusion. For high-cost calls: an architectural decision, a contested finding, a second pass on a bug with no movement. Strictly read-only."
when_to_use: Triggers — "second opinion on this code", "have devin check my conclusion", "what does devin say about this bug". An explicit request is consent to launch. Not for routine or single-answer questions, and not as a substitute for your own analysis — think first. If there is no hypothesis yet and you just need the area mapped, that is /devin:devin-delegate; questions about a running job are /devin:devin-jobs. If the hypothesis fits in a paragraph and involves little code, ask a model directly instead — a whole agentic run is overkill.
context: fork
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/devin-run.sh *)
---

Request: $ARGUMENTS

This is `devin-delegate` in consultation mode: devin reads the code itself and reaches its
own conclusion. Mechanics, waiting and exit codes are in `/devin:devin-delegate` and
`devin-runtime` — below is only what differs.

1. **Your own conclusion comes first.** Nothing to compare means nothing to delegate.
2. **Phrase the question independently of your conclusion** — a supplied hypothesis nearly
   always gets confirmed.
3. **Scope the area** and forbid `.env`, `*.key`, `*.pem`, `credentials.json` in the task
   text.
4. **If the human did not ask for the consultation**, show them the task text and working
   directory and wait for agreement.
5. **Launch in the background on the default `--permission read`:**
   `run --background --label "<hypothesis topic>"`. A second opinion must not change the
   working tree, and on devin `--permission bash` already permits edits it judges safe.
6. **Collect, compare against your own analysis, then report.** This skill runs forked
   (`context: fork`), so only your final message reaches the conversation — put the
   job-id in it.

One consultation per hypothesis. A second run on the same hypothesis adds a vote, not
knowledge. Different hypotheses or different areas can run in parallel.

Needs a follow-up on the same hypothesis: give the run `--session <name>` and continue with
`resume --session <name>` instead of restating the context.

## Reading the answer

- Label it as one external harness's opinion and name the model (SWE-2 at the effort level
  you used).
- **Instant, complete agreement deserves scepticism.** Models share blind spots and share
  your framing. The divergence is the finding — report it first.
- Confirm files were actually read: `transcript <job-id>`.
- An empty answer with exit 6 means a tool was blocked by read-only mode, not that devin
  had nothing to say — report the limit rather than rerunning with wider rights.
- Don't turn the review into an immediate code change — report to the human first. An edit
  is a separate decision and a separate `/devin:devin-delegate` call.
