"""End-to-end tests for scripts/delegate.py and scripts/guard.py, using fake agent CLIs.

Run: python3 -m unittest discover -s skills/delegate-spec/tests -v
"""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
DELEGATE = SCRIPTS / "delegate.py"
GUARD = SCRIPTS / "guard.py"
sys.path.insert(0, str(HERE))
import fakes  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
}


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="delegate-test-"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("pi", fakes.PI), ("cursor-agent", fakes.CURSOR)):
            path = self.bin / name
            path.write_text(f"#!{sys.executable}\n{body}")
            path.chmod(path.stat().st_mode | stat.S_IEXEC)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.env = dict(os.environ, **GIT_ENV,
                        PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                        DELEGATE_SPEC_HOME=str(self.tmp / "state"),
                        XDG_CONFIG_HOME=str(self.tmp / "config"),
                        FAKE_ARGV_LOG=str(self.tmp / "argv.log"))
        self.env.pop("DELEGATE_SPEC_CONFIG", None)
        self.sh("git", "init", "-q", "-b", "main")
        (self.repo / "README.md").write_text("hello\n")
        self.sh("git", "add", "README.md")
        self.sh("git", "commit", "-q", "-m", "init")
        cfg = self.repo / "docs/agents"
        cfg.mkdir(parents=True)
        (cfg / "delegate-spec.json").write_text(json.dumps({
            "default_agent": "pi", "max_parallel": 2, "verify": ["test -f README.md"],
            "agents": {"pi": {"model": "fake/model"}, "cursor": {"model": "fake-model"}},
        }))
        self.brief = self.tmp / "brief.md"
        self.brief.write_text("Implement ticket 1: add work.txt.")

    def sh(self, *args, cwd=None):
        return subprocess.run(args, cwd=cwd or self.repo, env=self.env, check=True,
                              capture_output=True, text=True).stdout.strip()

    def delegate(self, *args, expect_ok=True, **env):
        proc = subprocess.run([sys.executable, str(DELEGATE), *args], cwd=self.repo,
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=120)
        out = json.loads(proc.stdout)
        if expect_ok is not None:
            self.assertEqual(out.get("ok"), expect_ok, json.dumps(out, indent=2) + proc.stderr)
        return out

    def init(self):
        out = self.delegate("init", "--name", "spec-1", "--base", "main")
        self.run_id = out["run_id"]
        return out

    def dispatch(self, ticket="1", *extra, expect_ok=True, **env):
        return self.delegate("dispatch", "--run", self.run_id, "--ticket", ticket,
                             "--brief", str(self.brief), *extra, expect_ok=expect_ok, **env)

    def argv_log(self):
        return [json.loads(l) for l in (self.tmp / "argv.log").read_text().splitlines()]


