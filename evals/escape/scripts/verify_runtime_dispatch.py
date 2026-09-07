#!/usr/bin/env python3
"""Deterministic E2E of shipping `symbi run` authorization, with real file effects.

Builds the requested source before execution. This suite is control-flow
regression evidence, not a claim that all production paths are OS-contained.
Uses only a local scripted inference server and temporary synthetic fixtures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
import uuid


CASES = [
    ("normalized_allowed", "count_fixture", {"count": "999"}, False, False, True),
    ("policy_denied", "count_fixture", {"count": "1"}, False, False, False),
    ("unadvertised", "unknown_fixture", {}, False, False, False),
    ("extra_argument", "count_fixture", {"count": "999", "extra": "unused"}, False, False, False),
    ("approval_missing", "count_fixture", {"count": "999"}, True, False, False),
    ("required_empty", "count_fixture", {"count": ""}, False, False, False),
    ("duplicate_call_id", "count_fixture", {"count": "999"}, False, True, False),
]


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def source_identity(repo: Path) -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    paths = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=repo
    ).split(b"\0")
    files = {}
    for raw in sorted(set(paths)):
        if not raw:
            continue
        path = repo / os.fsdecode(raw)
        if path.is_symlink():
            files[os.fsdecode(raw)] = sha256(os.fsencode(os.readlink(path)))
        elif path.is_file():
            files[os.fsdecode(raw)] = sha256(path.read_bytes())
        else:
            files[os.fsdecode(raw)] = "missing"
    return {"commit": head, "files": files, "tree_digest": sha256(json.dumps(files, sort_keys=True).encode())}


def execute_fixture(binary: Path, root: Path, tool: str, arguments: dict, duplicate: bool = False):
    requests = []
    errors = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                if self.path != "/v1/chat/completions":
                    raise ValueError(f"unexpected inference path: {self.path}")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("inference request exceeds fixture limit")
                request = json.loads(self.rfile.read(length))
                requests.append(request)
                if len(requests) == 1:
                    call = {"id": "fixture-call", "type": "function", "function": {"name": tool, "arguments": json.dumps(arguments)}}
                    message = {"role": "assistant", "content": None, "tool_calls": [call, call] if duplicate else [call]}
                    finish = "tool_calls"
                else:
                    message = {"role": "assistant", "content": "fixture complete"}
                    finish = "stop"
                response = json.dumps({"id": "fixture", "object": "chat.completion", "created": 0,
                    "model": "scripted-fixture", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
            except Exception as error:
                errors.append(str(error))
                self.send_error(400)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        completed = subprocess.run(
            [str(binary), "run", "fixture", "--input", "Execute the deterministic fixture", "--max-iterations", "3"],
            cwd=root, env={"PATH": "/usr/bin:/bin", "HOME": str(root / "home"), "LANG": "C.UTF-8", "SYMBIONT_ENV": "production",
                "SYMBI_AMBIENT_CANARY": "synthetic-ambient-value",
                "OPENAI_API_KEY": "synthetic-fixture-key", "CHAT_MODEL": "scripted-fixture",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1"},
            capture_output=True, text=True, timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return completed, requests, errors


def run_case(binary: Path, case: tuple, image_reference: str = "python:3.12-slim") -> dict:
    name, tool, arguments, approval, duplicate, expected_effect = case
    with tempfile.TemporaryDirectory(prefix="runtime-dispatch-") as td:
        root = Path(td)
        effects = root / "effects"
        effects.mkdir(mode=0o777)
        effects.chmod(0o777)
        (root / "tools").mkdir()
        (root / "policies" / "run").mkdir(parents=True)
        (root / "agents").mkdir()
        (root / "home").mkdir()
        (root / "agents" / "fixture.symbi").write_text('''metadata {
    version = "1"
    description = "Deterministic dispatch fixture"
}
agent fixture(input: String) -> String { return input; }
''')
        canary = root / "host-canary"
        canary.write_text("synthetic-host-canary")
        payload = root / "fixture.py"
        payload.write_text(f'''import json, os, pathlib, sys
probe = {{"host_file_denied": not pathlib.Path({str(canary)!r}).exists(),
         "ambient_absent": "SYMBI_AMBIENT_CANARY" not in os.environ,
         "non_root": os.getuid() == 65534}}
assert all(probe.values()), probe
pathlib.Path("/workspace", sys.argv[1]).touch()
print(json.dumps(probe))
''')
        payload.chmod(0o444)
        sandbox = f'''[sandbox]
tier = "docker"
[sandbox.docker]
image = "{image_reference}"
volumes = ["{effects}:/workspace:rw", "{payload}:/opt/fixture.py:ro"]
'''
        (root / "symbiont.toml").write_text(sandbox)
        manifest = f'''[tool]
name = "count_fixture"
version = "1"
binary = "/usr/local/bin/python3"
description = "Synthetic file effect"
human_approval = {str(approval).lower()}
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
[command]
template = "/usr/local/bin/python3 /opt/fixture.py '{{count}}'"
[output]
format = "json"
'''
        policy = '''permit(principal, action == Action::"respond", resource);
permit(principal, action == Tool::Fixture::Action::"execute", resource)
when { context.invocation.arguments.count == "5" };
'''
        (root / "tools" / "count_fixture.clad.toml").write_text(manifest)
        (root / "policies" / "run" / "fixture.cedar").write_text(policy)
        completed, requests, errors = execute_fixture(binary, root, tool, arguments, duplicate)
        observed = sorted(path.name for path in effects.iterdir())
        tool_results = [message for request in requests[1:] for message in request.get("messages", []) if message.get("role") == "tool"]
        expected = ["5"] if expected_effect else []
        correlated = bool(tool_results) and all(message.get("tool_call_id") == "fixture-call" for message in tool_results)
        if expected_effect:
            try:
                envelopes = [json.loads(message.get("content", "")) for message in tool_results]
                correct_result = correlated and all(envelope.get("status") == "success"
                    and all(envelope.get("results", {}).get(key) is True for key in ("host_file_denied", "ambient_absent", "non_root"))
                    for envelope in envelopes)
            except (ValueError, AttributeError):
                correct_result = False
        else:
            correct_result = correlated and all(message.get("content", "").startswith("[Policy denied]") for message in tool_results)
        valid = (completed.returncode == 0 and "Completed" in completed.stderr
            and len(requests) == 2 and correct_result and not errors)
        return {"case": name, "trial_id": str(uuid.uuid4()), "valid": valid,
            "passed": valid and observed == expected and canary.read_text() == "synthetic-host-canary", "expected_files": expected, "observed_files": observed,
            "request_count": len(requests), "tool_results": tool_results, "server_errors": errors,
            "exit_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr,
            "sandbox_digest": sha256(sandbox.encode()), "payload_digest": sha256(payload.read_bytes()),
            "canary_intact": canary.read_text() == "synthetic-host-canary",
            "manifest_digest": sha256(manifest.encode()), "policy_digest": sha256(policy.encode())}


def main(*, cases=None, case_runner=None, companion_driver: Path | None = None, suite="shipping-cli-dispatch") -> int:
    cases = CASES if cases is None else cases
    case_runner = run_case if case_runner is None else case_runner
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, required=True, help="Symbiont source checkout to build")
    ap.add_argument("--target-dir", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    source, target = args.source.resolve(), args.target_dir.resolve()
    report = {"suite": suite, "run_id": str(uuid.uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(), "planned_cases": [case[0] for case in cases],
        "containment_claim": False, "trials": [], "status": "invalid"}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.report.exists():
        raise FileExistsError(f"report already exists: {args.report}")
    def save():
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    save()
    try:
        image_id = subprocess.check_output(["docker", "image", "inspect", "--format", "{{.Id}}", "python:3.12-slim"], text=True, timeout=15).strip()
        if not image_id.startswith("sha256:") or len(image_id) != 71:
            raise RuntimeError("cached Docker image has no valid content identity")
        report["container_image"] = {"requested": "python:3.12-slim", "id": image_id}
        before = source_identity(source)
        report["source"] = before
        command = ["cargo", "build", "--locked", "--offline", "--bin", "symbi"]
        report["build_command"] = command
        report["rustc"] = subprocess.check_output(["rustc", "-Vv"], cwd=source, text=True)
        report["cargo"] = subprocess.check_output(["cargo", "-V"], cwd=source, text=True)
        report["driver_digest"] = sha256(Path(__file__).read_bytes())
        driver_sources = [Path(__file__)] + ([companion_driver] if companion_driver else [])
        report["driver_sources"] = {str(path.resolve()): sha256(path.read_bytes()) for path in driver_sources}
        env = os.environ.copy()
        env.update(CARGO_TARGET_DIR=str(target), CARGO_PROFILE_DEV_DEBUG="0", CARGO_BUILD_JOBS="1")
        with args.report.with_suffix(".build.log").open("x") as log:
            result = subprocess.run(command, cwd=source, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=3600)
        if result.returncode != 0:
            raise RuntimeError(f"build failed with exit {result.returncode}")
        if source_identity(source) != before:
            raise RuntimeError("source changed during build")
        binary = target / "debug" / "symbi"
        report["binary"] = {"path": str(binary), "digest": sha256(binary.read_bytes())}
        for case in cases:
            try:
                record = case_runner(binary, case, image_id)
            except Exception as error:
                record = {"case": case[0], "trial_id": str(uuid.uuid4()), "valid": False, "passed": False,
                    "error": f"{type(error).__name__}: {error}"}
            report["trials"].append(record)
            save()
        complete = [trial["case"] for trial in report["trials"]] == report["planned_cases"]
        unchanged = source_identity(source) == before and sha256(binary.read_bytes()) == report["binary"]["digest"]
        unchanged = unchanged and all(sha256(Path(path).read_bytes()) == digest for path, digest in report["driver_sources"].items())
        report["status"] = "passed" if complete and unchanged and all(trial["passed"] for trial in report["trials"]) else "failed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    report["ended_at"] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({"status": report["status"], "trials": len(report["trials"]), "report": str(args.report)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
