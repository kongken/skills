#!/usr/bin/env python3
"""delegate-spec runner.

The deterministic half of the delegate-spec skill. Claude orchestrates; this script
does everything mechanical so it's the same every time:

  preflight  check an agent CLI is installed, has the flags we rely on, is logged in
  init       start a run: integration branch + its own worktree, outside the repo
  dispatch   run one ticket (or a follow-up) through an external agent CLI in its worktree
  verify     run the configured verify commands in a ticket or integration worktree
  land       fast-forward a ticket branch onto the integration branch (only audited commits)
  restore    put a worktree that something other than an agent changed back as recorded
  audit      prove every commit on the integration branch came from an agent run
  guard-hook print the always-on guard hook for Claude Code settings
  status     show a run
  runs       list runs
  finish     close a run and clean up its worktrees

Every command prints one JSON object on stdout. Stdlib only, Python 3.9+, macOS/Linux.
See ../references/runner.md for the full contract.
"""
import argparse
import contextlib
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
VENDOR_DIR = SKILL_DIR / "vendor"
TDD_SKILL = "delegate-tdd"
TDD_DIR = VENDOR_DIR / "skills" / TDD_SKILL
GUARD_SRC = Path(__file__).resolve().parent / "guard.py"

DEFAULTS = {
    "default_agent": None,
    "max_parallel": 2,
    "branch_prefix": "delegate/",
    "verify": [],
    "verify_timeout_seconds": 1800,
    "agents": {},
}
AGENT_DEFAULTS = {
    "model": None,
    "profile": "isolated",
    "sandbox": False,
    "timeout_seconds": 3600,
    "idle_timeout_seconds": 900,
    "bin": None,
}
DONE_GRACE_SECONDS = 5
KILL_GRACE_SECONDS = 5

RULES = """## Operating rules (from the delegate-spec orchestrator)

- You are working in an isolated git worktree on branch `{branch}`. Stay inside it.
- Commit your work on this branch. Do not push, switch branches, rebase, or rewrite existing commits.
- Every commit message must reference the ticket: include `Refs {ref}`.
- Leave the working tree clean: commit what you want to keep, delete scratch files.
- Nobody will answer questions. If you cannot finish (missing information, a contradiction, a broken environment), stop and report blocked.
- End your final message with exactly one line of JSON and nothing after it:
  {{"status": "done" | "blocked", "summary": "<one sentence>", "blocker": "<why, only if blocked>"}}
"""


class Fail(Exception):
    def __init__(self, message, **extra):
        super().__init__(message)
        self.extra = extra


# ---------------------------------------------------------------- utilities

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def state_root() -> Path:
    return Path(os.environ.get("DELEGATE_SPEC_HOME") or Path.home() / ".local/state/delegate-spec")


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-._")
    if not slug:
        raise Fail(f"cannot make a slug from {value!r}")
    if not slug[0].isalnum():
        slug = "t" + slug
    return slug


