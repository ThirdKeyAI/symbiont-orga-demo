"""Real shipping PTY sessions with local inference and a provisioned VM fixture."""
from pathlib import Path
import json
import shlex
import tempfile
import time
import uuid
import verify_runtime_dispatch as common
import verify_runtime_audit as audit

CASES = ["pty_persistent_state", "pty_long_line", "pty_background_cleanup",
         "pty_policy_denied", "pty_approval_missing", "pty_unadvertised",
         "pty_control_frame", "pty_extra_argument", "pty_startup_timeout",
         "pty_deadline", "pty_stdout_limit", "pty_stderr_limit",
         "pty_interaction_exhaustion", "pty_nonzero_exit"]
DENIED = {"pty_policy_denied", "pty_approval_missing", "pty_unadvertised",
          "pty_control_frame", "pty_extra_argument"}
PROOF = {"pty", "non_root", "controlling_terminal", "foreground", "echo_off",
         "canonical_off", "dimensions", "host_file_denied", "ambient_absent",
         "no_network_device", "no_new_privileges"}


def payload(message):
    envelope = json.loads(message["content"])
    assert envelope["status"] == "success" and envelope["execution_status"] == "prompt_observed"
    assert envelope["exit_code"] is None and envelope["session_id"]
    output = envelope["results"]["output"]
    assert "terminal-stderr" in output
    values = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
    assert len(values) == 1
    value = values[0]
    assert set(value["proof"]) == PROOF and all(v is True for v in value["proof"].values())
    return envelope, value


