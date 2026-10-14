# delegate.py reference

`python3 ${CLAUDE_SKILL_DIR}/scripts/delegate.py <command> [options]`

- Every command prints one JSON object on stdout, with `ok: true|false`.
- On failure the object has `error`, plus fields that explain it.
- The exit code is 0 only when `ok` is true.
- It needs Python 3.9+, uses only the standard library, and runs on macOS and Linux.

## Commands

### `preflight --agent cursor|pi [--model M]`

Checks that the agent CLI:
- is on `PATH`;
- has every flag the runner uses;
- has a configured model that the CLI lists;
- (cursor only) is logged in.

`problems` block a run. `warnings` don't. Run it once per agent before `init`.

### `init --name SLUG [--base REF] [--spec POINTER] [--integration-branch B]`

Starts a run. It creates (or reuses) the integration branch `delegate/<slug>/integration` from `--base`. The default base is `origin/HEAD`, or the current branch if that isn't set.

The branch is checked out in its own worktree outside the repo, so your checkout is never touched. `init` returns:
- `run_id`;
- `briefs_dir` and `notes_dir`, where you write briefs and notes;
- `integration_worktree`;
- `base_sha`.

It also installs the guard hook script.

### `dispatch --run ID --ticket N --brief FILE|- [--agent A] [--model M] [--profile isolated|inherit] [--sandbox] [--timeout S] [--ref REF] [--followup]`

Runs one agent session on the ticket:
- The first dispatch of a ticket creates its worktree and branch, `delegate/<slug>/<ticket>`, from the integration tip.
- Without `--followup`, it starts a fresh session that loads the pinned `delegate-tdd` skill.
- With `--followup`, it continues the ticket's last session, which must use the same agent. Use this for "merge the integration tip", "verify failed" or "you left changes uncommitted".

The runner appends operating rules to every brief:
- commit on this branch with `Refs <ref>` in the message;
- never push or rewrite history;
- leave the tree clean;
- finish with one JSON status line.

`--ref` defaults to `#N` when the ticket id is numeric, and to the id itself otherwise.

The runner refuses to start when the ticket is already running, or when `max_parallel` sessions are already running.

Key fields in the result:

| field | meaning |
|---|---|
| `outcome` | what to do next (see below) |
| `report` | the agent's final JSON line: `status`, `summary`, `blocker` |
| `commits` | commits this session made, recorded in the ledger |
| `uncommitted_tracked`, `untracked` | what the agent left in the worktree |
| `cli` | `ok`, `error`, `session_id`, `usage`, and for cursor `rejected_tool_calls` |
| `stopped` | `after_done` (normal for CLIs that linger), `timeout`, `idle_timeout`, or null |
| `attempt_dir` | holds `prompt.md`, `command.json`, `events.jsonl`, `stderr.log`, `result.json` |

### Outcomes

| outcome | meaning | your next move |
|---|---|---|
| `done` | committed, clean tree, reported done | `verify --ticket`, then `land` |
| `blocked` | the agent says it can't proceed | read `report.blocker`; clarify with a follow-up, or ask the user |
| `unreported` | it committed but sent no status line | `verify`; if the work looks complete, land it, otherwise follow up |
| `no_commits` | it finished without committing | follow up |
| `dirty` | tracked changes left uncommitted | follow up: "commit or discard" |
| `history_rewritten` | the branch no longer contains the earlier head | re-dispatch fresh; never land it |
| `cli_failed` | CLI error: auth, model, crash, provider error | read `cli.error` / `stderr_tail`; fix the config or switch agent |
| `timed_out` | total or idle timeout hit | follow up with a narrower brief, or split the ticket |
| `tampered` | the worktree changed since the runner last recorded it, so someone other than an agent wrote there (also returned by `verify` and `land`) | `restore`, report it to the user, then carry on |

Exit code 0 from an agent CLI only means the CLI ran. `outcome` combines the CLI's own completion signal, the agent's report, and git.

### `verify --run ID [--ticket N] [--cmd C ...] [--keep-going]`

Runs the configured `verify` commands in a ticket worktree, or in the integration worktree if no ticket is given. Each command runs with a timeout and keeps its full log.

Read `output_tail`: a green run with zero tests executed is not a pass. Files the build ignores (fixtures, secrets in `.gitignore`) are not in worktrees.

### `land --run ID --ticket N`

Fast-forwards the integration branch to the ticket branch. It refuses when:
- either worktree was tampered with;
- the integration worktree or the ticket worktree has uncommitted tracked changes;
- a pending commit is not in the ledger (`unaudited_commits`): someone other than an agent committed.

It returns `needs_merge` when the integration branch has moved on. In that case, follow up with the agent to merge `<integration branch>` into its branch, then land again. You never merge by hand.

### `restore --run ID [--ticket N]`

Puts a ticket worktree, or the integration worktree if no ticket is given, back to the state the runner last recorded:
- it resets HEAD to the recorded commit;
- it deletes untracked files (ignored files are kept).

The result's `discarded` field shows what was there. Use it only after a `tampered` outcome.

### Tamper checks

After each `init`, `dispatch`, `verify` and `land`, the runner records each worktree's HEAD and its full `git status`, untracked files included. The next `dispatch`, `verify` or `land` on that worktree compares against the record first. Any difference means something other than an agent changed it:
- you;
- a subagent;
- a resumed session without the guard;
- a person.

That stops the run with outcome `tampered`. Without this check, an agent's next commit would sweep those changes in and they would pass the audit.

### `guard-hook`

Prints the always-on guard as a Claude Code settings fragment, under `merge_into_settings`, and installs `guard.py` into the state root.

The hook in SKILL.md is registered only when the skill is invoked. It covers only the main agent of that process: not subagents, and not a session resumed later. The same command in `~/.claude/settings.json` covers all of them. It does nothing unless a run is active.

### `audit --run ID`

Checks that every commit in `base_sha..integration` appears in the ledger. `by_agent_ticket` attributes each commit.

### `status --run ID` / `runs [--active]`

`status` shows a run's state: tickets, sessions, which tickets are running and which have landed. `runs` lists runs.

### `finish --run ID [--delete-branches] [--abort]`

Removes clean, landed ticket worktrees and the integration worktree, and keeps anything else (it lists them in `kept`). It always keeps the integration branch, and marks the run inactive, which switches the guard off. `--abort` only marks the run inactive.

## State layout

Root: `$DELEGATE_SPEC_HOME`, default `~/.local/state/delegate-spec`.

```
guard.py                      copied by init; the hook in SKILL.md runs it
runs/<run_id>/
  run.json                    run + ticket state (locked on update)
  ledger.jsonl                one line per agent session that made commits
  briefs/ notes/              yours to write
  wt/_integration  wt/<ticket> worktrees (guarded while the run is active)
  tickets/<ticket>/attempt-N/ prompt, command, events, stderr, result
  agents/cursor-config/       isolated CURSOR_CONFIG_DIR (chats live here)
  agents/pi-sessions/         pi session files
  verify/                     verify logs
```

## Environment variables

| variable | effect |
|---|---|
| `DELEGATE_SPEC_HOME` | state root |
| `DELEGATE_SPEC_CONFIG` | user config path; default `$XDG_CONFIG_HOME/delegate-spec/config.json` |
| `DELEGATE_CURSOR_BIN`, `DELEGATE_PI_BIN` | agent binaries; default `cursor-agent`, `pi` |
