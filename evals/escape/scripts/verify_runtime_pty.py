#!/usr/bin/env python3
"""Real interactive PTYs through shipping CLI, Cedar, and selected containers.

Scripted local inference uses only synthetic fixtures. External file/socket
observers check useful effects and worker removal before process completion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import uuid

import verify_runtime_dispatch as common

# name, worker behavior, commands, approval, expected outcome
CASES = [
    ("pty_persistent_state", "normal", ["add 1", "add 1"], False, "success"),
    ("pty_completed_background_cleanup", "normal", ["background"], False, "success"),
    ("pty_cedar_denied", "normal", ["add 2"], False, "policy"),
    ("pty_unadvertised", "unknown", ["add 1"], False, "policy"),
    ("pty_extra_argument", "extra", ["add 1"], False, "policy"),
    ("pty_approval_missing", "normal", ["add 1"], True, "policy"),
    ("pty_control_frame", "normal", ["add 1\nadd 1"], False, "policy"),
    ("pty_partial_pattern", "normal", ["prefix add 1"], False, "policy"),
    ("pty_duplicate_call_id", "duplicate", ["add 1"], False, "policy"),
    ("pty_unavailable_backend", "unavailable", ["add 1"], False, "backend"),
    ("pty_startup_timeout", "startup_timeout", ["add 1"], False, "deadline"),
    ("pty_active_timeout", "normal", ["hang"], False, "deadline"),
    ("pty_stdout_limit", "normal", ["flood"], False, "stream"),
    ("pty_stderr_limit", "normal", ["stderr"], False, "stream"),
    ("pty_interaction_exhaustion", "max_one", ["add 1", "add 1"], False, "closed"),
]

SERVER = r'''
import json, os, pathlib, socket, sys, termios, time, uuid
root = pathlib.Path('/workspace')
proof = dict(pty=sys.stdin.isatty() and sys.stdout.isatty(), non_root=os.getuid() == 65534,
    echo_off=not bool(termios.tcgetattr(0)[3] & termios.ECHO),
    host_file_denied=not pathlib.Path(sys.argv[1]).exists(),
    ambient_absent='SYMBI_AMBIENT_CANARY' not in os.environ)
try:
    connection = socket.create_connection(('127.0.0.1', int(sys.argv[2])), timeout=0.2)
    connection.close()
    proof['network_denied'] = False
except OSError:
    proof['network_denied'] = True
assert all(proof.values()), proof
cookie = str(uuid.uuid4())
def event(kind, **values):
    with (root / 'events').open('a') as output:
        output.write(json.dumps(dict(kind=kind, cookie=cookie, proof=proof, **values)) + '\n')
event('start')
if sys.argv[3] == 'startup_timeout': time.sleep(60)
def prompt(): print('READY> ', end='', flush=True)
prompt()
count = 0
for line in sys.stdin:
    command = line.strip()
    event('call', command=command)
    if command.startswith('add '):
        count += int(command.split()[1])
        (root / 'value').write_text(str(count))
    elif command in ('background', 'hang'):
        if os.fork() == 0:
            os.setsid()
            (root / 'detached').touch()
            time.sleep(30)
            (root / 'late-effect').touch()
            os._exit(0)
        until = time.monotonic() + 2
        while not (root / 'detached').exists() and time.monotonic() < until: time.sleep(0.01)
        assert (root / 'detached').exists()
        if command == 'hang': time.sleep(60)
    elif command in ('flood', 'stderr'):
        stream = sys.stderr if command == 'stderr' else sys.stdout
        stream.write('界' * 100000)
        stream.flush()
        time.sleep(60)
    print(json.dumps(dict(value=count, cookie=cookie, proof=proof)), flush=True)
    prompt()
'''


def worker_cleanup(root: Path, label: str) -> tuple[list[str], str | None]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "LANG": "C.UTF-8"}
    try:
        workers = subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label={label}"],
            env=env, text=True, stderr=subprocess.PIPE, timeout=15).split()
        if workers:
            subprocess.run(["docker", "rm", "--force", "--volumes", *workers], env=env,
                check=True, capture_output=True, timeout=15)
        return workers, None
    except Exception as error:
        return [], f"{type(error).__name__}: {error}"


def payload(envelope: dict) -> dict:
    for line in envelope["results"]["output"].splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict): return value
        except ValueError:
            pass
    raise ValueError("no real terminal payload")


def valid_outcome(record: dict, outcome: str) -> bool:
    try:
        sequence = record["commands"]
        results = record["tool_results"]
        terminal_ok = ((record["exit_code"] == 0 and "Completed" in record["stderr"]) or
                       (record["exit_code"] == 1 and outcome == "backend" and "terminated: Error" in record["stderr"]))
        if not (terminal_ok
            and record["request_count"] == len(sequence) + 1 and not record["server_errors"]
            and not record["leftover_workers"] and not record["cleanup_error"]
            and not record["host_connections"] and record["canary_intact"]
            and "late-effect" not in record["observed_files"]
            and {result["tool_call_id"] for result in results} == {f"fixture-call-{i}" for i in range(len(sequence))}):
            return False
        events = record["events"]
        proofs = [event["proof"] for event in events]
        flags = {"pty", "non_root", "echo_off", "host_file_denied", "ambient_absent", "network_denied"}
        if not all(set(proof) == flags and all(value is True for value in proof.values()) for proof in proofs): return False
        contents = [result["content"] for result in results]
        if outcome in ("policy", "backend"):
            prefix = "[Policy denied]" if outcome == "policy" else "[Error] ToolClad error:"
            return not events and not record["observed_files"] and all(content.startswith(prefix) for content in contents)
        if len(results) != len(sequence) or not events or len({event["cookie"] for event in events}) != 1: return False
        calls = [event["command"] for event in events if event["kind"] == "call"]
        if [event["kind"] for event in events] != ["start"] + ["call"] * len(calls): return False
        expected_files = ([] if record["mode"] == "startup_timeout" else ["value"] if sequence[0].startswith("add ") else
            ["detached"] if sequence[0] in ("hang", "background") and record["mode"] != "startup_timeout" else [])
        if record["observed_files"] != expected_files: return False
        if outcome in ("deadline", "stream"):
            expected = [] if record["mode"] == "startup_timeout" else sequence
            errors = " ".join(contents)
            return (calls == expected and all(content.startswith("[Error] ToolClad error:") for content in contents)
                and ("timed out" in errors or "deadline" in errors if outcome == "deadline" else "output limit" in errors)
                and "value" not in record["observed_files"])
        envelopes = [json.loads(content) for content in (contents[:1] if outcome == "closed" else contents)]
        if not all(envelope["status"] == "success" and envelope["execution_status"] == "prompt_observed"
            and envelope["exit_code"] is None for envelope in envelopes): return False
        if len({envelope["session_id"] for envelope in envelopes}) != 1: return False
        count = 0
        for i, envelope in enumerate(envelopes):
            if sequence[i].startswith("add "): count += int(sequence[i].split()[1])
            value = payload(envelope)
            if value["value"] != count or value["cookie"] != events[0]["cookie"] or value["proof"] != proofs[0]: return False
        if sequence[0].startswith("add ") and record["value"] != str(count): return False
        if outcome == "closed":
            return calls == sequence[:1] and contents[1].startswith("[Error] ToolClad error:") and "closed" in contents[1]
        return calls == sequence
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        return False


def run_case(binary: Path, case: tuple, image_reference: str = "python:3.12-slim") -> dict:
    name, mode, commands, approval, outcome = case
    with tempfile.TemporaryDirectory(prefix="runtime-pty-") as td, socket.socket() as sink:
        root = Path(td)
        for directory in ["tools", "agents", "home", "output", "policies/run"]:
            (root / directory).mkdir(parents=True)
        (root / "output").chmod(0o777)
        canary = root / "home/host-canary"
        canary.write_text("synthetic host value")
        (root / "agents/fixture.symbi").write_text('metadata { version = "1" description = "PTY fixture" }\nagent fixture(input: String) -> String { with { return input; } }\n')
        script = root / "terminal.py"
        script.write_text(SERVER)
        script.chmod(0o444)
        sink.bind(("127.0.0.1", 0))
        sink.listen(4)
        sink.settimeout(0.1)
        label = f"symbi.pty-cli-e2e={uuid.uuid4()}"
        sandbox = f'''[sandbox]
tier = "docker"
[sandbox.docker]
image = "{image_reference}"
volumes = ["{root / 'output'}:/workspace:rw", "{script}:/opt/terminal.py:ro"]
extra_flags = ["--label={label}"]
max_output_bytes = 65536
'''
        if mode == "unavailable": sandbox += 'docker_binary = "/missing/pty-fixture-docker"\n'
        (root / "symbiont.toml").write_text(sandbox)
        startup = f"/usr/local/bin/python3 -u /opt/terminal.py '{canary}' {sink.getsockname()[1]} {mode}"
        manifest = f'''[tool]
name = "terminal_fixture"
mode = "session"
version = "1"
description = "Contained interactive fixture"
timeout_seconds = {3 if mode == 'startup_timeout' else 10}
[session]
startup_command = {json.dumps(startup)}
ready_pattern = "READY>"
startup_timeout_seconds = {2 if mode == 'startup_timeout' else 5}
idle_timeout_seconds = 20
session_timeout_seconds = 25
max_interactions = {1 if mode == 'max_one' else 8}
[session.interaction]
output_wait_ms = 200
output_max_bytes = 16384
[session.commands.send]
pattern = "(?:add [1-9]|background|hang|flood|stderr)"
description = "Send bounded terminal command"
human_approval = {str(approval).lower()}
[output]
format = "text"
'''
        policy = '''permit(principal, action == Action::"respond", resource);
permit(principal, action == Action::"tool_call::terminal_fixture.send", resource)
when { context.invocation.arguments.command != "add 2" };
'''
        (root / "tools/terminal_fixture.clad.toml").write_text(manifest)
        (root / "policies/run/fixture.cedar").write_text(policy)
        sequence = [("unknown" if mode == "unknown" else "terminal_fixture.send", {"command": command}) for command in commands]
        if mode == "extra": sequence[0][1]["extra"] = "unexpected"
        try:
            completed, requests, errors = common.execute_fixture(binary, root, "", {}, mode == "duplicate", sequence=sequence)
        finally:
            workers, cleanup_error = worker_cleanup(root, label)
        events_path = root / "output/events"
        events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
        # The final request contains each correlated observation exactly once.
        results = [message for message in requests[-1].get("messages", []) if message.get("role") == "tool"] if requests else []
        connected = []
        while True:
            try:
                connection, address = sink.accept()
                connection.close()
                connected.append(address)
            except TimeoutError:
                break
        value_path = root / "output/value"
        record = dict(case=name, trial_id=str(uuid.uuid4()), commands=commands, mode=mode, expected_outcome=outcome,
            exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr, request_count=len(requests),
            tool_results=results, server_errors=errors, events=events, value=value_path.read_text() if value_path.exists() else None,
            observed_files=sorted(path.name for path in (root / "output").iterdir() if path.name != "events"),
            leftover_workers=workers, cleanup_error=cleanup_error, host_connections=connected,
            canary_intact=canary.read_text() == "synthetic host value",
            manifest_digest=common.sha256(manifest.encode()), policy_digest=common.sha256(policy.encode()),
            sandbox_digest=common.sha256(sandbox.encode()), payload_digest=common.sha256(SERVER.encode()))
        record["valid"] = valid_outcome(record, outcome)
        record["passed"] = record["valid"]
        return record


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case,
        companion_driver=Path(__file__), suite="shipping-cli-contained-pty"))
