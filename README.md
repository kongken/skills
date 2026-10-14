# skills

Agent skills by kongken.

| skill | purpose |
|---|---|
| [delegate-spec](skills/delegate-spec/SKILL.md) | Implement a spec's tickets through external Cursor/Pi agents in separate worktrees. The invoking session orchestrates, verifies and reviews. |

## Prerequisites

`delegate-spec` extends [mattpocock/skills](https://github.com/mattpocock/skills)' `implement-spec` workflow; it does not replace planning/setup.

| dependency | when needed |
|---|---|
| `setup-matt-pocock-skills` | Once per target repo; provides `docs/agents/issue-tracker.md`. |
| `to-spec` → `to-tickets` | Before delegation; provides the spec, acceptance criteria and blocker graph. Existing compatible outputs can be reused. |
| `code-review` | Optional review instructions; otherwise review the integration diff directly. |
| `tdd` | Already bundled as a [condensed, pinned MIT adaptation](skills/delegate-spec/vendor/README.md); no separate installation for agents. |

Runtime: Python 3.9+, git, macOS/Linux, and at least one authenticated `cursor-agent` or `pi` CLI. Models must be configured explicitly.

## Install and run

```bash
npx skills@latest add mattpocock/skills --skill setup-matt-pocock-skills to-spec to-tickets
npx skills@latest add kongken/skills --skill delegate-spec
```

Or copy `skills/delegate-spec/` into `~/.claude/skills/` (including scripts, references and vendor files), then install the upstream preparation skills separately.

In the target repo:

1. Run `/setup-matt-pocock-skills` once, then `/to-spec` and `/to-tickets` to prepare work.
2. Configure agent models and verification commands; see [config](skills/delegate-spec/references/config.md).
3. Invoke `/delegate-spec <spec URL or path>`. It reads the prepared graph; it does not rerun the preparation skills.

## Token budget and boundaries

- The main skill contains orchestration only; config, tracker fallbacks and recovery details load on demand.
- Briefs share accessible pointers; follow-ups send deltas. Each fresh agent loads the compact vendored TDD instructions, not the upstream orchestration stack.
- Runner stdout is compact JSON. Successful dispatches return an actionable summary; full diagnostics remain in `attempt_dir/result.json`. Add `--verbose` to a command for full inline output.
- Guards, content fingerprints and a per-ticket ledger catch workflow mistakes; they are not a sandbox against malicious agents or concurrent writers. Cursor's global MCP servers can still start in isolated mode; see [isolation limits](skills/delegate-spec/references/config.md#profiles-how-much-of-the-users-own-setup-the-agent-sees).

## Develop

```bash
python3 -m unittest discover -s skills/delegate-spec/tests -v
```

Tests use fake agent CLIs and real Git worktrees; they do not call models.
