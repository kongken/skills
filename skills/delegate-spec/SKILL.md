---
name: delegate-spec
description: Implement mattpocock /to-spec + /to-tickets output through external Cursor or Pi agents; this session orchestrates only.
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

# Delegate spec

Input: a spec and ticket graph from mattpocock's `/to-spec` and `/to-tickets`. Read `docs/agents/issue-tracker.md`; if missing, stop and ask the user to run `/setup-matt-pocock-skills`. These are upstream preparation dependencies, not skills to reload during execution. The runner bundles its TDD skill; external agents need no global mattpocock skills.

## Boundary

You orchestrate and review; external CLIs change code. Write only config, briefs, notes and local tracker files. Use `delegate.py` for run/git operations. Code fixes, conflicts and review findings always become agent briefs. Reading/review subagents obey the same boundary.

The hook covers this process's main agent only. Recommend the settings fragment from `guard-hook` for subagents/resumed sessions; never edit user settings. The runner also fingerprints worktrees and checks commit provenance; these are workflow checks, not a sandbox.

## Workflow

Runner: `python3 ${CLAUDE_SKILL_DIR}/scripts/delegate.py`. Use `<command> --help` for flags. Read references only at their triggers below; keep normal results sparse (`--verbose` exposes diagnostics).

1. **Prepare.** Read repo `docs/agents/delegate-spec.json` and user `~/.config/delegate-spec/config.json` (environment overrides: [config](references/config.md)). Ask for missing models/verify commands, then run `preflight --agent <name>` for each agent; resolve every problem. Read config details only when configuring or troubleshooting isolation.

2. **Graph.** Fetch the spec, tickets and blockers through the configured tracker. Use the relevant [tracker recipe](references/trackers.md) only if reads are unspecified. Exclude the spec itself; confirm ambiguous edges and testing seams with the user. Show tickets, blockers and the ready set. Read the graph once: readiness thereafter means all blockers have **landed**, not merely closed.

3. **Start.** `init --name <slug> --base <ref> --spec <pointer>` returns the integration branch and brief/notes directories. Resume existing work with its run ID; use a new name for a new run. Save shared exploration once in `notes_dir` when useful.

4. **Delegate.** At most `max_parallel` ready tickets at once: write a [brief](references/brief.md), then `dispatch --run <id> --ticket <n> --brief <file> [--agent <name>]`, in the background where supported. Share accessible pointers, not pasted context; the runner adds operating rules.
   - `done`/`unreported`: inspect completion, `verify --ticket <n>`, then `land --ticket <n>` (each with `--run <id>`).
   - `needs_merge` or failed verify: brief the fix and `dispatch --followup`; verify the new head before landing.
   - `tampered`: `restore` that target and report it; re-dispatch unfinished work.
   - Other outcomes: consult [runner recovery](references/runner.md); CLI failures: [agent notes](references/agents.md). Follow up, switch agents with a fresh dispatch, or stop—never take over.
   After each landing, verify integration, delegate failures, then dispatch the newly ready frontier.

5. **Conclude.** Once all tickets land and integration verifies, review `base_sha..<integration branch>` once (use available `code-review` instructions). Delegate any findings as `review-fix`; verify, land and re-verify integration. Require `audit` to pass, then resolve tickets per tracker with branch/commit evidence. Push/open a PR only if `open_pr` or the user authorizes it. `finish`; report the branch, ticket→agent/commits, verification and anything left open.
