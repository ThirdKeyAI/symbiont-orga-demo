#!/usr/bin/env python3
"""Shipping managed CLI through real Firecracker and private broker capabilities."""
import argparse
import hashlib
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

import verify_runtime_dispatch as common
import verify_runtime_managed_cli as managed

CASES = ["allowed", "policy_denied", "approval_missing", "admission_denied",
         "admission_approval_missing", "extra_argument", "provider_redirect",
         "provider_credential_echo", "deadline"]
PRELAUNCH = {"admission_denied", "admission_approval_missing"}

def file_digest(path):
    with path.open("rb") as stream:
        return "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()


def delivered_text(block):
    content = block["content"]
    if isinstance(content, str):
        return content
    assert isinstance(content, list) and all(part.get("type") == "text" and isinstance(part.get("text"), str) for part in content)
    return "\n".join(part["text"] for part in content)

@contextmanager
def owned_fixture():
    root = Path(tempfile.mkdtemp(prefix="fmcli-", dir="/tmp"))
    try:
        yield root
    finally:
        # Never erase recovery state for a worker whose removal is unconfirmed.
        pending = list((root / "leases").glob("*.json")) + list((root / "leases").glob("vm-*"))
        if not pending:
            shutil.rmtree(root)


def profile(root, artifacts):
    records = list((root / "leases").glob("*.json"))
    assert len(records) == 1, "expected one CLI VM before its first tool request"
    record = json.loads(records[0].read_text())
    identity = record["state"]
    assert identity["phase"] == "vm_created"
    work = root / "leases" / ("vm-" + record["lease"])
    config = json.loads((work / "vm-config.json").read_text())
    executable = Path(f"/proc/{identity['pid']}/exe").resolve(strict=True)
    ports = sorted(p.name for p in work.glob("vsock_*"))
    result = {
        "vm_alive": executable == artifacts["firecracker"],
        "read_only_root": config["drives"][0]["is_read_only"] is True,
        "rootfs": config["drives"][0]["path_on_host"] == str(artifacts["rootfs"]),
        "no_network_interface": not config.get("network-interfaces"),
        "memory": config["machine-config"]["mem_size_mib"] == 768,
        "vcpu": config["machine-config"]["vcpu_count"] == 1,
        "exact_ports": ports == ["vsock_4051", "vsock_4052"],
        "tools_capability": (work / "vsock_4051").is_symlink() and (work / "vsock_4051").readlink().name == "tools.sock",
        "inference_capability": (work / "vsock_4052").is_symlink() and (work / "vsock_4052").readlink().name == "inference.sock",
    }
    assert all(result.values()), result
    return result


