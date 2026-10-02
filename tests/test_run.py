"""Focused CLI boundary and recovery tests for the stdlib native runner."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "skills" / "mozglo-orchestrator" / "scripts" / "run.py"
spec = importlib.util.spec_from_file_location("mozglo_run", RUNNER)
runner = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(runner)


FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, subprocess, sys, time
args = sys.argv[1:]
trace = os.environ.get("FAKE_TRACE")
if trace:
    with open(trace, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"args": args, "cwd": os.getcwd(), "prompt": sys.stdin.read()}) + "\n")
else:
    sys.stdin.read()
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "error":
    print("simulated provider error: 403 Forbidden", file=sys.stderr, flush=True)
    raise SystemExit(9)
if mode == "long":
    print(json.dumps({"type":"thread.started","thread_id":"11111111-2222-4333-8444-555555555555"}), flush=True)
    marker = os.environ["FAKE_HEARTBEAT"]
    while True:
        with open(marker, "a", encoding="ascii") as stream:
            stream.write("beat\n")
        time.sleep(0.08)
if mode == "orphan":
    child = "import os,time; p=os.environ['FAKE_HEARTBEAT'];\nwhile True:\n open(p,'a').write('beat\\n'); time.sleep(0.05)"
    subprocess.Popen([sys.executable, "-c", child], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(json.dumps({"type":"thread.started","thread_id":"11111111-2222-4333-8444-555555555555"}), flush=True)
    while True:
        time.sleep(1)
if mode == "success_child":
    child = "import os,time; p=os.environ['FAKE_HEARTBEAT'];\nfor _ in range(1000):\n open(p,'a').write('beat\\n'); time.sleep(0.05)"
    worker = subprocess.Popen([sys.executable, "-c", child], start_new_session=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with open(os.environ["FAKE_CHILD_PID"], "w", encoding="ascii") as stream:
        stream.write(str(worker.pid))
if mode != "no_uuid":
    print(json.dumps({"type":"thread.started","thread_id":"11111111-2222-4333-8444-555555555555"}), flush=True)
if mode == "fatal" or (mode == "fatal_first" and "resume" not in args):
    print(json.dumps({"type":"turn.failed","error":"simulated fatal turn"}), flush=True)
print(json.dumps({"type":"turn.completed","usage":{"input_tokens":1}}), flush=True)
if "-o" in args:
    output = args[args.index("-o") + 1]
    with open(output, "w", encoding="utf-8") as stream:
        stream.write("fake result\n")
'''


class NativeRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="mozglo-run-")
        self.base = Path(self.tmp.name)
        self.workspace = self.base / "workspace with spaces"
        self.workspace.mkdir()
        self.state = self.base / "private state"
        self.codex_home = self.base / "codex"
        self.codex_home.mkdir()
        self.config_home = self.codex_home / "config.toml"
        self.config_home.write_text("model = 'test'\n", encoding="utf-8")
        self.fake = self.base / "fake codex"
        self.fake.write_text(FAKE_CLI, encoding="utf-8")
        self.fake.chmod(0o755)
        self.trace = self.base / "trace.jsonl"
        self.env = os.environ.copy()
        self.env.update({"CODEX_HOME": str(self.codex_home), "FAKE_TRACE": str(self.trace),
                         "XDG_STATE_HOME": str(self.base / "xdg-state")})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_runner(self, *args: str, env: dict[str, str] | None = None, timeout: float = 10) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(RUNNER), *map(str, args)], cwd=ROOT,
                              env=env or self.env, text=True, capture_output=True, timeout=timeout)

    def start_args(self, *extra: str) -> list[str]:
        return ["start", "--workspace", str(self.workspace), "--state-dir", str(self.state),
                "--codex", str(self.fake), "--prompt", "bounded probe", *extra]

    def record(self) -> dict:
        return json.loads((self.state / "run.json").read_text(encoding="utf-8"))

    def test_allocation_budget_and_prompt_route(self) -> None:
        settings = runner.load_settings()
        default = runner.allocation(settings)
        self.assertEqual((default["total_agents"], default["sols"], default["lunas_per_sol"]), (7, 1, 5))
        self.assertEqual(runner.allocation(settings, sols=2, lunas=6)["total_agents"], 15)
        with self.assertRaisesRegex(runner.RunnerError, "configured maximum is 15"):
            runner.allocation(settings, sols=2, lunas=7)
        direct = runner.allocation(settings, sols=0, direct_lunas=1)
        prompt = runner.make_task_prompt("fix one bug", settings, direct)
        self.assertIn("do not create Sol managers", prompt)
        self.assertIn("fork_turns to none", prompt)
        self.assertIn(".mozglo/context.md", prompt)

    def test_start_resume_uses_explicit_uuid_workspace_profile_and_sandbox(self) -> None:
        profile = self.codex_home / "qa.config.toml"
        profile.write_text("model = 'qa'\n", encoding="utf-8")
        settings_file = self.base / "settings.json"
        settings_file.write_text(json.dumps({
            "models": {"astra": "root-test", "sol": "manager-test", "luna": "worker-test"},
            "reasoning_effort": {"astra": "low", "sol": "medium", "luna": "high"},
            "allocation": {"sols": 1, "lunas_per_sol": 1, "direct_lunas": 1, "max_agents": 15},
            "profile": "qa", "sandbox": "workspace-write",
        }), encoding="utf-8")
        start = self.run_runner(*self.start_args("--settings", str(settings_file)))
        self.assertEqual(start.returncode, 0, start.stderr)
        state = self.record()
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["session_uuid"], "11111111-2222-4333-8444-555555555555")
        self.assertEqual(state["requested_models"], {"astra": "root-test", "sol": "manager-test", "luna": "worker-test"})
        self.assertIsNone(state["effective_model_observed"])
        first_call = json.loads(self.trace.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(first_call["cwd"], str(self.workspace))
        self.assertIn("--skip-git-repo-check", first_call["args"])
        self.assertLess(first_call["args"].index("-C"), first_call["args"].index("--json"))
        self.assertIn("workspace-write", first_call["args"])
        self.assertIn("qa", first_call["args"])
        self.assertNotIn("bounded probe", first_call["args"])

        resume = self.run_runner("resume", "--workspace", str(self.workspace), "--state-dir", str(self.state))
        self.assertEqual(resume.returncode, 0, resume.stderr)
        calls = [json.loads(line) for line in self.trace.read_text(encoding="utf-8").splitlines()]
        resumed = calls[1]
        self.assertEqual(resumed["cwd"], str(self.workspace))
        self.assertLess(resumed["args"].index("-C"), resumed["args"].index("resume"))
        self.assertLess(resumed["args"].index("workspace-write"), resumed["args"].index("resume"))
        self.assertLess(resumed["args"].index("qa"), resumed["args"].index("resume"))
        self.assertIn(state["session_uuid"], resumed["args"])
        self.assertNotIn("--last", resumed["args"])
        self.assertEqual(self.record()["resume_count"], 1)
        self.assertTrue((self.state / "history").is_dir())

    def test_resume_refuses_workspace_and_config_drift(self) -> None:
        start = self.run_runner(*self.start_args())
        self.assertEqual(start.returncode, 0, start.stderr)
        wrong = self.base / "other"
        wrong.mkdir()
        mismatch = self.run_runner("resume", "--workspace", str(wrong), "--state-dir", str(self.state))
        self.assertEqual(mismatch.returncode, 2)
        self.config_home.write_text("model = 'changed'\n", encoding="utf-8")
        drift = self.run_runner("resume", "--workspace", str(self.workspace), "--state-dir", str(self.state))
        self.assertEqual(drift.returncode, 2)
        self.assertIn("config changed", drift.stderr)
        self.assertEqual(len(self.trace.read_text(encoding="utf-8").splitlines()), 1)

    def test_no_uuid_and_provider_error_remain_failed_with_logs(self) -> None:
        no_uuid_env = self.env | {"FAKE_MODE": "no_uuid"}
        missing = self.run_runner(*self.start_args(), env=no_uuid_env)
        self.assertEqual(missing.returncode, 1)
        state = self.record()
        self.assertEqual(state["status"], "failed")
        self.assertIsNone(state["session_uuid"])
        self.assertIn("without a session UUID", state["error"])
        self.assertTrue(Path(state["events_path"]).exists())

        self.state = self.base / "second state"
        failed = self.run_runner(*self.start_args(), env=self.env | {"FAKE_MODE": "error"})
        self.assertEqual(failed.returncode, 9)
        state = self.record()
        self.assertEqual(state["status"], "failed")
        self.assertIn("inspect the private event log", state["error"])
        self.assertIn("403 Forbidden", Path(state["events_path"]).read_text(encoding="utf-8"))

        self.state = self.base / "fatal state"
        fatal = self.run_runner(*self.start_args(), env=self.env | {"FAKE_MODE": "fatal"})
        self.assertEqual(fatal.returncode, 1)
        self.assertEqual(self.record()["status"], "failed")
        self.assertTrue(self.record()["fatal_turn_error"])

    def test_live_writer_is_locked_stopped_and_not_restarted(self) -> None:
        heartbeat = self.base / "heartbeat.txt"
        env = self.env | {"FAKE_MODE": "long", "FAKE_HEARTBEAT": str(heartbeat)}
        owner = subprocess.Popen([sys.executable, str(RUNNER), *self.start_args()], cwd=ROOT, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not heartbeat.exists() or not (self.state / "run.json").exists()):
            time.sleep(0.05)
        self.assertTrue(heartbeat.exists(), "fake CLI never started its writer")
        second = self.run_runner(*self.start_args())
        self.assertEqual(second.returncode, 2)
        self.assertIn("another MOZGLO CLI run owns", second.stderr)
        stop = self.run_runner("stop", "--state-dir", str(self.state), "--grace", "0.5")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        owner.wait(timeout=5)
        self.assertEqual(owner.returncode, 130)
        before = heartbeat.read_text(encoding="ascii")
        time.sleep(0.3)
        self.assertEqual(heartbeat.read_text(encoding="ascii"), before)
        self.assertEqual(self.record()["status"], "interrupted")

    def test_stop_cleans_owned_adopted_group(self) -> None:
        heartbeat = self.base / "detached-heartbeat.txt"
        env = self.env | {"FAKE_MODE": "orphan", "FAKE_HEARTBEAT": str(heartbeat)}
        owner = subprocess.Popen([sys.executable, str(RUNNER), *self.start_args()], cwd=ROOT, env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (not heartbeat.exists() or len(heartbeat.read_text().splitlines()) < 3):
            time.sleep(0.05)
        self.assertTrue(heartbeat.exists(), "detached fake tool child never started")
        before = len(heartbeat.read_text().splitlines())
        stop = self.run_runner("stop", "--state-dir", str(self.state), "--grace", "0.5")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        owner.wait(timeout=5)
        self.assertEqual(owner.returncode, 130)
        settled = len(heartbeat.read_text().splitlines())
        time.sleep(0.3)
        self.assertGreaterEqual(settled, before)
        self.assertEqual(len(heartbeat.read_text().splitlines()), settled)
        record = self.record()
        self.assertEqual(record["status"], "interrupted")
        self.assertTrue(record["adopted_process_groups_stopped"])

    def test_successful_turn_preserves_intentional_background_work(self) -> None:
        heartbeat = self.base / "background-heartbeat.txt"
        child_pid_file = self.base / "background-pid.txt"
        env = self.env | {"FAKE_MODE": "success_child", "FAKE_HEARTBEAT": str(heartbeat),
                          "FAKE_CHILD_PID": str(child_pid_file)}
        result = self.run_runner(*self.start_args(), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        record = self.record()
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["adopted_process_groups_stopped"], [])
        pid = int(child_pid_file.read_text(encoding="ascii"))
        info = runner._proc_info(pid)
        self.assertIsNotNone(info)
        pgrp, session, birth = int(info["pgrp"]), int(info["session"]), int(info["birth"])
        before = len(heartbeat.read_text().splitlines())
        time.sleep(0.2)
        self.assertGreater(len(heartbeat.read_text().splitlines()), before)
        current = runner._proc_info(pid)
        self.assertIsNotNone(current)
        self.assertEqual((current["birth"], current["pgrp"], current["session"]), (birth, pgrp, session))
        os.killpg(pgrp, signal.SIGTERM)

    def test_stop_refuses_stale_or_reused_pid(self) -> None:
        self.state.mkdir(mode=0o700)
        pid = os.getpid()
        info = runner._proc_info(pid)
        self.assertIsNotNone(info)
        (self.state / "run.json").write_text(json.dumps({
            "workspace": str(self.workspace), "pid": pid, "process_group": pid,
            "process_birth": info["birth"] + 1, "status": "running",
        }), encoding="utf-8")
        result = self.run_runner("stop", "--state-dir", str(self.state))
        self.assertEqual(result.returncode, 2)
        self.assertIn("refusing to signal", result.stderr)
        self.assertIsNone(runner._proc_info(-1))

    def test_state_directory_cannot_switch_workspace(self) -> None:
        start = self.run_runner(*self.start_args())
        self.assertEqual(start.returncode, 0, start.stderr)
        other = self.base / "other workspace"
        other.mkdir()
        result = self.run_runner("start", "--workspace", str(other), "--state-dir", str(self.state),
                                 "--codex", str(self.fake), "--prompt", "must not start")
        self.assertEqual(result.returncode, 2)
        self.assertIn("different workspace", result.stderr)
        self.assertEqual(len(self.trace.read_text(encoding="utf-8").splitlines()), 1)

    def test_failed_uuid_run_requires_resume_and_clears_turn_error(self) -> None:
        env = self.env | {"FAKE_MODE": "fatal_first"}
        first = self.run_runner(*self.start_args(), env=env)
        self.assertEqual(first.returncode, 1)
        self.assertEqual(self.record()["status"], "failed")
        self.assertTrue(self.record()["session_uuid"])
        blocked = self.run_runner(*self.start_args(), env=env)
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("reconcile it with resume/status", blocked.stderr)
        resumed = self.run_runner("resume", "--workspace", str(self.workspace), "--state-dir", str(self.state), env=env)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        record = self.record()
        self.assertEqual(record["status"], "completed")
        self.assertNotIn("fatal_turn_error", record)
        self.assertIsNone(record.get("error"))
        self.assertEqual(len(self.trace.read_text(encoding="utf-8").splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
