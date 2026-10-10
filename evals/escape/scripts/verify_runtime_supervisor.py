#!/usr/bin/env python3
"""Shipping CLI process loss and independent container cleanup, using synthetic effects."""
from __future__ import annotations

import json
from contextlib import contextmanager
import shutil
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

import verify_runtime_dispatch as common

CASES = [(name,) for name in (
    "supervised_useful_completion", "runtime_sigkill_active", "runtime_sigkill_creating",
    "supervisor_unavailable", "supervisor_state_mount_denied",
)]

PAYLOAD = r'''
import json, os, pathlib, sys, time
root = pathlib.Path('/workspace')
proof = dict(non_root=os.getuid() == 65534,
    host_file_denied=not pathlib.Path(sys.argv[1]).exists(),
    supervisor_denied=not pathlib.Path(sys.argv[2]).exists(),
    ambient_absent='SYMBI_AMBIENT_CANARY' not in os.environ)
assert all(proof.values()), proof
(root / 'proof').write_text(json.dumps(proof))
if sys.argv[3] == 'runtime_sigkill_active':
    if os.fork() == 0:
        os.setsid()
        while True:
            # Keep earlier effects intact if SIGKILL interrupts the next write.
            with (root / 'ticks').open('a') as ticks:
                ticks.write(str(time.monotonic()) + '\n')
            time.sleep(0.05)
    time.sleep(60)
(root / 'result').write_text('allowed work')
print(json.dumps(proof))
'''


def workers(label: str) -> list[str]:
    return subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label={label}"],
        text=True, stderr=subprocess.PIPE, timeout=15).split()