def run_case(binary, name, artifacts):
    result = dict(case=name, trial_id=str(uuid.uuid4()), valid=False, passed=False)
    with tempfile.TemporaryDirectory(prefix="fpty-", dir="/tmp") as directory:
        root = Path(directory)
        for folder in ["tools", "agents", "policies/run", "home"]:
            (root / folder).mkdir(parents=True)
        canary = root / "host-canary"
        canary.write_text("synthetic-host-only")
        source = 'agent fixture(input: String) -> String { with { return input; } }'
        if name == "pty_deadline":
            source = 'agent fixture() { with sandbox = "firecracker", timeout = 2.seconds {} }'
        (root / "agents/fixture.symbi").write_text(source)
        configuration = ['[sandbox]', 'tier = "firecracker"', '[sandbox.firecracker]']
        for key, value in dict(kernel_image_path=str(artifacts["kernel"]),
                              rootfs_path=str(artifacts["rootfs"]),
                              firecracker_binary=str(artifacts["firecracker"]),
                              rootfs_read_only=True, vcpus=1, mem_mib=256,
                              max_output_bytes=4096 if name in ("pty_stdout_limit", "pty_stderr_limit") else 65536).items():
            configuration.append(key + " = " + json.dumps(value))
        configuration += ['max_execution_time = { secs = 12, nanos = 0 }',
                          'startup_timeout = { secs = 4, nanos = 0 }',
                          '[sandbox.firecracker.supervisor]', 'state_dir = ' + json.dumps(str(root / "leases"))]
        (root / "symbiont.toml").write_text("\n".join(configuration) + "\n")
        startup = shlex.join(["/bin/pty_fixture", str(canary)])
        if name == "pty_startup_timeout": startup = "/bin/sleep 30"
        manifest = f'''[tool]
name = "count_fixture"
mode = "session"
version = "1"
description = "Actual guest terminal effects"
timeout_seconds = 5
[tool.cedar]
resource = "Tool::Fixture"
action = "execute"
[session]
startup_command = {json.dumps(startup)}
ready_pattern = "READY>"
startup_timeout_seconds = {1 if name == "pty_startup_timeout" else 4}
idle_timeout_seconds = 10
session_timeout_seconds = 10
max_interactions = {1 if name == "pty_interaction_exhaustion" else 8}
[session.interaction]
output_wait_ms = 1000
output_max_bytes = 65536
[session.commands.send]
pattern = "(?:add [1-9]|echo .+|hang|flood|stderr|background|exit)"
description = "Bounded terminal fixture command"
human_approval = {str(name == "pty_approval_missing").lower()}
[output]
format = "text"
'''
        (root / "tools/count_fixture.clad.toml").write_text(manifest)
        policy = 'permit(principal, action == Action::"respond", resource);\npermit(principal, action == Tool::Fixture::Action::"execute", resource) when { context.invocation.arguments.command != "add 9" };\n'
        (root / "policies/run/fixture.cedar").write_text(policy)
        exact = "字x" * 4096
        commands = {
            "pty_persistent_state": ["add 1", "add 2"],
            "pty_long_line": ["add 1", "echo " + exact],
            "pty_background_cleanup": ["add 1", "background"],
            "pty_policy_denied": ["add 9"],
            "pty_control_frame": ["add 1\nadd 2"],
            "pty_deadline": ["add 1", "hang"],
            "pty_stdout_limit": ["add 1", "flood"],
            "pty_stderr_limit": ["add 1", "stderr"],
            "pty_interaction_exhaustion": ["add 1", "add 2"],
            "pty_nonzero_exit": ["add 1", "exit"],
        }.get(name, ["add 1"])
        tool = "unknown_fixture" if name == "pty_unadvertised" else "count_fixture.send"
        sequence = [(tool, {"command": command}) for command in commands]
        if name == "pty_extra_argument": sequence[0][1]["extra"] = "unexpected"
        result["fixture_hashes"] = {str(path.relative_to(root)): common.sha256(path.read_bytes()) for path in
            [root / "symbiont.toml", root / "agents/fixture.symbi", root / "tools/count_fixture.clad.toml", root / "policies/run/fixture.cedar"]}
        result["commands"] = commands
        start = time.monotonic()
        completed, requests, errors = common.execute_fixture(binary, root, "", {}, sequence=sequence)
        messages = [m for m in requests[-1].get("messages", []) if m.get("role") == "tool"] if requests else []
        remaining = list((root / "leases").glob("*.json")) + list((root / "leases").glob("vm-*"))
        result.update(seconds=time.monotonic() - start, exit_code=completed.returncode,
                      stdout=completed.stdout, stderr=completed.stderr, inference_requests=len(requests),
                      server_errors=errors, tool_results=messages, owned_vm_state_remaining=[p.name for p in remaining],
                      host_canary_intact=canary.read_text() == "synthetic-host-only")
        try:
            assert result["host_canary_intact"] and not remaining and not errors
            expected_count = 1 if name == "pty_deadline" else len(commands)
            assert len(requests) == expected_count + 1 and len(messages) == expected_count
            assert [m["tool_call_id"] for m in messages] == [f"fixture-call-{i}" for i in range(expected_count)]
            reference = audit.reference(completed, root)
            entries = audit.audit_driver.verify_journal(Path(reference["path"]), reference["public_key"], run_id=reference["run_id"])
            reason = entries[-1]["event"].get("Terminated", {}).get("reason")
            expected_reason = "Completed"
            if name in DENIED:
                assert all(m["content"].startswith("[Policy denied]") for m in messages)
            elif name == "pty_startup_timeout":
                assert messages[0]["content"].startswith("[Error]") and "terminal startup timed out" in messages[0]["content"]
            else:
                first, value = payload(messages[0])
                assert value["value"] == 1
                result["guest_effect"] = value
                if name == "pty_deadline":
                    assert completed.returncode == 1
                    assert reason == "Timeout"
                    expected_reason = "Timeout"
                elif name == "pty_interaction_exhaustion":
                    assert messages[1]["content"].startswith("[Error]") and "closed" in messages[1]["content"]
                elif name in ("pty_stdout_limit", "pty_stderr_limit", "pty_nonzero_exit"):
                    expected = "exit 17" if name == "pty_nonzero_exit" else "stdout_truncated=true"
                    assert messages[1]["content"].startswith("[Error]") and expected in messages[1]["content"]
                else:
                    second, value = payload(messages[1])
                    assert second["session_id"] == first["session_id"] and value["cookie"] == result["guest_effect"]["cookie"]
                    assert value["value"] == (3 if name == "pty_persistent_state" else 1)
                    if name == "pty_long_line": assert value["echo"] == exact
                    result["guest_effect"] = value
            assert completed.returncode == (0 if expected_reason == "Completed" else 1)
            result["audit"] = audit.verify(reference, expected_reason)
            result.update(valid=True, passed=True)
        except (AssertionError, KeyError, TypeError, ValueError, IndexError) as error:
            result["error"] = repr(error)
    return result
