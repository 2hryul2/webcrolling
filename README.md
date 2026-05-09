# claude_webcroll

Web crawler and content collection tool built with Three Man Team methodology.

---

## Quick Start

### For the Project Owner

Tell the Architect what you need. Architect will:
1. Read your request
2. Plan the approach
3. Brief the Builder
4. Review what Builder ships
5. Deploy with your sign-off

### For Architect

Start with:
```
You are the Architect on this project. Please read your role file (agents/ARCHITECT.md).
Then read handoff/BUILD-LOG.md and handoff/ARCHITECT-BRIEF.md.
```

---

## The Process

Every piece of work follows the same path:

```
Project Owner → Architect (plan) → Builder (build) → Reviewer (review) → Architect (deploy)
```

- **Architect** writes the brief (`ARCHITECT-BRIEF.md`)
- **Builder** reads the brief, builds, writes `REVIEW-REQUEST.md`
- **Reviewer** reviews and writes `REVIEW-FEEDBACK.md`
- **Architect** deploys when everything clears

Nothing ships without Architect's sign-off and Project Owner's approval.

---

## Key Files

- `CLAUDE.md` — Project guidelines and token rules
- `handoff/BUILD-LOG.md` — Progress record
- `handoff/ARCHITECT-BRIEF.md` — Current work spec
- `agents/ARCHITECT.md` — Architect role and responsibilities
- `agents/BUILDER.md` — Builder role and responsibilities
- `agents/REVIEWER.md` — Reviewer role and responsibilities

---

## Token Optimization

Every session starts with five rules baked into CLAUDE.md:

1. Trust skills and memory — skip the file read
2. No speculation — every tool call needs a purpose
3. Parallelize when possible
4. Route large outputs to subagents
5. Never restate what the user said

---

## License

MIT
