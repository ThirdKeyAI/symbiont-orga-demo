#!/usr/bin/env python3
"""Required audit storage through the shipping CLI with independent verification."""
from pathlib import Path
import re
import subprocess
import uuid

import verify_runtime_dispatch as common
import verify_runtime_managed_cli as audit_driver

CASES = [(name,) for name in ("allowed_and_restart", "unsafe_directory", "symlink_directory", "write_failure", "deadline")]


def reference(completed, root):
    run = re.search(r"(?m)^Audit run: ([0-9a-f-]{36})$", completed.stderr)
    path = re.search(r"(?m)^Audit journal: (.+)$", completed.stderr)
    public = re.search(r"(?m)^Audit public key: ([0-9a-f]{64})$", completed.stderr)
    if not (run and path and public):
        raise ValueError("missing runtime audit reference")
    result = dict(run_id=run[1], path=path[1], public_key=public[1])
    if not Path(result["path"]).resolve().is_relative_to(root/".symbiont/governed"):
        raise ValueError("journal is outside the protected project directory")
    return result


def verify(reference, expected):
    path = Path(reference["path"])
    entries = audit_driver.verify_journal(path, reference["public_key"], run_id=reference["run_id"])
    if entries[-1]["event"].get("Terminated", {}).get("reason") != expected:
        raise ValueError("wrong signed terminal outcome")
    try:
        audit_driver.verify_journal(path, reference["public_key"], run_id=str(uuid.uuid4()))
    except ValueError:
        pass
    else:
        raise ValueError("journal accepted a different invocation identity")
    return dict(run_id=reference["run_id"], public_key=reference["public_key"],
                journal_hash=common.sha256(path.read_bytes()), records=len(entries))


def run_case(binary, case, image):
    name = case[0]
    expected_effect = name == "allowed_and_restart"
    options = {}
    if name in ("unsafe_directory", "symlink_directory"):
        def setup(root):
            parent = root/".symbiont"
            parent.mkdir(mode=0o700)
            if name == "unsafe_directory":
                audit = parent/"governed"
                audit.mkdir()
                audit.chmod(0o777)
            else:
                target = root/"audit-target"
                target.mkdir(mode=0o700)
                (parent/"governed").symlink_to(target, target_is_directory=True)
        options.update(fixture_setup=setup, preflight_error="Required audit initialization failed")
    if name == "write_failure":
        def corrupt(root, step, _):
            if step == 0:
                journals = list((root/".symbiont/governed").glob("*.jsonl"))
                assert len(journals) == 1
                (root/"signed-prefix.jsonl").write_bytes(journals[0].read_bytes())
                with journals[0].open("ab") as stream:
                    stream.write(b"synthetic unexpected write\n")
        options.update(before_response=corrupt, execution_failure="Required journal write failed")
    if name == "deadline":
        options.update(delayed_effect=True, agent_source='agent fixture() { with sandbox = "docker", timeout = 1.seconds {} }')

    def evidence(root, completed, requests):
        if name in ("unsafe_directory", "symlink_directory"):
            assert not list((root/".symbiont/governed").glob("*.jsonl"))
            assert not requests
            return {"passed": True, "failure_before_inference": True}
        audit = reference(completed, root)
        if name == "write_failure":
            prefix = audit_driver.verify_journal(root/"signed-prefix.jsonl", audit["public_key"], run_id=audit["run_id"])
            assert prefix and all("Terminated" not in entry["event"] for entry in prefix)
            try:
                audit_driver.verify_journal(Path(audit["path"]), audit["public_key"], run_id=audit["run_id"])
            except (ValueError, subprocess.CalledProcessError):
                pass
            else:
                raise ValueError("corrupted complete journal was accepted")
            return {"passed": True, "signed_prefix_records": len(prefix), "corruption_rejected": True}
        first = verify(audit, "Timeout" if name == "deadline" else "Completed")
        if name == "deadline":
            return {"passed": True, "audit": first}
        (root/"effects/5").unlink()
        second, second_requests, errors = common.execute_fixture(binary, root, "count_fixture", {"count": "999"})
        assert second.returncode == 0 and len(second_requests) == 2 and not errors
        assert (root/"effects/5").exists(), "second process did not perform the allowed effect"
        second_audit = reference(second, root)
        last = verify(second_audit, "Completed")
        assert first["run_id"] != last["run_id"] and first["public_key"] == last["public_key"]
        return {"passed": True, "first": first, "second": last, "restart_effect": True}

    options["evidence_verifier"] = evidence
    return common.run_case(binary, (name, "count_fixture", {"count":"999"}, False, False, expected_effect), image, **options)


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__),
        additional_drivers=[Path(audit_driver.__file__)], suite="shipping-required-audit"))
