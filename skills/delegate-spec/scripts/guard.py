#!/usr/bin/env python3
"""PreToolUse hook: stop the orchestrator from changing code during a delegate-spec run.

Claude Code runs this before Edit / Write / NotebookEdit / Bash calls once the
delegate-spec skill has been invoked. While any run is active, it denies:

- file-tool writes under that run's worktree root, and
- Bash commands that look like they write, when they run in or point at that root.

Every code change in a run must come from an external agent (`delegate.py dispatch`).
This guard is best-effort for Bash; runner fingerprints and provenance checks
are the backstop for workflow mistakes, not a sandbox against malicious agents.

`delegate.py init` copies this file to <state root>/guard.py so the hook command
can find it without knowing where the skill is installed. Stdlib only. Fails open:
any internal error allows the call, because a broken guard must not brick the session.
"""
import json
import os
import re
import sys
from pathlib import Path

FILE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

# Commands that change files or git state. Matched only when the command runs in,
# or names, a protected root. Read-only inspection (git log/diff/status, cat, grep,
# ls) stays allowed.
WRITE_PATTERNS = [
    r"\bgit\b[^;&|]*\b(commit|apply|am|cherry-pick|revert|rebase|reset|merge|stash|checkout|switch|restore|rm|mv|add|push|tag|clean|update-ref|worktree)\b",
    r"\bgit\b[^;&|]*\bbranch\s+-[a-zA-Z]*[dDmMcCf]",
    r"\bsed\b[^;&|]*\s-[a-zA-Z]*i",
    r"\bperl\b[^;&|]*\s-[a-zA-Z]*i",
    r"(^|[^0-9&>])>>?(?!\s*/dev/null)\s*[^&\s>]",
    r"\b(tee|cp|mv|rm|rmdir|touch|mkdir|ln|chmod|truncate|install|patch|dd)\b",
    r"\b(python3?|node|ruby|perl)\s+-[ce]\b",
]


def state_root() -> Path:
    return Path(os.environ.get("DELEGATE_SPEC_HOME") or Path.home() / ".local/state/delegate-spec")


def active_runs():
    runs_dir = state_root() / "runs"
    if not runs_dir.is_dir():
        return []
    runs = []
    for run_json in runs_dir.glob("*/run.json"):
        try:
            run = json.loads(run_json.read_text())
        except (OSError, ValueError):
            continue
        if run.get("active"):
            runs.append(run)
    return runs


def resolve(path: str, cwd: str) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    return Path(os.path.realpath(p))


def is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def protected_roots(runs):
    roots = []
    for run in runs:
        root = run.get("worktrees_root")
        if root:
            roots.append((run.get("run_id", "?"), Path(os.path.realpath(root)), root))
    return roots


def check_file_tool(tool_input, cwd, roots):
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    if not path:
        return None
    target = resolve(path, cwd)
    for run_id, root, _ in roots:
        if is_under(target, root):
            return run_id, root, f"writes {target}"
    return None


def check_bash(tool_input, cwd, roots):
    command = tool_input.get("command") or ""
    if not command:
        return None
    if not any(re.search(p, command) for p in WRITE_PATTERNS):
        return None
    here = Path(os.path.realpath(cwd)) if cwd else None
    for run_id, root, raw in roots:
        # Match the path as written too: /var/... and /private/var/... are the same dir on macOS.
        if (here and is_under(here, root)) or str(root) in command or raw in command:
            return run_id, root, f"runs a write-like command: {command[:200]}"
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = event.get("tool_name", "")
    tool_input = event.get("tool_input") or {}
    cwd = event.get("cwd") or ""
    roots = protected_roots(active_runs())
    if not roots:
        return 0
    if tool in FILE_TOOLS:
        hit = check_file_tool(tool_input, cwd, roots)
    elif tool == "Bash":
        hit = check_bash(tool_input, cwd, roots)
    else:
        hit = None
    if not hit:
        return 0
    run_id, root, what = hit
    reason = (
        f"delegate-spec run '{run_id}' is active and this call {what}. "
        f"Code under {root} may only be changed by an external agent: write a brief and use "
        f"`delegate.py dispatch` (or `dispatch --followup` to resume that ticket's session). "
        f"Read-only inspection is fine. If the run is over, close it with `delegate.py finish`."
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail open, but say so
        print(f"delegate-spec guard error (allowing call): {exc}", file=sys.stderr)
        sys.exit(0)
