# Configuration

There are two JSON files. The user file is merged over the repo file.

| file | owner | holds |
|---|---|---|
| `docs/agents/delegate-spec.json` in the repo | the team, committed | `verify`, `default_agent`, `max_parallel`, `branch_prefix`, `open_pr` |
| `~/.config/delegate-spec/config.json` (or `$DELEGATE_SPEC_CONFIG`) | each user, never committed | `agents.*`: models and profiles, which depend on that user's accounts and machine |

Models live in the user file because each machine has different providers, accounts and CLI logins. Don't commit a model to the repo unless the team really shares one.

```json
{
  "default_agent": "pi",
  "max_parallel": 2,
  "verify": ["go build ./...", "go test ./..."],
  "verify_timeout_seconds": 1800,
  "branch_prefix": "delegate/",
  "open_pr": false,
  "agents": {
    "pi":     { "model": "<provider>/<model-id>[:<thinking>]", "profile": "isolated" },
    "cursor": { "model": "<cursor model id>", "profile": "isolated", "sandbox": false }
  }
}
```

| key | default | notes |
|---|---|---|
| `default_agent` | none | used when `dispatch` has no `--agent` and the ticket has no agent yet |
| `max_parallel` | 2 | concurrent agent sessions per run; keep it low for small accounts and machines |
| `verify` | `[]` | shell commands run in the worktree; required before anything can land |
| `verify_timeout_seconds` | 1800 | per command |
| `branch_prefix` | `delegate/` | branches become `<prefix><slug>/integration` and `<prefix><slug>/<ticket>` |
| `open_pr` | false | if true, push the integration branch and open a PR at the end (step 9) |
| `agents.<a>.model` | none, **required** | check it against `cursor-agent models` / `pi --list-models` |
| `agents.<a>.profile` | `isolated` | see below |
| `agents.<a>.timeout_seconds` | 3600 | total wall clock per session |
| `agents.<a>.idle_timeout_seconds` | 900 | no output for this long counts as hung |
| `agents.cursor.sandbox` | false | true runs shell commands in Cursor's sandbox (no `--force`) |
| `agents.<a>.bin` | `cursor-agent` / `pi` | the `DELEGATE_<AGENT>_BIN` env var wins |

## Profiles: how much of the user's own setup the agent sees

Agent CLIs load whatever their user has installed: extensions, MCP servers, personal rules, global skills, hooks, and an approval mode. That varies on every machine. It can slow runs down, change behaviour, or send ticket content to third-party services, such as a memory extension that uploads every turn.

- **`isolated`** (default). This is a best-effort clean room.
  - **pi** runs with `--offline --no-extensions --no-skills --no-prompt-templates --no-approve`. Only the pinned `delegate-tdd` skill and the project's `AGENTS.md`/`CLAUDE.md` load.
  - **cursor** gets its own `CURSOR_CONFIG_DIR` per run, so the user's `cli-config.json` approval mode and default model don't apply, and the run passes explicit approval flags. What isolation can't remove:
    - Cursor still starts the user's global MCP servers from `~/.cursor/mcp.json`, and there is no flag to stop it. It also still applies user rules and skills from `~/.cursor`, `~/.agents` and `~/.claude`.
    - For a fully clean room, run the orchestrator with `HOME` pointing at an empty directory and authenticate with `CURSOR_API_KEY`.
    - Isolation keeps login only when credentials live in the OS keychain or `CURSOR_API_KEY`; `preflight` checks this.
- **`inherit`** runs the CLI exactly as the user has it configured. Use it when tickets genuinely need the user's MCP servers or extensions.

In both profiles the project's own `AGENTS.md`, `CLAUDE.md` and `.cursor/rules` load. They are the repo's conventions, and agents should follow them.

## Cursor sandbox

`sandbox: true` drops `--force`. Shell commands then run in Cursor's sandbox, with read-write access to the worktree only. Network access is limited to Cursor's default allowlist, so private registries and intranet hosts are blocked.

Two ways to cope with that:
- warm dependency caches before the run;
- leave the sandbox off for repos that fetch private dependencies during tests.

Without the sandbox, `--force` lets the agent run any command. Treat the worktree as a fence for git, not a security boundary.

## Things that differ per environment

`preflight` checks the first three; the rest are on you.

- **CLI version and flags.** If a flag the runner uses is missing, `preflight` fails. Upgrade the CLI.
- **Auth.**
  - cursor: keychain login, `CURSOR_API_KEY`, or `AGENT_CLI_CREDENTIAL_STORE=file`.
  - pi: provider env vars, its `auth.json`, or OAuth. Pi has no reliable auth check, so the first dispatch surfaces auth errors as `cli_failed`.
- **Models.** Model names are account-specific; nothing is hard-coded.
- **The Claude Code sandbox.** If your Claude Code session runs Bash in a sandbox, the agent CLIs need network access to their providers and write access to their state directories (`~/.cursor`, `~/.pi`) and to `$DELEGATE_SPEC_HOME`.
- **Platform.** macOS and Linux are supported. Windows isn't: the runner uses process groups and `fcntl`.
