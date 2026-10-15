# How each agent CLI is driven

Read this when a dispatch misbehaves. `attempt-N/command.json` shows the exact argv that was used, with the prompt replaced by a placeholder.

## Common

- **Isolation.** The agent runs in the ticket worktree, in its own process group. On exit, the runner kills anything left in the group, such as MCP servers the CLI started.
- **Git and stdin.** `GIT_EDITOR=true` and `GIT_TERMINAL_PROMPT=0` are set so git never waits for input. Stdin is `/dev/null`.
- **Timeouts.** The total timeout and the idle timeout (no output) come from config.
- **Completion.** Each CLI has its own completion event. After that event, the runner gives the CLI a few seconds to exit, then terminates it. Some versions keep running after they finish.
- **The skill.** The prompt's first token invokes the pinned `delegate-tdd` skill shipped in `vendor/`. Follow-ups don't.

## cursor-agent

```
CURSOR_CONFIG_DIR=<run>/agents/cursor-config        # isolated profile only
cursor-agent create-chat                             # session id, known before the run starts
cursor-agent -p --output-format stream-json --trust --workspace <wt> --model <m> \
  --resume <chat-id> --plugin-dir <skill>/vendor (--force | --sandbox enabled) "/delegate-tdd <brief>"
```

- **Completion:** the first `{"type":"result"}` line. It counts as success only when `subtype == "success"` and `is_error` is false.
- **Final message:** the last `assistant` event without `model_call_id` / `timestamp_ms`. The `result` text concatenates every assistant segment, so it isn't used.
- **The process exits 0 even when the agent's commands were rejected or failed.** `cli.rejected_tool_calls` counts the rejections; git and verify decide the rest.
- **CLI errors:** bad model, bad key or an untrusted workspace exit 1 with empty stdout. The reason is in `stderr.log`.
- **Plain-text lines** may appear in stdout and are ignored. So are event types the runner doesn't know (e.g. `thinking`).
- **`--force`** means "run everything". Without it, the approval mode comes from the config dir, which defaults to allowlist in an isolated dir. File edits are applied either way; only shell commands are gated.
- **Startup** can take tens of seconds while Cursor connects the user's global MCP servers. Set `idle_timeout_seconds` with that in mind.
- **Background process.** Cursor leaves a `worker-server` process per workspace, which exits by itself after a few minutes.
- **Commit trailer.** Cursor adds `Co-authored-by: Cursor <cursoragent@cursor.com>` to commits by default.
- **Transcripts:** `~/.cursor/projects/<slug>/agent-transcripts/<chat-id>/`.

## pi

```
cd <wt>
pi --mode json --model <provider/id[:thinking]> --session-dir <run>/agents/pi-sessions \
   --session-id <ticket>-a<N> --skill <skill>/vendor/skills/delegate-tdd \
   [--offline --no-extensions --no-skills --no-prompt-templates --no-approve]   # isolated
   "/skill:delegate-tdd <brief>"  < /dev/null
```

- **Completion:** `{"type":"agent_settled"}`. It counts as success only when `aborted` is false and the last assistant `message_end` has `stopReason == "stop"`.
- **A provider or auth failure still exits 0 in json mode.** It shows up as `stopReason: "error"` plus `errorMessage`, which the runner reports as `cli_failed`.
- **Stdin.** Pi waits for stdin to close in print/json mode, which is why the runner gives it `/dev/null`.
- **The skill invocation must be the very start of the message.** Anything prepended disables it.
- **Permissions.** Pi has no tool-approval system: every tool call runs. `--no-approve` only stops it trusting project-local `.pi/` extensions and `.agents/skills`.
- **Sessions:**
  - Sessions are bound to the worktree's path.
  - A follow-up reuses the ticket's session id from the same worktree, so don't delete or move a worktree while its ticket may still need follow-ups.
  - Session files have no lock; the runner never runs two sessions on one ticket.
- **Bash timeout.** Pi's bash tool has no default timeout; the runner's idle timeout is the backstop.
