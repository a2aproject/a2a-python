---
name: mistake-reflection
description: Use when you discover you made a mistake — caught by the user, by a tool result, by your own re-reading, or by a failed check. Appends a structured entry to docs/ai/ai_learnings.md and re-reads recent entries to avoid repeats.
---

# Mistake Reflection

Implements the mistake-handling step of `AGENTS.md` §"Mandatory
workflow".

## When to load this skill

Trigger on ANY of these, without waiting for the user to ask:

- The user corrects a factual claim, code change, or assumption.
- A tool result contradicts something you just stated or did
  (lint failure, test failure, type-check failure, file not found,
  command exit non-zero on something you said would succeed).
- You re-read a file or doc and realize a prior statement was wrong
  or unverified.
- You realize mid-task that you skipped a required step
  (e.g. didn't read `docs/ai/coding_conventions.md`,
  `docs/ai/mandatory_checks.md`, or `docs/ai/evidence_rules.md` at
  task start).
- You stated an inference as a fact without a `file:line` citation
  and later had to walk it back.

If unsure whether something counts: it counts. False positives are
cheap; false negatives are how the same mistake recurs.

## Procedure

Do these in order. Do NOT defer to the end of the task.

1. **Acknowledge the mistake to the user explicitly** in the current
   response. One or two sentences. No hedging, no minimization.
2. **Read recent entries** in `docs/ai/ai_learnings.md` (at minimum
   the last 5 entries, or the whole file if shorter). If the current
   mistake is a recurrence of an existing rule, say so explicitly and
   reference the prior entry's date — do not silently duplicate.
3. **Append a new entry** to `docs/ai/ai_learnings.md` using the
   template below. Append; do not rewrite existing entries.
4. **Continue the original task** only after steps 1–3 are done.

## Entry template

Copy this verbatim, fill in each field, append to the end of the file
(after the existing `---` separator):

```markdown
## YYYY-MM-DD — <one-line summary>

- **Mistake**: What went wrong. Be concrete. Quote the wrong claim or
  describe the wrong action. Include `file:line` references where
  applicable.
- **Trigger**: How the mistake surfaced (user correction, tool output,
  self-review). Include the specific signal if it was a tool result.
- **Root cause**: Why it happened. Distinguish between (a) missing
  knowledge, (b) skipped verification step, (c) false assumption from
  pattern-matching, (d) workflow gap. Avoid generic "I didn't think
  carefully" — name the specific failure mode.
- **Recurrence of**: If this matches an existing rule, link to the
  prior entry's date. Otherwise write "new".
- **Rule**: A concrete, checkable rule that would have prevented this.
  Phrase as an imperative ("Before X, do Y"). If the rule already
  exists and was violated, the rule should be about *enforcement*
  (e.g. a check to add to a skill, a step to add to AGENTS.md), not a
  restatement of the existing rule.
```

## Anti-patterns to avoid

- **Don't restate the same lesson with new wording.**  If
  you'd write essentially the same rule again, the real fix is to
  make the rule self-enforcing (update a skill or `AGENTS.md`), not
  to add a third entry.
- **Don't let rules go stale.**  When you read prior entries, flag stale 
  tooling references and either update them or note the staleness in your 
  new entry.
- **Don't write rules that depend on you remembering to follow them.**
  If a rule is "remember to do X at the start of every task", it will
  be skipped. Prefer rules that bind to a tool, a skill trigger, or a
  CI check.
- **Don't bury the acknowledgement.** Tell the user up front in the
  response that you got it wrong, before describing the fix.

## Cleanup ritual

Before appending, check the file's length:

- **≥ 10 entries**: pause and propose to the user that one or more
  entries be either (a) deleted (if obsolete or one-off), or (b)
  promoted into the workflow somewhere it will actually be read. If
  the candidate rule is about claims/citations/evidence specifically,
  `docs/ai/evidence_rules.md` is a natural target — otherwise leave
  the choice of destination to the user. Do this *before* adding the 
  new entry, so the file doesn't grow monotonically and stop being read.

This ritual is the only mechanism preventing `ai_learnings.md` from
becoming a write-only graveyard.

## Repo-specific notes

- `docs/ai/ai_learnings.md` is **gitignored**.
  Entries are local to the developer's checkout and will not be seen
  by other agents or in CI. The file is for the human developer to
  improve `AGENTS.md` / skills based on patterns.
- The protocol source and trigger pointer both live in `AGENTS.md`
  §"Mandatory workflow". `GEMINI.md` is a deprecated stub.
- Date format is `YYYY-MM-DD` to match existing entries.
