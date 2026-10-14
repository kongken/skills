---
name: delegate-spec
description: Implement the result of /to-spec and /to-tickets by delegating every ticket to external coding-agent CLIs (cursor-agent, pi) in isolated git worktrees, while this session only orchestrates, verifies, lands and reviews. Use when the user wants a spec or its tickets implemented through other agents.
disable-model-invocation: true
argument-hint: "<spec issue, URL or path>"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/delegate.py *)
hooks:
  PreToolUse:
    - matcher: "Edit|Write|MultiEdit|NotebookEdit|Bash"
      hooks:
        - type: command
          command: 'd="$DELEGATE_SPEC_HOME"; [ -n "$d" ] || d="$HOME/.local/state/delegate-spec"; if [ -f "$d/guard.py" ]; then exec python3 "$d/guard.py"; fi'
---

You have been provided a spec. It should have tickets, produced by `/to-tickets`, describing how to implement it. The issue tracker should have been provided to you in `docs/agents/issue-tracker.md`; if not, tell the user to run `/setup-matt-pocock-skills` and stop.

The goal is the entire spec implemented on a single **integration branch**, every ticket resolved the way the issue tracker closes work, and **every line of code written by an external agent**.

## The rule

You orchestrate; external agent CLIs develop. You may read code, run verify commands, review, drive git through `delegate.py`, and comment on or close tickets. You may not create, edit or delete files in the repo or in any run worktree, commit, resolve a conflict, or "just fix" anything: not a typo, and not when an agent keeps failing. The only files you write yourself are the ones these steps name: config, briefs, notes, and local tracker files. Anything that changes code becomes a **brief** for an agent. The same goes for any subagent you spawn: use subagents only for reading and review.

Three layers enforce this:

1. **A guard hook.** While a run is active, it denies writes under the run's worktrees. The hook this skill registers covers only your own tool calls, in this process. It does not cover subagents, and it does not survive a resumed session. Step 1 offers the user an always-on version that does.
2. **Tamper checks in the runner.** It records each worktree's state after every step. If anything else changes a worktree in between, `dispatch`, `verify` and `land` stop with outcome `tampered`. Then run `restore` and tell the user. Never repair it by hand.
3. **The audit.** `land` only fast-forwards commits that an agent run produced, and at the end `audit` proves it for the whole integration branch.

## The runner

`python3 ${CLAUDE_SKILL_DIR}/scripts/delegate.py <command>`. Every command prints one JSON object. The commands, outcomes and state layout are in [references/runner.md](references/runner.md). When an agent misbehaves, [references/agents.md](references/agents.md) explains how each CLI is driven.

Talk to agents through **context pointers**: links or paths to the spec, ticket, notes and commits. Don't copy what a pointer already gives them.

## Steps

1. **Configure.** Read the repo config `docs/agents/delegate-spec.json` and the user config `~/.config/delegate-spec/config.json`; [references/config.md](references/config.md) has the format. You need:
   - which agents to use, with a model for each;
   - the verify commands.

   Ask the user for anything missing and write it to the right file. Then run `preflight --agent <name>` for every agent you will use, and resolve or report every problem before going on.

   If the user's Claude Code settings (`~/.claude/settings.json`) don't have the delegate-spec guard yet, show them the output of `guard-hook` and recommend adding it. Don't edit their settings yourself.

2. **Read the task graph.** Use the per-tracker recipes in [references/trackers.md](references/trackers.md) to fetch:
   - the spec;
   - its tickets, excluding the spec itself;
   - each ticket's blockers.

   Collect the **seams** the spec agreed for testing. The agents can't ask the user, so every brief states its seams as already confirmed.

3. **Explore (optional).** Read whatever the tickets need and save notes under the run's `notes_dir`, so briefs can point at them instead of each agent rediscovering them. Fix shared names here when tickets touch the same code.

4. **Start the run.** Run `init --name <spec-slug> --base <branch> --spec <pointer>`. It creates the integration branch and its worktree outside the repo, and returns the run id and directories.

5. **Work the frontier.** A ticket is ready when every blocker has **landed** on the integration branch. Track this yourself; the tracker's open/closed state lags during a run. For each ready ticket, up to `max_parallel` at a time:
   - write a brief to `<briefs_dir>/<ticket>.md` from [references/brief.md](references/brief.md);
   - run `dispatch --run <id> --ticket <n> --brief <file> [--agent <name>] [--ref '#<n>']` in the background where your harness can.

6. **When a dispatch returns**, act on its `outcome`:
   - `done`: run `verify --ticket <n>`, then `land --ticket <n>`.
   - `needs_merge` (from land), or a failing verify: write a short follow-up brief and run `dispatch --followup`.
   - `tampered`: run `restore` and report it to the user.
   - Any other outcome: follow up, re-dispatch (another agent is fine), or stop and ask the user. **Never take over.**

   After each landing:
   - run `verify` on the integration branch, and turn a failure into a fix ticket for an agent;
   - dispatch every ticket the landing unblocked.

7. **Review once**, after every ticket has landed. Review `base_sha..<integration branch>`, using the `code-review` skill if you have it. Put all findings into one brief, dispatch it as ticket `review-fix`, then verify and land it. Do focused checks only after that; there is no second review round.

8. **Audit.** `audit` must pass. If it fails, stop and report the unaudited commits.

9. **Resolve tickets.** Close each ticket the way the tracker closes work (see trackers.md), citing the integration branch and that ticket's commits. Push or open a PR only when `open_pr` is set or the user asks.

10. **Finish.** Run `finish --run <id>`, then report:
    - the integration branch;
    - for each ticket, the agent that did it and its commits;
    - the verify results;
    - anything still open.
