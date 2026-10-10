"""Protected observer on the real shipping CLI's configured Docker worker."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import uuid
from pathlib import Path

import pytest

from detectors.fs_detector import parse_strace_paths
from harnesses.common.confined import _remove_container
from harnesses.common.protected_trace import ProtectedTrace

pytestmark = pytest.mark.skipif(not os.environ.get("ESCAPE_SYMBI_BINARY"),
                                reason="explicit prebuilt shipping binary and Docker E2E")


def test_shipping_dispatch_uses_same_protected_observer(tmp_path):
    binary = Path(os.environ["ESCAPE_SYMBI_BINARY"]).resolve(strict=True)
    evals = Path(__file__).parents[1]
    spec = importlib.util.spec_from_file_location("shipping_dispatch", evals / "scripts/verify_runtime_dispatch.py")
    dispatch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatch)
    image = subprocess.check_output(["docker", "image", "inspect", "escape-eval-sandboxed:latest", "--format", "{{.Id}}"], text=True).strip()
    label = f"eval.protected-trace={uuid.uuid4().hex}"
    trace = tmp_path / "shipping.strace"
    gate = evals / "harnesses/common/trace_gate.py"
    with ProtectedTrace(image=image, trace_path=trace, worker_uid=65534,
                        worker_gid=65534, worker_label=label) as observer:
        def setup(root):
            config = root / "symbiont.toml"
            text = config.read_text()
            text = text.replace('volumes = [', f'volumes = ["{gate}:{gate}:ro", "{observer.control_dir}:/trace-control:ro", ')
            text += f'extra_flags = ["--label={label}"]\n'
            config.write_text(text)
            manifest = root / "tools/count_fixture.clad.toml"
            text = manifest.read_text().replace(
                'template = "/usr/local/bin/python3 /opt/fixture.py',
                f'template = "/usr/local/bin/python3 -I {gate} /trace-control/gate.sock /usr/local/bin/python3 /opt/fixture.py')
            manifest.write_text(text)

        def launch(command, **kwargs):
            timeout = kwargs.pop("timeout")
            kwargs.pop("capture_output")
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as process:
                try:
                    observer.attach_and_release()
                    observer.wait_worker(process, timeout)
                    stdout, stderr = process.communicate(timeout=5)
                except BaseException:
                    if process.poll() is None:
                        process.send_signal(signal.SIGINT)
                    try:
                        process.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate(timeout=5)
                    raise
                finally:
                    if observer.receipt.get("worker_container"):
                        _remove_container(observer.receipt["worker_container"])
                return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

        record = dispatch.run_case(binary, dispatch.CASES[0], image_reference=image,
                                   fixture_setup=setup, process_runner=launch)
        (tmp_path / "shipping-result.json").write_text(json.dumps({
            "binary": str(binary), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "fixture_driver_sha256": hashlib.sha256((evals / "scripts/verify_runtime_dispatch.py").read_bytes()).hexdigest(),
            "record": record,
        }, indent=2) + "\n")
        assert record["passed"] is True, record
        assert record["observed_files"] == ["5"]
        assert observer.receipt["complete"] is True
    receipt = json.loads(trace.with_suffix(".observer.json").read_text())
    assert receipt["observer_cleanup_confirmed"] is True
    assert receipt["worker_uid"] == 65534
    assert "/workspace/5" in parse_strace_paths(trace.read_text(), reads_only=False)
