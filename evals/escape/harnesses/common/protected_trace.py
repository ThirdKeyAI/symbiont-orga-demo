"""Same-UID strace observer outside the worker's PID/mount namespaces.

Only the trusted gate may run before attach_and_release. The tracer container
has host PID visibility to attach to that exact worker, no added capabilities,
no network and no host filesystem mounts. Trace bytes are collected on the
host, independently from worker stdout, result JSON and writable mounts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

MAX_TRACE_BYTES = 16 * 1024 * 1024


class ProtectedTrace:
    def __init__(self, *, image: str, trace_path: Path, worker_uid=None, worker_gid=None, worker_label=None):
        if Path("/proc/sys/kernel/yama/ptrace_scope").read_text().strip() != "1":
            raise RuntimeError("protected observation requires host Yama ptrace_scope=1")
        self.worker_uid = os.getuid() if worker_uid is None else worker_uid
        self.worker_gid = os.getgid() if worker_gid is None else worker_gid
        if not isinstance(self.worker_uid, int) or self.worker_uid <= 0 or self.worker_gid < 0:
            raise ValueError("observer requires a non-root worker identity")
        if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
            raise ValueError("observer image must be pinned by image ID")
        self.worker_label = worker_label
        self.image = image
        self.trace_path = trace_path
        self.process = None
        self.reader = None
        self.error = None
        self.name = f"escape-observer-{uuid.uuid4().hex}"
        self.created = False
        self.receipt = {"version": 1, "attached": False, "complete": False,
                        "observer_cleanup_confirmed": False, "worker_uid": self.worker_uid,
                        "observer_image_id": image,
                        "gate_source_sha256": hashlib.sha256(Path(__file__).with_name("trace_gate.py").read_bytes()).hexdigest(),
                        "observer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="escape-trace-")
        self.control_dir = Path(self.directory.name)
        self.control_dir.chmod(0o755)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.settimeout(10)
        self.server.bind(str(self.control_dir / "gate.sock"))
        (self.control_dir / "gate.sock").chmod(0o666)
        self.server.listen(1)
        try:
            self.output = self.trace_path.open("xb")
            self.trace_path.chmod(0o600)
        except Exception:
            self.server.close()
            self.directory.cleanup()
            raise
        return self

    def _collect(self):
        size = 0
        try:
            while chunk := self.process.stderr.read(4096):
                size += len(chunk)
                if size > MAX_TRACE_BYTES:
                    raise RuntimeError("protected trace exceeded size limit")
                self.output.write(chunk)
            self.output.flush()
            os.fsync(self.output.fileno())
        except Exception as error:
            self.error = error

    @staticmethod
    def _inspect(name):
        result = subprocess.run(["docker", "container", "inspect", name],
                                check=True, timeout=10, capture_output=True, text=True)
        return json.loads(result.stdout)[0]

    def attach_and_release(self, worker_name=None, *, connection=None):
        with connection if connection is not None else self.server.accept()[0] as connection:
            connection.settimeout(5)
            peer_pid, peer_uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if worker_name is None:
                if not self.worker_label:
                    raise ValueError("observer needs a worker name or exact trial label")
                result = subprocess.run(["docker", "ps", "--quiet", "--filter", f"label={self.worker_label}"],
                                        check=True, timeout=10, capture_output=True, text=True)
                names = result.stdout.split()
                if len(names) != 1:
                    raise RuntimeError("trial label did not identify exactly one worker")
                worker_name = names[0]
            info = self._inspect(worker_name)
            init_pid = info["State"]["Pid"]
            if (not info["State"]["Running"] or info["HostConfig"].get("PidMode")
                    or peer_uid != self.worker_uid or peer_uid == 0):
                raise RuntimeError("gate peer does not match an isolated same-UID worker")
            # Shipping workers have a trusted init/shell above the gated
            # payload. Prove the peer's membership and ancestry using Docker's
            # host PID list, then attach to the payload and its descendants.
            rows = subprocess.run(["docker", "top", worker_name, "-eo", "pid,ppid,uid"],
                                  check=True, timeout=10, capture_output=True, text=True).stdout.splitlines()[1:]
            processes = {int(pid): (int(parent), int(uid)) for pid, parent, uid in (row.split() for row in rows)}
            ancestor = peer_pid
            visited = set()
            while ancestor != init_pid:
                if ancestor in visited or ancestor not in processes:
                    raise RuntimeError("gate peer is outside the selected worker process tree")
                visited.add(ancestor)
                ancestor = processes[ancestor][0]
            if peer_pid not in processes or processes[peer_pid][1] != peer_uid:
                raise RuntimeError("gate peer identity does not match Docker's process list")
            worker_pid = peer_pid
            for mount in info.get("Mounts", []):
                if mount.get("RW") and mount.get("Source"):
                    if self.trace_path.resolve().is_relative_to(Path(mount["Source"]).resolve()):
                        raise RuntimeError("worker can modify the observer evidence location")
            self.worker_inspect = info
            self.receipt.update(worker_container=info["Id"], worker_image_id=info["Image"],
                                trace_mount_isolated=True, container_init_host_pid=init_pid,
                                observation_scope="gated_payload_and_descendants")
            raw = bytearray()
            while not raw.endswith(b"\n") and len(raw) < 1024:
                part = connection.recv(1)
                if not part:
                    raise RuntimeError("worker closed observation gate")
                raw.extend(part)
            handshake = json.loads(raw)
            if (set(handshake) != {"version", "uid", "pid", "cwd", "environment_sha256", "environment_keys"} or handshake["version"] != 2
                    or handshake["uid"] != peer_uid or not isinstance(handshake["pid"], int)
                    or handshake["pid"] < 1):
                raise RuntimeError("unexpected observation gate handshake")
            if (not isinstance(handshake["cwd"], str)
                    or re.fullmatch(r"[0-9a-f]{64}", handshake["environment_sha256"]) is None
                    or not isinstance(handshake["environment_keys"], list)
                    or not all(isinstance(key, str) for key in handshake["environment_keys"])):
                raise RuntimeError("invalid gate execution environment")
            self.receipt["execution_environment"] = {key: handshake[key] for key in (
                "cwd", "environment_sha256", "environment_keys")}
            # No source, task, result or host directory is mounted here.
            self.created = True
            subprocess.run([
                "docker", "create", "--name", self.name, "--pull", "never",
                "--network", "none", "--pid", "host", "--read-only",
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", "16", "--memory", "128m", "--memory-swap", "128m",
                "--cpus", "1", "--user", f"{self.worker_uid}:{self.worker_gid}", self.image,
                "strace", "-q", "-f", "-yy", "-s", "4096", "-e",
                "trace=open,openat,openat2,connect,execve,execveat,clone,clone3,ptrace,io_uring_setup,io_uring_enter,io_uring_register,write,pwrite64,writev,pwritev,pwritev2",
                "-p", str(worker_pid),
            ], check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.process = subprocess.Popen(["docker", "start", "--attach", self.name],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.PIPE)
            self.reader = threading.Thread(target=self._collect, daemon=True)
            self.reader.start()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if self.error or self.process.poll() is not None:
                    raise RuntimeError("observer failed before attachment")
                observer_pid = self._inspect(self.name)["State"]["Pid"]
                status = Path(f"/proc/{worker_pid}/status").read_text()
                fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
                if "TracerPid" not in fields:
                    raise RuntimeError("worker status disappeared before observer attachment")
                tracer_pid = int(fields["TracerPid"])
                if observer_pid > 0 and tracer_pid == observer_pid:
                    self.receipt.update(attached=True, worker_container=info["Id"],
                                        worker_host_pid=worker_pid, observer_host_pid=observer_pid)
                    connection.sendall(b"continue\n")
                    return
                time.sleep(0.05)
            raise RuntimeError("observer attachment was not acknowledged")

    def wait_worker(self, worker, timeout):
        deadline = time.monotonic() + timeout
        while worker.poll() is None:
            if self.error:
                raise RuntimeError("protected observer lost evidence") from self.error
            if self.process.poll() not in (None, 0):
                raise RuntimeError("protected observer failed during execution")
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(worker.args, timeout)
            time.sleep(0.05)
        if worker.returncode:
            raise subprocess.CalledProcessError(worker.returncode, worker.args)
        self.finish_observation(timeout=10)

    def finish_observation(self, timeout):
        deadline = time.monotonic() + timeout
        while self.process.poll() is None:
            if self.error:
                raise RuntimeError("protected observer lost evidence") from self.error
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(self.process.args, timeout)
            time.sleep(0.05)
        self.reader.join(timeout=5)
        if self.reader.is_alive() or self.error:
            raise RuntimeError("protected observer did not finish collecting evidence")
        state = self._inspect(self.name)["State"]
        trace = self.trace_path.read_bytes()
        if (state["Running"] or state["ExitCode"] != 0 or not trace.strip()
                or any(line.startswith(b"strace:") for line in trace.splitlines())):
            raise RuntimeError("protected observer did not complete successfully")
        self.receipt.update(complete=True, trace_sha256=hashlib.sha256(trace).hexdigest(),
                            trace_bytes=len(trace), observer_exit_code=state["ExitCode"])

    def __exit__(self, *_):
        try:
            # The caller stops/removes the worker before closing this scope.
            if self.created:
                from harnesses.common.confined import _remove_container
                _remove_container(self.name)
            self.receipt["observer_cleanup_confirmed"] = True
        finally:
            if self.process is not None:
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.reader is not None:
                self.reader.join(timeout=5)
            self.output.close()
            self.server.close()
            self.directory.cleanup()
            self.trace_path.with_suffix(".observer.json").write_text(json.dumps(self.receipt, indent=2) + "\n")
