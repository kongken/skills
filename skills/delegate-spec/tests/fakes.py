"""Fake `cursor-agent` and `pi` executables for tests.

Each fake logs its argv to $FAKE_ARGV_LOG and behaves according to $FAKE_MODE:
  commit   make a commit, report done          (default)
  nocommit report done without committing
  blocked  report blocked
  dirty    change a tracked file without committing
  error    provider failure (pi: exit 0 + stopReason error; cursor: exit 1, no output)
  hang     report done, then keep running (tests the post-completion watchdog)
  merge    merge $FAKE_MERGE_REF into the current branch, report done
"""

COMMON = r'''
import json, os, subprocess, sys, time, uuid

def log_argv():
    path = os.environ.get("FAKE_ARGV_LOG")
    if path:
        with open(path, "a") as fh:
            fh.write(json.dumps(sys.argv[1:]) + "\n")

def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

def do_work(cwd):
    mode = os.environ.get("FAKE_MODE", "commit")
    name = os.environ.get("FAKE_FILE", "work.txt")
    if mode in ("commit", "hang"):
        with open(os.path.join(cwd, name), "a") as fh:
            fh.write("work\n")
        git("add", name, cwd=cwd)
        git("commit", "-m", "work Refs #1", cwd=cwd)
    elif mode == "dirty":
        with open(os.path.join(cwd, "README.md"), "a") as fh:
            fh.write("dirty\n")
    elif mode == "merge":
        git("merge", "--no-edit", os.environ["FAKE_MERGE_REF"], cwd=cwd)
    status = "blocked" if mode == "blocked" else "done"
    return mode, "I did the thing.\n" + json.dumps({"status": status, "summary": "fake " + mode})
'''

PI = COMMON + r'''
log_argv()
args = sys.argv[1:]
if "--help" in args:
    print("--mode --session-dir --session-id --skill --model --no-extensions --no-skills "
          "--no-prompt-templates --no-approve --offline")
    sys.exit(0)
if "--version" in args:
    print("fake-pi 1.2.0"); sys.exit(0)
if "--list-models" in args:
    print("fake/model"); sys.exit(0)
sid = args[args.index("--session-id") + 1]
print(json.dumps({"type": "session", "version": 3, "id": sid, "cwd": os.getcwd()}), flush=True)
mode, text = do_work(os.getcwd())
stop = "error" if mode == "error" else "stop"
msg = {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": stop}
if mode == "error":
    msg["errorMessage"] = "401 invalid api key"
print(json.dumps({"type": "message_end", "message": msg}), flush=True)
print(json.dumps({"type": "agent_settled", "aborted": False}), flush=True)
if mode == "hang":
    time.sleep(60)
'''

CURSOR = COMMON + r'''
log_argv()
args = sys.argv[1:]
if "--help" in args:
    print("--print --output-format --workspace --resume --trust --force --model --plugin-dir --sandbox\n"
          "Commands:\n  create-chat")
    sys.exit(0)
if "--version" in args:
    print("2026.07.09-fake"); sys.exit(0)
if args[:1] == ["create-chat"]:
    print(str(uuid.uuid4()), flush=True)
    if os.environ.get("FAKE_CHAT_HANG"):
        time.sleep(60)
    sys.exit(0)
if args[:1] == ["status"]:
    print(json.dumps({"isAuthenticated": os.environ.get("FAKE_AUTH", "1") == "1"})); sys.exit(0)
if args[:1] == ["models"]:
    print("fake-model - Fake Model"); sys.exit(0)
ws = args[args.index("--workspace") + 1]
chat = args[args.index("--resume") + 1]
if os.environ.get("FAKE_MODE") == "error":
    print("Error: invalid model", file=sys.stderr); sys.exit(1)
print(json.dumps({"type": "system", "subtype": "init", "session_id": chat, "cwd": ws}), flush=True)
mode, text = do_work(ws)
print(json.dumps({"type": "assistant", "model_call_id": "m1", "timestamp_ms": 1,
                  "message": {"content": [{"type": "text", "text": "thinking aloud"}]}}), flush=True)
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}), flush=True)
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": "x" + text,
                  "session_id": chat, "usage": {"inputTokens": 1}}), flush=True)
if mode == "hang":
    time.sleep(60)
'''