def wait_for(predicate, description: str, seconds: float = 15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(description)


def valid_outcome(record: dict) -> bool:
    if (record["cleanup_error"] or record["leftover_workers"] or record["leftover_leases"]
            or not record["canary_intact"] or record["server_errors"]):
        return False
    name = record["case"]
    if name.startswith("runtime_sigkill"):
        if record["exit_code"] != -9 or record["request_count"] != 1 or not record["killed_after_trigger"]:
            return False
        if name.endswith("creating"):
            return record["late_container_created"] and record["observed_files"] == []
        return (record["ticks_stopped"] and record["observed_files"] == ["proof", "ticks"]
            and record["proof"] == dict(non_root=True, host_file_denied=True, supervisor_denied=True, ambient_absent=True))
    if name == "supervisor_state_mount_denied":
        return (record["exit_code"] == 1 and record["request_count"] == 0
            and "Agent sandbox selection failed: bind mount exposes the protected sandbox supervisor state" in record["stderr"]
            and not record["tool_results"] and record["observed_files"] == [])
    results = record["tool_results"]
    if (record["exit_code"] != 0 or "Completed" not in record["stderr"] or record["request_count"] != 2
            or len(results) != 1 or results[0].get("tool_call_id") != "fixture-call"):
        return False
    if name == "supervisor_unavailable":
        return results[0].get("content", "").startswith("[Error] ToolClad error: Tool 'owner_fixture': sandbox supervisor executable unavailable") and record["observed_files"] == []
    try:
        envelope = json.loads(results[0]["content"])
        return (envelope["status"] == "success" and envelope["results"] == dict(record["proof"], exit_code=0)
            and record["proof"] == dict(non_root=True, host_file_denied=True, supervisor_denied=True, ambient_absent=True)
            and record["observed_files"] == ["proof", "result"] and record["result"] == "allowed work")
    except (KeyError, TypeError, ValueError):
        return False


@contextmanager
def fixture_directory():
    root = Path(tempfile.mkdtemp(prefix="runtime-owner-"))
    try:
        yield root
    finally:
        # An unresolved creation can still arrive after an empty inventory.
        # Keep its durable recovery state even when the trial itself fails.
        if list((root / "leases").glob("*.json")):
            print(json.dumps({"retained_fixture_for_recovery": str(root)}), flush=True)
        else:
            shutil.rmtree(root)


def run_case(binary: Path, case: tuple, image_reference: str = "python:3.12-slim") -> dict:
    name = case[0]
    with fixture_directory() as root:
        for directory in ["tools", "agents", "home", "output", "leases", "policies/run"]:
            (root / directory).mkdir(parents=True)
        (root / "leases").chmod(0o700)
        (root / "output").chmod(0o777)
        canary = root / "home/host-canary"
        canary.write_text("synthetic host value")
        (root / "agents/fixture.symbi").write_text('metadata { version = "1" description = "Supervisor fixture" }\nagent fixture(input: String) -> String { with { return input; } }\n')
        payload = root / "worker.py"
        payload.write_text(PAYLOAD)
        payload.chmod(0o444)
        label = f"symbi.owner-cli-e2e={uuid.uuid4()}"
        volumes = [f"{root / 'output'}:/workspace:rw", f"{payload}:/opt/worker.py:ro"]
        if name == "supervisor_state_mount_denied":
            volumes.append(f"{root / 'leases'}:/control:rw")
        wrapper_source = ""
        docker_setting = ""
        if name == "runtime_sigkill_creating":
            wrapper_source = f'''#!/usr/bin/python3
import os, pathlib, subprocess, sys, time
root = pathlib.Path({str(root)!r})
if sys.argv[1] == 'create':
    (root / 'create-entered').touch()
    time.sleep(3)
    output = subprocess.run(['/usr/bin/docker', *sys.argv[1:]], capture_output=True)
    if output.returncode == 0: (root / 'created-id').write_bytes(output.stdout)
    sys.stdout.buffer.write(output.stdout)
    sys.stderr.buffer.write(output.stderr)
    sys.exit(output.returncode)
os.execv('/usr/bin/docker', ['docker', *sys.argv[1:]])
'''
            wrapper = root / "docker-client"
            wrapper.write_text(wrapper_source)
            wrapper.chmod(0o700)
            docker_setting = f"docker_binary = {json.dumps(str(wrapper))}\n"
        sandbox = f'''[sandbox]
tier = "docker"
[sandbox.docker]
image = "{image_reference}"
volumes = {json.dumps(volumes)}
extra_flags = ["--label={label}"]
{docker_setting}[sandbox.docker.supervisor]
state_dir = "{root / 'leases'}"
'''
        if name == "supervisor_unavailable":
            sandbox += 'binary = "/missing/sandbox-supervisor-fixture"\n'
        (root / "symbiont.toml").write_text(sandbox)
        command = f"/usr/local/bin/python3 /opt/worker.py {canary} {root / 'leases'} {name}"
        manifest = f'''[tool]
name = "owner_fixture"
version = "1"
binary = "/usr/local/bin/python3"
description = "Synthetic supervised work"
timeout_seconds = 20
[command]
template = {json.dumps(command)}
[output]
format = "json"
'''
        policy = 'permit(principal, action == Action::"respond", resource);\npermit(principal, action == Action::"tool_call::owner_fixture", resource);\n'
        (root / "tools/owner_fixture.clad.toml").write_text(manifest)
        (root / "policies/run/fixture.cedar").write_text(policy)
        observation = dict(killed_after_trigger=False, late_container_created=False, ticks_stopped=False)

        def observe(process):
            trigger = root / ("create-entered" if name.endswith("creating") else "output/ticks")
            def triggered():
                if process.poll() is not None:
                    raise AssertionError("runtime exited before the expected live worker effect")
                return trigger.exists() and (name.endswith("creating") or bool(trigger.read_text()))
            wait_for(triggered, "live runtime never reached crash trigger")
            process.kill()
            process.wait(timeout=5)
            observation["killed_after_trigger"] = True
            if name.endswith("creating"):
                wait_for(lambda: (root / "created-id").exists(), "delayed creation never materialized")
                observation["late_container_created"] = True
            wait_for(lambda: not workers(label) and not list((root / "leases").glob("*.json")), "independent cleanup failed")
            if name.endswith("active"):
                before = trigger.read_text()
                time.sleep(0.3)
                observation["ticks_stopped"] = bool(before) and before == trigger.read_text()

        cleanup_error = None
        try:
            completed, requests, errors = common.execute_fixture(binary, root, "owner_fixture", {},
                process_observer=observe if name.startswith("runtime_sigkill") else None)
        finally:
            leftover = workers(label)
            if leftover:
                try:
                    subprocess.run(["docker", "rm", "--force", "--volumes", *leftover], check=True, capture_output=True, timeout=15)
                except Exception as error:
                    cleanup_error = str(error)
        proof = root / "output/proof"
        result = root / "output/result"
        record = dict(case=name, trial_id=str(uuid.uuid4()), **observation,
            exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr,
            request_count=len(requests), server_errors=errors,
            tool_results=[message for request in requests[1:] for message in request.get("messages", []) if message.get("role") == "tool"],
            observed_files=sorted(path.name for path in (root / "output").iterdir()),
            proof=json.loads(proof.read_text()) if proof.exists() else {}, result=result.read_text() if result.exists() else None,
            leftover_workers=leftover, leftover_leases=[path.name for path in (root / "leases").glob("*.json")],
            cleanup_error=cleanup_error, canary_intact=canary.read_text() == "synthetic host value",
            manifest_digest=common.sha256(manifest.encode()), policy_digest=common.sha256(policy.encode()),
            sandbox_digest=common.sha256(sandbox.encode()), payload_digest=common.sha256(PAYLOAD.encode()),
            fault_injector_digest=common.sha256(wrapper_source.encode()))
        record["valid"] = record["passed"] = valid_outcome(record)
        return record


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__), suite="shipping-cli-independent-supervisor"))
