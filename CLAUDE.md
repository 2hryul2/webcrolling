# claude_webcroll — Web Crawler & Content Collection

## Token Rules — Always Active

```
Is this in a skill or memory?   → Trust it. Skip the file read.
Is this speculative?            → Kill the tool call.
Can calls run in parallel?      → Parallelize them.
Output > 20 lines you won't use → Route to subagent.
About to restate what user said → Delete it.
```

Grep before Read. Never read a whole file to find one thing.
Do not re-read files already in context this session.

---

## Session Start — Every Role

1. Load your token-optimizer skill if you have one — first, before anything else.
2. Check `handoff/SESSION-CHECKPOINT.md` — if active and recent, read it. That is your state.
3. Load your role file:
   - `agents/ARCHITECT.md` (Architect)
   - `agents/BUILDER.md` (Builder)
   - `agents/REVIEWER.md` (Reviewer)
4. If no checkpoint — Architect reads `handoff/BUILD-LOG.md` + `handoff/ARCHITECT-BRIEF.md` only.

---

## Reference Files — On Demand Only

| File | Load when |
|---|---|
| handoff/ARCHITECT-BRIEF.md | Builder needs current step; Architect needs to update it |
| handoff/BUILD-LOG.md | Architect checks status |
| handoff/REVIEW-REQUEST.md | Reviewer loads at review start |
| handoff/REVIEW-FEEDBACK.md | Builder loads after Reviewer signals done |

---

## Handoff Files

All team communication flows through files in `handoff/`:
- `ARCHITECT-BRIEF.md` — Architect writes, Builder reads
- `REVIEW-REQUEST.md` — Builder writes, Reviewer reads
- `REVIEW-FEEDBACK.md` — Reviewer writes, Builder reads
- `BUILD-LOG.md` — shared record, Architect owns
- `SESSION-CHECKPOINT.md` — Architect writes at session end

---

## Team

- **Architect** — Plans, briefs, reviews, and deploys
- **Builder** — Builds exactly what the brief says
- **Reviewer** — Ensures quality, security, and spec compliance
- **Project Owner** — Defines requirements and approves deploys

---

## Important

This project uses Three Man Team methodology. See README.md for overview.