def run_case(binary, name, artifacts):
    result = dict(case=name, trial_id=str(uuid.uuid4()), valid=False, passed=False)
    with owned_fixture() as root:
        for folder in ["tools", "agents", "policies/managed-cli", "home", "host-bin", "leases"]:
            (root / folder).mkdir(parents=True)
        (root / "leases").chmod(0o700)
        canary = root / "host-canary"
        canary.write_text("synthetic-host-only")
        nonce = "guest-effect-" + uuid.uuid4().hex
        host_cli = root / "host-bin/claude"
        host_cli.write_text("#!/bin/sh\nexit 19\n")
        host_cli.chmod(0o700)
        (root / "agents/fixture.symbi").write_text(
            'metadata { executor = "claude_code" allowed_tools = "write_fixture"'
            + (' human_approval = true' if name == "admission_approval_missing" else "")
            + ' }\nagent fixture(input: String) -> String { with { return input; } }\n')
        manifest = '''[tool]
name = "write_fixture"
version = "1"
description = "Perform a normalized guest scratch-file write and read"
binary = "python3"
human_approval = APPROVAL
[args.count]
position = 1
type = "integer"
required = true
min = 1
max = 5
clamp = true
[command]
template = COMMAND
[output]
format = "text"
'''.replace("APPROVAL", str(name == "approval_missing").lower())
        code = f'from pathlib import Path; p=Path("/tmp/managed-effect"); p.write_text("{nonce}:"+"{{count}}"); print(p.read_text())'
        import shlex
        manifest = manifest.replace("COMMAND", json.dumps(shlex.join(["python3", "-c", code])))
        (root / "tools/write_fixture.clad.toml").write_text(manifest)
        policy = ('forbid' if name == "admission_denied" else 'permit') + '(principal, action == Action::"tool_call::claude_code", resource);\n'
        policy += 'permit(principal, action == Action::"tool_call::write_fixture", resource) when { context.invocation.arguments.count == "5" };\n'
        (root / "policies/managed-cli/fixture.cedar").write_text(policy)
        calls, observed, errors, profiles, redirects = [], {}, [], [], []
        released = threading.Event()
        key = "synthetic-host-key-" + uuid.uuid4().hex

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                redirects.append(self.path)
                self.send_response(204)
                self.end_headers()

            def do_POST(self):
                try:
                    assert self.headers.get("x-api-key") == key
                    length = int(self.headers["Content-Length"])
                    assert 0 < length <= 1024 * 1024
                    raw = self.rfile.read(length)
                    body = json.loads(raw)
                    assert body["model"] == managed.MODEL and self.path == "/v1/messages"
                    calls.append(dict(request_digest=common.sha256(raw), model=body["model"], fields=sorted(body)))
                    if len(calls) == 1:
                        profiles.append(profile(root, artifacts))
                    if name == "deadline":
                        released.wait(20)
                        return
                    if name == "provider_redirect":
                        self.send_response(302)
                        self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/forbidden")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if name == "provider_credential_echo":
                        response = json.dumps(dict(error=key)).encode()
                        content_type = "application/json"
                    else:
                        for message in body["messages"]:
                            blocks = message.get("content")
                            if isinstance(blocks, list):
                                for block in blocks:
                                    if block.get("type") == "tool_result":
                                        observed[block["tool_use_id"]] = block
                        if observed:
                            assert set(observed) == {"toolu_vm_0"}
                            content, reason = [dict(type="text", text="Scripted VM exchange complete")], "end_turn"
                        else:
                            assert len(calls) == 1
                            assert "mcp__symbi__write_fixture" in {tool["name"] for tool in body["tools"]}
                            arguments = {"count": "1" if name == "policy_denied" else "999"}
                            if name == "extra_argument":
                                arguments["extra"] = "unregistered"
                            content = [dict(type="tool_use", id="toolu_vm_0", name="mcp__symbi__write_fixture", input=arguments)]
                            reason = "tool_use"
                        message = dict(id="msg_vm_" + str(len(calls)), type="message", role="assistant",
                            model=managed.MODEL, content=content, stop_reason=reason, stop_sequence=None,
                            usage=dict(input_tokens=100, output_tokens=20))
                        response = managed.sse(message) if body.get("stream") else json.dumps(message).encode()
                        content_type = "text/event-stream" if body.get("stream") else "application/json"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(response)))
                    self.end_headers()
                    self.wfile.write(response)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as error:
                    errors.append(repr(error))
                    self.send_error(400, "synthetic fixture rejected input")

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        configuration = '[sandbox]\ntier = "firecracker"\n[sandbox.firecracker]\n'
        for field, value in dict(firecracker_binary=str(artifacts["firecracker"]),
            kernel_image_path=str(artifacts["kernel"]), rootfs_path=str(artifacts["rootfs"]),
            mem_mib=768, vcpus=1, rootfs_read_only=True).items():
            configuration += field + " = " + json.dumps(value) + "\n"
        configuration += 'max_execution_time = { secs = 30, nanos = 0 }\nstartup_timeout = { secs = 5, nanos = 0 }\n'
        configuration += '[sandbox.firecracker.supervisor]\nstate_dir = ' + json.dumps(str(root / "leases")) + '\n'
        configuration += f'''[managed_cli.inference]
base_url = "http://127.0.0.1:{server.server_port}"
model = "{managed.MODEL}"
api_key_env = "SYNTHETIC_PROVIDER_KEY"
max_requests = 8
max_output_tokens_per_request = 4096
request_timeout_seconds = 15
'''
        (root / "symbiont.toml").write_text(configuration)
        result["fixture_hashes"] = {str(path.relative_to(root)): file_digest(path) for path in
            [root / "symbiont.toml", root / "agents/fixture.symbi", root / "tools/write_fixture.clad.toml", root / "policies/managed-cli/fixture.cedar"]}
        command = [str(binary), "run", "fixture", "--input", "Complete the scripted registered tool exchange.",
            "--target", "/tmp", "--max-turns", "3", "--budget-timeout", "6s" if name == "deadline" else "30s"]
        env = dict(PATH=str(root / "host-bin") + ":/usr/bin:/bin", HOME=str(root / "home"), LANG="C.UTF-8",
            SYMBIONT_ENV="production", SYNTHETIC_PROVIDER_KEY=key, ANTHROPIC_API_KEY="synthetic-ambient-key",
            HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
            SYMBIONT_SANDBOX_SUPERVISOR=str(artifacts["supervisor"]), SYMBIONT_MASTER_KEY="0" * 64)
        started = time.monotonic()
        try:
            process = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=60)
        finally:
            released.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        remaining = list((root / "leases").glob("*.json")) + list((root / "leases").glob("vm-*"))
        result.update(directory=str(root), seconds=time.monotonic() - started, exit_code=process.returncode,
            stdout=process.stdout, stderr=process.stderr, inference_requests=calls, tool_results=observed,
            server_errors=errors, worker_profiles=profiles, redirect_requests=redirects, owned_vm_state_remaining=[p.name for p in remaining],
            host_canary_intact=canary.read_text() == "synthetic-host-only")
        try:
            assert not remaining and not errors and not redirects and result["host_canary_intact"]
            journals = list((root / ".symbiont/governed").glob("*.jsonl"))
            assert len(journals) == 1
            public = re.search(r"audit public key: ([a-f0-9]{64})", process.stdout).group(1)
            entries = managed.verify_journal(journals[0], public)
            requested = [entry["event"]["InferenceRequested"] for entry in entries if "InferenceRequested" in entry["event"]]
            assert len(requested) == len(calls)
            assert all(event["request_hash"] == call["request_digest"].removeprefix("sha256:") for event, call in zip(requested, calls))
            audited = [observation for entry in entries
                for observation in entry["event"].get("ToolBatchCompleted", {}).get("observations", [])
                if observation["source"] != "claude_code"]
            assert sorted((item["content"], item["is_error"]) for item in audited) == sorted(
                (delivered_text(block), bool(block.get("is_error"))) for block in observed.values())
            result["audit"] = dict(public_key=public, digest=common.sha256(journals[0].read_bytes()),
                events=[entry["event"] for entry in entries], admission=managed.admission_evidence(entries),
                inference_correlated=True, observations_correlated=True)
            assert key not in process.stdout + process.stderr + journals[0].read_text()
            verify_outcome(result, nonce)
            result.update(valid=True, passed=True)
        except Exception as error:
            result["error"] = repr(error)
        return result


