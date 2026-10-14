# Writing a brief

The brief is the only thing the external agent gets from you. It runs unattended in a fresh worktree and has its own context. It can read the repo, run commands, and fetch URLs if its environment allows.

Good briefs **point** at things rather than paste them: the spec, the ticket, the notes, the commits. Paste a fragment only when the agent couldn't reach the source, for example a tracker that needs auth it doesn't have.

The runner adds the operating rules itself: commit with `Refs <ref>`, no pushing, a clean tree, the final JSON status line. Don't repeat them.

## First dispatch of a ticket

```markdown
# Ticket <ref>: <title>

**Ticket:** <URL or path to the ticket>. Read it in full; its acceptance criteria are the definition of done.
**Spec:** <URL or path to the parent spec>. Read the parts this ticket touches.

## Seams (confirmed, do not ask)
- <seam>: <what it catches / what it misses>

## Context
- Notes: <notes_dir>/<file>.md: <one line on what's in it>
- Builds on: <tickets already landed that this one depends on, with their commit shas>
- Names fixed for this spec: <shared types, functions, files decided during exploration, if any>
- Glossary / ADRs: <paths, if the repo has them>

## Done means
- The acceptance criteria in the ticket hold.
- Tests at the seams above pass.
- `<verify command>` passes.
```

If the ticket body is only available to you (for example a private tracker), paste its **What to build** and **Acceptance criteria** sections under a `## Ticket text` heading instead of the link.

## Follow-ups (`dispatch --followup`)

Short, specific, and pointing at evidence:

- **Integration moved:** `The integration branch \`<branch>\` has new commits. Merge it into your branch, resolve any conflicts, re-run the tests, and commit the merge.`
- **Verify failed:** `\`<command>\` fails in your worktree. Full log: <log path>. Fix the cause (don't weaken or skip the tests) and commit.`
- **Left changes uncommitted:** `You left uncommitted changes in: <files>. Commit what belongs to the ticket and discard the rest.`
- **Blocked, now unblocked:** `<answer to report.blocker>. Continue the ticket.`

## Review fixes (ticket `review-fix`)

Dispatch them as a fresh ticket. Its worktree starts at the integration tip:

```markdown
# Review fixes for <spec ref>

Code review of `<base_sha>..<integration branch>` found the issues below. Fix each one, keep all tests green, and don't change behaviour beyond what each item asks.

1. <file:line>: <problem> → <expected change>
2. ...

Spec: <pointer>. Seams: <same list as the tickets>.
```
