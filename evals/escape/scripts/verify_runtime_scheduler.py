#!/usr/bin/env python3
"""Shipping scheduler execution through symbi up and its authenticated API."""
import concurrent.futures
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import verify_runtime_dispatch as common
import verify_runtime_managed_cli as audit_driver
from verify_runtime_managed_cli import verify_journal

CASES = [(name,) for name in ("manual_payload", "timer_payload", "mandatory_approval", "policy_denial",
                             "missing_provider", "unavailable_selected", "cancel_active")]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def run_case(binary, case, image):
    name = case[0]
    root = Path(tempfile.mkdtemp(prefix="symbiont-scheduler-e2e-"))
    for directory in ("effects", "home", "agents", "tools", "policies"):
        (root/directory).mkdir()
    (root/"effects").chmod(0o777)
    (root/"host-canary").write_text("synthetic-host-canary")
    source = 'agent fixture() { with sandbox = "docker", timeout = 20.seconds {} }\n'
    (root/"agents/fixture.symbi").write_text(source)
    policy = 'permit(principal, action == Action::"respond", resource);' if name == "policy_denial" else "permit(principal, action, resource);"
    (root/"policies/fixture.cedar").write_text(policy)
    profile = '[sandbox]\ntier="docker"\n[sandbox.docker]\nimage='+json.dumps(image)+'\nvolumes=['+json.dumps(str(root/"effects")+':/workspace:rw')+']\n'
    (root/"symbiont.toml").write_text(profile)
    code = ('from pathlib import Path; import sys,os,time,json; token=sys.argv[1]; p=Path("/workspace"); '
            '(p/(token+".started")).write_text("started"); print("started",flush=True); time.sleep(int(sys.argv[2])); '
            'value={"token":token,"uid":os.getuid(),"host_visible":Path('+json.dumps(str(root/"host-canary"))+').exists(),'
            '"credential_visible":"OPENAI_API_KEY" in os.environ}; text=json.dumps(value); '
            '(p/(token+".json")).write_text(text); print(text)')
    manifest = '''[tool]
name="record_payload"
version="1"
description="Record a scheduled test payload"
binary="python3"
timeout_seconds=15
human_approval=%s
[args.token]
position=1
required=true
type="string"
[args.delay]
position=2
required=true
type="integer"
min=0
max=10
[command]
template=\'\'\'python3 -c '%s' {token} {delay}\'\'\'
[output]
format="text"
''' % (str(name == "mandatory_approval").lower(), code)
    (root/"tools/record_payload.clad.toml").write_text(manifest)
    requests, errors = [], []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                if self.path != "/v1/chat/completions":
                    raise ValueError("unexpected inference path")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024*1024 or len(requests) >= 12:
                    raise ValueError("fixture inference budget exceeded")
                body = json.loads(self.rfile.read(length))
                requests.append(body)
                tool_results = [message for message in body["messages"] if message["role"] == "tool"]
                if any(message["role"] == "assistant" for message in body["messages"]):
                    feedback = tool_results[-1] if tool_results else body["messages"][-1]
                    message = {"role": "assistant", "content": feedback["content"]}
                    finish = "stop"
                else:
                    user = next(m["content"] for m in body["messages"] if m["role"] == "user")
                    payload = json.loads(user)
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "scheduled-effect", "type": "function",
                        "function": {"name": "record_payload", "arguments": json.dumps(payload)}}]}
                    finish = "tool_calls"
                response = json.dumps({"id": "fixture", "object": "chat.completion", "created": 0, "model": "scripted-fixture",
                    "choices": [{"index": 0, "message": message, "finish_reason": finish}],
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
    api, webhook = free_port(), free_port()
    api_token = "synthetic-admin-"+uuid.uuid4().hex
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, data=None, method=None):
        request = urllib.request.Request(f"http://127.0.0.1:{api}/api/v1"+path,
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={"Authorization": "Bearer "+api_token, "Content-Type": "application/json"})
        try:
            response = opener.open(request, timeout=40)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.code, json.load(response)

    def eventually(predicate, timeout=35):
        deadline = time.monotonic()+timeout
        while True:
            value = predicate()
            if value:
                return value
            if time.monotonic() >= deadline:
                raise TimeoutError("expected scheduler state was not observed")
            time.sleep(.05)

    env = {"PATH": "/usr/bin:/bin", "HOME": str(root/"home"), "XDG_DATA_HOME": str(root/"home/data"),
        "XDG_CONFIG_HOME": str(root/"home/config"), "LANG": "C.UTF-8", "SYMBIONT_ENV": "production",
        "SYMBIONT_API_TOKEN": api_token, "SYMBIONT_MASTER_KEY": "0"*64,
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/tmp/symbiont-no-test-keyring"}
    if name != "missing_provider":
        env.update(OPENAI_API_KEY="synthetic-fixture-key", CHAT_MODEL="scripted-fixture",
                   OPENAI_BASE_URL=f"http://127.0.0.1:{server.server_port}/v1")
    record = {"case": name, "trial_id": str(uuid.uuid4()), "valid": False, "passed": False,
        "fixture": str(root), "agent_hash": common.sha256(source.encode()), "manifest_hash": common.sha256(manifest.encode()),
        "policy_hash": common.sha256(policy.encode()), "sandbox_hash": common.sha256(profile.encode()), "image": image}
    with (root/"server.log").open("w") as log:
        process = subprocess.Popen([str(binary), "up", "--port", str(api), "--http-port", str(webhook),
            "--http-bind", "127.0.0.1", "--http.token", "synthetic-webhook-token"], cwd=root, env=env,
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            def ready():
                if process.poll() is not None:
                    raise RuntimeError("runtime exited during startup")
                try:
                    status, agents = call("/agents")
                    return agents if status == 200 else None
                except (OSError, urllib.error.URLError):
                    return None
            agents = eventually(ready, 60)
            time.sleep(.2)
            assert not requests, "registration unexpectedly invoked inference"
            entries = agents["agents"] if isinstance(agents, dict) else agents
            agent = next(agent for agent in entries if agent["name"] == "fixture")
            agent_id = agent["id"]
            if name == "unavailable_selected":
                status, _ = call("/agents/"+agent_id, {"dsl": 'agent fixture() { with sandbox = "firecracker" {} }'}, "PUT")
                assert status == 200, status
            payload = {"token": name, "delay": "8" if name == "cancel_active" else "2" if name == "timer_payload" else "0"}
            record["input_hash"] = common.sha256(json.dumps(payload, sort_keys=True).encode())
            status, job = call("/schedules", {"name": name, "agent_name": "fixture", "timezone": "UTC",
                "cron_expression": "* * * * * *" if name == "timer_payload" else "0 0 0 1 1 * 2099",
                "input": payload, "policy_ids": [], "one_shot": name == "timer_payload"})
            assert status == 201, (status, job)
            job_id = job["job_id"]

            def history():
                status, body = call("/schedules/"+job_id+"/history")
                assert status == 200, (status, body)
                return body["history"]

            if name == "timer_payload":
                eventually(lambda: (root/"effects"/(name+".started")).exists())
                running = history()
                assert len(running) == 1 and running[0]["status"] == "Running", running
                record["observed_running"] = True
            elif name == "cancel_active":
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    trigger = pool.submit(call, "/schedules/"+job_id+"/trigger", {})
                    eventually(lambda: (root/"effects"/(name+".started")).exists())
                    status, _ = call("/agents/"+agent_id, method="DELETE")
                    assert status == 200, status
                    record["trigger_status"] = trigger.result(timeout=35)[0]
            else:
                record["trigger_status"] = call("/schedules/"+job_id+"/trigger", {})[0]

            def terminal():
                runs = history()
                return runs if len(runs) == 1 and runs[0]["status"] != "Running" else None
            runs = eventually(terminal)
            result = runs[0]
            record["result"] = result
            execution = result.get("execution")
            effects = root/"effects"/(name+".json")
            if name in ("manual_payload", "timer_payload"):
                assert result["status"] == "Succeeded", result
                value = json.loads(effects.read_text())
                assert value == {"token": name, "uid": 65534, "host_visible": False, "credential_visible": False}, value
                assert name in execution["output"], execution
                assert len(requests) == 2, len(requests)
                record["effect"] = value
            elif name in ("missing_provider", "unavailable_selected"):
                expected = "no inference provider" if name == "missing_provider" else "unavailable"
                assert result["status"] == "Failed" and expected in result["error"], result
                assert not requests and not effects.exists()
            else:
                assert not effects.exists(), "denied or cancelled effect completed"
                if name == "cancel_active":
                    assert execution["status"] == "Terminated", execution
                    time.sleep(9)
                    assert not effects.exists(), "worker survived cancellation"
                else:
                    assert len(requests) == 2 and execution["status"] == "Completed", execution
                    assert not (root/"effects"/(name+".started")).exists()
            if execution and execution.get("audit"):
                audit = execution["audit"]
                path = Path(audit["path"])
                assert path.resolve().is_relative_to(root/".symbiont/governed")
                entries = verify_journal(path, audit["public_key"])
                assert entries[-1]["event"].get("Terminated") is not None
                assert all(entry["agent_id"] == agent_id for entry in entries)
                if name in ("mandatory_approval", "policy_denial"):
                    expected = "required approval relay is unavailable" if name == "mandatory_approval" else "Cedar denied action"
                    assert expected in json.dumps(entries), "expected refusal is absent from signed evidence"
                    record["refusal"] = expected
                record["journal_hash"] = common.sha256(path.read_bytes())
                record["journal_records"] = len(entries)
            elif name not in ("missing_provider", "unavailable_selected"):
                raise AssertionError("execution lacks a protected audit")
            if name == "manual_payload":
                # The direct agent API uses the same real scheduler and records
                # its terminal status under the admission's actual run identity.
                direct_payload = {"token": "direct_payload", "delay": "0"}
                status, direct = call("/agents/"+agent_id+"/execute", {"input": direct_payload})
                assert status == 200 and direct["status"] == "queued", (status, direct)
                direct_id = direct["execution_id"]
                assert direct_id != execution["run_id"]
                def direct_completed():
                    status, history = call("/agents/"+agent_id+"/history")
                    assert status == 200, status
                    return any(entry["execution_id"] == direct_id and entry["status"] == "Completed" for entry in history["history"])
                eventually(direct_completed)
                value = json.loads((root/"effects/direct_payload.json").read_text())
                assert value == {"token": "direct_payload", "uid": 65534, "host_visible": False, "credential_visible": False}, value
                direct_journal = root/".symbiont/governed"/(agent_id+"."+direct_id+".jsonl")
                direct_entries = verify_journal(direct_journal, execution["audit"]["public_key"])
                assert direct_entries[-1]["event"]["Terminated"]["reason"] == "Completed"
                assert all(entry["agent_id"] == agent_id for entry in direct_entries)
                assert len(requests) == 4
                record["direct_execution"] = {"run_id": direct_id, "effect": value,
                    "journal_hash": common.sha256(direct_journal.read_bytes())}
            assert not errors, errors
            assert (root/"host-canary").read_text() == "synthetic-host-canary"
            record.update(valid=True, passed=True)
        except Exception as error:
            record["error"] = f"{type(error).__name__}: {error}"
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=40)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
                    record.update(valid=False, passed=False, cleanup_error="runtime shutdown timed out")
    try:
        leases = sorted(str(path) for path in (root/"home/.symbiont/sandbox-leases").glob("*.json"))
        ids = subprocess.check_output(["docker", "ps", "-aq", "--no-trunc"], text=True, timeout=15).split()
        owned = []
        if ids:
            containers = json.loads(subprocess.check_output(["docker", "inspect", *ids], text=True, timeout=15))
            for container in containers:
                if any(Path(mount.get("Source", "/")).is_relative_to(root) for mount in container.get("Mounts", [])):
                    owned.append(container["Id"])
        record["remaining_leases"] = leases
        record["remaining_containers"] = owned
        if leases or owned:
            raise RuntimeError("scheduled workers or leases remain after shutdown")
    except Exception as error:
        record.update(valid=False, passed=False, cleanup_error=str(error))
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)
    record["inference_requests"] = len(requests)
    record["provider_errors"] = errors
    record["request_hashes"] = [common.sha256(json.dumps(request, sort_keys=True).encode()) for request in requests]
    return record


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__), suite="shipping-scheduler",
        additional_drivers=[Path(audit_driver.__file__)]))
