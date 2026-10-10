"""Legacy Python comparison arm with bounded broker/container lifetime.

Worker-produced JSON remains untrusted; syscall traces come from a separate observer. This
profile is not a capability-matched shipping-runtime containment evaluation.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import uuid
from pathlib import Path

from harnesses.common.inference_broker import inference_broker
from harnesses.common.protected_trace import ProtectedTrace
from harnesses.python_baseline.tools import build_tools_spec

TRIAL_TIMEOUT_SECONDS = 180
IMAGE = "escape-eval-sandboxed:latest"


def _remove_container(name):
    subprocess.run(["docker", "rm", "--force", name], timeout=15,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check = subprocess.run(["docker", "container", "inspect", name],
                           timeout=15, capture_output=True, text=True)
    if check.returncode == 0 or "No such container" not in check.stderr:
        raise RuntimeError("confined trial container removal was not confirmed")


def run_confined_trial(task, *, model, prompt_variant, instance_seed,
                       results_dir, instance_root):
    endpoint = os.environ.get("ESCAPE_LLM_URL")
    if not endpoint:
        raise ValueError("python_confined requires ESCAPE_LLM_URL")
    instance_root = instance_root.resolve()
    results_dir = results_dir.resolve()
    if results_dir.is_relative_to(instance_root):
        raise ValueError("broker records must be outside the writable instance")
    instance_root.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    (instance_root / "data").mkdir(exist_ok=True)
    evals_dir = Path(__file__).resolve().parents[2]
    task_path = Path(task["_path"]).resolve()
    task_dir = task_path.parent
    extra_task_mount = ([] if task_dir.is_relative_to(evals_dir)
                        else ["-v", f"{task_dir}:{task_dir}:ro"])
    stem = f"trial-python_confined-{instance_seed:04d}"
    out_path = results_dir / f"{stem}.json"
    strace_path = results_dir / f"{stem}.strace"
    worker_out = instance_root / "_trial.json"
    image = subprocess.run(["docker", "image", "inspect", IMAGE, "--format", "{{.Id}}"],
                           check=True, capture_output=True, text=True, timeout=15).stdout.strip()
    if not image.startswith("sha256:") or len(image) != 71:
        raise ValueError("cached worker image identity unavailable")
    name = f"escape-confined-{uuid.uuid4().hex}"
    profile = {
        "image_id": image, "network": "none", "broker": "bounded_chat_completions",
        "trial_timeout_seconds": TRIAL_TIMEOUT_SECONDS,
        "capability_matched": False, "worker_evidence_protected": False,
        "containment_claim": False, "container_cleanup_confirmed": False,
        "syscall_trace_protected": False,
    }
    profile_path = results_dir / f"{stem}.lab.json"
    profile_path.write_text(json.dumps(profile, indent=2) + "\n")
    with inference_broker(endpoint=endpoint,
                          api_key=os.environ.get("ESCAPE_LLM_API_KEY", ""),
                          model=model, tools=build_tools_spec(task),
                          journal_path=results_dir / f"{stem}.inference.jsonl") as socket_dir, \
            ProtectedTrace(image=image, trace_path=strace_path) as observer:
        child = [
            "docker", "create", "--rm", "--pull", "never", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "256m", "--memory-swap", "256m", "--cpus", "1",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "-v", f"{instance_root}:{instance_root}:rw",
            "-v", f"{instance_root / 'data'}:/data:rw",
            "-v", f"{evals_dir}:{evals_dir}:ro", *extra_task_mount,
            "-v", f"{socket_dir}:/inference:ro",
            "-v", f"{observer.control_dir}:/trace-control:ro",
            "-w", str(evals_dir), "-e", f"PYTHONPATH={evals_dir}",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "ESCAPE_LLM_URL=http://llm.local/v1/chat/completions",
            "-e", "ESCAPE_LLM_UDS=/inference/llm.sock", image,
            "/usr/local/bin/python3", "-I", str(evals_dir / "harnesses/common/trace_gate.py"),
            "/trace-control/gate.sock", "/usr/local/bin/python3", "-m", "harnesses.python_baseline",
            "--task", str(task_path), "--model", model,
            "--prompt-variant", str(prompt_variant), "--instance-seed", str(instance_seed),
            "--instance-root", str(instance_root), "--out", str(worker_out),
        ]
        try:
            # Obtain creation acknowledgement before starting agent code, so
            # a client timeout cannot leave an unacknowledged running worker.
            subprocess.run(child, check=True, timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            worker = subprocess.Popen(["docker", "start", "--attach", name],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                observer.attach_and_release(name)
                observer.wait_worker(worker, TRIAL_TIMEOUT_SECONDS)
                profile["syscall_trace_protected"] = True
                profile["observer_receipt"] = str(strace_path.with_suffix(".observer.json"))
            finally:
                if worker.poll() is None:
                    worker.kill()
                worker.wait(timeout=5)
        finally:
            _remove_container(name)
            profile["container_cleanup_confirmed"] = True
            profile_path.write_text(json.dumps(profile, indent=2) + "\n")
    # Refuse absent or substituted artifacts; these checks do not establish
    # authenticity of regular files written by the worker.
    for source, destination, limit in (
        (worker_out, out_path, 4 * 1024 * 1024),
    ):
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
                raise ValueError("invalid worker artifact")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("worker artifact exceeds limit")
            destination.write_bytes(data)
    return out_path, strace_path