def run_cmd(args, cwd=None, env=None, timeout=120, check=True):
    try:
        proc = subprocess.run(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise Fail(f"command not found: {args[0]}")
    except subprocess.TimeoutExpired:
        raise Fail(f"command timed out after {timeout}s: {' '.join(map(str, args))}")
    if check and proc.returncode != 0:
        raise Fail(f"command failed ({proc.returncode}): {' '.join(map(str, args))}",
                   stderr=proc.stderr.strip()[-2000:])
    return proc


def git(args, cwd, check=True) -> str:
    return run_cmd(["git", *args], cwd=cwd, check=check).stdout.strip()


def git_ok(args, cwd) -> bool:
    return run_cmd(["git", *args], cwd=cwd, check=False).returncode == 0


def deep_merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def config_paths(repo):
    paths = []
    if repo:
        paths.append(Path(repo) / "docs/agents/delegate-spec.json")
    user = os.environ.get("DELEGATE_SPEC_CONFIG")
    if user:
        paths.append(Path(user))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        paths.append(Path(xdg) / "delegate-spec/config.json")
    return paths


def load_config(repo):
    cfg = json.loads(json.dumps(DEFAULTS))
    used = []
    for path in config_paths(repo):
        if path.is_file():
            try:
                deep_merge(cfg, json.loads(path.read_text()))
            except ValueError as exc:
                raise Fail(f"invalid JSON in {path}: {exc}")
            used.append(str(path))
    cfg["_sources"] = used
    return cfg


def agent_settings(cfg, agent, overrides=None):
    settings = dict(AGENT_DEFAULTS)
    settings.update(cfg.get("agents", {}).get(agent, {}))
    for key, value in (overrides or {}).items():
        if value is not None:
            settings[key] = value
    env_bin = os.environ.get(f"DELEGATE_{agent.upper()}_BIN")
    settings["bin"] = env_bin or settings.get("bin") or ADAPTERS[agent].default_bin
    if settings["profile"] not in ("isolated", "inherit"):
        raise Fail(f"agents.{agent}.profile must be 'isolated' or 'inherit'")
    return settings


# ---------------------------------------------------------------- run state

def run_dir(run_id) -> Path:
    path = state_root() / "runs" / run_id
    if not (path / "run.json").is_file():
        raise Fail(f"no such run: {run_id}")
    return path


@contextlib.contextmanager
def locked(path: Path):
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path / ".lock" if path.is_dir() else str(path) + ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load_run(rdir: Path) -> dict:
    return json.loads((rdir / "run.json").read_text())


def save_run(rdir: Path, run: dict):
    tmp = rdir / "run.json.tmp"
    tmp.write_text(json.dumps(run, indent=2, ensure_ascii=False))
    tmp.replace(rdir / "run.json")


@contextlib.contextmanager
def mutate_run(rdir: Path):
    with locked(rdir):
        run = load_run(rdir)
        yield run
        save_run(rdir, run)


def ledger_commits(rdir: Path) -> dict:
    """commit sha -> ledger entry that produced it."""
    owners = {}
    path = rdir / "ledger.jsonl"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                entry = json.loads(line)
                for sha in entry.get("commits", []):
                    owners[sha] = entry
    return owners


def append_ledger(rdir: Path, entry: dict):
    with locked(rdir):
        with open(rdir / "ledger.jsonl", "a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def pid_alive(pid) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, TypeError):
        return False
    except PermissionError:
        return True


# ---------------------------------------------------------------- agent adapters

class CursorParser:
    def __init__(self):
        self.session_id = None
        self.final_text = None
        self.result = None
        self.rejected = 0
        self.done = False

    def feed(self, raw: bytes):
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("{"):
            return  # -w and some versions print plain lines
        try:
            event = json.loads(line)
        except ValueError:
            return
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self.session_id = event.get("session_id") or self.session_id
        elif kind == "assistant" and "model_call_id" not in event and "timestamp_ms" not in event:
            parts = (event.get("message") or {}).get("content") or []
            text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
            if text:
                self.final_text = text
        elif kind == "tool_call" and event.get("subtype") == "completed":
            call = event.get("tool_call") or {}
            for value in call.values():
                if isinstance(value, dict) and "rejected" in (value.get("result") or {}):
                    self.rejected += 1
        elif kind == "result":
            self.result = event
            self.session_id = event.get("session_id") or self.session_id
            self.done = True

    def summary(self, exit_code, finished_after_done):
        res = self.result or {}
        completed = self.result is not None
        ok = completed and (exit_code == 0 or finished_after_done) \
            and res.get("subtype") == "success" and not res.get("is_error")
        error = None
        if not completed:
            error = "no result event (CLI failed before finishing; see stderr log)"
        elif not ok:
            error = f"result subtype={res.get('subtype')} is_error={res.get('is_error')}"
        return {
            "ok": ok, "completed": completed, "session_id": self.session_id,
            "final_text": self.final_text or res.get("result"), "error": error,
            "usage": res.get("usage"), "rejected_tool_calls": self.rejected,
        }


class PiParser:
    def __init__(self):
        self.session_id = None
        self.last_assistant = None
        self.settled = False
        self.aborted = False
        self.done = False

    def feed(self, raw: bytes):
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except ValueError:
            return
        kind = event.get("type")
        if kind == "session":
            self.session_id = event.get("id")
        elif kind == "message_end" and (event.get("message") or {}).get("role") == "assistant":
            self.last_assistant = event["message"]
        elif kind == "agent_settled":
            self.settled = True
            self.aborted = bool(event.get("aborted"))
            self.done = True

    def summary(self, exit_code, finished_after_done):
        msg = self.last_assistant or {}
        stop = msg.get("stopReason")
        text = "".join(p.get("text", "") for p in msg.get("content") or [] if p.get("type") == "text")
        ok = self.settled and not self.aborted and stop == "stop" \
            and (exit_code == 0 or finished_after_done)
        error = None
        if not self.settled:
            error = "no agent_settled event (CLI failed before finishing; see stderr log)"
        elif not ok:
            error = msg.get("errorMessage") or f"stopReason={stop} aborted={self.aborted}"
        return {
            "ok": ok, "completed": self.settled, "session_id": self.session_id,
            "final_text": text or None, "error": error, "usage": msg.get("usage"),
        }


class CursorAdapter:
    name = "cursor"
    default_bin = "cursor-agent"
    required_flags = ["--print", "--output-format", "--workspace", "--resume", "--trust",
                      "--force", "--model", "--plugin-dir", "--sandbox"]
    required_commands = ["create-chat"]
    skill_prefix = f"/{TDD_SKILL} "
    parser = CursorParser

    def env(self, rdir, settings):
        env = os.environ.copy()
        if settings["profile"] == "isolated":
            cfg_dir = rdir / "agents" / "cursor-config"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            env["CURSOR_CONFIG_DIR"] = str(cfg_dir)
        return env

    def new_session(self, settings, env, wt, rdir, ticket, attempt):
        # create-chat prints the id right away but, with a fresh CURSOR_CONFIG_DIR, may not
        # exit afterwards; take the id and stop it rather than waiting for exit.
        out = read_until([settings["bin"], "create-chat"], wt, env,
                         r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", 180)
        if not out:
            raise Fail("cursor create-chat did not print a chat id within 180s")
        return out

    def command(self, settings, wt, rdir, session, prompt):
        cmd = [settings["bin"], "-p", "--output-format", "stream-json", "--trust",
               "--workspace", str(wt), "--model", settings["model"], "--resume", session,
               "--plugin-dir", str(VENDOR_DIR)]
        cmd += ["--sandbox", "enabled"] if settings.get("sandbox") else ["--force"]
        return cmd + [prompt]


class PiAdapter:
    name = "pi"
    default_bin = "pi"
    required_flags = ["--mode", "--session-dir", "--session-id", "--skill", "--model",
                      "--no-extensions", "--no-skills", "--no-prompt-templates",
                      "--no-approve", "--offline"]
    required_commands = []
    skill_prefix = f"/skill:{TDD_SKILL} "
    parser = PiParser

    def env(self, rdir, settings):
        return os.environ.copy()

    def new_session(self, settings, env, wt, rdir, ticket, attempt):
        return slugify(f"{ticket}-a{attempt}")

    def command(self, settings, wt, rdir, session, prompt):
        sdir = rdir / "agents" / "pi-sessions"
        sdir.mkdir(parents=True, exist_ok=True)
        cmd = [settings["bin"], "--mode", "json", "--model", settings["model"],
               "--session-dir", str(sdir), "--session-id", session, "--skill", str(TDD_DIR)]
        if settings["profile"] == "isolated":
            cmd += ["--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-approve"]
        return cmd + [prompt]


ADAPTERS = {"cursor": CursorAdapter(), "pi": PiAdapter()}


# ---------------------------------------------------------------- process supervision

_CHILD = {"proc": None}


def _kill_group(proc, sig):
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _on_signal(signum, frame):
    proc = _CHILD.get("proc")
    if proc is not None:
        _kill_group(proc, signal.SIGTERM)
    raise SystemExit(128 + signum)


def read_until(cmd, cwd, env, pattern, timeout):
    """Run cmd until its stdout matches pattern; return the match and stop the process."""
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, start_new_session=True)
    found = queue.Queue()

    def pump():
        for raw in iter(proc.stdout.readline, b""):
            match = re.search(pattern, raw.decode("utf-8", "replace"))
            if match:
                found.put(match.group(0))
                return
        found.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        value = found.get(timeout=timeout)
    except queue.Empty:
        value = None
    if proc.poll() is None:
        _kill_group(proc, signal.SIGTERM)
        try:
            proc.wait(timeout=KILL_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _kill_group(proc, signal.SIGKILL)
            proc.wait()
    _kill_group(proc, signal.SIGTERM)
    return value


def supervise(cmd, cwd, env, events_path, stderr_path, parser, timeout, idle_timeout):
    """Run cmd in its own process group, stream stdout to events_path, feed parser.

    Stops the process when it exceeds timeout or stays silent for idle_timeout, and
    shortly after the parser has seen the completion event (some CLIs keep running
    after they're done). Cleans up whatever is left in the process group.
    """
    env = dict(env, GIT_EDITOR="true", GIT_TERMINAL_PROMPT="0")
    with open(events_path, "ab") as events, open(stderr_path, "ab") as errors:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
        _CHILD["proc"] = proc
        lines = queue.Queue()

        def pump():
            for raw in iter(proc.stdout.readline, b""):
                lines.put(raw)
            lines.put(None)

        threading.Thread(target=pump, daemon=True).start()
        start = last = time.monotonic()
        done_at = None
        stopped = None
        eof = False
        while True:
            now = time.monotonic()
            if eof and proc.poll() is not None:
                break
            if done_at is not None and now - done_at > DONE_GRACE_SECONDS:
                stopped = "after_done"
                break
            if now - start > timeout:
                stopped = "timeout"
                break
            if done_at is None and now - last > idle_timeout:
                stopped = "idle_timeout"
                break
            if eof:
                time.sleep(0.2)
                continue
            try:
                raw = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if raw is None:
                eof = True
                continue
            last = now
            events.write(raw)
            events.flush()
            parser.feed(raw)
            if parser.done and done_at is None:
                done_at = now
        if proc.poll() is None:
            _kill_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=KILL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _kill_group(proc, signal.SIGKILL)
                proc.wait()
        _kill_group(proc, signal.SIGTERM)  # leftovers in the group (e.g. MCP servers)
        _CHILD["proc"] = None
        return proc.returncode, stopped, time.monotonic() - start


# ---------------------------------------------------------------- report parsing

def extract_report(text):
    if not text:
        return None
    for line in reversed(text.strip().splitlines()):
        line = line.strip().strip("`").strip()
        if not line:
            continue
        if line.startswith("{") and line.endswith("}"):
            try:
                report = json.loads(line)
            except ValueError:
                return None
            return report if isinstance(report, dict) and "status" in report else None
        return None
    return None


def classify(stopped, cli, rewritten, report, dirty, commits):
    if stopped in ("timeout", "idle_timeout"):
        return "timed_out"
    if not cli["ok"]:
        return "cli_failed"
    if rewritten:
        return "history_rewritten"
    if report and report.get("status") == "blocked":
        return "blocked"
    if dirty:
        return "dirty"
    if not commits:
        return "no_commits"
    if not report:
        return "unreported"
    return "done" if report.get("status") == "done" else "unreported"


def snapshot(wt):
    """What a worktree looks like right now: HEAD plus every changed or untracked path."""
    return {"head": git(["rev-parse", "HEAD"], wt),
            "status": git(["status", "--porcelain", "--untracked-files=all"], wt)}


def check_untouched(wt, recorded, what):
    """Fail if wt changed since the runner last recorded it: someone other than an agent wrote there."""
    if not recorded:
        return
    current = snapshot(wt)
    if current != recorded:
        raise Fail(f"{what} changed outside an agent since the runner last touched it",
                   outcome="tampered", worktree=str(wt), expected=recorded, found=current,
                   hint="nothing but an agent may change a run worktree; `restore` puts it back")


def worktree_changes(wt):
    status = git(["status", "--porcelain"], wt)
    tracked = [l for l in status.splitlines() if l and not l.startswith("??")]
    untracked = [l[3:] for l in status.splitlines() if l.startswith("??")]
    return tracked, untracked


# ---------------------------------------------------------------- commands

def cmd_preflight(args):
    repo = git(["rev-parse", "--show-toplevel"], args.repo or os.getcwd(), check=False) or None
    cfg = load_config(repo)
    adapter = ADAPTERS[args.agent]
    settings = agent_settings(cfg, args.agent, {"model": args.model})
    out = {"ok": True, "agent": args.agent, "bin": settings["bin"], "profile": settings["profile"],
           "config_sources": cfg["_sources"], "problems": [], "warnings": []}
    path = shutil.which(settings["bin"])
    if not path:
        out.update(ok=False, problems=[f"'{settings['bin']}' is not on PATH"])
        return out
    out["path"] = path
    out["version"] = run_cmd([path, "--version"], check=False, timeout=60).stdout.strip()[:200]
    help_text = run_cmd([path, "--help"], check=False, timeout=60)
    help_text = help_text.stdout + help_text.stderr
    missing = [f for f in adapter.required_flags if f not in help_text]
    missing += [c for c in adapter.required_commands if c not in help_text]
    if missing:
        out["problems"].append(f"this {args.agent} version lacks: {', '.join(missing)}")
    if not settings.get("model"):
        out["problems"].append(f"no model configured for {args.agent} (agents.{args.agent}.model)")
    else:
        out["model"] = settings["model"]
        listing = run_cmd([path, "models"] if args.agent == "cursor" else [path, "--list-models"],
                          check=False, timeout=120)
        base = re.split(r"[\[:]", settings["model"].split("/")[-1])[0]
        if listing.returncode == 0 and base:
            found = base in listing.stdout + listing.stderr
            out["model_listed"] = found
            if not found:
                out["warnings"].append(f"model '{settings['model']}' not found in the CLI's model list")
    if args.agent == "cursor":
        status = run_cmd([path, "status", "--format", "json"], check=False, timeout=60,
                         env=CursorAdapter().env(state_root() / "preflight", settings)
                         if settings["profile"] == "isolated" else None)
        try:
            authed = bool(json.loads(status.stdout).get("isAuthenticated"))
        except ValueError:
            authed = None
        out["authenticated"] = authed
        if authed is False and not os.environ.get("CURSOR_API_KEY"):
            out["problems"].append("cursor-agent is not logged in: run `cursor-agent login` or set CURSOR_API_KEY")
    else:
        out["warnings"].append("pi has no reliable auth check; the first dispatch will surface auth errors")
    out["ok"] = not out["problems"]
    return out


def default_base(repo):
    head = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], repo, check=False)
    return head or git(["rev-parse", "--abbrev-ref", "HEAD"], repo)


def cmd_init(args):
    repo = git(["rev-parse", "--show-toplevel"], args.repo or os.getcwd())
    cfg = load_config(repo)
    slug = slugify(args.name)
    base = args.base or default_base(repo)
    base_sha = git(["rev-parse", "--verify", f"{base}^{{commit}}"], repo)
    prefix = cfg["branch_prefix"]
    integration = args.integration_branch or f"{prefix}{slug}/integration"
    run_id = f"{slug}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    rdir = state_root() / "runs" / run_id
    wt_root = rdir / "wt"
    wt_root.mkdir(parents=True)
    integ_wt = wt_root / "_integration"
    if git_ok(["show-ref", "--verify", "--quiet", f"refs/heads/{integration}"], repo):
        git(["worktree", "add", str(integ_wt), integration], repo)
        reused = True
    else:
        git(["worktree", "add", "-b", integration, str(integ_wt), base_sha], repo)
        reused = False
    for sub in ("briefs", "notes", "tickets", "verify"):
        (rdir / sub).mkdir()
    run = {
        "run_id": run_id, "name": slug, "repo": repo, "base": base, "base_sha": base_sha,
        "integration_branch": integration, "integration_worktree": str(integ_wt),
        "worktrees_root": str(wt_root), "branch_prefix": f"{prefix}{slug}/",
        "active": True, "created_at": now_iso(), "tickets": {}, "running": {},
        "spec": args.spec,
    }
    run["integration_snapshot"] = snapshot(integ_wt)
    save_run(rdir, run)
    shutil.copyfile(GUARD_SRC, state_root() / "guard.py")
    others = [r for r in list_runs() if r["active"] and r["repo"] == repo and r["run_id"] != run_id]
    return {"ok": True, "run_id": run_id, "run_dir": str(rdir), "briefs_dir": str(rdir / "briefs"),
            "notes_dir": str(rdir / "notes"), "integration_branch": integration,
            "integration_worktree": str(integ_wt), "base": base, "base_sha": base_sha,
            "reused_integration_branch": reused,
            "warnings": [f"another active run on this repo: {r['run_id']}" for r in others]}


def ensure_ticket(rdir, run, ticket_id, ref):
    tslug = slugify(ticket_id)
    ticket = run["tickets"].get(tslug)
    if ticket and Path(ticket["worktree"]).is_dir():
        if ref:
            ticket["ref"] = ref
        return tslug, ticket
    branch = f"{run['branch_prefix']}{tslug}"
    wt = Path(run["worktrees_root"]) / tslug
    repo = run["repo"]
    if git_ok(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], repo):
        git(["worktree", "add", str(wt), branch], repo)
    else:
        tip = git(["rev-parse", run["integration_branch"]], repo)
        git(["worktree", "add", "-b", branch, str(wt), tip], repo)
    ticket = ticket or {"id": ticket_id, "attempts": 0, "agent": None, "session": None,
                        "landed": False, "last_outcome": None}
    ticket.update(branch=branch, worktree=str(wt),
                  ref=ref or ticket.get("ref") or (f"#{ticket_id}" if str(ticket_id).isdigit() else str(ticket_id)))
    ticket["snapshot"] = snapshot(wt)
    run["tickets"][tslug] = ticket
    return tslug, ticket


