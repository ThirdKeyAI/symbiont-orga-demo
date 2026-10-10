#!/usr/bin/env python3
"""Run protected capability-matched trials against a verified shipping binary."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from harnesses.matched.lab import CASES, compare_pair, digest, run_trial, toolclad_api
from harnesses.matched.evidence import verify_report
from verify_runtime_dispatch import source_identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--build-record", type=Path, required=True)
    parser.add_argument("--toolclad-source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=[case[0] for case in CASES])
    args = parser.parse_args()
    source, binary, toolclad = args.source.resolve(), args.binary.resolve(), args.toolclad_source.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    selected = [case for case in CASES if not args.cases or case[0] in args.cases]
    report = {"suite": "matched-protected-lab", "status": "invalid", "containment_claim": False,
              "planned_cases": [case[0] for case in selected], "trials": [], "pairs": []}
    report_path = output / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    save()
    try:
        build = json.loads(args.build_record.read_text())
        shipping = source_identity(source)
        if (shipping["commit"] != build["commit"] or digest(binary.read_bytes()) != build["binary_sha256"]
                or subprocess.check_output(["git", "status", "--porcelain"], cwd=source).strip()):
            raise ValueError("prebuilt binary/source do not match the supplied build record")
        for name, expected in build["files"].items():
            if digest((source / name).read_bytes()) != expected:
                raise ValueError("source differs from the recorded build inputs")
        reference = source_identity(toolclad)
        api = toolclad_api(toolclad)
        image = subprocess.check_output(["docker", "image", "inspect", "escape-eval-sandboxed:latest", "--format", "{{.Id}}"],
                                        text=True, timeout=10).strip()
        root = Path(__file__).resolve().parents[1]
        paths = [Path(__file__), root / "scripts/verify_runtime_dispatch.py"]
        paths += [root / name for name in (
            "harnesses/matched/lab.py", "harnesses/matched/payload.py", "harnesses/matched/evidence.py",
            "harnesses/common/protected_trace.py", "harnesses/common/trace_gate.py",
            "harnesses/common/observation_session.py", "harnesses/common/confined.py",
            "detectors/fs_detector.py", "detectors/strace.py")]
        report.update(shipping_source=shipping, reference_source=reference,
                      binary_sha256=digest(binary.read_bytes()), build_record_sha256=digest(args.build_record.read_bytes()),
                      reused_build=True, observer_image=image,
                      driver_sources={str(path): digest(path.read_bytes()) for path in paths})
        # Keep the supervisor's Unix socket below the platform path limit,
        # independently of the operator's chosen report directory depth.
        state_dir = Path(tempfile.mkdtemp(prefix="ml-state-"))
        report["supervisor_state_dir"] = str(state_dir)
        for case in selected:
            pair = []
            for arm in ("python_toolclad", "symbi_shipping"):
                trial = run_trial(case, arm, binary=binary, image=image,
                                  directory=output / case[0] / arm, api=api,
                                  supervisor_state_dir=state_dir)
                report["trials"].append(trial)
                pair.append(trial)
                save()
            report["pairs"].append({"case": case[0], **compare_pair(*pair)})
            save()
        unchanged = (source_identity(source) == shipping and source_identity(toolclad) == reference
                     and digest(binary.read_bytes()) == report["binary_sha256"]
                     and all(digest(Path(path).read_bytes()) == sha for path, sha in report["driver_sources"].items()))
        save()
        report["integrity"] = verify_report(output)
        report["status"] = "passed" if (unchanged and len(report["pairs"]) == len(selected)
            and all(pair.get("valid") and pair.get("expectation_met") and pair.get("task_completed")
                    for pair in report["pairs"])) else "failed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    save()
    print(json.dumps({"status": report["status"], "pairs": len(report["pairs"]), "report": str(report_path)}))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
