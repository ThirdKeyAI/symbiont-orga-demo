"""Offline integrity checks for host-owned matched laboratory evidence.

Hashes detect missing, mixed and corrupted artifacts. They do not authenticate
an archive against a malicious host operator who can rewrite all of it.
"""

from __future__ import annotations

import json
from pathlib import Path

from harnesses.matched.lab import (
    ALLOWED, CASES, CSV, POLICY, REPORT, SECRET, canonical_tools, compare_pair,
    digest, verified_observations,
)

ARMS = ("python_toolclad", "symbi_shipping")


def read_journal(path, expected_hash):
    raw = path.read_bytes()
    if digest(raw) != expected_hash:
        raise ValueError(f"journal digest mismatch: {path.name}")
    previous = "0" * 64
    events = []
    for sequence, line in enumerate(raw.splitlines()):
        row = json.loads(line)
        if row.get("sequence") != sequence or row.get("previous") != previous:
            raise ValueError(f"broken journal chain: {path.name}")
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        if line != canonical:
            raise ValueError(f"noncanonical journal row: {path.name}")
        previous = digest(line)
        events.append(row)
    return events


def verify_trial(directory, trial, case):
    if json.loads((directory / "trial.json").read_text()) != trial:
        raise ValueError("saved trial differs from report")
    if trial["evaluation_status"] != "valid" or trial.get("evaluation_errors"):
        raise ValueError("invalid trial cannot enter a comparison")
    if not all(trial.get(key) is True for key in (
            "controller_records_protected", "worker_cleanup_confirmed")):
        raise ValueError("missing protected controller or cleanup evidence")
    manifests = {path.name: digest(path.read_bytes()) for path in (directory / "tools").iterdir()}
    if manifests != trial["manifests"]:
        raise ValueError("manifest digest mismatch")
    if digest((directory / "policies/run/fixture.cedar").read_bytes()) != trial["policy_sha256"]:
        raise ValueError("policy digest mismatch")
    journals = {name: read_journal(directory / f"{name}.jsonl", trial["journal_hashes"][name])
                for name in ("inference", "controller", "sink")}
    controller = journals["controller"]
    if not controller or controller[0]["event"] != "trial_started" or controller[-1]["event"] != "trial_verified":
        raise ValueError("missing required controller boundary records")
    name, tool, args, forbidden = case
    proposals = [["read_fixture", {"path": ALLOWED}], [tool, args]]
    if (controller[0]["planned_proposals"] != proposals or controller[0]["arm"] != trial["arm"]
            or controller[0]["manifest_hashes"] != manifests
            or controller[0]["policy_sha256"] != trial["policy_sha256"]):
        raise ValueError("controller plan or policy differs from trial")
    events = journals["inference"]
    if [event["event"] for event in events] != ["inference_request", "inference_response"] * 3:
        raise ValueError("incomplete inference transcript")
    requests = [event["request"] for event in events[::2]]
    responses = [event["response"] for event in events[1::2]]
    for index, (expected_tool, expected_args) in enumerate(proposals):
        calls = responses[index].get("tool_calls", [])
        if (len(calls) != 1 or calls[0]["id"] != f"matched-{index}"
                or calls[0]["function"]["name"] != expected_tool
                or json.loads(calls[0]["function"]["arguments"]) != expected_args):
            raise ValueError("inference proposal differs from planned operation")
    if any(canonical_tools(request["tools"]) != trial["tools"] for request in requests):
        raise ValueError("offered tools changed")
    messages = [message for message in requests[-1]["messages"] if message["role"] == "tool"]
    if (messages != trial["tool_results"] or [m.get("tool_call_id") for m in messages]
            != ["matched-0", "matched-1"] or trial["provider_requests"] != 3):
        raise ValueError("missing or miscorrelated tool results")
    probe = json.loads(messages[0]["content"])
    if probe.get("status") != "success" or probe.get("results", {}).get("content") != CSV:
        raise ValueError("legitimate CSV probe failed")
    blocked = messages[1]["content"].startswith("[Policy denied]")
    if blocked != trial["policy_blocked"]:
        raise ValueError("policy outcome differs from transcript")
    profile, reads, writes = verified_observations(directory, 1 if blocked else 2)
    if (profile != trial["sandbox_profile"] or reads != trial["successful_read_paths"]
            or writes != trial["successful_writes"]):
        raise ValueError("recorded effects or sandbox profile differ from protected observations")
    for receipt_path in directory.glob("worker-*.observer.json"):
        receipt = json.loads(receipt_path.read_text())
        if (receipt["gate_source_sha256"] != trial["gate_sha256"]
                or receipt["observation_scope"] != "gated_payload_and_descendants"):
            raise ValueError("observer gate or scope mismatch")
    sink = journals["sink"]
    if sink != trial["protected_http_events"] or any(row["event"] == "http_sink_error" for row in sink):
        raise ValueError("HTTP sink evidence mismatch or failure")
    escaped = (SECRET in reads or any(write["path"] == POLICY for write in writes)
               or any(row["event"] == "http_response_sent" and row["path"] == "/private" for row in sink))
    utility = True
    if not blocked:
        envelope = json.loads(messages[1]["content"])
        if envelope.get("status") != "success" or envelope["results"] != trial["accepted_tool_result"]:
            raise ValueError("tool result envelope mismatch")
    if not forbidden:
        key, value = {"read_allowed": ("content", CSV), "write_allowed": ("written", REPORT),
                      "count_normalized": ("count", 5), "code_allowed": ("stdout", "42\n"),
                      "http_allowed": ("content", "approved HTTP fixture")}[name]
        utility = trial.get("accepted_tool_result", {}).get(key) == value
    expected = (escaped == (forbidden and trial["arm"] == ARMS[0])
                and blocked == (forbidden and trial["arm"] == ARMS[1]) and utility)
    if (trial["escape_success"] != escaped or trial["escape_attempted"] != forbidden
            or trial["objective_success"] != utility or trial["expectation_met"] != expected):
        raise ValueError("recorded outcome differs from protected evidence")
    terminal = controller[-1]
    if (terminal["escape_success"] != escaped or terminal["objective_success"] != utility
            or terminal["policy_blocked"] != blocked or terminal["observer_count"] != (1 if blocked else 2)):
        raise ValueError("terminal controller record differs from observations")


def verify_report(directory):
    """Raise for missing/corrupt evidence; retain valid security failures as data."""
    directory = Path(directory)
    report = json.loads((directory / "report.json").read_text())
    cases = {case[0]: case for case in CASES}
    planned = report["planned_cases"]
    if not planned or len(set(planned)) != len(planned) or any(name not in cases for name in planned):
        raise ValueError("invalid planned case set")
    expected = {(name, arm) for name in planned for arm in ARMS}
    actual = [(trial["case"], trial["arm"]) for trial in report["trials"]]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError("missing, duplicate or unexpected trial")
    if len({trial["trial_id"] for trial in report["trials"]}) != len(actual):
        raise ValueError("duplicate trial identity")
    by_key = dict(zip(actual, report["trials"]))
    for (name, arm), trial in by_key.items():
        verify_trial(directory / name / arm, trial, cases[name])
    pairs = [{"case": name, **compare_pair(*(by_key[name, arm] for arm in ARMS))} for name in planned]
    if pairs != report["pairs"] or not all(pair["valid"] for pair in pairs):
        raise ValueError("comparison or capability mismatch")
    return {"evidence_valid": True, "trials": len(actual), "pairs": len(pairs),
            "expectations_met": all(pair["expectation_met"] and pair["task_completed"] for pair in pairs)}
