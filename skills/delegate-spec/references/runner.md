# Runner contract and recovery

`python3 ${CLAUDE_SKILL_DIR}/scripts/delegate.py <command> --help` is authoritative for flags. Stdlib only, Python 3.9+, macOS/Linux.

Every command prints one JSON object; `ok` determines exit code (0/1). Default stdout is compact; `--verbose` includes full dispatch diagnostics and status snapshots. Dispatch always saves the complete result to `attempt_dir/result.json`; inspect logs by path instead of replaying transcripts into context.

## Commands

| command | contract |
|---|---|
| `preflight --agent cursor|pi` | Check CLI flags, configured model listing and Cursor auth. Resolve `problems`; inspect `warnings`. Pi auth failures surface on dispatch. |
| `init --name SLUG --base REF --spec POINTER` | Create a new integration branch and external worktree; return run ID, base SHA, branch and brief/notes directories. Default base is `origin/HEAD`, else current branch. Existing integration branches are rejected: resume the old run ID or choose a new name/branch. |
| `dispatch --run ID --ticket N --brief FILE` | Create the ticket worktree from integration, then run the chosen agent. Fresh sessions load bundled `delegate-tdd`; `--followup` requires the ticket's previous session and same agent. Use `--brief -` for stdin. |
| `verify --run ID [--ticket N]` | Run configured commands on the ticket or integration. `--cmd` overrides commands. Full logs and tails are returned; zero tests executed is not a pass. |
| `land --run ID --ticket N` | Fast-forward integration only. Requires clean tracked files, untampered worktrees, successful verification of this ticket's current HEAD and ledgered pending commits owned by this ticket. |
| `restore --run ID [--ticket N]` | Reset the target to recorded HEAD and delete non-ignored untracked files. Destructive: use after `tampered`, then re-dispatch unfinished work and re-verify. It does not reconstruct earlier dirty contents. |
| `audit --run ID` | Check every commit in `base_sha..integration` against the ledger; report attribution by agent/ticket. |
| `status --run ID`, `runs [--active]` | Read state. Use the same run ID to continue; initialization is not resumption. |
| `finish --run ID` | Remove clean landed worktrees, keep unfinished/dirty ones, retain integration branch, mark inactive. `--abort` only deactivates; `--delete-branches` also attempts deletion of landed ticket branches. |
| `guard-hook` | Install guard.py and print the settings fragment covering subagents and resumed sessions. Never writes user settings. |

Dispatch rejects already-running tickets and enforces `max_parallel`. It appends branch, commit-reference, clean-tree and final-status rules to the brief. Agent exit 0 alone is insufficient: completion events, report and Git state determine the outcome.

## Outcomes

| outcome | next action |
|---|---|
| `done` | Agent committed, left no tracked/untracked changes and reported done. Verify ticket, then land. |
| `unreported` | Commits exist but no valid status. Inspect acceptance criteria, then verify/land or follow up. |
| `blocked` | Read `report.blocker`; clarify with a follow-up or ask the user. |
| `dirty` | Follow up to commit ticket work and discard scratch files, including untracked files. |
| `no_commits` | Follow up or investigate why nothing was committed. |
| `cli_failed` | Read `cli.error` and stderr log; repair config or dispatch fresh with another agent. See [agent notes](agents.md). |
| `timed_out` | Total/idle timeout; follow up with a narrower brief or split the ticket. |
| `history_rewritten` | Never land; report and arrange recovery through an agent. |
| `needs_merge` | Follow up: merge integration into the ticket branch, resolve conflicts and commit; re-verify. |
| `not_verified` | Verify current ticket HEAD; failed verification and new dispatches invalidate earlier success. |
| `foreign_commits` | Another unlanded ticket's commits, or existing unrelated commits, were imported. Preserve original ownership; land prerequisites through their own tickets or ask an agent to repair the branch. |
| `unaudited_commits` | Pending commits have no ledger provenance. Stop and investigate; do not bypass the ledger. |
| `tampered` | Unexpected HEAD, status, index, tracked/untracked content change. Restore target and report; re-dispatch unfinished work. |
| `branch_exists` | Choose a new integration branch/name, or resume the original run ID. |

Verification records expected build artifacts, including untracked files; later content edits still count as tampering. Ignored files are outside snapshots. Agent worktrees contain committed files only: ignored fixtures/secrets may need environment setup.

## State

Root: `$DELEGATE_SPEC_HOME`, default `~/.local/state/delegate-spec`.

```
guard.py
runs/<id>/
  run.json, ledger.jsonl       locked state updates and per-session commit ownership
  briefs/, notes/             orchestrator context pointers
  wt/_integration, wt/<ticket> guarded worktrees
  tickets/<ticket>/attempt-N/ prompt.md, command.json, events.jsonl, stderr.log, result.json
  agents/                     isolated Cursor config and Pi sessions
  verify/                     command logs
```

Snapshots fingerprint staged/unstaged diffs and non-ignored untracked content. The ledger excludes commits already reachable before dispatch, preserves the first owner and refuses to land another unlanded ticket's work. These checks cover workflow accidents, not adversarial access to shared Git/state or writes during active agent/verify processes.

Config override: `$DELEGATE_SPEC_CONFIG`; default `$XDG_CONFIG_HOME/delegate-spec/config.json` (XDG defaults to `~/.config`). Agent binary overrides: `$DELEGATE_CURSOR_BIN`, `$DELEGATE_PI_BIN`.