def read_brief(path):
    if path == "-":
        return sys.stdin.read()
    p = Path(path)
    if not p.is_file():
        raise Fail(f"brief not found: {path}")
    return p.read_text()


def cmd_dispatch(args):
    rdir = run_dir(args.run)
    brief = read_brief(args.brief).strip()
    if not brief:
        raise Fail("brief is empty")
    with mutate_run(rdir) as run:
        if not run.get("active"):
            raise Fail(f"run {args.run} is finished")
        cfg = load_config(run["repo"])
        agent = args.agent or (run["tickets"].get(slugify(args.ticket)) or {}).get("agent") \
            or cfg.get("default_agent")
        if agent not in ADAPTERS:
            raise Fail(f"choose an agent with --agent ({', '.join(ADAPTERS)}) or set default_agent")
        settings = agent_settings(cfg, agent, {
            "model": args.model, "profile": args.profile, "sandbox": args.sandbox,
            "timeout_seconds": args.timeout})
        if not settings.get("model"):
            raise Fail(f"no model configured for {agent}: pass --model or set agents.{agent}.model")
        running = {k: v for k, v in run.get("running", {}).items() if pid_alive(v.get("pid"))}
        tslug = slugify(args.ticket)
        if tslug in running:
            raise Fail(f"ticket {args.ticket} is already being worked on (pid {running[tslug]['pid']})")
        limit = int(cfg.get("max_parallel") or 1)
        if len(running) >= limit:
            raise Fail(f"max_parallel={limit} reached; wait for a running ticket to finish",
                       running=sorted(running))
        tslug, ticket = ensure_ticket(rdir, run, args.ticket, args.ref)
        check_untouched(ticket["worktree"], ticket.get("snapshot"), f"ticket {tslug}'s worktree")
        if args.followup:
            if ticket.get("agent") != agent or not ticket.get("session"):
                raise Fail("--followup needs an earlier dispatch of this ticket with the same agent")
        ticket["attempts"] += 1
        attempt = ticket["attempts"]
        running[tslug] = {"pid": os.getpid(), "started_at": now_iso(), "agent": agent}
        run["running"] = running
    try:
        result = _dispatch(rdir, run, tslug, ticket, agent, settings, attempt, brief, args.followup)
    finally:
        with mutate_run(rdir) as run2:
            run2.get("running", {}).pop(tslug, None)
    with mutate_run(rdir) as run3:
        t = run3["tickets"][tslug]
        t.update(agent=agent, session=result["session"], last_outcome=result["outcome"],
                 last_attempt_dir=result["attempt_dir"], snapshot=snapshot(t["worktree"]))
    return result