def verify_outcome(result, nonce):
    """Require the intended effect or rejection, beyond a generic process failure."""
    name = result["case"]
    calls, profiles, observed = result["inference_requests"], result["worker_profiles"], result["tool_results"]
    events = result["audit"]["events"]
    reason = events[-1].get("Terminated", {}).get("reason")
    assert sum("Terminated" in event for event in events) == 1
    assert not result["redirect_requests"] and not result["owned_vm_state_remaining"]
    assert not result["server_errors"] and result["host_canary_intact"]
    assert all(profile and all(profile.values()) for profile in profiles)
    assert result["audit"]["inference_correlated"] and result["audit"]["observations_correlated"]
    if name in PRELAUNCH:
        assert result["exit_code"] == 1 and not calls and not profiles and not observed
        assert result["audit"]["admission"] == dict(pre_effect=False, completed=False)
        assert isinstance(reason, dict) and "Error" in reason
        required = "Cedar denied action" if name == "admission_denied" else "required approval relay is unavailable"
        assert required in json.dumps(events), events[-1]
    elif name in {"provider_redirect", "provider_credential_echo", "deadline"}:
        assert result["exit_code"] == 1 and len(calls) == 1 and len(profiles) == 1 and not observed
        assert result["audit"]["admission"] == dict(pre_effect=True, completed=False)
        assert isinstance(reason, dict) and "Error" in reason
        failure = reason["Error"]["message"]
        if name == "provider_redirect":
            assert "configured inference upstream rejected" in result["stderr"]
        elif name == "provider_credential_echo":
            assert "protected credentials" in failure, failure
        else:
            assert "deadline expired" in failure, failure
            assert 5 <= result["seconds"] < 15, result["seconds"]
    else:
        assert result["exit_code"] == 0 and reason == "Completed"
        assert len(calls) == 2 and len(profiles) == 1 and set(observed) == {"toolu_vm_0"}
        assert result["audit"]["admission"] == dict(pre_effect=True, completed=True)
        block = observed["toolu_vm_0"]
        text = delivered_text(block)
        if name == "allowed":
            payload = json.loads(text)
            assert not block.get("is_error") and payload["status"] == "success"
            assert payload["results"]["raw_output"] == nonce + ":5\n" and payload["results"]["exit_code"] == 0
            result["guest_effect"] = nonce + ":5"
        else:
            assert block.get("is_error"), text
            expected = {"policy_denied": "Cedar denied action", "approval_missing": "required approval relay is unavailable",
                "extra_argument": "unknown argument 'extra'"}[name]
            assert expected in text, text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ["source", "binary", "supervisor", "firecracker", "kernel", "rootfs", "report"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--case", choices=CASES, action="append")
    args = parser.parse_args()
    artifacts = {name: getattr(args, name).resolve(strict=True) for name in ["supervisor", "firecracker", "kernel", "rootfs"]}
    source = common.source_identity(args.source)
    identity = {"path": str(args.binary), "sha256": file_digest(args.binary)}
    hashes = {name: {"path": str(path), "sha256": file_digest(path)} for name, path in artifacts.items()}
    paths = [Path(__file__), Path(common.__file__), Path(managed.__file__)]
    drivers = {str(path): file_digest(path) for path in paths}
    planned = args.case or CASES
    report = dict(suite="shipping-firecracker-managed", source=source, executable=identity,
        artifacts=hashes, drivers=drivers, planned_cases=planned, trials=[], status="invalid", containment_claim=False,
        build=dict(skipped=True, reason="explicit prebuilt executable; retain matching build-gate evidence"))
    for name in planned:
        try:
            trial = run_case(args.binary, name, artifacts)
        except Exception as error:
            trial = dict(case=name, trial_id=str(uuid.uuid4()), valid=False, passed=False, error=repr(error))
        report["trials"].append(trial)
        args.report.write_text(json.dumps(report, indent=2) + "\n")
        print(name, trial["passed"], trial.get("error", ""), flush=True)
    report["source_unchanged"] = common.source_identity(args.source) == source
    report["artifacts_unchanged"] = (file_digest(args.binary) == identity["sha256"]
        and all(file_digest(path) == hashes[name]["sha256"] for name, path in artifacts.items()))
    report["drivers_unchanged"] = all(file_digest(path) == drivers[str(path)] for path in paths)
    if report["source_unchanged"] and report["artifacts_unchanged"] and report["drivers_unchanged"] and common.complete_trials(planned, report["trials"]):
        report["status"] = "passed"
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
