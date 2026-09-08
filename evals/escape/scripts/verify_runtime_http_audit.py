#!/usr/bin/env python3
"""Protected audit through the shipping symbi up HTTP webhook."""
import json
import os
from pathlib import Path
import subprocess
import urllib.error
import urllib.request
import uuid

import verify_runtime_scheduler as runtime_driver
import verify_runtime_dispatch as common
import verify_runtime_managed_cli as audit_driver

CASES = [(name,) for name in ("http_payload", "http_resource_limits", "http_audit_storage", "http_audit_write_failure", "http_provider_error")]


def run_case(binary, case, image):
    name = case[0]
    trusted = {}

    def setup(root):
        if name == "http_resource_limits":
            profile = root/"symbiont.toml"
            profile.write_text(profile.read_text() + 'memory_limit="1g"\ncpu_limit=2.0\n')
            path = root/"tools/record_payload.clad.toml"
            manifest = path.read_text()
            needle = '"uid":os.getuid(),'
            assert needle in manifest
            manifest = manifest.replace(needle, '"memory":int(Path("/sys/fs/cgroup/memory.max").read_text()),"cpu":Path("/sys/fs/cgroup/cpu.max").read_text().strip(),'+needle)
            path.write_text(manifest)
        audit = root/".symbiont/governed"
        audit.mkdir(parents=True, mode=0o700)
        if name == "http_audit_storage":
            audit.chmod(0o777)
            return
        # Provision a synthetic project key before the runtime starts so the
        # observer's public anchor is independent of any returned evidence.
        key = os.urandom(32)
        path = audit/"audit-signing.key"
        path.write_bytes(key)
        path.chmod(0o600)
        private = audit/"fixture.der"
        private.write_bytes(bytes.fromhex("302e020100300506032b657004220420") + key)
        private.chmod(0o600)
        trusted["public"] = subprocess.check_output(["openssl", "pkey", "-inform", "DER", "-in", str(private), "-pubout", "-outform", "DER"])[-32:].hex()
        private.unlink()

    def before_inference(root, _):
        if name == "http_provider_error":
            return {"error": {"message": "synthetic invalid inference response"}}
        if name == "http_audit_write_failure":
            journals = list((root/".symbiont/governed").glob("*.jsonl"))
            assert len(journals) == 1
            (root/"signed-prefix.jsonl").write_bytes(journals[0].read_bytes())
            with journals[0].open("ab") as stream:
                stream.write(b"synthetic unexpected write\n")
        return None

    def invocation(root, port, agent_id, requests, record):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def call(token):
            request = urllib.request.Request(f"http://127.0.0.1:{port}/webhook",
                data=json.dumps({"prompt":json.dumps({"token":token,"delay":"0"})}).encode(),
                headers={"Authorization":"Bearer synthetic-webhook-token", "Content-Type":"application/json"})
            try:
                response = opener.open(request, timeout=40)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                return response.code, json.load(response)
        status, body = call(name)
        record["http_status"] = status
        if name in ("http_payload", "http_resource_limits"):
            audits = []
            for token, response_status, response in [(name,status,body), (name+"_second",*call(name+"_second"))]:
                assert response_status == 200 and response["status"] == "completed", (response_status,response)
                value = json.loads((root/"effects"/(token+".json")).read_text())
                expected = {"token":token,"uid":65534,"host_visible":False,"credential_visible":False}
                if name == "http_resource_limits":
                    cpu = [int(part) for part in value["cpu"].split()]
                    assert len(cpu) == 2 and cpu[0] == cpu[1] and cpu[1] > 0, value
                    expected.update(memory=512*1024*1024, cpu=value["cpu"])
                assert value == expected, value
                audit = response["audit"]
                assert audit["public_key"] == trusted["public"]
                path = Path(audit["path"])
                assert path.resolve().is_relative_to(root/".symbiont/governed")
                entries = audit_driver.verify_journal(path, trusted["public"], run_id=audit["run_id"])
                assert all(entry["agent_id"] == agent_id for entry in entries)
                assert entries[-1]["event"]["Terminated"]["reason"] == "Completed"
                try:
                    audit_driver.verify_journal(path, trusted["public"], run_id=str(uuid.uuid4()))
                except ValueError:
                    pass
                else:
                    raise AssertionError("substituted invocation was accepted")
                audits.append(dict(run_id=audit["run_id"], journal_hash=common.sha256(path.read_bytes()), effect=value))
            assert audits[0]["run_id"] != audits[1]["run_id"] and len(requests) == 4
            record["audits"] = audits
        else:
            assert status == 500 and body.get("status") != "completed", (status,body)
            assert not list((root/"effects").iterdir()), "failure produced a tool effect"
            journals = list((root/".symbiont/governed").glob("*.jsonl"))
            if name == "http_audit_storage":
                assert not requests and not journals
                record["failure_before_inference"] = True
            else:
                assert len(requests) == 1 and len(journals) == 1
                path = journals[0]
                run_id = path.name.split(".")[1]
                if name == "http_audit_write_failure":
                    entries = audit_driver.verify_journal(root/"signed-prefix.jsonl", trusted["public"], run_id=run_id)
                    assert entries and all("Terminated" not in entry["event"] for entry in entries)
                    try:
                        audit_driver.verify_journal(path, trusted["public"], run_id=run_id)
                    except (ValueError,subprocess.CalledProcessError):
                        pass
                    else:
                        raise AssertionError("corrupted complete journal was accepted")
                    record["corruption_rejected"] = True
                else:
                    entries = audit_driver.verify_journal(path, trusted["public"], run_id=run_id)
                    assert "Error" in entries[-1]["event"]["Terminated"]["reason"]
                    record["signed_provider_failure"] = True
                record["journal_hash"] = common.sha256(path.read_bytes())
        record["public_key"] = trusted.get("public")

    return runtime_driver.run_case(binary, case, image, fixture_setup=setup, before_inference=before_inference, invocation=invocation)


if __name__ == "__main__":
    raise SystemExit(common.main(cases=CASES, case_runner=run_case, companion_driver=Path(__file__),
        additional_drivers=[Path(runtime_driver.__file__),Path(audit_driver.__file__)], suite="shipping-http-required-audit"))
