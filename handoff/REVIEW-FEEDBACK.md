# Review Feedback

Reviewer writes here after reviewing Builder's work.

---

## Template

```
# Review Feedback — Step N

Date: [date]
Status: APPROVED / APPROVED WITH CONDITIONS / REJECTED

## Conditions

[Every item here blocks the merge]
- `file:line` — [What is wrong] — [How to fix it]
- `file:line` — [What is wrong] — [How to fix it]

## Escalate to Architect

[Requires product or business decision]
- [What the question is] — [Why you cannot resolve it at code level]

## Cleared

[One sentence: what was reviewed and passed]
```

---

## Status Meanings

- **APPROVED** — ships as-is
- **APPROVED WITH CONDITIONS** — Builder fixes and re-submits
- **REJECTED** — fundamental problem; Builder re-architects with Architect

---

## How This Works

Reviewer:
1. Read the diff first (`git diff main..HEAD`)
2. Read REVIEW-REQUEST.md
3. Read the files Builder changed
4. Write this file

Builder:
1. If APPROVED → signal Architect
2. If APPROVED WITH CONDITIONS → fix and re-submit
3. If REJECTED → escalate to Architect

---

(Awaiting first review)