class InitAndDispatch(Harness):
    def test_init_creates_integration_worktree_outside_repo(self):
        out = self.init()
        wt = Path(out["integration_worktree"])
        self.assertTrue(wt.is_dir())
        self.assertFalse(str(wt).startswith(str(self.repo)))
        self.assertEqual(self.sh("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=wt), "delegate/spec-1/integration")
        self.assertTrue((self.tmp / "state/guard.py").is_file())

    def test_init_rejects_existing_integration_branch_before_creating_state(self):
        self.init()
        self.dispatch()
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        self.delegate("land", "--run", self.run_id, "--ticket", "1")
        self.delegate("finish", "--run", self.run_id)
        refused = self.delegate("init", "--name", "spec-1", "--base", "main", expect_ok=False)
        self.assertEqual(refused["outcome"], "branch_exists")
        self.assertEqual(len(self.delegate("runs")["runs"]), 1)

    def test_pi_dispatch_done_records_ledger(self):
        self.init()
        out = self.dispatch()
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(len(out["commits"]), 1)
        self.assertEqual(out["report"]["status"], "done")
        ledger = (self.tmp / "state/runs" / self.run_id / "ledger.jsonl").read_text()
        self.assertIn(out["commits"][0], ledger)
        argv = self.argv_log()[-1]
        for flag in ("--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-approve"):
            self.assertIn(flag, argv)
        self.assertTrue(argv[-1].startswith("/skill:delegate-tdd "))
        self.assertIn("Refs #1", argv[-1])

    def test_dispatch_returns_sparse_summary_with_full_diagnostics_on_disk(self):
        self.init()
        out = self.dispatch()
        self.assertNotIn("final_text_tail", out)
        self.assertNotIn("cli", out)
        self.assertEqual(out["report"]["status"], "done")
        full = json.loads((Path(out["attempt_dir"]) / "result.json").read_text())
        self.assertIn("final_text_tail", full)
        self.assertTrue(full["cli"]["ok"])
        verbose = self.dispatch("1", "--followup", "--verbose", FAKE_FILE="more.txt")
        self.assertIn("final_text_tail", verbose)
        self.assertTrue(verbose["cli"]["ok"])

    def test_status_hides_snapshots_unless_verbose_without_changing_state(self):
        self.init()
        self.dispatch()
        compact = self.delegate("status", "--run", self.run_id)
        self.assertNotIn("integration_snapshot", compact)
        self.assertNotIn("snapshot", compact["tickets"]["1"])
        full = self.delegate("status", "--run", self.run_id, "--verbose")
        self.assertIn("integration_snapshot", full)
        self.assertIn("snapshot", full["tickets"]["1"])

    def test_pi_inherit_profile_keeps_user_extensions(self):
        self.init()
        self.dispatch("1", "--profile", "inherit")
        self.assertNotIn("--no-extensions", self.argv_log()[-1])

    def test_provider_error_is_cli_failed_even_with_exit_zero(self):
        self.init()
        out = self.dispatch(expect_ok=False, FAKE_MODE="error")
        self.assertEqual(out["outcome"], "cli_failed")
        self.assertIn("401", out["cli"]["error"])

    def test_outcomes(self):
        self.init()
        for ticket, mode, outcome in (("2", "nocommit", "no_commits"), ("3", "blocked", "blocked"),
                                      ("4", "dirty", "dirty")):
            out = self.dispatch(ticket, expect_ok=False, FAKE_MODE=mode)
            self.assertEqual(out["outcome"], outcome, mode)

    def test_cursor_dispatch_and_followup_reuse_chat(self):
        self.init()
        first = self.dispatch("1", "--agent", "cursor")
        self.assertEqual(first["outcome"], "done")
        argv = self.argv_log()
        self.assertEqual(argv[0], ["create-chat"])
        run_argv = argv[1]
        self.assertIn("--force", run_argv)
        self.assertIn("--plugin-dir", run_argv)
        self.assertTrue(run_argv[-1].startswith("/delegate-tdd "))
        chat = run_argv[run_argv.index("--resume") + 1]
        self.assertEqual(first["session"], chat)
        second = self.dispatch("1", "--agent", "cursor", "--followup", FAKE_FILE="more.txt")
        self.assertEqual(second["outcome"], "done")
        follow_argv = self.argv_log()[-1]
        self.assertEqual(follow_argv[follow_argv.index("--resume") + 1], chat)
        self.assertFalse(follow_argv[-1].startswith("/delegate-tdd"))

    def test_cursor_create_chat_that_never_exits(self):
        self.init()
        out = self.dispatch("1", "--agent", "cursor", FAKE_CHAT_HANG="1")
        self.assertEqual(out["outcome"], "done")

    def test_cursor_sandbox_replaces_force(self):
        self.init()
        self.dispatch("1", "--agent", "cursor", "--sandbox")
        argv = self.argv_log()[-1]
        self.assertNotIn("--force", argv)
        self.assertIn("enabled", argv)

    def test_cursor_cli_error(self):
        self.init()
        out = self.dispatch("1", "--agent", "cursor", expect_ok=False, FAKE_MODE="error")
        self.assertEqual(out["outcome"], "cli_failed")
        self.assertIn("invalid model", out["stderr_tail"])

    def test_watchdog_stops_cli_that_hangs_after_finishing(self):
        self.init()
        out = self.dispatch("1", "--agent", "cursor", FAKE_MODE="hang")
        self.assertEqual(out["outcome"], "done")
        self.assertEqual(out["stopped"], "after_done")
        self.assertLess(out["elapsed_seconds"], 30)

    def test_untracked_files_make_dispatch_dirty(self):
        self.init()
        out = self.dispatch(expect_ok=False, FAKE_UNTRACKED="1")
        self.assertEqual(out["outcome"], "dirty")
        self.assertEqual(out["untracked"], ["scratch.tmp"])

    def test_followup_requires_previous_session(self):
        self.init()
        out = self.dispatch("1", "--followup", expect_ok=False)
        self.assertIn("--followup", out["error"])


class LandAndAudit(Harness):
    def test_land_requires_successful_verification_of_current_head(self):
        self.init()
        self.dispatch()
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "not_verified")
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        self.dispatch("1", "--followup", FAKE_FILE="more.txt")
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "not_verified")
        self.delegate("verify", "--run", self.run_id, "--ticket", "1", "--cmd", "false", expect_ok=False)
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "not_verified")
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        self.assertEqual(self.delegate("land", "--run", self.run_id, "--ticket", "1")["outcome"], "landed")

    def test_failed_verification_invalidates_previous_success_at_same_head(self):
        self.init()
        self.dispatch()
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        self.delegate("verify", "--run", self.run_id, "--ticket", "1", "--cmd", "false", expect_ok=False)
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "not_verified")

    def test_land_verify_audit_finish(self):
        self.init()
        self.dispatch("1")
        self.assertTrue(self.delegate("verify", "--run", self.run_id, "--ticket", "1")["ok"])
        landed = self.delegate("land", "--run", self.run_id, "--ticket", "1")
        self.assertEqual(landed["outcome"], "landed")
        self.assertTrue(self.delegate("verify", "--run", self.run_id)["ok"])
        audit = self.delegate("audit", "--run", self.run_id)
        self.assertEqual(audit["commits"], 1)
        fin = self.delegate("finish", "--run", self.run_id)
        self.assertEqual(fin["kept"], [])
        self.assertFalse(self.delegate("status", "--run", self.run_id)["active"])

    def test_land_needs_merge_then_followup_merge_lands(self):
        self.init()
        self.dispatch("1")
        self.dispatch("2", FAKE_FILE="two.txt")
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        self.delegate("land", "--run", self.run_id, "--ticket", "1")
        out = self.delegate("land", "--run", self.run_id, "--ticket", "2", expect_ok=False)
        self.assertEqual(out["outcome"], "needs_merge")
        merged = self.dispatch("2", "--followup", FAKE_MODE="merge",
                               FAKE_MERGE_REF="delegate/spec-1/integration")
        self.assertEqual(merged["outcome"], "done")
        self.assertEqual(len(merged["commits"]), 1, "only the merge commit is ticket 2's own")
        self.delegate("verify", "--run", self.run_id, "--ticket", "2")
        self.assertEqual(self.delegate("land", "--run", self.run_id, "--ticket", "2")["outcome"], "landed")
        self.assertEqual(self.delegate("audit", "--run", self.run_id)["commits"], 3)

    def test_importing_an_unlanded_ticket_preserves_ownership_and_blocks_landing(self):
        self.init()
        other = self.dispatch("2", FAKE_FILE="two.txt")
        imported = self.dispatch("1", expect_ok=False, FAKE_MODE="merge", FAKE_MERGE_REF="delegate/spec-1/2")
        self.assertEqual(imported["outcome"], "foreign_commits")
        self.assertEqual(imported["commits"], [])
        self.assertEqual(imported["foreign_commits"], other["commits"])
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "foreign_commits")
        self.delegate("verify", "--run", self.run_id, "--ticket", "2")
        self.delegate("land", "--run", self.run_id, "--ticket", "2")
        self.assertEqual(self.delegate("audit", "--run", self.run_id)["by_agent_ticket"], {"pi:2": other["commits"]})

    def test_imported_unrelated_history_is_not_added_to_ledger(self):
        self.sh("git", "switch", "-q", "-c", "unrelated")
        (self.repo / "unrelated.txt").write_text("existing work\n")
        self.sh("git", "add", "unrelated.txt")
        self.sh("git", "commit", "-q", "-m", "unrelated")
        sha = self.sh("git", "rev-parse", "HEAD")
        self.sh("git", "switch", "-q", "main")
        self.init()
        out = self.dispatch(expect_ok=False, FAKE_MODE="merge", FAKE_MERGE_REF="unrelated")
        self.assertEqual(out["outcome"], "foreign_commits")
        self.assertEqual(out["commits"], [])
        self.assertEqual(out["foreign_commits"], [sha])
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "unaudited_commits")

    def test_land_refuses_commit_not_made_by_an_agent(self):
        self.init()
        out = self.dispatch("1")
        wt = Path(out["worktree"])
        (wt / "sneaky.txt").write_text("by hand\n")
        self.sh("git", "add", "sneaky.txt", cwd=wt)
        self.sh("git", "commit", "-q", "-m", "hand-written", cwd=wt)
        refused = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "tampered")
        restored = self.delegate("restore", "--run", self.run_id, "--ticket", "1")
        self.assertEqual(restored["reset_to"], out["head_after"])
        self.delegate("verify", "--run", self.run_id, "--ticket", "1")
        landed = self.delegate("land", "--run", self.run_id, "--ticket", "1")
        self.assertEqual(landed["commits"], out["commits"])

    def test_files_written_outside_an_agent_block_the_next_dispatch(self):
        self.init()
        out = self.dispatch("1")
        (Path(out["worktree"]) / "by_hand.py").write_text("print('not from an agent')\n")
        refused = self.dispatch("1", "--followup", expect_ok=False)
        self.assertEqual(refused["outcome"], "tampered")
        verify = self.delegate("verify", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(verify["outcome"], "tampered")
        self.delegate("restore", "--run", self.run_id, "--ticket", "1")
        self.assertFalse((Path(out["worktree"]) / "by_hand.py").exists())
        self.assertEqual(self.dispatch("1", "--followup", FAKE_FILE="more.txt")["outcome"], "done")

    def test_content_edit_to_already_dirty_file_is_tampering(self):
        self.init()
        out = self.dispatch(expect_ok=False, FAKE_MODE="dirty")
        wt = Path(out["worktree"])
        before = self.sh("git", "status", "--porcelain", cwd=wt)
        (wt / "README.md").write_text("manual edit\n")
        self.assertEqual(before, self.sh("git", "status", "--porcelain", cwd=wt))
        refused = self.dispatch("1", "--followup", expect_ok=False)
        self.assertEqual(refused["outcome"], "tampered")

    def test_non_utf8_content_can_be_fingerprinted(self):
        self.init()
        out = self.dispatch()
        self.delegate("verify", "--run", self.run_id, "--ticket", "1", "--cmd", "printf '\\377\\n' > README.md")
        (Path(out["worktree"]) / "README.md").write_bytes(b"\xfe\n")
        refused = self.delegate("verify", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "tampered")

    def test_whitespace_only_content_tampering_is_detected(self):
        self.init()
        out = self.dispatch()
        self.delegate("verify", "--run", self.run_id, "--ticket", "1", "--cmd", "printf 'hello\\n \\n' > README.md")
        (Path(out["worktree"]) / "README.md").write_bytes(b"hello\n  \n")
        refused = self.delegate("verify", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "tampered")

    def test_content_edit_to_existing_untracked_file_is_tampering(self):
        self.init()
        out = self.dispatch()
        self.delegate("verify", "--run", self.run_id, "--ticket", "1", "--cmd", "touch artifact.txt")
        (Path(out["worktree"]) / "artifact.txt").write_text("manual edit\n")
        refused = self.delegate("verify", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(refused["outcome"], "tampered")

    def test_verify_output_does_not_count_as_tampering(self):
        self.init()
        self.dispatch("1")
        self.delegate("verify", "--run", self.run_id, "--ticket", "1", "--cmd", "touch build-artifact.txt")
        self.assertEqual(self.delegate("land", "--run", self.run_id, "--ticket", "1")["outcome"], "landed")

    def test_audit_flags_commit_made_on_integration_directly(self):
        out = self.init()
        wt = Path(out["integration_worktree"])
        (wt / "x.txt").write_text("x\n")
        self.sh("git", "add", "x.txt", cwd=wt)
        self.sh("git", "commit", "-q", "-m", "direct", cwd=wt)
        audit = self.delegate("audit", "--run", self.run_id, expect_ok=False)
        self.assertEqual(len(audit["unaudited"]), 1)
        self.dispatch("1")
        land = self.delegate("land", "--run", self.run_id, "--ticket", "1", expect_ok=False)
        self.assertEqual(land["outcome"], "tampered")

    def test_max_parallel_is_enforced(self):
        self.init()
        state = self.tmp / "state/runs" / self.run_id / "run.json"
        run = json.loads(state.read_text())
        run["running"] = {"a": {"pid": os.getpid()}, "b": {"pid": os.getpid()}}
        state.write_text(json.dumps(run))
        out = self.dispatch("3", expect_ok=False)
        self.assertIn("max_parallel", out["error"])


class Preflight(Harness):
    def test_preflight_ok(self):
        for agent in ("pi", "cursor"):
            out = self.delegate("preflight", "--agent", agent)
            self.assertTrue(out.get("model_listed"), out)

    def test_preflight_reports_missing_model_and_login(self):
        (self.repo / "docs/agents/delegate-spec.json").write_text("{}")
        out = self.delegate("preflight", "--agent", "cursor", expect_ok=False, FAKE_AUTH="0",
                            CURSOR_API_KEY="")
        problems = " ".join(out["problems"])
        self.assertIn("no model configured", problems)
        self.assertIn("not logged in", problems)

    def test_preflight_missing_binary(self):
        out = self.delegate("preflight", "--agent", "pi", expect_ok=False, DELEGATE_PI_BIN="no-such-pi")
        self.assertIn("not on PATH", out["problems"][0])


class GuardHookCommand(unittest.TestCase):
    def test_skill_hook_and_settings_snippet_run_the_same_command(self):
        import re
        skill = (HERE.parent / "SKILL.md").read_text()
        in_skill = re.search(r"command: '(.*)'\n", skill).group(1)
        sys.path.insert(0, str(SCRIPTS))
        import delegate
        self.assertEqual(in_skill, delegate.GUARD_COMMAND)


class Guard(Harness):
    def guard(self, tool, tool_input, cwd=None):
        event = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd or self.repo)}
        proc = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(event), env=self.env,
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        if not proc.stdout.strip():
            return "allow"
        return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"]

    def test_guard_blocks_writes_in_worktrees_only_while_active(self):
        wt = Path(self.init()["integration_worktree"])
        self.assertEqual(self.guard("Edit", {"file_path": str(wt / "a.go")}), "deny")
        self.assertEqual(self.guard("Write", {"file_path": str(self.tmp / "notes.md")}), "allow")
        self.assertEqual(self.guard("Bash", {"command": "git commit -am x"}, cwd=wt), "deny")
        self.assertEqual(self.guard("Bash", {"command": f"echo hi > {wt}/f"}), "deny")
        self.assertEqual(self.guard("Bash", {"command": "git log --oneline 2>/dev/null"}, cwd=wt), "allow")
        self.assertEqual(self.guard("Bash", {"command": "python3 delegate.py land --run x"}, cwd=wt), "allow")
        self.assertEqual(self.guard("Bash", {"command": "git commit -am x"}), "allow")
        self.delegate("finish", "--run", self.run_id, "--abort")
        self.assertEqual(self.guard("Edit", {"file_path": str(wt / "a.go")}), "allow")

    def test_runner_name_does_not_exempt_shell_writes(self):
        wt = Path(self.init()["integration_worktree"])
        commands = [
            f"touch {wt}/x # delegate.py",
            f"echo delegate.py > {wt}/x",
            f"python3 {DELEGATE} status --run {self.run_id}; touch {wt}/x",
            f"python3 {DELEGATE} status --run {self.run_id} && git -C {wt} commit -am x",
        ]
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.guard("Bash", {"command": command}), "deny")
        self.assertEqual(self.guard("Bash", {"command": f"python3 {DELEGATE} land --run x"}, cwd=wt), "allow")

    def test_guard_allows_everything_without_runs(self):
        self.assertEqual(self.guard("Write", {"file_path": str(self.repo / "x")}), "allow")


if __name__ == "__main__":
    unittest.main()