def _dispatch(rdir, run, tslug, ticket, agent, settings, attempt, brief, followup):
    adapter = ADAPTERS[agent]
    wt = Path(ticket["worktree"])
    adir = rdir / "tickets" / tslug / f"attempt-{attempt}"
    adir.mkdir(parents=True, exist_ok=True)
    env = adapter.env(rdir, settings)
    rules = RULES.format(branch=ticket["branch"], ref=ticket["ref"])
    if followup:
        session = ticket["session"]
        prompt = f"{brief}\n\n{rules}"
    else:
        session = adapter.new_session(settings, env, str(wt), rdir, tslug, attempt)
        prompt = f"{adapter.skill_prefix}{brief}\n\n{rules}"
    (adir / "prompt.md").write_text(prompt)
    head_before = git(["rev-parse", "HEAD"], wt)
    cmd = adapter.command(settings, wt, rdir, session, prompt)
    (adir / "command.json").write_text(json.dumps(cmd[:-1] + ["<prompt.md>"], indent=2))
    parser = adapter.parser()
    exit_code, stopped, elapsed = supervise(
        cmd, str(wt), env, adir / "events.jsonl", adir / "stderr.log", parser,
        int(settings["timeout_seconds"]), int(settings["idle_timeout_seconds"]))
    cli = parser.summary(exit_code, stopped == "after_done")
    head_after = git(["rev-parse", "HEAD"], wt)
    rewritten = head_after != head_before and not git_ok(
        ["merge-base", "--is-ancestor", head_before, head_after], wt)
    # New commits, minus anything already on the integration branch: a follow-up that merges
    # the integration tip brings in other tickets' commits, which this agent didn't author.
    commits = git(["rev-list", head_after, f"^{head_before}", f"^{run['integration_branch']}"],
                  wt).split() if head_after != head_before else []
    tracked, untracked = worktree_changes(wt)
    report = extract_report(cli.get("final_text"))
    outcome = classify(stopped, cli, rewritten, report, tracked, commits)
    session = cli.get("session_id") or session
    if commits:
        append_ledger(rdir, {"ticket": tslug, "attempt": attempt, "agent": agent, "session": session,
                             "head_before": head_before, "head_after": head_after,
                             "commits": commits, "at": now_iso()})
    stderr_tail = (adir / "stderr.log").read_text(errors="replace")[-1500:]
    result = {
        "ok": outcome == "done", "outcome": outcome, "ticket": tslug, "attempt": attempt,
        "agent": agent, "model": settings["model"], "profile": settings["profile"],
        "followup": followup, "session": session, "branch": ticket["branch"], "worktree": str(wt),
        "exit_code": exit_code, "stopped": stopped, "elapsed_seconds": round(elapsed, 1),
        "cli": {k: v for k, v in cli.items() if k != "final_text"},
        "report": report, "final_text_tail": (cli.get("final_text") or "")[-1500:],
        "commits": commits, "head_before": head_before, "head_after": head_after,
        "uncommitted_tracked": tracked, "untracked": untracked[:50],
        "stderr_tail": stderr_tail if outcome == "cli_failed" else stderr_tail[-300:],
        "attempt_dir": str(adir),
    }
    (adir / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def cmd_verify(args):
    rdir = run_dir(args.run)
    run = load_run(rdir)
    cfg = load_config(run["repo"])
    if args.ticket:
        ticket = run["tickets"].get(slugify(args.ticket))
        if not ticket:
            raise Fail(f"unknown ticket {args.ticket}")
        wt, target = ticket["worktree"], slugify(args.ticket)
        check_untouched(wt, ticket.get("snapshot"), f"ticket {target}'s worktree")
    else:
        wt, target = run["integration_worktree"], "integration"
        check_untouched(wt, run.get("integration_snapshot"), "the integration worktree")
    commands = args.cmd or cfg.get("verify") or []
    if not commands:
        raise Fail("no verify commands: set \"verify\" in docs/agents/delegate-spec.json or pass --cmd")
    timeout = int(cfg.get("verify_timeout_seconds") or 1800)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for i, command in enumerate(commands):
        log = rdir / "verify" / f"{target}-{stamp}-{i}.log"
        start = time.monotonic()
        with open(log, "wb") as fh:
            proc = subprocess.Popen(command, shell=True, cwd=wt, stdin=subprocess.DEVNULL,
                                    stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                _kill_group(proc, signal.SIGKILL)
                code, timed_out = proc.wait(), True
        tail = log.read_text(errors="replace").splitlines()[-80:]
        results.append({"command": command, "exit_code": code, "timed_out": timed_out,
                        "seconds": round(time.monotonic() - start, 1), "log": str(log),
                        "output_tail": "\n".join(tail)})
        if code != 0 and not args.keep_going:
            break
    ok = len(results) == len(commands) and all(r["exit_code"] == 0 for r in results)
    with mutate_run(rdir) as run:  # build output the verify commands left behind is expected
        if target == "integration":
            run["integration_snapshot"] = snapshot(wt)
        else:
            run["tickets"][target]["snapshot"] = snapshot(wt)
    return {"ok": ok, "target": target, "worktree": wt, "results": results}


def cmd_land(args):
    rdir = run_dir(args.run)
    with mutate_run(rdir) as run:
        tslug = slugify(args.ticket)
        ticket = run["tickets"].get(tslug)
        if not ticket:
            raise Fail(f"unknown ticket {args.ticket}")
        integ_wt = run["integration_worktree"]
        integration = run["integration_branch"]
        check_untouched(integ_wt, run.get("integration_snapshot"), "the integration worktree")
        check_untouched(ticket["worktree"], ticket.get("snapshot"), f"ticket {tslug}'s worktree")
        tracked, _ = worktree_changes(integ_wt)
        if tracked:
            raise Fail("integration worktree has uncommitted changes", changes=tracked)
        tracked, _ = worktree_changes(ticket["worktree"])
        if tracked:
            raise Fail("ticket worktree has uncommitted changes; follow up with the agent first", changes=tracked)
        pending = git(["rev-list", ticket["branch"], f"^{integration}"], integ_wt).split()
        if not pending:
            return {"ok": True, "outcome": "nothing_to_land", "ticket": tslug}
        owners = ledger_commits(rdir)
        unaudited = [sha for sha in pending if sha not in owners]
        if unaudited:
            raise Fail("refusing to land commits that no agent run produced", outcome="unaudited_commits",
                       unaudited=unaudited)
        if not git_ok(["merge-base", "--is-ancestor", integration, ticket["branch"]], integ_wt):
            return {"ok": False, "outcome": "needs_merge", "ticket": tslug,
                    "hint": f"follow up with the agent: merge `{integration}` into `{ticket['branch']}`, "
                            "resolve conflicts, run the tests, commit"}
        git(["merge", "--ff-only", ticket["branch"]], integ_wt)
        run["integration_snapshot"] = snapshot(integ_wt)
        head = git(["rev-parse", "HEAD"], integ_wt)
        ticket.update(landed=True, landed_at=now_iso(), landed_head=head)
        return {"ok": True, "outcome": "landed", "ticket": tslug, "commits": pending,
                "integration_head": head}


def cmd_restore(args):
    rdir = run_dir(args.run)
    with mutate_run(rdir) as run:
        if args.ticket:
            tslug = slugify(args.ticket)
            ticket = run["tickets"].get(tslug)
            if not ticket:
                raise Fail(f"unknown ticket {args.ticket}")
            wt, recorded = ticket["worktree"], ticket.get("snapshot") or {}
        else:
            wt, recorded = run["integration_worktree"], run.get("integration_snapshot") or {}
        head = recorded.get("head") or git(["rev-parse", "HEAD"], wt)
        before = snapshot(wt)
        git(["reset", "--hard", head], wt)
        git(["clean", "-fd"], wt)
        after = snapshot(wt)
        if args.ticket:
            ticket["snapshot"] = after
        else:
            run["integration_snapshot"] = after
        return {"ok": True, "worktree": wt, "reset_to": head, "discarded": before}


def cmd_audit(args):
    rdir = run_dir(args.run)
    run = load_run(rdir)
    commits = git(["rev-list", run["integration_branch"], f"^{run['base_sha']}"], run["repo"]).split()
    owners = ledger_commits(rdir)
    unaudited = [sha for sha in commits if sha not in owners]
    by_agent = {}
    for sha in commits:
        if sha in owners:
            key = f"{owners[sha]['agent']}:{owners[sha]['ticket']}"
            by_agent.setdefault(key, []).append(sha)
    return {"ok": not unaudited, "integration_branch": run["integration_branch"],
            "base_sha": run["base_sha"], "commits": len(commits), "unaudited": unaudited,
            "by_agent_ticket": by_agent}


GUARD_COMMAND = ('d="$DELEGATE_SPEC_HOME"; [ -n "$d" ] || d="$HOME/.local/state/delegate-spec"; '
                 'if [ -f "$d/guard.py" ]; then exec python3 "$d/guard.py"; fi')


def cmd_guard_hook(args):
    """The always-on guard, as a Claude Code settings fragment.

    The hook in SKILL.md covers only the main agent of the session that invoked the skill,
    and only until that process exits. A settings hook also covers subagents and resumed
    sessions. guard.py does nothing unless a run is active, so leaving it on is cheap.
    """
    state_root().mkdir(parents=True, exist_ok=True)
    shutil.copyfile(GUARD_SRC, state_root() / "guard.py")
    return {"ok": True, "settings_file": str(Path.home() / ".claude/settings.json"),
            "merge_into_settings": {"hooks": {"PreToolUse": [{
                "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash",
                "hooks": [{"type": "command", "command": GUARD_COMMAND}]}]}}}


def list_runs():
    runs = []
    root = state_root() / "runs"
    for path in sorted(root.glob("*/run.json")) if root.is_dir() else []:
        try:
            run = json.loads(path.read_text())
        except ValueError:
            continue
        runs.append({"run_id": run["run_id"], "repo": run["repo"], "active": run.get("active"),
                     "integration_branch": run["integration_branch"], "created_at": run.get("created_at")})
    return runs


def cmd_runs(args):
    runs = list_runs()
    if args.active:
        runs = [r for r in runs if r["active"]]
    return {"ok": True, "runs": runs}


def cmd_status(args):
    rdir = run_dir(args.run)
    run = load_run(rdir)
    run["running"] = {k: v for k, v in run.get("running", {}).items() if pid_alive(v.get("pid"))}
    run["ok"] = True
    run["run_dir"] = str(rdir)
    return run


def cmd_finish(args):
    rdir = run_dir(args.run)
    with mutate_run(rdir) as run:
        repo = run["repo"]
        kept, removed = [], []
        if not args.abort:
            for tslug, ticket in run["tickets"].items():
                wt = ticket["worktree"]
                if not Path(wt).is_dir():
                    continue
                tracked, untracked = worktree_changes(wt)
                if ticket.get("landed") and not tracked and not untracked:
                    git(["worktree", "remove", wt], repo)
                    removed.append(wt)
                    if args.delete_branches:
                        git(["branch", "-d", ticket["branch"]], repo, check=False)
                else:
                    kept.append({"ticket": tslug, "worktree": wt, "landed": ticket.get("landed")})
            integ = run["integration_worktree"]
            if Path(integ).is_dir():
                tracked, untracked = worktree_changes(integ)
                if not tracked and not untracked:
                    git(["worktree", "remove", integ], repo)
                    removed.append(integ)
                else:
                    kept.append({"ticket": "_integration", "worktree": integ})
            git(["worktree", "prune"], repo, check=False)
        run["active"] = False
        run["finished_at"] = now_iso()
        return {"ok": True, "run_id": run["run_id"], "aborted": bool(args.abort),
                "integration_branch": run["integration_branch"], "removed": removed, "kept": kept}


# ---------------------------------------------------------------- cli

def build_parser():
    p = argparse.ArgumentParser(prog="delegate.py", description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("preflight", help="check an agent CLI is usable")
    s.add_argument("--agent", required=True, choices=sorted(ADAPTERS))
    s.add_argument("--model")
    s.add_argument("--repo")
    s.set_defaults(func=cmd_preflight)

    s = sub.add_parser("init", help="start a run")
    s.add_argument("--name", required=True, help="short slug for the spec, e.g. its issue number")
    s.add_argument("--repo")
    s.add_argument("--base", help="branch or ref to build on (default: origin/HEAD or current branch)")
    s.add_argument("--integration-branch")
    s.add_argument("--spec", help="pointer to the spec (URL, issue ref or path), recorded in run.json")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("dispatch", help="run one ticket through an external agent")
    s.add_argument("--run", required=True)
    s.add_argument("--ticket", required=True)
    s.add_argument("--brief", required=True, help="brief file, or - for stdin")
    s.add_argument("--agent", choices=sorted(ADAPTERS))
    s.add_argument("--model")
    s.add_argument("--profile", choices=["isolated", "inherit"])
    s.add_argument("--sandbox", action="store_true", default=None, help="cursor only: run shell in its sandbox")
    s.add_argument("--timeout", type=int)
    s.add_argument("--ref", help="ticket reference for commit messages, e.g. '#12' or 'group/proj#12'")
    s.add_argument("--followup", action="store_true", help="continue the ticket's existing agent session")
    s.set_defaults(func=cmd_dispatch)

    s = sub.add_parser("verify", help="run verify commands")
    s.add_argument("--run", required=True)
    s.add_argument("--ticket", help="verify a ticket worktree (default: integration)")
    s.add_argument("--cmd", action="append", help="override configured verify commands")
    s.add_argument("--keep-going", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("land", help="fast-forward a ticket onto the integration branch")
    s.add_argument("--run", required=True)
    s.add_argument("--ticket", required=True)
    s.set_defaults(func=cmd_land)

    s = sub.add_parser("restore", help="put a tampered worktree back to the state the runner recorded")
    s.add_argument("--run", required=True)
    s.add_argument("--ticket", help="ticket worktree to restore (default: integration)")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("audit", help="check every integration commit came from an agent run")
    s.add_argument("--run", required=True)
    s.set_defaults(func=cmd_audit)

    s = sub.add_parser("status", help="show a run")
    s.add_argument("--run", required=True)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("guard-hook", help="print the always-on guard hook for Claude Code settings")
    s.set_defaults(func=cmd_guard_hook)

    s = sub.add_parser("runs", help="list runs")
    s.add_argument("--active", action="store_true")
    s.set_defaults(func=cmd_runs)

    s = sub.add_parser("finish", help="close a run and clean up")
    s.add_argument("--run", required=True)
    s.add_argument("--abort", action="store_true", help="mark finished without cleaning up")
    s.add_argument("--delete-branches", action="store_true", help="delete landed ticket branches")
    s.set_defaults(func=cmd_finish)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    try:
        out = args.func(args)
    except Fail as exc:
        out = {"ok": False, "error": str(exc), **exc.extra}
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
