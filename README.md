# skills

Agent skills by kongken.

| skill | what it does |
|---|---|
| [delegate-spec](skills/delegate-spec/SKILL.md) | Implement the tickets of a spec (from mattpocock's `/to-spec` + `/to-tickets`) by delegating every ticket to external coding-agent CLIs (cursor-agent, pi) in isolated git worktrees. The invoking session only orchestrates, verifies, lands and reviews; a guard hook and a commit audit make sure it never writes the code itself. |

## Install

```bash
npx skills@latest add kongken/skills --skill delegate-spec
```

Or copy `skills/delegate-spec/` into `~/.claude/skills/`.

`delegate-spec` builds on [mattpocock/skills](https://github.com/mattpocock/skills): run `/setup-matt-pocock-skills`, `/to-spec` and `/to-tickets` first. It needs Python 3.9+, git, and at least one of `cursor-agent` / `pi` installed and logged in.

## Develop

```bash
python3 -m unittest discover -s skills/delegate-spec/tests -v
```

The tests drive the runner against fake agent CLIs; they don't call any model.
