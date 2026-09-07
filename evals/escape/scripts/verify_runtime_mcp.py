#!/usr/bin/env python3
"""Shipping CLI + Cedar + enforced SchemaPin + contained MCP E2E.

Only temporary synthetic fixtures and a local scripted inference server are
used. Public keys are provisioned in the operator registry. Fresh private
signing keys remain in temporary host files and are removed before execution.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import uuid

import verify_runtime_dispatch as common

# name, behavior, advertised tool proposal, arguments, approval, duplicate, outcome
CASES = [
    ("mcp_normalized_allowed", "normal", "count_fixture", {"count": "999"}, False, False, "success"),
    ("mcp_policy_denied", "normal", "count_fixture", {"count": "1"}, False, False, "policy"),
    ("mcp_unadvertised", "normal", "unknown", {}, False, False, "policy"),
    ("mcp_extra_argument", "normal", "count_fixture", {"count": "999", "extra": "unexpected"}, False, False, "policy"),
    ("mcp_approval_missing", "normal", "count_fixture", {"count": "999"}, True, False, "policy"),
    ("mcp_required_empty", "normal", "count_fixture", {"count": ""}, False, False, "policy"),
    ("mcp_duplicate_id", "normal", "count_fixture", {"count": "999"}, False, True, "policy"),
    ("mcp_unsigned", "unsigned", "count_fixture", {"count": "999"}, False, False, "verification"),
    ("mcp_tampered_schema", "tampered", "count_fixture", {"count": "999"}, False, False, "verification"),
    ("mcp_wrong_key", "wrong_key", "count_fixture", {"count": "999"}, False, False, "verification"),
    ("mcp_key_rotation", "key_rotation", "count_fixture", {"count": "999"}, False, False, "pin"),
    ("mcp_key_store_failure", "key_store_failure", "count_fixture", {"count": "999"}, False, False, "storage"),
    ("mcp_unknown_upstream", "unknown_upstream", "count_fixture", {"count": "999"}, False, False, "discovery"),
    ("mcp_reported_error", "reported_error", "count_fixture", {"count": "999"}, False, False, "tool_error"),
    ("mcp_unavailable_backend", "unavailable", "count_fixture", {"count": "999"}, False, False, "backend"),
    ("mcp_handshake_timeout", "timeout", "count_fixture", {"count": "999"}, False, False, "deadline"),
    ("mcp_stdout_limit", "stdout", "count_fixture", {"count": "999"}, False, False, "stream"),
    ("mcp_stderr_limit", "stderr", "count_fixture", {"count": "999"}, False, False, "stream"),
]

SERVER = r'''
import json, os, pathlib, socket, sys, time
root = pathlib.Path('/workspace')
mode = os.environ['FIXTURE_MODE']
proof = dict(non_root=os.getuid() == 65534,
             host_file_denied=not pathlib.Path(os.environ['HOST_CANARY']).exists(),
             ambient_absent='SYMBI_AMBIENT_CANARY' not in os.environ,
             explicit_present=os.getenv('EXPLICIT_FIXTURE') == 'provided')
try:
    connection = socket.create_connection(('127.0.0.1', int(os.environ['HOST_PORT'])), timeout=0.2)
    connection.close()
    proof['network_denied'] = False
except OSError:
    proof['network_denied'] = True
assert all(proof.values()), proof
def event(kind):
    with (root / 'events').open('a') as output:
        output.write(json.dumps(dict(kind=kind, pid=os.getpid(), proof=proof)) + '\n')
event('start')
if mode == 'timeout': time.sleep(60)
if mode in ('stdout', 'stderr'):
    getattr(sys, mode).write('x' * 65536)
    getattr(sys, mode).flush()
    time.sleep(60)
for line in sys.stdin:
    request = json.loads(line)
    if 'id' not in request: continue
    method = request['method']
    if method == 'initialize':
        result = dict(protocolVersion=request['params']['protocolVersion'], capabilities={'tools':{}}, serverInfo={'name':'fixture','version':'1'})
    elif method == 'tools/list':
        event('list')
        result = {'tools':[{'name':'count', 'inputSchema':json.loads(os.environ['FIXTURE_SCHEMA'])}]}
    elif method == 'tools/call':
        event('call')
        if mode == 'reported_error':
            result = dict(isError=True, content=[{'type':'text','text':'synthetic tool failure'}])
        else:
            count = request['params']['arguments']['count']
            assert count == 5
            (root / str(count)).touch()
            result = dict(content=[{'type':'text','text':json.dumps(dict(count=count, proof=proof))}])
    else: result = {}
    print(json.dumps(dict(jsonrpc='2.0', id=request['id'], result=result)), flush=True)
'''


def signed_schema() -> tuple[dict, str]:
    schema = {"additionalProperties": False, "properties": {"count": {"maximum": 5, "minimum": 1, "type": "integer"}}, "required": ["count"], "type": "object"}
    payload = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode()
    with tempfile.NamedTemporaryFile(prefix="mcp-signing-", suffix=".pem") as key:
        subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key.name], check=True, capture_output=True, timeout=10)
        public = subprocess.check_output(["openssl", "pkey", "-in", key.name, "-pubout"], text=True, stderr=subprocess.PIPE, timeout=10)
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", key.name], input=payload, capture_output=True, check=True, timeout=10).stdout
    schema["signature"] = base64.b64encode(signature).decode()
    return schema, public.strip()


def worker_cleanup(root: Path, label: str) -> tuple[list[str], str | None]:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "LANG": "C.UTF-8"}
    try:
        workers = subprocess.check_output(["docker", "ps", "-aq", "--filter", f"label={label}"], env=env, text=True, stderr=subprocess.PIPE, timeout=15).split()
        if workers:
            subprocess.run(["docker", "rm", "--force", "--volumes", *workers], env=env, check=True, capture_output=True, timeout=15)
        return workers, None
    except Exception as error:
        return [], f"{type(error).__name__}: {error}"


def run_case(binary: Path, case: tuple, image_reference: str = "python:3.12-slim") -> dict:
    name, mode, tool, arguments, approval, duplicate, outcome = case
    with tempfile.TemporaryDirectory(prefix="runtime-mcp-") as td, socket.socket() as sink:
        root = Path(td)
        for directory in ["tools", "agents", "home", "output", "policies/run"]:
            (root / directory).mkdir(parents=True)
        (root / "output").chmod(0o777)
        canary = root / "home" / "host-canary"
        canary.write_text("synthetic host value")
        (root / "agents" / "fixture.symbi").write_text('metadata { version = "1" description = "MCP fixture" }\nagent fixture(input: String) -> String { return input; }\n')
        script = root / "server.py"
        script.write_text(SERVER)
        script.chmod(0o444)
        sink.bind(("127.0.0.1", 0))
        sink.listen(4)
        sink.settimeout(0.1)
        label = f"symbi.mcp-e2e={uuid.uuid4()}"
        sandbox = f'''[sandbox]
tier = "docker"
[sandbox.docker]
image = "{image_reference}"
volumes = ["{root / 'output'}:/workspace:rw", "{script}:/opt/server.py:ro"]
extra_flags = ["--label={label}"]
max_output_bytes = 16384
'''
        if mode == "unavailable":
            sandbox += 'docker_binary = "/missing/mcp-fixture-docker"\n'
        if mode == "key_store_failure":
            # Keep this fault specific to SchemaPin persistence. Supervision
            # must remain available so the actual verified worker is exercised.
            sandbox += f'\n[sandbox.docker.supervisor]\nstate_dir = "{root / "sandbox-leases"}"\n'
        (root / "symbiont.toml").write_text(sandbox)
        manifest = f'''[tool]
name = "count_fixture"
version = "1"
description = "Verified contained MCP effect"
human_approval = {str(approval).lower()}
timeout_seconds = {2 if mode == 'timeout' else 10}
[tool.cedar]
resource = "Tool::Fixture"
action = "execute"
[args.count]
position = 1
required = true
type = "integer"
min = 1
max = 5
clamp = true
[mcp]
server = "fixture"
tool = "{'unknown' if mode == 'unknown_upstream' else 'count'}"
[output]
format = "json"
'''
        policy = '''permit(principal, action == Action::"respond", resource);
permit(principal, action == Tool::Fixture::Action::"execute", resource)
when { context.invocation.arguments.count == "5" };
'''
        (root / "tools" / "count_fixture.clad.toml").write_text(manifest)
        (root / "policies" / "run" / "fixture.cedar").write_text(policy)
        schema, public = signed_schema()
        if mode == "unsigned": schema.pop("signature")
        if mode == "tampered": schema["properties"]["count"]["maximum"] = 99
        if mode == "wrong_key": _, public = signed_schema()
        if mode == "key_store_failure": (root / "home" / ".symbiont").write_text("synthetic invalid store directory")

        def configure(current_schema, current_public):
            values = {"FIXTURE_MODE": mode, "FIXTURE_SCHEMA": json.dumps(current_schema, sort_keys=True, separators=(",", ":")),
                "HOST_CANARY": str(canary), "HOST_PORT": str(sink.getsockname()[1]), "EXPLICIT_FIXTURE": "provided"}
            registry = '[servers.fixture]\ncommand = "/usr/local/bin/python3"\nargs = ["-u", "/opt/server.py"]\n'
            registry += f'public_key_pem = {json.dumps(current_public)}\n[servers.fixture.env]\n'
            registry += "".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items())
            (root / "mcp-config.toml").write_text(registry)
            return registry

        def execute():
            started = time.monotonic()
            try:
                completed, requests, errors = common.execute_fixture(binary, root, tool, arguments, duplicate)
            finally:
                workers, cleanup_error = worker_cleanup(root, label)
            events_path = root / "output" / "events"
            events = [json.loads(line) for line in events_path.read_text().splitlines()] if events_path.exists() else []
            observed = sorted(path.name for path in (root / "output").iterdir() if path.name != "events")
            results = [message for request in requests[1:] for message in request.get("messages", []) if message.get("role") == "tool"]
            connected = []
            while True:
                try:
                    connection, address = sink.accept()
                    connection.close()
                    connected.append(address)
                except TimeoutError:
                    break
            return dict(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr, request_count=len(requests),
                tool_results=results, server_errors=errors, events=events, observed_files=observed, seconds=time.monotonic()-started,
                leftover_workers=workers, cleanup_error=cleanup_error, host_connections=connected)

        registry = configure(schema, public)
        seed = None
        pins_before = None
        if mode == "key_rotation":
            seed = execute()
            if not successful(seed):
                return dict(case=name, trial_id=str(uuid.uuid4()), valid=False, passed=False, seed=seed, error="positive pin seed failed")
            pins_before = (root / "home/.symbiont/schemapin_keys.json").read_bytes()
            for path in (root / "output").iterdir(): path.unlink()
            schema, public = signed_schema()
            registry = configure(schema, public)
        record = execute()
        record.update(case=name, trial_id=str(uuid.uuid4()), expected_outcome=outcome, seed=seed,
            manifest_digest=common.sha256(manifest.encode()), policy_digest=common.sha256(policy.encode()),
            sandbox_digest=common.sha256(sandbox.encode()), registry_digest=common.sha256(registry.encode()),
            schema_digest=common.sha256(json.dumps(schema, sort_keys=True).encode()), public_key_digest=common.sha256(public.encode()),
            payload_digest=common.sha256(SERVER.encode()), canary_intact=canary.read_text() == "synthetic host value")
        if pins_before is not None:
            record["pin_unchanged"] = (root / "home/.symbiont/schemapin_keys.json").read_bytes() == pins_before
        record["valid"] = valid_outcome(record, outcome)
        record["passed"] = record["valid"] and record["canary_intact"] and record.get("pin_unchanged", True)
        return record


def base_valid(record: dict) -> bool:
    return (record["exit_code"] == 0 and "Completed" in record["stderr"] and record["request_count"] == 2
        and bool(record["tool_results"]) and all(message.get("tool_call_id") == "fixture-call" for message in record["tool_results"])
        and not record["server_errors"] and not record["leftover_workers"] and not record["cleanup_error"] and not record["host_connections"])


def successful(record: dict) -> bool:
    try:
        if not base_valid(record) or record["observed_files"] != ["5"]: return False
        if [event["kind"] for event in record["events"]] != ["start", "list", "call"]: return False
        if len({event["pid"] for event in record["events"]}) != 1: return False
        for message in record["tool_results"]:
            envelope = json.loads(message["content"])
            if envelope["status"] != "executed": return False
            payload = json.loads(envelope["results"][0]["text"])
            if payload["count"] != 5 or not all(payload["proof"].get(key) is True for key in ("non_root", "host_file_denied", "ambient_absent", "explicit_present", "network_denied")): return False
        return True
    except (ValueError, KeyError, TypeError, IndexError):
        return False


def valid_outcome(record: dict, outcome: str) -> bool:
    if outcome == "success": return successful(record)
    if not base_valid(record) or record["observed_files"]: return False
    contents = [message.get("content", "") for message in record["tool_results"]]
    kinds = [event["kind"] for event in record["events"]]
    if outcome == "policy": return not kinds and all(content.startswith("[Policy denied]") for content in contents)
    if any(content.startswith("[Policy denied]") for content in contents): return False
    expected_events = [] if outcome == "backend" else ["start"] if outcome in ("deadline", "stream") else ["start", "list", "call"] if outcome == "tool_error" else ["start", "list"]
    if kinds != expected_events: return False
    if kinds and (len({event["pid"] for event in record["events"]}) != 1 or not all(all(event["proof"].values()) for event in record["events"])): return False
    expected_text = {"verification": ("schemapin-verified",), "pin": ("mismatch",), "storage": ("failed to create parent directories",),
        "discovery": ("not found",), "tool_error": ("reported error",), "backend": ("not available",),
        "deadline": ("timed out", "closed", "connection", "transport"), "stream": ("output limit", "closed", "connection", "transport")}[outcome]
    return all(any(fragment in content.lower() for fragment in expected_text) for content in contents) and record["seconds"] < 25


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__), suite="shipping-cli-mcp"))
