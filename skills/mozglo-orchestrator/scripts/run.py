#!/usr/bin/env python3
"""Thin, recoverable launcher for one native Codex agent tree."""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator
import uuid


SETTINGS_FILE = Path(__file__).resolve().parents[1] / "settings.json"
VALID_EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
MAX_SOL = 3
MAX_AGENTS = 15
PR_SET_CHILD_SUBREAPER = 36
_STOP_REQUESTED = False


class RunnerError(Exception):
    pass


def _mark_stop_requested(_signum: int, _frame: Any) -> None:
    global _STOP_REQUESTED
    _STOP_REQUESTED = True


def _install_stop_notification() -> None:
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, _mark_stop_requested)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerError(f"cannot read JSON settings/state at {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RunnerError(f"expected a JSON object at {path}")
    return value


def load_settings(path: Path = SETTINGS_FILE) -> dict[str, Any]:
    raw = _read_json(path)
    models = raw.get("models", {})
    effort = raw.get("reasoning_effort", {})
    allocation = raw.get("allocation", {})
    if not isinstance(models, dict) or not isinstance(effort, dict) or not isinstance(allocation, dict):
        raise RunnerError("models, reasoning_effort, and allocation must be JSON objects")
    settings = {
        "models": {role: str(models.get(role, "")).strip() for role in ("astra", "sol", "luna")},
        "reasoning_effort": {role: str(effort.get(role, "")).strip() for role in ("astra", "sol", "luna")},
        "allocation": {
            "sols": allocation.get("sols", 1),
            "lunas_per_sol": allocation.get("lunas_per_sol", 5),
            "direct_lunas": allocation.get("direct_lunas", 1),
        },
        "profile": raw.get("profile"),
        "sandbox": raw.get("sandbox"),
    }
    for role, model in settings["models"].items():
        if not model or any(ch.isspace() for ch in model):
            raise RunnerError(f"models.{role} must be a non-empty model id")
    for role, value in settings["reasoning_effort"].items():
        if value not in VALID_EFFORTS:
            raise RunnerError(f"reasoning_effort.{role} must be one of {sorted(VALID_EFFORTS)}")
    if settings["profile"] is not None and (not isinstance(settings["profile"], str) or not settings["profile"].strip()):
        raise RunnerError("profile must be null or a non-empty profile name")
    if settings["sandbox"] not in {None, "read-only", "workspace-write"}:
        raise RunnerError("sandbox must be null, read-only, or workspace-write")
    max_agents = allocation.get("max_agents", MAX_AGENTS)
    if not isinstance(max_agents, int) or isinstance(max_agents, bool) or not 2 <= max_agents <= MAX_AGENTS:
        raise RunnerError(f"allocation.max_agents must be an integer from 2 to {MAX_AGENTS}")
    settings["allocation"]["max_agents"] = max_agents
    return settings


def allocation(settings: dict[str, Any], sols: int | None = None, lunas: int | None = None,
               direct_lunas: int | None = None) -> dict[str, int]:
    defaults = settings["allocation"]
    sol_count = defaults["sols"] if sols is None else sols
    luna_count = defaults["lunas_per_sol"] if lunas is None else lunas
    direct_count = defaults["direct_lunas"] if direct_lunas is None else direct_lunas
    if not isinstance(sol_count, int) or isinstance(sol_count, bool) or not 0 <= sol_count <= MAX_SOL:
        raise RunnerError(f"Sol count must be an integer from 0 to {MAX_SOL}")
    if not isinstance(luna_count, int) or isinstance(luna_count, bool) or luna_count < 1:
        raise RunnerError("Luna count must be a positive integer")
    if not isinstance(direct_count, int) or isinstance(direct_count, bool) or direct_count < 1:
        raise RunnerError("direct_lunas must be a positive integer when running without Sol managers")
    children = sol_count + sol_count * luna_count if sol_count else direct_count
    max_agents = defaults.get("max_agents", MAX_AGENTS)
    if children + 1 > min(MAX_AGENTS, max_agents):
        raise RunnerError(f"requested tree needs {children + 1} agents including Astra; configured maximum is {min(MAX_AGENTS, max_agents)}")
    return {"sols": sol_count, "lunas_per_sol": luna_count, "direct_lunas": direct_count,
            "native_children": children, "total_agents": children + 1}


def make_task_prompt(task: str, settings: dict[str, Any], alloc: dict[str, int]) -> str:
    if alloc["sols"]:
        roster = (
            f"Spawn exactly {alloc['sols']} direct Sol manager(s), names manager_1..manager_{alloc['sols']}, "
            f"model {settings['models']['sol']}, reasoning effort {settings['reasoning_effort']['sol']}. "
            f"Each Sol spawns exactly {alloc['lunas_per_sol']} Luna executor(s), names luna_01..luna_{alloc['lunas_per_sol']}, "
            f"model {settings['models']['luna']}, reasoning effort {settings['reasoning_effort']['luna']}."
        )
    else:
        roster = (
            f"This is a small task: spawn exactly {alloc['direct_lunas']} direct Luna executor, "
            f"model {settings['models']['luna']}, reasoning effort {settings['reasoning_effort']['luna']}; do not create Sol managers."
        )
    return (
        "You are the single Astra execution root for this run. Use only Codex native agent tools for delegation and live "
        "communication; do not launch this runner, another Codex CLI, a custom pool, or a custom message bus. "
        "Use compact briefs with only the task, applicable project instructions, edit scope, and acceptance checks; "
        "do not copy long conversation history. " + roster + " Sol plans and coordinates; Luna owns product implementation. "
        "Set fork_turns to none for every new agent. Use native messages to share decisions across branches. Wait for all "
        "workers to finish before your final report. Use only `.mozglo/context.md` for compact project memory: read it "
        "if it exists, preserve approvals and current decisions, mark superseded decisions, and update verified progress, "
        "checkpoints, and next step without replacing useful prior memory. Preserve "
        "approvals and scope, inspect existing work before retrying, and do not duplicate unfinished work. Report requested "
        "role models separately from any model identity "
        "the runtime actually exposes.\n\nUser task:\n" + task.strip()
    )


def _codex_config_fingerprint(profile: str | None = None, workspace: Path | None = None) -> dict[str, str | None]:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    candidates = [codex_home / "config.toml"]
    if profile:
        candidates.append(codex_home / f"{profile}.config.toml")
    if workspace:
        candidates.append(workspace / ".codex" / "config.toml")
    hashes: dict[str, str | None] = {}
    for path in candidates:
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            digest = None
        except OSError as exc:
            raise RunnerError(f"cannot fingerprint Codex settings file {path}: {exc}") from exc
        hashes[str(path.resolve())] = digest
    return hashes


def _workspace_state_dir(workspace: Path, override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")).expanduser()
    key = hashlib.sha256(str(workspace).encode("utf-8")).hexdigest()[:20]
    return (base / "mozglo" / key).resolve()


def _secure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise RunnerError(f"cannot secure state directory {path}: {exc}") from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    _secure_dir(path.parent)
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=".run-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        path.chmod(0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def _proc_info(pid: int) -> dict[str, int | str] | None:
    """Read Linux process birth identity and group without trusting PID alone."""
    try:
        content = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    close = content.rfind(")")
    if close < 0:
        return None
    fields = content[close + 2:].split()  # starts at field 3 (state)
    try:
        return {"state": fields[0], "ppid": int(fields[1]), "pgrp": int(fields[2]),
                "session": int(fields[3]), "birth": int(fields[19])}
    except (IndexError, ValueError):
        return None


def _matches_owned_process(record: dict[str, Any]) -> bool:
    try:
        pid = int(record["pid"])
        expected_birth = int(record["process_birth"])
        expected_group = int(record["process_group"])
    except (KeyError, TypeError, ValueError):
        return False
    info = _proc_info(pid)
    return bool(info and info["state"] not in {"Z", "X"} and info["birth"] == expected_birth and info["pgrp"] == expected_group == pid)


def _enable_subreaper() -> None:
    if not sys.platform.startswith("linux"):
        raise RunnerError("owned worker cleanup requires Linux /proc and child-subreaper support")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        raise RunnerError(f"could not enable child-subreaper mode: {os.strerror(ctypes.get_errno())}")


def _adopted_groups() -> list[int]:
    """Stop only new-session groups whose leaders were adopted by this run owner."""
    own_pid, own_pgrp, own_session = os.getpid(), os.getpgrp(), os.getsid(0)
    groups: dict[tuple[int, int], tuple[int, int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        info = _proc_info(pid)
        if not info or info["ppid"] != own_pid or info["state"] in {"Z", "X"}:
            continue
        pgrp, session = int(info["pgrp"]), int(info["session"])
        if pgrp != own_pgrp and session != own_session:
            groups[(pgrp, session)] = (pid, int(info["birth"]))
    stopped: list[int] = []
    for (pgrp, session), (pid, birth) in groups.items():
        info = _proc_info(pid)
        if not info or info["ppid"] != own_pid or info["birth"] != birth or info["pgrp"] != pgrp or info["session"] != session:
            continue
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
            members = []
            for entry in Path("/proc").iterdir():
                if entry.name.isdigit():
                    child = _proc_info(int(entry.name))
                    if child and child["state"] not in {"Z", "X"} and child["pgrp"] == pgrp and child["session"] == session:
                        members.append(int(entry.name))
            if not members:
                break
            current = _proc_info(pid)
            if not current or current["birth"] != birth or current["pgrp"] != pgrp or current["session"] != session:
                break
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgrp, sig)
            deadline = time.monotonic() + (0.4 if sig != signal.SIGKILL else 0.1)
            while time.monotonic() < deadline:
                if not any((_proc_info(child) or {}).get("pgrp") == pgrp for child in members):
                    break
                time.sleep(0.05)
        stopped.append(pgrp)
    return stopped


@contextlib.contextmanager
def _owner_lock(state_dir: Path) -> Iterator[None]:
    _secure_dir(state_dir)
    lock_path = state_dir / "owner.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    os.fchmod(fd, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunnerError("another MOZGLO CLI run owns this workspace; use status, stop, or resume") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _state_paths(state_dir: Path) -> tuple[Path, Path, Path]:
    return state_dir / "run.json", state_dir / "events.jsonl", state_dir / "last-message.txt"


def _load_run(state_dir: Path) -> dict[str, Any]:
    run_path, _, _ = _state_paths(state_dir)
    if not run_path.exists():
        raise RunnerError(f"no run record at {run_path}")
    return _read_json(run_path)


def _append_event(log, raw: bytes, record: dict[str, Any]) -> None:
    log.write(raw)
    log.flush()
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(event, dict):
        return
    changed = False
    if event.get("type") == "thread.started":
        thread_id = event.get("thread_id")
        if isinstance(thread_id, str) and thread_id:
            try:
                record["session_uuid"] = str(uuid.UUID(thread_id))
            except ValueError:
                record["invalid_session_id"] = True
            changed = True
    if event.get("type") in {"turn.failed", "thread.failed", "error"}:
        record["fatal_turn_error"] = True
        record["error"] = f"CLI reported fatal event {event['type']}"
        changed = True
    if event.get("type") == "turn.completed":
        record["saw_turn_completed"] = True
        record["effective_model_observed"] = event.get("model")
        changed = True
    if changed:
        record["_state_changed"] = True


def _base_cli_command(codex: str, workspace: Path, settings: dict[str, Any],
                      alloc: dict[str, int], state_dir: Path, resume_uuid: str | None,
                      sandbox: str | None = None) -> list[str]:
    _, _, last_message = _state_paths(state_dir)
    # `exec resume` takes its global options before the subcommand. In particular
    # its cwd and sandbox must match the original session before the UUID is read.
    common = [codex, "--no-daemon", "exec", "-C", str(workspace), "--skip-git-repo-check"]
    sandbox = sandbox or settings.get("sandbox")
    if sandbox:
        common.extend(["-s", sandbox])
    if settings.get("profile"):
        common.extend(["-p", settings["profile"]])
    common.extend([
        "-m", settings["models"]["astra"],
        "-c", "agents.enabled=true",
        "-c", f"agents.max_depth={2 if alloc['sols'] else 1}",
        "-c", f"agents.max_concurrent_threads_per_session={alloc['native_children']}",
        "-c", f"model_reasoning_effort={json.dumps(settings['reasoning_effort']['astra'])}",
    ])
    if resume_uuid:
        common.extend(["resume", resume_uuid, "--json", "-o", str(last_message)])
    else:
        common.extend(["--json", "-o", str(last_message)])
    common.append("-")
    return common


def _resume_prompt(run: dict[str, Any]) -> str:
    return (
        f"Resume the existing native Codex session `{run.get('session_uuid', '')}` from its durable conversation and "
        "checkpoints. Read `.mozglo/context.md` if it exists. First inspect the existing native agent roster and "
        "process/result state. Do not create replacement "
        "agents while prior workers may still be active. Continue only unfinished authorized work, preserve completed "
        "evidence, and report requested models separately from any effective identity actually exposed by the runtime."
    )


def _run_process(state_dir: Path, workspace: Path, settings: dict[str, Any], alloc: dict[str, int],
                 prompt: str, codex: str, resume_uuid: str | None, base_record: dict[str, Any]) -> int:
    run_path, events_path, last_message = _state_paths(state_dir)
    _secure_dir(state_dir)
    _secure_dir(events_path.parent / "history")
    archive = Path(base_record["log_archive"])
    if events_path.exists():
        os.replace(events_path, archive)
        archive.chmod(0o600)
    if last_message.exists():
        prior_turn = int(base_record.get("resume_count", 0))
        previous_result = events_path.parent / "history" / f"{base_record.get('run_id', 'run')}-result-before-{prior_turn}.txt"
        os.replace(last_message, previous_result)
        previous_result.chmod(0o600)
    for key in ("fatal_turn_error", "invalid_session_id", "effective_model_observed", "error"):
        base_record.pop(key, None)
    base_record["saw_turn_completed"] = False
    command = _base_cli_command(codex, workspace, settings, alloc, state_dir, resume_uuid,
                                base_record.get("config_snapshot", {}).get("sandbox_mode"))
    _enable_subreaper()
    # Never put the user task or credentials in argv. Codex reads one prompt from stdin.
    try:
        proc = subprocess.Popen(command, cwd=str(workspace), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True, bufsize=0)
    except OSError as exc:
        base_record.update({"status": "failed", "error": f"could not start Codex CLI: {exc}", "finished_at": time.time()})
        _atomic_json(run_path, base_record)
        raise RunnerError(base_record["error"]) from exc
    info = _proc_info(proc.pid)
    if info is None:
        proc.kill()
        proc.wait()
        base_record.update({"status": "failed", "error": "Linux /proc process identity unavailable; CLI was stopped safely", "finished_at": time.time()})
        _atomic_json(run_path, base_record)
        raise RunnerError(base_record["error"])
    base_record.update({"status": "running", "pid": proc.pid, "process_group": proc.pid,
                        "process_birth": info["birth"] if info else None, "started_at": time.time(),
                        "owner_pid": os.getpid(), "owner_birth": _proc_info(os.getpid())["birth"],
                        "session_uuid": resume_uuid, "events_path": str(events_path),
                        "result_path": str(last_message), "error": None, "saw_turn_completed": False})
    _atomic_json(run_path, base_record)
    log_fd = os.open(events_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(log_fd, 0o600)
    with os.fdopen(log_fd, "ab", buffering=0) as log:
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode("utf-8"))
            proc.stdin.close()
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                _append_event(log, raw, base_record)
                if base_record.pop("_state_changed", False):
                    _atomic_json(run_path, base_record)
            code = proc.wait()
        except KeyboardInterrupt:
            if _matches_owned_process(base_record):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if _matches_owned_process(base_record):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            code = proc.returncode if proc.returncode is not None else 130
            base_record["status"] = "interrupted"
        except BrokenPipeError:
            code = proc.wait()
        finally:
            if proc.poll() is None:
                if _matches_owned_process(base_record):
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)
    base_record["finished_at"] = time.time()
    successful_turn = bool(code == 0 and base_record.get("session_uuid") and base_record.get("saw_turn_completed")
                           and not base_record.get("fatal_turn_error") and not _STOP_REQUESTED)
    base_record["adopted_process_groups_stopped"] = [] if successful_turn else _adopted_groups()
    if base_record.get("status") != "interrupted" and not _STOP_REQUESTED:
        if (code == 0 and base_record.get("session_uuid") and base_record.get("saw_turn_completed")
                and not base_record.get("fatal_turn_error")):
            base_record["status"] = "completed"
        elif code in {-signal.SIGINT, -signal.SIGTERM, -signal.SIGKILL}:
            base_record["status"] = "interrupted"
        else:
            base_record["status"] = "failed"
            if code == 0:
                base_record["error"] = "CLI exited 0 without a session UUID and completed-turn event; see event log"
            else:
                base_record["error"] = f"CLI exited {code}; inspect the private event log at {events_path}"
    elif _STOP_REQUESTED:
        base_record["status"] = "interrupted"
        base_record["error"] = None
    base_record["exit_code"] = code
    base_record["last_message_exists"] = last_message.exists()
    if last_message.exists():
        last_message.chmod(0o600)
    _atomic_json(run_path, base_record)
    if base_record["status"] == "failed":
        print(f"mozglo: run failed; inspect private log {events_path}", file=sys.stderr)
    if code < 0:
        return 128 + abs(code)
    if code == 0 and base_record["status"] == "failed":
        return 1
    return code


def _settings_snapshot(settings: dict[str, Any], alloc: dict[str, int], workspace: Path) -> dict[str, Any]:
    return {"workspace": str(workspace), "settings": settings, "allocation": alloc,
            "codex_config_fingerprint": _codex_config_fingerprint(settings.get("profile"), workspace),
            "sandbox_mode": settings.get("sandbox"), "auth": "inherited from Codex environment/profile"}


def cmd_start(args: argparse.Namespace) -> int:
    try:
        workspace = Path(args.workspace).expanduser().resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"workspace does not exist or cannot be accessed: {exc}") from exc
    if not workspace.is_dir():
        raise RunnerError("workspace must be a directory")
    settings = load_settings(Path(args.settings).expanduser().resolve() if args.settings else SETTINGS_FILE)
    for role in ("astra", "sol", "luna"):
        model = getattr(args, f"model_{role}")
        effort = getattr(args, f"reasoning_{role}")
        if model:
            settings["models"][role] = model
        if effort:
            settings["reasoning_effort"][role] = effort
    if args.sandbox:
        settings["sandbox"] = args.sandbox
    alloc = allocation(settings, args.sols, args.lunas, args.direct_lunas)
    task = args.prompt
    if not task:
        task = sys.stdin.read()
    if not task.strip():
        raise RunnerError("start requires a non-empty task prompt")
    state_dir = _workspace_state_dir(workspace, Path(args.state_dir) if args.state_dir else None)
    snapshot = _settings_snapshot(settings, alloc, workspace)
    codex_binary = shutil.which(args.codex) or (str(Path(args.codex).resolve()) if os.path.sep in args.codex else None)
    if not codex_binary:
        raise RunnerError(f"Codex CLI executable not found: {args.codex}")
    _secure_dir(state_dir)
    with _owner_lock(state_dir):
        run_path, _, _ = _state_paths(state_dir)
        if run_path.exists():
            old = _read_json(run_path)
            if _matches_owned_process(old):
                raise RunnerError("a prior owned Codex process is still live; stop or wait for it before starting")
        record = {"version": 1, "status": "starting", "workspace": str(workspace),
                  "requested_models": settings["models"], "requested_reasoning_effort": settings["reasoning_effort"],
                  "allocation": alloc, "config_snapshot": snapshot, "session_uuid": None,
                  "codex_binary": codex_binary, "created_at": time.time(), "result_path": None,
                  "effective_models": None}
        if run_path.exists():
            old = _read_json(run_path)
            if Path(old.get("workspace", "")).resolve() != workspace:
                raise RunnerError("state directory belongs to a different workspace")
            if old.get("status") != "completed" and not (old.get("status") == "failed" and not old.get("session_uuid")):
                raise RunnerError(f"previous run status is {old.get('status')!r}; reconcile it with resume/status before starting")
            history = state_dir / "history"
            _secure_dir(history)
            old_run_id = old.get("run_id", "previous")
            old_log = Path(old.get("log_archive", ""))
            events_path = _state_paths(state_dir)[1]
            if events_path.exists() and old_log:
                os.replace(events_path, old_log)
                old_log.chmod(0o600)
            os.replace(run_path, history / f"{old_run_id}-record.json")
            result_path = Path(old.get("result_path", ""))
            if result_path.is_file():
                os.replace(result_path, history / f"{old_run_id}-last-message.txt")
        record["run_id"] = hashlib.sha256(f"{time.time_ns()}:{workspace}".encode()).hexdigest()[:16]
        record["log_archive"] = str(state_dir / "history" / f"{record['run_id']}.jsonl")
        _atomic_json(run_path, record)
        _install_stop_notification()
        return _run_process(state_dir, workspace, settings, alloc, make_task_prompt(task, settings, alloc),
                            codex_binary, None, record)


def cmd_resume(args: argparse.Namespace) -> int:
    try:
        workspace_arg = Path(args.workspace).expanduser().resolve(strict=True) if args.workspace else None
    except OSError as exc:
        raise RunnerError(f"workspace does not exist or cannot be accessed: {exc}") from exc
    # With no override, find exactly one prior run by explicit state directory; never choose --last.
    if args.state_dir:
        state_dir = Path(args.state_dir).expanduser().resolve()
    elif workspace_arg:
        state_dir = _workspace_state_dir(workspace_arg)
    else:
        raise RunnerError("resume requires --workspace or --state-dir")
    with _owner_lock(state_dir):
        record = _load_run(state_dir)
        if not record.get("session_uuid"):
            raise RunnerError("the previous CLI run did not produce a session UUID; it cannot be resumed")
        if _matches_owned_process(record):
            raise RunnerError("the prior owned Codex process is still live; stop or wait before resuming")
        workspace = Path(record.get("workspace", "")).resolve(strict=True)
        if workspace_arg and workspace_arg != workspace:
            raise RunnerError("workspace differs from the saved run; refusing to resume in a different workspace")
        snapshot = record.get("config_snapshot", {})
        if snapshot.get("workspace") != str(workspace):
            raise RunnerError("saved workspace snapshot is inconsistent; refusing resume")
        settings = snapshot.get("settings")
        alloc = snapshot.get("allocation")
        if not isinstance(settings, dict) or not isinstance(alloc, dict):
            raise RunnerError("saved settings snapshot is incomplete; cannot resume")
        if snapshot.get("codex_config_fingerprint") != _codex_config_fingerprint(settings.get("profile"), workspace):
            raise RunnerError("Codex config changed since this run; refusing resume with a different sandbox/profile context")
        prompt = args.prompt or _resume_prompt(record)
        record["log_archive"] = str(state_dir / "history" / f"{record.get('run_id', 'run')}-resume-{int(record.get('resume_count', 0)) + 1}.jsonl")
        record["status"] = "resuming"
        record["resume_count"] = int(record.get("resume_count", 0)) + 1
        record["last_resume_at"] = time.time()
        _atomic_json(_state_paths(state_dir)[0], record)
        _install_stop_notification()
        return _run_process(state_dir, workspace, settings, alloc, prompt, record.get("codex_binary", args.codex),
                            record["session_uuid"], record)


def cmd_status(args: argparse.Namespace) -> int:
    if not args.state_dir and not args.workspace:
        raise RunnerError("status requires --workspace or --state-dir")
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else _workspace_state_dir(workspace)
    record = _load_run(state_dir)
    if workspace and Path(record.get("workspace", "")).resolve() != workspace:
        raise RunnerError("workspace does not match the saved run")
    result = {key: record.get(key) for key in ("status", "session_uuid", "requested_models", "requested_reasoning_effort", "effective_model_observed", "allocation", "exit_code", "error", "events_path", "result_path", "finished_at")}
    result["owned_process_live"] = _matches_owned_process(record)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _terminate_owned(record: dict[str, Any], grace: float) -> bool:
    if not _matches_owned_process(record):
        info = _proc_info(int(record["pid"])) if str(record.get("pid", "")).isdigit() else None
        if info is not None:
            raise RunnerError("PID exists but birth time or process group differs; refusing to signal it")
        return False
    try:
        owner_pid, owner_birth = int(record["owner_pid"]), int(record["owner_birth"])
        owner_info = _proc_info(owner_pid)
    except (KeyError, TypeError, ValueError):
        owner_info = None
    if owner_info and owner_info["birth"] == owner_birth and owner_pid != os.getpid() and hasattr(signal, "SIGUSR1"):
        with contextlib.suppress(ProcessLookupError):
            os.kill(owner_pid, signal.SIGUSR1)
    pid = int(record["pid"])
    for sig in (signal.SIGINT, signal.SIGTERM):
        if not _matches_owned_process(record):
            return True
        os.killpg(pid, sig)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if not _matches_owned_process(record):
                return True
            time.sleep(0.1)
    if _matches_owned_process(record):
        os.killpg(pid, signal.SIGKILL)
    return True


def cmd_stop(args: argparse.Namespace) -> int:
    if not args.state_dir and not args.workspace:
        raise RunnerError("stop requires --workspace or --state-dir")
    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else None
    state_dir = Path(args.state_dir).expanduser().resolve() if args.state_dir else _workspace_state_dir(workspace)
    record = _load_run(state_dir)
    if workspace and Path(record.get("workspace", "")).resolve() != workspace:
        raise RunnerError("workspace does not match the saved run")
    if _terminate_owned(record, args.grace):
        # The foreground owner alone writes final status, UUID, and result metadata.
        print("Signaled the verified owned Codex process group; its foreground owner will record the outcome.")
    else:
        print("No live owned Codex process; nothing was signaled.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one native Codex agent tree and recover it by explicit session UUID.")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="start one Astra-rooted native session")
    start.add_argument("--workspace", required=True)
    start.add_argument("--settings")
    start.add_argument("--state-dir")
    start.add_argument("--codex", default=os.environ.get("MOZGLO_CODEX_BIN", "codex"))
    start.add_argument("--sols", type=int)
    start.add_argument("--lunas", type=int, help="Lunas per Sol manager")
    start.add_argument("--direct-lunas", type=int, help="direct Luna count when --sols 0")
    start.add_argument("--model-astra")
    start.add_argument("--model-sol")
    start.add_argument("--model-luna")
    for role in ("astra", "sol", "luna"):
        start.add_argument(f"--reasoning-{role}", choices=sorted(VALID_EFFORTS))
    start.add_argument("--sandbox", choices=("read-only", "workspace-write"))
    start.add_argument("--prompt", help="task prompt; stdin is used when omitted")
    start.set_defaults(func=cmd_start)
    resume = sub.add_parser("resume", help="resume the saved explicit Codex session UUID")
    resume.add_argument("--workspace")
    resume.add_argument("--state-dir")
    resume.add_argument("--codex", default=os.environ.get("MOZGLO_CODEX_BIN", "codex"))
    resume.add_argument("--prompt", help="optional compact recovery instruction")
    resume.set_defaults(func=cmd_resume)
    for name, func in (("status", cmd_status), ("stop", cmd_stop)):
        command = sub.add_parser(name)
        command.add_argument("--workspace")
        command.add_argument("--state-dir")
        if name == "stop":
            command.add_argument("--grace", type=float, default=3.0)
        command.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RunnerError as exc:
        print(f"mozglo: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("mozglo: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    os.umask(0o077)
    raise SystemExit(main())
