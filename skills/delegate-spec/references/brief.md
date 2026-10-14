# Briefs

Load for the first ticket. Point to sources the agent can actually read from its worktree: absolute local paths, authenticated URLs, shared notes and commits. Read only the relevant sections. Inline acceptance criteria only when the source is inaccessible (e.g. a private tracker without agent auth).

The runner supplies branch/commit rules and final JSON format; do not repeat them.

## First dispatch

```markdown
# <ticket ref>: <title>
Ticket: <pointer>; its acceptance criteria define done.
Spec: <pointer>; read the sections this ticket touches.
Seams (confirmed): <public boundaries and what each catches/misses>.
Context: <shared notes, landed prerequisites/SHAs, fixed names or ADRs; omit empty fields>.
Verify: <commands>.
```

## Follow-up (`dispatch --followup`)

Send only the delta and evidence; the session already has the ticket:
- Integration moved: `Merge <integration branch> into your branch, resolve conflicts, test and commit.`
- Verify failed: `<command> failed. Log: <path>. Fix the cause without weakening tests; commit.`
- Dirty tree: `<paths> remain. Commit ticket work and discard scratch files.`
- Blocked: `<answer>. Continue.`

## Review fix (`--ticket review-fix`, fresh dispatch)

Point to spec/seams and the reviewed commit range. List each finding as `file:line — problem → expected change`; fix all findings in one dispatch. Omit another ticket brief or full diff when the agent can read it.
