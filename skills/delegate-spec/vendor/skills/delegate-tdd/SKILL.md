---
name: delegate-tdd
description: Test-first implementation of one delegated ticket in an unattended worktree.
---

<!-- Condensed from mattpocock/skills skills/engineering/tdd @ 49dd158 (MIT). See ../../README.md for provenance and adaptations. -->

# Delegated TDD

Implement the ticket's acceptance criteria with **red → green**, one behavior at a time. The orchestrator reviews afterwards; keep unrelated refactoring out of this loop.

## Context and seams

Read the ticket, relevant spec sections, project glossary (`CONTEXT.md` or `GLOSSARY.md`) and applicable ADRs. Reuse shared notes instead of rediscovering decisions.

A **seam** is the public interface where behavior is observed. The brief's seams are already confirmed: test there, without asking again. If none are supplied, choose the highest public seam covering the ticket and name it in your final summary. External design/review skills are optional; continue without them.

Nobody can answer questions in this session. If acceptance criteria are contradictory, essential information is missing, or the environment is broken, report blocked rather than guessing.

## Loop

1. Choose the next acceptance behavior; write one test at its seam.
2. Run it and observe failure for the intended reason (not a broken fixture/setup).
3. Implement only enough to pass; run the relevant tests.
4. Repeat for the next behavior, then run the brief's verification commands.

Tests assert observable outcomes against independently known expectations, not internal calls or implementation-derived snapshots. Prefer integration-style tests; mock external boundaries only. Avoid writing all tests before implementation. For examples only when needed: [tests](tests.md), [mocking](mocking.md).
